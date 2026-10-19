"""搜索与过滤。

修复要点：
1. 转义 LIKE 通配符。原来 `pattern = f'%{query}%'` 把用户输入的 % 和 _ 直接当语法：
   搜索一个 `%` 会退化成全表匹配，`_` 会匹配任意单字符。（注入本身不存在，
   SQLAlchemy 已参数化，这里修的是语义错误与全表扫描。）
2. 分页参数做范围限制，避免 `?page=-1` 之类输入。
3. 排序加 id 兜底，保证同一时间戳的文章翻页顺序稳定（否则可能重复/漏出）。
"""
from sqlalchemy import or_

from app.models import Article, Source

MAX_PER_PAGE = 200
LIKE_ESCAPE = "\\"


def db_session():
    """延迟导入：避免模块级循环引用。"""
    from app import db

    return db.session


def _like_pattern(text):
    """把用户输入转成安全的 LIKE 模式。"""
    escaped = (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def search_articles(query, source_id=None, unread_only=False, page=1, per_page=50,
                    bookmarked_only=False, category=None, exclude=None, sort="newest",
                    min_length=0):
    """按标题/摘要/正文搜索，支持来源、分类、未读、收藏、关键词屏蔽、排序与长度过滤。"""
    """按标题/摘要/正文搜索，支持来源、分类、未读与收藏过滤、分页。"""
    q = Article.query

    if query and query.strip():
        pattern = _like_pattern(query.strip())
        q = q.filter(
            or_(
                Article.title.like(pattern, escape=LIKE_ESCAPE),
                Article.summary.like(pattern, escape=LIKE_ESCAPE),
                Article.content.like(pattern, escape=LIKE_ESCAPE),
            )
        )

    if source_id:
        q = q.filter(Article.source_id == source_id)

    if category:
        # 分类存在订阅源上，按源分类过滤文章
        q = q.filter(
            Article.source_id.in_(
                db_session().query(Source.id).filter(Source.category == category)
            )
        )

    if unread_only:
        # read 为 NULL（历史数据或外部写入）并不是「已读」，
        # 只判 is_(False) 会让这些文章从「只看未读」里凭空消失。
        q = q.filter(or_(Article.read.is_(False), Article.read.is_(None)))

    if bookmarked_only:
        q = q.filter(Article.bookmarked.is_(True))

    # 关键词屏蔽：标题命中任一词就不展示（用于过滤噪音源）
    for word in (exclude or []):
        word = (word or "").strip()
        if not word:
            continue
        q = q.filter(~Article.title.like(_like_pattern(word), escape=LIKE_ESCAPE))

    # 摘要长度下限：过滤掉只有标题没有内容的条目
    if min_length:
        from sqlalchemy import func

        q = q.filter(func.length(func.coalesce(Article.summary, "")) >= int(min_length))

    try:
        page = max(1, int(page or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = min(MAX_PER_PAGE, max(1, int(per_page or 50)))
    except (TypeError, ValueError):
        per_page = 50

    if sort == "oldest":
        ordering = (Article.published.asc(), Article.id.asc())
    elif sort == "source":
        ordering = (Article.source_id.asc(), Article.published.desc(), Article.id.desc())
    else:
        ordering = (Article.published.desc(), Article.id.desc())

    return (
        q.order_by(*ordering)
        .paginate(page=page, per_page=per_page, error_out=False)
    )
