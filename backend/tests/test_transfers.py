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
