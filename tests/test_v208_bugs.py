"""v2.0.8：JSON/HTML 分页接口、SSE、无限滚动、即时搜索与 4 项修复的验证用例。

用法：python tests/test_v208_bugs.py
"""
import json
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


def _seed(app, count=15):
    from datetime import timedelta

    from app import db
    from app.models import Article, utcnow

    with app.app_context():
        source = add_source_via_model(name="实时源", url="http://live.example/feed")
        source.category = "Tech"
        db.session.commit()
        now = utcnow()
        for index in range(count):
            db.session.add(Article(
                title=f"实时文章 {index}",
                link=f"http://live.example/{index}",
                source_id=source.id,
                published=now - timedelta(minutes=index),
                summary="摘要" * 30,
            ))
        db.session.commit()
        return source.id


# --------------------------------------------------------------------------- #
# 1. 分页接口（JSON + HTML 片段）
# --------------------------------------------------------------------------- #
@test
def test_api_articles_json_pagination():
    env = make_env()
    try:
        app = env.app()
        _seed(app, 15)
        client = app.test_client()

        first = client.get("/api/articles?limit=5").get_json()
        assert first["page"] == 1 and first["total"] == 15 and first["has_next"] is True
        assert len(first["items"]) == 5
        assert set(first["items"][0]) >= {"id", "title", "link", "read", "bookmarked", "source_id"}

        last = client.get("/api/articles?limit=5&page=3").get_json()
        assert last["has_next"] is False and len(last["items"]) == 5
    finally:
        env.close()


@test
def test_api_articles_html_fragment_is_rendered_server_side():
    env = make_env()
    try:
        app = env.app()
        _seed(app, 6)
        resp = app.test_client().get("/api/articles?html=1&limit=3")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert 'class="article' in body, "片段里没有文章卡片"
        assert body.count('class="article') == 3, body.count('class="article')
        assert "<html" not in body.lower(), "片段不应返回整页"
    finally:
        env.close()


@test
def test_api_articles_respects_filters():
    env = make_env()
    try:
        app = env.app()
        _seed(app, 4)
        client = app.test_client()
        unread = client.get("/api/articles?unread=true&limit=20").get_json()
        assert unread["total"] == 4
        bookmarked = client.get("/api/articles?bookmarked=true&limit=20").get_json()
        assert bookmarked["total"] == 0
        searched = client.get("/api/articles?q=实时文章 2&limit=20").get_json()
        assert searched["total"] >= 1
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 2. SSE
# --------------------------------------------------------------------------- #
@test
def test_sse_once_returns_event_stream():
    env = make_env()
    try:
        app = env.app()
        resp = app.test_client().get("/api/events?once=1", buffered=True)
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream", resp.mimetype
        body = resp.data.decode("utf-8")
        assert "data: " in body, body
        payload = json.loads(body.split("data: ", 1)[1].split("\n", 1)[0])
        assert "unread_total" in payload and "running" in payload
        assert resp.headers.get("X-Accel-Buffering") == "no"
    finally:
        env.close()


@test
def test_sse_reports_running_tasks():
    from app import tasks

    env = make_env()
    try:
        app = env.app()
        tasks.reset()
        task_id = tasks.start("scheduled_fetch", source_id=1, source_name="SSE 源")
        body = app.test_client().get("/api/events?once=1", buffered=True).data.decode("utf-8")
        payload = json.loads(body.split("data: ", 1)[1].split("\n", 1)[0])
        assert payload["running"] and payload["running"][0]["source_name"] == "SSE 源"
        assert "id" in payload["running"][0]
        tasks.finish(task_id, True, 2, "ok")
    finally:
        tasks.reset()
        env.close()


# --------------------------------------------------------------------------- #
# 3. 前端接线
# --------------------------------------------------------------------------- #
@test
def test_index_wires_infinite_scroll_and_live_search():
    env = make_env()
    try:
        app = env.app()
        _seed(app, 12)
        body = app.test_client().get("/").data.decode("utf-8")
        for needle in ("scroll-sentinel", "live-search", "/api/articles", "app.js"):
            assert needle in body, f"首页缺少 {needle}"
        js = open(os.path.join(REPO, "app", "static", "app.js"), encoding="utf-8").read()
        for needle in ("IntersectionObserver", "loadMore", "EventSource"):
            assert needle in js, f"app.js 缺少 {needle}"
    finally:
        env.close()


@test
def test_cards_fragment_matches_index_markup():
    """首页与「加载更多」必须共用同一份片段，否则样式会漂移。"""
    idx = open(os.path.join(REPO, "app", "web", "templates", "index.html"), encoding="utf-8").read()
    assert '{% include "_cards.html" %}' in idx, "首页没有复用公共片段"
    cards = open(os.path.join(REPO, "app", "web", "templates", "_cards.html"), encoding="utf-8").read()
    for needle in ("thumb(article)", "hl(article.title)", "bookmark-toggle"):
        assert needle in cards, f"片段缺少 {needle}"


