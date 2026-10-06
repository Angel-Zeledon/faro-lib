"""Continuous audit export: every new `activity_logs` row, delivered to the
tenant's HTTPS endpoint at least once, in order, with a cursor.

WHAT IS PROMISED (and what is not)

* **At least once.** The cursor advances only after the destination answered 2xx,
  and only by the rows that were in that request. A timeout after the receiver
  processed the batch resends it, so the receiver dedupes on the record `id`.
* **In order, per tenant.** Records are ordered by `(stream_xid, stream_seq)`
  (see `migrations.py`), and one batch is in flight per tenant at a time (a
  lease). Order is by the writing transaction, which can differ from the
  wall-clock order of two writers that overlapped; it is stable, the same on
  every replay, and each record carries `at` for the wall-clock reading.
* **Nothing is ever lost behind the cursor.** Only rows whose transaction has
  finished (`stream_xid < xmin` of the reading snapshot) are read, so a slow
  writer cannot commit a row BEHIND a cursor that has already passed it. The
  cost: one long-open transaction ANYWHERE in the database (an idle-in-
  transaction session, a stuck prepared transaction) holds every stream back
  until it ends. That shows up as lag in the status, never as a gap.
* **A stream never skips.** A failing destination is retried with backoff from
  the same cursor. After `DISABLE_AFTER_FAILURE_DAYS` failing days with no
  success in between it is switched off and the account is told
  (`audit_stream.auto_disabled`); the cursor is kept, so re-enabling resumes
  exactly where it stopped. Nothing is dropped while it is off: the rows stay in
  `activity_logs`.
* Rows written before the feature existed are not streamed (the CSV export
  covers history).

WHY A PYTHON WORKER LOOP, NOT RUST (docs/rust-migration.md): delivery has to
POST through the webhook SSRF guard (`backend/datasources/network.py`: resolve,
refuse private/metadata addresses, connect to the CHECKED address, never
follow redirects), which exists once, in Python, and the webhook retry and
signing code is reused rather than ported a second time. The configuration and
cursor routes are Rust; they only write `audit_streams`. The at-least-once /
no-loss proof does not depend on the language: it rests on the compare-and-set
on the cursor and the xmin gate below, both plain SQL.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from backend.audit.catalog import LEGACY, PREFIX, ROUTES
from backend.audit_stream import policy
from backend.db.connection import execute, query, query_one
from backend.webhooks import service as webhook_service
from backend.webhooks import signing
from backend.webhooks.service import Attempt

log = logging.getLogger(__name__)

CLAIM_BATCH = 10
LEASE_SECONDS = 120
LOG_KEEP_ROWS = 1000
DELIVER_THREADS = 4

_WATERMARK_SQL = "(SELECT pg_snapshot_xmin(pg_current_snapshot())::text::bigint)"


# -- Classifying a stored action ---------------------------------------------

def _build_lookup() -> dict[str, tuple[str, str]]:
    """stored action -> (normalised event, target type), for what the audit
    trail describes (catalogued routes and the legacy events it maps)."""
    out: dict[str, tuple[str, str]] = {}
    for route in ROUTES.values():
        out[PREFIX + route.action] = (route.action, route.target_type)
    for stored, (target_type, name) in LEGACY.items():
        out[stored] = (name, target_type)
    return out


_AUDIT_LOOKUP = _build_lookup()


def classify(action: str) -> tuple[str, Optional[str], Optional[str]]:
    """(kind, event, target_type) for a stored action."""
    hit = _AUDIT_LOOKUP.get(action)
    if hit is None:
        return "activity", None, None
    return "audit", hit[0], hit[1]


# -- Reading ------------------------------------------------------------------

def read_batch(tenant_id: str, cursor: tuple[int, int], limit: int) -> list[dict]:
    """The next rows after `cursor` whose transaction has finished, in order."""
    return query(
        f"""SELECT id, tenant_id, user_id, action, resource, context, status,
                   created_at, stream_xid, stream_seq
              FROM activity_logs
             WHERE tenant_id = %s AND stream_xid IS NOT NULL
               AND (stream_xid, stream_seq) > (%s, %s)
               AND stream_xid < {_WATERMARK_SQL}
             ORDER BY stream_xid, stream_seq
             LIMIT %s""",
        (tenant_id, cursor[0], cursor[1], limit))


def batch_records(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        kind, event, target_type = classify(row["action"])
        out.append(policy.record_for(row, kind=kind, event=event, target_type=target_type))
    return out


# -- Claiming -----------------------------------------------------------------

def claim_due(limit: int = CLAIM_BATCH, *, tenant_id: Optional[str] = None) -> list[dict]:
    """Take destinations that are due. One UPDATE both selects (SKIP LOCKED) and
    leases, so two workers never hold the same tenant, and a worker that dies
    mid-delivery has the tenant picked up again when the lease runs out.
    `tenant_id` narrows the claim to one tenant (tests, operators)."""
    token = uuid.uuid4().hex
    return query(
        """UPDATE audit_streams s
              SET lease_until = NOW() + (%s || ' seconds')::interval,
                  lease_token = %s
            WHERE s.tenant_id IN (
                SELECT tenant_id FROM audit_streams
                 WHERE (enabled OR test_requested_at IS NOT NULL)
                   AND next_attempt_at <= NOW()
                   AND (lease_until IS NULL OR lease_until < NOW())
                   AND (%s::text IS NULL OR tenant_id = %s)
                 ORDER BY next_attempt_at
                 LIMIT %s
                 FOR UPDATE SKIP LOCKED)
        RETURNING s.*""",
        (str(LEASE_SECONDS), token, tenant_id, tenant_id, limit))


def _release(stream: dict, *, delay_seconds: int) -> None:
    execute(
        """UPDATE audit_streams
              SET lease_until = NULL, lease_token = NULL,
                  next_attempt_at = NOW() + (%s || ' seconds')::interval
            WHERE tenant_id = %s AND lease_token = %s""",
        (str(delay_seconds), stream["tenant_id"], stream["lease_token"]))


# -- The delivery log ---------------------------------------------------------

def _log_attempt(tenant_id: str, *, kind: str, status: str, records: int = 0,
                 size: int = 0, first_cursor: Optional[str] = None,
                 last_cursor: Optional[str] = None, status_code: Optional[int] = None,
                 error: Optional[str] = None, duration_ms: Optional[int] = None) -> None:
    try:
        execute(
            """INSERT INTO audit_stream_deliveries
                   (tenant_id, kind, status, records, bytes, first_cursor, last_cursor,
                    status_code, error, duration_ms)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (tenant_id, kind, status, records, size, first_cursor, last_cursor,
             status_code, policy.truncate_error(error) if error else None, duration_ms))
        execute(
            """DELETE FROM audit_stream_deliveries
                WHERE tenant_id = %s AND id IN (
                    SELECT id FROM audit_stream_deliveries WHERE tenant_id = %s
                     ORDER BY created_at DESC, id OFFSET %s)""",
            (tenant_id, tenant_id, LOG_KEEP_ROWS))
    except Exception:  # noqa: BLE001 - the log must never fail the delivery path
        log.error("[audit-stream] could not write the delivery log tenant=%s", tenant_id,
                  exc_info=True)


