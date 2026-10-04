"""What is on, what is off, and what it costs — computed from the registry.

Two audiences, two shapes, and they are not the same endpoint on purpose:

  - `service_report()` is the admin panel: every field, its source, whether a
    secret is set (never its value), what breaks while the service is off, and
    the result of the last probe.
  - `capabilities()` is for every authenticated user and carries no
    configuration at all — just which features can answer right now. It is what
    lets a screen say "the assistant is unavailable" instead of spinning, and
    it deliberately cannot leak whether the reason was a missing key or an
    expired one.

`state` is one of:
    ready           — every required field present (deployment services: on)
    not_configured  — a required field is missing; `missing` names the variables
    off             — a deployment switch that is deliberately false
    degraded        — configured, but the last probe failed
"""

from __future__ import annotations

from typing import Any

from backend.config import Settings, settings
from backend.service_config import probes, store
from backend.service_config.registry import (
    BY_KEY, SERVICES, ConfigField, Service,
    editable_fields, required_fields, requirement_groups, service_fields,
)
from backend.service_config.resolver import resolve

# Last probe result per (service, scope). In memory: a probe is a point-in-time
# observation, and a stale one persisted across a restart would claim knowledge
# the process does not have. The panel shows "not checked yet" instead, which is
# true.
_last_probe: dict[tuple[str, str], probes.ProbeResult] = {}


def _mask(value: Any) -> str:
    """A hint that identifies a secret without disclosing it.

    Four trailing characters is enough for a human to tell "the key I pasted"
    from "the old one", and not enough to be a credential. Short values show
    nothing but their length: revealing 4 of 6 characters is disclosure.
    """
    text = "" if value is None else str(value)
    if not text:
        return ""
    if len(text) <= 8:
        return "•" * len(text)
    return "•" * 4 + text[-4:]


def _field_view(f: ConfigField, tenant_id: str | None) -> dict:
    """One field, as the panel renders it.

    A secret's hint is four trailing characters — enough for the person who
    pasted it to recognise it. In a TENANT scope that hint is withheld unless
    the tenant itself stored the value: the resolver falls back to the
    instance's credential, so showing the hint would hand every tenant admin
    four characters of the deployment's Twilio token. `has_value` still says
    the channel works without them configuring anything, which is the part they
    actually need.
    """
    resolved = resolve(f.key, tenant_id)
    has_value = bool(resolved.value) or resolved.value in (0, False)
    inherited = bool(tenant_id) and resolved.source != "tenant"
    view = {
        "key": f.key,
        "env": f.env,
        "kind": f.kind,
        "secret": f.secret,
        "required": f.required,
        "editable": f.editable,
        "doc": f.doc,
        "default": f.default,
        "source": resolved.source,
        "has_value": bool(has_value),
    }
    if f.secret:
        view["hint"] = "" if inherited else _mask(resolved.value)
        view["inherited"] = inherited
    else:
        view["value"] = resolved.value
    return view


def _missing_required(service: Service, tenant_id: str | None) -> list[str]:
    """Variables this service needs and does not have, named as the user would
    set them. Empty means the service can run.

    Two shapes of requirement, because two shapes exist in the product:

      - **AND** (`required=True` on fields): every one must be present. Twilio
        without its auth token is not a channel.
      - **OR** (`requires_any`): any one group is enough. Email runs on a Resend
        key or on SMTP credentials, and demanding both would be a lie in the
        other direction.

    When an OR is unsatisfied the FIRST group is named, because it is the
    cheapest path to a working service; `missing_alternatives` in the report
    carries the others so the panel can offer them.
    """
    missing = [
        f.env for f in required_fields(service)
        if not resolve(f.key, tenant_id).value
    ]

    groups = requirement_groups(service)
    if groups:
        satisfied = any(
            all(resolve(f.key, tenant_id).value for f in group)
            for group in groups
        )
        if not satisfied:
            missing.extend(f.env for f in groups[0] if f.env not in missing)
    return missing


