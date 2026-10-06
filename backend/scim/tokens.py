"""The per-tenant SCIM bearer token.

Shape: `scim_<id>_<secret>`. The id (16 hex characters) is the row's primary
key and is not secret - the admin screen shows it so a person can tell which
token an identity provider holds. The secret is 32 random bytes; only its
SHA-256 is stored, and the presented secret is compared with
`hmac.compare_digest`, so the comparison takes the same time whether the first
character or the last one is wrong.

One live token per tenant (a partial unique index enforces it). Rotating mints
a new one and revokes the old one in the same transaction: the old token stops
working the moment the new one is shown.

The prefix is deliberately neither `sk_live_` nor anything a JWT can start
with: `get_current_user` hands a SCIM token to neither the API-key path nor the
JWT decoder successfully, so outside `/scim/v2` it is a plain 401.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Optional

PREFIX = "scim_"
_TOKEN_RE = re.compile(r"^scim_([0-9a-f]{16})_([A-Za-z0-9_\-]{43})$")

# `last_used_at` is for the admin screen ("is the provider still syncing?"),
# not an audit log: written at most once a minute, like api_keys.last_used.
LAST_USED_THROTTLE = "1 minute"

# A hash nobody's secret produces, compared against when the id is unknown so
# a miss costs the same digest work as a hit.
_DUMMY_HASH = hashlib.sha256(b"scim-token-that-does-not-exist").hexdigest()


# ── Pure ─────────────────────────────────────────────────────────────────────

def mint() -> tuple[str, str, str]:
    """(token_id, raw_token, secret_hash). The raw token is shown ONCE."""
    token_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    return token_id, f"{PREFIX}{token_id}_{secret}", hash_secret(secret)


def parse(raw: Optional[str]) -> Optional[tuple[str, str]]:
    """(token_id, secret), or None for anything that is not a SCIM token."""
    if not isinstance(raw, str):
        return None
    m = _TOKEN_RE.match(raw.strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def verify(secret: str, stored_hash: Optional[str]) -> bool:
    """Constant-time comparison of a presented secret with a stored hash."""
    expected = stored_hash if isinstance(stored_hash, str) else _DUMMY_HASH
    ok = hmac.compare_digest(hash_secret(secret), expected)
    return ok and isinstance(stored_hash, str)


def hint(token_id: str) -> str:
    """What the admin screen shows instead of the token."""
    return f"{PREFIX}{token_id}_…"


# ── Database ─────────────────────────────────────────────────────────────────

def _public(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {
        "id": row["id"],
        "hint": hint(row["id"]),
        "manage_admins": bool(row["manage_admins"]),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "created_by": row.get("created_by"),
        "last_used_at": row["last_used_at"].isoformat() if row.get("last_used_at") else None,
    }


def active_row(tenant_id: str) -> Optional[dict]:
    from backend.db.connection import query_one
    return query_one(
        "SELECT * FROM scim_tokens WHERE tenant_id = %s AND revoked_at IS NULL",
        (tenant_id,),
    )


def active_public(tenant_id: str) -> Optional[dict]:
    return _public(active_row(tenant_id))


def create(tenant_id: str, admin_user_id: str, *, manage_admins: bool) -> tuple[str, dict, bool]:
    """Mint a token, revoking the live one if any. Returns (raw, public, rotated)."""
    from backend.db.connection import execute, query_one, transaction

    token_id, raw, secret_hash = mint()
    with transaction() as conn:
        # Two admins pressing "create" at once must not leave two live tokens
        # (the partial unique index would refuse the second INSERT anyway; the
        # lock turns that into an orderly rotation instead of a 500).
        query_one("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"scim-token:{tenant_id}",),
                  conn=conn)
        previous = query_one(
            "SELECT id FROM scim_tokens WHERE tenant_id = %s AND revoked_at IS NULL",
            (tenant_id,), conn=conn,
        )
        if previous:
            execute(
                "UPDATE scim_tokens SET revoked_at = NOW(), revoked_by = %s "
                "WHERE id = %s AND tenant_id = %s",
                (admin_user_id, previous["id"], tenant_id), conn=conn,
            )
        execute(
            """INSERT INTO scim_tokens (id, tenant_id, secret_hash, manage_admins, created_by)
               VALUES (%s, %s, %s, %s, %s)""",
            (token_id, tenant_id, secret_hash, bool(manage_admins), admin_user_id),
            conn=conn,
        )
    return raw, _public(active_row(tenant_id)), bool(previous)


def revoke(tenant_id: str, admin_user_id: str) -> bool:
    from backend.db.connection import query
    rows = query(
        "UPDATE scim_tokens SET revoked_at = NOW(), revoked_by = %s "
        "WHERE tenant_id = %s AND revoked_at IS NULL RETURNING id",
        (admin_user_id, tenant_id),
    )
    return bool(rows)


def set_manage_admins(tenant_id: str, value: bool) -> Optional[dict]:
    from backend.db.connection import query
    rows = query(
        "UPDATE scim_tokens SET manage_admins = %s "
        "WHERE tenant_id = %s AND revoked_at IS NULL RETURNING id",
        (bool(value), tenant_id),
    )
    return active_public(tenant_id) if rows else None


def authenticate(raw: Optional[str]) -> Optional[dict]:
    """The live token row for a presented credential, or None.

    None covers every reason alike (malformed, unknown, revoked, wrong secret):
    a caller must not be able to tell a revoked token from one that never was.
    """
    from backend.db.connection import query_one

    parsed = parse(raw)
    if not parsed:
        verify("x" * 43, None)  # same digest work as a real miss
        return None
    token_id, secret = parsed
    row = query_one(
        "SELECT * FROM scim_tokens WHERE id = %s AND revoked_at IS NULL", (token_id,),
    )
    if not verify(secret, row["secret_hash"] if row else None):
        return None
    return row


def touch(token_id: str) -> None:
    from backend.db.connection import execute
    execute(
        f"""UPDATE scim_tokens SET last_used_at = NOW()
             WHERE id = %s
               AND (last_used_at IS NULL OR last_used_at < NOW() - INTERVAL '{LAST_USED_THROTTLE}')""",
        (token_id,),
    )
