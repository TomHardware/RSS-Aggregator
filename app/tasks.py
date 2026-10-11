"""后台任务状态登记表。

抓取是异步的（首次抓取在后台线程、定时抓取在调度线程），用户看不到任何进展。
这里维护一份进程内的任务表，供 `/api/status` 与前端轮询使用：

* `start()` 登记一个正在运行的任务，超过并发上限时抛 `TooManyTasks`；
* `finish()` 记录结果（成功/失败、新增文章数）；
* `snapshot()` 返回「正在运行」与「最近完成」两组，供接口序列化。

内存实现，进程级：多 worker 部署时每个进程各自一份，
前端只会看到自己请求到的那个 worker 的任务。要跨进程需要引入 Redis 之类，
与本项目「单进程自托管」的定位不符，故不引入。
"""
import logging
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MAX_TASKS = 50            # 最多保留多少条历史
TASK_TTL_SECONDS = 3600   # 完成的任务保留多久
TASK_STALE_SECONDS = 1800  # 运行中的任务超过这个时长视为失效（线程意外终止等）
DEFAULT_MAX_CONCURRENT = 5

_lock = threading.RLock()
_tasks = OrderedDict()  # id -> task dict（含内部用的 *_ts 字段）


class TooManyTasks(RuntimeError):
    """同时运行的后台任务已达上限。"""


def _now():
    return time.time()


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")


def _prune_locked():
    cutoff = _now() - TASK_TTL_SECONDS
    for task_id in list(_tasks):
        task = _tasks[task_id]
        if task["state"] == "running":
            # 运行中的任务不会自己消失：线程意外终止时如果一直挂着，
            # 会永久占用一个并发额度，所以超过阈值就标记为失败并放行。
            if _now() - task["started_at_ts"] > TASK_STALE_SECONDS:
                logger.warning(
                    "任务 %s（%s）运行超过 %d 秒，判定为失效",
                    task_id, task["kind"], TASK_STALE_SECONDS,
                )
                task["state"] = "failed"
                task["message"] = "stale task (worker terminated?)"
                task["finished_at"] = _iso(_now())
                task["finished_at_ts"] = _now()
        elif (task.get("finished_at_ts") or 0) < cutoff:
            del _tasks[task_id]
    while len(_tasks) > MAX_TASKS:
        _tasks.popitem(last=False)


def _running_locked():
    return [task for task in _tasks.values() if task["state"] == "running"]


def public(task):
    """剥掉内部字段，得到可以序列化给前端的结构。"""
    return {
        "id": task["id"],
        "kind": task["kind"],
        "source_id": task["source_id"],
        "source_name": task["source_name"],
        "state": task["state"],
        "started_at": task["started_at"],
        "finished_at": task["finished_at"],
        "new_articles": task["new_articles"],
        "message": task["message"],
    }


def start(kind, source_id=None, source_name=None, max_concurrent=None):
    """登记一个开始运行的任务，返回任务 id。

    超过并发上限时抛 `TooManyTasks`：否则反复点「添加源」就能无限起线程。
    """
    limit = DEFAULT_MAX_CONCURRENT if max_concurrent is None else int(max_concurrent)
    with _lock:
        _prune_locked()
        running = len(_running_locked())
        if limit > 0 and running >= limit:
            raise TooManyTasks(f"同时运行的后台任务已达上限 {limit}")
        task_id = uuid.uuid4().hex[:12]
        _tasks[task_id] = {
            "id": task_id,
            "kind": kind,
            "source_id": source_id,
            "source_name": source_name,
            "state": "running",
            "started_at": _iso(_now()),
            "started_at_ts": _now(),
            "finished_at": None,
            "finished_at_ts": None,
            "new_articles": 0,
            "message": "",
        }
    return task_id


def finish(task_id, ok, new_articles=0, message=""):
    """记录任务结果。未知 id 直接忽略（例如任务表已被裁剪）。"""
    if not task_id:
        return
    with _lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        task["state"] = "success" if ok else "failed"
        task["finished_at"] = _iso(_now())
        task["finished_at_ts"] = _now()
        task["new_articles"] = int(new_articles or 0)
        task["message"] = (message or "")[:200]


def get(task_id):
    with _lock:
        _prune_locked()
        task = _tasks.get(task_id)
        return public(task) if task else None


def snapshot(limit=10):
    """返回 {"running": [...], "recent": [...]}（recent 为最近的在前）。"""
    with _lock:
        _prune_locked()
        running = [public(task) for task in _running_locked()]
        finished = [public(task) for task in _tasks.values() if task["state"] != "running"]
    finished.reverse()
    return {"running": running, "recent": finished[:limit]}


def running_count():
    with _lock:
        _prune_locked()
        return len(_running_locked())


def reset():
    """清空任务表（测试用）。"""
    with _lock:
        _tasks.clear()
