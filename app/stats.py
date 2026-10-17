"""统计汇总：给 /stats 页面提供数据。

只用 SQLAlchemy 的聚合查询，不引入图表库；前端用纯 CSS 画柱状图，
这样自托管环境不需要 CDN 也能显示。
"""
import logging
from datetime import timedelta

from sqlalchemy import case, func

from app import db
from app.models import Article, Source, utcnow

logger = logging.getLogger(__name__)

DAYS = 14


def daily_counts(days=DAYS):
    """最近 N 天每天入库的文章数，返回 [(日期字符串, 数量)]，按时间正序。"""
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1)
    try:
        rows = (
            db.session.query(func.date(Article.published), func.count(Article.id))
            .filter(Article.published.isnot(None))
            .filter(Article.published >= start)
            .group_by(func.date(Article.published))
            .all()
        )
    except Exception as exc:  # noqa: BLE001 - 统计失败不应让页面 500
        logger.warning("按日统计失败: %s", exc)
        return []

    counts = {}
    for day, count in rows:
        key = str(day)[:10]
        counts[key] = count

    result = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date().isoformat()
        result.append((day, counts.get(day, 0)))
    return result


def source_breakdown(limit=10):
    """按源统计文章数与未读数。"""
    try:
        rows = (
            db.session.query(
                Source.name,
                func.count(Article.id),
                func.sum(case((Article.read.is_(True), 0), else_=1)),
            )
            .outerjoin(Article, Article.source_id == Source.id)
            .group_by(Source.id)
            .order_by(func.count(Article.id).desc())
            .limit(limit)
            .all()
        )
    except Exception as exc:  # noqa: BLE001 - 统计失败不应让页面 500
        logger.warning("按源统计失败: %s", exc)
        return []
    return [
        {"name": name or "(unnamed)", "total": total or 0, "unread": unread or 0}
        for name, total, unread in rows
    ]


def overview():
    """总量与比例，用于统计页顶部卡片。"""
    total = Article.query.count()
    unread = Article.query.filter(Article.read.isnot(True)).count()
    bookmarked = Article.query.filter(Article.bookmarked.is_(True)).count()
    sources = Source.query.count()
    active = Source.query.filter(Source.enabled.is_(True)).count()
    return {
        "articles": total,
        "unread": unread,
        "bookmarked": bookmarked,
        "sources": sources,
        "sources_active": active,
        "read_ratio": round((total - unread) / total * 100) if total else 0,
    }
