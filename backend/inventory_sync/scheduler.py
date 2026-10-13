"""Who runs a sync, when, and never twice at once.

* **The lease.** Claiming a profile is one UPDATE over a row picked with
  ``FOR UPDATE SKIP LOCKED``: it sets `lease_owner` and `lease_until` only if no
  live lease exists. Two workers (or a worker and a "sync now" / "refresh"
  click) never run the same profile at the same time. The engine renews the
  lease after every batch.
* **Crash resume.** A lease that expires means its process died. If that run
  had finished staging and passed the brake, the new owner RESUMES applying
  from the first unapplied key (a checkpoint, not a restart); otherwise the
  dead run is closed as `failed / inventory_sync_interrupted` and a new run
  starts — nothing was applied before staging completed, so nothing is lost.
* **Freshness.** `scheduled` runs every `interval_minutes`; `continuous` polls
  every `poll_seconds` (incremental: watermark / ledger cursor / change capture)
  and does a full reconcile every `reconcile_minutes` (deletions, missed or
  late changes); `on_demand` runs only when someone presses Refresh. Every
  next run gets a jitter (±20%) so a thousand tenants on the same interval do
  not stampede, and a failure backs off exponentially (capped).
* **Health.** Paused (brake, schema drift) and blocked (credentials, grants,
  firewall, impossible config) profiles are not claimed: retrying a wrong
  password locks the customer's account. A blocked profile is retried once its
  data source is edited or re-tested, or when a person presses run / refresh.
* **Refresh.** `refresh()` coalesces (a refresh that finds a run in flight
  joins it — fifty people pressing at once make one source query), honours a
  per-profile cooldown, and waits a bounded time before answering "still
  running, data as of HH:MM" instead of hanging.
* **WORKER_ENABLED=false** — the loop does not start, so nothing runs on a
  schedule. A person's "sync now" and "refresh" still run: explicit requests.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.config import settings
from backend.db.connection import _json, execute, query, query_one
from backend.errors import AppError
from backend.inventory_sync import engine

log = logging.getLogger(__name__)

POLL_SECONDS = 5
MAX_PER_TENANT = 2          # scheduled/continuous runs one tenant may hold at once
MAX_BACKOFF_MINUTES = 24 * 60
JITTER = 0.2
_pool: Optional[ThreadPoolExecutor] = None
_pool_lock = threading.Lock()
_inflight = 0


def _setting(name: str, default: int) -> int:
    try:
        return max(1, int(getattr(settings, name, default) or default))
    except (TypeError, ValueError):
        return default


def _owner() -> str:
    from backend.workers.worker import worker_id
    return f"{worker_id()}:{threading.get_ident()}"


def _resume_or_close(profile_id: str) -> Optional[str]:
    """After taking over a lease: the dead run's id when it can be resumed
    (staging complete, brake passed), else None after closing it out loud."""
    dead = query_one("SELECT id, stage_complete, brake_passed FROM inventory_sync_runs "
                     "WHERE profile_id = %s AND status IN ('running') ORDER BY started_at DESC LIMIT 1",
                     (profile_id,))
    if dead and dead["stage_complete"] and dead["brake_passed"]:
        execute("UPDATE inventory_sync_runs SET status = 'failed', finished_at = NOW(), "
                "error_code = 'inventory_sync_interrupted' WHERE profile_id = %s AND status = 'running' "
                "AND id <> %s", (profile_id, dead["id"]))
        return dead["id"]
    execute("UPDATE inventory_sync_runs SET status = 'failed', finished_at = NOW(), "
            "error_code = 'inventory_sync_interrupted' WHERE profile_id = %s AND status = 'running'",
            (profile_id,))
    execute("DELETE FROM inventory_sync_staging s USING inventory_sync_runs r "
            "WHERE s.run_id = r.id AND r.profile_id = %s AND r.status <> 'running' "
            "AND r.status <> 'receiving'", (profile_id,))
    return None


_CLAIMABLE = (
    # A push profile is fed by the customer's system; there is nothing to pull.
    "enabled AND source_kind <> 'push' AND freshness <> 'on_demand' AND next_run_at IS NOT NULL "
    "AND next_run_at <= %s "
    "AND (lease_until IS NULL OR lease_until < NOW()) "
    "AND (health NOT IN ('paused', 'blocked') OR (health = 'blocked' AND EXISTS ("
    "  SELECT 1 FROM datasets d WHERE d.id = inventory_sync_profiles.data_source_id "
    "  AND d.updated_at > COALESCE(inventory_sync_profiles.health_changed_at, 'epoch'))))"
)


def claim_due(now: Optional[datetime] = None, owner: Optional[str] = None,
              profile_id: Optional[str] = None) -> Optional[dict]:
    """Lease the most overdue runnable profile, or None. `profile_id` narrows
    the pick to one profile (the same rules), so a check about one profile is
    not answered by another tenant's due profile."""
    owner = owner or _owner()
    # Per-tenant gate: one tenant's many continuous profiles (polling every
    # 15 s) cannot take every worker slot from the other tenants. Explicit
    # requests (refresh, push, "sync now") go through claim_one, not here.
    only = (" AND (SELECT COUNT(*) FROM inventory_sync_profiles o WHERE o.tenant_id = "
            "inventory_sync_profiles.tenant_id AND o.lease_until > NOW()) < %s" % int(MAX_PER_TENANT))
    only += " AND id = %s" if profile_id else ""
    params: tuple = (owner, engine.LEASE_MINUTES, now or datetime.now(timezone.utc))
    row = query_one(
        "UPDATE inventory_sync_profiles p SET lease_owner = %s, "
        "       lease_until = NOW() + %s * INTERVAL '1 minute' "
        f"WHERE p.id = (SELECT id FROM inventory_sync_profiles WHERE {_CLAIMABLE}{only} "
        "              ORDER BY next_run_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
        "RETURNING p.*",
        params + ((profile_id,) if profile_id else ()))
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
    return row


