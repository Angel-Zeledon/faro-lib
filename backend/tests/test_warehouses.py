"""Multi-warehouse foundation: a SKU can hold stock in multiple warehouses."""
from uuid import uuid4


def _sku():
    return f"WH_{uuid4().hex[:8]}"


class TestWarehouseStock:
    def test_same_sku_two_warehouses_creates_two_rows(self, client, auth_headers, test_tenant):
        from backend.inventory import service as svc
        tid = test_tenant["id"]
        sku = _sku()
        svc.upsert_stock(tid, sku, {"stock_actual": 100, "bodega": "Norte"})
        svc.upsert_stock(tid, sku, {"stock_actual": 40,  "bodega": "Sur"})
        from backend.db.connection import query
        rows = query(
            "SELECT bodega, stock_actual FROM inventory_stock WHERE tenant_id=%s AND sku=%s ORDER BY bodega",
            (tid, sku),
        )
        assert len(rows) == 2
        by_bodega = {r["bodega"]: float(r["stock_actual"]) for r in rows}
        assert by_bodega == {"Norte": 100.0, "Sur": 40.0}

    def test_upsert_without_bodega_defaults_to_principal(self, client, auth_headers, test_tenant):
        from backend.inventory import service as svc
        from backend.db.connection import query_one
        tid = test_tenant["id"]
        sku = _sku()
        svc.upsert_stock(tid, sku, {"stock_actual": 12})
        row = query_one("SELECT bodega FROM inventory_stock WHERE tenant_id=%s AND sku=%s", (tid, sku))
        assert row["bodega"] == "principal"

    def test_upsert_same_sku_same_bodega_updates_not_duplicates(self, client, auth_headers, test_tenant):
        from backend.inventory import service as svc
        from backend.db.connection import query
        tid = test_tenant["id"]
        sku = _sku()
        svc.upsert_stock(tid, sku, {"stock_actual": 5, "bodega": "Norte"})
        svc.upsert_stock(tid, sku, {"stock_actual": 9, "bodega": "Norte"})
        rows = query("SELECT stock_actual FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND bodega='Norte'", (tid, sku))
        assert len(rows) == 1 and float(rows[0]["stock_actual"]) == 9.0
