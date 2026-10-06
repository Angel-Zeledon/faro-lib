"""The message outbox: ask for an email or a WhatsApp message, let the worker
send it.

Why it exists (docs/rust-migration.md, wave 2). The Rust API must never speak
to Resend, SMTP or Twilio: two sender implementations would drift, and the rule
"the transport refuses the made-up `@stockai.demo` trial addresses" has to live
in exactly one place. A service that wants a message sent INSERTS a row into
`outbound_messages`; the `outbox-drain` worker loop claims due rows and calls
the SAME `send_*` functions the Python routes call, then records the outcome.

A row names WHAT to send, never the text: `kind` is an English key registered
in `KINDS` below, `params` is the data that kind needs. The Spanish copy stays
in `notifications/locale.py` and the email templates, as always (CLAUDE.md,
Language). A kind the registry does not know is refused at enqueue and, for a
row that got in some other way, failed at delivery with `unknown_kind`.

Delivery is at-least-once: a worker that dies after the provider accepted a
message but before the row said so will send it again after the lease. The
messages here (a code, a link, a notification) tolerate that; a duplicate is
better than a lost reset code.

Never raises into the caller: `enqueue` returns None and logs when it cannot
write, the same contract as `webhooks.service.emit`. A caller that must KNOW
(a route that answers "sent: true") must not use an outbox; it needs the
synchronous sender.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from backend.db.connection import execute, query, query_one

log = logging.getLogger(__name__)

# Five attempts: now, then +30 s, +2 min, +10 min, +40 min. Past that the row is
# `failed`, with the reason in `last_error` (never silently dropped).
BACKOFF_SECONDS: tuple[int, ...] = (30, 120, 600, 2400)
MAX_ATTEMPTS = len(BACKOFF_SECONDS) + 1
# A claimed row a crashed worker never finished is retried after this.
CLAIM_LEASE_SECONDS = 180
CLAIM_BATCH = 20
DEFAULT_TTL_SECONDS = 3600
MAX_TTL_SECONDS = 7 * 24 * 3600
MAX_ERROR_LENGTH = 300

CHANNELS = ("email", "whatsapp")


class OutboxRefused(Exception):
    """Waiting cannot help: the row fails now, with `reason` as its error."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class OutboxTransient(Exception):
    """The send did not go through; try again on the backoff schedule."""


@dataclass(frozen=True)
class Kind:
    channel: str
    name: str
    # Keys `params` must carry / may carry. Nothing else is accepted.
    required: tuple[str, ...]
    optional: tuple[str, ...]
    send: Callable[[dict], None]


# ── Senders: one per kind, the existing functions, called the way routes call them

def _email_ready(tenant_id: Optional[str]) -> None:
    from backend.notifications import email as email_mod
    if not email_mod.is_configured(tenant_id):
        raise OutboxRefused(email_mod.failure_reason(tenant_id))


def _delivered(ok: bool, tenant_id: Optional[str]) -> None:
    if not ok:
        from backend.notifications import email as email_mod
        raise OutboxTransient(email_mod.failure_reason(tenant_id))


def _user_name(user_id: Optional[str], tenant_id: str) -> str:
    row = query_one("SELECT full_name, email FROM users WHERE id = %s AND tenant_id = %s",
                    (user_id, tenant_id)) if user_id else None
    if not row:
        return ""
    return row.get("full_name") or (row.get("email") or "").split("@")[0] or ""


def _send_verification(row: dict, p: dict) -> None:
    from backend.notifications import email as m
    _email_ready(None)
    _delivered(m.send_verification_email(row["recipient"], p.get("full_name") or "", p["verify_url"]), None)


def _send_password_reset(row: dict, p: dict) -> None:
    from backend.notifications import email as m
    _email_ready(None)
    _delivered(m.send_password_reset_email(row["recipient"], p["reset_url"]), None)


def _send_change_password_code(row: dict, p: dict) -> None:
    from backend.notifications import email as m
    _email_ready(None)
    _delivered(m.send_change_password_code(row["recipient"], p["code"]), None)


