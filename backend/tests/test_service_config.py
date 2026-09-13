"""The configuration layer: precedence, secrecy, access, and honest failure.

What a buyer of this source is really buying here is a promise with three
parts, and each one has a test below that fails if the promise breaks:

1. **Nothing is undocumented.** Every `Settings` field is declared in the
   registry, so `.env.example` and `docs/configuracion.md` describe the real
   product rather than the part somebody remembered to write down.
2. **A missing key turns a feature off, never into an error.** Probes report
   failures instead of raising; consumers ask the resolver and degrade.
3. **A secret goes in and does not come back out.** The API returns four
   trailing characters at most, refuses to store a secret it cannot encrypt,
   and never lets one tenant read or write another's scope — or the
   instance's.

The access tests are the ones worth reading twice: `admin` is a role INSIDE a
tenant, so "admin can configure the deployment" would mean anybody who signs up
can rewrite the owner's credentials.
"""

from uuid import uuid4

import pytest

from backend.config import Settings, settings
from backend.db.connection import execute, query, query_one
from backend.service_config import access, probes, status as status_mod, store
from backend.service_config.registry import (
    BY_KEY, SERVICES, all_fields, editable_fields, required_fields,
)
from backend.service_config.resolver import effective, resolve

API = "/api/v1/service-config"


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _schema(client):
    """Migrations run on app startup, and `client` is what starts the app.

    Without this the tests that never take `client` (the store and resolver
    ones) reach a database where `service_config` does not exist yet — which
    looks like a broken feature and is really a test-ordering artifact.
    """
    return client


@pytest.fixture
def clean_overrides():
    """No stored overrides before or after — the table is instance-wide state."""
    execute("DELETE FROM service_config")
    store.invalidate()
    yield
    execute("DELETE FROM service_config")
    store.invalidate()


@pytest.fixture
def fernet_key(monkeypatch):
    """A real encryption key, so secret writes are allowed in these tests."""
    from cryptography.fernet import Fernet
    monkeypatch.setattr(
        "backend.config.settings.integrations_secret_key",
        Fernet.generate_key().decode(),
    )
    return True


@pytest.fixture
def operator(monkeypatch, registered_user):
    """Make the test admin an operator of this installation."""
    monkeypatch.setattr(
        "backend.config.settings.instance_admin_emails",
        [registered_user["email"]],
    )
    return registered_user


# ── 1. The registry is the whole truth ──────────────────────────────────────

def test_registry_covers_every_setting():
    """A new Settings field with no descriptor is invisible to the panel, absent
    from .env.example and missing from the docs. That drift is what produced an
    example file describing 20 of 45 variables."""
    assert status_mod.undocumented_settings() == set(), (
        "Settings fields with no registry descriptor: "
        f"{sorted(status_mod.undocumented_settings())}. Declare them in "
        "backend/service_config/registry.py."
    )


def test_every_registry_field_is_a_real_setting():
    """The other direction: a descriptor for a field that no longer exists would
    render a panel input that writes a value nothing reads."""
    unknown = set(all_fields()) - set(Settings.model_fields)
    assert unknown == set(), f"Registry describes fields Settings does not have: {unknown}"


def test_every_probe_name_resolves():
    """A service naming a probe function that does not exist would report
    'has_probe' and then fail the moment somebody pressed the button."""
    for service in SERVICES:
        if service.probe:
            assert service.probe in probes.PROBES, (
                f"{service.key} names probe {service.probe!r}, which probes.py "
                "does not define"
            )


def test_generated_docs_are_current():
    """`.env.example` and docs/configuracion.md are generated. If this fails,
    run: python -m backend.scripts.gen_config_docs"""
    from backend.scripts.gen_config_docs import (
        CONFIG_DOC, ENV_EXAMPLE, render_config_doc, render_env_example,
    )
    for path, rendered in ((ENV_EXAMPLE, render_env_example()),
                           (CONFIG_DOC, render_config_doc())):
        current = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert current == rendered, (
            f"{path.name} is out of date with the registry. "
            "Run: python -m backend.scripts.gen_config_docs"
        )


