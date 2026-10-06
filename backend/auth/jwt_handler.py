"""
JWT creation and verification.
Access tokens: short-lived (15 min), carry user/tenant/role.
Refresh tokens: long-lived (7 days), stored as a hash server-side.
Signed tokens: used for email verification and password reset.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from backend.config import settings

_ALG = "HS256"
_ACCESS_EXPIRE_MIN = 15
_REFRESH_EXPIRE_DAYS = 7


def create_access_token(
    user_id: str, tenant_id: str, role: str, email_verified: bool = True,
    session_started_at: float | None = None,
) -> str:
    """`email_verified` travels in the token so an unverified user can still log
    in and explore (see backend/auth/guards.require_verified_email): the claim,
    not a 403 at login, is what gates the few actions that reach outside the
    tenant. Defaults to True so callers that predate the claim keep behaving
    exactly as before.

    `session_started_at` (epoch seconds) is when the SESSION began, for a token
    minted by a refresh: the new token's own `iat` is later than the login. It
    travels as `sat`, and the tenant's maximum-session-lifetime policy measures
    from it (backend/auth/session_policy.py). A login passes nothing: its own
    `iat` is the start, and the claim is left out so the token is unchanged."""
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "email_verified": bool(email_verified),
        "jti": secrets.token_hex(8),
        "type": "access",
        # When this token was minted. It is what lets a password change
        # invalidate tokens it has never seen: the reset flow is unauthenticated
        # and holds no `jti`, so the only way to disown someone else's live
        # token is to compare when it was issued against when the account last
        # cut its sessions (users.sessions_invalid_before — see guards.py).
        #
        # SUB-SECOND on purpose, like `exp` below. A first attempt floored both
        # sides to the second, to keep a login made in the same second as a
        # reset from being mistaken for an older token and locking the user out
        # of the account they had just recovered. The suite then caught the
        # other half of that trade: run under load, the pre-reset token and the
        # reset itself landed in the SAME second too, and the token survived a
        # password change it should not have. A second is simply too coarse to
        # separate "issued just before" from "issued just after"; microseconds
        # separate both cases correctly and neither compromise is needed.
        "iat": datetime.now(timezone.utc).timestamp(),
        # timezone-aware UTC: datetime.utcnow().timestamp() misreads the naive
        # value as local time, so on a non-UTC host the token would live longer
        # (or shorter) than _ACCESS_EXPIRE_MIN by the host's UTC offset.
        "exp": (datetime.now(timezone.utc) + timedelta(minutes=_ACCESS_EXPIRE_MIN)).timestamp(),
    }
    if session_started_at is not None:
        payload["sat"] = float(session_started_at)
    return jwt.encode(payload, settings.secret_key, algorithm=_ALG)


def get_refresh_expire_days() -> int:
    return _REFRESH_EXPIRE_DAYS


def create_refresh_token() -> tuple[str, str]:
    """Returns (raw_token, sha256_hash). Store only the hash."""
    raw = secrets.token_urlsafe(64)
    return raw, _hash(raw)


def create_signed_token(payload: dict, expires_minutes: int = 60) -> str:
    data = {
        **payload,
        "exp": (datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)).timestamp(),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(data, settings.secret_key, algorithm=_ALG)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[_ALG],
            options={"verify_exp": True},
        )
    except jwt.ExpiredSignatureError:
        raise ValueError("Token expired")
    except jwt.InvalidTokenError as e:
        raise ValueError(f"Invalid token: {e}")


def hash_token(raw: str) -> str:
    return _hash(raw)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
