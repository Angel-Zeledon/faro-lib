"""TDD for backend/db/seed_mock.py — idempotent, coherent multi-warehouse
mock-data seeder (Multi-Warehouse Foundation, Task 4).

Scope note mirrored from the seeder itself: there is no sales-history table
in this schema (sales history only ever lives inside uploaded dataset
CSV/Excel files attached to a training session), so these tests only cover
what the seeder actually writes: `warehouses` and `inventory_stock`.
"""
from backend.db.connection import query, query_one
from backend.db.seed_mock import seed_mock_tenant


class TestSeedMockTenant:
    def test_seeds_warehouses_and_coherent_stock_rows(self, client, test_tenant):
        tid = test_tenant["id"]

        summary = seed_mock_tenant(tid, warehouses=3, skus=5)

        wh_rows = query("SELECT name FROM warehouses WHERE tenant_id = %s", (tid,))
        assert len(wh_rows) >= 3

        stock_rows = query(
            "SELECT sku, warehouse, current_stock, unit_cost, sale_price, "
            "lead_time_days FROM inventory_stock WHERE tenant_id = %s",
            (tid,),
        )
        assert len(stock_rows) > 0

        warehouses = {r["warehouse"] for r in stock_rows}
        assert len(warehouses) >= 3

        for row in stock_rows:
            assert float(row["current_stock"]) >= 0
            if row["unit_cost"] is not None and row["sale_price"] is not None:
                assert float(row["unit_cost"]) < float(row["sale_price"])
            assert 3 <= row["lead_time_days"] <= 30

        assert summary["tenant_id"] == tid
        assert summary["stock_rows"] == len(stock_rows)
        # No sales-history table exists in this schema (verified: no
        # `CREATE TABLE.*sales` in backend/db/migrations.py) — sales history
        # only ever lives in uploaded dataset files, never as DB rows. The
        # seeder must say so explicitly rather than silently skip it.
        assert summary["sales_history_seeded"] is False

    def test_stock_varies_across_warehouses_not_copy_pasted(self, client, test_tenant):
        tid = test_tenant["id"]
        seed_mock_tenant(tid, warehouses=3, skus=5)

        rows = query(
            "SELECT sku, warehouse, current_stock FROM inventory_stock WHERE tenant_id = %s",
            (tid,),
        )
        by_sku: dict[str, set[float]] = {}
        for r in rows:
            by_sku.setdefault(r["sku"], set()).add(float(r["current_stock"]))

        # At least one SKU must show differing stock across warehouses —
        # guards against a lazy implementation that copy-pastes the same
        # row per warehouse.
        assert any(len(values) > 1 for values in by_sku.values())

    def test_rerun_is_idempotent_no_duplicate_rows(self, client, test_tenant):
        tid = test_tenant["id"]
        seed_mock_tenant(tid, warehouses=3, skus=5)

        wh_before = query_one(
            "SELECT COUNT(*) AS c FROM warehouses WHERE tenant_id = %s", (tid,)
        )["c"]
        stock_before = query_one(
            "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id = %s", (tid,)
        )["c"]

        seed_mock_tenant(tid, warehouses=3, skus=5)

        wh_after = query_one(
            "SELECT COUNT(*) AS c FROM warehouses WHERE tenant_id = %s", (tid,)
        )["c"]
        stock_after = query_one(
            "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id = %s", (tid,)
        )["c"]

        assert wh_after == wh_before
        assert stock_after == stock_before
