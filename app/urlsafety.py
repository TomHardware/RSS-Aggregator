"""URL 安全校验（SSRF 防护）与受限 HTTP 抓取。

抓取目标完全由用户输入决定（订阅地址、文章链接、OPML 文件），因此必须限制
协议与目标地址，避免把服务端变成探测内网的工具（例如云环境的
169.254.169.254 元数据接口）。

策略：
* 只允许 http / https；
* 目标是字面量 IP 或域名解析结果落在回环、私有、链路本地、保留网段时拒绝；
* **每一跳重定向都重新校验**：只校验首地址是不够的，一个公网 feed 完全可以用
  302 把服务端引到内网（`requests` 默认自动跟随重定向，等于绕过全部防护）；
* **限制响应体大小**：恶意源可以返回几十 GB 的响应，整份读进内存会打爆进程；
* 解析失败时放行——连不上的域名本来也抓不到内容，放行不会扩大攻击面，
  否则会把「DNS 临时故障的源」和「OPML 里的离线订阅」全部误杀。
  注意这留下了 DNS rebinding 的理论窗口（先解析到公网、连接时再解析到内网），
  对公网暴露的部署建议在网络层再加出站白名单。
"""
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlsplit

import requests

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = ("http", "https")
DEFAULT_PORTS = {"http": 80, "https": 443}
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
READ_CHUNK = 64 * 1024

# 这些主机名一律视为本机，不必等 DNS
_LOCAL_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


class UnsafeURLError(ValueError):
    """目标地址被安全策略拒绝（包括重定向后的目标）。"""


class FetchResult:
    """受限读取的结果。content 可能因超过上限而被截断（truncated=True）。"""

    __slots__ = ("url", "status_code", "content", "truncated")

    def __init__(self, url, status_code, content, truncated=False):
        self.url = url
        self.status_code = status_code
        self.content = content
        self.truncated = truncated

    def __repr__(self):
        return f"<FetchResult {self.status_code} {self.url} {len(self.content)}B>"


def _default_resolver(host, port):
    """返回主机名解析出的所有 IP。"""
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def _is_internal(address):
    try:
        ip = ipaddress.ip_address(address.split("%")[0])  # 去掉 IPv6 的 scope id
    except ValueError:
        return True  # 无法解析成 IP 时按不安全处理
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def is_safe_url(url, allow_private=False, resolver=None):
    """判断该 URL 是否允许服务端主动请求。"""
    if not url or not isinstance(url, str):
        return False

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        return False

    host = parts.hostname
    if not host:
        return False

    try:
        port = parts.port
    except ValueError:  # 非法端口（例如 http://host:abc/）
        return False
    if port is not None and not 0 < port < 65536:
        return False

    if allow_private:
        return True

    if host.lower() in _LOCAL_HOSTNAMES:
        return False

    try:
        address = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        pass  # 不是字面量 IP，需要走 DNS
    else:
        return not _is_internal(str(address))

    resolve = resolver or _default_resolver
    try:
        addresses = resolve(host, port or DEFAULT_PORTS[scheme])
    except (socket.gaierror, OSError, UnicodeError) as exc:
        logger.debug("域名解析失败，按可抓取处理 %s: %s", host, exc)
        return True

    if not addresses:
        return True
    for address in addresses:
        if _is_internal(address):
            logger.warning("拒绝内网地址 %s（来自 %s）", address, url)
            return False
    return True


def _read_bounded(response, limit):
    """最多读取 limit 字节；返回 (内容, 是否被截断)。"""
    chunks = []
    total = 0
    for chunk in response.iter_content(READ_CHUNK):
        if not chunk:
            continue
        if total + len(chunk) > limit:
            chunks.append(chunk[: limit - total])
            total = limit
            return b"".join(chunks), True
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), False


def safe_get(
    url,
    timeout=15,
    headers=None,
    max_bytes=None,
    allow_private=False,
    max_redirects=5,
    resolver=None,
):
    """带 SSRF 防护的 GET。

    * 不自动跟随重定向，而是逐跳校验后再跳，重定向到内网会被拒绝；
    * 响应体最多读取 max_bytes 字节，超出即停止读取并标记 truncated；
    * 目标被拒绝时抛 UnsafeURLError。
    """
    limit = DEFAULT_MAX_BYTES if max_bytes is None else max(0, int(max_bytes))
    current = (url or "").strip()

    for _ in range(max(0, int(max_redirects)) + 1):
        if not is_safe_url(current, allow_private=allow_private, resolver=resolver):
            raise UnsafeURLError(f"目标地址被安全策略拒绝: {current}")

        response = requests.get(
            current,
            timeout=timeout,
            headers=headers or {},
            allow_redirects=False,
            stream=True,
        )
        try:
            location = (response.headers.get("Location") or "").strip()
            if response.status_code in REDIRECT_STATUSES and location:
                next_url = urljoin(current, location)
                logger.debug("跟随重定向 %s -> %s", current, next_url)
                current = next_url
                continue

            content, truncated = _read_bounded(response, limit)
            if truncated:
                logger.warning("响应体超过 %d 字节，已截断: %s", limit, current)
            return FetchResult(current, response.status_code, content, truncated)
        finally:
            response.close()

    raise UnsafeURLError(f"重定向次数超过上限: {url}")
