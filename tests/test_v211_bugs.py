"""v2.1.1：视图 identity、筛选标签与仓库卫生的验证用例。"""
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


def _seed(app):
    from datetime import timedelta

    from app import db
    from app.models import Article, utcnow

    with app.app_context():
        source = add_source_via_model(name="视图源", url="http://view.example/feed")
        source.category = "Tech"
        db.session.commit()
        now = utcnow()
        for index in range(6):
            db.session.add(Article(
                title=f"视图文章 {index}", link=f"http://view.example/{index}",
                source_id=source.id, published=now - timedelta(hours=index),
                read=(index % 2 == 0), bookmarked=(index < 2),
                summary="摘要内容" * 40,
            ))
        db.session.commit()
        return source.id


def _hero(body):
    """取出 hero 区块的标题、描述与计数。"""
    title = re.search(r'view-hero-title">\s*([^<]+)', body)
    desc = re.search(r'view-hero-desc">\s*([^<]+)', body)
    count = re.search(r'view-hero-count">\s*<strong>(\d+)', body)
    return (
        title.group(1).strip() if title else None,
        desc.group(1).strip() if desc else None,
        int(count.group(1)) if count else None,
    )


# --------------------------------------------------------------------------- #
# 1. 视图必须有明显不同的外观（本轮核心诉求）
# --------------------------------------------------------------------------- #
@test
def test_each_view_has_distinct_hero_identity():
    """切换视图时，标题、描述、计数三者都要变化，否则用户没有切换感。"""
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        client = app.test_client()

        identities = {}
        for view in ("all", "unread", "starred", "today"):
            url = "/" if view == "all" else f"/?view={view}"
            body = client.get(url).data.decode("utf-8")
            identities[view] = _hero(body)

        titles = {view: value[0] for view, value in identities.items()}
        descriptions = {view: value[1] for view, value in identities.items()}
        counts = {view: value[2] for view, value in identities.items()}

        assert all(titles.values()), f"有视图缺少标题: {titles}"
        assert len(set(titles.values())) == 4, f"视图标题重复，缺少区分度: {titles}"
        assert len(set(descriptions.values())) == 4, f"视图描述重复: {descriptions}"
        assert counts["all"] == 6, counts
        assert counts["unread"] == 3, counts
        assert counts["starred"] == 2, counts
    finally:
        env.close()


@test
def test_body_carries_current_view_for_theming():
    """视图色调由 body[data-view] 驱动，值必须随视图变化。"""
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        client = app.test_client()
        for view in ("all", "unread", "starred", "today"):
            url = "/" if view == "all" else f"/?view={view}"
            body = client.get(url).data.decode("utf-8")
            assert f'data-view="{view}"' in body, f"{view} 视图没有标记到 body"
    finally:
        env.close()


@test
def test_css_defines_per_view_tints():
    css = open(os.path.join(REPO, "app", "static", "app.css"), encoding="utf-8").read()
    for view in ("all", "unread", "starred", "today"):
        assert f'body[data-view="{view}"]' in css, f"缺少 {view} 视图的配色"
    assert "--view-tint" in css, "缺少视图色调变量"
    assert ".view-hero" in css, "缺少视图 hero 样式"
    assert "@keyframes viewIn" in css, "缺少视图切换动画"
    # 动画必须在减少动态效果时关闭
    assert re.search(r"prefers-reduced-motion[^{]*\{[^}]*\.view-hero\s*\{[^}]*animation:\s*none", css), \
        "视图动画没有尊重减少动态效果"


@test
def test_empty_states_differ_per_view():
    env = make_env()
    try:
        app = env.app()
        with app.app_context():
            from app import db

            db.create_all()
            add_source_via_model(name="空源", url="http://empty.example/feed")

        client = app.test_client()
        texts = {}
        for view in ("all", "unread", "starred", "today"):
            url = "/" if view == "all" else f"/?view={view}"
            body = client.get(url).data.decode("utf-8")
            match = re.search(r'class="empty view-empty"><span class="glyph">([^<]*)</span>([^<]*)', body)
            assert match, f"{view} 视图缺少空状态"
            texts[view] = (match.group(1).strip(), match.group(2).strip())
        assert len({value[1] for value in texts.values()}) == 4, f"空状态文案重复: {texts}"
        assert len({value[0] for value in texts.values()}) == 4, f"空状态图形重复: {texts}"
    finally:
        env.close()


@test
def test_card_heading_follows_view():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        body = app.test_client().get("/?view=starred").data.decode("utf-8")
        # 卡片标题应带上当前视图名，而不是永远写 Articles
        assert re.search(r"<h2[^>]*>\s*[★☰●◷]?\s*Bookmarks", body) or "Bookmarks" in body
        assert "view-hero-title" in body
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 2. 筛选标签
# --------------------------------------------------------------------------- #
@test
def test_filter_chips_show_and_remove_conditions():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        client = app.test_client()

        body = client.get("/search?q=视图&exclude=广告").data.decode("utf-8")
        assert "filter-chips" in body, "没有渲染筛选标签"
        assert "视图" in body and "广告" in body, "标签里没有显示当前条件"
        # 每个标签都带移除链接（不带该参数）
        assert "chip-x" in body, "标签没有移除按钮"

        clean = client.get("/").data.decode("utf-8")
        assert "filter-chips" not in clean, "没有任何筛选时不应显示标签区"
    finally:
        env.close()


@test
def test_filter_chip_removal_keeps_other_filters():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        body = app.test_client().get("/search?q=视图&exclude=广告").data.decode("utf-8")
        links = re.findall(r'class="chip" href="([^"]+)"', body)
        assert links, "没有找到标签链接"
        # 去掉 exclude 的链接应保留 q
        assert any("q=" in link and "exclude" not in link for link in links), links[:3]
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 3. 仓库卫生（远端被误提交的产物）
# --------------------------------------------------------------------------- #
@test
def test_no_bytecode_or_database_shipped():
    """仓库里不能有字节码、数据库或日志。

    注意：测试运行本身会生成 __pycache__，那不是「被提交的文件」，
    所以这里跳过 __pycache__ 目录，只检查会被分发的内容。
    """
    problems = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__"}]
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), REPO).replace("\\", "/")
            if name.endswith((".pyc", ".pyo", ".db", ".sqlite3", ".log")):
                problems.append(rel)
    assert not problems, f"仓库里混入运行时产物: {problems[:5]}"


@test
def test_gitignore_covers_runtime_artifacts():
    ignore = open(os.path.join(REPO, ".gitignore"), encoding="utf-8").read()
    for pattern in ("__pycache__", "*.py[codz]", "data/", "*.db", "*.log"):
        assert pattern in ignore, f".gitignore 缺少 {pattern}"


# --------------------------------------------------------------------------- #
# 4. 版本与文案
# --------------------------------------------------------------------------- #
@test
def test_version_and_docs_are_consistent():
    from app.version import __version__

    assert __version__ == "2.1.1", __version__
    changelog = open(os.path.join(REPO, "CHANGELOG.md"), encoding="utf-8").read()
    assert f"## [{__version__}]" in changelog
    assert "## [2.1.1] - 2026-10-20" in changelog, "版本日期应为 2026-10-20"
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
