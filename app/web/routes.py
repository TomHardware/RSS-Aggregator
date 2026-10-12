"""Web 路由。

修复要点：
1. 蓝图必须声明 template_folder='templates'。模板在 app/web/templates/，
   而 Flask(__name__) 只会去 app/templates/ 找，原来每个页面都
   TemplateNotFound → 整站 500。
2. 分页链接保留当前查询条件（原来用 request.view_args，把 q / lang / source_id /
   unread 全丢了，搜索翻到第二页就变成全量列表）。
3. 会改状态的操作用 POST（原来是 GET：浏览器预取、<img> 标签、爬虫都能删库）。
4. interval 容错解析并强制 ≥ 5 分钟（原来非数字直接 500，0/负数会注册非法任务）。
5. 重复 URL 友好提示而不是 IntegrityError 500；新增/导入的源立即注册调度任务。
6. 补上「标记已读」入口，让 unread 过滤形成闭环（原来 read 永远是 False）。
"""
import logging
import re
import threading

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError

from app import db, tasks
from app.auth import (
    auth_enabled,
    client_key,
    current_user,
    login_user,
    logout_user,
    throttle,
    verify_credentials,
)
from app.fetcher import fetch_source
from app.i18n import translate
from app.models import Article, Source, utcnow
from app.opml import export_opml, import_opml
from app.search import search_articles
from app.security import check_csrf_origin
from app.urlsafety import is_safe_url

logger = logging.getLogger(__name__)

MIN_INTERVAL_MINUTES = 5
DEFAULT_INTERVAL_MINUTES = 30
PER_PAGE = 50
MAX_OPML_BYTES = 5 * 1024 * 1024
FILTER_KEYS = ("q", "source_id", "unread", "lang")
MAX_URL_CHARS = 500      # 与 Source.url 的列宽一致
MAX_NAME_CHARS = 200     # 与 Source.name 的列宽一致
MAX_CATEGORY_CHARS = 100  # 与 Source.category 的列宽一致
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

web_bp = Blueprint("web", __name__, template_folder="templates")
web_bp.before_request(check_csrf_origin)


# --------------------------------------------------------------------------- #
# 辅助函数
# --------------------------------------------------------------------------- #
def _language():
    return request.args.get("lang") or current_app.config.get("APP_LANGUAGE", "en")


def _page_url(page):
    """保留当前查询条件的分页链接。"""
    args = request.args.to_dict(flat=True)
    args["page"] = page
    return url_for(request.endpoint, **args)


def _lang_url(language):
    """切换语言时保留其它查询条件。"""
    args = request.args.to_dict(flat=True)
    args["lang"] = language
    args.pop("page", None)
    return url_for(request.endpoint, **args)


def _translator(lang):
    """模板里用的翻译函数，支持占位符：_('unread_total', count=3)。"""

    def _(key, **kwargs):
        text = translate(key, lang)
        return text.format(**kwargs) if kwargs else text

    return _


def _context(**extra):
    lang = _language()
    counts, total_unread = _unread_counts()
    # 导出链接要带上当前的过滤条件，否则「导出」导出的内容和眼前看到的不一致
    export_filters = {}
    if request.args.get("source_id", type=int):
        export_filters["source_id"] = request.args.get("source_id", type=int)
    if request.args.get("unread", "").lower() == "true":
        export_filters["unread"] = "true"
    if request.args.get("bookmarked", "").lower() == "true":
        export_filters["bookmarked"] = "true"

    context = {
        "lang": lang,
        "_": _translator(lang),
        "page_url": _page_url,
        "lang_url": _lang_url,
        # 原来模板只取 i18n 字典，config.yaml 里的 app.name 配了也不会显示
        "app_name": current_app.config.get("APP_NAME") or translate("app_name", lang),
        "app_version": current_app.config.get("APP_VERSION", ""),
        "unread_counts": counts,
        "total_unread": total_unread,
        "auth_enabled": auth_enabled(),
        "current_user": current_user(),
        "export_filters": export_filters,
        "article_count": Article.query.count(),
        "source_names": {source.id: (source.name or source.url) for source in Source.query.all()},
    }
    context.update(extra)
    return context


