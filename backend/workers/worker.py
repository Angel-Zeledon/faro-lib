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
from backend.workers import loop_state
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
        # The job row is FAILED at this point either way. If the SESSION cannot
        # follow, the user is left with a session that says it is still training
        # and a worker that will never touch it again — and the line below used
        # to claim "Recovered" regardless, so the log agreed with the screen and
        # both were wrong. Same shape as the cancel path in api/v1/training.py.
        try:
            force_status(job["tenant_id"], job["session_id"], "FAILED")
            log.warning("Recovered stuck job %s for session %s → FAILED",
                        job["id"], job["session_id"])
        except Exception as exc:
            log.error(
                "Stuck job %s marked FAILED but session %s could NOT be moved off "
                "RUNNING — it will look like it is still training: %s",
                job["id"], job["session_id"], exc,
            )

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
            loop_state.beat(worker_id())
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


def _next_cron_run(cron_expr: str, now: datetime,
                   tenant_id: str | None = None) -> datetime:
    """Next occurrence of `cron_expr` strictly after `now`, in the tenant's clock.

    The RESULT is a UTC instant either way — `next_run` is compared against
    `now()` and must stay comparable. What the timezone decides is which wall
    clock "0 6 * * 1" refers to: the frequency picker calls it "cada lunes a las
    6am", so it has to mean 6am where the company is. Rescheduling here in UTC
    while the API computed the first run in local time would have quietly undone
    the fix on the very first firing.

    Falls back to a fixed retry delay when the expression itself is
    unparseable — an invalid cron must not leave `next_run` in the past, which
    would turn the scheduler poll into a hot loop retrying every 60 s forever.
    """
    try:
        base = now
        if tenant_id:
            from backend.api.v1.timezone import zoneinfo_of
            base = now.astimezone(zoneinfo_of(tenant_id))
        nxt = croniter(cron_expr, base).get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=base.tzinfo)
        return nxt.astimezone(timezone.utc)
    except Exception:
        return now + timedelta(seconds=_SCHEDULER_RETRY_AFTER_SECONDS)


