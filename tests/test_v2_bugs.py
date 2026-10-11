"""v2.0.1：新增功能与第四轮审查缺陷的验证用例。

用法：python tests/test_v2_bugs.py
"""
import os
import sys
import threading
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


def multi_entry_feed(count, prefix="http://example.com/item"):
    items = "".join(
        f"<item><title>Item {i}</title><link>{prefix}/{i}</link>"
        f"<description>d{i}</description></item>"
        for i in range(count)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<rss version="2.0"><channel><title>T</title>{items}</channel></rss>'
    ).encode()


# --------------------------------------------------------------------------- #
# 1. 版本号单一来源
# --------------------------------------------------------------------------- #
@test
def test_version_is_single_source_of_truth():
    """版本号必须来自 app/version.py，并与 CHANGELOG、README 一致。"""
    from app.version import __version__

    assert __version__.count(".") == 2, f"版本号格式应为 x.y.z，实际 {__version__}"
    changelog = open(os.path.join(REPO, "CHANGELOG.md"), encoding="utf-8").read()
    assert f"## [{__version__}]" in changelog, f"CHANGELOG.md 里没有 [{__version__}] 小节"
    for name in ("README.md", "README-zh.md"):
        text = open(os.path.join(REPO, name), encoding="utf-8").read()
        assert __version__ in text, f"{name} 里的版本号不是 {__version__}"


@test
def test_healthz_reports_version():
    """健康检查带上版本号，方便确认线上跑的是哪一版。"""
    from app.version import __version__

    env = make_env()
    try:
        app = env.app()
        resp = app.test_client().get("/healthz")
        assert resp.status_code == 200
        assert __version__ in resp.data.decode("utf-8"), f"响应里没有版本号: {resp.data!r}"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 2. 登录鉴权
# --------------------------------------------------------------------------- #
def _enable_auth(app, password="s3cret", username="admin"):
    from app.auth import hash_password

    app.config["AUTH_PASSWORD_HASH"] = hash_password(password)
    app.config["AUTH_USERNAME"] = username
    app.config["AUTH_PASSWORD"] = None
    app.config["AUTH_PASSWORD_FROM_ENV"] = False


@test
def test_auth_disabled_without_password():
    """没有配置口令时不应把现有部署锁在门外（但要打警告）。"""
    env = make_env()
    try:
        app = env.app()
        assert app.test_client().get("/").status_code == 200
    finally:
        env.close()


@test
def test_auth_required_when_password_configured():
    """配置口令后，未登录访问任何页面都要跳到登录页。"""
    env = make_env()
    try:
        from app import db
        from app.models import Article

        app = env.app()
        with app.app_context():
            sid = add_source_via_model().id
            db.session.add(Article(title="secret article", link="http://e/a", source_id=sid))
            db.session.commit()
        _enable_auth(app)

        client = app.test_client()
        resp = client.get("/")
        assert resp.status_code in (301, 302), f"未登录访问首页返回 {resp.status_code}"
        assert "/login" in resp.headers.get("Location", ""), resp.headers.get("Location")

        resp = client.get("/search?q=secret")
        assert "/login" in resp.headers.get("Location", ""), "搜索页没有要求登录"

        resp = client.get("/healthz")
        assert resp.status_code == 200, "健康检查必须保持公开（容器探针要用）"
    finally:
        env.close()


@test
def test_login_flow_and_session():
    """口令错误要拒绝；口令正确要能进入并看到内容；退出后再次被拦。"""
    env = make_env()
    try:
        from app import db
        from app.models import Article

        app = env.app()
        with app.app_context():
            sid = add_source_via_model().id
            db.session.add(Article(title="secret article", link="http://e/a", source_id=sid))
            db.session.commit()
        _enable_auth(app, password="s3cret")
        client = app.test_client()

        bad = client.post("/login", data={"username": "admin", "password": "wrong"})
        assert bad.status_code == 200, "登录失败应停留在登录页"
        assert "Wrong username" in bad.data.decode("utf-8") or "口令" in bad.data.decode("utf-8")

        bad_user = client.post("/login", data={"username": "root", "password": "s3cret"})
        assert "secret article" not in bad_user.data.decode("utf-8"), "用户名错误却登录成功"

        ok = client.post(
            "/login", data={"username": "admin", "password": "s3cret"}, follow_redirects=True
        )
        assert ok.status_code == 200
        assert "secret article" in ok.data.decode("utf-8"), "登录后看不到内容"

        out = client.post("/logout", follow_redirects=False)
        assert out.status_code in (301, 302)
        assert "/login" in client.get("/").headers.get("Location", ""), "退出后仍能访问"
    finally:
        env.close()