def _send_password_reset_otp(row: dict, p: dict) -> None:
    from backend.notifications import email as m
    _email_ready(None)
    _delivered(m.send_password_reset_otp(row["recipient"], p["code"]), None)


def _send_account_setup(row: dict, p: dict) -> None:
    from backend.notifications import email as m
    _email_ready(None)
    _delivered(m.send_account_setup_email(row["recipient"], p.get("full_name") or "", p["setup_url"]), None)


def _amount_text(tenant_id: str, amount: Any) -> str:
    from backend.api.v1.currency import currency_of
    from backend.formatting import money
    return money(float(amount), currency=currency_of(tenant_id))


def _po_reference(tenant_id: str, po_log_id: str) -> str:
    from backend.inventory.roi_service import format_po_number
    row = query_one("SELECT po_number FROM inventory_po_log WHERE id = %s AND tenant_id = %s",
                    (po_log_id, tenant_id)) or {}
    return format_po_number(row.get("po_number"), po_log_id)


def _order_link(po_log_id: str) -> str:
    from backend.inventory.po_approval_service import _order_link
    return _order_link(po_log_id)


def _send_po_approval_request(row: dict, p: dict) -> None:
    from backend.inventory.po_approval_link_service import decision_url
    from backend.notifications import email as m
    tenant = row["tenant_id"]
    _email_ready(tenant)
    token = p.get("decision_token")
    _delivered(m.send_po_approval_request_email(
        to=row["recipient"], approver_name=_user_name(p.get("approver_id"), tenant),
        requester_name=_user_name(p.get("requester_id"), tenant),
        po_ref=_po_reference(tenant, p["po_log_id"]), amount_text=_amount_text(tenant, p["amount"]),
        url=_order_link(p["po_log_id"]), tenant_id=tenant,
        decision_url=decision_url(token) if token else None), tenant)


def _send_po_approval_decision(row: dict, p: dict) -> None:
    from backend.notifications import email as m
    tenant = row["tenant_id"]
    _email_ready(tenant)
    _delivered(m.send_po_approval_decision_email(
        to=row["recipient"], requester_name=_user_name(p.get("requester_id"), tenant),
        decider_name=_user_name(p.get("decider_id"), tenant), approved=bool(p["approved"]),
        po_ref=_po_reference(tenant, p["po_log_id"]), amount_text=_amount_text(tenant, p["amount"]),
        comment=p.get("comment"), url=_order_link(p["po_log_id"]), tenant_id=tenant), tenant)


def _send_scheduled_report(row: dict, p: dict) -> None:
    from backend.notifications import email as m
    from backend.scheduled_reports import service as reports
    tenant = row["tenant_id"]
    _email_ready(tenant)
    try:
        subject, html = reports.render_for_delivery(tenant, p["run_id"], p["recipient_id"])
    except reports.DeliveryRefused as exc:
        raise OutboxRefused(exc.reason) from None
    try:
        m._send(row["recipient"], subject, html, tenant_id=tenant)
    except Exception as exc:  # noqa: BLE001 - transient by default, like every sender
        log.error("Failed to send scheduled report to %s: %s", row["recipient"], exc)
        raise OutboxTransient(m.failure_reason(tenant)) from None


def _send_whatsapp_verification_code(row: dict, p: dict) -> None:
    from backend.notifications import whatsapp as wa
    from backend.notifications.locale import render_es
    tenant = row["tenant_id"]
    if not wa.is_configured(tenant):
        raise OutboxRefused(wa.failure_reason(tenant))
    if not wa.send_whatsapp(row["recipient"], render_es("whatsapp_verification_code", code=p["code"]),
                            tenant_id=tenant):
        raise OutboxTransient(wa.failure_reason(tenant))


