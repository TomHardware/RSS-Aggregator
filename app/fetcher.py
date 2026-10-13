
import logging
import time
from datetime import datetime

import feedparser
import requests
from flask import current_app, has_app_context
from sqlalchemy.exc import DBAPIError, IntegrityError

from app import db
from app.fulltext import extract_full_text
from app.models import Article, Source, utcnow
from app.urlsafety import DEFAULT_MAX_BYTES, is_safe_url, safe_get

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 5000
MAX_TITLE_CHARS = 500
MAX_LINK_CHARS = 1000
LINK_QUERY_CHUNK = 400  # SQLite 的 SQL 变量上限是 999，分批查询


def _config(key, default=None):
    return current_app.config.get(key, default)


def _entry_link(entry):
    link = entry.get("link") or entry.get("id") or ""
    return link.strip() if isinstance(link, str) else ""


def _entry_published(entry):
    """条目时间；feedparser 已把时间归一到 UTC struct_time。"""
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6])
            except (TypeError, ValueError):
                logger.debug("无法解析时间字段 %s: %r", key, parsed)
    return utcnow()


def _existing_links(links):
    """一次性查出已存在的链接。"""
    if not links:
        return set()
    found = set()
    for start in range(0, len(links), LINK_QUERY_CHUNK):
        chunk = links[start:start + LINK_QUERY_CHUNK]
        rows = db.session.query(Article.link).filter(Article.link.in_(chunk)).all()
        found.update(row[0] for row in rows)
    return found


def _build_articles(source, feed):
    """把 feed 条目转成待入库的 Article，返回 (新增列表, 跳过全文抽取数)。

    注意 `fetch.max_entries` 限制的是**本次新增条数**，不是「只看前 N 条」：
    原来直接切 `feed.entries[:N]`，一个源一次发布 200 条时，
    第 51 条之后的内容会被静默丢弃、再也不会入库。
    现在遍历全部条目，达到新增上限就停下，剩下的留给下次抓取补齐。
    """
    limit = max(0, int(_config("FETCH_MAX_ENTRIES", 50)))
    entries = list(feed.entries) if limit else []

    existing = _existing_links([link for link in (_entry_link(e) for e in entries) if link])

    fulltext_budget = 0
    if _config("FULLTEXT_ENABLED", True):
        fulltext_budget = max(0, int(_config("FULLTEXT_MAX_PER_FETCH", 5)))
    fulltext_timeout = int(_config("FULLTEXT_TIMEOUT", 10))
    allow_private = bool(_config("SECURITY_ALLOW_PRIVATE_NETWORKS", False))

    created = []
    skipped = 0
    seen = set()
    for entry in entries:
        link = _entry_link(entry)
        if not link or link in existing or link in seen:
            continue
        if len(link) > MAX_LINK_CHARS:
            # 截断会把它变成一个「另一个 URL」：宁可跳过，也不能存错地址
            logger.warning(
                "跳过链接过长的条目（%d 字符，上限 %d）: %.60s...",
                len(link), MAX_LINK_CHARS, link,
            )
            continue
        seen.add(link)

        embedded = entry.get("content")
        if embedded:
            content = (embedded[0].get("value") or "")[:MAX_CONTENT_CHARS]
        elif fulltext_budget > 0:
            fulltext_budget -= 1
            content = extract_full_text(
                link, timeout=fulltext_timeout, allow_private=allow_private
            )[:MAX_CONTENT_CHARS]
        else:
            content = ""
            skipped += 1

        created.append(
            Article(
                title=(entry.get("title") or "Untitled")[:MAX_TITLE_CHARS],
                link=link,
                summary=entry.get("summary") or "",
                content=content,
                published=_entry_published(entry),
                source_id=source.id,
            )
        )
        if limit and len(created) >= limit:
            logger.info(
                "source=%s 本次已达新增上限 %d 条，其余条目留待下次抓取",
                source.id, limit,
            )
            break
    return created, skipped


def _persist(articles):
    """批量入库；撞上唯一约束时降级为逐条 savepoint 重试。返回真正入库的文章。"""
    if not articles:
        return []

    for article in articles:
        db.session.add(article)
    try:
        db.session.commit()
        return list(articles)
    except IntegrityError:
        db.session.rollback()
        logger.warning("检测到并发写入造成的重复条目，改为逐条入库")

    saved = []
    for article in articles:
        try:
            with db.session.begin_nested():
                db.session.add(article)
            saved.append(article)
        except IntegrityError:
            logger.debug("跳过重复文章: %s", article.link)
    db.session.commit()
    return saved


