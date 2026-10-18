"""v2.0.9 用例：侧栏视图、批量操作、外观与单篇删除。"""
import os
import re
import sys
import time
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_bugfixes import SAMPLE_RSS, add_source_via_model, fake_response, make_env  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def _seed(app, count=6):
    from datetime import timedelta

    from app import db
    from app.models import Article, utcnow

    with app.app_context():
        source = add_source_via_model(name="侧栏源", url="http://side.example/feed")
        source.category = "Tech"
        db.session.commit()
        now = utcnow()
        for index in range(count):
            db.session.add(Article(
                title=f"文章 {index}", link=f"http://side.example/{index}",
                source_id=source.id, published=now - timedelta(hours=index),
                read=(index % 2 == 0),
            ))
        db.session.commit()
        return source.id


@test
def test_sidebar_renders_views_and_feeds():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        body = app.test_client().get("/").data.decode("utf-8")
        assert 'class="app-sidebar"' in body, "没有侧栏"
        for needle in ("side-link", "side-title", "侧栏源"):
            assert needle in body, f"侧栏缺少 {needle}"
        assert "/?view=unread" in body, "缺少未读视图入口"
        assert "app.js" in body, "没有加载交互模块"
    finally:
        env.close()


@test
def test_views_filter_articles():
    env = make_env()
    try:
        app = env.app()
        _seed(app, 6)
        client = app.test_client()

        all_body = client.get("/").data.decode("utf-8")
        unread_body = client.get("/?view=unread").data.decode("utf-8")
        starred_body = client.get("/?view=starred").data.decode("utf-8")

        assert all_body.count('class="article') == 6, all_body.count('class="article')
        assert unread_body.count('class="article') == 3, unread_body.count('class="article')
        assert starred_body.count('class="article') == 0, starred_body.count('class="article')
    finally:
        env.close()


@test
def test_view_counts_render():
    from app.web.routes import _view_counts

    env = make_env()
    try:
        app = env.app()
        _seed(app, 6)
        with app.app_context():
            counts = _view_counts()
        assert counts["all"] == 6, counts
        assert counts["unread"] == 3, counts
        assert counts["starred"] == 0, counts
    finally:
        env.close()


@test
def test_batch_actions_read_unread_bookmark_delete():
    from app import db
    from app.models import Article

    env = make_env()
    try:
        app = env.app()
        _seed(app, 4)
        client = app.test_client()
        with app.app_context():
            ids = [row.id for row in Article.query.limit(2).all()]

        resp = client.post("/api/articles/batch", json={"action": "read", "ids": ids})
        assert resp.status_code == 200 and resp.get_json()["updated"] == 2
        with app.app_context():
            assert all(db.session.get(Article, i).read for i in ids)

        resp = client.post("/api/articles/batch", json={"action": "unread", "ids": ids})
        assert resp.get_json()["updated"] == 2
        with app.app_context():
            assert not any(db.session.get(Article, i).read for i in ids)

        resp = client.post("/api/articles/batch", json={"action": "bookmark", "ids": ids})
        assert resp.get_json()["updated"] == 2
        with app.app_context():
            assert all(db.session.get(Article, i).bookmarked for i in ids)

        resp = client.post("/api/articles/batch", json={"action": "delete", "ids": ids[:1]})
        assert resp.get_json()["updated"] == 1
        with app.app_context():
            assert db.session.get(Article, ids[0]) is None
    finally:
        env.close()


@test
def test_batch_rejects_bad_requests():
    env = make_env()
    try:
        app = env.app()
        client = app.test_client()
        assert client.post("/api/articles/batch", json={"action": "read", "ids": []}).status_code == 400
        assert client.post("/api/articles/batch", json={"action": "nope", "ids": [1]}).status_code == 400
        too_many = list(range(1, 600))
        assert client.post("/api/articles/batch", json={"action": "read", "ids": too_many}).status_code == 400
    finally:
        env.close()


@test
def test_single_article_delete():
    from app import db
    from app.models import Article

    env = make_env()
    try:
        app = env.app()
        _seed(app, 2)
        client = app.test_client()
        with app.app_context():
            article = Article.query.first()
            article_id, source_id = article.id, article.source_id
        resp = client.post(f"/article/{article_id}/delete")
        assert resp.status_code in (301, 302), resp.status_code
        with app.app_context():
            assert db.session.get(Article, article_id) is None
            from app.models import Source

            assert db.session.get(Source, source_id) is not None, "删除文章不应删除订阅源"
        assert client.post("/article/999999/delete").status_code == 404
    finally:
        env.close()