def _next_wait_seconds(row: dict) -> float:
    failures = int(row.get("consecutive_failures") or 0)
    if row.get("freshness") == "continuous":
        base = max(_setting("inventory_sync_min_poll_seconds", 15), int(row.get("poll_seconds") or 60))
    else:
        base = 60 * int(row.get("interval_minutes") or 60)
    if failures:
        base = min(MAX_BACKOFF_MINUTES * 60, base * (2 ** min(failures - 1, 16)))
    return base * (1 + random.uniform(-JITTER, JITTER))


def release(profile_id: str, owner: str) -> None:
    """Drop the lease and schedule the next run (jitter; backoff after failures)."""
    row = query_one("SELECT interval_minutes, consecutive_failures, freshness, poll_seconds "
                    "FROM inventory_sync_profiles WHERE id = %s", (profile_id,))
    if not row:
        return
    execute(
        "UPDATE inventory_sync_profiles SET lease_owner = NULL, lease_until = NULL, "
        "next_run_at = NOW() + %s * INTERVAL '1 second' WHERE id = %s AND lease_owner = %s",
        (_next_wait_seconds(row), profile_id, owner))


def _kind_for(profile: dict, trigger: str) -> str:
    """full / incremental / reconcile for a scheduled or continuous run."""
    incremental_capable = (profile.get("mode") == "incremental"
                           or (profile.get("stock_model") or "snapshot") == "ledger"
                           or (profile.get("strategy") or "auto") in
                           ("change_tracking", "oracle_rowscn", "change_stream"))
    if profile.get("freshness") == "continuous":
        due = profile.get("next_reconcile_at")
        if due is None or due <= datetime.now(timezone.utc) or not incremental_capable:
            return "reconcile"
        return "incremental"
    if (profile.get("stock_model") or "snapshot") == "ledger" and not profile.get("cursor_value"):
        return "full"
    return "incremental" if incremental_capable and profile.get("mode") == "incremental" else "full"


