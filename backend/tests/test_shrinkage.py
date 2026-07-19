"""
Tests for shrinkage (shrinkage / non-sale stock-outs): breakage, expiry,
self-consumption, gift/sample. Recording a shrinkage must decrement the SKU's
stock through the same path as every other stock-affecting event, and
accumulate the cost (quantity * unit cost) for a future monthly summary.
"""

import uuid

from backend.db.connection import query_one


def _stock(tenant_id, sku):
    row = query_one(
        "SELECT current_stock, unit_cost FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tenant_id, sku),
    )
    return row


def _seed_stock(client, auth_headers, sku, current_stock=100, unit_cost=4.0):
    resp = client.put(
        f"/api/v1/inventory/stock/{sku}",
        json={"current_stock": current_stock, "unit_cost": unit_cost},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestCreateShrinkage:
    def test_viewer_cannot_create_shrinkage(self, client, auth_headers, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"MER-V-{uuid.uuid4().hex[:6]}"
        _seed_stock(client, auth_headers, sku, current_stock=50, unit_cost=3.0)

        resp = client.post(
            "/api/v1/inventory/shrinkage",
            json={"sku": sku, "quantity": 5, "reason": "breakage"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

        # State unchanged: stock untouched, no shrinkage row created
        row = _stock(tid, sku)
        assert float(row["current_stock"]) == 50.0
        shrinkage = query_one(
            "SELECT * FROM inventory_shrinkage WHERE tenant_id = %s AND sku = %s",
            (tid, sku),
        )
        assert shrinkage is None

    def test_analyst_records_shrinkage_decrements_stock_and_accumulates_cost(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sku = f"MER-{uuid.uuid4().hex[:6]}"
        _seed_stock(client, auth_headers, sku, current_stock=100, unit_cost=4.0)

        resp = client.post(
            "/api/v1/inventory/shrinkage",
            json={"sku": sku, "quantity": 10, "reason": "expiry", "notes": "vencido en bodega"},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["reason"] == "expiry"
        assert float(data["quantity"]) == 10.0
        assert float(data["unit_cost"]) == 4.0
        assert float(data["total_cost"]) == 40.0

        # DB: stock decremented (100 - 10 = 90), not replaced
        row = _stock(tid, sku)
        assert float(row["current_stock"]) == 90.0

        # DB: shrinkage row persisted with accumulated cost
        shrinkage = query_one(
            "SELECT * FROM inventory_shrinkage WHERE tenant_id = %s AND sku = %s",
            (tid, sku),
        )
        assert shrinkage is not None
        assert float(shrinkage["quantity"]) == 10.0
        assert float(shrinkage["total_cost"]) == 40.0
        assert shrinkage["reason"] == "expiry"
        assert shrinkage["notes"] == "vencido en bodega"

        # DB: a stock snapshot was recorded (same path as receptions/edits)
        snap = query_one(
            """SELECT current_stock FROM inventory_snapshots
               WHERE tenant_id = %s AND sku = %s ORDER BY recorded_at DESC LIMIT 1""",
            (tid, sku),
        )
        assert float(snap["current_stock"]) == 90.0

    def test_quantity_exceeding_stock_rejected(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"MER-X-{uuid.uuid4().hex[:6]}"
        _seed_stock(client, auth_headers, sku, current_stock=5, unit_cost=2.0)

        resp = client.post(
            "/api/v1/inventory/shrinkage",
            json={"sku": sku, "quantity": 999, "reason": "breakage"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

        row = _stock(tid, sku)
        assert float(row["current_stock"]) == 5.0
        shrinkage = query_one(
            "SELECT * FROM inventory_shrinkage WHERE tenant_id = %s AND sku = %s",
            (tid, sku),
        )
        assert shrinkage is None

    def test_concurrent_shrinkage_never_drive_stock_negative(self, client, auth_headers, test_tenant):
        """
        Regression guard: record_shrinkage's stock decrement must be an atomic,
        guarded UPDATE (WHERE current_stock >= quantity), not a Python-side
        read-then-write check — otherwise two concurrent submissions can both
        read the same stock, both pass the pre-check, and both decrement,
        driving stock negative. Fires two real concurrent DB calls (via the
        connection pool, not sequential) for 60 units each against a stock of
        100: exactly one must succeed, the other must be rejected, and final
        stock must be the single successful decrement (40), never negative.
        """
        import threading
        from backend.inventory.shrinkage_service import record_shrinkage

        tid = test_tenant["id"]
        sku = f"MER-RACE-{uuid.uuid4().hex[:6]}"
        _seed_stock(client, auth_headers, sku, current_stock=100, unit_cost=1.0)

        results = []
        barrier = threading.Barrier(2)

        def attempt():
            barrier.wait()
            try:
                record_shrinkage(tid, sku, 60, "breakage")
                results.append("ok")
            except ValueError:
                results.append("rejected")

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(results) == ["ok", "rejected"]
        row = _stock(tid, sku)
        assert float(row["current_stock"]) == 40.0

    def test_invalid_reason_rejected(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"MER-R-{uuid.uuid4().hex[:6]}"
        _seed_stock(client, auth_headers, sku, current_stock=20)

        resp = client.post(
            "/api/v1/inventory/shrinkage",
            json={"sku": sku, "quantity": 1, "reason": "not_a_real_reason"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

        row = _stock(tid, sku)
        assert float(row["current_stock"]) == 20.0

    def test_unknown_sku_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/v1/inventory/shrinkage",
            json={"sku": "NO-EXISTE-MERMA", "quantity": 1, "reason": "gift"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_zero_or_negative_quantity_rejected(self, client, auth_headers, test_tenant):
        sku = f"MER-Z-{uuid.uuid4().hex[:6]}"
        _seed_stock(client, auth_headers, sku, current_stock=10)

        resp = client.post(
            "/api/v1/inventory/shrinkage",
            json={"sku": sku, "quantity": 0, "reason": "breakage"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestListShrinkage:
    def test_list_shrinkage_returns_history(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"MER-L-{uuid.uuid4().hex[:6]}"
        _seed_stock(client, auth_headers, sku, current_stock=30, unit_cost=1.5)

        client.post(
            "/api/v1/inventory/shrinkage",
            json={"sku": sku, "quantity": 3, "reason": "self_consumption"},
            headers=auth_headers,
        )
        client.post(
            "/api/v1/inventory/shrinkage",
            json={"sku": sku, "quantity": 2, "reason": "gift"},
            headers=auth_headers,
        )

        resp = client.get(f"/api/v1/inventory/shrinkage?sku={sku}", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 2
        reasons = {i["reason"] for i in items}
        assert reasons == {"self_consumption", "gift"}

        # DB cross-check: final stock reflects both shrinkage (30 - 3 - 2 = 25)
        row = _stock(tid, sku)
        assert float(row["current_stock"]) == 25.0

    def test_reasons_endpoint(self, client, auth_headers):
        resp = client.get("/api/v1/inventory/shrinkage/reasons", headers=auth_headers)
        assert resp.status_code == 200
        assert set(resp.json()["data"]) == {"breakage", "expiry", "self_consumption", "gift"}
