"""v1.3 第二轮审查：针对新发现缺陷的验证用例。

与 test_bugfixes.py 一样，同一份用例在修复前失败、修复后通过。
用法：python tests/test_v13_bugs.py
"""
import os
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_bugfixes import (  # noqa: E402 复用隔离环境与工具
    Env,
    SAMPLE_RSS,
    add_source_via_model,
    fake_response,
    make_env,
)

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# --------------------------------------------------------------------------- #
# 本地 HTTP 测试服务
# --------------------------------------------------------------------------- #
def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    """一个可编程的本地 HTTP 服务，记录每个请求。"""

    def __init__(self, handler_cls):
        self.port = free_port()
        self.hits = []
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), handler_cls)
        self._server.hits = self.hits
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        return False

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"


class RedirectHandler(BaseHTTPRequestHandler):
    """把请求 302 跳到另一个（内网）地址。"""

    def do_GET(self):  # noqa: N802
        target = self.server.target_url
        self.server.hits.append((self.path, target))
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class SinkHandler(BaseHTTPRequestHandler):
    """重定向目标：只要被访问到就记录下来。"""

    def do_GET(self):  # noqa: N802
        self.server.hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        self.send_header("Content-Length", str(len(SAMPLE_RSS)))
        self.end_headers()
        self.wfile.write(SAMPLE_RSS)

    def log_message(self, *args):
        pass


class BigHandler(BaseHTTPRequestHandler):
    """无限（超大）响应体，用来验证客户端是否有读取上限。"""

    TOTAL_MB = 40
    CHUNK = 1024 * 1024

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        self.end_headers()  # 不声明 Content-Length，靠关闭连接结束
        sent = 0
        try:
            for _ in range(self.TOTAL_MB):
                self.wfile.write(b"<" + b"x" * (self.CHUNK - 2) + b">")
                self.wfile.flush()
                sent += self.CHUNK
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.hits.append(sent)

    def log_message(self, *args):
        pass


# --------------------------------------------------------------------------- #
# 1. 重定向绕过 SSRF 防护
# --------------------------------------------------------------------------- #
@test
def test_redirect_to_internal_address_is_blocked():
    """源地址通过校验后重定向到内网地址：必须拦截重定向目标（否则可绕过 SSRF 防护）。"""
    with Server(SinkHandler) as sink, Server(RedirectHandler) as redirect:
        redirect._server.target_url = sink.url + "/secret"

        def only_first_hop(url, allow_private=False, resolver=None):
            """模拟「公网 feed 地址」：只放行第一跳，重定向目标应当被拦截。"""
            return f":{redirect.port}/" in url

        env = make_env()
        try:
            from app.fetcher import fetch_source

            app = env.app()
            with app.app_context():
                sid = add_source_via_model(url=f"{redirect.url}/feed").id
                with mock.patch("app.fetcher.is_safe_url", side_effect=only_first_hop), \
                     mock.patch("app.urlsafety.is_safe_url", side_effect=only_first_hop), \
                     mock.patch("app.fetcher.extract_full_text", return_value=""):
                    fetch_source(sid)
        finally:
            env.close()

        assert not sink.hits, (
            f"重定向目标（内网地址）被请求了 {len(sink.hits)} 次 —— SSRF 防护可被 302 绕过"
        )
        assert redirect.hits, "第一跳没有被请求，用例前提不成立"


@test
def test_redirect_chain_is_validated():
    """两次重定向后落到内网地址，同样要拦。"""
    with Server(SinkHandler) as sink, Server(RedirectHandler) as hop2, Server(RedirectHandler) as hop1:
        hop2._server.target_url = sink.url + "/deep"
        hop1._server.target_url = hop2.url + "/middle"

        def only_first_hop(url, allow_private=False, resolver=None):
            return f":{hop1.port}/" in url

        env = make_env()
        try:
            from app.fetcher import fetch_source

            app = env.app()
            with app.app_context():
                sid = add_source_via_model(url=f"{hop1.url}/feed").id
                with mock.patch("app.fetcher.is_safe_url", side_effect=only_first_hop), \
                     mock.patch("app.urlsafety.is_safe_url", side_effect=only_first_hop), \
                     mock.patch("app.fetcher.extract_full_text", return_value=""):
                    fetch_source(sid)
        finally:
            env.close()

        assert not hop2.hits, "第二跳没有被校验"
        assert not sink.hits, "多级重定向可以绕过 SSRF 防护"


# --------------------------------------------------------------------------- #
# 2. 响应体没有读取上限
# --------------------------------------------------------------------------- #
@test
def test_fetch_has_response_size_limit():
    """恶意源可以返回超大响应体：客户端必须有读取上限，不能整份读进内存。"""
    with Server(BigHandler) as big:
        env = make_env()
        try:
            from app.fetcher import fetch_source

            app = env.app()
            app.config["FETCH_MAX_BYTES"] = 2 * 1024 * 1024
            # 本地服务是回环地址，这里显式放开，确保请求真的发生（否则用例会「空过」）
            app.config["SECURITY_ALLOW_PRIVATE_NETWORKS"] = True
            with app.app_context():
                sid = add_source_via_model(url=f"{big.url}/feed.xml").id
                with mock.patch("app.fetcher.extract_full_text", return_value=""), \
                     mock.patch("app.fetcher.time.sleep", return_value=None):
                    fetch_source(sid)
        finally:
            env.close()

        assert big.hits, "请求没有真正发生，用例前提不成立"
        largest = max(big.hits)
        assert largest <= 12 * 1024 * 1024, (
            f"服务端成功发出了 {largest / 1024 / 1024:.1f} MB，"
            "说明客户端把超大响应体整份读入了内存"
        )