def _send_whatsapp_po_approval_link(row: dict, p: dict) -> None:
    """ONLY the link message: no free-text commands, nothing to answer."""
    from backend.inventory.po_approval_link_service import LINK_TTL_HOURS, decision_url
    from backend.notifications import whatsapp as wa
    from backend.notifications.locale import render_es
    tenant = row["tenant_id"]
    if not wa.is_configured(tenant):
        raise OutboxRefused(wa.failure_reason(tenant))
    body = render_es("po_approval_link_whatsapp", ref=_po_reference(tenant, p["po_log_id"]),
                     amount=_amount_text(tenant, p["amount"]), url=decision_url(p["decision_token"]),
                     hours=LINK_TTL_HOURS)
    # The bot's own gate (`whatsapp_bot`), not a new one: a plan without the bot
    # gets the email link only.
    if not wa.send_whatsapp(row["recipient"], body, tenant_id=tenant, plan_gated=True):
        raise OutboxTransient(wa.failure_reason(tenant))


def _kind(channel: str, name: str, required: tuple, optional: tuple, fn) -> Kind:
    return Kind(channel, name, required, optional, lambda row, _fn=fn: _fn(row, row["params"]))


KINDS: dict[tuple[str, str], Kind] = {(k.channel, k.name): k for k in [
    _kind("email", "verification", ("verify_url",), ("full_name",), _send_verification),
    _kind("email", "password_reset", ("reset_url",), (), _send_password_reset),
    _kind("email", "change_password_code", ("code",), (), _send_change_password_code),
    _kind("email", "password_reset_otp", ("code",), (), _send_password_reset_otp),
    _kind("email", "account_setup", ("setup_url",), ("full_name",), _send_account_setup),
    _kind("email", "po_approval_request", ("po_log_id", "amount"),
          ("approver_id", "requester_id", "decision_token"), _send_po_approval_request),
    _kind("email", "po_approval_decision", ("po_log_id", "amount", "approved"),
          ("requester_id", "decider_id", "comment"), _send_po_approval_decision),
    _kind("email", "scheduled_report", ("run_id", "recipient_id"), (), _send_scheduled_report),
    _kind("whatsapp", "verification_code", ("code",), (), _send_whatsapp_verification_code),
    _kind("whatsapp", "po_approval_link", ("po_log_id", "amount", "decision_token"),
          ("approver_id",), _send_whatsapp_po_approval_link),
]}


# ── Validation and enqueue ───────────────────────────────────────────────────

def check_params(channel: str, kind: str, params: dict) -> Optional[str]:
    """Why this request is not acceptable, or None. Pure."""
    spec = KINDS.get((channel, kind))
    if spec is None:
        return "unknown_kind"
    if not isinstance(params, dict):
        return "params_not_an_object"
    missing = [k for k in spec.required if params.get(k) is None]
    if missing:
        return f"missing_param:{missing[0]}"
    extra = [k for k in params if k not in spec.required and k not in spec.optional]
    if extra:
        return f"unexpected_param:{sorted(extra)[0]}"
    return None


