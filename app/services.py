"""共享业务操作层。

Web 路由、网页控制台与桌面 GUI 都调用这里，避免同一套逻辑写三遍。
所有函数都要求处于 Flask 应用上下文中（桌面 GUI 会在主线程推入上下文）。
"""
import logging
import threading

from flask import current_app, has_app_context

from app import db, tasks
from app.fetcher import fetch_source
from app.models import Article, Source, utcnow
from app.scheduler import MIN_INTERVAL_MINUTES, schedule_source, unschedule_source
from app.urlsafety import is_safe_url

logger = logging.getLogger(__name__)

MAX_URL_CHARS = 500      # 与 Source.url 列宽一致
MAX_NAME_CHARS = 200     # 与 Source.name 列宽一致
MAX_CATEGORY_CHARS = 100  # 与 Source.category 列宽一致
DEFAULT_INTERVAL_MINUTES = 30


class ServiceError(ValueError):
    """带用户可读说明的操作失败（提示文案的翻译键放在 message_key 上）。"""

    def __init__(self, message_key, **params):
        super().__init__(message_key)
        self.message_key = message_key
        self.params = params


def _config(key, default=None):
    if has_app_context():
        return current_app.config.get(key, default)
    return default


def scheduling_enabled():
    return bool(_config("SCHEDULER_ENABLED", True))


def parse_interval(raw, default=DEFAULT_INTERVAL_MINUTES):
    """容错解析更新间隔，并强制不小于最小值。"""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        value = int(default or DEFAULT_INTERVAL_MINUTES)
    return max(MIN_INTERVAL_MINUTES, value)


def _check_length(value, limit, message_key):
    if value and len(value) > limit:
        raise ServiceError(message_key, length=len(value), limit=limit)
    return value


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def list_feeds():
    return Source.query.order_by(Source.id).all()


def get_feed(source_id):
    source = db.session.get(Source, source_id)
    if source is None:
        raise ServiceError("msg_feed_not_found")
    return source


def list_articles(source_id=None, unread_only=False, limit=200):
    query = Article.query
    if source_id:
        query = query.filter(Article.source_id == source_id)
    if unread_only:
        from sqlalchemy import or_

        query = query.filter(or_(Article.read.is_(False), Article.read.is_(None)))
    return (
        query.order_by(Article.published.desc(), Article.id.desc()).limit(limit).all()
    )


def unread_counts():
    """返回 ({source_id: 未读数}, 总未读数)。"""
    from sqlalchemy import func, or_

    try:
        rows = (
            db.session.query(Article.source_id, func.count(Article.id))
            .filter(or_(Article.read.is_(False), Article.read.is_(None)))
            .group_by(Article.source_id)
            .all()
        )
    except Exception as exc:  # noqa: BLE001 - 统计失败不应让界面崩掉
        logger.warning("统计未读数失败: %s", exc)
        return {}, 0
    counts = {source_id: count for source_id, count in rows}
    return counts, sum(counts.values())


def status_snapshot():
    """任务进展 + 计数，供网页控制台与桌面 GUI 共用。"""
    data = tasks.snapshot()
    _, total_unread = unread_counts()
    return {
        "version": _config("APP_VERSION", ""),
        "server_time": utcnow().isoformat(sep=" ", timespec="seconds"),
        "running": data["running"],
        "recent": data["recent"],
        "unread_total": total_unread,
        "sources": Source.query.count(),
        "articles": Article.query.count(),
    }


# --------------------------------------------------------------------------- #
# 订阅源维护
# --------------------------------------------------------------------------- #
def schedule(source):
    """把源注册进调度器；未启用调度或调度器未绑定 app 时静默跳过。"""
    if not scheduling_enabled():
        return None
    try:
        return schedule_source(source, notify=True, app=current_app._get_current_object())
    except Exception as exc:  # noqa: BLE001 - 调度失败不应让界面报错
        logger.warning("注册调度任务失败 source=%s: %s", source.id, exc)
        return None