# -- Sending ------------------------------------------------------------------

def _headers(stream: dict, body: bytes, *, first_cursor: str, last_cursor: str,
             records: int, delivery_id: str) -> dict[str, str]:
    return {
        "Content-Type": "application/x-ndjson",
        "User-Agent": "StockAI-AuditStream/1",
        "X-StockAI-Stream": "audit",
        "X-StockAI-Delivery": delivery_id,
        "X-StockAI-Batch-Records": str(records),
        "X-StockAI-Batch-First": first_cursor,
        "X-StockAI-Batch-Last": last_cursor,
        "X-StockAI-Signature": signing.signature_header(
            stream["secret"], int(time.time()), body),
    }


# The seam tests replace: (url, body, headers) -> Attempt.
Sender = Callable[[str, bytes, dict[str, str]], Attempt]


def deliver_stream(stream: dict, *, send: Optional[Sender] = None) -> str:
    """One turn for a leased destination: a pending test, else one batch.
    Returns what happened: `idle`, `delivered`, `failed`, `superseded`, `test`,
    `disabled`."""
    send = send or webhook_service._send
    tenant_id = stream["tenant_id"]

    if stream.get("test_requested_at") is not None:
        _deliver_test(stream, send)
        if not stream["enabled"]:
            _release(stream, delay_seconds=86400)
            return "test"
        _release(stream, delay_seconds=0)
        return "test"

    cursor = (int(stream["cursor_xid"]), int(stream["cursor_seq"]))
    rows = read_batch(tenant_id, cursor, int(stream["batch_size"]))
    if not rows:
        _release(stream, delay_seconds=policy.IDLE_POLL_SECONDS)
        return "idle"
    records = batch_records(rows)
    body, count = policy.to_ndjson(records)
    rows = rows[:count]
    first_cursor = records[0]["cursor"]
    last_cursor = records[count - 1]["cursor"]
    last = (int(rows[-1]["stream_xid"]), int(rows[-1]["stream_seq"]))

    started = time.monotonic()
    attempt = send(stream["url"], body, _headers(
        stream, body, first_cursor=first_cursor, last_cursor=last_cursor,
        records=count, delivery_id=uuid.uuid4().hex))
    duration_ms = int((time.monotonic() - started) * 1000)
    outcome = policy.classify_outcome(attempt.status_code, attempt.error)

    if outcome == "success":
        advanced = query_one(
            """UPDATE audit_streams
                  SET cursor_xid = %s, cursor_seq = %s,
                      consecutive_failures = 0, failure_days = 0, last_failure_on = NULL,
                      last_error = NULL, last_status_code = %s,
                      last_attempt_at = NOW(), last_success_at = NOW(),
                      delivered_records = delivered_records + %s,
                      next_attempt_at = NOW() + (%s || ' seconds')::interval,
                      lease_until = NULL, lease_token = NULL, updated_at = NOW()
                WHERE tenant_id = %s AND lease_token = %s
                  AND cursor_xid = %s AND cursor_seq = %s
            RETURNING tenant_id""",
            (last[0], last[1], attempt.status_code, count,
             # More waiting (a full batch, or the byte cap cut it): go again now.
             "0" if (len(records) >= int(stream["batch_size"]) or count < len(records))
             else str(policy.IDLE_POLL_SECONDS),
             tenant_id, stream["lease_token"], cursor[0], cursor[1]))
        if advanced is None:
            # A replay moved the cursor (or the lease was lost) while this was in
            # flight. The rows were sent but the position they would set is no
            # longer the truth: drop it and read again from the cursor as it is.
            _log_attempt(tenant_id, kind="batch", status="superseded", records=count,
                         size=len(body), first_cursor=first_cursor, last_cursor=last_cursor,
                         status_code=attempt.status_code, duration_ms=duration_ms)
            _release(stream, delay_seconds=0)
            return "superseded"
        _log_attempt(tenant_id, kind="batch", status="delivered", records=count,
                     size=len(body), first_cursor=first_cursor, last_cursor=last_cursor,
                     status_code=attempt.status_code, duration_ms=duration_ms)
        return "delivered"

    error = attempt.error or (f"http_{attempt.status_code}"
                              if attempt.status_code is not None else "unknown")
    _log_attempt(tenant_id, kind="batch", status="failed", records=count, size=len(body),
                 first_cursor=first_cursor, last_cursor=last_cursor,
                 status_code=attempt.status_code, error=error, duration_ms=duration_ms)
    return "disabled" if _register_failure(stream, attempt, error) else "failed"


