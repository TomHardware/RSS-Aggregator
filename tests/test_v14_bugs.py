"""v1.4 第三轮审查：新增缺陷的验证用例。

同一份用例在修复前失败、修复后通过。
用法：python tests/test_v14_bugs.py
"""
import os
import sys
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


def flashes(body):
    """从渲染结果里取出 flash 消息。"""
    import re

    start = body.find('class="flashes"')
    if start == -1:
        return []
    chunk = body[start:start + 2000]
    return [m.strip() for m in re.findall(r"<li>(.*?)</li>", chunk, re.S)]


# --------------------------------------------------------------------------- #
# 1. next 参数里的控制字符会打崩请求
# --------------------------------------------------------------------------- #
@test
def test_redirect_target_rejects_control_characters():
    """next 含换行符时现在会 500：用户可控输入不得直接进响应头。"""
    env = make_env()
    try:
        from app import db
        from app.models import Article

        app = env.app()
        with app.app_context():
            sid = add_source_via_model().id
            art = Article(title="t", link="http://example.com/a", source_id=sid)
            db.session.add(art)
            db.session.commit()
            aid = art.id

        client = app.test_client()
        for candidate in [
            "/x\r\nLocation: http://evil.example",
            "/x\nSet-Cookie: a=b",
            "/x\x00y",
        ]:
            resp = client.post(f"/article/{aid}/read", data={"next": candidate})
            assert resp.status_code < 500, (
                f"next={candidate!r} 触发了 {resp.status_code}（控制字符进入了响应头）"
            )
            assert resp.headers.get("Location") in ("/", "/fallback") or (
                resp.headers.get("Location")
                and "\n" not in resp.headers["Location"]
                and "\r" not in resp.headers["Location"]
            ), f"Location 头异常: {resp.headers.get('Location')!r}"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 2. 超长 URL 被静默截断后仍去抓取
# --------------------------------------------------------------------------- #
@test
def test_oversized_feed_url_is_rejected_not_truncated():
    """超过字段长度的订阅地址应明确拒绝，而不是截断成一个「另一个 URL」再去抓。"""
    env = make_env()
    try:
        from app.models import Source

        app = env.app()
        app.config["SCHEDULER_ENABLED"] = False
        long_url = "http://example.com/feed?" + "x" * 600

        with mock.patch("app.web.routes.fetch_source", return_value=(True, "ok")):
            client = app.test_client()
            resp = client.post(
                "/add_source",
                data={"name": "L", "url": long_url, "interval": "30"},
                follow_redirects=True,
            )
        with app.app_context():
            stored = Source.query.filter(Source.name == "L").first()

        assert stored is None, (
            f"超长 URL 被截断后入库：提交 {len(long_url)} 字符，"
            f"入库 {len(stored.url)} 字符（末尾 {stored.url[-20:]!r}）"
        )
        assert resp.status_code == 200
    finally:
        env.close()


