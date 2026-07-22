"""Inter-warehouse transfers (feature 5.4): schema, lifecycle, API."""

import pytest

from backend.db.connection import query, query_one
from backend.inventory import service as inv_svc
from backend.inventory import transfer_service as tr_svc
from backend.inventory import warehouse_service as wh_svc


def _columns(table: str) -> set[str]:
    rows = query(
        """SELECT column_name FROM information_schema.columns
           WHERE table_name = %s""",
        (table,),
    )
    return {r["column_name"] for r in rows}


class TestTransferSchema:
    def test_transfer_tables_exist(self, client):
        assert {"id", "tenant_id", "from_warehouse", "to_warehouse",
                "status", "notes", "created_by", "created_at",
                "received_at"} <= _columns("inventory_transfer_log")
        assert {"id", "tenant_id", "transfer_id", "sku",
                "qty_sent", "qty_received"} <= _columns("inventory_transfer_items")

    def test_demand_share_and_po_destination_columns(self, client):
        assert "demand_share" in _columns("warehouses")
        assert "destination_warehouse" in _columns("inventory_po_log")


def _stock(tid, sku, wh):
    row = query_one(
        "SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND warehouse=%s",
        (tid, sku, wh))
    return float(row["current_stock"]) if row else None


@pytest.fixture()
def two_warehouses(client, test_tenant):
    tid = test_tenant["id"]
    wh_svc.create_warehouse(tid, "principal", is_default=True)
    wh_svc.create_warehouse(tid, "Norte")
    inv_svc.upsert_stock(tid, "A", {"current_stock": 100, "warehouse": "principal"})
    inv_svc.upsert_stock(tid, "A", {"current_stock": 10, "warehouse": "Norte"})
    return tid


@pytest.fixture()
def user_id(registered_user):
    return registered_user["user"]["id"]


class TestTransferLifecycle:
    def test_send_decrements_origin_only(self, two_warehouses, user_id):
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, user_id, "principal", "Norte",
                                   [{"sku": "A", "qty": 30}])
        assert t["status"] == "in_transit"
        assert _stock(tid, "A", "principal") == 70.0
        assert _stock(tid, "A", "Norte") == 10.0  # in transit is nowhere

    def test_receive_full_completes(self, two_warehouses, user_id):
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, user_id, "principal", "Norte",
                                   [{"sku": "A", "qty": 30}])
        out = tr_svc.receive_transfer(tid, t["id"], None)
        assert out["status"] == "received"
        assert _stock(tid, "A", "Norte") == 40.0
        header = query_one(
            "SELECT status, received_at FROM inventory_transfer_log WHERE id=%s", (t["id"],))
        assert header["status"] == "received"
        assert header["received_at"] is not None

    def test_partial_reception(self, two_warehouses, user_id):
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, user_id, "principal", "Norte",
                                   [{"sku": "A", "qty": 30}])
        out = tr_svc.receive_transfer(tid, t["id"], [{"sku": "A", "received_qty": 20}])
        assert out["status"] == "partial"
        assert _stock(tid, "A", "Norte") == 30.0
        out2 = tr_svc.receive_transfer(tid, t["id"], [{"sku": "A", "received_qty": 10}])
        assert out2["status"] == "received"
        assert _stock(tid, "A", "Norte") == 40.0

    def test_over_reception_rejected(self, two_warehouses, user_id):
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, user_id, "principal", "Norte",
                                   [{"sku": "A", "qty": 30}])
        with pytest.raises(ValueError):
            tr_svc.receive_transfer(tid, t["id"], [{"sku": "A", "received_qty": 31}])
        assert _stock(tid, "A", "Norte") == 10.0  # unchanged

    def test_cancel_restores_origin(self, two_warehouses, user_id):
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, user_id, "principal", "Norte",
                                   [{"sku": "A", "qty": 30}])
        out = tr_svc.cancel_transfer(tid, t["id"])
        assert out["status"] == "cancelled"
        assert _stock(tid, "A", "principal") == 100.0

    def test_cancel_after_reception_rejected(self, two_warehouses, user_id):
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, user_id, "principal", "Norte",
                                   [{"sku": "A", "qty": 30}])
        tr_svc.receive_transfer(tid, t["id"], [{"sku": "A", "received_qty": 5}])
        with pytest.raises(ValueError):
            tr_svc.cancel_transfer(tid, t["id"])

    def test_insufficient_stock_rejected_atomically(self, two_warehouses, user_id):
        tid = two_warehouses
        with pytest.raises(ValueError):
            tr_svc.create_transfer(tid, user_id, "principal", "Norte",
                                   [{"sku": "A", "qty": 50},
                                    {"sku": "A", "qty": 60}])  # 110 > 100 total
        # Nothing must have been applied
        assert _stock(tid, "A", "principal") == 100.0
        assert query_one(
            "SELECT COUNT(*)::int AS c FROM inventory_transfer_log WHERE tenant_id=%s",
            (tid,))["c"] == 0

    def test_same_warehouse_rejected(self, two_warehouses, user_id):
        with pytest.raises(ValueError):
            tr_svc.create_transfer(two_warehouses, user_id,
                                   "principal", "principal", [{"sku": "A", "qty": 1}])


