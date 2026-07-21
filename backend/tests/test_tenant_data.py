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

from backend.db.connection import execute, query_one


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