def run_claimed(profile: dict, owner: str, trigger: str, *, user_id: Optional[str] = None,
                override_brake: bool = False, run_id: Optional[str] = None,
                kind: Optional[str] = None, scope: Optional[dict] = None) -> Optional[dict]:
    """Run a profile this caller holds the lease of, then release it."""
    try:
        resume_id = None if run_id else _resume_or_close(profile["id"])
        if resume_id:
            return engine.execute_run(profile["tenant_id"], profile["id"], resume_id,
                                      lease_owner=owner, user_id=user_id, resume=True)
        kind = kind or _kind_for(profile, trigger)
        run_id = run_id or engine.start_run(profile["tenant_id"], profile["id"], trigger,
                                            user_id=user_id, worker=owner, kind=kind, scope=scope)
        run = engine.execute_run(profile["tenant_id"], profile["id"], run_id,
                                 override_brake=override_brake, lease_owner=owner, user_id=user_id)
        if run and run.get("status") == "success" and kind in ("reconcile", "full"):
            execute("UPDATE inventory_sync_profiles SET next_reconcile_at = NOW() + "
                    "reconcile_minutes * INTERVAL '1 minute' WHERE id = %s", (profile["id"],))
        return run
    except Exception:  # noqa: BLE001 - the loop must survive; the run row says what happened
        log.exception("inventory sync: profile %s crashed outside the run", profile.get("id"))
        return None
    finally:
        release(profile["id"], owner)


def _require_previewed(tenant_id: str, profile_id: str) -> dict:
    current = query_one("SELECT * FROM inventory_sync_profiles WHERE id = %s AND tenant_id = %s",
                        (profile_id, tenant_id))
    if not current:
        raise AppError("inventory_sync_profile_not_found", "Sync profile not found.", status_code=404,
                       params={"profile_id": profile_id})
    if current["dry_run_hash"] != current["config_hash"]:
        # Same bar as enabling: nobody applies a mapping they have not previewed.
        raise AppError("inventory_sync_dry_run_required",
                       "Preview this sync (dry run) before running it.", status_code=409)
    return current


def _refuse_push(tenant_id: str, profile_id: str) -> None:
    """A push profile has no source to read: running it as a pull failed with
    data_source_not_found and marked a healthy push sync "blocked"."""
    row = query_one("SELECT source_kind FROM inventory_sync_profiles WHERE id = %s AND tenant_id = %s",
                    (profile_id, tenant_id))
    if row and row["source_kind"] == "push":
        raise AppError("inventory_sync_push_has_nothing_to_pull",
                       "This sync is fed by your system (push): send it a batch instead.",
                       status_code=409, params={"profile_id": profile_id})


def start_manual(tenant_id: str, profile_id: str, user_id: str, *,
                 override_brake: bool = False, background: bool = True,
                 trigger: str = "manual", kind: Optional[str] = None,
                 scope: Optional[dict] = None) -> dict:
    """'Sync now': lease, create the run row, run it (in a thread unless
    `background` is False) and return the run row as it is at start."""
    owner = f"manual:{_owner()}"
    _refuse_push(tenant_id, profile_id)
    _require_previewed(tenant_id, profile_id)
    profile = claim_one(tenant_id, profile_id, owner)
    try:
        if profile.get("data_source_id") and not query_one(
                "SELECT 1 FROM datasets WHERE id = %s AND tenant_id = %s",
                (profile["data_source_id"], tenant_id)):
            raise AppError("data_source_not_found", "Data source not found", status_code=404)
        _resume_or_close(profile_id)
        kind = kind or ("scoped" if scope else ("full" if trigger == "manual" else _kind_for(profile, trigger)))
        run_id = engine.start_run(tenant_id, profile_id, trigger, user_id=user_id, worker=owner,
                                  kind=kind, scope=scope)
    except Exception:
        release(profile_id, owner)
        raise
    args = dict(user_id=user_id, override_brake=override_brake, run_id=run_id, kind=kind, scope=scope)
    if background:
        t = threading.Thread(target=run_claimed, args=(profile, owner, trigger), kwargs=args,
                             daemon=True, name=f"inventory-sync-{profile_id}")
        t.start()
    else:
        run_claimed(profile, owner, trigger, **args)
    return query_one("SELECT * FROM inventory_sync_runs WHERE id = %s", (run_id,))


