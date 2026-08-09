"""
Tenant data export (ZIP) + cascade delete — Costa Rica Ley 8968 / GDPR-style
right to export & erasure.

Assert state changes with direct DB queries (per repo testing mandate), not
just status codes: the export ZIP must contain only the caller tenant's rows,
and delete must actually remove the tenant row and its children from the DB.
"""
import io
import json
import zipfile
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one


def _seed_business_data(tenant_id: str, sku: str) -> None:
    """Minimal cross-table footprint so export/delete have real rows to touch."""
    execute(
        """INSERT INTO inventory_stock (id, tenant_id, sku, display_name, current_stock, min_stock)
           VALUES (gen_random_uuid()::text, %s, %s, 'Test SKU', 10, 5)""",
        (tenant_id, sku),
    )
    execute(
        """INSERT INTO suppliers (id, tenant_id, name, lead_time_days)
           VALUES (gen_random_uuid()::text, %s, %s, 10)""",
        (tenant_id, f"Supplier-{uuid4().hex[:6]}"),
    )


class TestExportPermissions:

    def test_viewer_denied(self, client, viewer_headers):
        resp = client.get("/api/v1/tenant/export", headers=viewer_headers)
        assert resp.status_code == 403

    def test_analyst_denied(self, client, analyst_headers):
        resp = client.get("/api/v1/tenant/export", headers=analyst_headers)
        assert resp.status_code == 403

    def test_admin_gets_zip(self, client, auth_headers, test_tenant):
        _seed_business_data(test_tenant["id"], f"SKU-{uuid4().hex[:8]}")
        resp = client.get("/api/v1/tenant/export", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/zip"

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "tenant.json" in names
        assert "inventory_stock.json" in names
        assert "users.json" in names

        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["tenant_id"] == test_tenant["id"]
        assert manifest["tables"]["inventory_stock"] >= 1
        assert manifest["tables"]["suppliers"] >= 1

        # No secrets leaked: users.json must never carry the password hash.
        users_rows = json.loads(zf.read("users.json"))
        assert len(users_rows) >= 1
        for row in users_rows:
            assert "hashed_password" not in row


class TestExportTenantIsolation:

    def test_export_excludes_other_tenant_rows(self, client, auth_headers, test_tenant):
        my_sku = f"MINE-{uuid4().hex[:8]}"
        other_sku = f"OTHER-{uuid4().hex[:8]}"
        _seed_business_data(test_tenant["id"], my_sku)

        from backend.tenants.service import create_tenant
        other_tenant = create_tenant(f"pytest-other-{uuid4().hex[:8]}")
        try:
            _seed_business_data(other_tenant["id"], other_sku)

            resp = client.get("/api/v1/tenant/export", headers=auth_headers)
            assert resp.status_code == 200, resp.text
            zf = zipfile.ZipFile(io.BytesIO(resp.content))

            stock_rows = json.loads(zf.read("inventory_stock.json"))
            skus = {r["sku"] for r in stock_rows}
            assert my_sku in skus
            assert other_sku not in skus

            tenant_row = json.loads(zf.read("tenant.json"))
            assert tenant_row["id"] == test_tenant["id"]
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other_tenant["id"],))


class TestDeletePermissions:

    def test_viewer_denied_tenant_still_exists(self, client, viewer_headers, test_tenant):
        resp = client.request(
            "DELETE", "/api/v1/tenant", headers=viewer_headers,
            json={"confirm": "DELETE"},
        )
        assert resp.status_code == 403
        assert query_one("SELECT id FROM tenants WHERE id = %s", (test_tenant["id"],)) is not None

    def test_analyst_denied_tenant_still_exists(self, client, analyst_headers, test_tenant):
        resp = client.request(
            "DELETE", "/api/v1/tenant", headers=analyst_headers,
            json={"confirm": "DELETE"},
        )
        assert resp.status_code == 403
        assert query_one("SELECT id FROM tenants WHERE id = %s", (test_tenant["id"],)) is not None


