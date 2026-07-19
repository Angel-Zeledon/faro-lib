"""
Tests for PO reception (feature 1.4): stock self-correction and real
lead-time learning when a purchase order arrives.
"""

import uuid

from backend.db.connection import execute, query_one


def _make_po(client, auth_headers, tenant_id, *, skus=None):
    """Log a PO with explicit cart lines (like the /hoy cart does)."""
    skus = skus or {}
    items = []
    for sku, (qty, prov) in skus.items():
        items.append({
            "sku": sku, "display_name": f"Prod {sku}", "supplier": prov,
            "signal": "PEDIR_YA", "recommended_qty": qty,
            "final_qty": qty, "unit_cost": 2.5, "status": "approved",
        })
    resp = client.post(
        "/api/v1/inventory/log-po",
        params={"session_id": f"sess_test_{uuid.uuid4().hex[:6]}"},
        json={"items": items},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _stock(tenant_id, sku):
    row = query_one(
        "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tenant_id, sku),
    )
    return float(row["current_stock"]) if row else None


class TestReceivePO:
    def test_viewer_cannot_receive(self, client, auth_headers, viewer_headers, test_tenant):
        po_id = _make_po(client, auth_headers, test_tenant["id"],
                         skus={"REC-V1": (10, "Prov X")})
        resp = client.post(f"/api/v1/inventory/po/{po_id}/receive", headers=viewer_headers)
        assert resp.status_code == 403
        # State unchanged
        po = query_one("SELECT reception_status FROM inventory_po_log WHERE id = %s", (po_id,))
        assert po["reception_status"] == "pending"

    def test_full_reception_updates_stock_status_and_lead_time(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sku = f"REC-{uuid.uuid4().hex[:6]}"
        # Existing stock 100 → after receiving 40 must be 140
        client.put(f"/api/v1/inventory/stock/{sku}",
                   json={"current_stock": 100}, headers=auth_headers)
        po_id = _make_po(client, auth_headers, tid, skus={sku: (40, "Proveedor Real")})

        resp = client.post(f"/api/v1/inventory/po/{po_id}/receive", json={}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["reception_status"] == "received"
        assert data["lead_time_days"] >= 0
        assert "Proveedor Real" in data["suppliers_observed"]

        # DB: stock incremented, not replaced
        assert _stock(tid, sku) == 140.0

        # DB: line quantities recorded
        item = query_one(
            "SELECT received_qty FROM inventory_po_items WHERE po_log_id = %s AND sku = %s",
            (po_id, sku),
        )
        assert float(item["received_qty"]) == 40.0

        # DB: lead-time observation stored for the supplier
        obs = query_one(
            """SELECT lead_time_days FROM supplier_lead_time_obs
               WHERE tenant_id = %s AND po_log_id = %s AND supplier = 'Proveedor Real'""",
            (tid, po_id),
        )
        assert obs is not None

        # Endpoint exposes the learned stats
        stats = client.get("/api/v1/inventory/suppliers/scorecard", headers=auth_headers)
        assert stats.status_code == 200
        provs = [s["supplier"] for s in stats.json()["data"]]
        assert "Proveedor Real" in provs

    def test_partial_then_complete(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"REC-P-{uuid.uuid4().hex[:6]}"
        po_id = _make_po(client, auth_headers, tid, skus={sku: (50, "Prov Parcial")})

        # First event: only 30 of 50 arrive
        resp = client.post(
            f"/api/v1/inventory/po/{po_id}/receive",
            json={"lines": [{"sku": sku, "received_qty": 30}]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["reception_status"] == "partial"
        assert _stock(tid, sku) == 30.0  # row auto-created with received qty

        # Second event: remaining 20 arrive → received
        resp = client.post(
            f"/api/v1/inventory/po/{po_id}/receive",
            json={"lines": [{"sku": sku, "received_qty": 20}]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["reception_status"] == "received"
        assert _stock(tid, sku) == 50.0

        item = query_one(
            "SELECT received_qty FROM inventory_po_items WHERE po_log_id = %s AND sku = %s",
            (po_id, sku),
        )
        assert float(item["received_qty"]) == 50.0

        # Only ONE lead-time observation (first event), partials don't skew
        obs_count = query_one(
            "SELECT COUNT(*) AS n FROM supplier_lead_time_obs WHERE po_log_id = %s",
            (po_id,),
        )
        assert obs_count["n"] == 1

    def test_double_full_reception_rejected(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"REC-D-{uuid.uuid4().hex[:6]}"
        po_id = _make_po(client, auth_headers, tid, skus={sku: (10, "Prov Doble")})

        assert client.post(f"/api/v1/inventory/po/{po_id}/receive",
                           json={}, headers=auth_headers).status_code == 200
        resp = client.post(f"/api/v1/inventory/po/{po_id}/receive",
                           json={}, headers=auth_headers)
        assert resp.status_code == 409
        # Stock must NOT have been double-counted
        assert _stock(tid, sku) == 10.0

    def test_unknown_sku_in_lines_rejected(self, client, auth_headers, test_tenant):
        po_id = _make_po(client, auth_headers, test_tenant["id"],
                         skus={f"REC-U-{uuid.uuid4().hex[:6]}": (5, "Prov U")})
        resp = client.post(
            f"/api/v1/inventory/po/{po_id}/receive",
            json={"lines": [{"sku": "NO-EXISTE", "received_qty": 5}]},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_receive_unknown_po_404(self, client, auth_headers):
        resp = client.post("/api/v1/inventory/po/no-existe/receive",
                           json={}, headers=auth_headers)
        assert resp.status_code == 404

    def test_cross_tenant_po_not_receivable(self, client, auth_headers, test_tenant):
        """Tenant B must not be able to receive tenant A's PO (IDOR guard)."""
        from backend.tenants.service import create_tenant
        from backend.users.service import create_user, mark_verified
        import uuid as _uuid

        po_id = _make_po(client, auth_headers, test_tenant["id"],
                         skus={f"REC-X-{_uuid.uuid4().hex[:6]}": (5, "Prov X")})

        email = f"otro-{_uuid.uuid4().hex[:8]}@pytest.example.com"
        t2 = create_tenant(f"otro-{_uuid.uuid4().hex[:6]}")
        u2 = create_user(tenant_id=t2["id"], email=email,
                         password="Otro-Tenant-123!", role="admin", full_name="Otro")
        mark_verified(t2["id"], u2["id"])
        login = client.post("/api/v1/auth/login",
                            json={"email": email, "password": "Otro-Tenant-123!"})
        h2 = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

        resp = client.post(f"/api/v1/inventory/po/{po_id}/receive", json={}, headers=h2)
        assert resp.status_code == 404
        po = query_one("SELECT reception_status FROM inventory_po_log WHERE id = %s", (po_id,))
        assert po["reception_status"] == "pending"

        execute("DELETE FROM tenants WHERE id = %s", (t2["id"],))

    def test_po_items_endpoint_shows_reception_state(self, client, auth_headers, test_tenant):
        sku = f"REC-I-{uuid.uuid4().hex[:6]}"
        po_id = _make_po(client, auth_headers, test_tenant["id"], skus={sku: (7, "Prov I")})
        resp = client.get(f"/api/v1/inventory/po/{po_id}/items", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["reception_status"] == "pending"
        assert data["items"][0]["sku"] == sku
        assert data["items"][0]["received_qty"] is None
