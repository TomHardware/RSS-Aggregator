"""端到端冒烟测试。

与单元级回归测试互补：这里真的启动 `python main.py`（走 main.py 的引导流程），
用真实 HTTP 请求驱动，并用一个本地 HTTP 服务提供真实 RSS 源，
验证「添加源 → 抓取入库 → 页面渲染 → 搜索 → OPML 导出/导入 → 健康检查」整条链路。

用法：python tests/test_e2e_smoke.py
"""
import os
import re
import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from xml.etree import ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable
APP_PORT = 5099
FEED_PORT = 5098

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Smoke Feed</title>
  <item>
    <title>Smoke Article One</title>
    <link>http://127.0.0.1:%d/one</link>
    <description>first smoke item</description>
    <pubDate>Mon, 02 Jan 2006 15:04:05 GMT</pubDate>
  </item>
  <item>
    <title>Smoke Article Two</title>
    <link>http://127.0.0.1:%d/two</link>
    <description>second smoke item</description>
    <pubDate>Tue, 03 Jan 2006 15:04:05 GMT</pubDate>
  </item>
</channel></rss>
""" % (FEED_PORT, FEED_PORT)

OPML = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0"><head><title>t</title></head><body>
  <outline text="Imported Cat">
    <outline type="rss" text="Imported Feed" xmlUrl="http://127.0.0.1:%d/feed2.xml"/>
  </outline>
</body></opml>
""" % FEED_PORT

CONFIG = """app:
  name: "RSS Aggregator Smoke"
  language: "en"
database:
  url: "sqlite:///{db_path}"
scheduler:
  enabled: true
  default_interval: 30
server:
  host: "127.0.0.1"
  port: {app_port}
  debug: false
fetch:
  retries: 2
  timeout: 10
fulltext:
  enabled: false
security:
  allow_private_networks: true
logging:
  level: "WARNING"
  file: "smoke.log"
"""

