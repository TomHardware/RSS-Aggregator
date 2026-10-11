"""v2.0.4：本地 GUI（桌面 + 网页控制台）与共享业务层的验证用例。

用法：python tests/test_v204_bugs.py
"""
import json
import os
import sys
import time
import traceback
import urllib.request
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_bugfixes import (  # noqa: E402
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
# 1. 共享业务层
# --------------------------------------------------------------------------- #
@test
def test_services_add_feed_validates_input():
    """共享业务层要挡住空地址、超长地址、内网地址与重复地址。"""
    from app.services import ServiceError, add_feed

    env = make_env()
    try:
        app = env.app()
        with app.app_context():
            for url, key in (
                ("", "msg_url_required"),
                ("http://e.example/" + "x" * 600, "msg_url_too_long"),
                ("http://127.0.0.1:8080/feed", "msg_url_blocked"),
            ):
                try:
                    add_feed(url)
                except ServiceError as exc:
                    assert exc.message_key == key, f"{url!r} 期望 {key}，实际 {exc.message_key}"
                else:
                    raise AssertionError(f"{url!r} 没有被拒绝")

            source = add_feed("http://ok.example/feed", "Name", "Tech", "45")
            assert source.id and source.interval == 45 and source.category == "Tech"
            assert source.enabled is True

            try:
                add_feed("http://ok.example/feed")
            except ServiceError as exc:
                assert exc.message_key == "msg_feed_exists"
            else:
                raise AssertionError("重复地址没有被拒绝")
    finally:
        env.close()


@test
def test_services_edit_toggle_delete():
    """编辑、暂停/恢复、删除都应生效并写回数据库。"""
    from app.services import delete_feed, edit_feed, get_feed, toggle_feed

    env = make_env()
    try:
        app = env.app()
        app.config["SCHEDULER_ENABLED"] = False
        with app.app_context():
            source = add_source_via_model(name="Old", interval=30)
            source = edit_feed(source.id, name="New", category="News", interval="90")
            assert (source.name, source.category, source.interval) == ("New", "News", 90)

            source = toggle_feed(source.id)
            assert source.enabled is False
            source = toggle_feed(source.id)
            assert source.enabled is True

            delete_feed(source.id)
            try:
                get_feed(source.id)
            except Exception as exc:  # noqa: BLE001
                assert "msg_feed_not_found" in str(exc)
            else:
                raise AssertionError("删除后仍能取到该订阅源")
    finally:
        env.close()


@test
def test_services_interval_is_clamped():
    """间隔必须被强制到最小值以上，非法值回退默认。"""
    from app.services import MIN_INTERVAL_MINUTES, parse_interval

    assert parse_interval("0") == MIN_INTERVAL_MINUTES
    assert parse_interval("-5") == MIN_INTERVAL_MINUTES
    assert parse_interval("abc", 30) == 30
    assert parse_interval("120") == 120


@test
def test_services_mark_read_and_counts():
    """标记已读要生效，未读统计要跟着变。"""
    from app import db
    from app.models import Article
    from app.services import mark_read, unread_counts

    env = make_env()
    try:
        app = env.app()
        with app.app_context():
            sid = add_source_via_model().id
            article = Article(title="t", link="http://e/a", source_id=sid)
            db.session.add(article)
            db.session.commit()

            _, total_before = unread_counts()
            assert total_before == 1, total_before

            mark_read(article.id, True)
            _, total_after = unread_counts()
            assert total_after == 0, total_after
    finally:
        env.close()


@test
def test_services_status_snapshot_shape():
    """状态快照要包含版本、任务与计数，供控制台和桌面窗口共用。"""
    from app import tasks
    from app.services import status_snapshot
    from app.version import __version__

    env = make_env()
    try:
        app = env.app()
        tasks.reset()
        with app.app_context():
            snapshot = status_snapshot()
        assert snapshot["version"] == __version__
        assert set(snapshot) >= {"running", "recent", "unread_total", "sources", "articles", "server_time"}
    finally:
        tasks.reset()
        env.close()


# --------------------------------------------------------------------------- #
# 2. 网页控制台
# --------------------------------------------------------------------------- #
@test
def test_console_page_renders_with_actions():
    """控制台页面要能渲染，并带上批量操作与编辑表单。"""
    env = make_env()
    try:
        app = env.app()
        with app.app_context():
            add_source_via_model(name="ConsoleFeed")
        client = app.test_client()
        resp = client.get("/console")
        assert resp.status_code == 200, resp.status_code
        body = resp.data.decode("utf-8")
        for needle in ("ConsoleFeed", "/refresh_all", "/toggle_source/", "/edit_source/", "/api/status"):
            assert needle in body, f"控制台页面缺少 {needle}"
    finally:
        env.close()


@test
def test_refresh_all_starts_fetches():
    """「全部抓取」应触发所有启用中的源，并跳过已暂停的源。"""
    from app import db
    from app.models import Source

    env = make_env()
    try:
        app = env.app()
        with app.app_context():
            active = add_source_via_model(name="active", url="http://a.example/feed")
            paused = add_source_via_model(name="paused", url="http://b.example/feed")
            paused.enabled = False
            db.session.commit()
            active_id, paused_id = active.id, paused.id

        called = []

        def fake_fetch(source_id, notify=True):
            called.append(source_id)
            return True, "ok"

        # 让后台分支不可用，从而确定性地走同步抓取（否则线程是异步的，断言会过早）
        with mock.patch("app.services.start_background_fetch", return_value=None), \
             mock.patch("app.services.fetch_source", side_effect=fake_fetch):
            resp = app.test_client().post("/refresh_all")
        assert resp.status_code in (301, 302), resp.status_code
        assert active_id in called, f"启用中的源没有被抓取: {called}"
        assert paused_id not in called, "已暂停的源也被抓取了"
    finally:
        env.close()


@test
def test_console_requires_login_when_auth_enabled():
    """开启鉴权后控制台也要登录才能看。"""
    from app.auth import hash_password

    env = make_env()
    try:
        app = env.app()
        app.config["AUTH_PASSWORD_HASH"] = hash_password("pw")
        resp = app.test_client().get("/console")
        assert resp.status_code in (301, 302), resp.status_code
        assert "/login" in resp.headers.get("Location", "")
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 3. 桌面 GUI
# --------------------------------------------------------------------------- #
@test
def test_desktop_gui_files_exist():
    """桌面 GUI 的入口与模块必须在仓库里。"""
    for rel in ("desktop_gui.py", "app/gui.py", "app/services.py",
                "app/web/templates/console.html"):
        path = os.path.join(REPO, rel.replace("/", os.sep))
        assert os.path.exists(path), f"缺少 {rel}"


@test
def test_desktop_gui_module_imports_without_display():
    """只导入 GUI 模块不应创建窗口（服务器 / 无图形环境也能 import）。"""
    import app.gui as gui

    assert callable(gui.main)
    assert callable(gui.start_server)
    assert hasattr(gui, "DesktopApp")


@test
def test_desktop_gui_embedded_server_serves_healthz():
    """窗口内嵌的 Web 服务要真的能起来并响应 /healthz。"""
    import socket

    from app.gui import start_server

    env = make_env()
    try:
        app = env.app()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server, thread = start_server(app, "127.0.0.1", port)
        try:
            deadline = time.time() + 10
            last = None
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as resp:
                        last = resp.read().decode()
                        break
                except Exception as exc:  # noqa: BLE001
                    last = exc
                    time.sleep(0.2)
            assert last and not isinstance(last, Exception), f"内置服务未就绪: {last}"
            from app.version import __version__

            assert __version__ in last, last
        finally:
            server.shutdown()
            thread.join(timeout=5)
    finally:
        env.close()


@test
def test_desktop_gui_uses_shared_services():
    """桌面 GUI 必须复用共享业务层，而不是自己再写一套数据库逻辑。"""
    source = open(os.path.join(REPO, "app", "gui.py"), encoding="utf-8").read()
    for name in ("add_feed", "edit_feed", "toggle_feed", "delete_feed", "mark_read", "fetch_all"):
        assert name in source, f"app/gui.py 没有使用 services.{name}"


# --------------------------------------------------------------------------- #
# 4. 版本一致性
# --------------------------------------------------------------------------- #
@test
def test_version_and_docs_are_consistent():
    """版本号必须与 CHANGELOG、两份 README 一致。"""
    from app.version import __version__

    assert __version__ == "2.0.4", __version__
    changelog = open(os.path.join(REPO, "CHANGELOG.md"), encoding="utf-8").read()
    assert f"## [{__version__}]" in changelog, f"CHANGELOG.md 缺少 [{__version__}] 小节"
    for name in ("README.md", "README-zh.md"):
        text = open(os.path.join(REPO, name), encoding="utf-8").read()
        assert __version__ in text, f"{name} 里的版本号不是 {__version__}"


@test
def test_healthz_reports_version():
    from app.version import __version__

    env = make_env()
    try:
        app = env.app()
        resp = app.test_client().get("/healthz")
        assert resp.status_code == 200
        assert __version__ in resp.data.decode()
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
