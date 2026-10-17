"""阅读视图的小工具。

只做两件事：
* 估算阅读时长（按每分钟 400 字符计，中文阅读速度的常见估计）；
* 把正文切成段落，模板据此渲染，避免把整块 HTML 原样丢进页面。
"""
import html
import re

CHARS_PER_MINUTE = 400
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
BLANK_RE = re.compile(r"\n{3,}")


def plain_text(raw):
    """把 HTML 粗切成纯文本：去脚本/样式、去标签、反转义实体。"""
    if not raw:
        return ""
    text = SCRIPT_RE.sub(" ", raw)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    text = BLANK_RE.sub("\n\n", text)
    return text.strip()


def paragraphs(raw):
    """拆成段落列表，保留空行以维持节奏。"""
    text = plain_text(raw)
    if not text:
        return []
    blocks = [block.strip() for block in re.split(r"\n{2,}", text)]
    return [block for block in blocks if block]


def reading_minutes(raw):
    """估算阅读时长，最少 1 分钟。"""
    length = len(plain_text(raw))
    return max(1, round(length / CHARS_PER_MINUTE))