failures = []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """让 3xx 直接以 HTTPError 形式返回，便于断言真实状态码。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class OutputCollector(threading.Thread):
    """持续读取子进程 stdout。

    必须一边跑一边读：管道缓冲写满后子进程会阻塞在日志写入上，
    表现为服务「假死」（请求处理到一半不再响应）。
    """

    def __init__(self, stream):
        super().__init__(daemon=True)
        self.stream = stream
        self.lines = []

    def run(self):
        for line in self.stream:
            self.lines.append(line)

    def text(self, limit=200):
        return "".join(self.lines[-limit:])


def check(label, condition, detail=""):
    if condition:
        print(f"PASS  {label}")
    else:
        print(f"FAIL  {label}  {detail}")
        failures.append(label)


def http(method, path, data=None, headers=None, timeout=20):
    """不跟随重定向，这样能拿到真实的 301/302/403/405 状态码。"""
    url = f"http://127.0.0.1:{APP_PORT}{path}"
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Connection", "close")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def wait_for(url, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def post_form(path, fields, timeout=30):
    import urllib.parse

    body = urllib.parse.urlencode(fields).encode()
    return http(
        "POST",
        path,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )


def _strip_tags(html):
    """剥掉 HTML 标签，便于对渲染后的正文做文本断言。"""
    import re as _re

    return _re.sub(r"<[^>]+>", "", html)


def main():
    workdir = tempfile.mkdtemp(prefix="rssagg-smoke-")
    db_path = os.path.join(workdir, "smoke.db").replace("\\", "/")
    with open(os.path.join(workdir, "feed.xml"), "w", encoding="utf-8") as fh:
        fh.write(FEED_XML)
    config_path = os.path.join(workdir, "config.yaml")
    with open(config_path, "w", encoding="utf-8") as fh:
        fh.write(CONFIG.format(db_path=db_path, app_port=APP_PORT))

    env = dict(os.environ, RSS_AGGREGATOR_CONFIG=config_path, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    feed_server = subprocess.Popen(
        [PYTHON, "-m", "http.server", str(FEED_PORT), "--bind", "127.0.0.1",
         "--directory", workdir],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    app_server = subprocess.Popen(
        [PYTHON, os.path.join(REPO, "main.py")], cwd=workdir,
        env={**env, "PYTHONPATH": REPO},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    collector = OutputCollector(app_server.stdout)
    collector.start()

    try:
        if not wait_for(f"http://127.0.0.1:{FEED_PORT}/feed.xml", 15):
            check("本地 feed 服务启动", False, "http.server 未就绪")
            return 1
        check("本地 feed 服务启动", True)

        if not wait_for(f"http://127.0.0.1:{APP_PORT}/healthz", 30):
            check("python main.py 启动成功", False, f"服务未就绪。输出:\n{collector.text()}")
            return 1
        check("python main.py 启动成功", True)

        status, body = http("GET", "/")
        check("首页渲染 200", status == 200, f"status={status}")
        check("首页包含应用名", "RSS Aggregator Smoke" in body)

        status, body = post_form(
            "/add_source",
            {"name": "Smoke", "url": f"http://127.0.0.1:{FEED_PORT}/feed.xml", "interval": "30"},
        )
        check("添加源返回重定向", status in (301, 302), f"status={status}")

        # 源本身是同步入库的，页面上应立刻可见
        status, body = http("GET", "/")
        check("添加源后首页立即显示源名称", "Smoke" in body, "源没有立即出现在列表里")

        # v2.0.1 起首次抓取在后台执行：这里轮询等待结果入库
        deadline = time.time() + 25
        fetched = False
        while time.time() < deadline:
            status, body = http("GET", "/")
            if "Smoke Article One" in body:
                fetched = True
                break
            time.sleep(0.5)
        check("后台首次抓取结果最终入库并渲染", fetched, "25 秒内没有抓到文章")

        # 任务状态接口：前端轮询依赖它，必须在真实服务器上可用
        # 后台任务的 finish 发生在文章入库之后，这里短暂轮询避免偶发时序竞争
        payload = {}
        for _attempt in range(20):
            status, body = http("GET", "/api/status")
            try:
                payload = json.loads(body)
            except ValueError:
                payload = {}
            if payload.get("recent"):
                break
            time.sleep(0.2)
        check("GET /api/status 返回 JSON", status == 200 and isinstance(payload, dict), f"status={status}")
        check("接口带版本号", bool(payload.get("version")), payload.get("version"))
        recent = payload.get("recent") or []
        done = [t for t in recent if t.get("state") == "success"]
        check(
            "接口能看到已完成的抓取任务",
            bool(done),
            f"recent={recent}",
        )
        if done:
            check(
                "任务记录了新增文章数",
                done[0].get("new_articles") == 2,
                f"new_articles={done[0].get('new_articles')}",
            )
        check(
            "接口带未读总数",
            isinstance(payload.get("unread_total"), int),
            payload.get("unread_total"),
        )
        status, body = http("GET", "/api/tasks/does-not-exist")
        check("未知任务返回 404", status == 404, f"status={status}")

        status, body = http("GET", "/search?q=Article+Two")
        _plain = _strip_tags(body)
        check("搜索命中", status == 200 and "Smoke Article Two" in _plain, f"status={status}")
        status, body = http("GET", "/search?q=Article+Two&unread=true")
        _plain = _strip_tags(body)
        check("未读过滤可用", status == 200 and "Smoke Article Two" in _plain)

        status, body = http("GET", "/export_opml")
        ok_export = False
        if status == 200:
            try:
                root = ET.fromstring(body)
                ok_export = any(o.get("xmlUrl") for o in root.iter("outline"))
            except ET.ParseError:
                ok_export = False
        check("OPML 导出是可解析 XML", ok_export, f"status={status}")

        import urllib.parse

        boundary = "----smoke"
        payload = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="opml_file"; filename="feeds.opml"\r\n'
            "Content-Type: text/xml\r\n\r\n"
            f"{OPML}\r\n--{boundary}--\r\n"
        ).encode()
        status, body = http(
            "POST", "/import_opml", data=payload,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        check("OPML 导入返回重定向", status in (301, 302), f"status={status}")
        status, body = http("GET", "/")
        check("导入的源出现在页面", "Imported Feed" in body)

        status, body = http("GET", "/")
        import re

        read_actions = re.findall(r'action="(/article/\d+/read)"', body)
        check("页面包含标记已读入口", len(read_actions) >= 2, f"找到 {len(read_actions)} 个")
        for action in read_actions:
            status, _ = post_form(action, {"next": "/"})
            check(f"标记已读 {action} 返回重定向", status in (301, 302), f"status={status}")
        status, after = http("GET", "/search?q=Smoke+Article&unread=true")
        check(
            "已读文章从未读列表消失",
            "Smoke Article One" not in after and "Smoke Article Two" not in after,
            "标记已读后仍出现在未读列表（注意搜索框会回显查询串，断言要用完整标题）",
        )
        status, still = http("GET", "/search?q=Smoke+Article")
        still_plain = re.sub(r'<[^>]+>', '', still)
        check(
            "文章并未被删除",
            "Smoke Article One" in still_plain and "Smoke Article Two" in still_plain,
            "标记已读不应删除文章",
        )

        status, body = http("GET", "/delete_source/1")
        check("删除源拒绝 GET", status == 405, f"status={status}")

        status, _ = http(
            "POST", "/add_source",
            data=urllib.parse.urlencode({"url": "http://x.example/f", "interval": "1"}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        check("非法 interval 不再 500", status < 500, f"status={status}")

        start = time.time()
        status, _ = http(
            "POST", "/add_source",
            data=urllib.parse.urlencode({"url": f"http://127.0.0.1:{FEED_PORT}/other.xml"}).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "http://evil.example",
            },
        )
        elapsed = time.time() - start
        check("跨站 POST 被拒绝", status == 403, f"status={status}, 耗时 {elapsed:.2f}s")

        if app_server.poll() is not None:
            check("服务进程存活", False, f"提前退出: {collector.text(80)}")
        else:
            check("服务进程存活", True)
    finally:
        for process in (app_server, feed_server):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if failures:
            print("\n--- 服务输出 ---")
            print(collector.text(120))

    print("\n" + "=" * 72)
    if failures:
        print(f"冒烟测试失败 {len(failures)} 项: {failures}")
        return 1
    print("冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