def _notify(source, articles):
    """发送通知邮件；只列出真正入库的文章，任何异常都不影响抓取结果。"""
    try:
        from app.notifications import send_email

        subject = f"RSS Aggregator: {source.name} 更新了 {len(articles)} 篇文章"
        body = "\n".join(article.title or "" for article in articles)
        send_email(subject, body)
    except Exception as exc:  # noqa: BLE001 - 通知失败不能影响抓取
        logger.error("发送通知邮件失败: %s", exc)


def _finish_task(task_id, ok, count=0, message=""):
    """把结果写回任务登记表（供前端轮询）；没有 task_id 时什么都不做。"""
    if not task_id:
        return
    from app.tasks import finish

    finish(task_id, ok, count, message)


def fetch_source(source_id, notify=False, task_id=None):
    """抓取一个源。必须在应用上下文中调用（调度器已自动推入上下文）。

    传入 `task_id` 时会把结果登记到 app/tasks.py，供 `/api/status` 与前端轮询展示。
    """
    if not has_app_context():
        raise RuntimeError(
            "fetch_source 需要 Flask 应用上下文；后台任务请通过 app.scheduler 注册"
        )

    source = db.session.get(Source, source_id)
    if source is None:
        _finish_task(task_id, False, 0, "Source not found")
        return False, "Source not found"

    allow_private = bool(_config("SECURITY_ALLOW_PRIVATE_NETWORKS", False))
    if not is_safe_url(source.url, allow_private=allow_private):
        logger.warning("抓取被安全策略拒绝（疑似内网地址）: %s", source.url)
        _finish_task(task_id, False, 0, "URL blocked by security policy")
        return False, "URL blocked by security policy"

    retries = max(1, int(_config("FETCH_RETRIES", 3)))
    timeout = int(_config("FETCH_TIMEOUT", 15))
    max_bytes = int(_config("FETCH_MAX_BYTES", DEFAULT_MAX_BYTES))
    headers = {"User-Agent": _config("FETCH_USER_AGENT", "RSSAggregator/1.0")}
    last_error = "unknown error"

    for attempt in range(1, retries + 1):
        try:
            result = safe_get(
                source.url,
                timeout=timeout,
                headers=headers,
                max_bytes=max_bytes,
                allow_private=allow_private,
            )
            if result.status_code != 200:
                message = f"HTTP {result.status_code}"
                if result.status_code >= 500 or result.status_code == 429:
                    raise requests.HTTPError(message)  # 服务端/限流问题，值得重试
                raise ValueError(message)  # 4xx 重试没有意义

            feed = feedparser.parse(result.content)
            if feed.bozo:
                logger.warning("Feed 解析告警 %s: %s", source.url, feed.get("bozo_exception"))
                if not feed.entries:
                    raise ValueError(f"Feed 无法解析: {feed.get('bozo_exception')}")

            created, skipped = _build_articles(source, feed)
            saved = _persist(created)

            source.last_fetched = utcnow()
            db.session.commit()

            # 保留策略：按配置裁剪过期/超量的文章（未配置时不做任何事）
            from app.retention import apply_retention

            removed = apply_retention(source.id)
        except Exception as exc:  # noqa: BLE001 - 统一走重试/失败分支
            db.session.rollback()
            last_error = str(exc) or exc.__class__.__name__
            retryable = isinstance(exc, (requests.RequestException, DBAPIError))
            logger.error(
                "抓取 %s 第 %d/%d 次失败: %s", source.url, attempt, retries, last_error
            )
            if attempt >= retries or not retryable:
                break
            time.sleep(min(2 ** attempt, 10))
            continue

        if notify and saved:
            _notify(source, saved)
        logger.info(
            "从 %s 抓取到 %d 条新文章（%d 条因全文抽取配额未抓正文，保留策略清理 %d 条）",
            source.name, len(saved), skipped, removed,
        )
        _finish_task(task_id, True, len(saved), f"Fetched {len(saved)} new entries")
        return True, f"Fetched {len(saved)} new entries"

    _finish_task(task_id, False, 0, last_error)
    return False, last_error
