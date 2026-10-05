"""Reading and writing configuration overrides that live in the database.

The environment is still the floor. This adds one layer above it so a buyer can
paste a DeepSeek key or a Twilio sender into the app and have it take effect
without editing a file and restarting a container.

Precedence, highest first, and it is deliberately short enough to hold in your
head — the panel prints which one won for every field:

    1. tenant override   (only for tenant-scoped services: email, whatsapp, sms)
    2. instance override (written from the admin panel)
    3. the environment   (`backend/.env` / real environment variables)
    4. the Settings default

Three rules this module enforces, each one bought with a failure that is
otherwise invisible:

  - **A secret is never stored in the clear.** Values on fields marked
    `secret=True` are encrypted with the same Fernet key that protects stored
    integration credentials. With no key configured, a write of a secret is
    REFUSED with a stated reason — never silently downgraded to plaintext.
  - **A read never raises.** Configuration is consulted on paths that are
    already handling failure (sending an alert at 8:00, answering a chat). If
    the database is unreachable, the override layer reports itself unavailable
    and the environment value is used, because a deployment that was working
    from `.env` must not stop working because a table could not be read.
  - **The cache expires.** Overrides are cached in-process for a few seconds so
    a loop that sends 200 emails does not run 200 queries. A write invalidates
    it immediately in THIS process; the TTL is what makes a second process
    (the split worker container) converge without a restart.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from backend.service_config.registry import BY_KEY, all_fields, field_owner

log = logging.getLogger(__name__)

# Long enough to collapse a burst of sends into one query, short enough that a
# key pasted into the panel on the API container reaches the worker container
# before anyone reloads the page to check.
_CACHE_TTL_S = 10.0

# (scope_key -> {field_key: value}). scope_key is "" for the instance row set,
# the tenant id otherwise.
_cache: dict[str, dict[str, Any]] = {}
_cache_stamp: float = 0.0
_cache_ok: bool = False


class ConfigStoreUnavailable(RuntimeError):
    """The override table could not be read or written.

    Callers that READ must not propagate this — see `overrides_for`, which
    swallows it and reports the environment instead. Callers that WRITE must,
    because a write that did not happen and says nothing is how a buyer ends up
    convinced they configured a key they did not.
    """


class SecretStorageUnavailable(RuntimeError):
    """A secret was submitted with no encryption key configured."""


# ── Encryption ──────────────────────────────────────────────────────────────
# One Fernet key for everything stored-and-secret is a deliberate choice: a
# second key is a second thing to lose, and losing either has the same
# consequence. It lives in `backend/service_config/crypto.py`.

def encryption_available() -> bool:
    from backend.service_config.crypto import secret_storage_enabled
    return secret_storage_enabled()


def _encrypt(value: str) -> str:
    from backend.service_config.crypto import encrypt_value
    return encrypt_value(value)


def _decrypt(token: str) -> str:
    from backend.service_config.crypto import decrypt_value
    return decrypt_value(token)


# ── Coercion ────────────────────────────────────────────────────────────────
# Every override is stored as TEXT. The registry knows what shape the consumer
# expects, so the value is turned back into it here rather than in each caller.

_TRUE = {"1", "true", "yes", "on", "t"}
_FALSE = {"0", "false", "no", "off", "f", ""}


def coerce(field_key: str, raw: str) -> Any:
    """Turn a stored TEXT value into the type the setting declares.

    An unparseable value is a configuration mistake, not a crash: it is logged
    and treated as absent, so the environment value takes over.
    """
    f = all_fields().get(field_key)
    kind = f.kind if f else "str"
    try:
        if kind == "int":
            return int(raw.strip())
        if kind == "float":
            return float(raw.strip())
        if kind == "bool":
            low = raw.strip().lower()
            if low in _TRUE:
                return True
            if low in _FALSE:
                return False
            raise ValueError(f"not a boolean: {raw!r}")
        if kind == "list":
            return [part.strip() for part in raw.split(",") if part.strip()]
        return raw
    except (TypeError, ValueError) as exc:
        log.warning(
            "Ignoring stored override for %s — %s. Falling back to the environment.",
            field_key, exc,
        )
        raise


def to_text(field_key: str, value: Any) -> str:
    """Render a submitted value as the TEXT that goes into the row."""
    f = all_fields().get(field_key)
    kind = f.kind if f else "str"
    if kind == "bool":
        return "true" if bool(value) else "false"
    if kind == "list":
        if isinstance(value, (list, tuple)):
            return ", ".join(str(v).strip() for v in value)
        return str(value)
    return "" if value is None else str(value)


# ── Reads ───────────────────────────────────────────────────────────────────

def _load_all() -> dict[str, dict[str, Any]]:
    """Read every override row and index it by scope. Raises on DB trouble."""
    from backend.db.connection import query

    rows = query(
        "SELECT tenant_id, field, value_plain, value_encrypted FROM service_config"
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        scope = row["tenant_id"] or ""
        raw = row["value_plain"]
        if raw is None and row["value_encrypted"]:
            try:
                raw = _decrypt(row["value_encrypted"])
            except Exception as exc:
                # A stored secret that will not decrypt means the Fernet key
                # changed. Saying so beats a service that reports itself
                # configured and then fails every call with a bad credential.
                log.error(
                    "Cannot decrypt stored override for %s (scope=%s): %s. "
                    "INTEGRATIONS_SECRET_KEY may have changed — the value must "
                    "be entered again.",
                    row["field"], scope or "instance", exc,
                )
                continue
        if raw is None:
            continue
        try:
            out.setdefault(scope, {})[row["field"]] = coerce(row["field"], raw)
        except (TypeError, ValueError):
            continue
    return out


def _refresh(force: bool = False) -> None:
    global _cache, _cache_stamp, _cache_ok
    now = time.monotonic()
    if not force and _cache_ok and (now - _cache_stamp) < _CACHE_TTL_S:
        return
    try:
        _cache = _load_all()
        _cache_ok = True
    except Exception as exc:
        # Never fatal. A deployment configured entirely through .env — which is
        # every deployment on first boot, before the table exists — must keep
        # working exactly as it did.
        log.debug("Configuration overrides unavailable (%s); using the environment.", exc)
        _cache = {}
        _cache_ok = False
    _cache_stamp = now


def invalidate() -> None:
    """Drop the cache. Called after every write, and by tests."""
    global _cache_stamp, _cache_ok
    _cache_stamp = 0.0
    _cache_ok = False


def store_available() -> bool:
    """Whether the override table could actually be read on the last attempt."""
    _refresh()
    return _cache_ok


def overrides_for(tenant_id: str | None = None) -> dict[str, Any]:
    """Overrides in effect for a scope, tenant values already layered on top."""
    _refresh()
    merged = dict(_cache.get("", {}))
    if tenant_id:
        merged.update(_cache.get(tenant_id, {}))
    return merged


def instance_overrides() -> dict[str, Any]:
    _refresh()
    return dict(_cache.get("", {}))


def tenant_overrides(tenant_id: str) -> dict[str, Any]:
    _refresh()
    return dict(_cache.get(tenant_id, {}))


# ── Writes ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WriteResult:
    written: tuple[str, ...]
    cleared: tuple[str, ...]


def _scope_guard(service_key: str, tenant_id: str | None) -> None:
    service = BY_KEY.get(service_key)
    if service is None:
        raise ValueError(f"unknown service: {service_key}")
    if not service.editable:
        raise ValueError(f"service is not editable from the panel: {service_key}")
    if tenant_id and not service.tenant_scoped:
        raise ValueError(f"service has no per-tenant scope: {service_key}")


def set_values(
    service_key: str,
    values: dict[str, Any],
    *,
    tenant_id: str | None = None,
    updated_by: str = "",
) -> WriteResult:
    """Write overrides for one service. An empty string CLEARS the override.

    Clearing is how a field goes back to the environment, and it is the reason
    "" is not stored as a value: a stored empty string and "no override" would
    look identical in the panel while behaving differently at the call site.
    """
    from backend.db.connection import transaction

    _scope_guard(service_key, tenant_id)
    service = BY_KEY[service_key]
    # Hiding an `instance_only` field in the panel is not enough: the endpoint
    # takes a field name, so the refusal belongs here too.
    allowed = {
        f.key: f for f in service.fields
        if f.editable and not (tenant_id and f.instance_only)
    }

    written: list[str] = []
    cleared: list[str] = []

    for key, value in values.items():
        f = allowed.get(key)
        if f is None:
            raise ValueError(f"{key} is not an editable field of {service_key}")
        text = to_text(key, value)
        if f.secret and text and not encryption_available():
            raise SecretStorageUnavailable(
                f"{f.env} is a secret and no INTEGRATIONS_SECRET_KEY is "
                "configured, so it cannot be stored encrypted."
            )
        if text != "":
            # Validate before writing: a value the consumer cannot parse must
            # be rejected at the panel, not discovered at 8:00 in the morning
            # when the alert loop reads it.
            coerce(key, text)

    try:
        with transaction() as conn:
            for key, value in values.items():
                f = allowed[key]
                text = to_text(key, value)
                if text == "":
                    conn_execute_clear(conn, tenant_id, key)
                    cleared.append(key)
                    continue
                enc = _encrypt(text) if f.secret else None
                plain = None if f.secret else text
                _upsert(conn, tenant_id, service_key, key, plain, enc, updated_by)
                written.append(key)
    except (SecretStorageUnavailable, ValueError):
        raise
    except Exception as exc:
        raise ConfigStoreUnavailable(str(exc)) from exc

    invalidate()
    return WriteResult(written=tuple(written), cleared=tuple(cleared))


def _upsert(
    conn, tenant_id: str | None, service_key: str, field_key: str,
    plain: str | None, encrypted: str | None, updated_by: str,
) -> None:
    """Replace the row for (scope, field).

    Delete-then-insert rather than `ON CONFLICT`, because the uniqueness this
    table needs is over `COALESCE(tenant_id, '')` — an EXPRESSION, since a NULL
    tenant_id (the instance scope) does not collide with itself under a plain
    unique constraint. Inferring an expression index in `ON CONFLICT` works but
    depends on the planner matching the expression exactly, and the whole
    sequence is already inside one transaction, so the simpler statement pair
    is atomic anyway. Writes here are an admin pressing Save, not a hot path.
    """
    from backend.db.connection import execute

    conn_execute_clear(conn, tenant_id, field_key)
    execute(
        """
        INSERT INTO service_config
            (id, tenant_id, service, field, value_plain, value_encrypted, updated_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (str(uuid.uuid4()), tenant_id, service_key, field_key,
         plain, encrypted, updated_by),
        conn=conn,
    )