# ── On-demand refresh ─────────────────────────────────────────────────────────

def _refreshable(tenant_id: str, profile_id: Optional[str]) -> list[dict]:
    if profile_id:
        rows = query("SELECT * FROM inventory_sync_profiles WHERE tenant_id = %s AND id = %s",
                     (tenant_id, profile_id))
        if not rows:
            raise AppError("inventory_sync_profile_not_found", "Sync profile not found.", status_code=404,
                           params={"profile_id": profile_id})
        return rows
    live = query("SELECT * FROM inventory_sync_profiles WHERE tenant_id = %s "
                 "AND (enabled OR freshness = 'on_demand') ORDER BY priority, created_at", (tenant_id,))
    if live:
        return live
    # No sync is switched on, but the stock on screen came from one (a person
    # previewed it and pressed "Sync now" without enabling it): "Refresh now"
    # runs those, exactly like that button, instead of answering "nothing".
    return query("SELECT * FROM inventory_sync_profiles WHERE tenant_id = %s ORDER BY priority, created_at",
                 (tenant_id,))


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def refresh(tenant_id: str, user_id: str, *, profile_id: Optional[str] = None,
            skus: Optional[list[str]] = None, wait_s: Optional[float] = None) -> dict:
    """Make the displayed stock current now. See the module doc.

    Returns one entry per profile: `status` is `done` (finished within the
    budget), `running` (still going: the screen shows the data as of the last
    success and polls), `joined` (a run was already in flight), `cooldown`
    (pressed again too soon: `retry_after_s`), `paused` / `blocked` (needs a
    person: the reason code) or `failed`. The stock shown is never blanked."""
    wait_s = float(wait_s if wait_s is not None else _setting("inventory_sync_refresh_wait_seconds", 8))
    cooldown = _setting("inventory_sync_refresh_cooldown_seconds", 30)
    scope = {"skus": [str(s) for s in skus][:1000]} if skus else None
    results = []
    for p in _refreshable(tenant_id, profile_id):
        entry = {"profile_id": p["id"], "profile_name": p["name"], "data_as_of": _iso(p.get("data_as_of"))}
        if p.get("source_kind") == "push":
            # Fed by the customer's system: its data is as of its last batch.
            results.append({**entry, "status": "push"})
            continue
        if p.get("health") in ("paused", "blocked"):
            results.append({**entry, "status": p["health"], "reason": p.get("health_reason")})
            continue
        running = query_one("SELECT id FROM inventory_sync_runs WHERE profile_id = %s AND status = 'running' "
                            "ORDER BY started_at DESC LIMIT 1", (p["id"],))
        if running and p.get("lease_until") and p["lease_until"] > datetime.now(timezone.utc):
            results.append({**entry, **_wait(running["id"], wait_s), "joined": True})
            continue
        claimed = query_one(
            "UPDATE inventory_sync_profiles SET last_refresh_at = NOW() WHERE id = %s AND "
            "(last_refresh_at IS NULL OR last_refresh_at < NOW() - %s * INTERVAL '1 second' OR %s) "
            "RETURNING id", (p["id"], cooldown, scope is not None))
        if not claimed:
            # Someone pressed it a moment ago: join their run if it is (about
            # to be) in flight — fifty presses make one source query.
            joined = None
            for _ in range(8):
                joined = query_one("SELECT id FROM inventory_sync_runs WHERE profile_id = %s AND "
                                   "status = 'running' ORDER BY started_at DESC LIMIT 1", (p["id"],))
                if joined:
                    break
                time.sleep(0.25)
            if joined:
                results.append({**entry, **_wait(joined["id"], wait_s), "joined": True})
                continue
            p = query_one("SELECT * FROM inventory_sync_profiles WHERE id = %s", (p["id"],)) or p
            left = cooldown - (datetime.now(timezone.utc) - p["last_refresh_at"]).total_seconds()
            results.append({**entry, "status": "cooldown", "retry_after_s": max(1, int(left))})
            continue
        try:
            run = start_manual(tenant_id, p["id"], user_id, trigger="refresh", scope=scope,
                               kind="scoped" if scope else None)
        except AppError as exc:
            if exc.code == "inventory_sync_running":
                again = query_one("SELECT id FROM inventory_sync_runs WHERE profile_id = %s AND "
                                  "status = 'running' ORDER BY started_at DESC LIMIT 1", (p["id"],))
                if again:
                    results.append({**entry, **_wait(again["id"], wait_s), "joined": True})
                    continue
            results.append({**entry, "status": "failed", "error_code": exc.code})
            continue
        results.append({**entry, **_wait(run["id"], wait_s)})
    return {"profiles": results, "freshness": freshness(tenant_id)}


