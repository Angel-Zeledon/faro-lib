"""A deployment with no credentials at all must still be a working product.

This is the state every buyer of this source starts in and the one nobody
develops in: `backend/.env` filled with the three required values and NOTHING
else. No language model, no mail transport, no Twilio, no vector store, no
Fernet key for integrations.

The promise the product makes about that state is specific, and it is the whole
reason `service_config` exists:

  1. **Nothing explodes.** No endpoint answers 5xx because a key is absent. A
     500 says "the server broke"; a missing key is the operator's to fix, and
     conflating them sends whoever debugs it to the wrong layer — the exact
     failure `test_error_envelope_and_bounds` was written for.
  2. **Everything says what is missing.** Not "something went wrong": the name
     of the variable, or a stated code the UI turns into that sentence.
  3. **The core still works.** Forecasts, the stock signal and purchase orders
     depend on nothing external, so they must behave identically.

These tests blank the credentials rather than trusting the machine's `.env`,
because the machine that runs them is the one machine in the world that has
every key configured.
"""

import pytest

from backend.db.connection import query
from backend.service_config import store

# Every optional credential in the registry, by Settings attribute. Blanking
# these IS the virgin install.
OPTIONAL_KEYS = [
    "deepseek_api_key",
    "resend_api_key", "smtp_user", "smtp_pass",
    "twilio_account_sid", "twilio_auth_token", "twilio_whatsapp_from",
    "twilio_sms_from",
    "voyageai_api_key", "pinecone_api_key", "pinecone_index",
    "integrations_secret_key",
    "contact_whatsapp", "contact_email", "upgrade_notify_email",
    "instance_admin_emails",
]


@pytest.fixture
def virgin(monkeypatch):
    """A deployment with the three required values and nothing else."""
    for key in OPTIONAL_KEYS:
        blank = [] if key == "instance_admin_emails" else ""
        monkeypatch.setattr(f"backend.config.settings.{key}", blank)
    # No stored overrides either — a fresh database has none.
    from backend.db.connection import execute
    execute("DELETE FROM service_config")
    store.invalidate()
    yield
    execute("DELETE FROM service_config")
    store.invalidate()


def _assert_not_a_server_error(resp, what: str):
    assert resp.status_code < 500, (
        f"{what} answered {resp.status_code} on a deployment with no keys. "
        f"A missing credential is the operator's to fix and must never be "
        f"reported as the server breaking. Body: {resp.text[:300]}"
    )


# ── 1. The product still reports itself honestly ────────────────────────────

def test_health_answers_and_names_every_service_that_is_off(client, virgin):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] is True
    services = body["services"]
    for key in ("llm", "email", "whatsapp", "sms", "rag", "integrations", "contact"):
        assert services[key] == "not_configured", (
            f"{key} claims '{services[key]}' with none of its credentials set"
        )
    # ...and the part that needs nothing external is up.
    assert services["core"] == "ready"


def test_capabilities_tell_a_user_what_cannot_answer(client, auth_headers, virgin):
    resp = client.get("/api/v1/service-config/capabilities", headers=auth_headers)
    assert resp.status_code == 200
    caps = resp.json()["data"]
    for key in ("assistant", "documents_search", "email", "whatsapp", "sms",
                "accounting_integrations"):
        assert caps[key] is False, f"{key} claims to work with no credentials"
    assert caps["contact_channels"] == {"whatsapp": False, "email": False}


# ── 2. Nothing explodes ─────────────────────────────────────────────────────

def test_the_assistant_refuses_in_words_instead_of_crashing(
    client, auth_headers, virgin, monkeypatch
):
    """The chat is the surface a buyer tries first and the one with the most
    moving parts behind it."""
    from backend.ai.local_llm import LLMNotConfigured

    def unconfigured(*_a, **_k):
        raise LLMNotConfigured("DEEPSEEK_API_KEY is not set")

    monkeypatch.setattr("backend.ai.local_llm.get_local_llm_client", unconfigured)

    created = client.post("/api/v1/chats", json={"title": "virgin"},
                          headers=auth_headers)
    _assert_not_a_server_error(created, "POST /chats")
    if created.status_code >= 400:
        return  # refusing to open a chat at all is honest too

    chat_id = created.json()["data"]["id"]
    resp = client.post(f"/api/v1/chats/{chat_id}/messages",
                       json={"content": "¿qué compro hoy?"}, headers=auth_headers)
    _assert_not_a_server_error(resp, "POST /chats/{id}/messages")


def test_the_integrations_screen_says_it_is_off(client, auth_headers, virgin):
    """No Fernet key means no credential can be stored, so the feature is off —
    and it has to SAY so rather than fail at the moment somebody pastes a
    password."""
    resp = client.get("/api/v1/integrations", headers=auth_headers)
    _assert_not_a_server_error(resp, "GET /integrations")
    if resp.status_code == 200:
        return
    assert resp.json().get("error_code"), "refused without a code the UI can render"


def test_the_upgrade_request_survives_having_nobody_to_notify(
    client, auth_headers, virgin
):
    """With no contact channel there is nobody to email — the ask must still be
    recorded, or somebody trying to pay becomes nothing at all."""
    resp = client.post("/api/v1/entitlements/upgrade-request",
                       json={"limit_key": "max_skus", "message": "necesito más"},
                       headers=auth_headers)
    _assert_not_a_server_error(resp, "POST /entitlements/upgrade-request")
    if resp.status_code < 300:
        rows = query("SELECT id FROM upgrade_requests")
        assert rows, "the request was accepted and stored nowhere"


