"""RSS-Aggregator 缺陷回归测试。

设计目标：同一份测试在「修复前」失败、在「修复后」通过，作为缺陷的客观证据。
不依赖 pytest：直接 `python tests/test_bugfixes.py` 即可运行；有 pytest 时也能被收集。

约定的工作方式：每个用例在临时目录里建一份独立配置 + 独立 SQLite 库，
并重置 ConfigLoader 单例，避免用例之间互相污染。
"""
import os
import sys
import shutil
import tempfile
import traceback
from unittest import mock
from xml.etree import ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Test Feed</title>
  <item>
    <title>Hello World</title>
    <link>http://example.com/a</link>
    <description>first</description>
    <pubDate>Mon, 02 Jan 2006 15:04:05 GMT</pubDate>
  </item>
  <item>
    <title>Second</title>
    <link>http://example.com/b</link>
    <description>second</description>
    <pubDate>Tue, 03 Jan 2006 15:04:05 GMT</pubDate>
  </item>
</channel></rss>
"""

SAMPLE_OPML = b"""<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.0"><head><title>t</title></head><body>
  <outline text="Tech" title="Tech">
    <outline type="rss" text="FeedA" title="FeedA" xmlUrl="http://a.example/feed"/>
    <outline type="rss" text="FeedB" title="FeedB" xmlUrl="http://b.example/feed"/>
  </outline>
  <outline type="rss" text="FeedC" title="FeedC" xmlUrl="http://c.example/feed"/>