def _missing_alternatives(service: Service, tenant_id: str | None) -> list[list[str]]:
    """The other ways to satisfy an unsatisfied OR — empty once one is met."""
    groups = requirement_groups(service)
    if not groups:
        return []
    if any(all(resolve(f.key, tenant_id).value for f in group) for group in groups):
        return []
    return [[f.env for f in group] for group in groups[1:]]


def _deployment_state(service: Service, tenant_id: str | None) -> str:
    """Deployment services are on or off, never 'unconfigured'."""
    switches = {
        "worker": ("worker_enabled", "scheduler_enabled"),
        "api_surface": ("public_api_only",),
    }.get(service.key)
    if not switches:
        return "ready"
    if service.key == "api_surface":
        return "on" if resolve("public_api_only", tenant_id).value else "off"
    return "ready" if any(resolve(k, tenant_id).value for k in switches) else "off"


def service_report(service: Service, tenant_id: str | None = None) -> dict:
    """The full picture for one service, for the admin panel."""
    missing = _missing_required(service, tenant_id)
    probe_key = (service.key, tenant_id or "")
    last = _last_probe.get(probe_key)

    if service.kind == "deployment":
        state = _deployment_state(service, tenant_id)
    elif service.switch and not resolve(service.switch, tenant_id).value:
        # Paused by the operator. Wins over `not_configured` too: the panel's
        # first job is to say the feature is off on purpose; `missing` still
        # names what would be needed once it is switched on.
        state = "off"
    elif missing:
        state = "not_configured"
    elif last is not None and not last.ok:
        state = "degraded"
    else:
        state = "ready"

    return {
        "key": service.key,
        "kind": service.kind,
        "state": state,
        "summary": service.summary,
        "what_breaks": service.what_breaks,
        "docs_note": service.docs_note,
        "missing": missing,
        "missing_alternatives": _missing_alternatives(service, tenant_id),
        "editable": service.editable,
        "tenant_scoped": service.tenant_scoped,
        "has_probe": bool(service.probe),
        "scope": "tenant" if tenant_id else "instance",
        "fields": [_field_view(f, tenant_id) for f in service_fields(service)],
        "editable_fields": [
            f.key for f in editable_fields(service, tenant_scope=bool(tenant_id))
        ],
        "borrowed_fields": list(service.borrows),
        "last_check": None if last is None else {
            "ok": last.ok,
            "code": last.code,
            "detail": last.detail,
            "checked_at": last.checked_at,
            "extra": last.extra,
        },
    }


def full_report(tenant_id: str | None = None) -> dict:
    """Every service, plus the state of the override layer itself."""
    return {
        "services": [service_report(s, tenant_id) for s in SERVICES],
        "overrides": {
            "store_available": store.store_available(),
            "encryption_available": store.encryption_available(),
            # `env`, `generated` or `none`. A generated key is real encryption,
            # but it lives with storage/ rather than in the deployment's
            # secrets, and a second process on another volume would make its
            # own — so the panel says which one is in force.
            "encryption_source": _encryption_source(),
            "instance_fields": sorted(store.instance_overrides().keys()),
            "tenant_fields": sorted(store.tenant_overrides(tenant_id).keys()) if tenant_id else [],
        },
        "environment": settings.environment,
        "version": settings.app_version,
        "undocumented_settings": sorted(undocumented_settings()),
    }


def _encryption_source() -> str:
    """Where the Fernet key came from. Never raises — this is a report."""
    try:
        from backend.service_config.crypto import key_source
        return key_source()
    except Exception:  # noqa: BLE001 - a report must not become the outage
        return "none"


def record_probe(service_key: str, tenant_id: str | None, result: probes.ProbeResult) -> None:
    _last_probe[(service_key, tenant_id or "")] = result


