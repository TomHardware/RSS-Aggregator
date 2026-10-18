"""实时接口：分页 JSON / HTML 片段，以及 SSE 任务推送。

设计取舍：
* `/api/articles` 同时支持 JSON 与 HTML 片段（`html=1`）。前端「加载更多」与
  「即时搜索」都复用它，**片段由服务端渲染**，避免把文章卡片的模板在 JS 里重写一遍。
* `/api/events` 用 Server-Sent Events 推送任务状态，替代每 5 秒轮询；
  浏览器不支持或断线时，前端会退回轮询，功能不受影响。

SSE 是长连接：这里限制单次连接的最长时间并发送心跳，避免代理层超时或线程堆积。
"""
import json
import logging
import time



from flask import Response, current_app, render_template, request

from app import db
from app.models import Article, Source
from app.search import search_articles
from app.web.routes import _context, _t, web_bp

logger = logging.getLogger(__name__)

MAX_LIMIT = 30
SSE_MAX_SECONDS = 240   # 单条连接最长存活时间
SSE_INTERVAL = 2.0      # 推送间隔
SSE_TICK_LIMIT = int(SSE_MAX_SECONDS / SSE_INTERVAL)


def _parse_filters():
    return {
        "q": request.args.get("q", ""),
        "source_id": request.args.get("source_id", type=int),
        "unread": request.args.get("unread", "false").lower() == "true",
        "bookmarked": request.args.get("bookmarked", "false").lower() == "true",
        "category": (request.args.get("category") or "").strip(),
    }


def _page_args():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        limit = min(MAX_LIMIT, max(1, int(request.args.get("limit", 10))))
    except (TypeError, ValueError):
        limit = 10
    return page, limit


@web_bp.route("/api/articles")
def api_articles():
    """分页文章列表：`html=1` 返回片段，否则返回 JSON。"""
    filters = _parse_filters()
    page, limit = _page_args()

    pagination = search_articles(
        filters["q"], filters["source_id"], filters["unread"],
        page=page, per_page=limit, bookmarked_only=filters["bookmarked"],
        category=filters["category"] or None,
    )
    items = list(pagination.items)

    if request.args.get("html"):
        return render_template("_cards.html", items=items, **_context())

    return {
        "page": pagination.page,
        "per_page": pagination.per_page,
        "total": pagination.total,
        "has_next": bool(pagination.has_next),
        "has_prev": bool(pagination.has_prev),
        "items": [
            {
                "id": article.id,
                "title": article.title,
                "link": article.link,
                "summary": (article.summary or "")[:300],
                "published": article.published.isoformat(sep=" ") if article.published else None,
                "read": bool(article.read),
                "bookmarked": bool(article.bookmarked),
                "source_id": article.source_id,
            }
            for article in items
        ],
    }


def _status_payload():
    """SSE 推送的负载：任务进展 + 未读数 + 版本号。"""
    from app.services import status_snapshot

    snapshot = status_snapshot()
    return {
        "version": snapshot["version"],
        "running": snapshot["running"],
        "recent": snapshot["recent"][:3],
        "unread_total": snapshot["unread_total"],
        "sources": snapshot["sources"],
    }


@web_bp.route("/api/events")
def api_events():
    """SSE 任务推送。`?once=1` 只推一次就关闭，便于测试与排查。

    注意：这里必须自己处理应用上下文——生成器是在请求上下文结束之后才被
    Werkzeug 迭代的，直接用 `current_app` 会抛「Working outside of application context」。
    """
    app = current_app._get_current_object()
    once = request.args.get("once", "").lower() in ("1", "true", "yes")

    def stream():
        last_payload = None
        ticks = 0
        while ticks < (1 if once else SSE_TICK_LIMIT):
            ticks += 1
            try:
                with app.app_context():
                    payload = json.dumps(_status_payload(), ensure_ascii=False)
            except Exception as exc:  # noqa: BLE001 - 单次取数失败不应断开连接
                logger.warning("SSE 取状态失败: %s", exc)
                yield ": error\n\n"
            else:
                if payload != last_payload:
                    yield f"data: {payload}\n\n"
                    last_payload = payload
                else:
                    yield ": keep-alive\n\n"
            if not once:
                time.sleep(SSE_INTERVAL)
        yield "event: close\ndata: {}\n\n"

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",   # 告诉 nginx 不要缓冲
            "Connection": "keep-alive",
        },
    )