# --------------------------------------------------------------------------- #
# 3. 健康检查不校验数据库
# --------------------------------------------------------------------------- #
@test
def test_healthz_detects_broken_database():
    """数据库/schema 不可用时 /healthz 必须报不健康，否则容器会被误判为正常。"""
    env = make_env()
    try:
        from sqlalchemy import text

        from app import db

        app = env.app()
        client = app.test_client()
        assert client.get("/healthz").status_code == 200, "正常情况下健康检查应当通过"

        with app.app_context():
            db.session.execute(text("DROP TABLE article"))
            db.session.commit()

        resp = client.get("/healthz")
        assert resp.status_code >= 500, (
            f"article 表已被删除，/healthz 仍返回 {resp.status_code}（容器会误判为健康）"
        )
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 4. 调度器重复启动
# --------------------------------------------------------------------------- #
@test
def test_scheduler_skipped_in_debug_reloader_parent():
    """flask run --debug / 调试模式下，重载器父进程不得启动调度器（否则重复抓取）。"""
    env = make_env(scheduler_enabled=True)
    try:
        from app.scheduler import scheduler_allowed

        app = env.app()
        app.config["SERVER_DEBUG"] = False
        app.debug = True
        os.environ.pop("WERKZEUG_RUN_MAIN", None)
        try:
            assert scheduler_allowed(app) is False, (
                "调试模式下重载器父进程仍会启动调度器 → 两个进程重复抓取"
            )
        finally:
            app.debug = False
    finally:
        env.close()


@test
def test_scheduler_can_be_disabled_per_process():
    """多进程部署（gunicorn -w N）需要能只在其中一个进程跑调度器。"""
    env = make_env(scheduler_enabled=True)
    try:
        from app.scheduler import scheduler_allowed

        app = env.app()
        app.config["SERVER_DEBUG"] = False
        app.debug = False
        os.environ["RSS_AGGREGATOR_SCHEDULER"] = "off"
        try:
            assert scheduler_allowed(app) is False, (
                "缺少按进程关闭调度器的手段，多 worker 部署会重复抓取"
            )
        finally:
            os.environ.pop("RSS_AGGREGATOR_SCHEDULER", None)
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 5. read 为 NULL 的文章在「只看未读」里消失
# --------------------------------------------------------------------------- #
@test
def test_unread_filter_keeps_null_read_articles():
    """read 为 NULL（历史数据/外部写入）的文章不算已读，不应在未读列表里消失。"""
    env = make_env()
    try:
        from sqlalchemy import text

        from app import db
        from app.search import search_articles

        app = env.app()
        with app.app_context():
            sid = add_source_via_model().id
            db.session.execute(
                text(
                    "INSERT INTO article (title, link, summary, content, read, source_id) "
                    "VALUES ('null-read row', 'http://example.com/nr', '', '', NULL, :sid)"
                ),
                {"sid": sid},
            )
            db.session.commit()

            unread = search_articles("", None, True, page=1, per_page=50)
            titles = [a.title for a in unread.items]
            assert "null-read row" in titles, (
                "read 为 NULL 的文章在「只看未读」里消失了（它并不是已读）"
            )
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 6. 通知邮件与实际入库不一致
# --------------------------------------------------------------------------- #
@test
def test_notification_lists_only_saved_articles():
    """并发冲突导致部分文章没入库时，通知邮件不应把它们列出来。"""
    import feedparser

    env = make_env()
    try:
        from app import db
        from app.fetcher import fetch_source
        from app.models import Article

        app = env.app()
        captured = {}

        def fake_send(subject, body):
            captured["subject"] = subject
            captured["body"] = body
            return True

        with app.app_context():
            sid = add_source_via_model().id
            # 先占掉 feed 里的第一条链接，再让预查「看不见」它 → 提交时撞唯一约束
            db.session.add(Article(title="existing a", link="http://example.com/a", source_id=sid))
            db.session.commit()

            assert feedparser.parse(SAMPLE_RSS).entries, "样例 feed 解析失败"

            with mock.patch("app.urlsafety.requests.get", return_value=fake_response()), \
                 mock.patch("app.fetcher.extract_full_text", return_value=""), \
                 mock.patch("app.fetcher._existing_links", return_value=set()), \
                 mock.patch("app.notifications.send_email", side_effect=fake_send):
                ok, msg = fetch_source(sid, notify=True)

            assert ok, f"抓取应成功: {msg}"
            assert Article.query.filter_by(link="http://example.com/b").count() == 1
        assert captured, "没有发送通知邮件，用例前提不成立"
        assert "Hello World" not in captured["body"], (
            f"邮件里列出了并未入库的文章：{captured['body']!r}"
        )
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def main():
    passed, failed = [], []
    for fn in TESTS:
        started = time.time()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed.append((fn.__name__, exc))
            print(f"FAIL  {fn.__name__}  ({time.time() - started:.1f}s)\n      {type(exc).__name__}: {exc}")
            if os.environ.get("RSSAGG_TRACE"):
                traceback.print_exc()
        else:
            passed.append(fn.__name__)
            print(f"PASS  {fn.__name__}  ({time.time() - started:.1f}s)")
    print("\n" + "=" * 72)
    print(f"PASSED {len(passed)} / {len(TESTS)}   FAILED {len(failed)}")
    for name, exc in failed:
        print(f"  - {name}: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
