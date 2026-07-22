"""
DB-level backstop: inventory_stock.current_stock can never go negative.

App-level guards (transfer_service._adjust_stock's atomic decrement floor,
shrinkage_service's conditional UPDATE) close the TOCTOU races that let stock
drift below zero, but the CHECK constraint added in migrations.py is the last
line of defense — it makes the invariant unviolable regardless of which code
path writes the row. These tests prove the constraint is actually installed and
rejects a direct negative write.
"""

import pytest
from uuid import uuid4


def _sku():
    return f"SKU-{uuid4().hex[:8].upper()}"


class TestStockNonNegativeConstraint:

    def test_direct_negative_update_is_rejected(self, client, test_tenant):
        """A raw UPDATE driving current_stock below zero must raise, and the row
        must remain >= 0 afterward."""
        from backend.inventory import service as inv_svc
        from backend.db.connection import execute, query_one

        tenant_id = test_tenant["id"]
        sku = _sku()
        inv_svc.upsert_stock(tenant_id, sku, {"current_stock": 10})

        # The constraint must reject this — psycopg2 raises a CheckViolation
        # (subclass of IntegrityError).
        with pytest.raises(Exception):
            execute(
                "UPDATE inventory_stock SET current_stock = -1 "
                "WHERE tenant_id = %s AND sku = %s",
                (tenant_id, sku),
            )

        row = query_one(
            "SELECT current_stock FROM inventory_stock "
            "WHERE tenant_id = %s AND sku = %s",
            (tenant_id, sku),
        )
        assert row is not None, "Stock row disappeared"
        assert float(row["current_stock"]) >= 0, (
            f"current_stock went negative ({row['current_stock']}) — the CHECK "
            "constraint did not hold"
        )
        # The valid pre-existing value is untouched by the rejected write.
        assert float(row["current_stock"]) == 10

    def test_constraint_is_installed(self, client, test_tenant):
        """The named CHECK constraint exists in the catalog (guards against the
        migration silently no-op'ing)."""
        from backend.db.connection import query_one

        row = query_one(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname = 'inventory_stock_current_stock_nonneg'"
        )
        assert row is not None, (
            "inventory_stock_current_stock_nonneg constraint is not installed"
        )

    def test_zero_is_allowed(self, client, test_tenant):
        """Zero is a legitimate stock level (empty shelf) — the constraint is
        >= 0, not > 0."""
        from backend.inventory import service as inv_svc
        from backend.db.connection import execute, query_one

        tenant_id = test_tenant["id"]
        sku = _sku()
        inv_svc.upsert_stock(tenant_id, sku, {"current_stock": 5})

        execute(
            "UPDATE inventory_stock SET current_stock = 0 "
            "WHERE tenant_id = %s AND sku = %s",
            (tenant_id, sku),
        )
        row = query_one(
            "SELECT current_stock FROM inventory_stock "
            "WHERE tenant_id = %s AND sku = %s",
            (tenant_id, sku),
        )
        assert row is not None
        assert float(row["current_stock"]) == 0
