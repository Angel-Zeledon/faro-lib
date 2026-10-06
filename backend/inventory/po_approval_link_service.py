"""Decision links for purchase-order approvals: the database side the PYTHON
paths need (schema in `po_approval_link_migrations.py`).

An approver who is not looking at the app gets a message (email, and WhatsApp
where a verified number and a working channel exist) carrying a link. The link
opens a small confirmation page; approving or rejecting there is an explicit
POST, never a GET, so a mail scanner that prefetches the URL decides nothing.

Where the pieces live (docs/rust-migration.md, "Approve or reject from a
message"):

* Issuing at REQUEST time and revoking at DECISION time are here, because
  Python still serves `POST /po/{id}/approval/request` and both decision routes.
* The public page and its decision endpoint, the resend and the revoke routes
  are Rust only (`backend-rs/src/routes/approval_links.rs`). They go through the
  same decision rules: `po_approval_service.decide` in Python,
  `routes/po_approvals.rs::decide_core` in Rust, with `channel="message"`.

What a link is (the whole security model, in one place):

* 256 random bits (`po_confirmation_core.new_token`), only the SHA-256 hash is
  stored, compared in constant time; a malformed string never reaches the table;
* bound to ONE approver, ONE approval request (and so one order) and the scope
  `decide`; it cannot be replayed on another order, tenant or person;
* single use: deciding consumes it, and a decision (by anybody, in the app or
  by message) revokes every other open link of that request;
* expires after `LINK_TTL_HOURS`; re-sending rotates the token (the earlier one
  stops working) and never reopens a link that was already used;
* a used, expired, revoked, unknown or malformed link all answer the same
  neutral 404, so nobody can tell those cases apart or probe for orders.

With `APPROVAL_LINKS_ENABLED` off (the default) nothing here ever runs and an
approval request is exactly what it was before.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.config import settings
from backend.db.connection import query
from backend.inventory import po_confirmation_core as core

log = logging.getLogger(__name__)

# How long an approver has to act on a message. Long enough for a weekend, short
# enough that a forwarded or leaked message stops being a credential. The Rust
# side carries the same number (a unit test compares the two sources).
LINK_TTL_HOURS = 72

CHANNELS = ("email", "whatsapp")


def enabled() -> bool:
    return bool(settings.approval_links_enabled)


def decision_url(token: str) -> str:
    return f"{settings.frontend_url.rstrip('/')}/aprobar/{token}"


def _whatsapp_numbers(tenant_id: str, approver_ids: list[str]) -> dict[str, str]:
    """approver id -> number, only when a message can actually go out: the
    tenant's channel is configured, its plan includes the WhatsApp bot (the
    existing gate; this adds none) and the number is the person's VERIFIED one.
    An unverified number may belong to somebody else; a link is a credential."""
    if not approver_ids:
        return {}
    from backend.entitlements.service import tenant_has_feature
    from backend.notifications import whatsapp as wa
    if not wa.is_configured(tenant_id) or not tenant_has_feature(tenant_id, "whatsapp_bot"):
        return {}
    rows = query(
        """SELECT id, whatsapp_number FROM users
            WHERE tenant_id = %s AND id = ANY(%s)
              AND COALESCE(TRIM(whatsapp_number), '') <> ''
              AND whatsapp_verified_at IS NOT NULL""",
        (tenant_id, approver_ids))
    return {r["id"]: r["whatsapp_number"].strip() for r in rows}


def issue_links(tenant_id: str, po_log_id: str, approval_id: str, approvers: list[dict],
                *, created_by: str) -> list[dict]:
    """Create (or rotate) the links of one approval request.

    One per approver per channel. Returns one dict per link that now carries a
    usable token: `{link_id, approver_id, channel, token, recipient}`. The raw
    token exists only in this return value. A link already USED is left alone
    (no row comes back for it): a decision was taken with it.
    """
    numbers = _whatsapp_numbers(tenant_id, [a["id"] for a in approvers])
    out: list[dict] = []
    for a in approvers:
        wanted = [("email", a.get("email") or "")]
        if a["id"] in numbers:
            wanted.append(("whatsapp", numbers[a["id"]]))
        for channel, recipient in wanted:
            token = core.new_token()
            rows = query(
                """INSERT INTO po_approval_links
                       (tenant_id, po_log_id, approval_id, approver_id, token_hash, channel,
                        expires_at, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, NOW() + make_interval(hours => %s), %s)
                   ON CONFLICT (approval_id, approver_id, channel) DO UPDATE
                      SET token_hash = EXCLUDED.token_hash, issued_at = NOW(),
                          expires_at = EXCLUDED.expires_at, revoked_at = NULL,
                          revoked_by = NULL, revoked_reason = NULL
                    WHERE po_approval_links.used_at IS NULL
                RETURNING id""",
                (tenant_id, po_log_id, approval_id, a["id"], core.hash_token(token), channel,
                 LINK_TTL_HOURS, created_by))
            if rows:
                out.append({"link_id": rows[0]["id"], "approver_id": a["id"], "channel": channel,
                            "token": token, "recipient": recipient})
    return out


def queue_whatsapp_links(tenant_id: str, po_log_id: str, amount: float, issued: list[dict],
                         *, created_by: str) -> int:
    """Hand the WhatsApp links to the outbox: ONLY the link message, no
    free-text commands. Returns how many were queued."""
    from backend.notifications import outbox
    queued = 0
    for i in issued:
        if i["channel"] != "whatsapp":
            continue
        key = f"po_approval_link:{i['link_id']}:{core.hash_token(i['token'])[:12]}"
        mid = outbox.enqueue(
            tenant_id, "whatsapp", "po_approval_link", i["recipient"],
            {"po_log_id": po_log_id, "amount": amount, "decision_token": i["token"],
             "approver_id": i["approver_id"]},
            created_by=created_by, dedupe_key=key,
            ttl_seconds=LINK_TTL_HOURS * 3600)
        if mid:
            queued += 1
    return queued


def revoke_open(tenant_id: str, approval_id: str, *, reason: str,
                by: Optional[str] = None, conn: Optional[Any] = None) -> int:
    """Kill every link of this request that was not used yet. Returns how many.
    `reason` is a code (`decided`, `revoked`), stored for the trail."""
    rows = query(
        """UPDATE po_approval_links
              SET revoked_at = NOW(), revoked_by = %s, revoked_reason = %s
            WHERE tenant_id = %s AND approval_id = %s
              AND used_at IS NULL AND revoked_at IS NULL
        RETURNING id""",
        (by, reason, tenant_id, approval_id), conn=conn)
    return len(rows)
