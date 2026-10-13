
import logging
import secrets
import threading
import time
from collections import deque

from flask import current_app, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger(__name__)

SESSION_KEY = "authenticated_user"
PUBLIC_ENDPOINTS = {"web.login", "web.healthz", "static"}

MAX_FAILURES = 5
LOCKOUT_SECONDS = 300
FAILURE_WINDOW_SECONDS = 900
MAX_TRACKED_CLIENTS = 10000  # 失败记录表的键上限，防止长期运行内存膨胀

# 明文口令 → 派生哈希的缓存：配置里写明文时，避免每次登录都重新做一次慢哈希
_hash_cache = {"plain": None, "hash": None}


class LoginThrottle:
    """按来源记录失败次数；内存实现，进程级足够抵挡在线爆破。"""

    def __init__(self, max_failures=MAX_FAILURES, lockout_seconds=LOCKOUT_SECONDS):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._failures = {}
        self._lock = threading.Lock()
        self._since_prune = 0

    def _trim(self, key, now):
        """返回该来源仍需保留的失败窗口（必要时新建并写回字典）。

        注意不能在这里删除空窗口：`setdefault` 新建的窗口一旦被删掉，
        调用方 append 的就是一个游离的 deque，首次失败会**记不进去**，
        限流会直接失效。
        """
        window = self._failures.setdefault(key, deque())
        while window and now - window[0] > FAILURE_WINDOW_SECONDS:
            window.popleft()
        return window

    def _prune_all(self, now):
        """清掉所有过期/空记录；键太多时再丢掉最旧的一批。"""
        for key in list(self._failures):
            window = self._failures[key]
            while window and now - window[0] > FAILURE_WINDOW_SECONDS:
                window.popleft()
            if not window:
                del self._failures[key]
        while len(self._failures) > MAX_TRACKED_CLIENTS:
            self._failures.pop(next(iter(self._failures)))

    def locked_for(self, key):
        """返回剩余锁定秒数；0 表示未锁定。"""
        now = time.time()
        with self._lock:
            window = self._failures.get(key)
            if not window or len(window) < self.max_failures:
                return 0
            remaining = self.lockout_seconds - (now - window[-1])
        return max(0, int(remaining))

    def record_failure(self, key):
        now = time.time()
        with self._lock:
            self._since_prune += 1
            if self._since_prune >= 50:
                self._since_prune = 0
                self._prune_all(now)  # 定期清理，避免键无限增长
            self._trim(key, now).append(now)
            if len(self._failures) > MAX_TRACKED_CLIENTS:
                self._prune_all(now)

    def reset(self, key):
        with self._lock:
            self._failures.pop(key, None)

    def tracked_clients(self):
        with self._lock:
            return len(self._failures)


throttle = LoginThrottle()


def hash_password(password):
    """生成可直接写进 config.yaml 的哈希。"""
    return generate_password_hash(password)


def _configured_hash():
    """返回配置里的口令哈希；没有配置口令时返回 None。

    配置写的是明文时，派生一次并缓存：`generate_password_hash` 故意很慢，
    原实现每次登录尝试都要重新派生一遍。
    """
    direct = current_app.config.get("AUTH_PASSWORD_HASH")
    if direct:
        return direct
    plain = current_app.config.get("AUTH_PASSWORD")
    if plain:
        if _hash_cache["plain"] != plain or _hash_cache["hash"] is None:
            logger.warning(
                "auth.password 是明文口令，建议改用 auth.password_hash"
                "（生成方式：python -m app.auth <password>）"
            )
            _hash_cache["plain"] = plain
            _hash_cache["hash"] = generate_password_hash(plain)
        return _hash_cache["hash"]
    return None


def auth_enabled(config=None):
    """是否启用登录鉴权。

    可传入 `app.config`：应用工厂在**应用上下文之外**判断是否需要打警告，
    此时读 `current_app` 会抛 `RuntimeError: Working outside of application context`。
    """
    config = config if config is not None else current_app.config
    if not config.get("AUTH_ENABLED", True):
        return False
    return bool(
        config.get("AUTH_PASSWORD_HASH")
        or config.get("AUTH_PASSWORD")
        or config.get("AUTH_PASSWORD_FROM_ENV")
    )


def current_user():
    return session.get(SESSION_KEY)


def verify_credentials(username, password):
    """校验用户名与口令。返回 (是否通过, 提示键)。"""
    expected_user = current_app.config.get("AUTH_USERNAME", "admin")
    password_hash = _configured_hash()
    if password_hash is None:
        return False, "msg_login_not_configured"
    # 用户名比对也走常量时间，避免通过响应时间枚举用户名
    user_ok = secrets.compare_digest(str(username or ""), str(expected_user))
    password_ok = check_password_hash(password_hash, password or "")
    if user_ok and password_ok:
        return True, "msg_login_ok"
    return False, "msg_login_failed"


def client_key():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


def require_login():
    """全局 before_request 钩子：未登录时跳转到登录页。"""
    if not auth_enabled():
        return None
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if request.endpoint is None:  # 404 等
        return None
    if current_user():
        return None
    # 接口返回 JSON 401，而不是把 fetch() 重定向到登录页（那会拿到一页 HTML）
    if request.path.startswith("/api/"):
        return {"error": "authentication required"}, 401
    return redirect(url_for("web.login", next=request.full_path if request.query_string else request.path))


def login_user(username):
    session.clear()
    session[SESSION_KEY] = username
    session.permanent = bool(current_app.config.get("AUTH_PERMANENT_SESSION", True))


def logout_user():
    session.clear()


if __name__ == "__main__":  # pragma: no cover - 命令行工具
    import sys

    if len(sys.argv) != 2:
        print("用法: python -m app.auth <password>")
        print("输出可直接填入 config.yaml 的 auth.password_hash")
        raise SystemExit(2)
    print(hash_password(sys.argv[1]))
