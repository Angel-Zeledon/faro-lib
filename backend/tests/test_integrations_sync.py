"""Tests for the accounting-integrations sync service: fetch provider data
-> import stock -> build a sales dataset -> auto-train (backend/integrations/
sync_service.py). Provider is faked via monkeypatching registry.get_provider
so no real network call is ever made.
"""
from datetime import date

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def fernet_key(monkeypatch):
    monkeypatch.setattr(
        "backend.config.settings.integrations_secret_key", Fernet.generate_key().decode()
    )


def test_sync_imports_stock_dataset_and_enqueues_training(client, test_tenant, monkeypatch, fernet_key):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    from backend.integrations import store, registry, sync_service, base
    from backend.db.connection import query_one

    class FakeProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return [base.ProviderProduct("SKU-Z", "Zeta", 5.0)]
        def fetch_stock(self): return [base.ProviderStock("SKU-Z", 12.0, "principal")]
        def fetch_sales(self, since=None):
            return [base.ProviderSaleLine(date(2026, 1, d), "SKU-Z", 3.0, 8.0) for d in range(1, 20)]
    monkeypatch.setattr(registry, "get_provider", lambda name, creds: FakeProvider(creds))

    tid = test_tenant["id"]
    conn = store.create_connection(tid, "alegra", {"email": "a@b.com", "token": "t"})
    result = sync_service.sync_connection(conn["id"])

    assert result["session_id"] and result["job_id"] and result["dataset_id"]
    assert "SKU-Z" in result["stock_synced"]

    # stock imported
    row = query_one("SELECT current_stock, unit_cost FROM inventory_stock WHERE tenant_id=%s AND sku=%s",
                     (tid, "SKU-Z"))
    assert float(row["current_stock"]) == 12.0
    assert float(row["unit_cost"]) == 5.0

    # a dataset + session were created and a job enqueued
    assert query_one("SELECT COUNT(*) c FROM datasets WHERE tenant_id=%s", (tid,))["c"] >= 1
    assert query_one("SELECT COUNT(*) c FROM jobs WHERE tenant_id=%s", (tid,))["c"] >= 1

    sess = query_one("SELECT status FROM sessions WHERE id=%s AND tenant_id=%s",
                      (result["session_id"], tid))
    assert sess["status"] == "QUEUED"

    cfg = query_one("SELECT * FROM session_configs WHERE session_id=%s AND tenant_id=%s",
                     (result["session_id"], tid))
    assert cfg["columns_cfg"]["schema_version"] == "canonical_v1"
    assert cfg["models_cfg"]["selected_models"]

    # last_sync_at set, no error
    conn_row = query_one("SELECT last_sync_at, last_error, status FROM integration_connections WHERE id=%s",
                          (conn["id"],))
    assert conn_row["last_sync_at"] is not None
    assert conn_row["last_error"] is None
    assert conn_row["status"] == "connected"


def test_sync_records_error_on_provider_failure(client, test_tenant, monkeypatch, fernet_key):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    from backend.integrations import store, registry, sync_service, base
    from backend.db.connection import query_one

    class BoomProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): raise base.IntegrationSyncError("provider is down")
        def fetch_stock(self): return []
        def fetch_sales(self, since=None): return []
    monkeypatch.setattr(registry, "get_provider", lambda name, creds: BoomProvider(creds))

    tid = test_tenant["id"]
    conn = store.create_connection(tid, "siigo", {"partner_id": "p", "username": "u", "access_key": "k"})

    datasets_before = query_one("SELECT COUNT(*) c FROM datasets WHERE tenant_id=%s", (tid,))["c"]
    jobs_before = query_one("SELECT COUNT(*) c FROM jobs WHERE tenant_id=%s", (tid,))["c"]

    with pytest.raises(base.IntegrationSyncError):
        sync_service.sync_connection(conn["id"])

    conn_row = query_one("SELECT last_sync_at, last_error, status FROM integration_connections WHERE id=%s",
                          (conn["id"],))
    assert conn_row["status"] == "error"
    assert "provider is down" in conn_row["last_error"]
    assert conn_row["last_sync_at"] is not None

    # No dataset/job created on failure
    assert query_one("SELECT COUNT(*) c FROM datasets WHERE tenant_id=%s", (tid,))["c"] == datasets_before
    assert query_one("SELECT COUNT(*) c FROM jobs WHERE tenant_id=%s", (tid,))["c"] == jobs_before


def test_run_daily_syncs_all_connections_isolating_failures(client, test_tenant, monkeypatch, fernet_key):
    """The daily scheduler loop body (`run_daily_integration_syncs`) must
    attempt every connection across every tenant and isolate per-connection
    failures: one tenant's broken provider must not stop another tenant's
    sync from completing, and the loop itself must never raise."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    from backend.integrations import store, registry, sync_service, base
    from backend.db.connection import query_one, execute
    from backend.tenants.service import create_tenant
    from uuid import uuid4

    class HealthyProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return [base.ProviderProduct("SKU-OK", "Okay", 4.0)]
        def fetch_stock(self): return [base.ProviderStock("SKU-OK", 7.0, "principal")]
        def fetch_sales(self, since=None):
            return [base.ProviderSaleLine(date(2026, 1, d), "SKU-OK", 2.0, 6.0) for d in range(1, 20)]

    class BrokenProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): raise base.IntegrationSyncError("connection reset by provider")
        def fetch_stock(self): return []
        def fetch_sales(self, since=None): return []

    def fake_get_provider(name, creds):
        if name == "alegra":
            return HealthyProvider(creds)
        return BrokenProvider(creds)

    monkeypatch.setattr(registry, "get_provider", fake_get_provider)

    healthy_tenant = test_tenant
    broken_tenant = create_tenant(f"pytest-{uuid4().hex[:10]}")
    try:
        healthy_conn = store.create_connection(
            healthy_tenant["id"], "alegra", {"email": "a@b.com", "token": "t"}
        )
        broken_conn = store.create_connection(
            broken_tenant["id"], "siigo", {"partner_id": "p", "username": "u", "access_key": "k"}
        )

        # The loop body must not raise even though one connection fails.
        sync_service.run_daily_integration_syncs()

        healthy_row = query_one(
            "SELECT last_sync_at, last_error, status FROM integration_connections WHERE id=%s",
            (healthy_conn["id"],),
        )
        assert healthy_row["last_sync_at"] is not None
        assert healthy_row["status"] == "connected"
        assert healthy_row["last_error"] is None

        broken_row = query_one(
            "SELECT last_sync_at, last_error, status FROM integration_connections WHERE id=%s",
            (broken_conn["id"],),
        )
        assert broken_row["status"] == "error"
        assert "connection reset by provider" in broken_row["last_error"]
    finally:
        execute("DELETE FROM tenants WHERE id = %s", (broken_tenant["id"],))
