"""v2.0.9 补丁用例：命令面板必须能关闭（[hidden] 被 display 覆盖的回归）。"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_bugfixes import make_env  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def test_hidden_attribute_is_not_overridden():
    """任何设置了 display 的元素，被标记 [hidden] 时必须真正隐藏。

    浏览器默认的 [hidden] { display: none } 是弱声明，样式表里的 display:flex
    会覆盖它 —— 这正是命令面板「点了关不掉」的根因。
    """
    css = open(os.path.join(REPO, "app", "static", "app.css"), encoding="utf-8").read()
    assert re.search(r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", css), \
        "缺少 [hidden] 的全局 display:none !important 兜底"
    assert re.search(r"\.palette-mask\[hidden\]\s*\{[^}]*display:\s*none", css), \
        "命令面板遮罩缺少针对性的隐藏规则"


@test
def test_elements_with_display_also_have_hidden_guard():
    """凡是靠 display 布局、又可能被 hidden 的元素，都要有隐藏规则。"""
    css = open(os.path.join(REPO, "app", "static", "app.css"), encoding="utf-8").read()
    for selector in (".palette-mask", ".batch-bar", ".live-search"):
        block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        if not block:
            continue
        uses_flex_or_grid = re.search(r"display:\s*(flex|grid|block)", block.group(1))
        if uses_flex_or_grid:
            guard = re.search(r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", css)
            assert guard, f"{selector} 使用 display 布局但缺少 [hidden] 兜底"


@test
def test_palette_close_is_defensive():
    js = open(os.path.join(REPO, "app", "static", "app.js"), encoding="utf-8").read()
    # 关闭时必须同时设置 hidden 与内联 display，样式表失效也能关
    close_fn = re.search(r"function close\(\)\s*\{(.*?)\}", js, re.S)
    assert close_fn, "找不到 close()"
    body = close_fn.group(1)
    assert "hidden = true" in body, "close() 没有设置 hidden"
    assert "style.display" in body, "close() 没有内联兜底"
    # 初始化时就应处于关闭状态
    assert re.search(r"// 面板初始必须是关闭状态\s*\n\s*close\(\);", js), "面板没有在初始化时关闭"


@test
def test_palette_click_handling_is_precise():
    js = open(os.path.join(REPO, "app", "static", "app.js"), encoding="utf-8").read()
    assert "closest('.palette')" in js, "遮罩点击判定应排除面板内部"
    assert "stopPropagation" in js, "面板内点击应阻止冒泡"


@test
def test_palette_markup_starts_hidden():
    env = make_env()
    try:
        app = env.app()
        body = app.test_client().get("/").data.decode("utf-8")
        assert re.search(r'id="palette-mask"[^>]*hidden', body), "命令面板没有默认隐藏"
        assert 'id="palette-input"' in body
        assert 'id="palette-data"' in body
        # 条目数据必须是合法 JSON
        import json

        data = re.search(r'id="palette-data">(.*?)</script>', body, re.S).group(1)
        items = json.loads(data)
        assert items and all("href" in item and "label" in item for item in items), items
    finally:
        env.close()


def main():
    passed, failed = [], []
    for fn in TESTS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed.append((fn.__name__, exc))
            print(f"FAIL  {fn.__name__}\n      {type(exc).__name__}: {exc}")
        else:
            passed.append(fn.__name__)
            print(f"PASS  {fn.__name__}")
    print("\n" + "=" * 72)
    print(f"PASSED {len(passed)} / {len(TESTS)}   FAILED {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