_UNREAD_CACHE = {"at": 0.0, "value": ({}, 0)}


def _unread_counts(max_age=3.0):
    """每个源的未读数与总未读数（read 为 NULL 也算未读）。

    前端每几秒轮询一次，这里做一个 3 秒的短缓存，避免把统计查询压到每次轮询上。
    """
    import time as _time

    now = _time.time()
    if now - _UNREAD_CACHE["at"] < max_age:
        return _UNREAD_CACHE["value"]
    try:
        rows = (
            db.session.query(Article.source_id, func.count(Article.id))
            .filter(or_(Article.read.is_(False), Article.read.is_(None)))
            .group_by(Article.source_id)
            .all()
        )
    except Exception as exc:  # noqa: BLE001 - 统计失败不应让页面 500
        logging.getLogger(__name__).warning("统计未读数失败: %s", exc)
        return {}, 0
    counts = {source_id: count for source_id, count in rows}
    value = (counts, sum(counts.values()))
    _UNREAD_CACHE.update({"at": now, "value": value})
    return value


def _parse_interval(raw, default=DEFAULT_INTERVAL_MINUTES):
    """容错解析更新间隔，并强制不小于 5 分钟。"""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return max(MIN_INTERVAL_MINUTES, int(default or DEFAULT_INTERVAL_MINUTES))
    return max(MIN_INTERVAL_MINUTES, value)


def _scheduling_enabled():
    return bool(current_app.config.get("SCHEDULER_ENABLED", True))


def _schedule(source):
    """把源注册进调度器；调度器未启用时静默跳过。"""
    if not _scheduling_enabled():
        return
    from app.scheduler import schedule_source

    try:
        schedule_source(source, notify=True, app=current_app._get_current_object())
    except Exception as exc:  # noqa: BLE001 - 调度失败不应让页面 500
        logger.warning("注册调度任务失败 source=%s: %s", source.id, exc)


def _schedule_all():
    if not _scheduling_enabled():
        return
    from app.scheduler import schedule_source

    app = current_app._get_current_object()
    for source in Source.query.all():
        try:
            schedule_source(source, notify=True, app=app)
        except Exception as exc:  # noqa: BLE001
            logger.warning("注册调度任务失败 source=%s: %s", source.id, exc)


def _local_redirect(candidate, fallback):
    """只接受站内相对路径，避免开放重定向。

    同时拒绝控制字符：换行符会被 Werkzeug 判定为响应头注入并抛
    `ValueError: Header values must not contain newline characters`，
    表现为用户提交一个带换行的 next 就让请求 500。
    """
    if (
        candidate
        and candidate.startswith("/")
        and not candidate.startswith("//")
        and not _CONTROL_CHARS.search(candidate)
    ):
        return candidate
    return fallback


def _t(key, **kwargs):
    """按当前界面语言取提示文案。"""
    text = translate(key, _language())
    return text.format(**kwargs) if kwargs else text


def _too_long(field_label_key, value, limit):
    return _t(
        "msg_field_too_long",
        field=_t(field_label_key),
        length=len(value),
        limit=limit,
    ) if value and len(value) > limit else None


def _allow_private():
    return bool(current_app.config.get("SECURITY_ALLOW_PRIVATE_NETWORKS", False))


# --------------------------------------------------------------------------- #
# 登录 / 登出
# --------------------------------------------------------------------------- #
def _login_context(**extra):
    lang = _language()
    context = {
        "lang": lang,
        "_": _translator(lang),
        "app_name": current_app.config.get("APP_NAME") or translate("app_name", lang),
        "app_version": current_app.config.get("APP_VERSION", ""),
    }
    context.update(extra)
    return context


