"""Who runs a sync, when, and never twice at once.

* **The lease.** Claiming a profile is one UPDATE over a row picked with
  ``FOR UPDATE SKIP LOCKED``: it sets `lease_owner` and `lease_until` only if no
  live lease exists. Two workers (or a worker and a "sync now" click) can never
  run the same profile at the same time; the engine renews the lease after every
  batch, and a lease that expires (the process died) is taken over — the dead
  run is closed as `failed` / `inventory_sync_interrupted`, and since a run
  applies nothing before staging completes and is idempotent after, the next
  run simply does the work again.
* **Backoff.** After N consecutive failures the next attempt waits
  interval x 2^(N-1), capped at a day; a success resets it.
* **The stale alert.** An enabled profile with no success within twice its
  interval raises one critical event per episode (`stale_alerted_at`).
* **WORKER_ENABLED=false** — the loop does not start, so nothing runs on a
  schedule. A person's "sync now" still runs: it is an explicit request.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.db.connection import execute, query, query_one
from backend.errors import AppError
from backend.inventory_sync import engine

log = logging.getLogger(__name__)

POLL_SECONDS = 30
MAX_BACKOFF_MINUTES = 24 * 60


def _owner() -> str:
    from backend.workers.worker import worker_id
    return f"{worker_id()}:{threading.get_ident()}"


def _close_interrupted(profile_id: str) -> None:
    """A run still 'running' when its lease is being taken over died with its
    process. Close it out loud instead of leaving it 'running' forever."""
    execute(
        "UPDATE inventory_sync_runs SET status = 'failed', finished_at = NOW(), "
        "error_code = 'inventory_sync_interrupted' WHERE profile_id = %s AND status = 'running'",
        (profile_id,))
    execute("DELETE FROM inventory_sync_staging s USING inventory_sync_runs r "
            "WHERE s.run_id = r.id AND r.profile_id = %s AND r.status <> 'running'", (profile_id,))


def claim_due(now: Optional[datetime] = None, owner: Optional[str] = None) -> Optional[dict]:
    """Lease the most overdue enabled profile, or None."""
    owner = owner or _owner()
    row = query_one(
        "UPDATE inventory_sync_profiles p SET lease_owner = %s, "
        "       lease_until = NOW() + %s * INTERVAL '1 minute' "
        "WHERE p.id = (SELECT id FROM inventory_sync_profiles "
        "              WHERE enabled AND next_run_at IS NOT NULL AND next_run_at <= %s "
        "                AND (lease_until IS NULL OR lease_until < NOW()) "
        "              ORDER BY next_run_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
        "RETURNING p.*",
        (owner, engine.LEASE_MINUTES, now or datetime.now(timezone.utc)))
    if row:
        _close_interrupted(row["id"])
    return row


def claim_one(tenant_id: str, profile_id: str, owner: Optional[str] = None) -> dict:
    """Lease one specific profile for a manual run, or refuse (409) when a
    run already holds it."""
    owner = owner or _owner()
    row = query_one(
        "UPDATE inventory_sync_profiles SET lease_owner = %s, "
        "       lease_until = NOW() + %s * INTERVAL '1 minute' "
        "WHERE id = %s AND tenant_id = %s AND (lease_until IS NULL OR lease_until < NOW()) "
        "RETURNING *",
        (owner, engine.LEASE_MINUTES, profile_id, tenant_id))
    if not row:
        if not query_one("SELECT 1 FROM inventory_sync_profiles WHERE id = %s AND tenant_id = %s",
                         (profile_id, tenant_id)):
            raise AppError("inventory_sync_profile_not_found", "Sync profile not found.",
                           status_code=404, params={"profile_id": profile_id})
        raise AppError("inventory_sync_running", "This sync is already running.", status_code=409)
    _close_interrupted(profile_id)
    return row


def release(profile_id: str, owner: str) -> None:
    """Drop the lease and schedule the next run (with backoff after failures)."""
    row = query_one("SELECT interval_minutes, consecutive_failures, last_status "
                    "FROM inventory_sync_profiles WHERE id = %s", (profile_id,))
    if not row:
        return
    interval = int(row["interval_minutes"] or 60)
    failures = int(row["consecutive_failures"] or 0)
    wait = interval if failures == 0 else min(MAX_BACKOFF_MINUTES, interval * (2 ** (failures - 1)))
    execute(
        "UPDATE inventory_sync_profiles SET lease_owner = NULL, lease_until = NULL, "
        "next_run_at = NOW() + %s * INTERVAL '1 minute' WHERE id = %s AND lease_owner = %s",
        (wait, profile_id, owner))


def run_claimed(profile: dict, owner: str, trigger: str, *, user_id: Optional[str] = None,
                override_brake: bool = False, run_id: Optional[str] = None) -> Optional[dict]:
    """Run a profile this caller holds the lease of, then release it."""
    try:
        run_id = run_id or engine.start_run(profile["tenant_id"], profile["id"], trigger,
                                            user_id=user_id, worker=owner)
        return engine.execute_run(profile["tenant_id"], profile["id"], run_id,
                                  override_brake=override_brake, lease_owner=owner,
                                  user_id=user_id)
    except Exception:  # noqa: BLE001 - the loop must survive; the run row says what happened
        log.exception("inventory sync: profile %s crashed outside the run", profile.get("id"))
        return None
    finally:
        release(profile["id"], owner)


def start_manual(tenant_id: str, profile_id: str, user_id: str, *,
                 override_brake: bool = False, background: bool = True) -> dict:
    """'Sync now': lease, create the run row, run it (in a thread unless
    `background` is False) and return the run row as it is at start."""
    owner = f"manual:{_owner()}"
    current = query_one("SELECT dry_run_hash, config_hash FROM inventory_sync_profiles "
                        "WHERE id = %s AND tenant_id = %s", (profile_id, tenant_id))
    if current and current["dry_run_hash"] != current["config_hash"]:
        # Same bar as enabling: nobody applies a mapping they have not previewed.
        raise AppError("inventory_sync_dry_run_required",
                       "Preview this sync (dry run) before running it.", status_code=409)
    profile = claim_one(tenant_id, profile_id, owner)
    try:
        src = query_one("SELECT connection_status FROM datasets WHERE id = %s AND tenant_id = %s",
                        (profile["data_source_id"], tenant_id))
        if not src:
            raise AppError("data_source_not_found", "Data source not found", status_code=404)
        run_id = engine.start_run(tenant_id, profile_id, "manual", user_id=user_id, worker=owner)
    except Exception:
        release(profile_id, owner)
        raise
    if background:
        t = threading.Thread(target=run_claimed, args=(profile, owner, "manual"),
                             kwargs={"user_id": user_id, "override_brake": override_brake,
                                     "run_id": run_id},
                             daemon=True, name=f"inventory-sync-{profile_id}")
        t.start()
    else:
        run_claimed(profile, owner, "manual", user_id=user_id, override_brake=override_brake,
                    run_id=run_id)
    return query_one("SELECT * FROM inventory_sync_runs WHERE id = %s", (run_id,))


def alert_stale(now: Optional[datetime] = None) -> int:
    """Raise one critical event per profile that has not succeeded within
    twice its interval. Returns how many were raised."""
    from backend.activity.events import record_event
    now = now or datetime.now(timezone.utc)
    rows = query(
        "SELECT * FROM inventory_sync_profiles WHERE enabled AND stale_alerted_at IS NULL "
        "AND COALESCE(last_success_at, created_at) < %s - (2 * interval_minutes) * INTERVAL '1 minute'",
        (now,))
    for row in rows:
        since = row.get("last_success_at") or row.get("created_at")
        hours = round((now - since).total_seconds() / 3600, 1)
        record_event(row["tenant_id"], "system", "inventory_sync.stale", resource=row["id"],
                     details={"profile_name": row["name"], "hours": hours},
                     reason="inventory_sync_not_succeeding",
                     reason_params={"hours": hours, "interval_minutes": row["interval_minutes"],
                                    "error_code": row.get("last_error_code")},
                     status="error")
        execute("UPDATE inventory_sync_profiles SET stale_alerted_at = %s WHERE id = %s",
                (now, row["id"]))
    return len(rows)


def run_due_once(now: Optional[datetime] = None, max_runs: int = 5) -> int:
    """One pass of the loop: run up to `max_runs` due profiles, then check for
    stale ones. Returns the number of runs made. Extracted for the tests."""
    made = 0
    owner = _owner()
    for _ in range(max_runs):
        profile = claim_due(now, owner)
        if not profile:
            break
        run_claimed(profile, owner, "schedule")
        made += 1
    alert_stale(now)
    return made


def loop() -> None:
    log.info("Inventory sync loop started")
    while True:
        made = 0
        try:
            made = run_due_once()
        except Exception as e:  # noqa: BLE001
            log.error("Inventory sync loop error: %s", e, exc_info=True)
        if made == 0:
            time.sleep(POLL_SECONDS)
