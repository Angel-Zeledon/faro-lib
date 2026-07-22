"""Inter-warehouse transfers (feature 5.4): schema, lifecycle, API."""

from backend.db.connection import query


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
