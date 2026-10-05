"""
Tests for ROI monthly evolution (feature 1.5): overstock capital-freed
snapshots and the monthly summary aggregation.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.db.connection import execute, query_one


class TestSumOverstockValue:
    def test_only_counts_overstock_items(self):
        from backend.inventory.service import _sum_overstock_value

        items = [
            {"signal": "SOBRESTOCK", "inventory_value": 1000.0},
            {"signal": "OK", "inventory_value": 500.0},
            {"signal": "SOBRESTOCK", "inventory_value": 250.0},
            {"signal": "PEDIR_YA", "inventory_value": None},
        ]
        assert _sum_overstock_value(items) == 1250.0

    def test_empty_items_returns_zero(self):
        from backend.inventory.service import _sum_overstock_value
        assert _sum_overstock_value([]) == 0.0

    def test_missing_inventory_value_treated_as_zero(self):
        from backend.inventory.service import _sum_overstock_value
        assert _sum_overstock_value([{"signal": "SOBRESTOCK"}]) == 0.0


class TestRunMonthlyOverstockSnapshot:
    """
    The loop resolves its session with `resolve_active_session` — the same
    resolver every screen uses — and reads the status at the tenant's own
    planning grain.

    Both tests here used to patch `get_latest_completed_session`, which this
    loop stopped calling. A patch on a function nobody calls does nothing, so
    `test_skips_tenant_without_completed_session` was asserting an empty table
    for a reason unrelated to what it claimed to test: it could not fail. And
    the surviving `get_inventory_status` double took `(t, s)` positionally, so
    the day the loop started passing `period=` the call raised TypeError, the
    per-tenant `except` swallowed it into a log line, and the only visible
    symptom was a missing row.
    """

    def _patch_loop(self, monkeypatch, tid, sid, period, items, seen):
        from backend.inventory import service
        from backend.sessions import planning_service

        monkeypatch.setattr(
            service, "get_tenants_with_active_sessions",
            lambda: [{"tenant_id": tid}],
        )
        monkeypatch.setattr(
            planning_service, "resolve_active_session",
            lambda t: sid if t == tid else None,
        )
        monkeypatch.setattr(
            planning_service, "get_planning",
            lambda t: {"period": period},
        )

        def _status(t, s, period=None, **kwargs):
            seen.append({"tenant_id": t, "session_id": s, "period": period})
            return items

        monkeypatch.setattr(service, "get_inventory_status", _status)

    def test_inserts_snapshot_row_per_tenant(self, client, monkeypatch, test_tenant):
        from backend.inventory import service

        tid = test_tenant["id"]
        sess_id = f"sess_{tid[:8]}"
        seen: list[dict] = []

        self._patch_loop(monkeypatch, tid, sess_id, "daily", [
            {"sku": "OS-1", "signal": "SOBRESTOCK", "inventory_value": 3000.0},
            {"sku": "OS-2", "signal": "SOBRESTOCK", "inventory_value": 1500.0},
            {"sku": "OK-1", "signal": "OK", "inventory_value": 999.0},
        ], seen)

        service.run_monthly_overstock_snapshot()

        row = query_one(
            """SELECT overstock_value, session_id FROM inventory_overstock_snapshots
               WHERE tenant_id = %s ORDER BY recorded_at DESC LIMIT 1""",
            (tid,),
        )
        assert row is not None
        assert float(row["overstock_value"]) == 4500.0
        assert row["session_id"] == sess_id

    def test_reads_the_status_at_the_tenants_own_grain(self, client, monkeypatch, test_tenant):
        """
        The snapshot difference between two months is what /impacto headlines as
        "capital liberado", so the SOBRESTOCK population it measures has to be
        the one the app calls overstocked. Read as daily, a weekly tenant's
        coverage is divided by the wrong unit and a different set of SKUs comes
        back overstocked — a figure about a population no screen ever showed.
        """
        from backend.inventory import service

        tid = test_tenant["id"]
        seen: list[dict] = []

        self._patch_loop(monkeypatch, tid, f"sess_{tid[:8]}", "weekly", [], seen)

        service.run_monthly_overstock_snapshot()

        assert seen, "the loop never reached get_inventory_status"
        assert seen[0]["period"] == "weekly"

    def test_a_tenant_with_no_active_session_is_skipped(self, client, monkeypatch, test_tenant):
        from backend.inventory import service
        from backend.sessions import planning_service

        tid = test_tenant["id"]
        monkeypatch.setattr(
            service, "get_tenants_with_active_sessions",
            lambda: [{"tenant_id": tid}],
        )
        monkeypatch.setattr(planning_service, "resolve_active_session", lambda t: None)
        # If the loop ignored the resolver and snapshotted anyway, this would
        # raise rather than quietly writing a row against the wrong session.
        monkeypatch.setattr(
            service, "get_inventory_status",
            lambda *a, **k: pytest.fail("no session resolved — nothing should be read"),
        )

        service.run_monthly_overstock_snapshot()

        row = query_one(
            "SELECT id FROM inventory_overstock_snapshots WHERE tenant_id = %s",
            (tid,),
        )
        assert row is None


class TestGetMonthlySummary:
    def test_aggregates_by_calendar_month_and_computes_capital_freed(self, test_tenant):
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
                    skus_order_now, skus_order_soon, suggested_count, approved_count)
               VALUES (%s, 's1', %s, 2, 20, 500, 1, 0, 2, 2)""",
            (tid, last_month),
        )
        execute(
            """INSERT INTO inventory_po_log
                   (tenant_id, session_id, generated_at, sku_count, total_units, total_value,
                    skus_order_now, skus_order_soon, suggested_count, approved_count)
               VALUES (%s, 's1', %s, 1, 10, 300, 2, 0, 4, 3)""",
            (tid, last_month),
        )
        execute(
            """INSERT INTO inventory_po_log
                   (tenant_id, session_id, generated_at, sku_count, total_units, total_value,
                    skus_order_now, skus_order_soon, suggested_count, approved_count)
               VALUES (%s, 's1', %s, 1, 5, 150, 1, 1, 2, 1)""",
            (tid, this_month),
        )

        # Overstock snapshots are opening measurements: 10000 at the start of
        # last month, 6000 at the start of this one. The 4000 drop therefore
        # happened *during last month* and belongs to last month's row.
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
        assert this_row["urgent_lines_ordered"] == 1
        assert this_row["total_value"] == 150.0
        assert this_row["adoption_rate"] == 0.5          # 1 approved / 2 suggested
        # No snapshot opening next month yet, so this month is still unmeasured.
        assert this_row["capital_freed"] is None
        assert this_row["capital_freed_status"] == "not_measured"

        last_row = next(r for r in rows if r["month"] == last_month.strftime("%Y-%m"))
        assert last_row["pos_count"] == 2
        assert last_row["urgent_lines_ordered"] == 3
        assert last_row["total_value"] == 800.0
        assert last_row["adoption_rate"] == pytest.approx(5 / 6)
        assert last_row["capital_freed"] == 4000.0    # 10000 -> 6000 during last month
        assert last_row["capital_freed_status"] == "measured"

    def test_month_with_no_activity_returns_zeroed_row(self, test_tenant):
        from backend.inventory.roi_service import get_monthly_summary

        rows = get_monthly_summary(test_tenant["id"], months=2)

        assert len(rows) == 2
        for row in rows:
            assert row["pos_count"] == 0
            assert row["urgent_lines_ordered"] == 0
            assert row["total_value"] is None   # unknown, not a measured zero
            assert row["adoption_rate"] is None
            assert row["capital_freed"] is None
            assert row["capital_freed_status"] == "not_measured"

    def test_a_month_that_grew_is_told_apart_from_a_month_never_measured(
        self, test_tenant,
    ):
        """
        Both cases leave `capital_freed` None, and the screen printed the same
        sentence for both — *"Necesitamos dos mediciones mensuales seguidas"*.
        So a tenant whose dead stock had just GROWN was told we lacked data, and
        the column became structurally incapable of reporting anything but good
        news. The status is what separates them.
        """
        from backend.inventory.roi_service import get_monthly_summary

        tid = test_tenant["id"]
        now = datetime.now(tz=timezone.utc)
        this_month = now.replace(day=1, hour=12, minute=0, second=0, microsecond=0)
        last_month = (this_month - timedelta(days=1)).replace(
            day=1, hour=12, minute=0, second=0, microsecond=0
        )
        for value, when in ((5000, last_month), (8000, this_month)):
            execute(
                """INSERT INTO inventory_overstock_snapshots
                       (tenant_id, session_id, overstock_value, recorded_at)
                   VALUES (%s, 's1', %s, %s)""",
                (tid, value, when),
            )

        rows = get_monthly_summary(tid, months=2)
        last_row = next(r for r in rows if r["month"] == last_month.strftime("%Y-%m"))
        this_row = rows[0]

        # Both measurements exist and overstock went UP: this is a fact about
        # the tenant's inventory, not a gap in ours.
        assert last_row["capital_freed"] is None
        assert last_row["capital_freed_status"] == "grew"
        # No snapshot opening next month, so this one genuinely is unmeasured.
        assert this_row["capital_freed"] is None
        assert this_row["capital_freed_status"] == "not_measured"

    def test_capital_freed_is_none_when_overstock_increases(self, test_tenant):
        from backend.inventory.roi_service import get_monthly_summary

        tid = test_tenant["id"]
        now = datetime.now(tz=timezone.utc)
        this_month = now.replace(day=1, hour=12, minute=0, second=0, microsecond=0)
        last_month = (this_month - timedelta(days=1)).replace(
            day=1, hour=12, minute=0, second=0, microsecond=0
        )

        # Last month: overstock value = 5000. This month: overstock value = 8000.
        # Delta = 5000 - 8000 = -3000 (negative), so capital_freed should be None.
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
        # Overstock went UP during last month (5000 -> 8000), so no capital was
        # freed and the row must say "unknown" rather than 0.
        last_row = next(r for r in rows if r["month"] == last_month.strftime("%Y-%m"))
        assert last_row["capital_freed"] is None
        this_row = rows[0]  # most recent first
        assert this_row["month"] == this_month.strftime("%Y-%m")
        assert this_row["capital_freed"] is None


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
