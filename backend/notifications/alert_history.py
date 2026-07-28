"""
The alerts Faro sent, read back from inside the app.

The 08:00 UTC loop already writes one `activity_logs` row per alert per
recipient, carrying `status` ('success' / 'failed') and a `context` blob — that
is what `record_notification_delivery` (inventory/service.py) and `_record`
(freshness_service.py) exist for. Until now nothing read those rows back, so the
only copy of a stockout digest was the email itself: delete the mail and the
information was gone, and a delivery that failed was visible to nobody.

This module is the read side. No new store, no new write path — it reads the
rows the loop already writes.

Two things it deliberately does NOT do:

* It does not invent a "read" flag on the alert rows. Those rows belong to the
  delivery record and are written by a background thread; mutating them from a
  request would mean the audit trail and the UI state share a column. Instead
  the user's own "I opened the bell" moment is itself an activity row
  (`MARK_READ_ACTION`), and unread is derived: alerts newer than that marker.
  Nothing is guessed — with no marker every alert is unread, which is true.

* It does not hide failures. A group where every send failed is returned with
  status 'failed' and the machine reason (`not_configured` / `transport_error`)
  the transport reported, because "no alerts" must never be readable as
  "nothing went wrong".

Fan-out grouping: one alert to three admins over two channels is six rows, and a
bell listing it six times is noise. Consecutive rows of the same KIND (not the
same action — email and WhatsApp are two channels of one alert) within
`_FANOUT_WINDOW_SECONDS` are one entry whose delivered/failed counts are the
per-recipient outcomes — which is exactly what makes a partial failure visible
("delivered to 2, failed for 1") instead of averaged away.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.activity.service import log_action
from backend.db.connection import query, query_one

log = logging.getLogger(__name__)

# ── What counts as an alert ───────────────────────────────────────────────────
# Every action here is a message Faro decided to send on its own, on a schedule,
# to a recipient who was not looking at the app. That is the whole set whose
# history vanishes with the email.
#
# Deliberately excluded: `po_sent_to_suppliers` and `inventory_alert_test_fire`.
# Both are foreground actions the user triggered and whose outcome the UI
# already reports in the response to that same click; they belong in the full
# activity log (/me/activity), not in the alert bell.
ALERT_ACTIONS: dict[str, str] = {
    "inventory_alert_email":          "stockout_digest",
    "inventory_alert_whatsapp":       "stockout_digest",
    "supplier_lead_time_alert_email": "supplier_lead_time",
    "data_freshness_reminder_email":  "data_freshness",
    "data_freshness_reminder_whatsapp": "data_freshness",
    "monthly_roi_email":              "monthly_roi",
}

# The user's own "I have seen the bell" marker. Not an alert (it is not in
# ALERT_ACTIONS), so it can never mark itself read.
MARK_READ_ACTION = "alerts_marked_read"

# Rows of the same action closer together than this are one fan-out. The loop
# mails every recipient of a tenant back to back; 15 minutes is far longer than
# that takes and far shorter than the gap between two runs of the same alert
# (daily at the tightest).
_FANOUT_WINDOW_SECONDS = 900

# Raw rows pulled per grouped entry requested. A tenant with more alert
# recipients than this may get fewer than `limit` entries in one page — which is
# a short list, never a wrong one.
_ROWS_PER_ENTRY = 20
_MAX_ROWS = 400

# Which context keys are meaningful per kind. Whitelisted rather than passed
# through so the payload cannot start leaking whatever a future context adds,
# and so the frontend knows the exact params its i18n string interpolates.
# `recipient` is never included: the bell is per tenant and an address is PII
# that adds nothing to "what was this about and when".
_DETAIL_KEYS: dict[str, tuple[str, ...]] = {
    "stockout_digest":    ("critical", "warning"),
    "supplier_lead_time": ("suppliers",),
    "data_freshness":     ("sales_age_days", "stock_age_days"),
    "monthly_roi":        ("month",),
}


def ensure_index() -> None:
    """Index the tenant-wide, newest-first read this module does.

    `activity_logs` only ships an index on (user_id, created_at); the bell asks
    a different question — every alert of the TENANT — because a digest that
    reached one admin and failed for another has to be visible as one event with
    both outcomes.
    """
    from backend.db.connection import execute
    execute(
        "CREATE INDEX IF NOT EXISTS idx_activity_tenant_time "
        "ON activity_logs(tenant_id, created_at DESC)"
    )


def _as_utc(ts: Optional[datetime]) -> Optional[datetime]:
    """TIMESTAMPTZ columns can come back naive from the driver; they are UTC."""
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _channel_of(action: str, context: dict) -> str:
    """'email' / 'whatsapp'. The context carries it; the action name is the
    fallback for a row written before that key existed."""
    channel = context.get("channel")
    if channel in ("email", "whatsapp"):
        return str(channel)
    return "whatsapp" if action.endswith("_whatsapp") else "email"


def _details(kind: str, context: dict) -> dict[str, Any]:
    return {
        k: context[k]
        for k in _DETAIL_KEYS.get(kind, ())
        if context.get(k) is not None
    }


def _last_read_at(tenant_id: str, user_id: str) -> Optional[datetime]:
    row = query_one(
        """SELECT MAX(created_at) AS last_at FROM activity_logs
           WHERE tenant_id = %s AND user_id = %s AND action = %s""",
        (tenant_id, user_id, MARK_READ_ACTION),
    )
    return _as_utc(row.get("last_at")) if row else None


def _group(rows: list[dict]) -> list[dict]:
    """Collapse one fan-out (same action, adjacent in time) into one entry.

    `rows` must be newest first. Returns entries newest first, each carrying the
    per-recipient outcome split — the part that makes a partial delivery
    failure readable instead of rounded to "sent".
    """
    entries: list[dict] = []
    for row in rows:
        action = row["action"]
        kind = ALERT_ACTIONS.get(action)
        if kind is None:  # pragma: no cover - the query already filters
            continue
        created = _as_utc(row["created_at"])
        context = row.get("context") or {}
        delivered = row.get("status") == "success"

        # Compared against the group's newest row, not the previous one: a
        # chain of rows each just inside the window would otherwise merge two
        # different runs into one unbounded entry.
        current = entries[-1] if entries else None
        same_fanout = (
            current is not None
            and current["kind"] == kind
            and current["created_at"] - created <= timedelta(seconds=_FANOUT_WINDOW_SECONDS)
        )
        if not same_fanout:
            current = {
                "id":              row["id"],
                "kind":            kind,
                "created_at":      created,
                "channels":        set(),
                "delivered_count": 0,
                "failed_count":    0,
                "failure_reason":  None,
                # Taken from the newest row of the fan-out: every recipient of
                # one run gets the same numbers, and the newest is the one whose
                # timestamp the entry shows.
                "details":         _details(kind, context),
            }
            entries.append(current)

        current["channels"].add(_channel_of(action, context))
        if delivered:
            current["delivered_count"] += 1
        else:
            current["failed_count"] += 1
            if current["failure_reason"] is None and context.get("reason"):
                current["failure_reason"] = str(context["reason"])
    return entries


def _finalize(entry: dict, last_read_at: Optional[datetime]) -> dict:
    """Public shape of one entry. `status` is the honest three-way outcome, not
    a boolean: a digest that reached one admin and not another is neither
    'delivered' nor 'failed'."""
    delivered, failed = entry["delivered_count"], entry["failed_count"]
    if failed == 0:
        status = "delivered"
    elif delivered == 0:
        status = "failed"
    else:
        status = "partial"

    created = entry["created_at"]
    channels = sorted(entry["channels"])
    return {
        "id":              entry["id"],
        "kind":            entry["kind"],
        "created_at":      created.isoformat() if created else None,
        "channel":         channels[0] if len(channels) == 1 else "mixed",
        "status":          status,
        "delivered_count": delivered,
        "failed_count":    failed,
        "failure_reason":  entry["failure_reason"],
        "details":         entry["details"],
        "unread":          last_read_at is None or (created is not None and created > last_read_at),
    }


def list_alerts(tenant_id: str, user_id: str, limit: int = 20) -> dict:
    """The tenant's last `limit` alerts, newest first, with unread derived for
    `user_id`.

    Tenant-wide and not user-wide on purpose: an alert is one event that fans
    out to several recipients, and scoping it to the caller would hide a
    delivery that failed for a colleague — the exact silence this feature
    exists to break.
    """
    rows = query(
        """SELECT id, action, context, status, created_at
           FROM activity_logs
           WHERE tenant_id = %s AND action IN %s
           ORDER BY created_at DESC
           LIMIT %s""",
        (tenant_id, tuple(ALERT_ACTIONS), min(limit * _ROWS_PER_ENTRY, _MAX_ROWS)),
    )

    last_read_at = _last_read_at(tenant_id, user_id)
    entries = [_finalize(e, last_read_at) for e in _group([dict(r) for r in rows])][:limit]
    return {
        "items":        entries,
        "unread_count": sum(1 for e in entries if e["unread"]),
        "last_read_at": last_read_at.isoformat() if last_read_at else None,
        "limit":        limit,
    }


def mark_read(tenant_id: str, user_id: str) -> dict:
    """Record that this user has seen the bell, and report the state that
    leaves. Writing a marker row (instead of stamping the alert rows) keeps the
    delivery record immutable and keeps the flag per user, which is what it is:
    two admins read their alerts at different times."""
    log_action(tenant_id, user_id, MARK_READ_ACTION)
    read_at = _last_read_at(tenant_id, user_id)
    return {
        "last_read_at": read_at.isoformat() if read_at else None,
        "unread_count": 0,
    }