</body></opml>
"""


class Env:
    """一个隔离的应用环境：临时工作目录 + 临时配置 + 临时数据库。"""

    def __init__(self, scheduler_enabled=False, name="cfg.yaml"):
        self.tmp = tempfile.mkdtemp(prefix="rssagg-test-")
        self.cwd = os.getcwd()
        self.scheduler_enabled = scheduler_enabled
        self.cfg = os.path.join(self.tmp, name)
        db_path = os.path.join(self.tmp, "rss.db").replace("\\", "/")
        with open(self.cfg, "w", encoding="utf-8") as fh:
            fh.write(
                "app:\n"
                '  name: "RSS Aggregator"\n'
                '  language: "en"\n'
                '  timezone: "Asia/Shanghai"\n'
                "database:\n"
                f'  url: "sqlite:///{db_path}"\n'
                "scheduler:\n"
                f"  enabled: {str(scheduler_enabled).lower()}\n"
                "  default_interval: 30\n"
                "server:\n"
                '  host: "127.0.0.1"\n'
                "  port: 5000\n"
                "  debug: false\n"
                "notifications:\n"
                "  email:\n"
                "    enabled: false\n"
            )
        self.reset_config_singleton()
        os.chdir(self.tmp)

    @staticmethod
    def reset_config_singleton():
        from app.config import ConfigLoader

        for attr in ("_instance", "_config", "_config_path"):
            if hasattr(ConfigLoader, attr):
                setattr(ConfigLoader, attr, None)

    def app(self):
        """按 main.py 的顺序引导应用：建表 → 启动调度器。

        修复前没有 app.schema / 调度器由 create_app 自行启动，这里做兼容处理，
        让同一份用例在修复前后都能跑到真正的断言点。
        """
        from app import create_app, db

        # 任务登记表是进程级的：用例之间要清空，否则上一个用例留下的
        # 「运行中」任务会占用并发额度，影响后续用例。
        try:
            from app import tasks as task_registry

            task_registry.reset()
        except ImportError:
            pass

        application = create_app(self.cfg)
        with application.app_context():
            db.create_all()
            try:
                from app.schema import ensure_schema
            except ImportError:
                pass
            else:
                ensure_schema(application)
        if self.scheduler_enabled:
            from app.scheduler import init_scheduler

            init_scheduler(application)
        return application

    def close(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)


def make_env(**kw):
    return Env(**kw)


class FakeResponse:
    """requests 响应的替身，覆盖 app.urlsafety.safe_get 用到的接口。

    safe_get 以 stream=True 发起请求、逐块读取并限制大小，
    因此替身必须提供 headers / iter_content / close，而不只是 content。
    """

    def __init__(self, content=SAMPLE_RSS, status=200, headers=None):
        self.status_code = status
        self.content = content
        self.headers = dict(headers or {})
        self.closed = False

    @property
    def text(self):
        return self.content.decode("utf-8", "ignore")

    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def close(self):
        self.closed = True


def fake_response(content=SAMPLE_RSS, status=200, headers=None):
    return FakeResponse(content, status, headers)


def add_source_via_model(name="Test Feed", url="http://example.com/feed", interval=30):
    from app import db
    from app.models import Source

    src = Source(name=name, url=url, interval=interval)
    db.session.add(src)
    db.session.commit()
    return src


# --------------------------------------------------------------------------- #
# 1. 配置加载
# --------------------------------------------------------------------------- #
@test
def test_config_get_returns_configured_value():
    """ConfigLoader.get() 必须返回配置文件里的值，而不是永远返回默认值。"""
    env = make_env()
    try:
        from app.config import ConfigLoader

        ConfigLoader(env.cfg)
        assert ConfigLoader.get("app.name") == "RSS Aggregator", (
            f"get('app.name') 返回 {ConfigLoader.get('app.name')!r}，应为 'RSS Aggregator'"
        )
        assert ConfigLoader.get("server.port") == 5000
        assert ConfigLoader.get("scheduler.default_interval") == 30
        assert ConfigLoader.get("no.such.key", "fallback") == "fallback"
    finally:
        env.close()


@test
def test_config_singleton_follows_new_path():
    """换一个配置文件路径再构造，应加载新文件（单例不能吃掉新路径）。"""
    env = make_env()
    try:
        from app.config import ConfigLoader

        other = os.path.join(env.tmp, "other.yaml")
        with open(other, "w", encoding="utf-8") as fh:
            fh.write('app:\n  name: "Other App"\n')
        ConfigLoader(env.cfg)
        ConfigLoader(other)
        assert ConfigLoader.get("app.name") == "Other App", (
            "第二个配置文件被忽略，ConfigLoader 单例没有跟随路径更新"
        )
    finally:
        env.close()


@test
def test_create_app_builds_flask_config():
    """create_app() 必须能正常构造，并把 YAML 映射成 Flask 配置。"""
    env = make_env()
    try:
        app = env.app()
        assert app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///")
        assert app.config["SCHEDULER_ENABLED"] is False
        assert app.config["APP_LANGUAGE"] == "en"
        assert app.config["SECRET_KEY"], "未配置 SECRET_KEY，flash()/session 会直接抛错"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 2. Web 层
# --------------------------------------------------------------------------- #
@test
def test_pages_render():
    """首页/搜索页必须能渲染：模板目录不在 Flask 默认查找路径内时整站 500。"""
    env = make_env()
    try:
        from app import db
        from app.models import Article

        app = env.app()
        with app.app_context():
            sid = add_source_via_model().id
            db.session.add(Article(title="render me", link="http://example.com/r", source_id=sid))
            db.session.commit()
        client = app.test_client()
        index = client.get("/")
        assert index.status_code == 200, f"首页返回 {index.status_code}"
        assert "render me" in index.data.decode("utf-8"), "首页没有渲染文章列表"
        search = client.get("/search?q=render")
        assert search.status_code == 200, f"搜索页返回 {search.status_code}"
        import re as _re

        _plain = _re.sub(r"<[^>]+>", "", search.data.decode("utf-8"))
        assert "render me" in _plain, "搜索页没有渲染结果"
    finally:
        env.close()


@test
def test_add_source_flash_does_not_crash():
    """add_source 会调用 flash()，没有 SECRET_KEY 时必然 500。"""
    env = make_env()
    try:
        app = env.app()
        app.config["SCHEDULER_ENABLED"] = False
        with mock.patch("app.web.routes.fetch_source", return_value=(True, "ok")):
            client = app.test_client()
            resp = client.post(
                "/add_source",
                data={"name": "N", "url": "http://example.com/feed", "interval": "30"},
            )
        assert resp.status_code in (301, 302), f"状态码 {resp.status_code}，期望重定向"
    finally:
        env.close()


@test
def test_add_source_rejects_non_numeric_interval():
    """interval 传非数字不能 500。"""
    env = make_env()
    try:
        app = env.app()
        app.config["SCHEDULER_ENABLED"] = False
        with mock.patch("app.web.routes.fetch_source", return_value=(True, "ok")):
            client = app.test_client()
            resp = client.post(
                "/add_source",
                data={"name": "N", "url": "http://example.com/feed", "interval": "abc"},
            )
        assert resp.status_code < 500, f"状态码 {resp.status_code}，非数字 interval 导致服务器错误"
    finally:
        env.close()


@test
def test_add_source_clamps_interval_to_minimum():
    """README 承诺最小 5 分钟，interval=0 / 负数必须被纠正。"""
    env = make_env()
    try:
        from app.models import Source

        app = env.app()
        app.config["SCHEDULER_ENABLED"] = False
        with mock.patch("app.web.routes.fetch_source", return_value=(True, "ok")):
            client = app.test_client()
            client.post("/add_source", data={"name": "Z", "url": "http://z.example/feed", "interval": "0"})
            client.post("/add_source", data={"name": "N", "url": "http://n.example/feed", "interval": "-7"})
            with app.app_context():
                intervals = {s.url: s.interval for s in Source.query.all()}
        assert intervals["http://z.example/feed"] >= 5, f"interval 未纠正: {intervals}"
        assert intervals["http://n.example/feed"] >= 5, f"interval 未纠正: {intervals}"
    finally:
        env.close()


@test
def test_add_source_duplicate_url_does_not_crash():
    """同一个 URL 重复添加应被友好拒绝，而不是 IntegrityError 500。"""
    env = make_env()
    try:
        from app.models import Source

        app = env.app()
        app.config["SCHEDULER_ENABLED"] = False
        with mock.patch("app.web.routes.fetch_source", return_value=(True, "ok")):
            client = app.test_client()
            data = {"name": "D", "url": "http://dup.example/feed", "interval": "30"}
            first = client.post("/add_source", data=data)
            second = client.post("/add_source", data=data)
            with app.app_context():
                count = Source.query.filter_by(url=data["url"]).count()
        assert first.status_code < 500 and second.status_code < 500, (
            f"重复添加返回 {first.status_code}/{second.status_code}"
        )
        assert count == 1, f"数据库里有 {count} 条同名源"
    finally:
        env.close()


@test
def test_state_changing_routes_reject_get():
    """删除/刷新会改状态，不能只靠 GET（浏览器预取即可删库）。"""
    env = make_env()
    try:
        app = env.app()
        app.config["SCHEDULER_ENABLED"] = False
        with app.app_context():
            sid = add_source_via_model().id
        client = app.test_client()
        with mock.patch("app.web.routes.fetch_source", return_value=(True, "ok")):
            assert client.get(f"/delete_source/{sid}").status_code == 405, "GET 仍能删除源"
            assert client.get(f"/refresh_source/{sid}").status_code == 405, "GET 仍能触发抓取"
            assert client.post(f"/refresh_source/{sid}").status_code in (301, 302)
            assert client.post(f"/delete_source/{sid}").status_code in (301, 302)
    finally:
        env.close()


@test
def test_pagination_keeps_search_query():
    """搜索结果翻页必须带上 q / source_id / unread，否则第二页变成全量列表。"""
    env = make_env()
    try:
        app = env.app()
        with app.app_context():
            from app import db
            from app.models import Article

            sid = add_source_via_model().id
            for i in range(60):
                db.session.add(
                    Article(
                        title=f"needle {i}",
                        link=f"http://example.com/{i}",
                        summary="needle",
                        source_id=sid,
                    )
                )
            db.session.commit()
        client = app.test_client()
        html = client.get("/search?q=needle&unread=true").data.decode("utf-8")
        assert "page=2" in html, "没有渲染下一页链接，用例前提不成立"
        next_links = [seg for seg in html.split('href="') if "page=2" in seg]
        assert any("q=needle" in seg for seg in next_links), (
            "翻页链接丢失了搜索条件 q=needle"
        )
    finally:
        env.close()


@test
def test_pagination_keeps_language():
    """切换语言后翻页必须保持语言，否则每翻一页语言被重置。"""
    env = make_env()
    try:
        app = env.app()
        with app.app_context():
            from app import db
            from app.models import Article

            sid = add_source_via_model().id
            for i in range(60):
                db.session.add(Article(title=f"t{i}", link=f"http://example.com/l{i}", source_id=sid))
            db.session.commit()
        client = app.test_client()
        html = client.get("/?lang=zh").data.decode("utf-8")
        next_links = [seg for seg in html.split('href="') if "page=2" in seg]
        assert next_links, "没有渲染下一页链接"
        assert any("lang=zh" in seg for seg in next_links), "翻页链接丢失了 lang=zh"
    finally:
        env.close()


@test
def test_search_escapes_like_wildcards():
    """LIKE 通配符必须转义：搜索 '%' 不应匹配所有文章。"""
    env = make_env()
    try:
        app = env.app()
        with app.app_context():
            from app import db
            from app.models import Article

            sid = add_source_via_model().id
            db.session.add(Article(title="ordinary", link="http://example.com/o", source_id=sid))
            db.session.commit()
        client = app.test_client()
        html = client.get("/search?q=%25").data.decode("utf-8")
        assert "ordinary" not in html, "通配符 % 被当成 LIKE 语法，搜索退化成全量匹配"
    finally:
        env.close()


@test
def test_unread_workflow_is_reachable():
    """unread 过滤要有闭环：必须存在把文章标记为已读的入口。"""
    env = make_env()
    try:
        app = env.app()
        with app.app_context():
            from app import db
            from app.models import Article

            sid = add_source_via_model().id
            art = Article(title="unread me", link="http://example.com/u", source_id=sid)
            db.session.add(art)
            db.session.commit()
            aid = art.id
        client = app.test_client()
        resp = client.post(f"/article/{aid}/read")
        assert resp.status_code in (301, 302), f"标记已读接口返回 {resp.status_code}"
        with app.app_context():
            assert Article.query.get(aid).read is True
        html = client.get("/search?unread=true").data.decode("utf-8")
        assert "unread me" not in html, "已读文章仍出现在未读列表里"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 3. 抓取与调度
# --------------------------------------------------------------------------- #
@test
def test_fetch_source_succeeds_and_dedupes():
    """基准能力：抓取入库、重复抓取不产生重复文章。"""
    env = make_env()
    try:
        from app.fetcher import fetch_source

        app = env.app()
        with app.app_context():
            sid = add_source_via_model().id
            with mock.patch("app.urlsafety.requests.get", return_value=fake_response()), \
                 mock.patch("app.fetcher.extract_full_text", return_value=""):
                ok, msg = fetch_source(sid)
                assert ok, f"首次抓取失败: {msg}"
                ok2, msg2 = fetch_source(sid)
                assert ok2, f"二次抓取失败: {msg2}"
            from app.models import Article

            assert Article.query.count() == 2, "重复抓取产生了重复文章"
            assert Article.query.filter_by(link="http://example.com/a").count() == 1
    finally:
        env.close()


@test
def test_scheduled_job_runs_without_request_context():
    """调度器在后台线程执行任务，fetch_source 必须在没有应用上下文时也能跑。"""
    env = make_env(scheduler_enabled=True)
    try:
        from app.scheduler import scheduler
        from app.models import Source

        app = env.app()
        from app.scheduler import schedule_source

        with app.app_context():
            src = add_source_via_model()
            sid = src.id
            schedule_source(src)
        job = scheduler.get_job(f"source_{sid}")
        assert job is not None, "没有注册调度任务"
        # 模拟 APScheduler 的后台线程：不推任何应用上下文，直接调用任务函数
        with mock.patch("app.urlsafety.requests.get", return_value=fake_response()), \
             mock.patch("app.fetcher.extract_full_text", return_value=""):
            result = job.func(*job.args, **job.kwargs)
        assert result is None or result[0] is True, f"后台任务没有成功执行: {result!r}"
        with app.app_context():
            assert Source.query.get(sid).last_fetched is not None, "后台任务未真正抓取"
        if scheduler.running:
            scheduler.shutdown(wait=False)
    finally:
        env.close()


@test
def test_add_source_registers_schedule():
    """新增源必须立刻进入调度，否则要等重启才会自动更新。"""
    env = make_env(scheduler_enabled=True)
    try:
        from app.scheduler import scheduler
        from app.models import Source

        app = env.app()
        with mock.patch("app.web.routes.fetch_source", return_value=(True, "ok")):
            client = app.test_client()
            client.post(
                "/add_source",
                data={"name": "S", "url": "http://s.example/feed", "interval": "30"},
            )
        with app.app_context():
            sid = Source.query.filter_by(url="http://s.example/feed").first().id
        assert scheduler.get_job(f"source_{sid}") is not None, "新增的源没有注册调度任务"
        if scheduler.running:
            scheduler.shutdown(wait=False)
    finally:
        env.close()


@test
def test_init_scheduler_honours_disabled_flag():
    """config 里 scheduler.enabled=false 时不得启动调度器。"""
    env = make_env(scheduler_enabled=False)
    try:
        from app.scheduler import scheduler

        app = env.app()
        from app.scheduler import init_scheduler

        init_scheduler(app)
        assert not scheduler.running, "SCHEDULER_ENABLED=false 仍然启动了调度器"
    finally:
        if _scheduler_running():
            from app.scheduler import scheduler as sched

            sched.shutdown(wait=False)
        env.close()


def _scheduler_running():
    try:
        from app.scheduler import scheduler

        return scheduler.running
    except Exception:
        return False


@test
def test_fetch_failure_leaves_session_clean():
    """抓取中途出错必须回滚：否则待提交对象残留，会被后续任意一次 commit 偷偷写库。"""
    env = make_env()
    try:
        from app import db
        from app.fetcher import fetch_source
        from app.models import Article, Source

        app = env.app()
        with app.app_context():
            sid = add_source_via_model().id

            # 第一条抽取成功（文章已 add），第二条抛错 → 走失败分支
            with mock.patch("app.urlsafety.requests.get", return_value=fake_response()), \
                 mock.patch(
                     "app.fetcher.extract_full_text",
                     side_effect=["fulltext-a", RuntimeError("fulltext boom")],
                 ), \
                 mock.patch("app.fetcher.time.sleep", return_value=None):
                ok, msg = fetch_source(sid)
            assert ok is False, f"抓取失败却报告成功: {msg}"

            pending = list(db.session.new)
            assert not pending, (
                f"抓取失败后 session 里残留 {len(pending)} 个待提交对象，"
                "它们会被下一次 commit 悄悄写进数据库"
            )
            assert db.session.query(Source).count() == 1
            assert db.session.query(Article).count() == 0
    finally:
        env.close()


@test
def test_article_link_is_unique():
    """同一链接不能入库两次（并发抓取会重复插入）。"""
    env = make_env()
    try:
        from app import db
        from app.models import Article
        from sqlalchemy.exc import IntegrityError

        app = env.app()
        with app.app_context():
            sid = add_source_via_model().id
            db.session.add(Article(title="a", link="http://example.com/same", source_id=sid))
            db.session.commit()
            db.session.add(Article(title="b", link="http://example.com/same", source_id=sid))
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                return
            raise AssertionError("article.link 没有唯一约束，重复链接被成功写入")
    finally:
        env.close()


@test
def test_fetch_tolerates_concurrent_insert():
    """并发抓取撞上唯一约束时应回退重试，而不是整批失败。"""
    env = make_env()
    try:
        from app import db
        from app.fetcher import fetch_source
        from app.models import Article

        app = env.app()
        with app.app_context():
            sid = add_source_via_model().id
            # 模拟检查与插入之间被另一线程抢先写入：预查返回空，实际库里已有一条
            with mock.patch("app.urlsafety.requests.get", return_value=fake_response()), \
                 mock.patch("app.fetcher.extract_full_text", return_value=""), \
                 mock.patch("app.fetcher._existing_links", return_value=set()):
                ok, msg = fetch_source(sid)
            assert ok, f"并发冲突导致整批抓取失败: {msg}"
            assert Article.query.filter_by(link="http://example.com/a").count() == 1
            assert Article.query.filter_by(link="http://example.com/b").count() == 1
    finally:
        env.close()


@test
def test_fetch_rejects_unsafe_url():
    """抓取用户可控 URL 必须有 SSRF 防护（内网/回环/元数据地址）。"""
    env = make_env()
    try:
        from app.fetcher import fetch_source

        app = env.app()
        with app.app_context():
            sid = add_source_via_model(url="http://169.254.169.254/latest/meta-data/").id
            with mock.patch("app.urlsafety.requests.get") as http:
                ok, msg = fetch_source(sid)
            assert not http.called, "对内网元数据地址发起了请求（SSRF）"
            assert ok is False
    finally:
        env.close()


@test
def test_url_safety_helper():
    """URL 安全校验的单元行为。"""
    try:
        from app.urlsafety import is_safe_url
    except ImportError as exc:  # pragma: no cover
        raise AssertionError(f"缺少 URL 安全校验模块: {exc}")

    def resolver_public(host, port):
        return ["93.184.216.34"]

    def resolver_private(host, port):
        return ["127.0.0.1"] if host == "localhost" else ["10.0.0.5"]

    assert is_safe_url("http://example.com/feed", resolver=resolver_public)
    assert not is_safe_url("http://localhost:5000/feed", resolver=resolver_private)
    assert not is_safe_url("http://intranet.local/feed", resolver=resolver_private)
    assert not is_safe_url("file:///etc/passwd", resolver=resolver_public)
    assert not is_safe_url("gopher://example.com/", resolver=resolver_public)
    assert not is_safe_url("http://example.com:0/feed", resolver=resolver_public)


# --------------------------------------------------------------------------- #
# 4. OPML
# --------------------------------------------------------------------------- #
@test
def test_opml_import_works():
    """OPML 导入必须真的导入（原实现用了 stdlib 不存在的 getparent）。"""
    env = make_env()
    try:
        from app.opml import import_opml
        from app.models import Source

        app = env.app()
        with app.app_context():
            ok, failed = import_opml(SAMPLE_OPML.decode("utf-8"))
            assert failed == 0, f"导入报告了 {failed} 个失败"
            assert ok == 3, f"成功导入 {ok} 个，期望 3 个"
            cats = {s.url: s.category for s in Source.query.all()}
            assert cats["http://a.example/feed"] == "Tech", f"分类丢失: {cats}"
            assert cats["http://c.example/feed"] == "Imported", f"顶层分类错误: {cats}"
    finally:
        env.close()


@test
def test_opml_import_is_idempotent():
    """重复导入同一份 OPML 不应产生重复源。"""
    env = make_env()
    try:
        from app.opml import import_opml
        from app.models import Source

        app = env.app()
        with app.app_context():
            import_opml(SAMPLE_OPML.decode("utf-8"))
            import_opml(SAMPLE_OPML.decode("utf-8"))
            assert Source.query.count() == 3, f"重复导入产生了 {Source.query.count()} 条记录"
    finally:
        env.close()


@test
def test_opml_import_rejects_unsafe_feed_url():
    """导入的订阅地址同样要过安全校验。"""
    env = make_env()
    try:
        from app.opml import import_opml
        from app.models import Source

        app = env.app()
        bad = (
            '<opml version="1.0"><body>'
            '<outline type="rss" text="X" xmlUrl="http://127.0.0.1:8080/feed"/>'
            "</body></opml>"
        )
        with app.app_context():
            import_opml(bad)
            assert Source.query.count() == 0, "导入时未拦截内网地址"
    finally:
        env.close()


@test
def test_opml_export_roundtrip():
    """导出结果必须是可解析的 XML，并保留分类与地址。"""
    env = make_env()
    try:
        from app.opml import export_opml
        from app.models import Source
        from app import db

        app = env.app()
        with app.app_context():
            db.session.add(Source(name="A", url="http://a.example/feed", category="Tech"))
            db.session.commit()
            out = export_opml()
        root = ET.fromstring(out)
        urls = [o.get("xmlUrl") for o in root.iter("outline") if o.get("xmlUrl")]
        assert urls == ["http://a.example/feed"], f"导出内容异常: {urls}"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 5. 部署与工程一致性
# --------------------------------------------------------------------------- #
@test
def test_dockerfile_exists_at_build_context_root():
    """docker-compose 用 `build: .`，构建上下文根目录必须有 Dockerfile。"""
    root_dockerfile = os.path.join(REPO, "Dockerfile")
    assert os.path.isfile(root_dockerfile), (
        "仓库根目录没有 Dockerfile（只有 app/.dockerfile），`docker compose build` 必然失败"
    )
    content = open(root_dockerfile, encoding="utf-8").read()
    assert "/app/data" in content, "容器内没有把数据库放到被挂载的 /app/data 目录"


@test
def test_sqlite_path_is_persisted_in_compose():
    """compose 把 ./data 挂到 /app/data（WORKDIR=/app），DB 必须落在 data/ 下才持久。"""
    import re

    cfg = open(os.path.join(REPO, "config.yaml"), encoding="utf-8").read()
    m = re.search(r'url:\s*"([^"]+)"', cfg)
    assert m, f"config.yaml 里找不到 database.url:\n{cfg}"
    url = m.group(1)
    if url.startswith("sqlite:////"):  # 绝对路径
        assert url.endswith("/app/data/rss.db") or "/data/" in url, f"绝对路径不在挂载卷内: {url}"
    else:
        path = url.replace("sqlite:///", "")
        assert path.startswith("data/"), (
            f"数据库路径 {path!r} 不在 data/ 下；容器里会落到未挂载的位置，重启即丢数据"
        )
    compose = open(os.path.join(REPO, "docker-compose.yaml"), encoding="utf-8").read()
    assert "./data:/app/data" in compose, "compose 没有把 ./data 挂载到 /app/data"


@test
def test_relative_sqlite_path_anchored_to_project_root():
    """相对 sqlite 路径必须锚定到项目根目录，而不是 Flask 的 instance 目录。"""
    try:
        from app.config import normalize_sqlite_uri
    except ImportError as exc:  # pragma: no cover
        raise AssertionError(f"缺少 normalize_sqlite_uri: {exc}")

    base = os.path.join(REPO, "data").replace("\\", "/")
    uri = normalize_sqlite_uri("sqlite:///data/rss.db", base_dir=REPO)
    assert uri == f"sqlite:///{base}/rss.db", f"得到 {uri}"
    assert os.path.isdir(os.path.join(REPO, "data")), "没有自动创建数据库目录"

    abs_uri = "sqlite:////var/lib/rss/rss.db"
    assert normalize_sqlite_uri(abs_uri, base_dir=REPO) == abs_uri
    assert normalize_sqlite_uri("sqlite:///:memory:", base_dir=REPO) == "sqlite:///:memory:"
    assert normalize_sqlite_uri("postgresql://u:p@h/db", base_dir=REPO) == "postgresql://u:p@h/db"


@test
def test_default_config_survives_absolute_path():
    """配置里的相对 sqlite 路径会被 Flask-SQLAlchemy 放到 instance 目录，需显式说明。"""
    env = make_env()
    try:
        app = env.app()
        with app.app_context():
            from app import db

            database = db.engine.url.database
        assert database, "SQLite 引擎没有解析出数据库文件路径"
        assert os.path.normcase(os.path.abspath(database)) == os.path.normcase(
            os.path.join(env.tmp, "rss.db")
        ), f"数据库没有落在配置指定的位置: {database}"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def main():
    passed, failed = [], []
    for fn in TESTS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed.append((fn.__name__, exc))
            print(f"FAIL  {fn.__name__}\n      {type(exc).__name__}: {exc}")
            if os.environ.get("RSSAGG_TRACE"):
                traceback.print_exc()
        else:
            passed.append(fn.__name__)
            print(f"PASS  {fn.__name__}")
    print("\n" + "=" * 72)
    print(f"PASSED {len(passed)} / {len(TESTS)}   FAILED {len(failed)}")
    if failed:
        print("失败用例:")
        for name, exc in failed:
            print(f"  - {name}: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