def forget_probe(service_key: str, tenant_id: str | None = None) -> None:
    """Drop the remembered probe for a scope, after its configuration changed.

    A probe is evidence about the credentials that were in place when it ran.
    Keeping a failed one after somebody pasted a new key would leave the panel
    saying `degraded` about a key it has never tried — the user fixes the
    problem and the screen keeps accusing them. Forgetting returns the service
    to "not checked yet", which is the true statement.
    """
    _last_probe.pop((service_key, tenant_id or ""), None)


def run_probe(service_key: str, tenant_id: str | None = None) -> probes.ProbeResult:
    """Run a service's probe and remember the outcome for the panel."""
    service = BY_KEY.get(service_key)
    if service is None:
        return probes.ProbeResult(False, "unexpected", f"Unknown service {service_key}.")
    if not service.probe:
        return probes.ProbeResult(
            False, "unexpected", f"{service_key} has nothing to reach."
        )
    missing = _missing_required(service, tenant_id)
    if missing:
        result = probes.ProbeResult(
            False, "not_configured", ", ".join(missing) + " missing."
        )
    else:
        result = probes.run(service.probe, tenant_id)
    record_probe(service_key, tenant_id, result)
    return result


# ── Capabilities — the shape every authenticated user may read ──────────────

def _service_is_ready(service_key: str, tenant_id: str | None) -> bool:
    service = BY_KEY[service_key]
    if service.switch and not resolve(service.switch, tenant_id).value:
        return False
    return not _missing_required(service, tenant_id)


def capabilities(tenant_id: str | None = None) -> dict:
    """Which user-visible features can answer right now.

    Deliberately boolean and reason-free. A viewer learning that the assistant
    is off is helping them; a viewer learning WHICH credential is missing is
    telling a non-admin about the deployment's secrets.
    """
    llm = _service_is_ready("llm", tenant_id)
    # The OR that makes email configured — a Resend key or SMTP credentials —
    # used to be written out here as well as implied by the registry. Two
    # copies of one rule is one copy too many: this one said "configured" and
    # `service_report` said "ready" on a deployment where mail could not leave.
    email_ready = _service_is_ready("email", tenant_id)
    whatsapp = _service_is_ready("whatsapp", tenant_id)
    return {
        # AI surfaces. `assistant` gates the chat and the RAG analyst; the
        # narrative and the diagnosis degrade to rule-based text rather than
        # disappearing, so they are reported separately.
        "assistant": llm,
        "ai_narrative": llm,
        "documents_search": _service_is_ready("rag", tenant_id),
        # Delivery channels for alerts and purchase orders.
        "email": bool(email_ready),
        "whatsapp": whatsapp,
        "sms": _service_is_ready("sms", tenant_id),
        # The conversational bot needs both a channel and (unless it is in
        # generic mode) a model.
        "whatsapp_bot": whatsapp and (
            llm or bool(resolve("whatsapp_bot_generic_mode", tenant_id).value)
        ),
        # The commercial surface: with no channel, a tenant at its ceiling has
        # no way to ask for room, so the buttons hide instead of dead-ending.
        "contact_channels": {
            "whatsapp": bool(resolve("contact_whatsapp", tenant_id).value),
            "email": bool(resolve("contact_email", tenant_id).value),
        },
        # Background work. A queued training that will never run is the most
        # expensive "nothing happened" in the product.
        "background_worker": bool(resolve("worker_enabled", tenant_id).value),
        "scheduled_jobs": bool(resolve("scheduler_enabled", tenant_id).value),
    }


# ── The guard that keeps this file honest ───────────────────────────────────

def undocumented_settings() -> set[str]:
    """`Settings` fields no service declares.

    A new setting that nobody documented is invisible to the panel, absent from
    `.env.example` and missing from the manual — which is exactly how the four
    sources of truth drifted apart before this module existed. The test suite
    fails on a non-empty result; the report carries it too, so a buyer running
    a modified copy can see it without running the tests.
    """
    declared = set()
    for service in SERVICES:
        declared.update(f.key for f in service.fields)
    return set(Settings.model_fields) - declared
