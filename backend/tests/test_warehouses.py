"""Multi-warehouse foundation: a SKU can hold stock in multiple warehouses."""
from uuid import uuid4


def _sku():
    return f"WH_{uuid4().hex[:8]}"


def _bodega():
    return f"Bodega_{uuid4().hex[:8]}"


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


class TestWarehouseBulkImport:
    def test_bulk_import_persists_bodega_and_autocreates_warehouse(self, client, auth_headers, test_tenant):
        """A CSV with a bodega column persists inventory_stock.bodega and
        auto-creates a matching row in warehouses the first time that bodega
        name is seen for the tenant."""
        sku = _sku()
        bodega = _bodega()
        csv_text = (
            "sku,stock_actual,bodega\n"
            f"{sku},75,{bodega}\n"
        )
        r = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("stock.csv", csv_text.encode("utf-8"), "text/csv")},
            headers=auth_headers,
        )
        assert r.status_code == 200

        from backend.db.connection import query_one
        tid = test_tenant["id"]

        stock_row = query_one(
            "SELECT stock_actual, bodega FROM inventory_stock WHERE tenant_id=%s AND sku=%s",
            (tid, sku),
        )
        assert stock_row is not None
        assert float(stock_row["stock_actual"]) == 75
        assert stock_row["bodega"] == bodega

        wh_row = query_one(
            "SELECT name FROM warehouses WHERE tenant_id=%s AND name=%s",
            (tid, bodega),
        )
        assert wh_row is not None
        assert wh_row["name"] == bodega
