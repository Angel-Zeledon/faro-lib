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