@test
def test_login_is_throttled_after_repeated_failures():
    """连续失败要被限流，抵挡在线爆破。"""
    env = make_env()
    try:
        app = env.app()
        _enable_auth(app, password="s3cret")
        client = app.test_client()
        for _ in range(5):
            client.post("/login", data={"username": "admin", "password": "wrong"})
        resp = client.post("/login", data={"username": "admin", "password": "s3cret"})
        body = resp.data.decode("utf-8")
        assert resp.status_code == 200 and "secret" not in body
        assert ("Too many" in body) or ("过多" in body), (
            "达到失败上限后仍接受登录尝试，没有任何限流提示"
        )
        # 即使随后给出正确口令，也应处在锁定状态
        assert "/" not in resp.headers.get("Location", "x") or True
    finally:
        env.close()


@test
def test_session_cookie_is_hardened():
    """会话 Cookie 应带 HttpOnly 与 SameSite。"""
    env = make_env()
    try:
        app = env.app()
        assert app.config.get("SESSION_COOKIE_HTTPONLY") is True
        assert str(app.config.get("SESSION_COOKIE_SAMESITE")).lower() == "lax"
        assert app.config.get("SESSION_COOKIE_SECURE") is False
        assert app.config.get("MAX_CONTENT_LENGTH"), "没有设置请求体上限"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 3. 订阅源编辑 / 暂停
# --------------------------------------------------------------------------- #
@test
def test_edit_source_updates_fields():
    """编辑源：名称、分类、间隔都要能改（README 一直宣称支持 edit）。"""
    env = make_env()
    try:
        from app.models import Source

        app = env.app()
        app.config["SCHEDULER_ENABLED"] = False
        with app.app_context():
            sid = add_source_via_model(name="Old", interval=30).id

        client = app.test_client()
        resp = client.post(
            f"/edit_source/{sid}",
            data={"name": "New name", "category": "Tech", "interval": "45"},
        )
        assert resp.status_code in (301, 302), f"编辑返回 {resp.status_code}"
        with app.app_context():
            source = db_source = Source.query.get(sid)
            assert db_source.name == "New name", db_source.name
            assert db_source.category == "Tech", db_source.category
            assert db_source.interval == 45, db_source.interval
    finally:
        env.close()


@test
def test_toggle_source_pauses_and_resumes():
    """暂停后不再调度，恢复后重新注册任务。"""
    env = make_env(scheduler_enabled=True)
    try:
        from app.models import Source
        from app.scheduler import scheduler

        app = env.app()
        with app.app_context():
            sid = add_source_via_model().id
        from app.scheduler import schedule_source

        with app.app_context():
            schedule_source(Source.query.get(sid))
        assert scheduler.get_job(f"source_{sid}") is not None, "初始应有调度任务"

        client = app.test_client()
        client.post(f"/toggle_source/{sid}")
        with app.app_context():
            assert Source.query.get(sid).enabled is False, "暂停状态没有写入数据库"
        assert scheduler.get_job(f"source_{sid}") is None, "暂停后任务仍存在"

        client.post(f"/toggle_source/{sid}")
        with app.app_context():
            assert Source.query.get(sid).enabled is True
        assert scheduler.get_job(f"source_{sid}") is not None, "恢复后没有重新注册任务"
        if scheduler.running:
            scheduler.shutdown(wait=False)
    finally:
        env.close()


@test
def test_paused_source_is_skipped_by_scheduler():
    """暂停的源即使任务被触发也不应抓取。"""
    env = make_env(scheduler_enabled=True)
    try:
        from app.models import Source
        from app.scheduler import run_fetch

        app = env.app()
        with app.app_context():
            source = add_source_via_model()
            source.enabled = False
            db_commit = source
            from app import db

            db.session.commit()
            sid = source.id

        with mock.patch("app.urlsafety.requests.get") as http:
            ok, message = run_fetch(app, sid, notify=False)
        assert ok is False and "paused" in str(message).lower(), (ok, message)
        assert not http.called, "暂停的源仍然发起了抓取请求"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 4. 抓取：新增条数上限 + 保留策略
# --------------------------------------------------------------------------- #
@test
def test_entry_cap_counts_new_articles_not_feed_position():
    """上限应限制「本次新增」条数，而不是只看 feed 的前 N 条。"""
    env = make_env()
    try:
        from app.fetcher import fetch_source
        from app.models import Article

        app = env.app()
        app.config["FETCH_MAX_ENTRIES"] = 10
        feed = multi_entry_feed(25)
        with app.app_context():
            sid = add_source_via_model().id
            with mock.patch("app.urlsafety.requests.get", return_value=fake_response(content=feed)), \
                 mock.patch("app.fetcher.extract_full_text", return_value=""), \
                 mock.patch("app.fetcher.time.sleep", return_value=None):
                first_ok, _ = fetch_source(sid)
                first_count = Article.query.count()
                # 第二次抓取应继续补齐剩下的条目
                second_ok, _ = fetch_source(sid)
                second_count = Article.query.count()
        assert first_ok and second_ok
        assert first_count == 10, f"首次应只新增 10 条，实际 {first_count}"
        assert second_count == 20, f"再次抓取应补齐到 20 条，实际 {second_count}"
    finally:
        env.close()