@test
def test_batch_bar_and_appearance_markup():
    env = make_env()
    try:
        app = env.app()
        _seed(app, 2)
        body = app.test_client().get("/").data.decode("utf-8")
        for needle in ("batch-bar", "data-batch-action", "select-box", "select-all",
                       "density-toggle", "data-accent-choice", "swatch"):
            assert needle in body, f"缺少 {needle}"
    finally:
        env.close()


@test
def test_app_js_holds_all_behaviour():
    js = open(os.path.join(REPO, "app", "static", "app.js"), encoding="utf-8").read()
    for needle in ("applyTheme", "applyAccent", "applyDensity", "initPalette", "initShortcuts",
                   "initInfiniteScroll", "initLiveSearch", "initLiveStatus", "initBatch",
                   "EventSource", "IntersectionObserver", "localStorage"):
        assert needle in js, f"app.js 缺少 {needle}"


@test
def test_css_has_density_and_accent_tokens():
    css = open(os.path.join(REPO, "app", "static", "app.css"), encoding="utf-8").read()
    for needle in ('html[data-accent="violet"]', 'html[data-density="compact"]',
                   'html[data-density="comfortable"]', "--sidebar-w", ".app-shell",
                   ".side-link", ".batch-bar", "prefers-reduced-motion"):
        assert needle in css, f"样式缺少 {needle}"


@test
def test_templates_have_no_inline_scripts():
    """交互逻辑已抽到 app.js，模板里不应再有内联 <script> 逻辑块。"""
    template_dir = os.path.join(REPO, "app", "web", "templates")
    for name in sorted(os.listdir(template_dir)):
        if not name.endswith(".html"):
            continue
        text = open(os.path.join(template_dir, name), encoding="utf-8").read()
        inline = re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", text, re.S)
        for block in inline:
            body = block.strip()
            if body.startswith("type=") or len(body) < 80:
                continue
            assert "JSON.parse" in body or "tojson" in body or body.startswith("{"), \
                f"{name} 仍有内联交互脚本"


@test
def test_base_template_tolerates_minimal_context():
    """登录页只传少量变量，基础模板不能因此报错。"""
    from app.auth import hash_password

    env = make_env()
    try:
        app = env.app()
        app.config["AUTH_PASSWORD_HASH"] = hash_password("pw")
        resp = app.test_client().get("/login")
        assert resp.status_code == 200, resp.status_code
        body = resp.data.decode("utf-8")
        assert "app-shell" in body, "登录页没有套用基础布局"
    finally:
        env.close()


@test
def test_version_and_docs_are_consistent():
    from app.version import __version__

    assert __version__ == "2.0.9", __version__
    changelog = open(os.path.join(REPO, "CHANGELOG.md"), encoding="utf-8").read()
    assert f"## [{__version__}]" in changelog
    for name in ("README.md", "README-zh.md"):
        assert __version__ in open(os.path.join(REPO, name), encoding="utf-8").read()


@test
def test_i18n_keys_are_complete():
    from app.i18n import TRANSLATIONS

    zh, en = TRANSLATIONS["zh"], TRANSLATIONS["en"]
    assert set(zh) == set(en), f"语言键不一致: {sorted(set(zh) ^ set(en))[:10]}"
    used = set()
    template_dir = os.path.join(REPO, "app", "web", "templates")
    for name in os.listdir(template_dir):
        if name.endswith(".html"):
            used |= set(re.findall(r"_\(\s*['\"]([a-z0-9_]+)['\"]",
                                   open(os.path.join(template_dir, name), encoding="utf-8").read()))
    missing = sorted(key for key in used if key not in zh)
    assert not missing, f"缺少翻译: {missing}"


@test
def test_healthz_reports_new_version():
    from app.version import __version__

    env = make_env()
    try:
        app = env.app()
        resp = app.test_client().get("/healthz")
        assert resp.status_code == 200 and __version__ in resp.data.decode()
    finally:
        env.close()


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