@web_bp.route("/login", methods=["GET", "POST"])
def login():
    if not auth_enabled():
        return redirect(url_for("web.index"))
    if current_user():
        return redirect(_local_redirect(request.args.get("next"), url_for("web.index")))

    error = None
    if request.method == "POST":
        key = client_key()
        locked = throttle.locked_for(key)
        if locked:
            logger.warning("登录尝试被限流: %s", key)
            error = _t("msg_login_locked", seconds=locked)
        else:
            ok, message_key = verify_credentials(
                request.form.get("username"), request.form.get("password")
            )
            if ok:
                throttle.reset(key)
                login_user(request.form.get("username"))
                logger.info("登录成功: %s", request.form.get("username"))
                return redirect(
                    _local_redirect(request.form.get("next"), url_for("web.index"))
                )
            throttle.record_failure(key)
            logger.warning("登录失败: user=%r from %s", request.form.get("username"), key)
            error = _t(message_key)

    return render_template(
        "login.html",
        **_login_context(error=error, next=request.form.get("next") or request.args.get("next", ""))
    )


@web_bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash(_t("msg_logged_out"))
    return redirect(url_for("web.login") if auth_enabled() else url_for("web.index"))


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #
@web_bp.route("/")
def index():
    page = request.args.get("page", 1, type=int) or 1
    articles = (
        Article.query.order_by(Article.published.desc(), Article.id.desc())
        .paginate(page=max(1, page), per_page=PER_PAGE, error_out=False)
    )
    return render_template(
        "index.html",
        **_context(sources=Source.query.order_by(Source.id).all(), articles=articles)
    )


@web_bp.route("/search")
def search():
    query = request.args.get("q", "")
    source_id = request.args.get("source_id", type=int)
    unread = request.args.get("unread", "false").lower() == "true"
    bookmarked = request.args.get("bookmarked", "false").lower() == "true"
    page = request.args.get("page", 1, type=int) or 1
    pagination = search_articles(
        query, source_id, unread, page=page, per_page=PER_PAGE, bookmarked_only=bookmarked
    )
    return render_template(
        "index.html",
        **_context(
            sources=Source.query.order_by(Source.id).all(),
            articles=pagination,
            search_query=query,
            search_source_id=source_id,
            search_unread=unread,
            search_bookmarked=bookmarked,
        )
    )


# --------------------------------------------------------------------------- #
# 收藏与导出
# --------------------------------------------------------------------------- #
@web_bp.route("/article/<int:article_id>/bookmark", methods=["POST"])
def bookmark_article(article_id):
    """切换收藏状态；带 ?ajax=1 时返回 JSON，供页面无刷新更新。"""
    from app.services import ServiceError, toggle_bookmark

    try:
        article = toggle_bookmark(article_id)
    except ServiceError as exc:
        if request.args.get("ajax"):
            return jsonify({"error": _t(exc.message_key)}), 404
        flash(_t(exc.message_key))
        return redirect(_local_redirect() or url_for("web.index"))

    if request.args.get("ajax"):
        return jsonify({"id": article.id, "bookmarked": bool(article.bookmarked)})
    flash(_t("msg_bookmark_on") if article.bookmarked else _t("msg_bookmark_off"))
    return redirect(_local_redirect() or url_for("web.index"))


@web_bp.route("/bookmarks")
def bookmarks():
    """只看收藏。"""
    page = request.args.get("page", 1, type=int) or 1
    pagination = search_articles("", None, False, page=page, per_page=PER_PAGE, bookmarked_only=True)
    return render_template(
        "index.html",
        **_context(
            sources=Source.query.order_by(Source.id).all(),
            articles=pagination,
            search_bookmarked=True,
        )
    )


