"""The decisions behind the audit stream, as pure functions.

No database and no HTTP, so each rule can be pinned by a test that a mock cannot
fool: the cursor text, the NDJSON batch, the retry delays, and when a destination
that keeps failing is switched off.

The retry/failure-day rules of outbound webhooks (`backend/webhooks/policy.py`)
are reused where they mean the same thing, and not copied.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Iterable, Optional

from backend.webhooks import policy as webhook_policy

SCHEMA = "stockai.audit.v1"

# One batch is capped by rows (the destination's `batch_size`) AND by bytes, so
# a run of unusually large rows cannot build a body a receiver refuses.
MAX_BATCH_BYTES = 1_000_000
DEFAULT_BATCH_SIZE = 500

# Delay before the next attempt after the 1st, 2nd, ... consecutive failure. A
# batch is never skipped (order is the promise), so unlike a webhook delivery
# there is no "give up on this row": the last delay repeats until the
# destination answers or is switched off by `should_disable`.
BACKOFF_SECONDS: tuple[int, ...] = (10, 30, 60, 300, 900, 2400, 3600)

# Looked at again this soon when the stream is caught up.
IDLE_POLL_SECONDS = 5

DISABLE_AFTER_FAILURE_DAYS = webhook_policy.DISABLE_AFTER_FAILURE_DAYS

REASON_FAILING = "failing_for_days"
REASON_HOST_REFUSED = "host_refused"
REASON_MANUAL = "manual"


# -- Cursor -------------------------------------------------------------------

def format_cursor(xid: int, seq: int) -> str:
    return f"{xid}:{seq}"


def parse_cursor(text: str) -> Optional[tuple[int, int]]:
    """`"<xid>:<seq>"` -> (xid, seq), or None for anything else."""
    if not isinstance(text, str):
        return None
    head, sep, tail = text.partition(":")
    if sep != ":" or not head.isascii() or not tail.isascii():
        return None
    if not head.isdigit() or not tail.isdigit():
        return None
    xid, seq = int(head), int(tail)
    if xid > 2**62 or seq > 2**62:
        return None
    return xid, seq


# -- Batch --------------------------------------------------------------------

def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (datetime, date)) else value


def _context(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {"unreadable": True}
    return raw if raw is not None else {}


def record_for(row: dict, *, kind: str, event: Optional[str], target_type: Optional[str]) -> dict:
    """One stream record. `kind` is `audit` for what the audit trail covers and
    `activity` for the rest of the feed. `action` is the stored name (stable, what
    the activity feed shows); `event` and `target_type` are the audit trail's
    normalised ones, for rows the trail describes."""
    return {
        "schema": SCHEMA,
        "cursor": format_cursor(int(row["stream_xid"]), int(row["stream_seq"])),
        "id": row["id"],
        "at": _iso(row["created_at"]),
        "tenant_id": row["tenant_id"],
        "actor": row["user_id"],
        "action": row["action"],
        "kind": kind,
        "event": event,
        "target_type": target_type,
        "resource": row.get("resource"),
        "status": row.get("status"),
        "context": _context(row.get("context")),
    }


def to_ndjson(records: Iterable[dict], *, max_bytes: int = MAX_BATCH_BYTES) -> tuple[bytes, int]:
    """(body, how many records fit). One compact JSON object per line, `\\n`
    after each, UTF-8. Stops before the line that would pass `max_bytes`, but
    always includes the first record: a lone oversized record is sent whole
    rather than blocking the stream forever."""
    lines: list[bytes] = []
    size = 0
    for rec in records:
        line = json.dumps(rec, default=str, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8") + b"\n"
        if lines and size + len(line) > max_bytes:
            break
        lines.append(line)
        size += len(line)
    return b"".join(lines), len(lines)


# -- Retry and disable --------------------------------------------------------

def next_delay_seconds(consecutive_failures: int) -> int:
    """Seconds before the next attempt, `consecutive_failures` counting the one
    that just happened (>= 1)."""
    n = max(1, consecutive_failures)
    return BACKOFF_SECONDS[min(n, len(BACKOFF_SECONDS)) - 1]


def classify_outcome(status_code: Optional[int], error: Optional[str]) -> str:
    """`success` on a 2xx; anything else retries. Unlike a webhook there is no
    permanent failure: a 401 from the SIEM (an expired token) is fixed by a
    person, and the stream resumes from the same cursor, so waiting is right.
    Only a refused ADDRESS is permanent (see `service.deliver_batch`)."""
    if status_code is not None and 200 <= status_code < 300:
        return webhook_policy.OUTCOME_SUCCESS
    return webhook_policy.OUTCOME_RETRY


advance_failure_days = webhook_policy.advance_failure_days
should_disable = webhook_policy.should_disable
truncate_error = webhook_policy.truncate_error