def _deliver_test(stream: dict, send: Sender) -> None:
    tenant_id = stream["tenant_id"]
    record = {
        "schema": policy.SCHEMA, "cursor": None, "id": f"test_{uuid.uuid4().hex[:12]}",
        "at": datetime.now(timezone.utc).isoformat(), "tenant_id": tenant_id,
        "actor": "system", "action": "audit_stream.test", "kind": "test",
        "event": "audit_stream.test", "target_type": None, "resource": None,
        "status": "success", "context": {},
    }
    body, _ = policy.to_ndjson([record])
    started = time.monotonic()
    attempt = send(stream["url"], body, _headers(
        stream, body, first_cursor="", last_cursor="", records=1,
        delivery_id=uuid.uuid4().hex))
    ok = policy.classify_outcome(attempt.status_code, attempt.error) == "success"
    _log_attempt(tenant_id, kind="test", status="delivered" if ok else "failed", records=1,
                 size=len(body), status_code=attempt.status_code,
                 error=None if ok else (attempt.error or f"http_{attempt.status_code}"),
                 duration_ms=int((time.monotonic() - started) * 1000))
    # Cleared only by the lease holder; a test never touches the failure counters.
    execute("UPDATE audit_streams SET test_requested_at = NULL "
            "WHERE tenant_id = %s AND lease_token = %s",
            (tenant_id, stream["lease_token"]))