def enqueue(tenant_id: str, channel: str, kind: str, recipient: str, params: dict, *,
            created_by: Optional[str] = None, dedupe_key: Optional[str] = None,
            ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Optional[str]:
    """Queue one message. Returns its id; None when it was a duplicate of a
    `dedupe_key` already queued, or when it could not be queued (logged)."""
    problem = check_params(channel, kind, params)
    recipient = (recipient or "").strip()
    if problem is None and not recipient:
        problem = "recipient_missing"
    if problem is not None:
        log.error("[outbox] refused %s/%s for tenant %s: %s", channel, kind, tenant_id, problem)
        return None
    ttl = max(1, min(int(ttl_seconds), MAX_TTL_SECONDS))
    try:
        row = query_one(
            """INSERT INTO outbound_messages
                   (tenant_id, channel, kind, recipient, params, created_by, dedupe_key,
                    next_attempt_at, expires_at)
               VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, NOW(), NOW() + (%s || ' seconds')::interval)
               ON CONFLICT (tenant_id, dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
            RETURNING id""",
            (tenant_id, channel, kind, recipient, json.dumps(params, separators=(",", ":")),
             created_by, dedupe_key, str(ttl)))
        return row["id"] if row else None
    except Exception:  # noqa: BLE001 - the caller's action goes on
        log.error("[outbox] could not queue %s/%s for tenant %s", channel, kind, tenant_id, exc_info=True)
        return None


# ── Draining ─────────────────────────────────────────────────────────────────

def truncate_error(text: object) -> str:
    return str(text).replace("\x00", "")[:MAX_ERROR_LENGTH]


def next_delay_seconds(attempts_done: int) -> Optional[int]:
    """Seconds before the next attempt, or None when attempts are spent."""
    if attempts_done < 1 or attempts_done >= MAX_ATTEMPTS:
        return None
    return BACKOFF_SECONDS[attempts_done - 1]


def expire_overdue() -> int:
    """Give up on rows nobody could deliver in time and scrub their params."""
    rows = query(
        """UPDATE outbound_messages
              SET status = 'abandoned', params = '{}'::jsonb, next_attempt_at = NULL,
                  last_error = COALESCE(last_error, 'expired')
            WHERE status = 'pending' AND expires_at <= NOW()
        RETURNING id""")
    return len(rows)


def claim_due(limit: int = CLAIM_BATCH) -> list[dict]:
    """Take due rows. Claiming counts the attempt and pushes `next_attempt_at`
    out by a lease, so a worker that dies mid-send has its rows picked up again
    later and two workers never take the same row."""
    rows = query(
        """UPDATE outbound_messages m
              SET attempts = m.attempts + 1,
                  last_attempt_at = NOW(),
                  next_attempt_at = NOW() + (%s || ' seconds')::interval
            WHERE m.id IN (
                SELECT id FROM outbound_messages
                 WHERE status = 'pending' AND next_attempt_at <= NOW() AND expires_at > NOW()
                 ORDER BY next_attempt_at
                 LIMIT %s
                 FOR UPDATE SKIP LOCKED)
        RETURNING m.*""",
        (str(CLAIM_LEASE_SECONDS), limit))
    return sorted(rows, key=lambda r: r["created_at"])


def _finish(row_id: str, status: str, error: Optional[str]) -> None:
    sent = status == "sent"
    execute(
        """UPDATE outbound_messages
              SET status = %s, params = '{}'::jsonb, next_attempt_at = NULL, last_error = %s,
                  sent_at = CASE WHEN %s THEN NOW() ELSE sent_at END
            WHERE id = %s""",
        (status, truncate_error(error) if error else None, sent, row_id))


def deliver_one(row: dict) -> str:
    """Send one claimed row and record what happened. Returns the new status."""
    spec = KINDS.get((row["channel"], row["kind"]))
    if spec is None:
        _finish(row["id"], "failed", "unknown_kind")
        return "failed"
    problem = check_params(row["channel"], row["kind"], row["params"])
    if problem is not None:
        _finish(row["id"], "failed", problem)
        return "failed"
    if row["channel"] == "email":
        # A trial login is a made-up address on a TLD that does not exist; a
        # provider would only bounce it against our sender. Said out loud, once.
        from backend.trial.service import is_trial_email
        if is_trial_email(row["recipient"]):
            _finish(row["id"], "abandoned", "trial_address")
            return "abandoned"
    try:
        spec.send(row)
    except OutboxRefused as exc:
        _finish(row["id"], "failed", exc.reason)
        return "failed"
    except Exception as exc:  # noqa: BLE001 - transient by default
        delay = next_delay_seconds(int(row["attempts"]))
        error = exc.args[0] if isinstance(exc, OutboxTransient) and exc.args else type(exc).__name__
        if delay is None:
            _finish(row["id"], "failed", error)
            return "failed"
        execute(
            """UPDATE outbound_messages
                  SET last_error = %s, next_attempt_at = NOW() + (%s || ' seconds')::interval
                WHERE id = %s""",
            (truncate_error(error), str(delay), row["id"]))
        return "pending"
    _finish(row["id"], "sent", None)
    return "sent"


def process_due(limit: int = CLAIM_BATCH,
                deliver: Callable[[dict], str] = deliver_one) -> int:
    """Expire what is overdue, then claim and deliver one batch. Returns how
    many rows were attempted."""
    expire_overdue()
    rows = claim_due(limit)
    for row in rows:
        try:
            deliver(row)
        except Exception:  # noqa: BLE001 - one bad row must not stall the batch
            log.error("[outbox] message %s crashed", row.get("id"), exc_info=True)
    return len(rows)