def _wait(run_id: str, wait_s: float) -> dict:
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        run = query_one("SELECT id, status, error_code, created, updated, unchanged, rejected, "
                        "finished_at FROM inventory_sync_runs WHERE id = %s", (run_id,))
        if not run or run["status"] not in ("running", "receiving"):
            break
        if time.monotonic() >= deadline:
            return {"status": "running", "run_id": run_id}
        time.sleep(0.25)
    if not run:
        return {"status": "failed", "run_id": run_id, "error_code": "inventory_sync_profile_changed"}
    status = "done" if run["status"] == "success" else run["status"]
    return {"status": status, "run_id": run_id, "error_code": run.get("error_code"),
            "created": run.get("created"), "updated": run.get("updated"),
            "unchanged": run.get("unchanged"), "rejected": run.get("rejected")}


def freshness(tenant_id: str) -> dict:
    """The tenant's freshness chip: `fresh` / `stale` / `blocked` / `none`,
    `data_as_of` (the oldest last-success among live profiles: the stock is at
    least that fresh), and per profile what is wrong when it is not fresh.

    With no live profile but some switched off, those are reported (as
    `stale`, reason `inventory_sync_disabled`): the stock on screen came from
    them and nothing keeps it current. `none` means the tenant has no sync."""
    all_rows = query("SELECT id, name, enabled, freshness, interval_minutes, poll_seconds, health, "
                     "health_reason, data_as_of, last_success_at, lease_until FROM inventory_sync_profiles "
                     "WHERE tenant_id = %s", (tenant_id,))
    rows = [r for r in all_rows if r["enabled"] or r["freshness"] == "on_demand"] or all_rows
    if not rows:
        return {"state": "none", "data_as_of": None, "profiles": []}
    now = datetime.now(timezone.utc)
    items, worst = [], "fresh"
    order = {"fresh": 0, "stale": 1, "blocked": 2}
    for r in rows:
        budget = (2 * int(r["poll_seconds"] or 60) if r["freshness"] == "continuous"
                  else 120 * int(r["interval_minutes"] or 60))
        last = r.get("data_as_of") or r.get("last_success_at")
        off = not r["enabled"] and r["freshness"] != "on_demand"
        reason = r.get("health_reason")
        if r["health"] in ("paused", "blocked"):
            state = "blocked"
        elif off:
            state, reason = "stale", "inventory_sync_disabled"
        elif last is None or (r["freshness"] != "on_demand" and (now - last).total_seconds() > budget):
            state = "stale"
        else:
            state = "fresh"
        worst = state if order[state] > order[worst] else worst
        items.append({"profile_id": r["id"], "name": r["name"], "state": state, "health": r["health"],
                      "reason": reason, "data_as_of": _iso(last), "enabled": bool(r["enabled"]),
                      "running": bool(r.get("lease_until") and r["lease_until"] > now)})
    known = [r.get("data_as_of") or r.get("last_success_at") for r in rows]
    as_of = None if any(k is None for k in known) else min(known)
    return {"state": worst, "data_as_of": _iso(as_of), "profiles": items}


