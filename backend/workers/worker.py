"""
Background worker loop — polls the job queue and executes training jobs
in a thread pool so they don't block the FastAPI event loop.
"""

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from croniter import croniter

from backend.config import settings
from backend.db.connection import execute, query
from backend.training import queue as job_queue
from backend.training.job_service import create_job, get_job, mark_running
from backend.workers.runner import run_training_job

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(
    max_workers=settings.max_concurrent_jobs,
    thread_name_prefix="forecast-worker",
)
_running_jobs: set[str] = set()
_WORKER_ID = "worker-1"


def _execute(tenant_id: str, session_id: str, job_id: str) -> None:
    _running_jobs.add(job_id)
    try:
        run_training_job(tenant_id, session_id, job_id)
    except Exception as e:
        log.error(f"Unhandled exception in job {job_id}: {e}", exc_info=True)
    finally:
        _running_jobs.discard(job_id)


async def _loop() -> None:
    log.info(f"Worker {_WORKER_ID} started (max_concurrent={settings.max_concurrent_jobs})")
    consecutive_errors = 0
    while True:
        try:
            if len(_running_jobs) < settings.max_concurrent_jobs:
                item = job_queue.dequeue()
                if item:
                    job_id = item["job_id"]
                    tenant_id = item["tenant_id"]
                    job = get_job(tenant_id, job_id)
                    if job and job["status"] == "QUEUED":
                        mark_running(tenant_id, job_id, _WORKER_ID)
                        session_id = job["session_id"]
                        log.info(f"Dispatching job={job_id} session={session_id}")
                        _executor.submit(_execute, tenant_id, session_id, job_id)
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            backoff = min(consecutive_errors * settings.worker_poll_interval_seconds, 30)
            log.error(f"Worker loop error (attempt {consecutive_errors}): {e}", exc_info=True)
            await asyncio.sleep(backoff)
            continue
        await asyncio.sleep(settings.worker_poll_interval_seconds)


_SCHEDULER_POLL_SECONDS = 60


def _scheduler_loop() -> None:
    log.info("Scheduler loop started")
    while True:
        try:
            now = datetime.now(timezone.utc)
            due = query(
                "SELECT id, tenant_id, session_id, cron_expr FROM scheduled_jobs "
                "WHERE enabled = true AND next_run <= %s",
                (now,),
            )
            for job in due:
                sched_id   = job["id"]
                tenant_id  = job["tenant_id"]
                session_id = job["session_id"]
                cron_expr  = job["cron_expr"]
                try:
                    create_job(tenant_id, session_id, created_by="scheduler")
                    nxt = croniter(cron_expr, now).get_next(datetime)
                    execute(
                        "UPDATE scheduled_jobs SET next_run = %s WHERE id = %s",
                        (nxt, sched_id),
                    )
                    log.info(f"Scheduled job triggered: session={session_id} next={nxt.isoformat()}")
                except Exception as e:
                    log.error(f"Failed to trigger scheduled job {sched_id}: {e}", exc_info=True)
        except Exception as e:
            log.error(f"Scheduler loop error: {e}", exc_info=True)
        time.sleep(_SCHEDULER_POLL_SECONDS)


def _inventory_alert_loop() -> None:
    """Fires inventory stockout alerts, then supplier lead-time deviation
    alerts (feature 3.3), daily at 8:00 AM UTC. The two run independently:
    a supplier drifting late matters most while stock still looks healthy,
    which is exactly when the stockout digest sends nothing."""
    log.info("Inventory alert scheduler started")
    while True:
        try:
            now = datetime.now(timezone.utc)
            next_run = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if now >= next_run:
                next_run = next_run.replace(day=next_run.day + 1)
            sleep_secs = (next_run - now).total_seconds()
            log.info("Inventory alert: next run at %s UTC (%.0f s)", next_run.isoformat(), sleep_secs)
            time.sleep(max(sleep_secs, 1))
        except Exception:
            time.sleep(3600)
            continue
        try:
            from backend.inventory.service import run_daily_inventory_alerts
            run_daily_inventory_alerts()
        except Exception as e:
            log.error("Inventory alert error: %s", e, exc_info=True)
        try:
            from backend.inventory.supplier_health_service import (
                run_daily_supplier_lead_time_alerts,
            )
            run_daily_supplier_lead_time_alerts()
        except Exception as e:
            log.error("Supplier lead-time alert error: %s", e, exc_info=True)


def _next_month_start(now: datetime) -> datetime:
    """Returns the next day-1 00:05 UTC boundary strictly after `now`."""
    candidate = now.replace(day=1, hour=0, minute=5, second=0, microsecond=0)
    if candidate <= now:
        if candidate.month == 12:
            candidate = candidate.replace(year=candidate.year + 1, month=1)
        else:
            candidate = candidate.replace(month=candidate.month + 1)
    return candidate


def _monthly_overstock_snapshot_loop() -> None:
    """Monthly pass on the 1st: snapshot each tenant's SOBRESTOCK value, then
    mail the previous month's recap (feature 3.2).

    Order is load-bearing: the snapshot taken now is the closing measurement of
    the month that just ended, so the recap's capital-freed figure only exists
    once it has been written."""
    log.info("Monthly overstock snapshot scheduler started")
    while True:
        try:
            now = datetime.now(timezone.utc)
            next_run = _next_month_start(now)
            sleep_secs = (next_run - now).total_seconds()
            log.info("Overstock snapshot: next run at %s UTC (%.0f s)", next_run.isoformat(), sleep_secs)
            time.sleep(max(sleep_secs, 1))
        except Exception:
            time.sleep(3600)
            continue
        try:
            from backend.inventory.service import run_monthly_overstock_snapshot
            run_monthly_overstock_snapshot()
        except Exception as e:
            log.error("Overstock snapshot error: %s", e, exc_info=True)
        try:
            from backend.inventory.roi_service import run_monthly_roi_emails
            sent = run_monthly_roi_emails()
            log.info("Monthly ROI recap: mailed %d tenants", sent)
        except Exception as e:
            log.error("Monthly ROI recap error: %s", e, exc_info=True)


def start() -> threading.Thread:
    """Start the worker loop, job scheduler, inventory alert scheduler, and monthly overstock snapshot scheduler."""
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_loop())
        except Exception as e:
            log.critical(f"Worker loop terminated unexpectedly: {e}", exc_info=True)

    worker_thread = threading.Thread(target=_run, daemon=True, name="job-worker")
    worker_thread.start()

    scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name="job-scheduler")
    scheduler_thread.start()

    alert_thread = threading.Thread(target=_inventory_alert_loop, daemon=True, name="inventory-alerts")
    alert_thread.start()

    overstock_thread = threading.Thread(
        target=_monthly_overstock_snapshot_loop, daemon=True, name="overstock-snapshot",
    )
    overstock_thread.start()

    return worker_thread