# ── 2. Precedence ───────────────────────────────────────────────────────────

def test_no_override_returns_the_environment_value(clean_overrides):
    assert resolve("deepseek_model").value == settings.deepseek_model
    assert resolve("deepseek_model").source in ("env", "default")


def test_instance_override_beats_the_environment(clean_overrides):
    store.set_values("llm", {"deepseek_model": "deepseek-reasoner"}, updated_by="test")
    resolved = resolve("deepseek_model")
    assert resolved.value == "deepseek-reasoner"
    assert resolved.source == "instance"
    assert effective().deepseek_model == "deepseek-reasoner"


def test_tenant_override_beats_the_instance(clean_overrides, test_tenant):
    store.set_values("whatsapp", {"twilio_whatsapp_from": "whatsapp:+10000000000"},
                     updated_by="test")
    store.set_values("whatsapp", {"twilio_whatsapp_from": "whatsapp:+19999999999"},
                     tenant_id=test_tenant["id"], updated_by="test")

    assert effective().twilio_whatsapp_from == "whatsapp:+10000000000"
    assert effective(test_tenant["id"]).twilio_whatsapp_from == "whatsapp:+19999999999"
    assert resolve("twilio_whatsapp_from", test_tenant["id"]).source == "tenant"


def test_a_tenant_cannot_override_a_service_that_is_not_tenant_scoped(
    clean_overrides, test_tenant
):
    """A tenant with its own LLM key is not a need; it is a place a secret can
    cross a border. The store refuses rather than silently writing a row nobody
    would ever read."""
    with pytest.raises(ValueError):
        store.set_values("llm", {"deepseek_model": "x"},
                         tenant_id=test_tenant["id"], updated_by="test")
    assert query("SELECT id FROM service_config") == []


def test_clearing_an_override_returns_to_the_environment(clean_overrides):
    store.set_values("llm", {"deepseek_model": "deepseek-reasoner"}, updated_by="test")
    assert effective().deepseek_model == "deepseek-reasoner"

    store.set_values("llm", {"deepseek_model": ""}, updated_by="test")
    assert effective().deepseek_model == settings.deepseek_model
    assert query_one(
        "SELECT COUNT(*) AS n FROM service_config WHERE field = 'deepseek_model'"
    )["n"] == 0


def test_types_survive_the_round_trip(clean_overrides):
    """Everything is stored as TEXT; the registry says what shape to restore."""
    store.set_values("whatsapp", {"whatsapp_bot_generic_mode": "true"}, updated_by="test")
    assert effective().whatsapp_bot_generic_mode is True

    store.set_values("email", {"smtp_port": "2525"}, updated_by="test")
    assert effective().smtp_port == 2525


def test_a_value_the_consumer_cannot_parse_is_refused_at_the_write(clean_overrides):
    """Rejected at the panel, not discovered at 8:00 by the alert loop."""
    with pytest.raises(ValueError):
        store.set_values("email", {"smtp_port": "not-a-port"}, updated_by="test")
    assert query("SELECT id FROM service_config") == []


def test_an_unknown_field_is_refused(clean_overrides):
    with pytest.raises(ValueError):
        store.set_values("llm", {"database_url": "postgres://evil"}, updated_by="test")
    assert query("SELECT id FROM service_config") == []


def test_a_non_editable_field_cannot_be_written(clean_overrides):
    """`integrations_secret_key` encrypts everything this table stores, including
    itself. A panel that could rewrite it would make its own rows unreadable."""
    with pytest.raises(ValueError):
        store.set_values("integrations", {"integrations_secret_key": "x"},
                         updated_by="test")


# ── 3. Secrets ──────────────────────────────────────────────────────────────