def conn_execute_clear(conn, tenant_id: str | None, field_key: str) -> None:
    from backend.db.connection import execute

    execute(
        "DELETE FROM service_config "
        " WHERE COALESCE(tenant_id, '') = COALESCE(%s, '') AND field = %s",
        (tenant_id, field_key),
        conn=conn,
    )


def clear_service(service_key: str, *, tenant_id: str | None = None) -> tuple[str, ...]:
    """Remove every override of a service in one scope, back to the environment."""
    from backend.db.connection import transaction

    _scope_guard(service_key, tenant_id)
    keys = tuple(
        f.key for f in BY_KEY[service_key].fields
        if f.editable and not (tenant_id and f.instance_only)
    )
    try:
        with transaction() as conn:
            for key in keys:
                conn_execute_clear(conn, tenant_id, key)
    except Exception as exc:
        raise ConfigStoreUnavailable(str(exc)) from exc
    invalidate()
    return keys


def written_fields_by_scope() -> dict[str, dict[str, str]]:
    """Which service each stored field belongs to, per scope — for auditing."""
    _refresh()
    out: dict[str, dict[str, str]] = {}
    for scope, fields in _cache.items():
        for key in fields:
            owner = field_owner(key)
            out.setdefault(scope, {})[key] = owner.key if owner else "?"
    return out
