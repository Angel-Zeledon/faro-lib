"""
Supplier module integration tests.
Covers: supplier CRUD, SKU-supplier linking.
"""

import pytest
from uuid import uuid4


# ── Fixture overrides: @example.com avoids email-validator rejection ───────────

@pytest.fixture
def registered_user(test_tenant):
    from backend.users import service as user_svc
    email = f"sup-{uuid4().hex[:8]}@example.com"
    password = "TestPass123!"
    user = user_svc.create_user(test_tenant["id"], email, password, "admin", "Test")
    user_svc.mark_verified(test_tenant["id"], user["id"])
    return {"user": user, "tenant": test_tenant, "password": password, "email": email}


@pytest.fixture
def auth_headers(client, registered_user):
    resp = client.post("/api/v1/auth/login", json={
        "email": registered_user["email"],
        "password": registered_user["password"],
    })
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ok(resp, code=200):
    assert resp.status_code == code, f"Expected {code}, got {resp.status_code}: {resp.text}"
    return resp.json()["data"]


def _sku():
    return f"SKU-{uuid4().hex[:8].upper()}"


# ── Supplier CRUD ──────────────────────────────────────────────────────────────

class TestSupplierCRUD:

    def test_list_empty(self, client, auth_headers):
        """GET /inventory/suppliers returns 200 with a list."""
        r = client.get("/api/v1/inventory/suppliers", headers=auth_headers)
        assert r.status_code == 200
        assert isinstance(r.json()["data"], list)

    def test_create_supplier(self, client, auth_headers):
        """POST /inventory/suppliers creates a new supplier and returns 201."""
        from backend.db.connection import query_one
        name = f"Proveedor Test {uuid4().hex[:4]}"
        r = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": name,
            "lead_time_dias": 12,
            "lead_time_std": 2,
            "payment_terms": "30 dias",
        })
        assert r.status_code == 201
        d = r.json()["data"]
        assert d["lead_time_dias"] == 12
        assert "id" in d

        row = query_one(
            "SELECT name, lead_time_dias, payment_terms, active FROM suppliers WHERE id = %s",
            (d["id"],),
        )
        assert row is not None, "Supplier row was not persisted to the DB"
        assert row["name"] == name
        assert row["lead_time_dias"] == 12
        assert row["payment_terms"] == "30 dias"
        assert row["active"] is True

    def test_create_supplier_minimal(self, client, auth_headers):
        """POST /inventory/suppliers works with only required field 'name'."""
        r = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": f"MinProv-{uuid4().hex[:6]}",
        })
        assert r.status_code == 201
        d = r.json()["data"]
        assert "id" in d

    def test_create_requires_name(self, client, auth_headers):
        """POST /inventory/suppliers without 'name' returns 422."""
        r = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "lead_time_dias": 10,
        })
        assert r.status_code == 422

    def test_created_supplier_appears_in_list(self, client, auth_headers):
        """A newly created supplier shows up in GET /inventory/suppliers."""
        cr = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": f"Listed-{uuid4().hex[:6]}", "lead_time_dias": 8,
        })
        assert cr.status_code == 201
        sup_id = cr.json()["data"]["id"]

        lr = client.get("/api/v1/inventory/suppliers", headers=auth_headers)
        assert lr.status_code == 200
        ids = [s["id"] for s in lr.json()["data"]]
        assert sup_id in ids

    def test_patch_supplier_lead_time(self, client, auth_headers):
        """PATCH /inventory/suppliers/{id} updates lead_time_dias."""
        cr = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": f"Prov-{uuid4().hex[:6]}", "lead_time_dias": 10,
        })
        assert cr.status_code == 201
        sup_id = cr.json()["data"]["id"]

        pr = client.patch(
            f"/api/v1/inventory/suppliers/{sup_id}",
            headers=auth_headers,
            json={"lead_time_dias": 20},
        )
        assert pr.status_code == 200
        assert pr.json()["data"]["lead_time_dias"] == 20

    def test_patch_supplier_name(self, client, auth_headers):
        """PATCH /inventory/suppliers/{id} updates name."""
        cr = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": f"OldName-{uuid4().hex[:6]}", "lead_time_dias": 5,
        })
        assert cr.status_code == 201
        sup_id = cr.json()["data"]["id"]
        new_name = f"NewName-{uuid4().hex[:6]}"

        pr = client.patch(
            f"/api/v1/inventory/suppliers/{sup_id}",
            headers=auth_headers,
            json={"name": new_name},
        )
        assert pr.status_code == 200
        assert pr.json()["data"]["name"] == new_name

    def test_patch_nonexistent_supplier_returns_404(self, client, auth_headers):
        """PATCH on a non-existent supplier_id returns 404."""
        r = client.patch(
            f"/api/v1/inventory/suppliers/{uuid4().hex}",
            headers=auth_headers,
            json={"lead_time_dias": 10},
        )
        assert r.status_code == 404

    def test_soft_delete(self, client, auth_headers):
        """DELETE /inventory/suppliers/{id} returns 204 and supplier no longer lists."""
        cr = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": f"Del-{uuid4().hex[:6]}", "lead_time_dias": 5,
        })
        assert cr.status_code == 201
        sup_id = cr.json()["data"]["id"]

        dr = client.delete(f"/api/v1/inventory/suppliers/{sup_id}", headers=auth_headers)
        assert dr.status_code == 204

        # Should not appear in list after deletion
        lr = client.get("/api/v1/inventory/suppliers", headers=auth_headers)
        ids = [s["id"] for s in lr.json()["data"]]
        assert sup_id not in ids

    def test_delete_nonexistent_supplier_returns_404(self, client, auth_headers):
        """DELETE on a non-existent supplier_id returns 404."""
        r = client.delete(
            f"/api/v1/inventory/suppliers/{uuid4().hex}",
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_unauthenticated_request_returns_401(self, client):
        """Requests without auth token return 401 or 403."""
        r = client.get("/api/v1/inventory/suppliers")
        assert r.status_code in (401, 403)

    def test_tenant_isolation(self, client, auth_headers):
        """A supplier created by tenant A is not visible to tenant B."""
        # Create supplier for tenant A
        cr = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": f"Isolated-{uuid4().hex[:6]}", "lead_time_dias": 5,
        })
        assert cr.status_code == 201
        sup_id = cr.json()["data"]["id"]

        # Create an independent tenant B
        from backend.tenants.service import create_tenant
        from backend.users import service as user_svc
        from backend.db.connection import execute
        t2 = create_tenant(f"tenant-b-{uuid4().hex[:6]}")
        email2 = f"b-{uuid4().hex[:6]}@example.com"
        u2 = user_svc.create_user(t2["id"], email2, "TestPass123!", "admin", "B")
        user_svc.mark_verified(t2["id"], u2["id"])
        try:
            lr = client.post("/api/v1/auth/login", json={
                "email": email2, "password": "TestPass123!",
            })
            assert lr.status_code == 200
            h2 = {"Authorization": f"Bearer {lr.json()['data']['access_token']}"}
            r = client.get("/api/v1/inventory/suppliers", headers=h2)
            assert r.status_code == 200
            ids2 = [s["id"] for s in r.json()["data"]]
            assert sup_id not in ids2
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))