class TestDeleteConfirmationGuard:

    def test_missing_confirmation_rejected(self, client, auth_headers, test_tenant):
        resp = client.request("DELETE", "/api/v1/tenant", headers=auth_headers, json={})
        assert resp.status_code == 422  # pydantic: confirm is required
        assert query_one("SELECT id FROM tenants WHERE id = %s", (test_tenant["id"],)) is not None

    def test_wrong_confirmation_rejected(self, client, auth_headers, test_tenant):
        resp = client.request(
            "DELETE", "/api/v1/tenant", headers=auth_headers,
            json={"confirm": "definitely-not-right"},
        )
        assert resp.status_code == 400
        assert query_one("SELECT id FROM tenants WHERE id = %s", (test_tenant["id"],)) is not None


class TestDeleteCascade:

    def test_admin_delete_removes_tenant_and_children(self, client, auth_headers, test_tenant, registered_user):
        tenant_id = test_tenant["id"]
        user_id = registered_user["user"]["id"]
        sku = f"DEL-{uuid4().hex[:8]}"
        _seed_business_data(tenant_id, sku)

        # Sanity: rows exist before delete.
        assert query_one("SELECT id FROM inventory_stock WHERE tenant_id = %s", (tenant_id,)) is not None
        assert query_one("SELECT id FROM suppliers WHERE tenant_id = %s", (tenant_id,)) is not None
        assert query_one("SELECT id FROM users WHERE id = %s", (user_id,)) is not None

        resp = client.request(
            "DELETE", "/api/v1/tenant", headers=auth_headers,
            json={"confirm": test_tenant["slug"]},
        )
        assert resp.status_code == 200, resp.text

        assert query_one("SELECT id FROM tenants WHERE id = %s", (tenant_id,)) is None
        assert query_one("SELECT id FROM users WHERE id = %s", (user_id,)) is None
        assert query_one("SELECT id FROM inventory_stock WHERE tenant_id = %s", (tenant_id,)) is None
        assert query_one("SELECT id FROM suppliers WHERE tenant_id = %s", (tenant_id,)) is None

    def test_admin_delete_with_literal_delete_confirmation(self, client, auth_headers, test_tenant):
        tenant_id = test_tenant["id"]
        resp = client.request(
            "DELETE", "/api/v1/tenant", headers=auth_headers,
            json={"confirm": "DELETE"},
        )
        assert resp.status_code == 200, resp.text
        assert query_one("SELECT id FROM tenants WHERE id = %s", (tenant_id,)) is None

    def test_delete_does_not_touch_other_tenant(self, client, auth_headers, test_tenant):
        from backend.tenants.service import create_tenant
        other_tenant = create_tenant(f"pytest-other-{uuid4().hex[:8]}")
        try:
            other_sku = f"KEEP-{uuid4().hex[:8]}"
            _seed_business_data(other_tenant["id"], other_sku)

            resp = client.request(
                "DELETE", "/api/v1/tenant", headers=auth_headers,
                json={"confirm": "DELETE"},
            )
            assert resp.status_code == 200, resp.text

            assert query_one("SELECT id FROM tenants WHERE id = %s", (other_tenant["id"],)) is not None
            assert query_one(
                "SELECT id FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
                (other_tenant["id"], other_sku),
            ) is not None
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other_tenant["id"],))


def _tenant_scoped_tables() -> set[str]:
    """Every table the live schema scopes by tenant_id."""
    return {
        r["table_name"] for r in query(
            "SELECT table_name FROM information_schema.columns "
            "WHERE column_name = 'tenant_id' AND table_schema = 'public'"
        )
    }


