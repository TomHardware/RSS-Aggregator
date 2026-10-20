"""后台调度。

要点：
1. 任务必须自己推应用上下文。`schedule_all` 的 app_context 只在「注册任务」时生效；
   任务真正在调度线程执行时做 `Source.query` 会抛
   `RuntimeError: Working outside of application context`，定时抓取 100% 失败。
2. 调度器实例放在本模块，不要放在 `app/__init__.py`：
   否则 `import app.scheduler` 会把包属性 `app.scheduler` 覆盖成模块对象，
   之后 `from app import scheduler` 拿到的是模块而不是调度器实例。
3. `init_scheduler` 尊重 `scheduler.enabled`，并在调试重载器父进程里不启动
   （否则重载器下会起两个调度器重复抓取）。多 worker 时可用
   `RSS_AGGREGATOR_SCHEDULER=off` 只保留一个进程跑调度。
4. 同一源限制单实例运行（max_instances=1）并合并错过的执行，避免任务堆积。
5. v2.0.1：只调度 `enabled` 的源；`app.timezone` 真正传给调度器（原来配了不生效）。
"""
import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.fetcher import fetch_source
from app.models import Source

logger = logging.getLogger(__name__)

MIN_INTERVAL_MINUTES = 5

scheduler = BackgroundScheduler()


def run_fetch(app, source_id, notify=False):
    """调度任务入口：在调度线程里补上应用上下文后再抓取。

    同样登记到任务表，这样前端轮询也能看到定时抓取的进展。
    """
    from app.tasks import TooManyTasks, start

    with app.app_context():
        source = Source.query.get(source_id)
        if source is not None and not source.enabled:
            logger.info("source=%s 已暂停，跳过本次抓取", source_id)
            return False, "Source paused"

        try:
            task_id = start(
                "scheduled_fetch",
                source_id=source_id,
                source_name=source.name if source else None,
                max_concurrent=app.config.get("TASKS_MAX_CONCURRENT"),
            )
        except TooManyTasks as exc:
            logger.warning("后台任务过多，本次定时抓取跳过: %s", exc)
            return False, "Too many concurrent tasks"

        return fetch_source(source_id, notify, task_id=task_id)


def schedule_source(source, notify=True, app=None):
    """为一个源注册（或替换）周期抓取任务；源被暂停时改为移除任务。"""
    app = app or getattr(scheduler, "app", None)
    if app is None:
        raise RuntimeError("调度器尚未绑定 Flask app，请先调用 init_scheduler(app)")

    if not getattr(source, "enabled", True):
        unschedule_source(source.id)
        return None

    interval = max(MIN_INTERVAL_MINUTES, int(source.interval or 30))
    job_id = f"source_{source.id}"
    scheduler.add_job(
        run_fetch,
        trigger=IntervalTrigger(minutes=interval),
        id=job_id,
        args=[app, source.id, notify],
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    logger.info(
        "已注册抓取任务 source=%s 间隔=%d 分钟 notify=%s", source.id, interval, notify
    )
    return job_id


def unschedule_source(source_id):
    """删除源或暂停源时移除对应任务。"""
    job_id = f"source_{source_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logger.info("已移除抓取任务 source=%s", source_id)


def schedule_all(app, notify=True):
    """按数据库当前内容注册全部任务（幂等，已存在的会被替换）。"""
    with app.app_context():
        sources = Source.query.all()
    scheduled = 0
    for source in sources:
        if schedule_source(source, notify=notify, app=app):
            scheduled += 1
    return scheduled


def scheduler_allowed(app):
    """是否应该在本进程运行调度器。

    多进程部署（gunicorn -w N）时每个 worker 都会导入本模块，若各自启动一个
    调度器就会重复抓取、重复发信。这里提供逐进程开关：
    除 web worker 外的那个进程设 `RSS_AGGREGATOR_SCHEDULER=off` 即可。
    """
    override = os.environ.get("RSS_AGGREGATOR_SCHEDULER", "").strip().lower()
    if override in {"0", "off", "false", "no", "disable", "disabled"}:
        logger.info("环境变量 RSS_AGGREGATOR_SCHEDULER=%s，本进程不启动调度器", override)
        return False

    if not app.config.get("SCHEDULER_ENABLED", True):
        return False

    # 调试重载器会派生父/子两个进程，只在真正干活的子进程里跑。
    # 注意 `flask run --debug` 时 app.debug 还没生效（引导逻辑在导入期执行），
    # 但 --debug 已经把 FLASK_DEBUG 写进环境变量，所以要一并判断。
    debug = bool(
        app.debug
        or app.config.get("DEBUG")
        or app.config.get("SERVER_DEBUG")
        or os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true"}
    )
    if debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return False
    return True


def apply_timezone(app):
    """把 app.timezone 真正应用到调度器上（原来只读进配置、从未使用）。"""
    timezone = (app.config.get("APP_TIMEZONE") or "").strip()
    if not timezone:
        return False
    try:
        scheduler.configure(timezone=timezone)
    except Exception as exc:  # noqa: BLE001 - 时区写错不该阻断启动
        logger.warning("调度器时区 %r 无效，改用系统时区：%s", timezone, exc)
        return False
    logger.info("调度器时区已设为 %s", timezone)
    return True


def init_scheduler(app):
    """幂等启动调度器；返回是否启动了调度。"""
    scheduler.app = app

    if not scheduler_allowed(app):
        logger.info("调度器未启动（配置已关闭，或当前是调试重载器父进程）")
        return False

    if not scheduler.running:
        apply_timezone(app)
        scheduler.start()
    count = schedule_all(app)
    logger.info("调度器已启动，共注册 %d 个源", count)
    return True