class TestConcurrencyGuards:
    """QA found TOCTOU races: two concurrent sends drove stock negative, and
    two concurrent receives double-credited the destination. The fixes are
    atomic conditional UPDATEs, verified here with real threads."""

    def test_concurrent_sends_cannot_oversell(self, two_warehouses, user_id):
        import threading
        tid = two_warehouses
        # 100 in principal; two threads each try to send all 100.
        results: list = []

        def send():
            try:
                tr_svc.create_transfer(tid, user_id, "principal", "Norte",
                                       [{"sku": "A", "qty": 100}])
                results.append("ok")
            except ValueError:
                results.append("rejected")

        threads = [threading.Thread(target=send) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()

        # Exactly one succeeds; stock never goes negative.
        assert sorted(results) == ["ok", "rejected"]
        assert _stock(tid, "A", "principal") == 0.0
        assert query_one(
            "SELECT COUNT(*)::int AS c FROM inventory_stock "
            "WHERE tenant_id=%s AND current_stock < 0", (tid,))["c"] == 0
        assert query_one(
            "SELECT COUNT(*)::int AS c FROM inventory_transfer_log "
            "WHERE tenant_id=%s AND status='in_transit'", (tid,))["c"] == 1

    def test_concurrent_receives_cannot_overcredit(self, two_warehouses, user_id):
        import threading
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, user_id, "principal", "Norte",
                                   [{"sku": "A", "qty": 40}])
        results: list = []

        def receive():
            try:
                tr_svc.receive_transfer(tid, t["id"], None)
                results.append("ok")
            except ValueError:
                results.append("rejected")

        threads = [threading.Thread(target=receive) for _ in range(2)]
        for t2 in threads: t2.start()
        for t2 in threads: t2.join()

        assert "ok" in results  # at least one applied
        # Destination credited exactly once (10 base + 40), never doubled.
        assert _stock(tid, "A", "Norte") == 50.0
        row = query_one(
            "SELECT qty_sent, qty_received FROM inventory_transfer_items "
            "WHERE tenant_id=%s AND transfer_id=%s", (tid, t["id"]))
        assert float(row["qty_received"]) <= float(row["qty_sent"])


