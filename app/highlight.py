"""搜索关键词高亮。

要点：
1. **先转义再高亮**：文章标题与摘要来自外部 feed，直接拼 `<mark>` 会引入 XSS。
2. 高亮片段由服务端生成，前端不再做字符串替换，避免二次注入。
3. 只高亮前若干个匹配，防止超长摘要被切成上千个 `<mark>`。
"""
import html
import re

MAX_MATCHES = 30
MARK_OPEN = '<mark class="hl">'
MARK_CLOSE = "</mark>"


def _pattern(term):
    return re.compile(re.escape(term), re.IGNORECASE)


def highlight(text, terms, max_matches=MAX_MATCHES):
    """把 text 里出现的 terms 包成 <mark>，返回已转义的 HTML 片段。"""
    if not text:
        return ""
    escaped = html.escape(str(text))
    terms = [term.strip() for term in (terms or []) if term and term.strip()]
    if not terms:
        return escaped

    # 转义后关键词本身也要转义，否则 & < > 之类无法匹配
    escaped_terms = sorted({html.escape(term) for term in terms}, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(term) for term in escaped_terms), re.IGNORECASE)

    out, last, count = [], 0, 0
    for match in pattern.finditer(escaped):
        if count >= max_matches:
            break
        out.append(escaped[last:match.start()])
        out.append(f"{MARK_OPEN}{match.group(0)}{MARK_CLOSE}")
        last = match.end()
        count += 1
    out.append(escaped[last:])
    return "".join(out)


def split_terms(query):
    """把搜索框输入拆成关键词（空白分隔，去重，限制数量）。"""
    if not query:
        return []
    seen, terms = set(), []
    for token in str(query).split():
        token = token.strip()
        if len(token) < 1 or token.lower() in seen:
            continue
        seen.add(token.lower())
        terms.append(token)
        if len(terms) >= 8:
            break
    return terms