def _record_schedule_failure(sched_id: str, cron_expr: str, now: datetime, error: str,
                             tenant_id: str | None = None) -> None:
    """Persist why a scheduled trigger failed and move `next_run` forward.

    Without a stored error a schedule that has been failing for weeks is
    indistinguishable from a healthy one, because the only trace was a log line. Advancing `next_run`
    is part of the same fix — a failed trigger used to leave `next_run` in the
    past, so the scheduler re-attempted (and re-failed) every poll.
    """
    try:
        execute(
            "UPDATE scheduled_jobs SET last_error = %s, last_error_at = NOW(), next_run = %s "
            "WHERE id = %s",
            (error[:_SCHEDULER_ERROR_MAX_CHARS],
             _next_cron_run(cron_expr, now, tenant_id), sched_id),
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
            # NOT `create_job(tenant_id, session_id)`. That trained the session
            # the whole app was reading, validated nothing, and had no dedupe —
            # an engine error at 3 a.m. marked the serving session FAILED and
            # /hoy, the semáforo and the digest went quiet (stability 11.6).
            # Each run now trains a NEW session built from this one, and only
            # replaces what the buyer reads once it succeeds.
            from backend.sessions import retrain_service
            retrain_service.launch_scheduled_retrain(tenant_id, sched_id, session_id)
            nxt = _next_cron_run(cron_expr, now, tenant_id)
            execute(
                "UPDATE scheduled_jobs SET next_run = %s, last_run = %s, "
                "last_error = NULL, last_error_at = NULL WHERE id = %s",
                (nxt, now, sched_id),
            )
            triggered += 1
            log.info(f"Scheduled job triggered: session={session_id} next={nxt.isoformat()}")
        except Exception as e:
            log.error(f"Failed to trigger scheduled job {sched_id}: {e}", exc_info=True)
            _record_schedule_failure(sched_id, cron_expr, now, str(e), tenant_id)
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


def _previous_daily_run(now: datetime, hour: int) -> datetime:
    """The most recent `hour`:00:00 UTC boundary at or before `now`.

    The mirror of `_next_daily_run`, and the half that was missing: without a
    way to name the boundary that has already passed, a loop could not tell
    "we ran the 08:00 pass" from "we were not alive at 08:00".
    """
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate > now:
        candidate -= timedelta(days=1)
    return candidate


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
            # Did we miss today's pass? A restart at 08:02 used to ask for the
            # next boundary after now and sleep until tomorrow (11.28).
            caught_up = loop_state.missed_boundary(
                loop_state.INVENTORY_ALERTS, _previous_daily_run(now, 8), now,
                loop_state.DAILY_CATCHUP,
            )
            if caught_up is not None:
                log.warning("Inventory alert: catching up the %s pass",
                            caught_up.isoformat())
                boundary = caught_up
            else:
                next_run = _next_daily_run(now, 8)
                sleep_secs = (next_run - now).total_seconds()
                log.info("Inventory alert: next run at %s UTC (%.0f s)",
                         next_run.isoformat(), sleep_secs)
                time.sleep(max(sleep_secs, 1))
                woke_at = datetime.now(timezone.utc)
                boundary = (next_run if woke_at >= next_run
                            else _previous_daily_run(woke_at, 8))
        except Exception as e:
            # Never swallow silently: this branch used to hide the month-end
            # crash above, so a whole day without alerts left no trace at all.
            log.error(
                "Inventory alert scheduler error — retrying in %d s: %s",
                _DAILY_LOOP_RETRY_SECONDS, e, exc_info=True,
            )
            time.sleep(_DAILY_LOOP_RETRY_SECONDS)
            continue
        # Idempotency guard for the path that does NOT restart. `missed_boundary`
        # above only protects the top of the loop (a crash/restart); the sleep
        # branch computes `boundary` again after waking and used to run the
        # three jobs unconditionally. If the system clock is corrected
        # BACKWARDS during the sleep (NTP), `woke_at` can come back before
        # `next_run`, `boundary` resolves via `_previous_daily_run` to a pass
        # this loop already completed, and every tenant would get a second
        # copy of all three daily digests — the exact duplicate
        # `loop_state.mark_run` exists to prevent. `continue` here is safe: the
        # only way to reach the jobs without sleeping is the catch-up branch
        # above, which already checked `recorded < boundary` itself, so the
        # next iteration always falls through to a real `time.sleep` rather
        # than looping hot.
        already_done = loop_state.last_boundary(loop_state.INVENTORY_ALERTS)
        if already_done is not None and already_done >= boundary:
            log.warning(
                "Inventory alert: boundary %s already recorded (last=%s) — "
                "the clock did not advance past it, skipping this pass",
                boundary.isoformat(), already_done.isoformat(),
            )
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
        # The boundary is recorded once the three passes have been attempted.
        # Attempted, not succeeded: each one already reports its own failure,
        # and re-running the whole pass on the next restart would mail a second
        # digest to everybody the first one reached.
        loop_state.mark_run(loop_state.INVENTORY_ALERTS, boundary)


# 12:00 UTC: after the 08:00 tenant digests (so a failure in THAT pass is
# already in `system_loop_runs`), and the morning in LatAm (06:00–09:00), so the
# night's failures reach the operator at the start of their working day.
_OPERATOR_DIGEST_HOUR_UTC = 12


def _operator_digest_loop() -> None:
    """Mails the instance operators what failed in the last 24h, daily at
    12:00 UTC (stability §14.g). Same boundary/catch-up/idempotency shape as
    `_inventory_alert_loop` — see that loop for why each step is there."""
    log.info("Operator digest scheduler started")
    while True:
        try:
            now = datetime.now(timezone.utc)
            caught_up = loop_state.missed_boundary(
                loop_state.OPERATOR_DIGEST,
                _previous_daily_run(now, _OPERATOR_DIGEST_HOUR_UTC), now,
                loop_state.DAILY_CATCHUP,
            )
            if caught_up is not None:
                log.warning("Operator digest: catching up the %s pass",
                            caught_up.isoformat())
                boundary = caught_up
            else:
                next_run = _next_daily_run(now, _OPERATOR_DIGEST_HOUR_UTC)
                sleep_secs = (next_run - now).total_seconds()
                log.info("Operator digest: next run at %s UTC (%.0f s)",
                         next_run.isoformat(), sleep_secs)
                time.sleep(max(sleep_secs, 1))
                woke_at = datetime.now(timezone.utc)
                boundary = (next_run if woke_at >= next_run
                            else _previous_daily_run(woke_at, _OPERATOR_DIGEST_HOUR_UTC))
        except Exception as e:
            log.error(
                "Operator digest scheduler error — retrying in %d s: %s",
                _DAILY_LOOP_RETRY_SECONDS, e, exc_info=True,
            )
            time.sleep(_DAILY_LOOP_RETRY_SECONDS)
            continue
        already_done = loop_state.last_boundary(loop_state.OPERATOR_DIGEST)
        if already_done is not None and already_done >= boundary:
            log.warning(
                "Operator digest: boundary %s already recorded (last=%s) — "
                "the clock did not advance past it, skipping this pass",
                boundary.isoformat(), already_done.isoformat(),
            )
            continue
        try:
            # Records its own pass (completed / skipped with a reason / failed):
            # unlike the tenant digests, the outcome here IS the signal.
            from backend.notifications.operator_digest import run_operator_digest
            result = run_operator_digest(boundary)
            log.info("Operator digest: %s", result)
        except Exception as e:
            log.error("Operator digest error: %s", e, exc_info=True)
            loop_state.mark_run(loop_state.OPERATOR_DIGEST, boundary,
                                status=loop_state.STATUS_FAILED,
                                error=f"{type(e).__name__}: {e}"[:500])


def _previous_month_start(now: datetime) -> datetime:
    """The most recent day-1 00:05 UTC boundary at or before `now`."""
    candidate = now.replace(day=1, hour=0, minute=5, second=0, microsecond=0)
    if candidate > now:
        if candidate.month == 1:
            candidate = candidate.replace(year=candidate.year - 1, month=12)
        else:
            candidate = candidate.replace(month=candidate.month - 1)
    return candidate


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
            # The loud half of 11.28: the snapshot taken on the 1st is the
            # CLOSING measurement of the month that just ended, and nothing can
            # produce it afterwards. A missed 1st breaks that month's
            # "capital freed" figure permanently, so this one catches up for
            # three days rather than six hours.
            caught_up = loop_state.missed_boundary(
                loop_state.MONTHLY_OVERSTOCK, _previous_month_start(now), now,
                loop_state.MONTHLY_CATCHUP,
            )
            if caught_up is not None:
                log.warning("Overstock snapshot: catching up the %s pass",
                            caught_up.isoformat())
                boundary = caught_up
            else:
                next_run = _next_month_start(now)
                sleep_secs = (next_run - now).total_seconds()
                log.info("Overstock snapshot: next run at %s UTC (%.0f s)",
                         next_run.isoformat(), sleep_secs)
                time.sleep(max(sleep_secs, 1))
                woke_at = datetime.now(timezone.utc)
                boundary = (next_run if woke_at >= next_run
                            else _previous_month_start(woke_at))
        except Exception as e:
            log.error(
                "Overstock snapshot scheduler error — retrying in %d s: %s",
                _DAILY_LOOP_RETRY_SECONDS, e, exc_info=True,
            )
            time.sleep(_DAILY_LOOP_RETRY_SECONDS)
            continue
        # Same idempotency guard as `_inventory_alert_loop`, and more important
        # here: a duplicate run would re-snapshot SOBRESTOCK for a month that
        # already closed and mail the ROI recap twice. See that loop's comment
        # for why `continue` cannot busy-loop.
        already_done = loop_state.last_boundary(loop_state.MONTHLY_OVERSTOCK)
        if already_done is not None and already_done >= boundary:
            log.warning(
                "Overstock snapshot: boundary %s already recorded (last=%s) — "
                "the clock did not advance past it, skipping this pass",
                boundary.isoformat(), already_done.isoformat(),
            )
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
        loop_state.mark_run(loop_state.MONTHLY_OVERSTOCK, boundary)


# Trial accounts last 24 hours (backend/trial/service.py). Hourly is late by at
# most an hour, and between the end and the sweep the tenant is already read
# only and refused at login, so the hour costs nothing but disk.
_TRIAL_REAPER_SECONDS = 3600


def _trial_reaper_loop() -> None:
    log.info("Trial reaper loop started")
    while True:
        try:
            from backend.trial.service import reap_expired_trials
            reap_expired_trials()
        except Exception as e:
            log.error("Trial reaper error: %s", e, exc_info=True)
        time.sleep(_TRIAL_REAPER_SECONDS)


def enabled_components() -> list[str]:
    """Thread names start() will launch under the current settings.

    The job-claim loop runs when worker_enabled; the cron loops (scheduled
    jobs, daily alerts, monthly snapshot, operator digest) when scheduler_enabled — they are split so a scaled-out deployment can run
    many claim loops but exactly one scheduler.
    """
    components: list[str] = []
    if settings.worker_enabled:
        components.append("job-worker")
    if settings.scheduler_enabled:
        components += [
            "job-scheduler", "inventory-alerts", "overstock-snapshot",
            "operator-digest", "trial-reaper",
        ]
    return components


_COMPONENT_TARGETS = {
    "job-scheduler":      _scheduler_loop,
    "inventory-alerts":   _inventory_alert_loop,
    "overstock-snapshot": _monthly_overstock_snapshot_loop,
    "operator-digest":    _operator_digest_loop,
    "trial-reaper":       _trial_reaper_loop,
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
