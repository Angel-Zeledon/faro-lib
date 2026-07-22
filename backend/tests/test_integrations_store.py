"""Test the integration_connections table and integration storage layer."""

from uuid import uuid4

import pytest


def test_integration_connections_table_exists(client):
    """Verify integration_connections table with credentials column exists."""
    from backend.db.connection import query_one

    row = query_one(
        """SELECT column_name FROM information_schema.columns
           WHERE table_name='integration_connections' AND column_name='credentials'"""
    )
    assert row is not None


@pytest.fixture
def fernet_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setattr(
        "backend.config.settings.integrations_secret_key", Fernet.generate_key().decode()
    )


def test_store_encrypts_and_hides_credentials(client, test_tenant, fernet_key):
    from backend.integrations import store
    from backend.db.connection import query_one

    tid = test_tenant["id"]
    conn = store.create_connection(tid, "alegra", {"email": "a@b.com", "token": "SECRET"})
    # stored ciphertext, not plaintext
    row = query_one("SELECT credentials FROM integration_connections WHERE id=%s", (conn["id"],))
    assert "SECRET" not in row["credentials"]
    # list never exposes credentials
    listed = store.list_connections(tid)
    assert "credentials" not in listed[0]
    assert store.get_credentials(conn["id"]) == {"email": "a@b.com", "token": "SECRET"}


def test_create_connection_upserts_by_tenant_and_provider(client, test_tenant, fernet_key):
    from backend.integrations import store

    tid = test_tenant["id"]
    first = store.create_connection(tid, "alegra", {"email": "a@b.com", "token": "OLD"})
    second = store.create_connection(tid, "alegra", {"email": "a@b.com", "token": "NEW"})

    assert second["id"] == first["id"]
    assert store.get_credentials(first["id"]) == {"email": "a@b.com", "token": "NEW"}
    assert len(store.list_connections(tid)) == 1


def test_list_and_get_connection_never_include_credentials(client, test_tenant, fernet_key):
    from backend.integrations import store

    tid = test_tenant["id"]
    conn = store.create_connection(tid, "siigo", {
        "partner_id": "p1", "username": "u1", "access_key": "k1",
    })

    listed = store.list_connections(tid)
    assert len(listed) == 1
    assert "credentials" not in listed[0]
    assert listed[0]["id"] == conn["id"]
    assert listed[0]["provider"] == "siigo"

    fetched = store.get_connection(conn["id"])
    assert fetched is not None
    assert "credentials" not in fetched
    assert fetched["id"] == conn["id"]

    assert store.get_connection(f"intg_{uuid4().hex[:12]}") is None


def test_mark_synced_success_and_error_paths(client, test_tenant, fernet_key):
    from backend.integrations import store

    tid = test_tenant["id"]
    conn = store.create_connection(tid, "alegra", {"email": "a@b.com", "token": "T"})

    store.mark_synced(conn["id"], error="boom")
    failed = store.get_connection(conn["id"])
    assert failed["status"] == "error"
    assert failed["last_error"] == "boom"
    assert failed["last_sync_at"] is not None

    store.mark_synced(conn["id"])
    recovered = store.get_connection(conn["id"])
    assert recovered["status"] == "connected"
    assert recovered["last_error"] is None


def test_delete_connection_is_tenant_scoped(client, test_tenant, fernet_key):
    from backend.integrations import store
    from backend.tenants.service import create_tenant
    from backend.db.connection import execute

    tid = test_tenant["id"]
    other_tenant = create_tenant(f"pytest-other-{uuid4().hex[:10]}")
    try:
        conn = store.create_connection(tid, "alegra", {"email": "a@b.com", "token": "T"})

        # tenant B cannot delete tenant A's connection
        deleted = store.delete_connection(other_tenant["id"], conn["id"])
        assert deleted is False
        assert store.get_connection(conn["id"]) is not None

        # the owning tenant can delete it
        deleted = store.delete_connection(tid, conn["id"])
        assert deleted is True
        assert store.get_connection(conn["id"]) is None
    finally:
        execute("DELETE FROM tenants WHERE id = %s", (other_tenant["id"],))
