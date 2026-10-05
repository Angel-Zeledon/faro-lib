"""What value is actually in effect, and where it came from.

Consumers do not read `settings.deepseek_api_key` any more. They read

    cfg = effective()                  # instance scope
    cfg = effective(tenant_id)         # a tenant's own channel credentials
    cfg.deepseek_api_key

`EffectiveConfig` proxies `Settings`, so a consumer that asks for a field with
no override gets exactly what it got before this layer existed — including for
every field that is environment-only. That is the property that makes this safe
to introduce into working code: the fallback is the previous behaviour, not a
different one.

`resolve()` returns the source alongside the value because the panel has to say
it out loud. "Your key is not working" is a very different conversation from
"the key you pasted last week is being shadowed by the one in the .env file",
and without the source they look identical from the outside.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from backend.config import Settings, settings
from backend.service_config import store
from backend.service_config.registry import field_owner

Source = Literal["tenant", "instance", "env", "default"]


@dataclass(frozen=True)
class Resolved:
    value: Any
    source: Source

    @property
    def is_override(self) -> bool:
        return self.source in ("tenant", "instance")


def _env_source(field_key: str) -> Source:
    """Whether the environment actually set this, or it is the built-in default.

    Compared against the model's declared default rather than remembered at
    import: a field with no default (`secret_key`) can only have come from the
    environment, and one that equals its default cannot be distinguished from
    unset — which is the honest answer, since they behave identically.
    """
    model_field = Settings.model_fields.get(field_key)
    if model_field is None:
        return "env"
    default = model_field.default
    try:
        current = getattr(settings, field_key)
    except AttributeError:
        return "default"
    if default is None or repr(default) == "PydanticUndefined":
        return "env"
    return "default" if current == default else "env"


def resolve(field_key: str, tenant_id: str | None = None) -> Resolved:
    """The value in effect for a field, and which layer supplied it."""
    if field_key not in Settings.model_fields:
        raise AttributeError(f"{field_key} is not a setting")

    owner = field_owner(field_key)
    tenant_allowed = bool(owner and owner.tenant_scoped and owner.editable)

    if tenant_id and tenant_allowed:
        tenant_rows = store.tenant_overrides(tenant_id)
        if field_key in tenant_rows:
            return Resolved(tenant_rows[field_key], "tenant")

    if owner is None or owner.editable:
        instance_rows = store.instance_overrides()
        if field_key in instance_rows:
            return Resolved(instance_rows[field_key], "instance")

    return Resolved(getattr(settings, field_key), _env_source(field_key))


def value_of(field_key: str, tenant_id: str | None = None) -> Any:
    return resolve(field_key, tenant_id).value


class EffectiveConfig:
    """A read-only view of `settings` with the override layers applied.

    Attribute access only, and only for names `Settings` declares — a typo
    raises `AttributeError` here exactly as it would on `settings` itself,
    instead of quietly resolving to None and disabling a service.
    """

    __slots__ = ("_tenant_id",)

    def __init__(self, tenant_id: str | None = None):
        self._tenant_id = tenant_id or None

    @property
    def tenant_id(self) -> str | None:
        return self._tenant_id

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return resolve(name, self._tenant_id).value

    def source_of(self, field_key: str) -> Source:
        return resolve(field_key, self._tenant_id).source

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        scope = self._tenant_id or "instance"
        return f"<EffectiveConfig scope={scope}>"


def effective(tenant_id: str | None = None) -> EffectiveConfig:
    """The configuration in effect, for a tenant or for the instance."""
    return EffectiveConfig(tenant_id)


def fingerprint(*field_keys: str, tenant_id: str | None = None) -> tuple:
    """A comparable snapshot of some fields, for consumers that cache a client.

    A service that builds an SDK client once (RAG's Voyage and Pinecone
    handles) used to cache the VERDICT too — including "disabled, no key" —
    which was correct while configuration could only change by restarting the
    process. It no longer can: a key pasted into the panel at 10:05 has to take
    effect without a restart, and a cached "disabled" would make that key look
    broken. Comparing this tuple is how such a consumer notices, without
    rebuilding its client on every call.
    """
    return tuple(resolve(k, tenant_id).value for k in field_keys)