class TestCloseWithLoss:
    """Closing a partial transfer writes off the missing units as shrinkage
    (reason transfer_loss) WITHOUT touching stock — the in-transit units
    already left the origin at send time and never arrived anywhere."""

    def _partial(self, tid, user_id):
        t = tr_svc.create_transfer(tid, user_id, "principal", "Norte",
                                   [{"sku": "A", "qty": 30}])
        tr_svc.receive_transfer(tid, t["id"], [{"sku": "A", "received_qty": 20}])
        return t["id"]

    def test_close_records_loss_and_status(self, two_warehouses, user_id):
        tid = two_warehouses
        transfer_id = self._partial(tid, user_id)
        out = tr_svc.close_transfer(tid, transfer_id, user_id)
        assert out["status"] == "closed"
        # Shrinkage row: 10 missing units attributed to the origin warehouse
        row = query_one(
            """SELECT quantity, reason, warehouse FROM inventory_shrinkage
               WHERE tenant_id = %s AND sku = 'A'""", (tid,))
        assert row is not None
        assert float(row["quantity"]) == 10.0
        assert row["reason"] == "transfer_loss"
        assert row["warehouse"] == "principal"
        # Stock untouched by the close: origin lost the units at send time
        assert _stock(tid, "A", "principal") == 70.0
        assert _stock(tid, "A", "Norte") == 30.0

    def test_close_requires_partial_status(self, two_warehouses, user_id):
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, user_id, "principal", "Norte",
                                   [{"sku": "A", "qty": 30}])
        with pytest.raises(ValueError):
            tr_svc.close_transfer(tid, t["id"], user_id)  # in_transit -> cancel instead
        tr_svc.receive_transfer(tid, t["id"], None)
        with pytest.raises(ValueError):
            tr_svc.close_transfer(tid, t["id"], user_id)  # fully received

    def test_close_api_permission_pair(self, client, viewer_headers, analyst_headers,
                                       two_warehouses, user_id):
        tid = two_warehouses
        transfer_id = self._partial(tid, user_id)
        r = client.post(f"/api/v1/inventory/transfers/{transfer_id}/close",
                        headers=viewer_headers)
        assert r.status_code == 403
        assert query_one(
            "SELECT status FROM inventory_transfer_log WHERE id=%s",
            (transfer_id,))["status"] == "partial"
        r = client.post(f"/api/v1/inventory/transfers/{transfer_id}/close",
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "closed"


class TestTransferApi:
    def test_viewer_denied_state_unchanged(self, client, viewer_headers, two_warehouses):
        tid = two_warehouses
        r = client.post("/api/v1/inventory/transfers", json={
            "from_warehouse": "principal", "to_warehouse": "Norte",
            "items": [{"sku": "A", "qty": 10}],
        }, headers=viewer_headers)
        assert r.status_code == 403
        assert _stock(tid, "A", "principal") == 100.0
        assert query_one(
            "SELECT COUNT(*)::int AS c FROM inventory_transfer_log WHERE tenant_id=%s",
            (tid,))["c"] == 0

    def test_analyst_full_cycle(self, client, analyst_headers, two_warehouses):
        tid = two_warehouses
        r = client.post("/api/v1/inventory/transfers", json={
            "from_warehouse": "principal", "to_warehouse": "Norte",
            "items": [{"sku": "A", "qty": 10}],
        }, headers=analyst_headers)
        assert r.status_code == 201, r.text
        transfer_id = r.json()["data"]["id"]
        assert _stock(tid, "A", "principal") == 90.0

        r = client.get("/api/v1/inventory/transfers?status=in_transit",
                       headers=analyst_headers)
        assert r.status_code == 200
        assert len(r.json()["data"]) == 1

        r = client.post(f"/api/v1/inventory/transfers/{transfer_id}/receive",
                        json={"lines": None}, headers=analyst_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "received"
        assert _stock(tid, "A", "Norte") == 20.0

    def test_cross_tenant_denied(self, client, analyst_headers, two_warehouses,
                                 make_tenant_user_headers):
        """A transfer of tenant A must be invisible/untouchable from tenant B."""
        tid = two_warehouses
        r = client.post("/api/v1/inventory/transfers", json={
            "from_warehouse": "principal", "to_warehouse": "Norte",
            "items": [{"sku": "A", "qty": 10}],
        }, headers=analyst_headers)
        assert r.status_code == 201
        transfer_id = r.json()["data"]["id"]

        other_headers = make_tenant_user_headers(role="analyst")
        r2 = client.post(f"/api/v1/inventory/transfers/{transfer_id}/receive",
                         json={"lines": None}, headers=other_headers)
        assert r2.status_code == 404
        assert _stock(tid, "A", "Norte") == 10.0  # nothing arrived

        listed = client.get("/api/v1/inventory/transfers", headers=other_headers)
        assert listed.status_code == 200
        assert listed.json()["data"] == []