def test_the_daily_alert_loop_runs_with_no_channel_at_all(virgin):
    """The 08:00 loop touches email AND WhatsApp. With neither configured it
    must complete, not raise into the scheduler and kill the other tenants'
    alerts behind it."""
    from backend.inventory import service as inv_svc

    inv_svc.run_daily_inventory_alerts()  # must not raise


def test_the_freshness_loop_runs_with_no_channel_at_all(virgin):
    from backend.notifications import freshness_service as fs

    fs.run_daily_freshness_reminders()  # must not raise


def test_every_probe_reports_instead_of_raising(virgin):
    """The panel's own diagnostics are the last thing that may explode: they run
    exactly when everything else is already broken."""
    from backend.service_config import status as status_mod
    from backend.service_config.registry import SERVICES

    for service in SERVICES:
        if not service.probe:
            continue
        result = status_mod.run_probe(service.key)
        assert result.ok is False
        assert result.code == "not_configured", (
            f"{service.key} probed as '{result.code}' with no credentials"
        )
        assert result.detail, f"{service.key} failed without saying what is missing"


# ── 3. The core is untouched ────────────────────────────────────────────────

def test_the_product_itself_does_not_depend_on_any_of_it(
    client, auth_headers, virgin
):
    """Forecasting, the signal and the orders are what the buyer is buying. None
    of them may notice that every optional service is off."""
    for path in ("/api/v1/inventory/status", "/api/v1/planning",
                 "/api/v1/sessions", "/api/v1/entitlements"):
        resp = client.get(path, headers=auth_headers)
        _assert_not_a_server_error(resp, f"GET {path}")
        assert resp.status_code != 403, f"GET {path} was refused on a virgin install"


# ── 4. The panel is usable on a virgin install, and stops being implicit ────
# The whole feature is worthless on day one if the only way to reach it is to
# edit the file it exists to replace. These pin both halves: it opens for the
# installer, and it closes the moment "the only company" stops being true.

def test_the_installer_can_open_the_panel_with_no_operator_configured(
    client, auth_headers, virgin, registered_user, monkeypatch
):
    """A fresh deployment names nobody. Its admin must still be able to
    configure it, or the panel is decoration on the day it matters most.

    `sole_tenant_id` is pinned rather than skipped-around: the development
    database holds hundreds of tenants from previous runs, so the real query
    answers None here and the test that matters most would never execute on the
    machine that runs it. What "exactly one tenant" MEANS is covered separately
    by the three tests below; this one is about what the endpoint does once that
    is true.
    """
    from backend.service_config import access

    monkeypatch.setattr(
        access, "sole_tenant_id", lambda: registered_user["tenant"]["id"]
    )
    assert access.bootstrap_scope() == registered_user["tenant"]["id"]

    resp = client.get("/api/v1/service-config/services", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    operator = resp.json()["data"]["operator"]
    assert operator["editing_enabled"] is True
    assert operator["bootstrap"] is True, "the panel must say the access is implicit"
    assert operator["explicit"] is False


def test_a_second_company_ends_the_implicit_access(monkeypatch, virgin):
    """Otherwise "the only tenant" would quietly mean "whoever signed up first",
    and that account could read every other company's credentials."""
    from backend.service_config import access
    from backend.auth.guards import CurrentUser

    monkeypatch.setattr(access, "sole_tenant_id", lambda: None)  # i.e. two exist
    assert access.bootstrap_scope() is None
    assert access.instance_editing_enabled() is False
    assert access.is_instance_operator(
        CurrentUser(user_id="usr_1", tenant_id="ten_first", role="admin")
    ) is False


def test_a_named_operator_beats_the_implicit_one(monkeypatch, virgin):
    """A deployment that answered the question does not get a second answer."""
    from backend.service_config import access

    monkeypatch.setattr(access, "sole_tenant_id", lambda: "ten_sole")
    monkeypatch.setattr(
        "backend.config.settings.instance_admin_emails", ["owner@example.com"]
    )
    assert access.bootstrap_scope() is None


def test_an_unreachable_database_grants_nothing(monkeypatch, virgin):
    """`sole_tenant_id` answers a question with a query. A query that fails is
    not a yes — failing open here would hand the panel to any admin the moment
    the database hiccuped."""
    from backend.service_config import access

    def boom(*_a, **_k):
        raise RuntimeError("database is down")

    monkeypatch.setattr("backend.db.connection.query", boom)
    assert access.sole_tenant_id() is None
    assert access.instance_editing_enabled() is False


def test_the_bootstrap_operator_is_one_tenant_and_not_any_admin(
    client, virgin, registered_user, make_tenant_user_headers, monkeypatch
):
    """The implicit grant is to the installer's tenant, not to the role. Another
    company's admin, on the same deployment, stays out — otherwise "first run"
    would be a way in for everybody."""
    from backend.service_config import access

    monkeypatch.setattr(
        access, "sole_tenant_id", lambda: registered_user["tenant"]["id"]
    )
    other = make_tenant_user_headers(role="admin")

    resp = client.get("/api/v1/service-config/services", headers=other)
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "not_instance_operator"

    # ...and that admin keeps its own channels, which is the whole point of the
    # split: it is not locked out of the product, only out of the deployment.
    mine = client.get("/api/v1/service-config/tenant/services", headers=other)
    assert mine.status_code == 200
    assert [s["key"] for s in mine.json()["data"]["services"]]
