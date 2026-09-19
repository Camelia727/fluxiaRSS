"""定时任务：每天凌晨自动采集（cron 版 run_pipeline）。

用 APScheduler 的 BackgroundScheduler 在 uvicorn 进程内挂一个每日任务；
结果存内存供 /health 展示。采集失败不抛出，记进 last_collection。
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from . import config
from .pipeline import run_pipeline

_last: dict | None = None
_lock = threading.Lock()
_scheduler: BackgroundScheduler | None = None


def _job() -> None:
    global _last
    results = {}
    try:
        from .db import list_zones
        zones = list_zones()
        for z in zones:
            zid = z["id"]
            try:
                results[zid] = run_pipeline(zone=zid)
            except Exception as exc:
                results[zid] = {"error": str(exc)}
    except Exception as exc:
        results["_error"] = str(exc)
    with _lock:
        _last = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "result": results,
        }


def last_collection() -> dict | None:
    with _lock:
        return _last


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _job,
        trigger="cron",
        hour=config.COLLECT_HOUR,
        minute=config.COLLECT_MINUTE,
        id="daily_collect",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
