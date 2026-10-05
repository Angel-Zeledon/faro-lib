"""The per-tenant inbound address, its sender allow-list and the message log.

The address is `sales+<token>@<domain>`. The token is the secret half: it
routes a message to a tenant, so whoever knows it can mail that tenant's inbox.
Regenerating it overwrites the stored token, which is exactly what makes the old
address stop working (nothing keeps a list of old tokens on purpose).
"""
from __future__ import annotations

import re
import secrets
from typing import Optional

from backend.db.connection import execute, query, query_one, _json
from backend.errors import AppError
from backend.service_config.resolver import effective
from backend.utils.ids import generate_id

LOCAL_PART = "sales"
MAX_ALLOWED_SENDERS = 20
MESSAGES_SHOWN = 20

OUTCOME_INGESTED = "ingested"
OUTCOME_NEEDS_REVIEW = "needs_review"
OUTCOME_REJECTED = "rejected"
OUTCOME_PROCESSING = "processing"

_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def inbound_domain() -> str:
    return (effective(None).inbound_email_domain or "").strip().lower()


def inbound_secret() -> str:
    return effective(None).inbound_email_secret or ""


def is_enabled() -> bool:
    """On only when BOTH the domain and the shared secret are configured. A
    domain with no secret would be an open door; a secret with no domain has no
    address to hand out."""
    return bool(inbound_domain() and inbound_secret())


def _new_token() -> str:
    return secrets.token_hex(12)


def get_or_create(tenant_id: str) -> dict:
    """The tenant's address row, created on first use. Race-safe: two first
    calls insert once (ON CONFLICT DO NOTHING) and both read the same row."""
    execute(
        "INSERT INTO inbound_email_addresses (tenant_id, token) VALUES (%s, %s) "
        "ON CONFLICT (tenant_id) DO NOTHING",
        (tenant_id, _new_token()),
    )
    return query_one(
        "SELECT * FROM inbound_email_addresses WHERE tenant_id = %s", (tenant_id,)
    )


def address_for(token: str) -> str:
    return f"{LOCAL_PART}+{token}@{inbound_domain()}"


def regenerate(tenant_id: str) -> dict:
    get_or_create(tenant_id)
    execute(
        "UPDATE inbound_email_addresses SET token = %s, rotated_at = NOW() "
        "WHERE tenant_id = %s",
        (_new_token(), tenant_id),
    )
    return query_one(
        "SELECT * FROM inbound_email_addresses WHERE tenant_id = %s", (tenant_id,)
    )


def tenant_for_token(token: str) -> Optional[str]:
    row = query_one(
        "SELECT tenant_id FROM inbound_email_addresses WHERE token = %s", (token,)
    )
    return row["tenant_id"] if row else None


def set_allowed_senders(tenant_id: str, emails: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in emails:
        addr = (raw or "").strip().lower()
        if not _EMAIL.match(addr):
            raise AppError(
                "inbound_email_invalid_sender",
                f"'{raw}' is not a valid e-mail address.",
                status_code=422, params={"email": str(raw)[:120]},
            )
        if addr not in cleaned:
            cleaned.append(addr)
    if len(cleaned) > MAX_ALLOWED_SENDERS:
        raise AppError(
            "inbound_email_too_many_senders",
            f"At most {MAX_ALLOWED_SENDERS} allowed senders.",
            status_code=422, params={"max": MAX_ALLOWED_SENDERS},
        )
    get_or_create(tenant_id)
    execute(
        "UPDATE inbound_email_addresses SET allowed_senders = %s WHERE tenant_id = %s",
        (_json(cleaned), tenant_id),
    )
    return cleaned


def sender_is_allowed(tenant_id: str, sender: str, allowed: list[str]) -> bool:
    """A verified analyst/admin of the tenant, or an address the admin listed.

    A viewer cannot upload data in the app, so a viewer's mailbox cannot either
    (unless an admin explicitly lists it). The From header is not authenticated
    here — see docs/inbound-email.md: the secret address is the real gate."""
    sender = (sender or "").strip().lower()
    if not sender:
        return False
    if sender in {a.lower() for a in allowed}:
        return True
    row = query_one(
        "SELECT 1 AS ok FROM users WHERE tenant_id = %s AND LOWER(email) = %s "
        "AND email_verified = TRUE AND status = 'active' "
        "AND role IN ('admin', 'analyst')",
        (tenant_id, sender),
    )
    return row is not None


def list_messages(tenant_id: str, limit: int = MESSAGES_SHOWN) -> list[dict]:
    rows = query(
        "SELECT id, sender, filename, outcome, reason, reason_params, dataset_id, "
        "retrain, received_at FROM inbound_email_messages WHERE tenant_id = %s "
        "ORDER BY received_at DESC LIMIT %s",
        (tenant_id, limit),
    )
    return [dict(r) for r in rows]


def claim(tenant_id: str, message_id: str, sender: str, filename: Optional[str],
          sha: str, size: Optional[int]) -> Optional[str]:
    """Take the (tenant, message, attachment) slot. Returns the row id, or None
    when this exact attachment of this message was already received — which is
    how a provider's retry becomes a no-op."""
    row_id = generate_id("iem")
    row = query_one(
        "INSERT INTO inbound_email_messages "
        "(id, tenant_id, message_id, sender, filename, attachment_sha256, size_bytes) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (tenant_id, message_id, attachment_sha256) DO NOTHING "
        "RETURNING id",
        (row_id, tenant_id, message_id, sender, filename, sha, size),
    )
    return row["id"] if row else None


def settle(row_id: str, outcome: str, reason: Optional[str] = None,
           params: Optional[dict] = None, dataset_id: Optional[str] = None,
           retrain: Optional[str] = None) -> None:
    execute(
        "UPDATE inbound_email_messages SET outcome = %s, reason = %s, "
        "reason_params = %s, dataset_id = %s, retrain = %s WHERE id = %s",
        (outcome, reason, _json(params or {}), dataset_id, retrain, row_id),
    )
