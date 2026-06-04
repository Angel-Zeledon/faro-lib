"""
JWT creation and verification.
Access tokens: short-lived (15 min), carry user/tenant/role.
Refresh tokens: long-lived (7 days), stored as a hash server-side.
Signed tokens: used for email verification and password reset.
"""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

import jwt

from backend.config import settings

_ALG = "HS256"
_ACCESS_EXPIRE_MIN = 15
_REFRESH_EXPIRE_DAYS = 7


def create_access_token(user_id: str, tenant_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "jti": secrets.token_hex(8),
        "type": "access",
        "exp": (datetime.utcnow() + timedelta(minutes=_ACCESS_EXPIRE_MIN)).timestamp(),
    }
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
        "exp": (datetime.utcnow() + timedelta(minutes=expires_minutes)).timestamp(),
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