def unschedule(source_id):
    if not scheduling_enabled():
        return
    try:
        unschedule_source(source_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("移除调度任务失败 source=%s: %s", source_id, exc)


def add_feed(url, name="", category="General", interval=None):
    """新增订阅源。失败时抛 ServiceError（带翻译键）。"""
    url = (url or "").strip()
    name = (name or "").strip()
    category = (category or "").strip() or "General"

    if not url:
        raise ServiceError("msg_url_required")
    _check_length(url, MAX_URL_CHARS, "msg_url_too_long")
    _check_length(name, MAX_NAME_CHARS, "msg_field_too_long")
    _check_length(category, MAX_CATEGORY_CHARS, "msg_field_too_long")

    if not is_safe_url(url, allow_private=bool(_config("SECURITY_ALLOW_PRIVATE_NETWORKS", False))):
        logger.warning("拒绝添加被安全策略拦截的地址: %s", url)
        raise ServiceError("msg_url_blocked")

    if Source.query.filter_by(url=url).first():
        raise ServiceError("msg_feed_exists", url=url)

    source = Source(
        name=name or url,
        url=url,
        category=category,
        interval=parse_interval(
            interval, _config("SCHEDULER_DEFAULT_INTERVAL", DEFAULT_INTERVAL_MINUTES)
        ),
    )
    db.session.add(source)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise ServiceError("msg_feed_exists", url=url) from None

    schedule(source)
    return source


def edit_feed(source_id, name=None, category=None, interval=None):
    source = get_feed(source_id)
    if name is not None:
        name = name.strip()
        _check_length(name, MAX_NAME_CHARS, "msg_field_too_long")
        source.name = name or source.url
    if category is not None:
        category = category.strip() or "General"
        _check_length(category, MAX_CATEGORY_CHARS, "msg_field_too_long")
        source.category = category
    if interval is not None:
        source.interval = parse_interval(interval, source.interval)
    db.session.commit()
    schedule(source)  # 间隔可能变了，重新注册
    return source


def toggle_feed(source_id):
    source = get_feed(source_id)
    source.enabled = not source.enabled
    db.session.commit()
    schedule(source)  # 暂停 → 移除任务；恢复 → 重新注册
    return source


def delete_feed(source_id):
    source = get_feed(source_id)
    unschedule(source.id)
    db.session.delete(source)
    db.session.commit()


def mark_read(article_id, read=True):
    article = db.session.get(Article, article_id)
    if article is None:
        raise ServiceError("msg_article_not_found")
    article.read = read
    db.session.commit()
    return article


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #
def refresh_feed(source_id, notify=True):
    """同步抓取一个源，返回 (ok, message)。"""
    return fetch_source(get_feed(source_id).id, notify=notify)


def start_background_fetch(source_id, source_name=None, kind="initial_fetch"):
    """后台抓取并登记任务；并发已满时返回 None（调用方回落同步抓取）。"""
    app = current_app._get_current_object() if has_app_context() else None
    try:
        task_id = tasks.start(
            kind,
            source_id=source_id,
            source_name=source_name,
            max_concurrent=_config("TASKS_MAX_CONCURRENT"),
        )
    except tasks.TooManyTasks as exc:
        logger.warning("后台任务过多，改为同步抓取 source=%s: %s", source_id, exc)
        return None

    def worker():
        with app.app_context():
            try:
                fetch_source(source_id, notify=True, task_id=task_id)
            except Exception as exc:  # noqa: BLE001 - 后台任务不能把异常抛给界面
                logger.error("后台抓取失败 source=%s: %s", source_id, exc)
                tasks.finish(task_id, False, 0, str(exc))

    thread = threading.Thread(target=worker, name=f"{kind}-{source_id}", daemon=True)
    thread.start()
    return thread


def fetch_all(notify=False):
    """触发全部启用中的源抓取：能后台就后台，超出并发则同步。返回 (已启动, 同步完成)。"""
    started, done = 0, 0
    for source in list_feeds():
        if not source.enabled:
            continue
        if start_background_fetch(source.id, source.name, kind="manual_fetch") is None:
            refresh_feed(source.id, notify=notify)
            done += 1
        else:
            started += 1
    return started, done