# ── Get supplier by name ───────────────────────────────────────────────────────

class TestGetSupplierByName:

    def test_matches_case_insensitively(self, client, test_tenant):
        from backend.inventory import supplier_service as sup_svc

        tid = test_tenant["id"]
        name = f"Distribuidora {uuid4().hex[:8]}"
        created = sup_svc.create_supplier(tid, {"name": name, "email": "ventas@example.com"})

        found = sup_svc.get_supplier_by_name(tid, name.upper())
        assert found is not None
        assert found["id"] == created["id"]

    def test_returns_none_for_no_match(self, client, test_tenant):
        from backend.inventory import supplier_service as sup_svc

        assert sup_svc.get_supplier_by_name(test_tenant["id"], "Nonexistent Supplier XYZ") is None

    def test_returns_none_for_empty_name(self, client, test_tenant):
        from backend.inventory import supplier_service as sup_svc

        assert sup_svc.get_supplier_by_name(test_tenant["id"], "") is None
        assert sup_svc.get_supplier_by_name(test_tenant["id"], None) is None


# ── SKU-Supplier linking ───────────────────────────────────────────────────────

class TestSkuSupplierLink:

    def test_assign_and_retrieve(self, client, auth_headers):
        """PUT /inventory/stock/{sku}/suppliers/{id} links supplier to SKU."""
        sku = _sku()
        # Create stock entry
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            headers=auth_headers,
            json={"stock_actual": 100, "lead_time_dias": 10},
        )
        # Create supplier
        cr = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": f"Sup-{uuid4().hex[:4]}", "lead_time_dias": 10,
        })
        assert cr.status_code == 201
        sup_id = cr.json()["data"]["id"]

        # Assign supplier to SKU
        ar = client.put(
            f"/api/v1/inventory/stock/{sku}/suppliers/{sup_id}",
            headers=auth_headers,
            json={"is_primary": True, "unit_cost": 5000},
        )
        assert ar.status_code == 200

        # Retrieve SKU suppliers
        gr = client.get(f"/api/v1/inventory/stock/{sku}/suppliers", headers=auth_headers)
        assert gr.status_code == 200
        data = gr.json()["data"]
        assert isinstance(data, list)
        assert len(data) >= 1
        supplier_ids = [link["supplier_id"] for link in data]
        assert sup_id in supplier_ids

    def test_assign_nonexistent_supplier_returns_404(self, client, auth_headers):
        """PUT /inventory/stock/{sku}/suppliers/{bad_id} returns 404."""
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            headers=auth_headers,
            json={"stock_actual": 10, "lead_time_dias": 5},
        )
        r = client.put(
            f"/api/v1/inventory/stock/{sku}/suppliers/{uuid4().hex}",
            headers=auth_headers,
            json={"is_primary": True},
        )
        assert r.status_code == 404

    def test_remove_sku_supplier(self, client, auth_headers):
        """DELETE /inventory/stock/{sku}/suppliers/{id} unlinks the supplier."""
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            headers=auth_headers,
            json={"stock_actual": 50, "lead_time_dias": 7},
        )
        cr = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": f"RemSup-{uuid4().hex[:4]}", "lead_time_dias": 7,
        })
        assert cr.status_code == 201
        sup_id = cr.json()["data"]["id"]

        # Assign
        client.put(
            f"/api/v1/inventory/stock/{sku}/suppliers/{sup_id}",
            headers=auth_headers,
            json={"is_primary": True},
        )

        # Remove link
        dr = client.delete(
            f"/api/v1/inventory/stock/{sku}/suppliers/{sup_id}",
            headers=auth_headers,
        )
        assert dr.status_code == 204

        # Verify removed
        gr = client.get(f"/api/v1/inventory/stock/{sku}/suppliers", headers=auth_headers)
        assert gr.status_code == 200
        supplier_ids = [link["supplier_id"] for link in gr.json()["data"]]
        assert sup_id not in supplier_ids

    def test_get_sku_suppliers_empty(self, client, auth_headers):
        """GET /inventory/stock/{sku}/suppliers on SKU with no linked suppliers returns empty list."""
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            headers=auth_headers,
            json={"stock_actual": 20, "lead_time_dias": 5},
        )
        gr = client.get(f"/api/v1/inventory/stock/{sku}/suppliers", headers=auth_headers)
        assert gr.status_code == 200
        assert isinstance(gr.json()["data"], list)
        assert len(gr.json()["data"]) == 0

    def test_where_used_bom_endpoint_unlinked_component_returns_empty_list(self, client, auth_headers):
        """
        where_used() (api/v1/inventory.py:579-582) has no existence check on child_sku —
        it always returns ok(get_parents_using(...)), so a component with no BOM links
        always 200s with an empty list. There is no code path that 404s here.
        """
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            headers=auth_headers,
            json={"stock_actual": 50},
        )
        wr = client.get(f"/api/v1/inventory/bom/{sku}/used-in", headers=auth_headers)
        assert wr.status_code == 200
        assert wr.json()["data"] == []

    def test_where_used_bom_endpoint_returns_linked_parent(self, client, auth_headers):
        """Once a BOM link exists, used-in must list the parent SKU."""
        parent, child = _sku(), _sku()
        for sku in (parent, child):
            client.put(f"/api/v1/inventory/stock/{sku}", headers=auth_headers, json={"stock_actual": 1})
        put = client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            headers=auth_headers,
            json={"quantity": 2.0},
        )
        assert put.status_code == 200

        wr = client.get(f"/api/v1/inventory/bom/{child}/used-in", headers=auth_headers)
        assert wr.status_code == 200
        parents = [p["parent_sku"] for p in wr.json()["data"]]
        assert parents == [parent]

    def test_assign_with_moq_and_lead_time(self, client, auth_headers):
        """PUT /inventory/stock/{sku}/suppliers/{id} accepts optional moq and lead_time_dias."""
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            headers=auth_headers,
            json={"stock_actual": 200, "lead_time_dias": 14},
        )
        cr = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": f"MoqSup-{uuid4().hex[:4]}", "lead_time_dias": 14,
        })
        assert cr.status_code == 201
        sup_id = cr.json()["data"]["id"]

        ar = client.put(
            f"/api/v1/inventory/stock/{sku}/suppliers/{sup_id}",
            headers=auth_headers,
            json={"is_primary": False, "moq": 50, "lead_time_dias": 10, "unit_cost": 1200},
        )
        assert ar.status_code == 200
