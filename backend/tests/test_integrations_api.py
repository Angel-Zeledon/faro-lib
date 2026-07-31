"""Tests for the accounting-integrations API: connect/list/sync/delete
(backend/api/v1/integrations.py). Gated to Enterprise (Feature.INTEGRATIONS).

registry.get_provider is monkeypatched to a FAKE provider throughout so no
real network call is ever made; only the HTTP layer (routing, guards,
permissions, DB state) is under test here.
"""
import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def fernet_key(monkeypatch):
    """The store encrypts credentials with Fernet — tests need a real key."""
    monkeypatch.setattr(
        "backend.config.settings.integrations_secret_key", Fernet.generate_key().decode()
    )


@pytest.fixture
def fake_provider(monkeypatch):
    """A provider whose test_connection always succeeds — no network calls.

    It returns a month of real sales because the pre-training gate holds ERP
    data to the same standard as an upload: a provider that reports nothing
    cannot train anything, and the sync is refused. Tests that want to exercise
    the refusal use `fake_provider_no_sales` below.
    """
    from datetime import date

    from backend.integrations import registry, base

    class FakeProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return [base.ProviderProduct("SKU-Z", "Zeta", 5.0)]
        def fetch_stock(self): return [base.ProviderStock("SKU-Z", 12.0, "principal")]
        def fetch_sales(self, since=None):
            return [base.ProviderSaleLine(date(2026, 1, d), "SKU-Z", 3.0 + (d % 4), 8.0)
                    for d in range(1, 32)]

    monkeypatch.setattr(registry, "get_provider", lambda name, creds: FakeProvider(creds))
    return FakeProvider


@pytest.fixture
def fake_provider_no_sales(monkeypatch):
    """Connects fine, reports no sales at all — the gate must refuse the sync."""
    from backend.integrations import registry, base

    class EmptyProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return []
        def fetch_stock(self): return []
        def fetch_sales(self, since=None): return []

    monkeypatch.setattr(registry, "get_provider", lambda name, creds: EmptyProvider(creds))
    return EmptyProvider


@pytest.fixture
def fake_provider_bad_creds(monkeypatch):
    """A provider whose test_connection always rejects credentials."""
    from backend.integrations import registry, base

    class RejectingProvider(base.AccountingProvider):
        def test_connection(self):
            raise base.IntegrationAuthError("bad credentials")
        def fetch_products(self): return []
        def fetch_stock(self): return []
        def fetch_sales(self, since=None): return []

    monkeypatch.setattr(registry, "get_provider", lambda name, creds: RejectingProvider(creds))
    return RejectingProvider


CREDS = {"email": "a@b.com", "token": "t"}


def _connection_count(tid):
    from backend.db.connection import query_one
    return query_one(
        "SELECT COUNT(*) c FROM integration_connections WHERE tenant_id=%s", (tid,)
    )["c"]


# ── Gating: non-Enterprise plan ─────────────────────────────────────────────

