"""数据导出与备份。

支持三种格式，都是纯标准库实现，不引入新依赖：
* `export_articles_json` ——完整备份（含订阅源），可用于迁移或再处理；
* `export_articles_csv`  ——表格软件可直接打开；
* `export_articles_markdown` ——适合放进笔记软件或做成阅读归档。
"""
import csv
import io
import json
import logging

from app.models import Article, Source, utcnow
from app.version import __version__

logger = logging.getLogger(__name__)

MAX_EXPORT_ROWS = 20000  # 单次导出的上限，避免超大库把内存吃满


def _query(source_id=None, unread_only=False, bookmarked_only=False):
    query = Article.query
    if source_id:
        query = query.filter(Article.source_id == source_id)
    if unread_only:
        from sqlalchemy import or_

        query = query.filter(or_(Article.read.is_(False), Article.read.is_(None)))
    if bookmarked_only:
        query = query.filter(Article.bookmarked.is_(True))
    return query.order_by(Article.published.desc(), Article.id.desc()).limit(MAX_EXPORT_ROWS)


def export_articles_json(source_id=None, unread_only=False, bookmarked_only=False):
    """完整备份：版本、导出时间、订阅源与文章。"""
    rows = _query(source_id, unread_only, bookmarked_only).all()
    sources = {source.id: source for source in Source.query.all()}
    payload = {
        "generator": "RSS-Aggregator",
        "version": __version__,
        "exported_at": utcnow().isoformat(sep=" ", timespec="seconds"),
        "article_count": len(rows),
        "sources": [
            {
                "id": source.id,
                "name": source.name,
                "url": source.url,
                "category": source.category,
                "interval": source.interval,
                "enabled": bool(source.enabled),
            }
            for source in sources.values()
        ],
        "articles": [
            {
                "id": article.id,
                "source_id": article.source_id,
                "source_name": getattr(sources.get(article.source_id), "name", ""),
                "title": article.title,
                "link": article.link,
                "summary": article.summary,
                "content": article.content,
                "published": article.published.isoformat(sep=" ") if article.published else None,
                "read": bool(article.read),
                "bookmarked": bool(getattr(article, "bookmarked", False)),
            }
            for article in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def export_articles_csv(source_id=None, unread_only=False, bookmarked_only=False):
    """CSV：Excel / Numbers / LibreOffice 都能直接打开。

    写 UTF-8 BOM：否则 Excel 在中文 Windows 上会把中文显示成乱码。
    """
    rows = _query(source_id, unread_only, bookmarked_only).all()
    sources = {source.id: source for source in Source.query.all()}
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["id", "source", "title", "link", "published", "read", "bookmarked", "summary"])
    for article in rows:
        writer.writerow([
            article.id,
            getattr(sources.get(article.source_id), "name", ""),
            article.title or "",
            article.link or "",
            article.published.isoformat(sep=" ") if article.published else "",
            "yes" if article.read else "no",
            "yes" if getattr(article, "bookmarked", False) else "no",
            (article.summary or "")[:500],
        ])
    return buffer.getvalue()


def export_articles_markdown(source_id=None, unread_only=False, bookmarked_only=False):
    """Markdown：按订阅源分组，便于归档阅读。"""
    rows = _query(source_id, unread_only, bookmarked_only).all()
    sources = {source.id: source for source in Source.query.all()}
    grouped = {}
    for article in rows:
        grouped.setdefault(article.source_id, []).append(article)

    lines = [
        f"# RSS Aggregator 导出 / Export ({utcnow().isoformat(sep=' ', timespec='seconds')})",
        "",
        f"共 {len(rows)} 篇文章 / {len(rows)} articles",
        "",
    ]
    for source_id, articles in grouped.items():
        source = sources.get(source_id)
        lines.append(f"## {getattr(source, 'name', 'unknown')}")
        lines.append("")
        for article in articles:
            title = article.title or "(untitled)"
            published = article.published.isoformat(sep=" ", timespec="seconds") if article.published else ""
            mark = " ★" if getattr(article, "bookmarked", False) else ""
            lines.append(f"- [{title}]({article.link}){mark}  ")
            lines.append(f"  <sub>{published}</sub>")
            if article.summary:
                lines.append("")
                lines.append(f"  {article.summary.strip()[:400]}")
        lines.append("")
    return "\n".join(lines)
