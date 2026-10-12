"""v2.0.5：界面主题、收藏、导出与跨平台适配的验证用例。

用法：python tests/test_v205_bugs.py
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


def _seed(app):
    """建一个源和一篇文章，返回 (source_id, article_id)。"""
    from app import db
    from app.models import Article

    with app.app_context():
        source = add_source_via_model(name="UI Feed", url="http://ui.example/feed")
        article = Article(
            title="界面测试文章 UI article",
            link="http://ui.example/1",
            summary="摘要 summary " * 40,
            source_id=source.id,
        )
        db.session.add(article)
        db.session.commit()
        return source.id, article.id


# --------------------------------------------------------------------------- #
# 1. 主题与设计系统
# --------------------------------------------------------------------------- #
@test
def test_static_assets_are_served():
    """样式与主题脚本必须真的能取到（放在错误的目录会 404，界面等于没样式）。"""
    env = make_env()
    try:
        app = env.app()
        client = app.test_client()
        for path in ("/static/app.css", "/static/theme.js"):
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} 返回 {resp.status_code}"
            assert len(resp.data) > 500, f"{path} 内容过短"
    finally:
        env.close()


@test
def test_css_supports_system_and_manual_themes():
    """深色模式要同时支持「跟随系统」与「手动切换」。"""
    with open(os.path.join(REPO, "app", "static", "app.css"), encoding="utf-8") as fh:
        css = fh.read()
    assert "prefers-color-scheme: dark" in css, "CSS 没有跟随系统的深色模式"
    assert 'html[data-theme="dark"]' in css, "CSS 缺少手动深色主题"
    assert 'html[data-theme="light"]' in css, "CSS 缺少手动浅色主题"
    assert "prefers-reduced-motion" in css, "没有尊重「减少动态效果」偏好"
    assert ":focus-visible" in css, "缺少键盘焦点样式"


@test
def test_theme_script_avoids_flash_and_remembers_choice():
    with open(os.path.join(REPO, "app", "static", "theme.js"), encoding="utf-8") as fh:
        js = fh.read()
    assert "localStorage" in js, "主题选择没有被记住"
    assert "matchMedia" in js, "没有读取系统偏好"
    # 必须在 DOMContentLoaded 之前就应用，否则深色下会闪白屏
    assert js.index("apply(stored())") < js.index("DOMContentLoaded"), "主题应用晚于 DOM 就绪，会闪白"


@test
def test_pages_link_the_design_system():
    """三个页面都要引用同一套样式与主题脚本。"""
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        client = app.test_client()
        for path in ("/", "/console", "/bookmarks"):
            body = client.get(path).data.decode("utf-8")
            assert "static/app.css" in body, f"{path} 没有引用样式表"
            assert "static/theme.js" in body, f"{path} 没有引用主题脚本"
            assert 'id="theme-toggle"' in body, f"{path} 缺少主题切换按钮"
    finally:
        env.close()


@test
def test_index_exposes_accessibility_and_polling_hooks():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        body = app.test_client().get("/").data.decode("utf-8")
        for needle in ('class="skip-link"', 'id="unread-total"', 'id="task-status"',
                       'aria-live="polite"', "bookmark-toggle"):
            assert needle in body, f"首页缺少 {needle}"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 2. 收藏
# --------------------------------------------------------------------------- #
@test
def test_bookmark_toggle_and_filter():
    from app import db
    from app.models import Article
    from app.services import toggle_bookmark

    env = make_env()
    try:
        app = env.app()
        _, article_id = _seed(app)
        with app.app_context():
            article = toggle_bookmark(article_id)
            assert article.bookmarked is True
            article = toggle_bookmark(article_id)
            assert article.bookmarked is False
            toggle_bookmark(article_id)
            assert db.session.get(Article, article_id).bookmarked is True

        client = app.test_client()
        body = client.get("/bookmarks").data.decode("utf-8")
        assert "界面测试文章" in body, "收藏页没有列出已收藏文章"
        assert client.post(f"/article/{article_id}/bookmark?ajax=1").get_json()["bookmarked"] is False
    finally:
        env.close()


@test
def test_bookmark_ajax_returns_json_and_404_for_missing():
    env = make_env()
    try:
        app = env.app()
        _, article_id = _seed(app)
        client = app.test_client()
        resp = client.post(f"/article/{article_id}/bookmark?ajax=1")
        assert resp.status_code == 200 and resp.is_json
        assert set(resp.get_json()) == {"id", "bookmarked"}
        missing = client.post("/article/99999/bookmark?ajax=1")
        assert missing.status_code == 404, missing.status_code
    finally:
        env.close()


@test
def test_search_bookmark_filter():
    from app import db
    from app.models import Article
    from app.services import toggle_bookmark

    env = make_env()
    try:
        app = env.app()
        source_id, article_id = _seed(app)
        with app.app_context():
            db.session.add(Article(title="未收藏 unstarred", link="http://ui.example/2",
                                   source_id=source_id))
            db.session.commit()
            toggle_bookmark(article_id)  # 把第一篇文章标为收藏

        client = app.test_client()
        body = client.get("/search?bookmarked=true").data.decode("utf-8")
        assert "界面测试文章" in body, "已收藏文章没有出现在收藏过滤里"
        assert "未收藏" not in body, "未收藏文章不该出现在收藏过滤里"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 3. 导出
# --------------------------------------------------------------------------- #
@test
def test_export_formats_render():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        client = app.test_client()

        data = client.get("/export/articles.json")
        assert data.status_code == 200, data.status_code
        payload = data.get_json()
        assert payload["version"], "导出缺少版本号"
        assert payload["article_count"] >= 1
        assert "sources" in payload and "articles" in payload
        assert any(item.get("title") for item in payload["articles"])

        csv_data = client.get("/export/articles.csv")
        assert csv_data.status_code == 200
        text = csv_data.data.decode("utf-8-sig")
        assert text.splitlines()[0].startswith("id,source,title"), text.splitlines()[0]
        assert "界面测试文章" in text

        md = client.get("/export/articles.markdown").data.decode("utf-8")
        assert md.startswith("#"), md[:40]
        assert "界面测试文章" in md

        assert client.get("/export/articles.xml").status_code == 404
    finally:
        env.close()


@test
def test_csv_has_bom_for_excel():
    """Excel 在中文 Windows 上需要 BOM 才不会乱码。"""
    from app.exporter import export_articles_csv

    env = make_env()
    try:
        app = env.app()
        _seed(app)
        with app.app_context():
            payload = export_articles_csv()
        assert payload.startswith("id,source"), payload[:30]
    finally:
        env.close()


@test
def test_export_respects_filters():
    from app import db
    from app.models import Article

    env = make_env()
    try:
        app = env.app()
        source_id, article_id = _seed(app)
        with app.app_context():
            db.session.add(Article(title="SECOND-ARTICLE", link="http://ui.example/2",
                                   source_id=source_id, read=True))
            db.session.commit()

        client = app.test_client()
        unread_only = client.get("/export/articles.json?unread=true").get_json()
        titles = [item["title"] for item in unread_only["articles"]]
        assert "SECOND-ARTICLE" not in titles, f"已读文章出现在未读导出里: {titles}"

        body = client.get("/").data.decode("utf-8")
        assert "/export/articles.json" in body, "首页没有导出链接"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 4. 跨平台
# --------------------------------------------------------------------------- #
@test
def test_platform_helpers_behave_per_platform():
    from app import platform_utils as pu

    name = pu.platform_name()
    assert name.startswith(("Windows", "macOS", "Linux")), name

    data_dir = pu.user_data_dir()
    assert data_dir and ("RSS-Aggregator" in data_dir), data_dir
    if pu.WINDOWS:
        assert "AppData" in data_dir or "APPDATA" in os.environ, data_dir
    elif pu.MACOS:
        assert "Application Support" in data_dir, data_dir
    else:
        assert ".local/share" in data_dir or os.environ.get("XDG_DATA_HOME"), data_dir

    assert pu.default_font_family(), "没有给出默认字体"
    assert isinstance(pu.enable_high_dpi(), bool)
    pu.configure_console_encoding()  # 不应抛异常


@test
def test_desktop_gui_is_display_optional():
    """没有图形环境时不能崩，要给出可复制的替代命令。"""
    source = open(os.path.join(REPO, "app", "gui.py"), encoding="utf-8").read()
    assert "TclError" in source, "没有处理无图形环境的情况"
    assert "console_hint" in source, "没有给出各平台可复制的启动命令"
    assert 'font=("", ' not in source, "仍在使用会退化的空字体名"
    assert "enable_high_dpi" in source, "启动时没有声明 DPI 感知"


@test
def test_platform_utils_has_no_hard_dependency():
    """platform_utils 不能引入新依赖，且在没有图形环境时也能导入。"""
    source = open(os.path.join(REPO, "app", "platform_utils.py"), encoding="utf-8").read()
    imports = re.findall(r"^import (\w+)|^from (\w+)", source, re.MULTILINE)
    modules = {a or b for a, b in imports}
    assert modules <= {"logging", "os", "platform", "sys", "ctypes"}, f"引入了额外依赖: {modules}"


# --------------------------------------------------------------------------- #
# 5. 版本与迁移
# --------------------------------------------------------------------------- #
@test
def test_bookmark_column_is_migrated_for_existing_databases():
    """老库要能自动补上 bookmarked 列。"""
    from app.schema import COLUMN_MIGRATIONS

    targets = [(table, column) for table, column, _, _ in COLUMN_MIGRATIONS]
    assert ("article", "bookmarked") in targets, targets


@test
def test_version_and_docs_are_consistent():
    from app.version import __version__

    assert __version__ == "2.0.5", __version__
    changelog = open(os.path.join(REPO, "CHANGELOG.md"), encoding="utf-8").read()
    assert f"## [{__version__}]" in changelog, f"CHANGELOG.md 缺少 [{__version__}] 小节"
    for name in ("README.md", "README-zh.md"):
        text = open(os.path.join(REPO, name), encoding="utf-8").read()
        assert __version__ in text, f"{name} 里的版本号不是 {__version__}"


@test
def test_i18n_keys_are_complete():
    """模板里用到的键必须在两种语言里都存在，否则页面会显示裸键名。"""
    from app.i18n import TRANSLATIONS

    zh, en = TRANSLATIONS["zh"], TRANSLATIONS["en"]
    assert set(zh) == set(en), f"语言键不一致: {sorted(set(zh) ^ set(en))[:10]}"

    used = set()
    template_dir = os.path.join(REPO, "app", "web", "templates")
    for name in os.listdir(template_dir):
        if not name.endswith(".html"):
            continue
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