def test_connect_rejected_for_non_enterprise_plan(
    client, make_tenant_user_headers, monkeypatch, fernet_key, fake_provider,
):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    # role="admin" so the only thing that can block this request is the
    # plan-entitlement gate — isolates the PLAN_UPGRADE_REQUIRED assertion
    # from role-permission behavior (covered separately below).
    headers, tid = make_tenant_user_headers(plan="starter", role="admin", return_tenant_id=True)

    resp = client.post(
        "/api/v1/integrations/alegra/connect", json=CREDS, headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "PLAN_UPGRADE_REQUIRED"
    assert _connection_count(tid) == 0


# ── Permission pair: viewer denied, admin succeeds ──────────────────────────

def test_viewer_denied_connect_and_delete_no_state_change(
    client, make_tenant_user_headers, fernet_key, fake_provider,
):
    headers, tid = make_tenant_user_headers(plan="enterprise", role="viewer", return_tenant_id=True)

    resp = client.post(
        "/api/v1/integrations/alegra/connect", json=CREDS, headers=headers,
    )
    assert resp.status_code == 403
    assert _connection_count(tid) == 0

    resp = client.delete("/api/v1/integrations/nonexistent-id", headers=headers)
    assert resp.status_code == 403
    assert _connection_count(tid) == 0


def test_admin_connect_succeeds_and_response_has_no_credentials(
    client, make_tenant_user_headers, fernet_key, fake_provider,
):
    headers, tid = make_tenant_user_headers(plan="enterprise", role="admin", return_tenant_id=True)

    resp = client.post(
        "/api/v1/integrations/alegra/connect", json=CREDS, headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["provider"] == "alegra"
    assert "credentials" not in body
    assert "creds" not in body

    assert _connection_count(tid) == 1

    from backend.db.connection import query_one
    row = query_one(
        "SELECT provider, status FROM integration_connections WHERE tenant_id=%s", (tid,)
    )
    assert row["provider"] == "alegra"
    assert row["status"] == "connected"


def test_analyst_denied_connect(client, make_tenant_user_headers, fernet_key, fake_provider):
    # connect is admin-only; analyst is not "admin or above" for this route
    headers, tid = make_tenant_user_headers(plan="enterprise", role="analyst", return_tenant_id=True)
    resp = client.post(
        "/api/v1/integrations/alegra/connect", json=CREDS, headers=headers,
    )
    assert resp.status_code == 403
    assert _connection_count(tid) == 0


# ── Connect with bad credentials ────────────────────────────────────────────

def test_connect_bad_credentials_returns_400_no_row(
    client, make_tenant_user_headers, fernet_key, fake_provider_bad_creds,
):
    headers, tid = make_tenant_user_headers(plan="enterprise", role="admin", return_tenant_id=True)

    resp = client.post(
        "/api/v1/integrations/alegra/connect", json=CREDS, headers=headers,
    )
    assert resp.status_code == 400
    assert _connection_count(tid) == 0


def test_connect_unknown_provider_returns_404(
    client, make_tenant_user_headers, fernet_key, fake_provider,
):
    headers, tid = make_tenant_user_headers(plan="enterprise", role="admin", return_tenant_id=True)

    resp = client.post(
        "/api/v1/integrations/not-a-real-provider/connect", json=CREDS, headers=headers,
    )
    assert resp.status_code == 404
    assert _connection_count(tid) == 0


# ── List — no credentials leak ──────────────────────────────────────────────

def test_list_includes_providers_and_never_credentials(
    client, make_tenant_user_headers, fernet_key, fake_provider,
):
    headers, tid = make_tenant_user_headers(plan="enterprise", role="admin", return_tenant_id=True)
    client.post("/api/v1/integrations/alegra/connect", json=CREDS, headers=headers)

    resp = client.get("/api/v1/integrations", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "providers" in data and "alegra" in data["providers"]
    assert len(data["connections"]) == 1
    assert "credentials" not in data["connections"][0]
    assert "token" not in str(data["connections"][0])


# ── Sync: analyst can trigger, cross-tenant is 404 ──────────────────────────

def test_sync_triggers_job_and_cross_tenant_is_404(
    client, make_tenant_user_headers, fernet_key, fake_provider,
):
    admin_headers, tid = make_tenant_user_headers(plan="enterprise", role="admin", return_tenant_id=True)
    connect_resp = client.post(
        "/api/v1/integrations/alegra/connect", json=CREDS, headers=admin_headers,
    )
    connection_id = connect_resp.json()["data"]["id"]

    from backend.db.connection import query_one
    jobs_before = query_one("SELECT COUNT(*) c FROM jobs WHERE tenant_id=%s", (tid,))["c"]

    analyst_headers, _ = make_tenant_user_headers(
        plan="enterprise", role="analyst", return_tenant_id=True
    )
    # Analyst belongs to a DIFFERENT tenant than the connection here — reuse
    # the admin's own tenant instead by creating an analyst in the same tenant.
    # (make_tenant_user_headers always creates a fresh tenant, so build an
    # analyst user directly inside tid.)
    from backend.users import service as user_svc
    from uuid import uuid4
    email = f"analyst-{uuid4().hex[:8]}@example.com"
    password = "TestPass123!"
    new_user = user_svc.create_user(tenant_id=tid, email=email, password=password,
                                     role="analyst", full_name="Sync Analyst")
    user_svc.mark_verified(tid, new_user["id"])

    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    same_tenant_analyst_headers = {
        "Authorization": f"Bearer {login.json()['data']['access_token']}"
    }

    resp = client.post(
        f"/api/v1/integrations/{connection_id}/sync", headers=same_tenant_analyst_headers,
    )
    assert resp.status_code == 200

    jobs_after = query_one("SELECT COUNT(*) c FROM jobs WHERE tenant_id=%s", (tid,))["c"]
    assert jobs_after == jobs_before + 1

    # Cross-tenant: analyst from a totally different (enterprise) tenant
    # cannot sync this connection.
    other_analyst_headers, _ = make_tenant_user_headers(
        plan="enterprise", role="analyst", return_tenant_id=True
    )
    resp = client.post(
        f"/api/v1/integrations/{connection_id}/sync", headers=other_analyst_headers,
    )
    assert resp.status_code == 404


def test_viewer_denied_sync(client, make_tenant_user_headers, fernet_key, fake_provider):
    admin_headers, tid = make_tenant_user_headers(plan="enterprise", role="admin", return_tenant_id=True)
    connect_resp = client.post(
        "/api/v1/integrations/alegra/connect", json=CREDS, headers=admin_headers,
    )
    connection_id = connect_resp.json()["data"]["id"]

    from backend.users import service as user_svc
    from uuid import uuid4
    email = f"viewer-{uuid4().hex[:8]}@example.com"
    password = "TestPass123!"
    new_user = user_svc.create_user(tenant_id=tid, email=email, password=password,
                                     role="viewer", full_name="Sync Viewer")
    user_svc.mark_verified(tid, new_user["id"])
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    viewer_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    resp = client.post(
        f"/api/v1/integrations/{connection_id}/sync", headers=viewer_headers,
    )
    assert resp.status_code == 403


# ── Delete ───────────────────────────────────────────────────────────────────

def test_admin_delete_removes_row(client, make_tenant_user_headers, fernet_key, fake_provider):
    headers, tid = make_tenant_user_headers(plan="enterprise", role="admin", return_tenant_id=True)
    connect_resp = client.post(
        "/api/v1/integrations/alegra/connect", json=CREDS, headers=headers,
    )
    connection_id = connect_resp.json()["data"]["id"]
    assert _connection_count(tid) == 1

    resp = client.delete(f"/api/v1/integrations/{connection_id}", headers=headers)
    assert resp.status_code == 200
    assert _connection_count(tid) == 0
