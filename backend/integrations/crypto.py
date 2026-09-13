"""Encrypt integration credentials at rest with Fernet."""
import json
from cryptography.fernet import Fernet

from backend.config import settings


def integrations_enabled() -> bool:
    return bool(settings.integrations_secret_key)


def _fernet() -> Fernet:
    if not settings.integrations_secret_key:
        raise RuntimeError("INTEGRATIONS_SECRET_KEY not configured")
    return Fernet(settings.integrations_secret_key.encode())


def encrypt_credentials(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_credentials(token: str) -> dict:
    return json.loads(_fernet().decrypt(token.encode()).decode())


# ── Single-value helpers ─────────────────────────────────────────────────────
# `backend/service_config/store.py` stores one credential per row rather than a
# credentials dict, and reuses this key rather than introducing a second one.
# One key for everything stored-and-secret is deliberate: a second key is a
# second thing to lose, and losing either has the same consequence.

def encrypt_value(value: str) -> str:
    """Encrypt a single configuration value. Raises with no key configured."""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_value(token: str) -> str:
    """Decrypt a single configuration value. Raises if the key has changed."""
    return _fernet().decrypt(token.encode()).decode()