# ── Alerts, retention, the loop ───────────────────────────────────────────────

def alert_stale(now: Optional[datetime] = None) -> int:
    """Raise one critical event per profile that has not succeeded within
    twice its interval. Returns how many were raised."""
    from backend.activity.events import record_event
    now = now or datetime.now(timezone.utc)
    rows = query(
        "SELECT * FROM inventory_sync_profiles WHERE enabled AND freshness <> 'on_demand' "
        "AND stale_alerted_at IS NULL AND COALESCE(last_success_at, created_at) < %s - "
        "(CASE WHEN freshness = 'continuous' THEN GREATEST(2 * poll_seconds, 600) "
        "      ELSE 120 * interval_minutes END) * INTERVAL '1 second'",
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


def prune(now: Optional[datetime] = None) -> dict:
    """Keep the run log, staging and replay stores bounded: run rows older
    than the retention (the last 20 of each profile are always kept), staging
    of runs that are no longer running, idempotency keys past a day, applied
    movement ids past the overlap window."""
    days = _setting("inventory_sync_run_retention_days", 90)
    runs = query(
        "DELETE FROM inventory_sync_runs r WHERE r.started_at < NOW() - %s * INTERVAL '1 day' "
        "AND r.status NOT IN ('running', 'receiving') AND r.id NOT IN ("
        "  SELECT id FROM (SELECT id, row_number() OVER (PARTITION BY profile_id ORDER BY started_at DESC) rn "
        "  FROM inventory_sync_runs) x WHERE rn <= 20) RETURNING r.id", (days,))
    staging = query("DELETE FROM inventory_sync_staging s USING inventory_sync_runs r WHERE s.run_id = r.id "
                    "AND r.status NOT IN ('running', 'receiving') RETURNING s.run_id")
    execute("DELETE FROM inventory_sync_idempotency WHERE created_at < NOW() - INTERVAL '1 day'")
    execute("DELETE FROM inventory_sync_applied_movements WHERE applied_at < NOW() - INTERVAL '7 days'")
    return {"runs": len(runs), "staging": len(staging)}


def _executor() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=_setting("inventory_sync_max_parallel_runs", 4),
                                       thread_name_prefix="inventory-sync")
        return _pool


def _run_and_count(profile: dict, owner: str, trigger: str) -> None:
    global _inflight
    try:
        run_claimed(profile, owner, trigger)
    finally:
        with _pool_lock:
            _inflight -= 1


def run_due_once(now: Optional[datetime] = None, max_runs: int = 5, parallel: bool = False) -> int:
    """One pass of the loop: claim up to `max_runs` due profiles (bounded by
    the free parallel slots), then check for stale ones. Returns the number of
    runs started. `parallel=False` runs them inline (the tests)."""
    global _inflight
    made = 0
    for _ in range(max_runs):
        if parallel:
            with _pool_lock:
                if _inflight >= _setting("inventory_sync_max_parallel_runs", 4):
                    break
        owner = f"{_owner()}:{made}"
        profile = claim_due(now, owner)
        if not profile:
            break
        trigger = "continuous" if profile.get("freshness") == "continuous" else "schedule"
        if parallel:
            with _pool_lock:
                _inflight += 1
            _executor().submit(_run_and_count, profile, owner, trigger)
        else:
            run_claimed(profile, owner, trigger)
        made += 1
    alert_stale(now)
    return made


def loop() -> None:
    log.info("Inventory sync loop started")
    last_prune = 0.0
    while True:
        made = 0
        try:
            made = run_due_once(parallel=True)
            if time.monotonic() - last_prune > 3600:
                prune()
                last_prune = time.monotonic()
        except Exception as e:  # noqa: BLE001
            log.error("Inventory sync loop error: %s", e, exc_info=True)
        if made == 0:
            time.sleep(POLL_SECONDS)
