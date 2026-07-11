"""
Tests for ROI monthly evolution (feature 1.5): overstock capital-freed
snapshots and the monthly summary aggregation.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.db.connection import execute, query_one


class TestSumOverstockValue:
    def test_only_counts_sobrestock_items(self):
        from backend.inventory.service import _sum_overstock_value

        items = [
            {"signal": "SOBRESTOCK", "valor_inventario": 1000.0},
            {"signal": "OK", "valor_inventario": 500.0},
            {"signal": "SOBRESTOCK", "valor_inventario": 250.0},
            {"signal": "PEDIR_YA", "valor_inventario": None},
        ]
        assert _sum_overstock_value(items) == 1250.0

    def test_empty_items_returns_zero(self):
        from backend.inventory.service import _sum_overstock_value
        assert _sum_overstock_value([]) == 0.0

    def test_missing_valor_inventario_treated_as_zero(self):
        from backend.inventory.service import _sum_overstock_value
        assert _sum_overstock_value([{"signal": "SOBRESTOCK"}]) == 0.0


class TestRunMonthlyOverstockSnapshot:
    def test_inserts_snapshot_row_per_tenant(self, client, monkeypatch, test_tenant):
        from backend.inventory import service

        tid = test_tenant["id"]
        sess_id = f"sess_{tid[:8]}"

        monkeypatch.setattr(
            service, "get_tenants_with_active_sessions",
            lambda: [{"tenant_id": tid}],
        )
        monkeypatch.setattr(
            service, "get_latest_completed_session",
            lambda t: {"session_id": sess_id} if t == tid else None,
        )
        monkeypatch.setattr(
            service, "get_inventory_status",
            lambda t, s: [
                {"sku": "OS-1", "signal": "SOBRESTOCK", "valor_inventario": 3000.0},
                {"sku": "OS-2", "signal": "SOBRESTOCK", "valor_inventario": 1500.0},
                {"sku": "OK-1", "signal": "OK", "valor_inventario": 999.0},
            ],
        )

        service.run_monthly_overstock_snapshot()

        row = query_one(
            """SELECT overstock_value, session_id FROM inventory_overstock_snapshots
               WHERE tenant_id = %s ORDER BY recorded_at DESC LIMIT 1""",
            (tid,),
        )
        assert row is not None
        assert float(row["overstock_value"]) == 4500.0
        assert row["session_id"] == sess_id

    def test_skips_tenant_without_completed_session(self, client, monkeypatch, test_tenant):
        from backend.inventory import service

        tid = test_tenant["id"]
        monkeypatch.setattr(
            service, "get_tenants_with_active_sessions",
            lambda: [{"tenant_id": tid}],
        )
        monkeypatch.setattr(service, "get_latest_completed_session", lambda t: None)

        service.run_monthly_overstock_snapshot()

        row = query_one(
            "SELECT id FROM inventory_overstock_snapshots WHERE tenant_id = %s",
            (tid,),
        )
        assert row is None
