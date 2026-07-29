"""
Background worker loop — polls the job queue and executes training jobs
in a thread pool so they don't block the FastAPI event loop.
"""

import asyncio
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from croniter import croniter

from backend.config import settings
from backend.db.connection import execute, query, query_one
from backend.training import queue as job_queue
from backend.training.job_service import create_job
from backend.workers.runner import run_training_job

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(
    max_workers=settings.max_concurrent_jobs,
    thread_name_prefix="forecast-worker",
)
_running_jobs: set[str] = set()


def worker_id() -> str:
    """This instance's identity for job claiming and orphan recovery.

    WORKER_ID env when set (give long-lived workers a fixed one, so recovery
    still recognizes their orphans after the container is recreated);
    otherwise the host/container name.
    """
    return settings.worker_id.strip() or socket.gethostname()


def recover_orphaned_jobs() -> int:
    """Fail RUNNING jobs that THIS worker abandoned in a crash/restart.

    Runs at worker startup, not API startup: in a split deployment an API
    redeploy must not kill jobs a healthy worker is actively training. Scoped
    to this instance's worker_id — plus legacy NULL rows claimed before ids
    were recorded — so one worker's restart never fails a sibling's live job.

    Returns the number of jobs recovered.
    """
    from backend.sessions.service import force_status

    stuck = query(
        "SELECT id, tenant_id, session_id FROM jobs "
        "WHERE status = 'RUNNING' AND (worker_id = %s OR worker_id IS NULL)",
        (worker_id(),),
    )
    for job in stuck:
        execute(
            "UPDATE jobs SET status = 'FAILED', completed_at = NOW(), error = %s WHERE id = %s",
            ("Worker restarted — job aborted", job["id"]),
        )
        try:
            force_status(job["tenant_id"], job["session_id"], "FAILED")
        except Exception:
            pass
        log.warning(f"Recovered stuck job {job['id']} for session {job['session_id']} → FAILED")

    if stuck:
        log.info(f"Recovered {len(stuck)} stuck RUNNING job(s) on worker startup")
    return len(stuck)


def _tenant_at_concurrent_job_limit(tenant_id: str) -> bool:
    """True when the tenant already has as many RUNNING jobs as its plan (or
    per-tenant quota override) allows — its next QUEUED job must wait, while
    other tenants' jobs keep flowing. None means unlimited. Testing mode
    disables the check, mirroring entitlements.service.enforce_limit. The
    process-wide settings.max_concurrent_jobs cap still applies on top (the
    dequeue loop only runs below it)."""
    if settings.testing_mode:
        return False
    from backend.entitlements.service import tenant_limits
    from backend.tenants.service import get_tenant
    tenant = get_tenant(tenant_id)
    if not tenant:
        return False
    max_jobs = tenant_limits(tenant)["max_concurrent_jobs"]
    if max_jobs is None:
        return False
    row = query_one(
        "SELECT COUNT(*) AS cnt FROM jobs WHERE tenant_id = %s AND status = 'RUNNING'",
        (tenant_id,),
    )
    running = row["cnt"] if row else 0
    return running >= max_jobs


def _execute(tenant_id: str, session_id: str, job_id: str) -> None:
    _running_jobs.add(job_id)
    try:
        run_training_job(tenant_id, session_id, job_id)
    except Exception as e:
        log.error(f"Unhandled exception in job {job_id}: {e}", exc_info=True)
    finally:
        _running_jobs.discard(job_id)


async def _loop() -> None:
    log.info(f"Worker {worker_id()} started (max_concurrent={settings.max_concurrent_jobs})")
    consecutive_errors = 0
    while True:
        try:
            if len(_running_jobs) < settings.max_concurrent_jobs:
                # One statement takes the job and marks it RUNNING. The old
                # dequeue -> get_job -> mark_running sequence let two workers
                # both see the same QUEUED row and both dispatch it.
                item = job_queue.claim(
                    worker_id(), is_tenant_blocked=_tenant_at_concurrent_job_limit)
                if item:
                    job_id = item["job_id"]
                    session_id = item["session_id"]
                    log.info(f"Dispatching job={job_id} session={session_id}")
                    _executor.submit(_execute, item["tenant_id"], session_id, job_id)
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            backoff = min(consecutive_errors * settings.worker_poll_interval_seconds, 30)
            log.error(f"Worker loop error (attempt {consecutive_errors}): {e}", exc_info=True)
            await asyncio.sleep(backoff)
            continue
        await asyncio.sleep(settings.worker_poll_interval_seconds)