@test
def test_css_covers_live_components():
    css = open(os.path.join(REPO, "app", "static", "app.css"), encoding="utf-8").read()
    for selector in (".scroll-sentinel", ".live-search", ".live-row", ".live-dot", "spin"):
        assert selector in css, f"样式缺少 {selector}"
    assert css.count("prefers-reduced-motion") >= 3, "新动效没有尊重减少动态效果"


# --------------------------------------------------------------------------- #
# 4. 本轮修复
# --------------------------------------------------------------------------- #
@test
def test_skip_link_is_translated():
    from app.i18n import translate

    for lang in ("zh", "en"):
        text = translate("skip_to_content", lang)
        assert text and text != "skip_to_content", f"{lang} 缺少 skip_to_content"
    # skip-link 已收敛到 base.html，由所有页面继承
    base = open(os.path.join(REPO, "app", "web", "templates", "base.html"), encoding="utf-8").read()
    assert 'class="skip-link"' in base, "base.html 缺少跳过导航"
    assert "_('skip_to_content')" in base or '_("skip_to_content")' in base, "跳过导航没有走 i18n"
    assert "跳到正文" not in base, "跳过导航仍硬编码中文"


@test
def test_category_chips_preserve_filters():
    """点分类不应丢掉当前的搜索词/未读条件。"""
    env = make_env()
    try:
        app = env.app()
        _seed(app, 3)
        body = app.test_client().get("/search?q=实时&unread=true").data.decode("utf-8")
        # 分类链接应带上 q 与 unread
        links = re.findall(r'href="(/search\?[^"]*category=[^"]*)"', body)
        assert links, "没有渲染分类链接"
        assert any("q=" in link and "unread=true" in link for link in links), links[:3]
    finally:
        env.close()


@test
def test_reader_neighbours_work_for_old_articles():
    """超过 300 条之后的老文章也要能拿到上下篇链接。"""
    from app import db
    from app.models import Article

    env = make_env()
    try:
        app = env.app()
        _seed(app, 5)
        with app.app_context():
            source_id = Article.query.first().source_id
            # 造一批更旧的文章，让目标文章落在「最近 300 条」之外
            from datetime import timedelta

            from app.models import utcnow

            base = utcnow() - timedelta(days=400)
            for index in range(320):
                db.session.add(Article(
                    title=f"旧文 {index}", link=f"http://old.example/{index}",
                    source_id=source_id, published=base - timedelta(minutes=index),
                ))
            db.session.commit()
            target = Article.query.filter(Article.title == "旧文 5").first()
            from app.web.routes import _neighbours

            newer, older = _neighbours(target)
            assert newer is not None, "老文章缺少「更新一篇」"
            assert older is not None, "老文章缺少「更早一篇」"
    finally:
        env.close()


@test
def test_unread_cache_invalidated_after_writes():
    """标记已读后未读数必须立刻更新，不能等缓存过期。"""
    from app import db
    from app.models import Article
    from app.services import unread_counts
    from app.web.routes import _unread_counts

    env = make_env()
    try:
        app = env.app()
        _seed(app, 2)
        with app.app_context():
            assert _unread_counts()[1] == 2   # 先填满缓存
        client = app.test_client()
        article_id = client.get("/api/articles?limit=1").get_json()["items"][0]["id"]
        client.post(f"/article/{article_id}/read")
        with app.app_context():
            assert unread_counts()[1] == 1
            assert _unread_counts()[1] == 1, "缓存没有失效，仍返回旧未读数"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 5. 版本
# --------------------------------------------------------------------------- #
@test
def test_version_and_docs_are_consistent():
    from app.version import __version__

    assert __version__ >= "2.0.8", __version__
    changelog = open(os.path.join(REPO, "CHANGELOG.md"), encoding="utf-8").read()
    assert f"## [{__version__}]" in changelog, f"CHANGELOG.md 缺少 [{__version__}] 小节"
    for name in ("README.md", "README-zh.md"):
        text = open(os.path.join(REPO, name), encoding="utf-8").read()
        assert __version__ in text, f"{name} 里的版本号不是 {__version__}"


@test
def test_i18n_keys_are_complete():
    from app.i18n import TRANSLATIONS

    zh, en = TRANSLATIONS["zh"], TRANSLATIONS["en"]
    assert set(zh) == set(en), f"语言键不一致: {sorted(set(zh) ^ set(en))[:10]}"
    used = set()
    template_dir = os.path.join(REPO, "app", "web", "templates")
    for name in os.listdir(template_dir):
        if name.endswith(".html"):
            with open(os.path.join(template_dir, name), encoding="utf-8") as fh:
                used |= set(re.findall(r"_\(\s*['\"]([a-z0-9_]+)['\"]", fh.read()))
    missing = sorted(key for key in used if key not in zh)
    assert not missing, f"缺少翻译: {missing}"


@test
def test_healthz_reports_new_version():
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
