"""v2.1.0 用例：语言切换、文件选择器本地化、排序与关键词屏蔽。"""
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


def _strip(html):
    return re.sub(r"<[^>]+>", "", html)


def _seed(app):
    from datetime import timedelta

    from app import db
    from app.models import Article, utcnow

    with app.app_context():
        source = add_source_via_model(name="语言源", url="http://lang.example/feed")
        source.category = "Tech"
        db.session.commit()
        now = utcnow()
        rows = [("新闻 A", 0), ("广告推广 B", 1), ("新闻 C", 2)]
        for index, (title, hours) in enumerate(rows):
            db.session.add(Article(
                title=title, link=f"http://lang.example/{index}",
                source_id=source.id, published=now - timedelta(hours=hours),
                summary="内容" * 80,
            ))
        db.session.commit()
        return source.id


# --------------------------------------------------------------------------- #
# 1. 语言切换（本轮明确报告的 bug）
# --------------------------------------------------------------------------- #
@test
def test_language_switch_button_exists_on_every_page():
    """每个页面暴露的都是同一个布局，语言切换按钮必须存在。"""
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        client = app.test_client()
        for path in ("/", "/console", "/stats", "/bookmarks"):
            body = client.get(path).data.decode("utf-8")
            assert "lang-switch" in body, f"{path} 缺少语言切换区"
            assert "/language/zh" in body and "/language/en" in body, f"{path} 缺少语言选项"
            assert "lang-topbar" in body, f"{path} 缺少窄屏语言按钮"
    finally:
        env.close()


@test
def test_language_switch_changes_interface_language():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        client = app.test_client()

        client.get("/language/en")
        english = client.get("/").data.decode("utf-8")
        assert "Articles" in english, "英文界面没有生效"

        client.get("/language/zh")
        chinese = client.get("/").data.decode("utf-8")
        assert "文章" in chinese, "中文界面没有生效"
    finally:
        env.close()


@test
def test_language_choice_is_remembered_by_cookie():
    env = make_env()
    try:
        app = env.app()
        _seed(app)

        client = app.test_client()
        resp = client.get("/language/zh")
        assert "rss_lang=zh" in resp.headers.get("Set-Cookie", ""), "没有写入语言 cookie"

        # 新客户端只带 cookie，不带 URL 参数
        fresh = app.test_client()
        fresh.set_cookie("rss_lang", "zh")
        assert "文章" in fresh.get("/").data.decode("utf-8"), "cookie 没有被采纳"
    finally:
        env.close()


@test
def test_language_cookie_beats_config_default():
    from app.web.routes import LANG_COOKIE

    env = make_env()
    try:
        app = env.app()
        app.config["APP_LANGUAGE"] = "en"
        client = app.test_client()
        client.set_cookie(LANG_COOKIE, "zh")
        assert "文章" in client.get("/").data.decode("utf-8"), "cookie 应优先于配置默认值"

        # URL 参数优先级最高
        assert "文章" in client.get("/?lang=zh").data.decode("utf-8")
    finally:
        env.close()


@test
def test_language_switch_rejects_external_redirect():
    """next 只能是站内地址，不能把用户带去外站。"""
    env = make_env()
    try:
        app = env.app()
        client = app.test_client()
        resp = client.get("/language/zh?next=http://evil.example/x")
        location = resp.headers.get("Location", "")
        assert "evil.example" not in location, f"存在开放重定向: {location}"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 2. 文件选择按钮本地化（本轮明确报告的 bug）
# --------------------------------------------------------------------------- #
@test
def test_native_file_input_is_hidden_and_relabelled():
    """原生 file 控件的按钮文案由浏览器决定，必须用自绘按钮替代。"""
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        client = app.test_client()
        for path in ("/", "/console"):
            body = client.get(path).data.decode("utf-8")
            assert 'class="file-picker"' in body, f"{path} 没有自绘文件选择器"
            assert 'class="file-button"' in body, f"{path} 缺少自绘按钮"
            assert "data-file-name" in body, f"{path} 缺少文件名回显"
            # 原生控件必须带 file-input 类（被 CSS 视觉隐藏）
            assert re.search(r'<input[^>]*type="file"[^>]*class="file-input"', body), \
                f"{path} 原生控件没有被隐藏"
    finally:
        env.close()


@test
def test_file_button_text_follows_language():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        client = app.test_client()

        client.get("/language/en")
        english = client.get("/").data.decode("utf-8")
        assert "Choose file" in english, "英文界面缺少 Choose file"
        assert "选择文件" not in english, "英文界面仍出现中文『选择文件』"

        client.get("/language/zh")
        chinese = client.get("/").data.decode("utf-8")
        assert "选择文件" in chinese, "中文界面缺少『选择文件』"
    finally:
        env.close()


@test
def test_file_picker_css_hides_native_control():
    css = open(os.path.join(REPO, "app", "static", "app.css"), encoding="utf-8").read()
    assert ".file-input" in css and "clip: rect(0, 0, 0, 0)" in css, "原生控件没有被视觉隐藏"
    assert ".file-button" in css, "缺少自绘按钮样式"
    js = open(os.path.join(REPO, "app", "static", "app.js"), encoding="utf-8").read()
    assert "initFilePickers" in js, "缺少文件名回显逻辑"


# --------------------------------------------------------------------------- #
# 3. 排序与关键词屏蔽
# --------------------------------------------------------------------------- #
@test
def test_sort_orders_articles():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        client = app.test_client()

        newest = _strip(client.get("/?sort=newest").data.decode("utf-8"))
        oldest = _strip(client.get("/?sort=oldest").data.decode("utf-8"))
        assert newest.index("新闻 A") < newest.index("新闻 C"), "最新优先顺序不对"
        assert oldest.index("新闻 C") < oldest.index("新闻 A"), "最早优先顺序不对"
    finally:
        env.close()


@test
def test_exclude_keywords_hide_noise():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        client = app.test_client()

        body = _strip(client.get("/?exclude=广告").data.decode("utf-8"))
        assert "新闻 A" in body, "屏蔽误伤了正常文章"
        assert "广告推广" not in body, "屏蔽词没有生效"
    finally:
        env.close()


@test
def test_exclude_works_with_search():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        client = app.test_client()

        body = _strip(client.get("/search?q=新闻&exclude=广告").data.decode("utf-8"))
        assert "新闻 A" in body, "搜索 + 屏蔽没有返回正常结果"
        assert "广告推广" not in body, "搜索 + 屏蔽没有过滤噪音"
    finally:
        env.close()


@test
def test_exclude_accepts_multiple_separators():
    from app.web.routes import _excluded_terms

    env = make_env()
    try:
        app = env.app()
        with app.app_context():
            pass
        client = app.test_client()
        # 中英文逗号都要支持
        client.get("/?exclude=广告,推广")
        with app.test_request_context("/?exclude=广告，推广, spam"):
            terms = _excluded_terms()
        assert terms == ["广告", "推广", "spam"], terms
    finally:
        env.close()


@test
def test_search_api_supports_sort():
    env = make_env()
    try:
        app = env.app()
        _seed(app)
        client = app.test_client()
        newest = client.get("/api/articles?sort=newest&limit=10").get_json()
        oldest = client.get("/api/articles?sort=oldest&limit=10").get_json()
        assert newest["items"][0]["title"] == "新闻 A", newest["items"][0]
        assert oldest["items"][0]["title"] == "新闻 C", oldest["items"][0]
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 4. 版本与文案一致性
# --------------------------------------------------------------------------- #
@test
def test_version_and_docs_are_consistent():
    from app.version import __version__

    assert __version__ >= "2.1.0", __version__
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
