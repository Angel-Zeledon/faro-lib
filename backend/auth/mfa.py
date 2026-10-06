"""Second factor for password sign-in: TOTP (RFC 6238) and recovery codes.

Scope, stated once because three things look alike and are not:

* **This module is the VERIFY half.** Enrolling, confirming, disabling,
  regenerating recovery codes and the tenant policy are routes in the Rust
  service (``backend-rs/src/routes/mfa/``), which read and write the same
  tables. Both sides must agree bit for bit on the TOTP algorithm, the recovery
  code hash and the secret encryption; ``backend-rs/test-vectors/mfa.json``
  holds the fixed vectors both test suites read.
* **Only the password door asks for a code.** Social and enterprise (OIDC /
  SCIM-provisioned) sign-ins are the identity provider's own proof of the
  person, with the provider's own MFA policy; they never reach this module and
  are exempt on purpose. API keys are machine credentials and are unaffected.
* **Nothing here touches refresh-token rotation or
  ``users.sessions_invalid_before``.** After a good code the caller issues the
  same token pair a password-only login always issued.

Storage: the TOTP secret is Fernet-encrypted with the key every other stored
secret uses (``service_config/crypto.py``); recovery codes are stored as an
HMAC of the code, never the code; the challenge token that links "password
ok" to "code ok" is opaque and stored as its SHA-256.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.config import settings
from backend.db.connection import execute, query_one

PERIOD_SECONDS = 30
DIGITS = 6
# Steps accepted either side of "now": a phone clock that is up to one period
# off still works. One step either side, the RFC's own recommended minimum
# tolerance, and no more: each extra step multiplies a guess's chance by 3/2.
WINDOW_STEPS = 1

LOGIN_CHALLENGE_MINUTES = 5
ENROLL_CHALLENGE_MINUTES = 15
# Guesses a single challenge allows, whatever their outcome. Counted by an
# atomic UPDATE before the code is looked at, so concurrent guesses cannot
# share one budget.
CHALLENGE_MAX_ATTEMPTS = 5

PURPOSE_LOGIN = "login"
PURPOSE_ENROLL = "enroll"


# ── TOTP (RFC 6238 over RFC 4226, HMAC-SHA1) ─────────────────────────────────

def generate_secret() -> str:
    """A 160-bit secret, base32 without padding (what authenticator apps take)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _decode_secret(secret_b32: str) -> bytes:
    cleaned = secret_b32.strip().replace(" ", "").upper()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def hotp(secret_b32: str, counter: int, digits: int = DIGITS) -> str:
    """RFC 4226 HOTP with dynamic truncation."""
    mac = hmac.new(_decode_secret(secret_b32), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def time_step(now: Optional[float] = None) -> int:
    return int((time.time() if now is None else now) // PERIOD_SECONDS)


def matching_step(
    secret_b32: str, code: str, *, now: Optional[float] = None,
    last_used_step: Optional[int] = None, digits: int = DIGITS,
) -> Optional[int]:
    """The time-step ``code`` is valid for, or None.

    A step at or before ``last_used_step`` never matches (replay protection:
    one accepted code opens one login). Every candidate step is compared, in
    constant time, whether or not an earlier one matched.
    """
    code = (code or "").strip().replace(" ", "")
    if len(code) != digits or not code.isascii() or not code.isdigit():
        return None
    current = time_step(now)
    found: Optional[int] = None
    for step in range(current - WINDOW_STEPS, current + WINDOW_STEPS + 1):
        ok = hmac.compare_digest(hotp(secret_b32, step, digits), code)
        if ok and (last_used_step is None or step > last_used_step) and found is None:
            found = step
    return found


# ── Recovery codes ───────────────────────────────────────────────────────────

def normalize_recovery_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch not in " -\t\r\n")


def recovery_code_hash(code: str) -> str:
    """HMAC-SHA256 keyed with SECRET_KEY over the normalised code."""
    msg = ("mfa-recovery:" + normalize_recovery_code(code)).encode()
    return hmac.new(settings.secret_key.encode(), msg, hashlib.sha256).hexdigest()


def looks_like_totp(code: str) -> bool:
    c = (code or "").strip().replace(" ", "")
    return len(c) == DIGITS and c.isascii() and c.isdigit()


# ── Enrollment state (written by the Rust routes, read here) ─────────────────

def active_enrollment(user_id: str) -> Optional[dict]:
    return query_one(
        "SELECT user_id, tenant_id, secret_enc, last_used_step FROM user_mfa "
        "WHERE user_id = %s AND status = 'active'",
        (user_id,),
    )


def tenant_requires_mfa(tenant_id: str) -> bool:
    row = query_one("SELECT mfa_required FROM tenants WHERE id = %s", (tenant_id,))
    return bool(row and row.get("mfa_required"))


def _secret(row: dict) -> str:
    from backend.service_config.crypto import decrypt_value
    return decrypt_value(row["secret_enc"])


# ── Challenges ───────────────────────────────────────────────────────────────

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_challenge(user_id: str, tenant_id: str, purpose: str) -> tuple[str, int]:
    """Mint an opaque challenge token. Returns (raw token, lifetime in seconds)."""
    minutes = LOGIN_CHALLENGE_MINUTES if purpose == PURPOSE_LOGIN else ENROLL_CHALLENGE_MINUTES
    raw = secrets.token_urlsafe(48)
    # A new login supersedes any open challenge of the same purpose for the
    # user: stale tokens do not pile up and only the latest one can be used.
    execute(
        "DELETE FROM mfa_challenges WHERE user_id = %s AND purpose = %s",
        (user_id, purpose),
    )
    execute(
        """INSERT INTO mfa_challenges (token_hash, user_id, tenant_id, purpose, expires_at)
           VALUES (%s, %s, %s, %s, %s)""",
        (_hash_token(raw), user_id, tenant_id, purpose,
         datetime.now(timezone.utc) + timedelta(minutes=minutes)),
    )
    return raw, minutes * 60


def claim_attempt(raw: str, purpose: str) -> Optional[dict]:
    """Spend one guess of the challenge, atomically.

    Returns the challenge row, or None when the token is unknown, expired,
    already used, of another purpose, or out of guesses. The increment IS the
    check (one UPDATE ... RETURNING), so two concurrent guesses cannot both
    read "4 of 5 used".
    """
    return query_one(
        """UPDATE mfa_challenges SET attempts = attempts + 1
            WHERE token_hash = %s AND purpose = %s AND consumed_at IS NULL
              AND expires_at > NOW() AND attempts < %s
        RETURNING token_hash, user_id, tenant_id, attempts""",
        (_hash_token(raw), purpose, CHALLENGE_MAX_ATTEMPTS),
    )


def consume(token_hash: str) -> bool:
    """Mark a challenge used. False when somebody else already did."""
    row = query_one(
        "UPDATE mfa_challenges SET consumed_at = NOW() "
        "WHERE token_hash = %s AND consumed_at IS NULL RETURNING token_hash",
        (token_hash,),
    )
    return row is not None


# ── Verifying a second factor ────────────────────────────────────────────────

def verify_second_factor(user_id: str, code: str) -> Optional[str]:
    """Check ``code`` against the user's active enrollment.

    Returns ``"totp"``, ``"recovery"`` or None. A TOTP code is accepted once
    per time-step (the step is stored by a compare-and-set UPDATE, so two
    requests carrying the same code cannot both win); a recovery code is
    burned by a compare-and-set UPDATE on ``used_at``.
    """
    enrollment = active_enrollment(user_id)
    if not enrollment:
        return None

    if looks_like_totp(code):
        step = matching_step(
            _secret(enrollment), code, last_used_step=enrollment.get("last_used_step"),
        )
        if step is None:
            return None
        won = query_one(
            """UPDATE user_mfa SET last_used_step = %s
                WHERE user_id = %s AND (last_used_step IS NULL OR last_used_step < %s)
            RETURNING user_id""",
            (step, user_id, step),
        )
        return "totp" if won else None

    normalized = normalize_recovery_code(code)
    if not normalized:
        return None
    burned = query_one(
        """UPDATE user_mfa_recovery_codes SET used_at = NOW()
            WHERE id = (SELECT id FROM user_mfa_recovery_codes
                         WHERE user_id = %s AND code_hash = %s AND used_at IS NULL
                         LIMIT 1)
              AND used_at IS NULL
        RETURNING id""",
        (user_id, recovery_code_hash(normalized)),
    )
    return "recovery" if burned else None


def recovery_codes_remaining(user_id: str) -> int:
    row = query_one(
        "SELECT COUNT(*) AS n FROM user_mfa_recovery_codes WHERE user_id = %s AND used_at IS NULL",
        (user_id,),
    )
    return int(row["n"]) if row else 0
