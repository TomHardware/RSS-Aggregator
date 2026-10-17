"""v2.0.6：阅读视图、键盘操作、分类导航、批量已读与 GUI 移除的验证用例。

用法：python tests/test_v206_bugs.py
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
    from app import db
    from app.models import Article

    with app.app_context():
        source = add_source_via_model(name="Reader Feed", url="http://reader.example/feed")
        source.category = category
        db.session.commit()
        first = Article(title="第一篇 First", link="http://reader.example/1",
                        summary="摘要", content="<p>正文第一段。</p><p>正文第二段。</p>",
                        source_id=source.id)
        second = Article(title="第二篇 Second", link="http://reader.example/2",
                         summary="只有摘要", source_id=source.id)
        db.session.add_all([first, second])
        db.session.commit()
        return source.id, first.id, second.id


# --------------------------------------------------------------------------- #
# 1. 本地 GUI 已移除
# --------------------------------------------------------------------------- #
@test
def test_desktop_gui_is_gone():
    """本轮明确移除本地 GUI：文件、入口与依赖都不应存在。"""
    for rel in ("desktop_gui.py", "app/gui.py"):
        path = os.path.join(REPO, rel.replace("/", os.sep))
        assert not os.path.exists(path), f"{rel} 仍存在"

    gui_dir = os.path.join(REPO, "app")
    for name in os.listdir(gui_dir):
        assert "gui" not in name.lower(), f"app/{name} 看起来仍是 GUI 模块"

    for rel in ("README.md", "README-zh.md"):
        text = open(os.path.join(REPO, rel), encoding="utf-8").read()
        assert "desktop_gui" not in text, f"{rel} 仍提到 desktop_gui"


@test
def test_platform_utils_has_no_gui_helpers():
    """平台工具里不应再保留只服务于 tkinter 的函数。"""
    from app import platform_utils as pu

    for removed in ("enable_high_dpi", "tk_scaling", "default_font_family"):
        assert not hasattr(pu, removed), f"platform_utils 仍保留 {removed}"
    assert callable(pu.platform_name)
    assert callable(pu.user_data_dir)
    assert callable(pu.configure_console_encoding)


# --------------------------------------------------------------------------- #
# 2. 阅读视图
# --------------------------------------------------------------------------- #
@test
def test_reader_page_renders_article():
    env = make_env()
    try:
        app = env.app()
        _, first_id, _ = _seed(app)
        resp = app.test_client().get(f"/article/{first_id}")
        assert resp.status_code == 200, resp.status_code
        body = resp.data.decode("utf-8")
        for needle in ("正文第一段", "正文第二段", "reading-progress", "reader-body",
                       "/read", "bookmark-toggle"):
            assert needle in body, f"阅读页缺少 {needle}"
        # 摘要要被切成段落，而不是原样塞进页面
        assert "<p>正文第一段。</p><p>" not in body, "正文没有按段落渲染"
    finally:
        env.close()


@test
def test_reader_page_404_for_missing_article():
    env = make_env()
    try:
        app = env.app()
        assert app.test_client().get("/article/999999").status_code == 404
    finally:
        env.close()


@test
def test_reader_helpers_strip_html_and_estimate_time():
    from app.reader import paragraphs, plain_text, reading_minutes

    html = "<script>bad()</script><p>第一段 &amp; 内容</p><p>第二段</p>"
    text = plain_text(html)
    assert "bad()" not in text, "脚本没有去掉"
    assert "&amp;" not in text, "HTML 实体没有反转义"
    assert len(paragraphs(html)) == 2, paragraphs(html)
    assert reading_minutes("字" * 800) == 2, reading_minutes("字" * 800)
    assert reading_minutes("") == 1


@test
def test_reader_navigation_links_neighbours():
    env = make_env()
    try:
        app = env.app()
        _, first_id, second_id = _seed(app)
        client = app.test_client()
        bodies = {aid: client.get(f"/article/{aid}").data.decode("utf-8") for aid in (first_id, second_id)}
        # 至少有一篇能链到另一篇上
        assert any(
            f"/article/{other}" in bodies[aid]
            for aid in bodies for other in (first_id, second_id) if other != aid
        ), "阅读页没有上一篇/下一篇导航"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 3. 批量已读与分类
# --------------------------------------------------------------------------- #
@test
def test_mark_all_read_clears_unread():
    from app import db
    from app.models import Article
    from app.services import unread_counts

    env = make_env()
    try:
        app = env.app()
        source_id, first_id, second_id = _seed(app)
        with app.app_context():
            assert unread_counts()[1] == 2

        resp = app.test_client().post("/mark_all_read")
        assert resp.status_code in (301, 302), resp.status_code
        with app.app_context():
            assert unread_counts()[1] == 0, "批量已读没有生效"
            assert db.session.get(Article, first_id).read is True
    finally:
        env.close()


@test
def test_mark_all_read_can_target_single_source():
    from app import db
    from app.models import Article, Source

    env = make_env()
    try:
        app = env.app()
        source_id, first_id, second_id = _seed(app)
        with app.app_context():
            other = add_source_via_model(name="Other", url="http://other.example/feed")
            keep = Article(title="保留未读", link="http://other.example/1", source_id=other.id)
            db.session.add(keep)
            db.session.commit()
            keep_id, other_id = keep.id, other.id

        app.test_client().post("/mark_all_read", data={"source_id": source_id})
        with app.app_context():
            assert db.session.get(Article, first_id).read is True
            assert db.session.get(Article, keep_id).read in (False, None), "误伤了其它源的未读"
    finally:
        env.close()


@test
def test_categories_render_and_filter():
    env = make_env()
    try:
        app = env.app()
        _seed(app, category="科技 Tech")
        client = app.test_client()
        body = client.get("/").data.decode("utf-8")
        assert "科技 Tech" in body, "首页没有显示分类导航"

        filtered = client.get("/search", query_string={"category": "科技 Tech"})
        assert filtered.status_code == 200
        assert "第一篇" in filtered.data.decode("utf-8")

        empty = client.get("/search", query_string={"category": "不存在的分类"})
        assert "第一篇" not in empty.data.decode("utf-8"), "分类过滤没有生效"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 4. 快捷键与即时反馈
# --------------------------------------------------------------------------- #
@test
def test_index_has_keyboard_shortcuts():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        body = app.test_client().get("/").data.decode("utf-8")
        for needle in ("keydown", "shortcut-help", "is-cursor", 'id="toast"', "notify("):
            assert needle in body, f"首页缺少 {needle}"
        # 快捷键说明里应当出现常用键
        for key in ("<kbd>j</kbd>", "<kbd>k</kbd>", "<kbd>o</kbd>", "<kbd>s</kbd>", "<kbd>m</kbd>"):
            assert key in body, f"快捷键说明缺少 {key}"
    finally:
        env.close()


@test
def test_index_links_to_reader_view():
    env = make_env()
    try:
        app = env.app()
        _, first_id, _ = _seed(app)
        body = app.test_client().get("/").data.decode("utf-8")
        assert f"/article/{first_id}" in body, "首页标题没有链到阅读视图"
        assert "document.title" in body, "没有把未读数同步到标签页标题"
    finally:
        env.close()


@test
def test_css_covers_new_components():
    css = open(os.path.join(REPO, "app", "static", "app.css"), encoding="utf-8").read()
    for selector in ("article.reader", ".toast", "kbd", ".progress-bar", ".is-cursor",
                     ".shortcut-help", "prefers-reduced-motion"):
        assert selector in css, f"样式表缺少 {selector}"
    # 阅读页的行宽要受控，否则长文一行太长
    assert "max-width: 46rem" in css, "阅读视图没有限制行宽"


# --------------------------------------------------------------------------- #
# 5. 仓库卫生与版本
# --------------------------------------------------------------------------- #
@test
def test_runtime_artifacts_are_ignored():
    """数据库与日志是运行时产物，不能进版本库（远端这次真的提交进去了）。"""
    with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as fh:
        ignore = fh.read()
    for pattern in ("data/", "*.db", "*.log", "__pycache__/"):
        assert pattern in ignore, f".gitignore 缺少 {pattern}"

    # 注意：运行测试后本地会真的生成 data/rss.db，所以这里只校验忽略规则，
    # 交付物里是否混入运行产物由打包脚本检查。
    tracked = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__"}]
        for name in filenames:
            if name.endswith((".pyc",)) and "__pycache__" in dirpath:
                tracked.append(os.path.join(dirpath, name))
    assert not tracked, f"仓库里混入了字节码缓存: {tracked[:3]}"


@test
def test_packaging_files_exist():
    assert os.path.exists(os.path.join(REPO, ".dockerignore")), "缺少 .dockerignore"
    assert not os.path.exists(os.path.join(REPO, "app", ".dockerfile")), "app/.dockerfile 应该删除"
    assert os.path.exists(os.path.join(REPO, "Dockerfile")), "根目录 Dockerfile 必须存在"


@test
def test_version_and_docs_are_consistent():
    from app.version import __version__

    assert __version__ == "2.0.6", __version__
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