def test_a_secret_is_stored_encrypted_and_never_in_the_clear(
    clean_overrides, fernet_key
):
    store.set_values("llm", {"deepseek_api_key": "sk-super-secret-value"},
                     updated_by="test")
    row = query_one(
        "SELECT value_plain, value_encrypted FROM service_config "
        " WHERE field = 'deepseek_api_key'"
    )
    assert row["value_plain"] is None
    assert row["value_encrypted"]
    assert "sk-super-secret-value" not in row["value_encrypted"]
    # ...and it still resolves to the real thing for the consumer.
    assert effective().deepseek_api_key == "sk-super-secret-value"


def test_a_secret_write_is_refused_without_an_encryption_key(
    clean_overrides, monkeypatch
):
    """Refused with a stated reason, never downgraded to plaintext."""
    monkeypatch.setattr("backend.config.settings.integrations_secret_key", "")
    with pytest.raises(store.SecretStorageUnavailable):
        store.set_values("llm", {"deepseek_api_key": "sk-x"}, updated_by="test")
    assert query("SELECT id FROM service_config") == []


def test_the_report_masks_a_secret(clean_overrides, fernet_key):
    store.set_values("llm", {"deepseek_api_key": "sk-abcdefghijklmnop"},
                     updated_by="test")
    report = status_mod.service_report(BY_KEY["llm"])
    field = next(f for f in report["fields"] if f["key"] == "deepseek_api_key")
    assert "value" not in field
    assert field["hint"].endswith("mnop")
    assert "abcdefghij" not in field["hint"]
    assert field["has_value"] is True


def test_a_tenant_is_not_shown_a_hint_of_the_instance_secret(
    clean_overrides, fernet_key, test_tenant, monkeypatch
):
    """The resolver falls back to the instance's credential, so a naive hint
    would hand every tenant admin four characters of the deployment's Twilio
    token. They still learn the channel works — just not what it is."""
    monkeypatch.setattr(
        "backend.config.settings.twilio_auth_token", "instance-token-ABCD"
    )
    report = status_mod.service_report(BY_KEY["whatsapp"], test_tenant["id"])
    field = next(f for f in report["fields"] if f["key"] == "twilio_auth_token")
    assert field["has_value"] is True
    assert field["hint"] == ""
    assert field["inherited"] is True

    # Its own value, however, it may recognise.
    store.set_values(
        "whatsapp", {"twilio_auth_token": "tenant-token-WXYZ"},
        tenant_id=test_tenant["id"], updated_by="test",
    )
    report = status_mod.service_report(BY_KEY["whatsapp"], test_tenant["id"])
    field = next(f for f in report["fields"] if f["key"] == "twilio_auth_token")
    assert field["hint"].endswith("WXYZ")
    assert field["inherited"] is False

    # And the operator's own view is unchanged.
    instance = status_mod.service_report(BY_KEY["whatsapp"])
    field = next(f for f in instance["fields"] if f["key"] == "twilio_auth_token")
    assert field["hint"].endswith("ABCD")


def test_a_short_secret_discloses_nothing(clean_overrides, fernet_key):
    """Revealing 4 of 6 characters is disclosure, not a hint."""
    store.set_values("llm", {"deepseek_api_key": "sk-123"}, updated_by="test")
    report = status_mod.service_report(BY_KEY["llm"])
    field = next(f for f in report["fields"] if f["key"] == "deepseek_api_key")
    assert set(field["hint"]) == {"•"}


# ── 4. State and honest degradation ─────────────────────────────────────────

def test_a_missing_required_field_names_the_variable(clean_overrides, monkeypatch):
    monkeypatch.setattr("backend.config.settings.deepseek_api_key", "")
    report = status_mod.service_report(BY_KEY["llm"])
    assert report["state"] == "not_configured"
    assert "DEEPSEEK_API_KEY" in report["missing"]
    assert report["what_breaks"]