@test
def test_oversized_article_link_is_skipped_not_truncated():
    """文章链接超过字段长度时应跳过该条，而不是截断成无效链接。"""
    big_link = "http://example.com/" + "a" * 1200
    feed = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>T</title>'
        f"<item><title>Big</title><link>{big_link}</link>"
        "<description>d</description></item>"
        "</channel></rss>"
    ).encode()

    env = make_env()
    try:
        from app.fetcher import fetch_source
        from app.models import Article

        app = env.app()
        with app.app_context():
            sid = add_source_via_model().id
            with mock.patch("app.urlsafety.requests.get", return_value=fake_response(content=feed)), \
                 mock.patch("app.fetcher.extract_full_text", return_value=""), \
                 mock.patch("app.fetcher.time.sleep", return_value=None):
                ok, msg = fetch_source(sid)
            assert ok, f"抓取应成功: {msg}"
            stored = Article.query.all()
            truncated = [a for a in stored if a.link != big_link]
            assert not truncated, (
                f"超长链接被截断后入库: 原始 {len(big_link)} 字符 → "
                f"入库 {len(truncated[0].link)} 字符"
            )
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 3. 配置写错时报错不指明位置
# --------------------------------------------------------------------------- #
@test
def test_invalid_config_reports_offending_key():
    """配置项类型写错时，报错必须指明是哪个键、哪个文件。"""
    env = make_env()
    try:
        bad = os.path.join(env.tmp, "bad.yaml")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write('app:\n  name: "X"\nserver:\n  port: "not-a-number"\n')

        from app import create_app

        try:
            create_app(bad)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            assert "port" in message, (
                f"报错没有指明出错的配置项（原文：{type(exc).__name__}: {message}）"
            )
            assert "bad.yaml" in message or "config" in message.lower(), (
                f"报错没有指明配置文件（原文：{message}）"
            )
        else:
            raise AssertionError("配置项类型错误时没有报错")
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 4. 用户可见提示未本地化
# --------------------------------------------------------------------------- #
@test
def test_user_messages_are_localized():
    """中文界面下提示必须是中文，英文界面下必须是英文。"""
    env = make_env()
    try:
        app = env.app()
        app.config["SCHEDULER_ENABLED"] = False
        client = app.test_client()

        resp = client.post("/add_source?lang=zh", data={"url": ""}, follow_redirects=True)
        zh_messages = flashes(resp.data.decode("utf-8"))
        assert zh_messages, "中文界面下没有产生任何提示"
        assert any("[\u4e00-\u9fff]" and __import__("re").search(r"[\u4e00-\u9fff]", m)
                   for m in zh_messages), (
            f"中文界面下提示仍是英文: {zh_messages}"
        )

        resp = client.post("/add_source?lang=en", data={"url": ""}, follow_redirects=True)
        en_messages = flashes(resp.data.decode("utf-8"))
        assert en_messages, "英文界面下没有产生任何提示"
        assert not any(__import__("re").search(r"[\u4e00-\u9fff]", m) for m in en_messages), (
            f"英文界面下出现了中文提示: {en_messages}"
        )
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 5. 把内部异常原文直接抛给用户
# --------------------------------------------------------------------------- #
@test
def test_fetch_error_is_not_leaked_to_user():
    """抓取失败时不能把堆栈/连接池原文显示给用户，细节应只进日志。"""
    env = make_env()
    try:
        app = env.app()
        app.config["SCHEDULER_ENABLED"] = False
        client = app.test_client()

        # 一个必然失败的源（域名无法解析），走真实的 fetch_source
        resp = client.post(
            "/add_source?lang=zh",
            data={"name": "Bad", "url": "http://no-such-host.invalid/feed", "interval": "30"},
            follow_redirects=True,
        )
        messages = " ".join(flashes(resp.data.decode("utf-8")))
        leaked = [token for token in
                  ("HTTPConnectionPool", "Max retries", "Traceback", "Errno", "host=")
                  if token in messages]
        assert not leaked, f"提示里泄露了内部异常细节 {leaked}：{messages!r}"
    finally:
        env.close()


# --------------------------------------------------------------------------- #
# 6. 仓库布局（防止上传时再次丢文件）
# --------------------------------------------------------------------------- #
@test
def test_repository_layout_is_complete():
    """上传到 GitHub 时反复丢失的文件，用测试固定下来。"""
    required = [
        "Dockerfile", ".dockerignore", "CHANGELOG.md", "README.md", "README-zh.md",
        "config.yaml", "main.py", "requirements.txt", "docker-compose.yaml",
        ".gitignore", "app/urlsafety.py", "app/security.py", "app/schema.py",
        "app/web/templates/index.html",
        "tests/test_bugfixes.py", "tests/test_v13_bugs.py", "tests/test_v14_bugs.py",
        "tests/test_e2e_smoke.py", "tests/test_debug_reloader.py",
    ]
    missing = [p for p in required if not os.path.exists(os.path.join(REPO, p.replace("/", os.sep)))]
    assert not missing, f"仓库缺少文件: {missing}"

    assert not os.path.exists(os.path.join(REPO, "app", ".dockerfile")), (
        "app/.dockerfile 仍然存在：它已被根目录 Dockerfile 取代，留着会让构建行为不确定"
    )

    gitignore = open(os.path.join(REPO, ".gitignore"), encoding="utf-8", errors="replace").read()
    for pattern in ("data/", "*.db", "*.log", "logs/"):
        assert pattern in gitignore, f".gitignore 缺少运行时忽略项: {pattern}"


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
    for name, exc in failed:
        print(f"  - {name}: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
