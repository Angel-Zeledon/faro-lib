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


class TestGetMonthlySummary:
    def test_aggregates_by_calendar_month_and_computes_capital_liberado(self, test_tenant):
        from backend.inventory.roi_service import get_monthly_summary

        tid = test_tenant["id"]
        now = datetime.now(tz=timezone.utc)
        this_month = now.replace(day=1, hour=12, minute=0, second=0, microsecond=0)
        last_month = (this_month - timedelta(days=1)).replace(
            day=1, hour=12, minute=0, second=0, microsecond=0
        )

        # Last month: 2 orders. This month: 1 order.
        execute(
            """INSERT INTO inventory_po_log
                   (tenant_id, session_id, generated_at, sku_count, total_units, total_value,
                    skus_pedir_ya, skus_pedir_pronto, suggested_count, approved_count)
               VALUES (%s, 's1', %s, 2, 20, 500, 1, 0, 2, 2)""",
            (tid, last_month),
        )
        execute(
            """INSERT INTO inventory_po_log
                   (tenant_id, session_id, generated_at, sku_count, total_units, total_value,
                    skus_pedir_ya, skus_pedir_pronto, suggested_count, approved_count)
               VALUES (%s, 's1', %s, 1, 10, 300, 2, 0, 4, 3)""",
            (tid, last_month),
        )
        execute(
            """INSERT INTO inventory_po_log
                   (tenant_id, session_id, generated_at, sku_count, total_units, total_value,
                    skus_pedir_ya, skus_pedir_pronto, suggested_count, approved_count)
               VALUES (%s, 's1', %s, 1, 5, 150, 1, 1, 2, 1)""",
            (tid, this_month),
        )

        # Overstock snapshots: last month 10000, this month 6000 -> 4000 freed.
        execute(
            """INSERT INTO inventory_overstock_snapshots
                   (tenant_id, session_id, overstock_value, recorded_at)
               VALUES (%s, 's1', 10000, %s)""",
            (tid, last_month),
        )
        execute(
            """INSERT INTO inventory_overstock_snapshots
                   (tenant_id, session_id, overstock_value, recorded_at)
               VALUES (%s, 's1', 6000, %s)""",
            (tid, this_month),
        )

        rows = get_monthly_summary(tid, months=3)

        assert len(rows) == 3
        assert rows[0]["month"] == this_month.strftime("%Y-%m")  # most recent first

        this_row = rows[0]
        assert this_row["pos_count"] == 1
        assert this_row["skus_pedir_ya"] == 1
        assert this_row["total_value"] == 150.0
        assert this_row["adoption_rate"] == 0.5          # 1 approved / 2 suggested
        assert this_row["capital_liberado"] == 4000.0

        last_row = next(r for r in rows if r["month"] == last_month.strftime("%Y-%m"))
        assert last_row["pos_count"] == 2
        assert last_row["skus_pedir_ya"] == 3
        assert last_row["total_value"] == 800.0
        assert last_row["adoption_rate"] == pytest.approx(5 / 6)
        assert last_row["capital_liberado"] is None      # no snapshot before last_month

    def test_month_with_no_activity_returns_zeroed_row(self, test_tenant):
        from backend.inventory.roi_service import get_monthly_summary

        rows = get_monthly_summary(test_tenant["id"], months=2)

        assert len(rows) == 2
        for row in rows:
            assert row["pos_count"] == 0
            assert row["skus_pedir_ya"] == 0
            assert row["adoption_rate"] is None
            assert row["capital_liberado"] is None

    def test_capital_liberado_is_none_when_overstock_increases(self, test_tenant):
        from backend.inventory.roi_service import get_monthly_summary

        tid = test_tenant["id"]
        now = datetime.now(tz=timezone.utc)
        this_month = now.replace(day=1, hour=12, minute=0, second=0, microsecond=0)
        last_month = (this_month - timedelta(days=1)).replace(
            day=1, hour=12, minute=0, second=0, microsecond=0
        )

        # Last month: overstock value = 5000. This month: overstock value = 8000.
        # Delta = 5000 - 8000 = -3000 (negative), so capital_liberado should be None.
        execute(
            """INSERT INTO inventory_overstock_snapshots
                   (tenant_id, session_id, overstock_value, recorded_at)
               VALUES (%s, 's1', 5000, %s)""",
            (tid, last_month),
        )
        execute(
            """INSERT INTO inventory_overstock_snapshots
                   (tenant_id, session_id, overstock_value, recorded_at)
               VALUES (%s, 's1', 8000, %s)""",
            (tid, this_month),
        )

        rows = get_monthly_summary(tid, months=2)

        assert len(rows) == 2
        this_row = rows[0]  # most recent first
        assert this_row["month"] == this_month.strftime("%Y-%m")
        assert this_row["capital_liberado"] is None  # overstock went UP, so no capital freed


class TestRoiMonthlyEndpoint:
    def test_viewer_can_read(self, client, viewer_headers):
        resp = client.get("/api/v1/inventory/roi/monthly", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) == 6  # default months

    def test_unauthenticated_rejected(self, client):
        resp = client.get("/api/v1/inventory/roi/monthly")
        assert resp.status_code == 401

    def test_months_param_respected(self, client, auth_headers):
        resp = client.get("/api/v1/inventory/roi/monthly?months=3", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 3