def test_a_service_that_is_an_OR_is_not_ready_on_nothing(clean_overrides, monkeypatch):
    """Email runs on a Resend key OR on SMTP credentials. Neither field could be
    marked `required` without lying about the other path, so for a while nothing
    was — and the service reported `ready` on a deployment where mail could not
    leave at all. The most expensive kind of wrong: the one that looks fine."""
    for field in ("resend_api_key", "smtp_user", "smtp_pass"):
        monkeypatch.setattr(f"backend.config.settings.{field}", "")

    report = status_mod.service_report(BY_KEY["email"])
    assert report["state"] == "not_configured"
    assert report["missing"] == ["RESEND_API_KEY"]
    assert report["missing_alternatives"] == [["SMTP_USER", "SMTP_PASS"]]
    assert status_mod.capabilities()["email"] is False


def test_either_side_of_the_OR_is_enough(clean_overrides, monkeypatch):
    for field in ("resend_api_key", "smtp_user", "smtp_pass"):
        monkeypatch.setattr(f"backend.config.settings.{field}", "")

    monkeypatch.setattr("backend.config.settings.resend_api_key", "re_key")
    assert status_mod.service_report(BY_KEY["email"])["state"] == "ready"
    assert status_mod.service_report(BY_KEY["email"])["missing_alternatives"] == []

    monkeypatch.setattr("backend.config.settings.resend_api_key", "")
    monkeypatch.setattr("backend.config.settings.smtp_user", "user@example.com")
    monkeypatch.setattr("backend.config.settings.smtp_pass", "app-password")
    assert status_mod.service_report(BY_KEY["email"])["state"] == "ready"
    assert status_mod.capabilities()["email"] is True


def test_half_an_OR_group_is_not_enough(clean_overrides, monkeypatch):
    """An SMTP user with no password is not a transport, and saying `ready`
    there would send every alert into a login failure nobody reads."""
    monkeypatch.setattr("backend.config.settings.resend_api_key", "")
    monkeypatch.setattr("backend.config.settings.smtp_user", "user@example.com")
    monkeypatch.setattr("backend.config.settings.smtp_pass", "")
    assert status_mod.service_report(BY_KEY["email"])["state"] == "not_configured"


def test_the_commercial_surface_reports_itself_off_when_it_is(
    clean_overrides, monkeypatch
):
    """With no contact channel a free tenant at its ceiling has no way to ask
    for room — the entire commercial surface, silently absent."""
    monkeypatch.setattr("backend.config.settings.contact_whatsapp", "")
    monkeypatch.setattr("backend.config.settings.contact_email", "")
    report = status_mod.service_report(BY_KEY["contact"])
    assert report["state"] == "not_configured"
    assert report["missing"] == ["CONTACT_WHATSAPP"]
    assert report["missing_alternatives"] == [["CONTACT_EMAIL"]]

    monkeypatch.setattr("backend.config.settings.contact_email", "hola@example.com")
    assert status_mod.service_report(BY_KEY["contact"])["state"] == "ready"


def test_every_non_deployment_service_can_report_itself_unconfigured(monkeypatch):
    """The guard against the hole this section closed: a service that cannot
    ever say `not_configured` cannot warn anybody. Every external service must
    declare either a required field or an alternatives group."""
    from backend.service_config.registry import requirement_groups

    for service in SERVICES:
        if service.kind != "external":
            continue
        assert required_fields(service) or requirement_groups(service), (
            f"'{service.key}' declares no way to be unconfigured, so it will "
            "report itself ready on a deployment that has none of its keys."
        )


def test_a_probe_reports_a_failure_instead_of_raising(clean_overrides, monkeypatch):
    monkeypatch.setattr("backend.config.settings.deepseek_api_key", "")
    result = status_mod.run_probe("llm")
    assert result.ok is False
    assert result.code == "not_configured"
    assert "DEEPSEEK_API_KEY" in result.detail