def _register_failure(stream: dict, attempt: Attempt, error: str) -> bool:
    """Count the failure, schedule the retry, and switch the stream off when it
    has failed on enough different days (or the address is refused outright).
    Returns True when this call disabled it."""
    today = datetime.now(timezone.utc).date()
    failures = int(stream["consecutive_failures"] or 0) + 1
    days, last_on = policy.advance_failure_days(
        int(stream["failure_days"] or 0), stream["last_failure_on"], today)
    if attempt.permanent:
        reason: Optional[str] = policy.REASON_HOST_REFUSED
    elif policy.should_disable(days):
        reason = policy.REASON_FAILING
    else:
        reason = None
    disable = reason is not None
    changed = query_one(
        """UPDATE audit_streams
              SET consecutive_failures = %s, failure_days = %s, last_failure_on = %s,
                  last_error = %s, last_status_code = %s, last_attempt_at = NOW(),
                  next_attempt_at = NOW() + (%s || ' seconds')::interval,
                  enabled = CASE WHEN %s THEN FALSE ELSE enabled END,
                  disabled_at = CASE WHEN %s THEN NOW() ELSE disabled_at END,
                  disabled_reason = CASE WHEN %s THEN %s ELSE disabled_reason END,
                  lease_until = NULL, lease_token = NULL, updated_at = NOW()
            WHERE tenant_id = %s AND lease_token = %s
        RETURNING tenant_id""",
        (failures, days, last_on, policy.truncate_error(error), attempt.status_code,
         str(policy.next_delay_seconds(failures)), disable, disable, disable, reason,
         stream["tenant_id"], stream["lease_token"]))
    if changed is None or not disable:
        return False
    from backend.activity.events import record_event
    record_event(
        stream["tenant_id"], "system", "audit_stream.auto_disabled",
        resource=stream["tenant_id"],
        reason=("audit_stream_host_refused" if reason == policy.REASON_HOST_REFUSED
                else "audit_stream_failing_for_days"),
        reason_params={"days": days},
        details={"host": urlsplit(stream["url"]).hostname})
    log.warning("[audit-stream] disabled tenant=%s reason=%s after %d failing day(s)",
                stream["tenant_id"], reason, days)
    return True


# -- The loop -----------------------------------------------------------------

def _deliver_safely(stream: dict) -> str:
    try:
        return deliver_stream(stream)
    except Exception as exc:  # noqa: BLE001 - one tenant must not stall the others
        log.error("[audit-stream] delivery crashed tenant=%s", stream.get("tenant_id"),
                  exc_info=True)
        try:
            _register_failure(stream, Attempt(None, type(exc).__name__), type(exc).__name__)
        except Exception:  # noqa: BLE001
            log.error("[audit-stream] could not record the crash tenant=%s",
                      stream.get("tenant_id"), exc_info=True)
        return "failed"


_pool_lock = threading.Lock()
_pool: Optional[ThreadPoolExecutor] = None


def _executor() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=DELIVER_THREADS,
                                       thread_name_prefix="audit-stream")
        return _pool


def process_due(limit: int = CLAIM_BATCH) -> int:
    """Claim and deliver one round (tenants in parallel, each tenant alone).
    Returns how many destinations were worked on."""
    streams = claim_due(limit)
    if not streams:
        return 0
    list(_executor().map(_deliver_safely, streams))
    return len(streams)


def run_loop(poll_seconds: float = 2.0) -> None:
    """The worker thread body (backend/workers/worker.py)."""
    log.info("Audit stream loop started")
    while True:
        handled = 0
        try:
            handled = process_due()
        except Exception:  # noqa: BLE001
            log.error("Audit stream loop error", exc_info=True)
        if handled == 0:
            time.sleep(poll_seconds)