@web_bp.route("/export/articles.<fmt>")
def export_articles(fmt):
    """导出文章：json / csv / markdown。"""
    from flask import Response

    from app import exporter

    exporters = {
        "json": (exporter.export_articles_json, "application/json; charset=utf-8"),
        "csv": (exporter.export_articles_csv, "text/csv; charset=utf-8"),
        "markdown": (exporter.export_articles_markdown, "text/markdown; charset=utf-8"),
        "md": (exporter.export_articles_markdown, "text/markdown; charset=utf-8"),
    }
    if fmt not in exporters:
        return "unsupported format", 404, {"Content-Type": "text/plain; charset=utf-8"}

    func_, mimetype = exporters[fmt]
    source_id = request.args.get("source_id", type=int)
    unread_only = request.args.get("unread", "false").lower() == "true"
    bookmarked_only = request.args.get("bookmarked", "false").lower() == "true"
    payload = func_(source_id=source_id, unread_only=unread_only, bookmarked_only=bookmarked_only)

    if fmt == "csv":
        # 加 BOM，Excel 在中文 Windows 上打开才不会乱码
        payload = "\ufeff" + payload
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    return Response(
        payload,
        mimetype=mimetype,
        headers={"Content-Disposition": f'attachment; filename="rss-articles-{stamp}.{fmt}"'},
    )


@web_bp.route("/healthz")
def healthz():
    """容器健康检查。

    必须真的探一次数据库：原来直接返回 "ok"，
    数据库或表结构坏掉时容器仍被判为健康，故障不会被重启发现。
    """
    try:
        with db.engine.connect() as conn:
            for table in ("source", "article"):
                conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
    except Exception as exc:  # noqa: BLE001 - 健康检查本身不能抛异常
        logger.warning("健康检查失败: %s", exc)
        return "database unavailable", 503, {"Content-Type": "text/plain; charset=utf-8"}
    # 带上版本号，便于确认线上跑的是哪一版
    version = current_app.config.get("APP_VERSION", "")
    return f"ok {version}".strip(), 200, {"Content-Type": "text/plain; charset=utf-8"}


# --------------------------------------------------------------------------- #
# 任务状态接口（前端轮询用）
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 网页控制台（本地 GUI 的网页版）
# --------------------------------------------------------------------------- #
@web_bp.route("/console")
def console():
    """集中管理页：订阅源一览 + 批量操作 + 任务进展。"""
    counts, total_unread = _unread_counts()
    return render_template(
        "console.html",
        **_context(
            sources=Source.query.order_by(Source.id).all(),
            unread_counts=counts,
            total_unread=total_unread,
            article_count=Article.query.count(),
        )
    )


@web_bp.route("/refresh_all", methods=["POST"])
def refresh_all():
    """一键抓取全部启用中的源（能后台就后台，并发满了则同步）。"""
    from app.services import fetch_all

    started, done = fetch_all(notify=True)
    flash(_t("gui_fetch_all_started"))
    logger.info("全部抓取：后台 %d 个，同步完成 %d 个", started, done)
    return redirect(url_for("web.console"))


@web_bp.route("/api/status")
def api_status():
    """后台任务进展 + 未读数 + 版本号，供页面轮询。

    只读、无副作用；任务表在内存里，多 worker 部署时各自独立。
    """
    from app.models import utcnow

    data = tasks.snapshot()
    _, total_unread = _unread_counts()
    return jsonify(
        {
            "version": current_app.config.get("APP_VERSION", ""),
            "server_time": utcnow().isoformat(sep=" ", timespec="seconds"),
            "running": data["running"],
            "recent": data["recent"],
            "unread_total": total_unread,
            "sources": Source.query.count(),
        }
    )


