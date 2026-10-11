"""v2.0.2：任务状态接口 + 前端轮询，以及第五轮审查缺陷的验证用例。

用法：python tests/test_v202_bugs.py
"""
import os
import sys
import time
import traceback
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_bugfixes import (  # noqa: E402 复用隔离环境与工具
    SAMPLE_RSS,
    add_source_via_model,
    fake_response,
    make_env,
)

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def wait_until(predicate, timeout=10.0, interval=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# --------------------------------------------------------------------------- #
# 1. 任务登记表
# --------------------------------------------------------------------------- #
@test
def test_task_registry_lifecycle():
    """start → running；finish → recent，并带上新增文章数。"""
    from app import tasks

    tasks.reset()
    task_id = tasks.start("initial_fetch", source_id=7, source_name="Feed A")
    snap = tasks.snapshot()
    assert [t["id"] for t in snap["running"]] == [task_id], snap
    assert snap["recent"] == []

    tasks.finish(task_id, True, 3, "Fetched 3 new entries")
    snap = tasks.snapshot()
    assert snap["running"] == []
    assert len(snap["recent"]) == 1
    entry = snap["recent"][0]
    assert entry["state"] == "success" and entry["new_articles"] == 3
    assert entry["source_name"] == "Feed A"
    assert "_ts" not in entry and "started_at_ts" not in entry, "内部字段泄漏到了接口数据"
    tasks.reset()


@test
def test_task_registry_rejects_unknown_finish():
    """finish 未知 id 不应抛异常（任务可能已被裁剪）。"""
    from app import tasks

    tasks.reset()
    tasks.finish("does-not-exist", True, 1, "x")
    assert tasks.get("does-not-exist") is None


@test
def test_task_concurrency_limit():
    """并发上限必须生效，否则反复点「添加源」能无限起线程。"""
    from app import tasks

    tasks.reset()
    tasks.start("initial_fetch", source_id=1, max_concurrent=2)
    tasks.start("initial_fetch", source_id=2, max_concurrent=2)
    try:
        tasks.start("initial_fetch", source_id=3, max_concurrent=2)
    except tasks.TooManyTasks:
        pass
    else:
        raise AssertionError("超过并发上限仍未拒绝")
    assert tasks.running_count() == 2
    tasks.reset()


# --------------------------------------------------------------------------- #
# 2. /api/status
# --------------------------------------------------------------------------- #
@test
def test_api_status_shape():
    """接口返回版本号、任务列表与计数。"""
    from app import tasks
    from app.version import __version__

    env = make_env()
    try:
        app = env.app()
        with app.app_context():
            add_source_via_model()
        tasks.reset()
        task_id = tasks.start("scheduled_fetch", source_id=1, source_name="S")
        tasks.finish(task_id, True, 2, "ok")

        resp = app.test_client().get("/api/status")
        assert resp.status_code == 200, resp.status_code
        data = resp.get_json()
        assert data["version"] == __version__, data
        assert data["recent"][0]["new_articles"] == 2
        assert data["sources"] == 1
        assert isinstance(data["unread_total"], int)
        assert "server_time" in data
    finally:
        tasks.reset()
        env.close()


@test
def test_api_task_detail_and_404():
    from app import tasks

    env = make_env()
    try:
        app = env.app()
        tasks.reset()
        task_id = tasks.start("initial_fetch", source_id=1)
        client = app.test_client()
        ok = client.get(f"/api/tasks/{task_id}")
        assert ok.status_code == 200 and ok.get_json()["id"] == task_id
        missing = client.get("/api/tasks/nope")
        assert missing.status_code == 404, missing.status_code
    finally:
        tasks.reset()
        env.close()


@test
def test_api_status_returns_json_401_when_logged_out():
    """未登录时接口要回 JSON 401，而不是把 fetch() 重定向到登录页。"""
    from app.auth import hash_password

    env = make_env()
    try:
        app = env.app()
        app.config["AUTH_PASSWORD_HASH"] = hash_password("pw")
        client = app.test_client()
        resp = client.get("/api/status", headers={"Accept": "application/json"})
        assert resp.status_code == 401, f"应返回 401，实际 {resp.status_code}"
        assert resp.get_json(), "401 响应不是 JSON"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 3. 后台抓取会登记任务
# --------------------------------------------------------------------------- #
@test
def test_initial_fetch_registers_task():
    """添加源后的后台抓取要能在任务表里看到，并最终变成 success。"""
    from app import tasks

    env = make_env()
    try:
        app = env.app()
        tasks.reset()
        with mock.patch("app.urlsafety.requests.get", return_value=fake_response()), \
             mock.patch("app.fetcher.extract_full_text", return_value=""), \
             mock.patch("app.fetcher.time.sleep", return_value=None):
            client = app.test_client()
            resp = client.post(
                "/add_source",
                data={"name": "Async", "url": "http://async.example/feed", "interval": "30"},
            )
            assert resp.status_code in (301, 302)

            def finished():
                return any(t["state"] != "running" for t in tasks.snapshot()["recent"])

            assert wait_until(finished, timeout=15), "后台任务一直没有结束"

        entry = tasks.snapshot()["recent"][0]
        assert entry["state"] == "success", entry
        assert entry["kind"] == "initial_fetch"
        assert entry["new_articles"] == 2, f"应记录 2 篇新文章，实际 {entry['new_articles']}"
    finally:
        tasks.reset()
        env.close()


@test
def test_scheduled_fetch_registers_task():
    """定时抓取同样登记任务，前端轮询才能看到它。"""
    from app import tasks
    from app.scheduler import run_fetch

    env = make_env(scheduler_enabled=True)
    try:
        app = env.app()
        tasks.reset()
        with app.app_context():
            sid = add_source_via_model().id
        with mock.patch("app.urlsafety.requests.get", return_value=fake_response()), \
             mock.patch("app.fetcher.extract_full_text", return_value=""), \
             mock.patch("app.fetcher.time.sleep", return_value=None):
            ok, message = run_fetch(app, sid, notify=False)
        assert ok, message
        recent = tasks.snapshot()["recent"]
        assert recent and recent[0]["kind"] == "scheduled_fetch", recent
        assert recent[0]["new_articles"] == 2
    finally:
        tasks.reset()
        env.close()


@test
def test_add_source_falls_back_to_sync_when_tasks_full():
    """后台任务已满时，「添加源」应退回同步抓取，而不是失败。"""
    from app import tasks
    from app.models import Source

    env = make_env()
    try:
        app = env.app()
        app.config["TASKS_MAX_CONCURRENT"] = 1
        tasks.reset()
        tasks.start("initial_fetch", source_id=999, max_concurrent=1)  # 占满

        calls = []

        def fake_fetch(source_id, notify=False, task_id=None):
            calls.append(task_id)
            return True, "ok"

        with mock.patch("app.web.routes.fetch_source", side_effect=fake_fetch):
            resp = app.test_client().post(
                "/add_source",
                data={"name": "Sync", "url": "http://sync.example/feed", "interval": "30"},
            )
        assert resp.status_code in (301, 302)
        with app.app_context():
            assert Source.query.filter_by(name="Sync").count() == 1, "源没有被添加"
        assert calls and calls[0] is None, "没有退回同步抓取（task_id 应为 None）"
        assert tasks.running_count() == 1, "不应再登记新任务"
    finally:
        tasks.reset()
        env.close()


# --------------------------------------------------------------------------- #
# 4. 前端轮询
# --------------------------------------------------------------------------- #
@test
def test_index_page_has_polling_hook():
    """页面要有轮询容器、接口地址与定时器，否则功能等于没做。"""
    env = make_env()
    try:
        app = env.app()
        body = app.test_client().get("/").data.decode("utf-8")
        assert 'id="task-status"' in body, "缺少任务状态容器"
        assert "/api/status" in body, "缺少轮询接口地址"
        assert "setInterval" in body, "缺少定时轮询"
        assert 'id="unread-total"' in body, "未读数没有可更新的元素"
        assert "location.reload" in body, "抓取完成后不会自动刷新"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 5. 第五轮修复
# --------------------------------------------------------------------------- #
@test
def test_plaintext_password_hash_is_cached():
    """配置写明文口令时，不应每次登录都重新做一次慢哈希。"""
    import app.auth as auth

    env = make_env()
    try:
        app = env.app()
        app.config["AUTH_PASSWORD_HASH"] = None
        app.config["AUTH_PASSWORD"] = "secret"
        auth._hash_cache.update({"plain": None, "hash": None})
        with app.app_context():
            with mock.patch.object(auth, "generate_password_hash", wraps=auth.generate_password_hash) as hashed:
                first = auth._configured_hash()
                second = auth._configured_hash()
                assert hashed.call_count == 1, f"重复派生了 {hashed.call_count} 次哈希"
            assert first == second
    finally:
        env.close()


@test
def test_login_throttle_prunes_stale_entries():
    """失败记录表必须会清理，否则长期运行会一直涨。"""
    from app.auth import FAILURE_WINDOW_SECONDS, LoginThrottle

    throttle = LoginThrottle()
    for i in range(20):
        throttle.record_failure(f"10.0.0.{i}")
    assert throttle.tracked_clients() == 20

    stale = time.time() - FAILURE_WINDOW_SECONDS - 10
    for window in throttle._failures.values():
        window.clear()
        window.append(stale)

    for _ in range(50):  # 触发周期性清理
        throttle.record_failure("10.9.9.9")

    assert throttle.tracked_clients() <= 2, f"过期记录没有被清理: {throttle.tracked_clients()}"


@test
def test_retention_module_has_no_dynamic_import():
    """retention 里不应再用 __import__('datetime') 这种写法。"""
    source = open(os.path.join(REPO, "app", "retention.py"), encoding="utf-8").read()
    assert "__import__" not in source, "retention.py 仍在使用动态 import"
    assert "from datetime import timedelta" in source


@test
def test_version_and_docs_are_consistent():
    """版本号必须与 CHANGELOG、两份 README 一致。"""
    from app.version import __version__

    assert __version__.count(".") == 2, __version__
    changelog = open(os.path.join(REPO, "CHANGELOG.md"), encoding="utf-8").read()
    assert f"## [{__version__}]" in changelog, f"CHANGELOG.md 缺少 [{__version__}] 小节"
    for name in ("README.md", "README-zh.md"):
        text = open(os.path.join(REPO, name), encoding="utf-8").read()
        assert __version__ in text, f"{name} 里的版本号不是 {__version__}"


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
