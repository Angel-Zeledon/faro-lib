"""
Tests for mermas (shrinkage / non-sale stock-outs): breakage, expiry,
self-consumption, gift/sample. Recording a merma must decrement the SKU's
stock through the same path as every other stock-affecting event, and
accumulate the cost (quantity * unit cost) for a future monthly summary.
"""

import uuid

from backend.db.connection import query_one


def _stock(tenant_id, sku):
    row = query_one(
        "SELECT stock_actual, costo_unitario FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tenant_id, sku),
    )
    return row


def _seed_stock(client, auth_headers, sku, stock_actual=100, costo_unitario=4.0):
    resp = client.put(
        f"/api/v1/inventory/stock/{sku}",
        json={"stock_actual": stock_actual, "costo_unitario": costo_unitario},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestCreateMerma:
    def test_viewer_cannot_create_merma(self, client, auth_headers, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"MER-V-{uuid.uuid4().hex[:6]}"
        _seed_stock(client, auth_headers, sku, stock_actual=50, costo_unitario=3.0)

        resp = client.post(
            "/api/v1/inventory/mermas",
            json={"sku": sku, "quantity": 5, "reason": "breakage"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

        # State unchanged: stock untouched, no merma row created
        row = _stock(tid, sku)
        assert float(row["stock_actual"]) == 50.0
        merma = query_one(
            "SELECT * FROM inventory_mermas WHERE tenant_id = %s AND sku = %s",
            (tid, sku),
        )
        assert merma is None

    def test_analyst_records_merma_decrements_stock_and_accumulates_cost(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sku = f"MER-{uuid.uuid4().hex[:6]}"
        _seed_stock(client, auth_headers, sku, stock_actual=100, costo_unitario=4.0)

        resp = client.post(
            "/api/v1/inventory/mermas",
            json={"sku": sku, "quantity": 10, "reason": "expiry", "notes": "vencido en bodega"},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["reason"] == "expiry"
        assert float(data["quantity"]) == 10.0
        assert float(data["costo_unitario"]) == 4.0
        assert float(data["costo_total"]) == 40.0

        # DB: stock decremented (100 - 10 = 90), not replaced
        row = _stock(tid, sku)
        assert float(row["stock_actual"]) == 90.0

        # DB: merma row persisted with accumulated cost
        merma = query_one(
            "SELECT * FROM inventory_mermas WHERE tenant_id = %s AND sku = %s",
            (tid, sku),
        )
        assert merma is not None
        assert float(merma["quantity"]) == 10.0
        assert float(merma["costo_total"]) == 40.0
        assert merma["reason"] == "expiry"
        assert merma["notes"] == "vencido en bodega"

        # DB: a stock snapshot was recorded (same path as receptions/edits)
        snap = query_one(
            """SELECT stock_actual FROM inventory_snapshots
               WHERE tenant_id = %s AND sku = %s ORDER BY recorded_at DESC LIMIT 1""",
            (tid, sku),
        )
        assert float(snap["stock_actual"]) == 90.0

    def test_quantity_exceeding_stock_rejected(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"MER-X-{uuid.uuid4().hex[:6]}"
        _seed_stock(client, auth_headers, sku, stock_actual=5, costo_unitario=2.0)

        resp = client.post(
            "/api/v1/inventory/mermas",
            json={"sku": sku, "quantity": 999, "reason": "breakage"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

        row = _stock(tid, sku)
        assert float(row["stock_actual"]) == 5.0
        merma = query_one(
            "SELECT * FROM inventory_mermas WHERE tenant_id = %s AND sku = %s",
            (tid, sku),
        )
        assert merma is None

    def test_concurrent_mermas_never_drive_stock_negative(self, client, auth_headers, test_tenant):
        """
        Regression guard: record_merma's stock decrement must be an atomic,
        guarded UPDATE (WHERE stock_actual >= quantity), not a Python-side
        read-then-write check — otherwise two concurrent submissions can both
        read the same stock, both pass the pre-check, and both decrement,
        driving stock negative. Fires two real concurrent DB calls (via the
        connection pool, not sequential) for 60 units each against a stock of
        100: exactly one must succeed, the other must be rejected, and final
        stock must be the single successful decrement (40), never negative.
        """
        import threading
        from backend.inventory.merma_service import record_merma

        tid = test_tenant["id"]
        sku = f"MER-RACE-{uuid.uuid4().hex[:6]}"
        _seed_stock(client, auth_headers, sku, stock_actual=100, costo_unitario=1.0)

        results = []
        barrier = threading.Barrier(2)

        def attempt():
            barrier.wait()
            try:
                record_merma(tid, sku, 60, "breakage")
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
        assert float(row["stock_actual"]) == 40.0

    def test_invalid_reason_rejected(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"MER-R-{uuid.uuid4().hex[:6]}"
        _seed_stock(client, auth_headers, sku, stock_actual=20)

        resp = client.post(
            "/api/v1/inventory/mermas",
            json={"sku": sku, "quantity": 1, "reason": "not_a_real_reason"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

        row = _stock(tid, sku)
        assert float(row["stock_actual"]) == 20.0

    def test_unknown_sku_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/v1/inventory/mermas",
            json={"sku": "NO-EXISTE-MERMA", "quantity": 1, "reason": "gift"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_zero_or_negative_quantity_rejected(self, client, auth_headers, test_tenant):
        sku = f"MER-Z-{uuid.uuid4().hex[:6]}"
        _seed_stock(client, auth_headers, sku, stock_actual=10)

        resp = client.post(
            "/api/v1/inventory/mermas",
            json={"sku": sku, "quantity": 0, "reason": "breakage"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestListMermas:
    def test_list_mermas_returns_history(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"MER-L-{uuid.uuid4().hex[:6]}"
        _seed_stock(client, auth_headers, sku, stock_actual=30, costo_unitario=1.5)

        client.post(
            "/api/v1/inventory/mermas",
            json={"sku": sku, "quantity": 3, "reason": "self_consumption"},
            headers=auth_headers,
        )
        client.post(
            "/api/v1/inventory/mermas",
            json={"sku": sku, "quantity": 2, "reason": "gift"},
            headers=auth_headers,
        )

        resp = client.get(f"/api/v1/inventory/mermas?sku={sku}", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 2
        reasons = {i["reason"] for i in items}
        assert reasons == {"self_consumption", "gift"}

        # DB cross-check: final stock reflects both mermas (30 - 3 - 2 = 25)
        row = _stock(tid, sku)
        assert float(row["stock_actual"]) == 25.0

    def test_reasons_endpoint(self, client, auth_headers):
        resp = client.get("/api/v1/inventory/mermas/reasons", headers=auth_headers)
        assert resp.status_code == 200
        assert set(resp.json()["data"]) == {"breakage", "expiry", "self_consumption", "gift"}