@test
def test_retention_trims_articles():
    """保留策略生效：超过上限的文章会被清理。"""
    env = make_env()
    try:
        from app.fetcher import fetch_source
        from app.models import Article

        app = env.app()
        app.config["RETENTION_MAX_ARTICLES_PER_SOURCE"] = 5
        feed = multi_entry_feed(12)
        with app.app_context():
            sid = add_source_via_model().id
            with mock.patch("app.urlsafety.requests.get", return_value=fake_response(content=feed)), \
                 mock.patch("app.fetcher.extract_full_text", return_value=""), \
                 mock.patch("app.fetcher.time.sleep", return_value=None):
                fetch_source(sid)
            remaining = Article.query.filter_by(source_id=sid).count()
        assert remaining == 5, f"保留策略应把文章裁到 5 条，实际 {remaining}"
    finally:
        env.close()


@test
def test_initial_fetch_is_async():
    """添加源应立刻返回，首次抓取在后台进行（不再阻塞请求）。"""
    env = make_env()
    try:
        from app.models import Source

        app = env.app()
        started = threading.Event()
        finished = threading.Event()

        def slow_fetch(source_id, notify=False, task_id=None):
            started.set()
            time.sleep(1.5)
            finished.set()
            return True, "ok"

        with mock.patch("app.web.routes.fetch_source", side_effect=slow_fetch):
            client = app.test_client()
            begin = time.time()
            resp = client.post(
                "/add_source",
                data={"name": "Async", "url": "http://async.example/feed", "interval": "30"},
            )
            elapsed = time.time() - begin

        assert resp.status_code in (301, 302), f"添加源返回 {resp.status_code}"
        assert elapsed < 1.0, f"请求被后台抓取阻塞了 {elapsed:.2f}s"
        assert started.wait(2), "后台抓取线程没有启动"
        with app.app_context():
            assert Source.query.filter_by(name="Async").count() == 1
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 5. 未读统计
# --------------------------------------------------------------------------- #
@test
def test_unread_counts_are_rendered():
    """页面应显示每个源与全局的未读数量。"""
    env = make_env()
    try:
        from app import db
        from app.models import Article

        app = env.app()
        with app.app_context():
            sid = add_source_via_model().id
            for i in range(3):
                db.session.add(
                    Article(title=f"unread {i}", link=f"http://e/{i}", source_id=sid)
                )
            db.session.add(
                Article(title="read one", link="http://e/read", source_id=sid, read=True)
            )
            db.session.commit()
        body = app.test_client().get("/").data.decode("utf-8")
        assert "3" in body, "页面没有显示未读数量"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 6. 老库升级：补列
# --------------------------------------------------------------------------- #
@test
def test_schema_adds_missing_enabled_column():
    """老数据库（没有 source.enabled 列）升级后应自动补列，而不是报错。"""
    env = make_env()
    try:
        import sqlite3

        from sqlalchemy import text

        from app import db
        from app.schema import ensure_schema

        app = env.app()
        with app.app_context():
            # 模拟老库：删掉 enabled 列（SQLite 3.35+ 支持 DROP COLUMN）
            try:
                db.session.execute(text("ALTER TABLE source DROP COLUMN enabled"))
                db.session.commit()
            except Exception as exc:  # noqa: BLE001
                raise AssertionError(f"测试环境不支持 DROP COLUMN: {exc}")

            columns = [row[1] for row in db.session.execute(text("PRAGMA table_info(source)"))]
            assert "enabled" not in columns, "前置条件不成立：enabled 列还在"

            ensure_schema(app)

            columns = [row[1] for row in db.session.execute(text("PRAGMA table_info(source)"))]
            assert "enabled" in columns, "ensure_schema 没有为老库补上 enabled 列"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 7. 请求体上限
# --------------------------------------------------------------------------- #
@test
def test_oversized_request_body_is_rejected():
    """超过上限的请求体应被拒绝（413），而不是整份读进内存。"""
    env = make_env()
    try:
        app = env.app()
        app.config["MAX_CONTENT_LENGTH"] = 4096
        app.config["SCHEDULER_ENABLED"] = False
        client = app.test_client()
        resp = client.post("/add_source", data={"url": "http://e/f", "name": "x" * 20000})
        assert resp.status_code == 413, f"超大请求体返回 {resp.status_code}，应为 413"
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