_SCHEDULER_POLL_SECONDS = 60
_SCHEDULER_ERROR_MAX_CHARS = 500
_SCHEDULER_RETRY_AFTER_SECONDS = 3600


def _next_cron_run(cron_expr: str, now: datetime) -> datetime:
    """Next occurrence of `cron_expr` strictly after `now`.

    Falls back to a fixed retry delay when the expression itself is
    unparseable — an invalid cron must not leave `next_run` in the past, which
    would turn the scheduler poll into a hot loop retrying every 60 s forever.
    """
    try:
        return croniter(cron_expr, now).get_next(datetime)
    except Exception:
        return now + timedelta(seconds=_SCHEDULER_RETRY_AFTER_SECONDS)


def _record_schedule_failure(sched_id: str, cron_expr: str, now: datetime, error: str) -> None:
    """Persist why a scheduled trigger failed and move `next_run` forward.

    Mirrors `integration_connections.last_error`: without a stored error a
    schedule that has been failing for weeks is indistinguishable from a
    healthy one, because the only trace was a log line. Advancing `next_run`
    is part of the same fix — a failed trigger used to leave `next_run` in the
    past, so the scheduler re-attempted (and re-failed) every poll.
    """
    try:
        execute(
            "UPDATE scheduled_jobs SET last_error = %s, last_error_at = NOW(), next_run = %s "
            "WHERE id = %s",
            (error[:_SCHEDULER_ERROR_MAX_CHARS], _next_cron_run(cron_expr, now), sched_id),
        )
    except Exception as e:
        log.error("Could not record failure for scheduled job %s: %s", sched_id, e, exc_info=True)


def _run_due_scheduled_jobs(now: datetime) -> int:
    """Trigger every enabled schedule whose `next_run` has passed.

    Returns the number of schedules successfully triggered. Extracted from the
    loop so it can be exercised directly by tests without sleeping.
    """
    due = query(
        "SELECT id, tenant_id, session_id, cron_expr FROM scheduled_jobs "
        "WHERE enabled = true AND next_run <= %s",
        (now,),
    )
    triggered = 0
    for job in due:
        sched_id   = job["id"]
        tenant_id  = job["tenant_id"]
        session_id = job["session_id"]
        cron_expr  = job["cron_expr"]
        try:
            create_job(tenant_id, session_id, created_by="scheduler")
            nxt = _next_cron_run(cron_expr, now)
            execute(
                "UPDATE scheduled_jobs SET next_run = %s, last_run = %s, "
                "last_error = NULL, last_error_at = NULL WHERE id = %s",
                (nxt, now, sched_id),
            )
            triggered += 1
            log.info(f"Scheduled job triggered: session={session_id} next={nxt.isoformat()}")
        except Exception as e:
            log.error(f"Failed to trigger scheduled job {sched_id}: {e}", exc_info=True)
            _record_schedule_failure(sched_id, cron_expr, now, str(e))
    return triggered


def _scheduler_loop() -> None:
    log.info("Scheduler loop started")
    while True:
        try:
            _run_due_scheduled_jobs(datetime.now(timezone.utc))
        except Exception as e:
            log.error(f"Scheduler loop error: {e}", exc_info=True)
        time.sleep(_SCHEDULER_POLL_SECONDS)


_DAILY_LOOP_RETRY_SECONDS = 3600


def _next_daily_run(now: datetime, hour: int) -> datetime:
    """Next `hour`:00:00 UTC boundary strictly after `now`.

    Must be date arithmetic, not `replace(day=day + 1)`: that raised
    "day is out of range for month" on the 31st, on Feb 28/29 and on the last
    day of every 30-day month, killing the daily scheduler for the whole day.
    """
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _inventory_alert_loop() -> None:
    """Fires inventory stockout alerts, then supplier lead-time deviation
    alerts (feature 3.3), then data-freshness reminders, daily at 8:00 AM UTC.
    The three run independently: a supplier drifting late matters most while
    stock still looks healthy, which is exactly when the stockout digest sends
    nothing — and a tenant whose file is two months old produces no stockout
    digest at all, because the semáforo it would be built from is blind."""
    log.info("Inventory alert scheduler started")
    while True:
        try:
            now = datetime.now(timezone.utc)
            next_run = _next_daily_run(now, 8)
            sleep_secs = (next_run - now).total_seconds()
            log.info("Inventory alert: next run at %s UTC (%.0f s)", next_run.isoformat(), sleep_secs)
            time.sleep(max(sleep_secs, 1))
        except Exception as e:
            # Never swallow silently: this branch used to hide the month-end
            # crash above, so a whole day without alerts left no trace at all.
            log.error(
                "Inventory alert scheduler error — retrying in %d s: %s",
                _DAILY_LOOP_RETRY_SECONDS, e, exc_info=True,
            )
            time.sleep(_DAILY_LOOP_RETRY_SECONDS)
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
        try:
            # Last of the three on purpose: a tenant with real stockouts gets
            # the actionable digest first, and this only adds why their numbers
            # may not be trustworthy. It is also the only one of the three that
            # still fires when the tenant has stopped opening the app.
            from backend.notifications.freshness_service import (
                run_daily_freshness_reminders,
            )
            run_daily_freshness_reminders()
        except Exception as e:
            log.error("Data-freshness reminder error: %s", e, exc_info=True)


