"""
Identity for the WhatsApp bot: a pre-registered, verified phone number on a
user profile is the sole credential. A number is only usable once
`whatsapp_verified_at` is set; the pending (unverified) number lives in the
existing `users.whatsapp_number` column and the 6-digit code reuses the
`pw_change_codes` table (purpose='whatsapp').
"""

from __future__ import annotations

import hashlib
import hmac
import random
import re
from datetime import datetime, timedelta, timezone

from backend.config import settings
from backend.db.connection import execute, query_one

_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
_CODE_EXPIRE_MINUTES = 15
_CODE_MAX_ATTEMPTS = 5
_PURPOSE = "whatsapp"


def normalize_phone(raw: str) -> str:
    """Strip Twilio's `whatsapp:` channel prefix and whitespace."""
    s = (raw or "").strip()
    if s.lower().startswith("whatsapp:"):
        s = s[len("whatsapp:"):].strip()
    return s


def is_e164(num: str) -> bool:
    return bool(_E164_RE.fullmatch(num or ""))


def _hash_code(code: str) -> str:
    return hmac.new(settings.secret_key.encode(), code.encode(), hashlib.sha256).hexdigest()


def resolve_sender(phone: str) -> dict | None:
    """
    Map an inbound E.164 number to its verified user. Returns None for unknown
    or unverified numbers — the webhook turns that into a polite reject and
    never leaks tenant data.
    """
    row = query_one(
        """SELECT id AS user_id, tenant_id, role
           FROM users
           WHERE whatsapp_number = %s AND whatsapp_verified_at IS NOT NULL""",
        (phone,),
    )
    if not row:
        return None
    return {"user_id": row["user_id"], "tenant_id": row["tenant_id"],
            "role": row["role"], "phone": phone}


def start_verification(tenant_id: str, user_id: str, phone: str) -> str:
    """
    Begin linking `phone` to this user. Stores it unverified and returns a
    fresh 6-digit code. Raises ValueError on bad format or if the number is
    already verified by a different user.
    """
    phone = normalize_phone(phone)
    if not is_e164(phone):
        raise ValueError("whatsapp_number must be E.164 format with country code, e.g. +573001234567")

    clash = query_one(
        """SELECT id FROM users
           WHERE whatsapp_number = %s AND whatsapp_verified_at IS NOT NULL
             AND id <> %s""",
        (phone, user_id),
    )
    if clash:
        raise ValueError("This WhatsApp number is already linked to another account")

    execute(
        """UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = NULL,
               updated_at = NOW()
           WHERE id = %s AND tenant_id = %s""",
        (phone, user_id, tenant_id),
    )

    code = f"{random.SystemRandom().randint(0, 999999):06d}"
    execute(
        "DELETE FROM pw_change_codes WHERE user_id = %s AND purpose = %s AND used = FALSE",
        (user_id, _PURPOSE),
    )
    execute(
        """INSERT INTO pw_change_codes (id, user_id, tenant_id, code_hash, expires_at, purpose)
           VALUES (gen_random_uuid()::text, %s, %s, %s, %s, %s)""",
        (user_id, tenant_id, _hash_code(code),
         datetime.now(timezone.utc) + timedelta(minutes=_CODE_EXPIRE_MINUTES), _PURPOSE),
    )
    return code


def confirm_verification(tenant_id: str, user_id: str, code: str) -> bool:
    """
    Validate the pending code and, on success, mark the number verified.
    Enforces expiry and an attempt cap; a wrong code burns one attempt.
    """
    row = query_one(
        """SELECT id, code_hash, expires_at, attempts
           FROM pw_change_codes
           WHERE user_id = %s AND tenant_id = %s AND purpose = %s AND used = FALSE
           ORDER BY expires_at DESC LIMIT 1""",
        (user_id, tenant_id, _PURPOSE),
    )
    if not row:
        return False
    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return False
    if (row["attempts"] or 0) >= _CODE_MAX_ATTEMPTS:
        return False

    if not hmac.compare_digest(row["code_hash"], _hash_code(code)):
        execute("UPDATE pw_change_codes SET attempts = attempts + 1 WHERE id = %s", (row["id"],))
        return False

    execute("UPDATE pw_change_codes SET used = TRUE WHERE id = %s", (row["id"],))
    execute(
        "UPDATE users SET whatsapp_verified_at = NOW(), updated_at = NOW() WHERE id = %s AND tenant_id = %s",
        (user_id, tenant_id),
    )
    return True