def test_a_failed_probe_makes_a_configured_service_degraded(clean_overrides, monkeypatch):
    """'Configured' and 'working' are different claims, and the panel says which."""
    monkeypatch.setattr("backend.config.settings.deepseek_api_key", "sk-present")
    status_mod.forget_probe("llm")
    assert status_mod.service_report(BY_KEY["llm"])["state"] == "ready"

    status_mod.record_probe("llm", None, probes.ProbeResult(False, "auth_failed", "401"))
    try:
        assert status_mod.service_report(BY_KEY["llm"])["state"] == "degraded"
    finally:
        status_mod.forget_probe("llm")


def test_changing_the_configuration_forgets_a_stale_probe(
    client, auth_headers, operator, clean_overrides, monkeypatch
):
    """A failed probe is evidence about the key that was in place when it ran.
    After somebody pastes a new one, keeping it would have the panel report
    `degraded` about a credential it has never tried."""
    monkeypatch.setattr("backend.config.settings.deepseek_api_key", "sk-present")
    status_mod.record_probe("llm", None, probes.ProbeResult(False, "auth_failed", "401"))
    assert status_mod.service_report(BY_KEY["llm"])["state"] == "degraded"

    resp = client.put(
        f"{API}/services/llm",
        headers=auth_headers,
        json={"values": {"deepseek_model": "deepseek-reasoner"}},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["service"]["state"] == "ready"
    assert resp.json()["data"]["service"]["last_check"] is None


def test_capabilities_carry_no_configuration(clean_overrides, test_tenant):
    caps = status_mod.capabilities(test_tenant["id"])
    flat = repr(caps)
    for f in all_fields().values():
        assert f.env not in flat, f"{f.env} leaked into capabilities"
    assert set(caps) >= {"assistant", "email", "whatsapp", "background_worker"}


def test_the_store_being_unreachable_falls_back_to_the_environment(monkeypatch):
    """A deployment configured entirely through .env must not stop working
    because a table could not be read."""
    def boom(*_a, **_k):
        raise RuntimeError("database is down")

    monkeypatch.setattr("backend.service_config.store._load_all", boom)
    store.invalidate()
    try:
        assert effective().deepseek_model == settings.deepseek_model
        assert store.store_available() is False
    finally:
        store.invalidate()


# ── 5. Access — the part that is a security boundary ────────────────────────

def test_capabilities_are_open_to_any_authenticated_user(client, viewer_headers):
    resp = client.get(f"{API}/capabilities", headers=viewer_headers)
    assert resp.status_code == 200
    assert "assistant" in resp.json()["data"]


def test_capabilities_require_authentication(client):
    assert client.get(f"{API}/capabilities").status_code in (401, 403)


def test_a_tenant_admin_is_not_an_instance_operator(client, auth_headers, monkeypatch):
    """The whole point of INSTANCE_ADMIN_EMAILS: signing up must not grant the
    ability to read or rewrite the deployment's credentials."""
    monkeypatch.setattr(
        "backend.config.settings.instance_admin_emails", ["someone.else@example.com"]
    )
    resp = client.get(f"{API}/services", headers=auth_headers)
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "not_instance_operator"


def test_with_no_operator_configured_nobody_edits_the_instance(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr("backend.config.settings.instance_admin_emails", [])
    resp = client.get(f"{API}/services", headers=auth_headers)
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "instance_config_disabled"
    assert resp.json()["error_params"]["env"] == "INSTANCE_ADMIN_EMAILS"


def test_an_operator_sees_every_service(client, auth_headers, operator):
    resp = client.get(f"{API}/services", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert {s["key"] for s in data["services"]} == {s.key for s in SERVICES}
    assert data["undocumented_settings"] == []
    assert data["operator"]["editing_enabled"] is True


def test_an_operator_writes_and_the_value_takes_effect(
    client, auth_headers, operator, clean_overrides
):
    resp = client.put(
        f"{API}/services/llm",
        headers=auth_headers,
        json={"values": {"deepseek_model": "deepseek-reasoner"}},
    )
    assert resp.status_code == 200, resp.text
    # State change asserted against the database, not the response echo.
    row = query_one(
        "SELECT value_plain, tenant_id FROM service_config WHERE field = 'deepseek_model'"
    )
    assert row["value_plain"] == "deepseek-reasoner"
    assert row["tenant_id"] is None
    assert effective().deepseek_model == "deepseek-reasoner"


def test_a_viewer_cannot_write_and_nothing_changes(
    client, viewer_headers, monkeypatch, viewer_user, clean_overrides
):
    """The permission pair the house rules require: denied AND state unchanged."""
    monkeypatch.setattr(
        "backend.config.settings.instance_admin_emails", [viewer_user["email"]]
    )
    resp = client.put(
        f"{API}/services/llm",
        headers=viewer_headers,
        json={"values": {"deepseek_model": "deepseek-reasoner"}},
    )
    assert resp.status_code == 403
    assert query("SELECT id FROM service_config") == []


def test_an_analyst_cannot_write_and_nothing_changes(
    client, analyst_headers, monkeypatch, analyst_user, clean_overrides
):
    monkeypatch.setattr(
        "backend.config.settings.instance_admin_emails", [analyst_user["email"]]
    )
    resp = client.put(
        f"{API}/services/llm",
        headers=analyst_headers,
        json={"values": {"deepseek_model": "deepseek-reasoner"}},
    )
    assert resp.status_code == 403
    assert query("SELECT id FROM service_config") == []


def test_an_operator_cannot_write_an_environment_only_service(
    client, auth_headers, operator, clean_overrides
):
    resp = client.put(
        f"{API}/services/core",
        headers=auth_headers,
        json={"values": {"secret_key": "hijacked"}},
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "service_not_editable"
    assert query("SELECT id FROM service_config") == []


def test_writing_a_secret_with_no_encryption_key_is_a_stated_refusal(
    client, auth_headers, operator, clean_overrides, monkeypatch
):
    monkeypatch.setattr("backend.config.settings.integrations_secret_key", "")
    resp = client.put(
        f"{API}/services/llm",
        headers=auth_headers,
        json={"values": {"deepseek_api_key": "sk-x"}},
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "secret_storage_unavailable"
    assert query("SELECT id FROM service_config") == []


def test_the_instance_report_never_returns_a_secret_in_the_clear(
    client, auth_headers, operator, clean_overrides, fernet_key
):
    store.set_values("llm", {"deepseek_api_key": "sk-do-not-leak-me"}, updated_by="t")
    resp = client.get(f"{API}/services", headers=auth_headers)
    assert resp.status_code == 200
    assert "sk-do-not-leak-me" not in resp.text


def test_an_unknown_service_is_a_404(client, auth_headers, operator):
    resp = client.get(f"{API}/services/nope", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "service_not_found"


# ── 6. The per-tenant exception ─────────────────────────────────────────────

def test_a_tenant_admin_configures_its_own_channel(
    client, auth_headers, registered_user, clean_overrides
):
    resp = client.put(
        f"{API}/tenant/services/whatsapp",
        headers=auth_headers,
        json={"values": {"twilio_whatsapp_from": "whatsapp:+50688887777"}},
    )
    assert resp.status_code == 200, resp.text
    row = query_one(
        "SELECT tenant_id, value_plain FROM service_config "
        " WHERE field = 'twilio_whatsapp_from'"
    )
    assert row["tenant_id"] == registered_user["tenant"]["id"]
    assert row["value_plain"] == "whatsapp:+50688887777"


def test_a_tenant_write_lands_in_its_own_scope_only(
    client, auth_headers, registered_user, clean_overrides
):
    """Scope comes from the token. There is no body field to forge."""
    client.put(
        f"{API}/tenant/services/whatsapp",
        headers=auth_headers,
        json={"values": {"twilio_whatsapp_from": "whatsapp:+50688887777"}},
    )
    other_tenant = str(uuid4())
    assert effective(other_tenant).twilio_whatsapp_from == settings.twilio_whatsapp_from
    assert (
        effective(registered_user["tenant"]["id"]).twilio_whatsapp_from
        == "whatsapp:+50688887777"
    )


def test_a_tenant_cannot_reach_an_instance_only_service(
    client, auth_headers, clean_overrides
):
    resp = client.put(
        f"{API}/tenant/services/llm",
        headers=auth_headers,
        json={"values": {"deepseek_model": "deepseek-reasoner"}},
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "service_not_tenant_scoped"
    assert query("SELECT id FROM service_config") == []


def test_a_tenant_cannot_take_over_a_field_it_could_never_read(
    client, auth_headers, clean_overrides
):
    """`WHATSAPP_WEBHOOK_BASE_URL` lives on a tenant-scoped service, but the
    inbound webhook verifies Twilio's signature BEFORE it knows which tenant
    the message belongs to — so it can only ever read the instance value.
    Accepting a per-tenant one would store a setting displayed as in effect and
    read by nothing, which is the exact silent no-op this layer exists to make
    impossible."""
    resp = client.put(
        f"{API}/tenant/services/whatsapp",
        headers=auth_headers,
        json={"values": {"whatsapp_webhook_base_url": "https://evil.example.com"}},
    )
    assert resp.status_code == 422
    assert query("SELECT id FROM service_config") == []

    # The panel does not offer it in that scope either.
    listing = client.get(f"{API}/tenant/services", headers=auth_headers)
    whatsapp = next(
        s for s in listing.json()["data"]["services"] if s["key"] == "whatsapp"
    )
    assert "whatsapp_webhook_base_url" not in whatsapp["editable_fields"]

    # ...and the operator still can, at instance scope.
    assert "whatsapp_webhook_base_url" in [
        f.key for f in editable_fields(BY_KEY["whatsapp"])
    ]


def test_a_viewer_cannot_write_a_tenant_channel(
    client, viewer_headers, clean_overrides
):
    resp = client.put(
        f"{API}/tenant/services/whatsapp",
        headers=viewer_headers,
        json={"values": {"twilio_whatsapp_from": "whatsapp:+50688887777"}},
    )
    assert resp.status_code == 403
    assert query("SELECT id FROM service_config") == []


def test_a_tenants_own_channel_makes_its_capability_true(
    client, auth_headers, registered_user, clean_overrides, fernet_key, monkeypatch
):
    """The reason the tenant scope exists at all: a deployment with no WhatsApp
    channel of its own can still message THIS tenant's people."""
    monkeypatch.setattr("backend.config.settings.twilio_account_sid", "")
    monkeypatch.setattr("backend.config.settings.twilio_auth_token", "")
    monkeypatch.setattr("backend.config.settings.twilio_whatsapp_from", "")
    tenant_id = registered_user["tenant"]["id"]
    assert status_mod.capabilities(tenant_id)["whatsapp"] is False

    store.set_values(
        "whatsapp",
        {
            "twilio_account_sid": "AC123",
            "twilio_auth_token": "tok",
            "twilio_whatsapp_from": "whatsapp:+50688887777",
        },
        tenant_id=tenant_id,
        updated_by="test",
    )
    assert status_mod.capabilities(tenant_id)["whatsapp"] is True
    assert status_mod.capabilities(None)["whatsapp"] is False


# ── 6.bis The tenant scope has to REACH the wire ────────────────────────────
# Storing an override, showing it as in effect, and then sending through the
# instance's transport anyway is the same silent lie as not storing it — with a
# screen that swears otherwise. These two tests cover the two halves of the
# path: credential → transport, and caller → sender.

def test_the_transport_uses_the_tenants_own_credentials(
    clean_overrides, fernet_key, test_tenant, monkeypatch
):
    from backend.notifications import email as email_mod

    monkeypatch.setattr("backend.config.settings.resend_api_key", "re_instance_key")
    store.set_values(
        "email",
        {"resend_api_key": "re_tenant_key", "email_from": "Tenant <hola@tenant.test>"},
        tenant_id=test_tenant["id"],
        updated_by="test",
    )

    seen = {}

    class _Resp:
        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["auth"] = (headers or {}).get("Authorization", "")
        seen["from"] = (json or {}).get("from", "")
        return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    email_mod._transport_send(
        "buyer@example.com", "s", "<p>x</p>", tenant_id=test_tenant["id"]
    )
    assert seen["auth"] == "Bearer re_tenant_key"
    assert seen["from"] == "Tenant <hola@tenant.test>"

    # ...and the instance scope is untouched by that tenant's choice.
    seen.clear()
    email_mod._transport_send("buyer@example.com", "s", "<p>x</p>")
    assert seen["auth"] == "Bearer re_instance_key"


def test_the_daily_digest_hands_its_tenant_to_the_sender(monkeypatch):
    """The loop that runs at 08:00 is the one place nobody watches. If it drops
    the scope, a tenant's stored sender is never used and the panel still says
    it is in effect."""
    from backend.inventory import service as inv_svc

    captured: list[dict] = []
    monkeypatch.setattr(
        "backend.notifications.email.send_inventory_alert_email",
        lambda **kw: captured.append(kw) or True,
    )
    monkeypatch.setattr(
        "backend.notifications.whatsapp.send_whatsapp",
        lambda *a, **kw: captured.append(kw) or True,
    )
    # Same arrangement `test_alert_history.py` uses to drive this loop.
    from backend.db import session_store
    from backend.sessions import planning_service

    monkeypatch.setattr(
        inv_svc, "get_tenants_with_active_sessions", lambda: [{"tenant_id": "ten_scope"}])
    monkeypatch.setattr(planning_service, "resolve_active_session", lambda t: "sess-test")
    monkeypatch.setattr(session_store, "get_forecasts", lambda t, s: {})
    monkeypatch.setattr(inv_svc, "list_stock", lambda t, **kw: [])
    monkeypatch.setattr(inv_svc, "get_learned_lead_times", lambda t: {})
    monkeypatch.setattr(
        inv_svc, "_compute_inventory_status",
        lambda *a, **kw: [{"sku": "SKU-1", "signal": "PEDIR_YA", "coverage_days": 1.0,
                           "recommended_qty": 10, "display_name": "P1", "supplier": "Acme"}],
    )
    monkeypatch.setattr(
        inv_svc, "get_tenant_alert_recipients",
        lambda tid: [{"id": "usr_1", "email": "buyer@example.com",
                      "whatsapp_number": "+50688887777"}],
    )
    monkeypatch.setattr(inv_svc, "record_notification_delivery", lambda *a, **kw: None)

    inv_svc.run_daily_inventory_alerts()

    assert captured, "the loop sent nothing — the test's stubs no longer match it"
    assert all(kw.get("tenant_id") == "ten_scope" for kw in captured), (
        f"a send left without its tenant scope: {captured}"
    )


# ── 7. The operator helper itself ───────────────────────────────────────────

def test_an_api_key_is_never_an_instance_operator(monkeypatch):
    from backend.auth.guards import CurrentUser

    monkeypatch.setattr(
        "backend.config.settings.instance_admin_emails", ["owner@example.com"]
    )
    machine = CurrentUser(
        user_id="usr_x", tenant_id="ten_x", role="admin", api_key_id="key_1"
    )
    assert access.is_instance_operator(machine) is False


def test_operator_matching_ignores_case_and_padding(monkeypatch, registered_user):
    from backend.auth.guards import CurrentUser

    monkeypatch.setattr(
        "backend.config.settings.instance_admin_emails",
        [f"  {registered_user['email'].upper()}  "],
    )
    user = CurrentUser(
        user_id=registered_user["user"]["id"],
        tenant_id=registered_user["tenant"]["id"],
        role="admin",
    )
    assert access.is_instance_operator(user) is True