class TestErasureLeavesNothingBehind:
    """`DELETE /tenant` promises "ALL of its data. Irreversible" and returns 200.

    It kept that promise only for the tables someone remembered. `_DELETE_ORDER`
    is a hand-maintained list, most tenant-scoped tables have NO foreign key to
    `tenants` (see the module docstring), and ten of them had fallen off it —
    among them `activity_logs`, `direct_messages`, `scenarios`, the whole
    transfer history and `user_preferences`. Those rows stayed readable in the
    database after the account was erased, and the caller was told it was done.
    Found 2026-08-08 while tracing why the local database had accumulated 572k
    inventory rows belonging to tenants that no longer existed.

    The existing cascade test passed throughout, because it only ever checked
    inventory_stock, suppliers and users — three tables that WERE on the list.
    """

    def test_delete_order_covers_every_tenant_scoped_table(self):
        """The guard that keeps the list from rotting again.

        Compares against the live schema instead of a hardcoded roster, so a new
        tenant-scoped table fails here the day it is added rather than quietly
        surviving erasure forever.
        """
        from backend.tenants.data_export import _DELETE_ORDER

        missing = _tenant_scoped_tables() - set(_DELETE_ORDER)
        assert not missing, (
            f"{len(missing)} tenant-scoped table(s) survive account erasure: "
            f"{sorted(missing)} — add them to _DELETE_ORDER, children first")

    def test_rows_in_the_forgotten_tables_are_actually_gone(
        self, client, auth_headers, test_tenant, registered_user,
    ):
        """The behaviour, not just the list: seed three tables that used to
        survive, erase, then look for anything left in EVERY tenant-scoped
        table directly in the database."""
        tenant_id = test_tenant["id"]
        user_id = registered_user["user"]["id"]

        execute(
            "INSERT INTO activity_logs (id, tenant_id, user_id, action) "
            "VALUES (%s, %s, %s, %s)",
            (f"act_{uuid4().hex[:8]}", tenant_id, user_id, "login"),
        )
        execute(
            "INSERT INTO user_preferences (user_id, tenant_id, language) "
            "VALUES (%s, %s, %s)",
            (user_id, tenant_id, "es"),
        )
        execute(
            "INSERT INTO scenarios (id, tenant_id, session_id, name) "
            "VALUES (%s, %s, %s, %s)",
            (f"scn_{uuid4().hex[:8]}", tenant_id, f"sess_{uuid4().hex[:8]}", "Escenario"),
        )
        for table in ("activity_logs", "user_preferences", "scenarios"):
            assert query_one(
                f"SELECT tenant_id FROM {table} WHERE tenant_id = %s", (tenant_id,)
            ) is not None, f"{table} was not seeded — the test would prove nothing"

        resp = client.request(
            "DELETE", "/api/v1/tenant", headers=auth_headers,
            json={"confirm": "DELETE"},
        )
        assert resp.status_code == 200, resp.text

        left = {}
        for table in sorted(_tenant_scoped_tables()):
            n = query_one(
                f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id = %s", (tenant_id,)
            )["c"]
            if n:
                left[table] = n
        assert not left, (
            f"erasure returned 200 while leaving rows behind: {left}")


@pytest.fixture
def fernet_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setattr(
        "backend.config.settings.integrations_secret_key", Fernet.generate_key().decode()
    )


class TestIntegrationConnectionsExportAndDelete:

    def test_export_includes_connections_without_credentials(
        self, client, auth_headers, test_tenant, fernet_key
    ):
        from backend.integrations import store

        tenant_id = test_tenant["id"]
        secret_token = f"SECRET-{uuid4().hex}"
        store.create_connection(tenant_id, "alegra", {"email": "a@b.com", "token": secret_token})

        resp = client.get("/api/v1/tenant/export", headers=auth_headers)
        assert resp.status_code == 200, resp.text

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = set(zf.namelist())
        assert "integration_connections.json" in names

        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["tables"]["integration_connections"] >= 1

        connections_bytes = zf.read("integration_connections.json")
        connections_rows = json.loads(connections_bytes)
        assert len(connections_rows) >= 1
        for row in connections_rows:
            assert "credentials" not in row
        # Belt and suspenders: neither ciphertext nor plaintext token anywhere
        # in the exported file (not just absent as a top-level key).
        assert secret_token not in connections_bytes.decode("utf-8")
        assert secret_token.encode("utf-8") not in connections_bytes

    def test_delete_removes_connections(self, client, auth_headers, test_tenant, fernet_key):
        from backend.integrations import store

        tenant_id = test_tenant["id"]
        conn = store.create_connection(tenant_id, "siigo", {"partner_id": "p1", "username": "u1"})
        assert query_one(
            "SELECT id FROM integration_connections WHERE id = %s", (conn["id"],)
        ) is not None

        resp = client.request(
            "DELETE", "/api/v1/tenant", headers=auth_headers,
            json={"confirm": test_tenant["slug"]},
        )
        assert resp.status_code == 200, resp.text

        assert query_one(
            "SELECT id FROM integration_connections WHERE id = %s", (conn["id"],)
        ) is None
