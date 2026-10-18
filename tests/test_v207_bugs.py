"""v2.0.7：搜索高亮、缩略图、统计页、命令面板与视觉升级的验证用例。

用法：python tests/test_v207_bugs.py
"""
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


def _seed(app, category="Tech"):
    from datetime import timedelta

    from app import db
    from app.models import Article, utcnow

    with app.app_context():
        source = add_source_via_model(name="视觉源", url="http://visual.example/feed")
        source.category = category
        db.session.commit()
        now = utcnow()
        db.session.add_all([
            Article(title="Hello 世界", link="http://visual.example/1", source_id=source.id,
                    published=now, bookmarked=True,
                    summary='Hello 摘要 <img src="http://img.example/cover.png"> 正文'),
            Article(title="Second 条目", link="http://visual.example/2", source_id=source.id,
                    published=now - timedelta(days=1), read=True, summary="普通摘要"),
        ])
        db.session.commit()
        return source.id


# --------------------------------------------------------------------------- #
# 1. 关键词高亮
# --------------------------------------------------------------------------- #
@test
def test_highlight_wraps_terms_case_insensitively():
    from app.highlight import highlight

    out = highlight("Hello hello HELLO", ["hello"])
    assert out.count('<mark class="hl">') == 3, out


@test
def test_highlight_escapes_html_before_marking():
    """外部 feed 的标题必须先转义，否则 <mark> 注入就等于 XSS。"""
    from app.highlight import highlight

    out = highlight('<script>alert("x")</script> alert', ["alert"])
    assert "<script>" not in out, f"未转义: {out}"
    assert "&lt;script&gt;" in out
    assert '<mark class="hl">alert</mark>' in out


@test
def test_highlight_limits_matches_and_handles_empty():
    from app.highlight import MAX_MATCHES, highlight

    out = highlight("a " * 200, ["a"])
    assert out.count("<mark") <= MAX_MATCHES
    assert highlight("", ["x"]) == ""
    assert highlight("plain", []) == "plain"
    assert highlight("<b>x</b>", ["x"]).startswith("&lt;b&gt;")


@test
def test_split_terms_dedupes_and_caps():
    from app.highlight import split_terms

    terms = split_terms("Hello  hello   world")
    assert terms == ["Hello", "world"], terms
    assert len(split_terms(" ".join(f"t{i}" for i in range(20)))) <= 8
    assert split_terms("") == []


@test
def test_search_page_highlights_query():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        body = app.test_client().get("/search?q=hello").data.decode("utf-8")
        assert '<mark class="hl">Hello</mark>' in body or '<mark class="hl">hello</mark>' in body, \
            "搜索结果没有高亮关键词"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 2. 缩略图（多模态视觉）
# --------------------------------------------------------------------------- #
@test
def test_first_image_extraction():
    from app.reader import first_image

    assert first_image('<p><img src="http://a.example/1.png"></p>') == "http://a.example/1.png"
    assert first_image('<img class="x" src="/relative.png">') is None, "相对路径不应透出"
    assert first_image("<img src='https://b.example/2.jpg'>") == "https://b.example/2.jpg"
    assert first_image("no image here") is None
    assert first_image(None) is None
    assert first_image('<img src="http://a.example/' + "x" * 3000 + '">') is None, "超长地址应丢弃"


@test
def test_index_renders_thumbnail_for_article_with_image():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        body = app.test_client().get("/").data.decode("utf-8")
        assert 'class="thumb"' in body, "列表没有渲染缩略图"
        assert "img.example/cover.png" in body
        assert 'loading="lazy"' in body, "缩略图没有懒加载"
        assert "onerror=" in body, "图片加载失败没有兜底"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 3. 统计页
# --------------------------------------------------------------------------- #
@test
def test_stats_overview_and_daily_counts():
    from app import stats

    env = make_env()
    try:
        app = env.app()
        _seed(app)
        with app.app_context():
            overview = stats.overview()
            assert overview["articles"] == 2
            assert overview["unread"] == 1, overview
            assert overview["bookmarked"] == 1
            assert overview["read_ratio"] == 50, overview

            daily = stats.daily_counts(7)
            assert len(daily) == 7, "日统计天数不对"
            assert sum(count for _, count in daily) >= 1

            breakdown = stats.source_breakdown()
            assert breakdown and breakdown[0]["total"] == 2
            assert breakdown[0]["unread"] == 1, f"未读数算错了: {breakdown}"
    finally:
        env.close()


@test
def test_stats_page_renders_chart_and_cards():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        body = app.test_client().get("/stats").data.decode("utf-8")
        for needle in ('class="stat-card"', 'class="chart"', 'class="bar"', 'class="meter"'):
            assert needle in body, f"统计页缺少 {needle}"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 4. 命令面板与视觉
# --------------------------------------------------------------------------- #
@test
def test_index_has_command_palette():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        body = app.test_client().get("/").data.decode("utf-8")
        for needle in ("palette-mask", "palette-input", "palette-list", "data-open-palette"):
            assert needle in body, f"命令面板缺少 {needle}"
        js = open(os.path.join(REPO, "app", "static", "app.js"), encoding="utf-8").read()
        for needle in ("Ctrl", "ArrowDown", "Escape", "palette"):
            assert needle in js, f"app.js 缺少 {needle}"
    finally:
        env.close()


@test
def test_css_has_visual_upgrade_and_respects_motion():
    css = open(os.path.join(REPO, "app", "static", "app.css"), encoding="utf-8").read()
    for selector in ("backdrop-filter", "mark.hl", ".thumb", ".palette", ".chart .bar",
                     ".skeleton", "prefers-reduced-motion", "body::before"):
        assert selector in css, f"样式表缺少 {selector}"
    # 动效必须能在减少动态效果时关掉
    assert css.count("prefers-reduced-motion") >= 2, "减少动态效果的处理不足"


@test
def test_empty_state_is_illustrated():
    css = open(os.path.join(REPO, "app", "static", "app.css"), encoding="utf-8").read()
    assert ".empty .glyph" in css, "空状态没有图形化占位"


# --------------------------------------------------------------------------- #
# 5. 缺陷回归
# --------------------------------------------------------------------------- #
@test
def test_export_respects_category_filter():
    """2.0.6 的导出会忽略分类筛选，导致导出的内容和页面不一致。"""
    env = make_env()
    try:
        app = env.app()
        _seed(app, category="科技")
        with app.app_context():
            other = add_source_via_model(name="其它", url="http://other2.example/feed")
            other.category = "新闻"
            from app import db
            from app.models import Article

            db.session.add(Article(title="别的分类", link="http://other2.example/1", source_id=other.id))
            db.session.commit()

        client = app.test_client()
        payload = client.get("/export/articles.json?category=科技").get_json()
        titles = [item["title"] for item in payload["articles"]]
        assert "Hello 世界" in titles, titles
        assert "别的分类" not in titles, f"分类过滤没有生效: {titles}"
    finally:
        env.close()


@test
def test_stats_source_unread_uses_real_sum():
    """v2.0.7 开发中 func.case 用错导致未读数等于总数，这里锁住行为。"""
    from app import stats

    env = make_env()
    try:
        app = env.app()
        _seed(app)
        with app.app_context():
            row = stats.source_breakdown()[0]
        assert row["unread"] < row["total"], f"未读数不该等于总数: {row}"
    finally:
        env.close()


@test
def test_version_and_docs_are_consistent():
    from app.version import __version__

    assert __version__ >= "2.0.7", __version__
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