def _integration_sync_loop() -> None:
    """Daily accounting-integrations sync, at 6:00 AM UTC — before the
    8:00 AM inventory alert loop, so a freshly synced stock/sales dataset
    feeds the same day's stockout digest instead of the previous day's."""
    log.info("Integration sync scheduler started")
    while True:
        try:
            now = datetime.now(timezone.utc)
            next_run = _next_daily_run(now, 6)
            sleep_secs = (next_run - now).total_seconds()
            log.info("Integration sync: next run at %s UTC (%.0f s)", next_run.isoformat(), sleep_secs)
            time.sleep(max(sleep_secs, 1))
        except Exception as e:
            log.error(
                "Integration sync scheduler error — retrying in %d s: %s",
                _DAILY_LOOP_RETRY_SECONDS, e, exc_info=True,
            )
            time.sleep(_DAILY_LOOP_RETRY_SECONDS)
            continue
        try:
            from backend.integrations.crypto import integrations_enabled
            if integrations_enabled():
                from backend.integrations.sync_service import run_daily_integration_syncs
                run_daily_integration_syncs()
            else:
                log.info("Integration sync skipped: INTEGRATIONS_SECRET_KEY not configured")
        except Exception as e:
            log.error("Integration sync error: %s", e, exc_info=True)


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
        except Exception as e:
            log.error(
                "Overstock snapshot scheduler error — retrying in %d s: %s",
                _DAILY_LOOP_RETRY_SECONDS, e, exc_info=True,
            )
            time.sleep(_DAILY_LOOP_RETRY_SECONDS)
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


def enabled_components() -> list[str]:
    """Thread names start() will launch under the current settings.

    The job-claim loop runs when worker_enabled; the cron loops (scheduled
    jobs, daily alerts, monthly snapshot, integration sync) when
    scheduler_enabled — they are split so a scaled-out deployment can run
    many claim loops but exactly one scheduler.
    """
    components: list[str] = []
    if settings.worker_enabled:
        components.append("job-worker")
    if settings.scheduler_enabled:
        components += [
            "job-scheduler", "inventory-alerts",
            "overstock-snapshot", "integration-sync",
        ]
    return components


_COMPONENT_TARGETS = {
    "job-scheduler":      _scheduler_loop,
    "inventory-alerts":   _inventory_alert_loop,
    "overstock-snapshot": _monthly_overstock_snapshot_loop,
    "integration-sync":   _integration_sync_loop,
}


def start() -> list[threading.Thread]:
    """Start the components this instance is configured to run.

    With worker_enabled: the job-claim loop, preceded by recovery of this
    instance's orphaned RUNNING jobs. With scheduler_enabled: the cron loops.
    Both default on (single-process dev); a split deployment turns them off in
    the API and on in the dedicated worker.
    """
    def _run():
        try:
            recover_orphaned_jobs()
        except Exception as e:
            log.error(f"Orphan recovery failed (continuing): {e}", exc_info=True)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_loop())
        except Exception as e:
            log.critical(f"Worker loop terminated unexpectedly: {e}", exc_info=True)

    threads: list[threading.Thread] = []
    for name in enabled_components():
        target = _run if name == "job-worker" else _COMPONENT_TARGETS[name]
        t = threading.Thread(target=target, daemon=True, name=name)
        t.start()
        threads.append(t)

    if threads:
        log.info(f"Started components: {[t.name for t in threads]} (id={worker_id()})")
    else:
        log.info("No worker components enabled in this instance")
    return threads
