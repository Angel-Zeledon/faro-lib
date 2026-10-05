"""Encryption at rest for a data source's secrets (password, CA certificate).

Fernet (AES-128-CBC + HMAC-SHA256) keyed from `SECRET_KEY` — the key every SQL
source password has been encrypted with since SQL sources exist, so changing it
here would make every stored password unreadable.

Decryption FAILS LOUDLY. The previous fallback returned the ciphertext itself
as the password when decryption failed (after a `SECRET_KEY` rotation), so the
driver sent a 100-character token to the customer's server and the user read
"password authentication failed" for a password nobody had changed. Now that
case is `data_source_credentials_unreadable`: "enter the password again".
Only the legacy base64 form (written before Fernet was introduced) is still
read, and only when the value is not a Fernet token.
"""

from __future__ import annotations

import base64
import binascii
import hashlib

from backend.errors import AppError


def _fernet():
    from cryptography.fernet import Fernet

    from backend.config import settings
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    return Fernet(key)


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def _unreadable() -> AppError:
    return AppError(
        "data_source_credentials_unreadable",
        "The stored credentials of this data source cannot be decrypted (the "
        "server's secret key changed). Enter the password again.",
        status_code=409,
    )


def decrypt(token: str) -> str:
    if not token:
        return ""
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError):
        pass
    if token.startswith("gAAAAA"):
        # A Fernet token this key cannot open: never pass it on as a password.
        raise _unreadable()
    try:
        return base64.b64decode(token.encode("ascii"), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeError, ValueError):
        raise _unreadable()