@web_bp.route("/api/tasks/<task_id>")
def api_task(task_id):
    """查询单个任务的状态。"""
    task = tasks.get(task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    return jsonify(task)


# --------------------------------------------------------------------------- #
# 源管理
# --------------------------------------------------------------------------- #
@web_bp.route("/add_source", methods=["POST"])
def add_source():
    url = (request.form.get("url") or "").strip()
    name = (request.form.get("name") or "").strip()
    category = (request.form.get("category") or "").strip() or "General"

    if not url:
        flash(_t("msg_url_required"))
        return redirect(url_for("web.index"))

    # 超长输入必须明确拒绝：截断会把地址悄悄换成一个「另一个 URL」再去抓取
    if len(url) > MAX_URL_CHARS:
        logger.warning("拒绝超长订阅地址：%d 字符", len(url))
        flash(_t("msg_url_too_long", length=len(url), limit=MAX_URL_CHARS))
        return redirect(url_for("web.index"))
    for label_key, value, limit in (
        ("name", name, MAX_NAME_CHARS),
        ("category", category, MAX_CATEGORY_CHARS),
    ):
        message = _too_long(label_key, value, limit)
        if message:
            flash(message)
            return redirect(url_for("web.index"))

    if not is_safe_url(url, allow_private=_allow_private()):
        logger.warning("拒绝添加被安全策略拦截的地址: %s", url)
        flash(_t("msg_url_blocked"))
        return redirect(url_for("web.index"))

    if Source.query.filter_by(url=url).first():
        flash(_t("msg_feed_exists", url=url))
        return redirect(url_for("web.index"))

    interval = _parse_interval(
        request.form.get("interval"),
        current_app.config.get("SCHEDULER_DEFAULT_INTERVAL", DEFAULT_INTERVAL_MINUTES),
    )
    source = Source(name=name or url, url=url, category=category, interval=interval)
    db.session.add(source)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(_t("msg_feed_exists", url=url))
        return redirect(url_for("web.index"))

    _schedule(source)

    if current_app.config.get("FETCH_INITIAL_ASYNC", True):
        # 首次抓取放后台：全文抽取 + 网络重试最坏能拖住请求几十秒
        if start_background_fetch(source.id, source.name) is not None:
            flash(_t("msg_source_added_fetching"))
            logger.info("源 %s 已添加，首次抓取转入后台", source.id)
            return redirect(url_for("web.index"))
        # 后台任务已满：退回同步抓取，功能仍然可用

    ok, message = fetch_source(source.id, notify=True)
    flash(_t("msg_source_added") if ok else _t("msg_source_added_fetch_failed"))
    if not ok:
        logger.warning("源 %s 首次抓取失败: %s", source.id, message)
    return redirect(url_for("web.index"))


def start_background_fetch(source_id, source_name=None):
    """在后台线程里完成首次抓取（需要自己推应用上下文）。

    并发任务已达上限时返回 None，由调用方回落到同步抓取——
    否则反复点「添加源」就能无限起线程。
    """
    try:
        task_id = tasks.start(
            "initial_fetch",
            source_id=source_id,
            source_name=source_name,
            max_concurrent=current_app.config.get("TASKS_MAX_CONCURRENT"),
        )
    except tasks.TooManyTasks as exc:
        logger.warning("后台任务过多，改为同步抓取 source=%s: %s", source_id, exc)
        return None

    app = current_app._get_current_object()

    def worker():
        with app.app_context():
            try:
                fetch_source(source_id, notify=True, task_id=task_id)
            except Exception as exc:  # noqa: BLE001 - 后台任务不能把异常抛给请求
                logger.error("后台首次抓取失败 source=%s: %s", source_id, exc)
                tasks.finish(task_id, False, 0, str(exc))

    thread = threading.Thread(target=worker, name=f"initial-fetch-{source_id}", daemon=True)
    thread.start()
    return thread


@web_bp.route("/delete_source/<int:source_id>", methods=["POST"])
def delete_source(source_id):
    source = db.get_or_404(Source, source_id)
    if _scheduling_enabled():
        from app.scheduler import unschedule_source

        unschedule_source(source.id)
    label = source.name or source.url
    db.session.delete(source)
    db.session.commit()
    flash(_t("msg_source_deleted", name=label))
    return redirect(url_for("web.index"))


@web_bp.route("/refresh_source/<int:source_id>", methods=["POST"])
def refresh_source(source_id):
    source = db.get_or_404(Source, source_id)
    ok, message = fetch_source(source.id, notify=True)
    if ok:
        flash(_t("msg_refresh_ok"))
    else:
        flash(_t("msg_refresh_failed"))
        logger.warning("手动刷新源 %s 失败: %s", source.id, message)
    return redirect(url_for("web.index"))


@web_bp.route("/edit_source/<int:source_id>", methods=["POST"])
def edit_source(source_id):
    """编辑订阅源：名称、分类、更新间隔（README 与翻译表里一直有 edit，之前没有实现）。"""
    source = db.get_or_404(Source, source_id)
    name = (request.form.get("name") or "").strip()
    category = (request.form.get("category") or "").strip() or "General"

    for label_key, value, limit in (
        ("name", name, MAX_NAME_CHARS),
        ("category", category, MAX_CATEGORY_CHARS),
    ):
        message = _too_long(label_key, value, limit)
        if message:
            flash(message)
            return redirect(url_for("web.index"))

    source.name = name or source.url
    source.category = category
    source.interval = _parse_interval(
        request.form.get("interval"), source.interval or DEFAULT_INTERVAL_MINUTES
    )
    db.session.commit()
    _schedule(source)  # 间隔变了要重新注册任务
    flash(_t("msg_source_updated", name=source.name))
    return redirect(url_for("web.index"))


@web_bp.route("/toggle_source/<int:source_id>", methods=["POST"])
def toggle_source(source_id):
    """暂停 / 恢复某个源（暂停后不再调度，但保留已抓到的文章）。"""
    source = db.get_or_404(Source, source_id)
    source.enabled = not source.enabled
    db.session.commit()
    _schedule(source)  # 暂停 → 移除任务；恢复 → 重新注册
    flash(
        _t("msg_source_resumed" if source.enabled else "msg_source_paused", name=source.name or source.url)
    )
    logger.info("源 %s 已%s", source.id, "恢复" if source.enabled else "暂停")
    return redirect(url_for("web.index"))


@web_bp.route("/article/<int:article_id>/read", methods=["POST"])
def mark_read(article_id):
    article = db.get_or_404(Article, article_id)
    article.read = True
    db.session.commit()
    return redirect(
        _local_redirect(request.form.get("next"), url_for("web.index"))
    )


@web_bp.route("/article/<int:article_id>/unread", methods=["POST"])
def mark_unread(article_id):
    article = db.get_or_404(Article, article_id)
    article.read = False
    db.session.commit()
    return redirect(
        _local_redirect(request.form.get("next"), url_for("web.index"))
    )


# --------------------------------------------------------------------------- #
# OPML
# --------------------------------------------------------------------------- #
@web_bp.route("/export_opml")
def export_opml_route():
    content = export_opml()
    return current_app.response_class(
        content,
        mimetype="text/xml",
        headers={"Content-Disposition": "attachment; filename=feeds.opml"},
    )


@web_bp.route("/import_opml", methods=["POST"])
def import_opml_route():
    upload = request.files.get("opml_file")
    if upload is None or not upload.filename:
        flash(_t("msg_opml_no_file"))
        return redirect(url_for("web.index"))

    raw = upload.read(MAX_OPML_BYTES + 1)
    if len(raw) > MAX_OPML_BYTES:
        flash(_t("msg_opml_too_large", limit=MAX_OPML_BYTES // (1024 * 1024)))
        return redirect(url_for("web.index"))

    success, failed = import_opml(raw, allow_private=_allow_private())
    if success:
        _schedule_all()  # 新导入的源也要进入调度
    flash(_t("msg_opml_result", success=success, failed=failed))
    return redirect(url_for("web.index"))


@web_bp.errorhandler(403)
def forbidden(error):  # pragma: no cover - 仅提供可读提示
    logger.warning("返回 403: %s", error)
    return "403 Forbidden: cross-site request blocked", 403


@web_bp.errorhandler(413)
def payload_too_large(error):  # pragma: no cover - 仅提供可读提示
    limit = current_app.config.get("MAX_CONTENT_LENGTH", 0)
    return f"413 Payload Too Large: request body exceeds {limit} bytes", 413
