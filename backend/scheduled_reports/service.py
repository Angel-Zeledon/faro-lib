"""The worker pass: claim due report schedules, build the report, queue the mail.

Guarantees, and where each one lives:

* **No double send, ever.** A run is owned by whoever INSERTs its
  `report_schedule_runs` row; the key is (schedule, local wall-clock minute), a UNIQUE
  constraint. The insert and the advance of `next_run_at` happen in ONE
  transaction that holds the schedule row (`FOR UPDATE SKIP LOCKED`), so a
  second worker skips it and a restart finds the period already owned. The
  repeated hour at the end of daylight saving time maps to the same local key
  (see `schedule_math`). Each message also carries a `dedupe_key`
  (`report:<run>:<recipient row>`), so even a re-driven run (a worker that
  died mid-way) cannot queue a recipient twice.
* **Never later than it is worth.** A run due longer ago than the catch-up
  window is recorded as `skipped`, with an event, instead of being sent stale.
* **Never to someone who should no longer get it.** Every recipient is
  re-resolved at send time: a user who left the tenant, was suspended, or is now
  limited to some warehouses (the report is company-wide) is skipped with the
  reason recorded; an external address no longer on the admin's allow-list is
  skipped; anyone who used the unsubscribe link is skipped.
* **Failure is visible and bounded.** A run that could not be built or handed
  over counts against the schedule; the third failure in a row pauses it by
  itself (an event the bell shows) rather than failing silently forever.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.activity.events import record_event
from backend.db.connection import execute, query, query_one, transaction
from backend.notifications import outbox
from backend.scheduled_reports import builder, catalog
from backend.scheduled_reports.schedule_math import advance, local_key, next_run_after

log = logging.getLogger(__name__)

SYSTEM_ACTOR = "system"
# Reports are for the day they describe; a mail the outbox could not deliver
# within this long is abandoned (and says so in the delivery counts).
OUTBOX_TTL_SECONDS = 6 * 3600


def _tz(tenant_id: str) -> str:
    from backend.api.v1.timezone import timezone_of
    return timezone_of(tenant_id)


# ── Recipients ───────────────────────────────────────────────────────────────

def resolve_recipients(schedule: dict) -> tuple[list[dict], list[dict]]:
    """(deliverable, skipped) for this schedule, decided NOW.

    deliverable: `{"recipient_id", "email", "kind"}`; skipped: `{"recipient",
    "kind", "reason"}`. Never raises for a bad recipient: a bad recipient is a
    skipped one with its reason on the run.
    """
    tenant_id = schedule["tenant_id"]
    rows = query(
        """SELECT r.id, r.kind, r.user_id, r.email, r.unsubscribed_at,
                  u.email AS user_email, u.status AS user_status, u.tenant_id AS user_tenant,
                  u.warehouse_scope AS user_scope,
                  (a.email IS NOT NULL) AS allowed
             FROM report_schedule_recipients r
             LEFT JOIN users u ON r.kind = 'user' AND u.id = r.user_id
             LEFT JOIN report_external_allowlist a
                    ON r.kind = 'external' AND a.tenant_id = r.tenant_id AND a.email = r.email
            WHERE r.schedule_id = %s AND r.tenant_id = %s
            ORDER BY r.created_at, r.id""",
        (schedule["id"], tenant_id))
    ok_rows: list[dict] = []
    skipped: list[dict] = []
    for r in rows:
        who = r["user_id"] if r["kind"] == "user" else r["email"]

        def skip(reason: str) -> None:
            skipped.append({"recipient": who, "kind": r["kind"], "reason": reason})

        if r["unsubscribed_at"] is not None:
            skip("unsubscribed")
            continue
        if r["kind"] == "user":
            if r["user_email"] is None or r["user_tenant"] != tenant_id:
                skip("user_removed")
            elif r["user_status"] != "active":
                skip("user_inactive")
            elif r["user_scope"] is not None:
                # JSONB null decodes to None; anything else (a list, even an
                # empty one) is a scope, and the report is company-wide.
                skip("user_warehouse_scoped")
            elif not (r["user_email"] or "").strip():
                skip("user_without_email")
            else:
                ok_rows.append({"recipient_id": r["id"], "email": r["user_email"].strip(), "kind": "user"})
        else:
            if not r["allowed"]:
                skip("external_not_allowed")
            else:
                ok_rows.append({"recipient_id": r["id"], "email": r["email"], "kind": "external"})
    return ok_rows, skipped


# ── Bookkeeping ──────────────────────────────────────────────────────────────

def _finish_run(run_id: str, status: str, *, error: Optional[str] = None, queued: int = 0,
                skipped: Optional[list] = None, snapshot: Optional[dict] = None) -> None:
    execute(
        """UPDATE report_schedule_runs
              SET status = %s, error = %s, recipients_queued = %s,
                  recipients_skipped = %s::jsonb,
                  snapshot = COALESCE(%s::jsonb, snapshot), finished_at = NOW()
            WHERE id = %s""",
        (status, error, queued, json.dumps(skipped or []),
         json.dumps(snapshot) if snapshot is not None else None, run_id))


def _record_failure(schedule: dict, run_id: str, reason: str, error: str) -> None:
    """The run failed: say so on the run, the schedule and the activity feed,
    and pause the schedule at the third consecutive failure."""
    error = str(error)[:300]
    _finish_run(run_id, "failed", error=error)
    row = query_one(
        """UPDATE report_schedules
              SET consecutive_failures = consecutive_failures + 1, last_run_at = NOW(),
                  last_status = 'failed', last_error = %s, updated_at = NOW()
            WHERE id = %s AND tenant_id = %s
        RETURNING consecutive_failures""",
        (error, schedule["id"], schedule["tenant_id"]))
    failures = int(row["consecutive_failures"]) if row else 0
    record_event(schedule["tenant_id"], SYSTEM_ACTOR, "scheduled_report.failed",
                 resource=schedule["id"], reason=reason,
                 details={"schedule_name": schedule["name"]})
    if failures >= catalog.MAX_CONSECUTIVE_FAILURES:
        _auto_pause(schedule, "failures", "report_failed_repeatedly", failures)


def _auto_pause(schedule: dict, paused_reason: str, event_reason: str, failures: int = 0) -> None:
    changed = query_one(
        """UPDATE report_schedules
              SET enabled = FALSE, paused_reason = %s, paused_at = NOW(), updated_at = NOW()
            WHERE id = %s AND tenant_id = %s AND enabled
        RETURNING id""",
        (paused_reason, schedule["id"], schedule["tenant_id"]))
    if changed:
        record_event(schedule["tenant_id"], SYSTEM_ACTOR, "scheduled_report.auto_paused",
                     resource=schedule["id"], reason=event_reason,
                     details={"schedule_name": schedule["name"], "failures": failures or None})


# ── One run ──────────────────────────────────────────────────────────────────

def execute_run(schedule: dict, run: dict, now: Optional[datetime] = None) -> str:
    """Build and queue one claimed run. Returns the run's final status.
    Safe to call again for the same run (a re-driven one): the outbox
    `dedupe_key` per recipient makes a repeat queue nothing twice."""
    now = now or datetime.now(timezone.utc)
    tenant_id = schedule["tenant_id"]
    try:
        from backend.notifications import email as email_mod
        if not email_mod.is_configured(tenant_id):
            # Nothing can leave: do not pretend a report was queued.
            _record_failure(schedule, run["id"], "no_transport_configured", "not_configured")
            return "failed"
        sections = schedule["sections"]
        if isinstance(sections, str):
            sections = json.loads(sections)
        try:
            report = builder.build_report(tenant_id, list(sections), schedule["frequency"], now)
        except Exception as exc:  # noqa: BLE001
            log.exception("[scheduled-reports] report %s could not be built", schedule["id"])
            _record_failure(schedule, run["id"], "report_build_failed", type(exc).__name__)
            return "failed"
        report["schedule_name"] = schedule["name"]
        recipients, skipped = resolve_recipients(schedule)
        if not recipients:
            _finish_run(run["id"], "skipped", error="no_recipients", skipped=skipped, snapshot=report)
            execute("""UPDATE report_schedules SET last_run_at = NOW(), last_status = 'skipped',
                              last_error = 'no_recipients', updated_at = NOW() WHERE id = %s""",
                    (schedule["id"],))
            _auto_pause(schedule, "no_recipients", "report_no_recipients")
            return "skipped"
        # The snapshot is stored BEFORE queueing: the mail is rendered from it.
        execute("UPDATE report_schedule_runs SET snapshot = %s::jsonb WHERE id = %s",
                (json.dumps(report), run["id"]))
        queued = 0
        for rcp in recipients:
            msg_id = outbox.enqueue(
                tenant_id, "email", "scheduled_report", rcp["email"],
                {"run_id": run["id"], "recipient_id": rcp["recipient_id"]},
                created_by=SYSTEM_ACTOR,
                dedupe_key=f"report:{run['id']}:{rcp['recipient_id']}",
                ttl_seconds=OUTBOX_TTL_SECONDS)
            # None is a duplicate (a re-driven run: already queued) or a refusal
            # (logged). A duplicate counts as queued; check which it was.
            if msg_id is not None or _already_queued(tenant_id, run["id"], rcp["recipient_id"]):
                queued += 1
            else:
                skipped.append({"recipient": rcp["email"], "kind": rcp["kind"], "reason": "enqueue_failed"})
        if queued == 0:
            _record_failure(schedule, run["id"], "report_build_failed", "enqueue_failed")
            return "failed"
        _finish_run(run["id"], "queued", queued=queued, skipped=skipped)
        execute("""UPDATE report_schedules
                      SET consecutive_failures = 0, last_run_at = NOW(), last_status = 'queued',
                          last_error = NULL, updated_at = NOW()
                    WHERE id = %s AND tenant_id = %s""", (schedule["id"], tenant_id))
        record_event(tenant_id, SYSTEM_ACTOR, "scheduled_report.queued", resource=schedule["id"],
                     details={"schedule_name": schedule["name"], "recipients": queued,
                              "skipped": len(skipped) or None})
        return "queued"
    except Exception as exc:  # noqa: BLE001 - one schedule must not stop the pass
        log.exception("[scheduled-reports] run %s crashed", run.get("id"))
        try:
            _record_failure(schedule, run["id"], "report_build_failed", type(exc).__name__)
        except Exception:  # noqa: BLE001
            log.exception("[scheduled-reports] could not record the failure of run %s", run.get("id"))
        return "failed"


def _already_queued(tenant_id: str, run_id: str, recipient_id: str) -> bool:
    return query_one(
        "SELECT 1 AS x FROM outbound_messages WHERE tenant_id = %s AND dedupe_key = %s",
        (tenant_id, f"report:{run_id}:{recipient_id}")) is not None


# ── Claiming ─────────────────────────────────────────────────────────────────

def claim_next(now: datetime) -> Optional[dict]:
    """Claim ONE due schedule: returns `{"schedule", "run"}` when there is a run
    to make, `{}` when a schedule was handled without one (re-anchored to a new
    time zone, a duplicate period, a cron that cannot advance), and None when
    nothing is due."""
    with transaction() as conn:
        sched = query_one(
            """SELECT * FROM report_schedules
                WHERE enabled AND next_run_at <= %s
                ORDER BY next_run_at LIMIT 1 FOR UPDATE SKIP LOCKED""", (now,), conn=conn)
        if sched is None:
            return None
        tenant_id = sched["tenant_id"]
        tz_name = _tz(tenant_id)
        if sched["anchored_tz"] != tz_name:
            # The tenant moved zones since the next run was computed. Firing at
            # the old wall clock would send at the wrong hour; re-anchor, and
            # do not treat the stale instant as due.
            try:
                nxt = next_run_after(sched["cron_expr"], tz_name, now)
            except Exception:  # noqa: BLE001
                log.exception("[scheduled-reports] cannot re-anchor schedule %s", sched["id"])
                nxt = now + timedelta(hours=1)
            execute("UPDATE report_schedules SET next_run_at = %s, anchored_tz = %s, updated_at = NOW() "
                    "WHERE id = %s", (nxt, tz_name, sched["id"]), conn=conn)
            return {}
        due = sched["next_run_at"]
        try:
            nxt = advance(sched["cron_expr"], tz_name, due, now)
        except Exception as exc:  # noqa: BLE001
            # Never leave `next_run_at` in the past (a hot loop), and never
            # invent a time: say it failed and look again in an hour.
            log.exception("[scheduled-reports] cannot compute the next run of %s", sched["id"])
            execute("""UPDATE report_schedules
                          SET next_run_at = %s, last_status = 'failed', last_error = %s,
                              updated_at = NOW() WHERE id = %s""",
                    (now + timedelta(hours=1), f"next_run_unavailable:{type(exc).__name__}", sched["id"]),
                    conn=conn)
            return {}
        late = now - due > timedelta(hours=catalog.CATCHUP_HOURS[sched["frequency"]])
        run = query_one(
            """INSERT INTO report_schedule_runs (tenant_id, schedule_id, period_key, due_at, status, error, finished_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (schedule_id, period_key) DO NOTHING
            RETURNING *""",
            (tenant_id, sched["id"], local_key(due, tz_name), due,
             "skipped" if late else "building",
             "missed_beyond_catchup_window" if late else None,
             now if late else None), conn=conn)
        execute("UPDATE report_schedules SET next_run_at = %s, updated_at = NOW() WHERE id = %s",
                (nxt, sched["id"]), conn=conn)
        if run is None:
            return {}  # this local period already has its run: do nothing
        if late:
            execute("""UPDATE report_schedules SET last_run_at = NOW(), last_status = 'skipped',
                              last_error = 'missed_beyond_catchup_window' WHERE id = %s""",
                    (sched["id"],), conn=conn)
    if late:
        record_event(tenant_id, SYSTEM_ACTOR, "scheduled_report.skipped", resource=sched["id"],
                     reason="report_missed_window", details={"schedule_name": sched["name"]})
        return {}
    return {"schedule": sched, "run": run}


def recover_stale(now: datetime) -> int:
    """Re-drive runs a dead worker left `building`; give up on the third time."""
    cutoff = now - timedelta(seconds=catalog.STALE_RUN_SECONDS)
    rows = query(
        """UPDATE report_schedule_runs SET attempts = attempts + 1, started_at = %s
            WHERE status = 'building' AND started_at < %s AND attempts < %s
        RETURNING *""", (now, cutoff, catalog.MAX_RUN_ATTEMPTS))
    for run in rows:
        sched = query_one("SELECT * FROM report_schedules WHERE id = %s AND tenant_id = %s",
                          (run["schedule_id"], run["tenant_id"]))
        if sched is None:
            _finish_run(run["id"], "failed", error="schedule_deleted")
            continue
        execute_run(sched, run, now)
    dead = query(
        """SELECT * FROM report_schedule_runs WHERE status = 'building' AND started_at < %s AND attempts >= %s""",
        (cutoff, catalog.MAX_RUN_ATTEMPTS))
    for run in dead:
        sched = query_one("SELECT * FROM report_schedules WHERE id = %s AND tenant_id = %s",
                          (run["schedule_id"], run["tenant_id"]))
        if sched is None:
            _finish_run(run["id"], "failed", error="schedule_deleted")
        else:
            _record_failure(sched, run["id"], "report_build_failed", "interrupted")
    return len(rows) + len(dead)


def process_due(now: Optional[datetime] = None, limit: int = 50) -> int:
    """One pass: recover stale runs, then claim and run every due schedule.
    Returns how many runs were made."""
    now = now or datetime.now(timezone.utc)
    made = recover_stale(now)
    for _ in range(limit):
        claimed = claim_next(now)
        if claimed is None:
            break
        if claimed:
            execute_run(claimed["schedule"], claimed["run"], now)
            made += 1
    return made


# ── Delivery (called by the outbox drain for each queued message) ────────────

class DeliveryRefused(Exception):
    """This message must not be sent any more. `reason` is a stable code that
    ends up on the outbox row."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def render_for_delivery(tenant_id: str, run_id: str, recipient_id: str) -> tuple[str, str]:
    """(subject, html) for one queued message, or `DeliveryRefused`.

    Eligibility is decided AGAIN here, not only when the message was queued:
    the outbox may deliver minutes or hours later (retries), and a person who
    left, was suspended, unsubscribed, or whose schedule was paused or deleted
    in between must not get a company report.
    """
    from backend.config import settings
    from backend.scheduled_reports import render, tokens
    run = query_one("SELECT * FROM report_schedule_runs WHERE id = %s AND tenant_id = %s", (run_id, tenant_id))
    if run is None:
        raise DeliveryRefused("run_missing")
    schedule = query_one("SELECT * FROM report_schedules WHERE id = %s AND tenant_id = %s",
                         (run["schedule_id"], tenant_id))
    if schedule is None:
        raise DeliveryRefused("schedule_deleted")
    if not schedule["enabled"]:
        raise DeliveryRefused("schedule_paused")
    if not run.get("snapshot"):
        raise DeliveryRefused("snapshot_missing")
    deliverable, _ = resolve_recipients(schedule)
    if recipient_id not in {r["recipient_id"] for r in deliverable}:
        raise DeliveryRefused("recipient_not_eligible")
    token = tokens.mint(settings.secret_key, recipient_id)
    base = settings.frontend_url.rstrip("/")
    snapshot = run["snapshot"]
    snapshot.setdefault("schedule_name", schedule["name"])
    return render.render_email(snapshot, schedule_name=schedule["name"],
                               open_url=f"{base}/reportes-programados",
                               unsubscribe_url=f"{base}/reportes-programados/baja?token={token}")
