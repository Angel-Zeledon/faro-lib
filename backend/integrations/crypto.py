"""Encrypt integration credentials at rest with Fernet.

Where the key comes from, in order:

  1. `INTEGRATIONS_SECRET_KEY` in the environment. Always wins.
  2. `storage/instance_secret.key`, generated on first use.

Rule 2 exists because of what rule 1 alone did to a virgin install: with no key,
NOTHING secret can be stored — not a DeepSeek key, not a Twilio token, not an
SMTP password — because storing a credential unencrypted is not an option this
code offers. So the buyer opened the configuration panel, found every secret
field refusing to save, and was sent back to editing a file and restarting the
container, which is the thing the panel exists to end. The first run is exactly
when nobody has a key yet.

Generating one is the lesser of the two risks, but it is not free, and the two
things it costs are stated at the moment it happens (a WARNING naming the file)
rather than discovered later:

  * **It lives with `storage/`, not with the database it protects.** That is
    deliberate — a key inside the rows it encrypts protects nothing — and it
    means `storage/` must be backed up, which `docs/configuracion.md` already
    says for the datasets and artifacts beside it. Lose the file and every
    stored credential has to be entered again.
  * **A second process with a different `storage/` generates a DIFFERENT key**,
    and then the credentials one wrote are unreadable by the other. Before
    running an API and a worker on separate volumes, promote the generated key
    into `INTEGRATIONS_SECRET_KEY` so both read the same one. The panel says so,
    and so does the log line at generation.
"""
import json
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet

from backend.config import settings

log = logging.getLogger(__name__)

KEY_FILENAME = "instance_secret.key"

# Cached per process: the file is read on every encrypt/decrypt otherwise, and
# those run inside the alert loop.
_cached_key: str | None = None


def _key_path() -> Path:
    return Path(settings.storage_path) / KEY_FILENAME


def _read_or_create_key() -> str | None:
    """The generated key, creating it on first use. None if that is impossible.

    Never raises: this sits under `integrations_enabled()`, which every consumer
    calls to decide whether a feature is available. A read-only disk means the
    feature is off, not that the request fails.
    """
    global _cached_key
    if _cached_key:
        return _cached_key

    path = _key_path()
    try:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                _cached_key = value
                return value
            log.warning("%s is empty — generating a new key.", path)

        path.parent.mkdir(parents=True, exist_ok=True)
        value = Fernet.generate_key().decode()
        # Written 0600 where the OS honours it. On Windows the mode is advisory,
        # which is why the warning below matters more than the bits.
        path.write_text(value, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass

        log.warning(
            "No INTEGRATIONS_SECRET_KEY was set, so one was generated at %s. "
            "It encrypts every credential stored from the configuration panel. "
            "Back up that file with the rest of storage/, and before running a "
            "second process on a different volume, copy its contents into "
            "INTEGRATIONS_SECRET_KEY so both read the same key.",
            path,
        )
        _cached_key = value
        return value
    except Exception as exc:  # noqa: BLE001 - an unwritable disk turns it off
        log.error(
            "Could not read or create %s (%s). Secrets cannot be stored from "
            "the panel until INTEGRATIONS_SECRET_KEY is set.", path, exc,
        )
        return None


def active_key() -> str | None:
    """The key in effect, or None when there is none and none can be made."""
    if settings.integrations_secret_key:
        return settings.integrations_secret_key
    return _read_or_create_key()


def key_source() -> str:
    """Where the key came from — `env`, `generated`, or `none`. For the panel."""
    if settings.integrations_secret_key:
        return "env"
    return "generated" if _read_or_create_key() else "none"


def reset_cache() -> None:
    """Forget the cached key. For tests that move `storage_path`."""
    global _cached_key
    _cached_key = None


def integrations_enabled() -> bool:
    return active_key() is not None


def _fernet() -> Fernet:
    key = active_key()
    if not key:
        raise RuntimeError("INTEGRATIONS_SECRET_KEY not configured")
    return Fernet(key.encode())


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
