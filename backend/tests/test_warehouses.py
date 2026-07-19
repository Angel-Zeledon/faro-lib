"""Multi-warehouse foundation: a SKU can hold stock in multiple warehouses."""
from uuid import uuid4


def _sku():
    return f"WH_{uuid4().hex[:8]}"


def _warehouse():
    return f"warehouse_{uuid4().hex[:8]}"


class TestWarehouseStock:
    def test_same_sku_two_warehouses_creates_two_rows(self, client, auth_headers, test_tenant):
        from backend.inventory import service as svc
        tid = test_tenant["id"]
        sku = _sku()
        svc.upsert_stock(tid, sku, {"current_stock": 100, "warehouse": "Norte"})
        svc.upsert_stock(tid, sku, {"current_stock": 40,  "warehouse": "Sur"})
        from backend.db.connection import query
        rows = query(
            "SELECT warehouse, current_stock FROM inventory_stock WHERE tenant_id=%s AND sku=%s ORDER BY warehouse",
            (tid, sku),
        )
        assert len(rows) == 2
        by_warehouse = {r["warehouse"]: float(r["current_stock"]) for r in rows}
        assert by_warehouse == {"Norte": 100.0, "Sur": 40.0}

    def test_upsert_without_warehouse_defaults_to_principal(self, client, auth_headers, test_tenant):
        from backend.inventory import service as svc
        from backend.db.connection import query_one
        tid = test_tenant["id"]
        sku = _sku()
        svc.upsert_stock(tid, sku, {"current_stock": 12})
        row = query_one("SELECT warehouse FROM inventory_stock WHERE tenant_id=%s AND sku=%s", (tid, sku))
        assert row["warehouse"] == "principal"

    def test_upsert_same_sku_same_warehouse_updates_not_duplicates(self, client, auth_headers, test_tenant):
        from backend.inventory import service as svc
        from backend.db.connection import query
        tid = test_tenant["id"]
        sku = _sku()
        svc.upsert_stock(tid, sku, {"current_stock": 5, "warehouse": "Norte"})
        svc.upsert_stock(tid, sku, {"current_stock": 9, "warehouse": "Norte"})
        rows = query("SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND warehouse='Norte'", (tid, sku))
        assert len(rows) == 1 and float(rows[0]["current_stock"]) == 9.0

    def test_upsert_stock_returns_the_warehouse_it_wrote(self, client, auth_headers, test_tenant):
        from backend.inventory import service as svc
        tid = test_tenant["id"]
        sku = _sku()
        svc.upsert_stock(tid, sku, {"current_stock": 100, "warehouse": "Norte"})
        result = svc.upsert_stock(tid, sku, {"current_stock": 40, "warehouse": "Sur"})
        assert result["warehouse"] == "Sur"
        assert float(result["current_stock"]) == 40.0

    def test_upsert_with_only_warehouse_field_on_existing_row_does_not_raise(
        self, client, auth_headers, test_tenant,
    ):
        """
        On an existing (tenant, sku, warehouse) row, an update payload containing
        ONLY "warehouse" (already the conflict target, never in the SET list)
        must not produce an empty SET clause / SQL syntax error.
        """
        from backend.inventory import service as svc
        tid = test_tenant["id"]
        sku = _sku()
        svc.upsert_stock(tid, sku, {"current_stock": 100, "warehouse": "Norte"})
        result = svc.upsert_stock(tid, sku, {"warehouse": "Norte"})
        assert result["warehouse"] == "Norte"
        assert float(result["current_stock"]) == 100.0  # untouched, no error raised


class TestWarehouseBulkImport:
    def test_bulk_import_persists_warehouse_and_autocreates_warehouse(self, client, auth_headers, test_tenant):
        """A CSV with a warehouse column persists inventory_stock.warehouse and
        auto-creates a matching row in warehouses the first time that warehouse
        name is seen for the tenant."""
        sku = _sku()
        warehouse = _warehouse()
        csv_text = (
            "sku,current_stock,warehouse\n"
            f"{sku},75,{warehouse}\n"
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
            "SELECT current_stock, warehouse FROM inventory_stock WHERE tenant_id=%s AND sku=%s",
            (tid, sku),
        )
        assert stock_row is not None
        assert float(stock_row["current_stock"]) == 75
        assert stock_row["warehouse"] == warehouse

        wh_row = query_one(
            "SELECT name FROM warehouses WHERE tenant_id=%s AND name=%s",
            (tid, warehouse),
        )
        assert wh_row is not None
        assert wh_row["name"] == warehouse


class TestWarehouseEndpoints:
    """GET/POST /inventory/warehouses — list/create warehouses directly."""

    def test_viewer_denied_on_create_no_row_created(self, client, viewer_headers, test_tenant):
        from backend.db.connection import query_one
        tid = test_tenant["id"]
        name = _warehouse()

        r = client.post(
            "/api/v1/inventory/warehouses",
            json={"name": name},
            headers=viewer_headers,
        )
        assert r.status_code == 403

        row = query_one(
            "SELECT name FROM warehouses WHERE tenant_id=%s AND name=%s",
            (tid, name),
        )
        assert row is None

    def test_analyst_can_create_warehouse(self, client, analyst_headers, test_tenant):
        from backend.db.connection import query_one
        tid = test_tenant["id"]
        name = _warehouse()

        r = client.post(
            "/api/v1/inventory/warehouses",
            json={"name": name},
            headers=analyst_headers,
        )
        assert r.status_code == 201

        row = query_one(
            "SELECT name, is_default FROM warehouses WHERE tenant_id=%s AND name=%s",
            (tid, name),
        )
        assert row is not None
        assert row["name"] == name
        assert row["is_default"] is False

    def test_list_returns_created_warehouses(self, client, analyst_headers, test_tenant):
        name = _warehouse()
        create_r = client.post(
            "/api/v1/inventory/warehouses",
            json={"name": name},
            headers=analyst_headers,
        )
        assert create_r.status_code == 201

        r = client.get("/api/v1/inventory/warehouses", headers=analyst_headers)
        assert r.status_code == 200
        names = [w["name"] for w in r.json()["data"]]
        assert name in names

    def test_duplicate_name_is_idempotent_not_duplicated(self, client, analyst_headers, test_tenant):
        from backend.db.connection import query
        tid = test_tenant["id"]
        name = _warehouse()

        r1 = client.post("/api/v1/inventory/warehouses", json={"name": name}, headers=analyst_headers)
        r2 = client.post("/api/v1/inventory/warehouses", json={"name": name}, headers=analyst_headers)
        assert r1.status_code == 201
        assert r2.status_code == 201

        rows = query(
            "SELECT id FROM warehouses WHERE tenant_id=%s AND name=%s",
            (tid, name),
        )
        assert len(rows) == 1
