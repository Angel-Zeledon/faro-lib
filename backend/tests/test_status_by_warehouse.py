"""Network-aware per-warehouse semaphore (feature 5.4, spec §2)."""

import pytest

from backend.db import session_store
from backend.inventory import service as inv_svc
from backend.inventory import warehouse_service as wh_svc
from backend.inventory.series import SERIES_SEPARATOR


def _forecast_entry(daily, days=30):
    return {"lightgbm": {
        "historical": [],
        "forecast": [
            {"date": f"2026-08-{i+1:02d}", "value": daily, "lower": None, "upper": None}
            for i in range(days)
        ],
    }}


def _seed_stock(tid, sku, warehouse, stock, lead_time=5):
    inv_svc.upsert_stock(tid, sku, {
        "current_stock": stock, "lead_time_days": lead_time,
        "warehouse": warehouse, "moq": 1,
    })


class TestPerWarehouseDemand:
    def test_share_split_demand(self, test_tenant, completed_session):
        tid = test_tenant["id"]
        sid = completed_session["id"]
        # Global demand 10/day, split 80/20
        session_store.set_forecasts(tid, sid, {"A": _forecast_entry(10.0)})
        _seed_stock(tid, "A", "principal", 100)
        _seed_stock(tid, "A", "Norte", 100)
        wh_svc.set_demand_share(tid, "principal", 80)
        wh_svc.set_demand_share(tid, "Norte", 20)

        items = inv_svc.get_inventory_status_by_warehouse(tid, sid)
        by_wh = {i["warehouse"]: i for i in items if i["sku"] == "A"}
        assert by_wh["principal"]["daily_demand"] == pytest.approx(8.0)
        assert by_wh["Norte"]["daily_demand"] == pytest.approx(2.0)

    def test_store_keyed_forecasts_override_shares(self, test_tenant, completed_session):
        tid = test_tenant["id"]
        sid = completed_session["id"]
        session_store.set_forecasts(tid, sid, {
            f"A{SERIES_SEPARATOR}Norte": _forecast_entry(3.0),
            f"A{SERIES_SEPARATOR}principal": _forecast_entry(7.0),
        })
        _seed_stock(tid, "A", "principal", 100)
        _seed_stock(tid, "A", "Norte", 100)
        items = inv_svc.get_inventory_status_by_warehouse(tid, sid)
        by_wh = {i["warehouse"]: i for i in items if i["sku"] == "A"}
        assert by_wh["Norte"]["daily_demand"] == pytest.approx(3.0)
        assert by_wh["principal"]["daily_demand"] == pytest.approx(7.0)


class TestNetworkPass:
    def _seed_donor_and_needy(self, tid, sid, donor_stock=600.0):
        # 10/day everywhere; needy warehouse has 5 units (0.5 days of
        # coverage); the donor's default 600 units = 60 days, comfortably
        # above the 30-day post-donation floor.
        session_store.set_forecasts(tid, sid, {
            f"A{SERIES_SEPARATOR}Norte": _forecast_entry(10.0),
            f"A{SERIES_SEPARATOR}principal": _forecast_entry(10.0),
        })
        _seed_stock(tid, "A", "Norte", 5)
        _seed_stock(tid, "A", "principal", donor_stock)

    def test_donor_converts_order_to_transfer(self, test_tenant, completed_session):
        tid, sid = test_tenant["id"], completed_session["id"]
        self._seed_donor_and_needy(tid, sid)
        items = inv_svc.get_inventory_status_by_warehouse(tid, sid)
        needy = next(i for i in items if i["warehouse"] == "Norte" and i["sku"] == "A")
        assert needy["signal"] == "PEDIR_YA"
        assert needy["recommended_action"] == "transfer"
        ts = needy["transfer_suggestion"]
        assert ts["from_warehouse"] == "principal"
        assert ts["qty"] > 0
        assert ts["donor_coverage_days_after"] >= 30

    def test_no_donor_stays_order(self, test_tenant, completed_session):
        tid, sid = test_tenant["id"], completed_session["id"]
        # Donor has 60 units = 6 days of coverage -> cannot donate anything
        self._seed_donor_and_needy(tid, sid, donor_stock=60.0)
        items = inv_svc.get_inventory_status_by_warehouse(tid, sid)
        needy = next(i for i in items if i["warehouse"] == "Norte" and i["sku"] == "A")
        assert needy["recommended_action"] == "order"
        assert needy["transfer_suggestion"] is None

    def test_aggregated_status_unchanged(self, test_tenant, completed_session):
        """Regression: the SKU-level status must not learn about warehouses."""
        tid, sid = test_tenant["id"], completed_session["id"]
        self._seed_donor_and_needy(tid, sid)
        items = inv_svc.get_inventory_status(tid, sid)
        row = next(i for i in items if i["sku"] == "A")
        assert row["current_stock"] == 605.0  # summed across warehouses
        assert "recommended_action" not in row


class TestNetworkPassPeriodAware:
    """Polish #5: the donor coverage-after must read in the ACTIVE period's unit,
    and the 30-day 'don't strand the donor' floor must convert into that period
    so the physical guard stays equivalent. Daily stays byte-identical."""

    def _seed(self, tid, sid, per_period_demand):
        # Same layout as TestNetworkPass but demand is stated at the active
        # grain (units/PERIOD): needy Norte has 0.x periods of cover; the donor
        # principal has 600 units.
        session_store.set_forecasts(tid, sid, {
            f"A{SERIES_SEPARATOR}Norte": _forecast_entry(per_period_demand),
            f"A{SERIES_SEPARATOR}principal": _forecast_entry(per_period_demand),
        })
        _seed_stock(tid, "A", "Norte", 5)
        _seed_stock(tid, "A", "principal", 600)

    def test_daily_value_and_unit_unchanged(self, test_tenant, completed_session):
        tid, sid = test_tenant["id"], completed_session["id"]
        self._seed(tid, sid, 10.0)  # 10 units/day
        items = inv_svc.get_inventory_status_by_warehouse(tid, sid, period="daily")
        needy = next(i for i in items
                     if i["warehouse"] == "Norte" and i["sku"] == "A")
        ts = needy["transfer_suggestion"]
        # Donor donates 45, keeps 555 units at 10/day = 55.5 DAYS — the exact
        # value produced before the polish (daily path is untouched).
        assert ts is not None
        assert ts["donor_coverage_days_after"] == 55.5
        assert ts["coverage_unit"] == "day"

    def test_weekly_same_situation_reads_in_weeks(self, test_tenant, completed_session):
        tid, sid = test_tenant["id"], completed_session["id"]
        # Same physical rate (10 units/day) expressed at the weekly grain: 70/wk.
        # Under the OLD code the floor was 30 *weeks* of demand (70*30 = 2100 >
        # 600 stock), so nothing was donatable and the transfer never fired.
        self._seed(tid, sid, 70.0)
        items = inv_svc.get_inventory_status_by_warehouse(tid, sid, period="weekly")
        needy = next(i for i in items
                     if i["warehouse"] == "Norte" and i["sku"] == "A")
        ts = needy["transfer_suggestion"]
        assert ts is not None, "transfer must still fire under a weekly horizon"
        assert ts["coverage_unit"] == "week"
        # 555 units / 70 per-week = 7.9 WEEKS == the daily 55.5 days / 7.
        assert ts["donor_coverage_days_after"] == 7.9
        assert ts["donor_coverage_days_after"] == pytest.approx(55.5 / 7, abs=0.05)


class TestPreloadedData:
    """The daily alert loop fetches forecasts/stock/lead-times once per tenant
    and shares them with both status computations — preloaded inputs must be
    honored (no re-fetch) and produce identical results."""

    def _seed(self, tid, sid):
        session_store.set_forecasts(tid, sid, {
            f"A{SERIES_SEPARATOR}Norte": _forecast_entry(10.0),
            f"A{SERIES_SEPARATOR}principal": _forecast_entry(10.0),
        })
        _seed_stock(tid, "A", "Norte", 5)
        _seed_stock(tid, "A", "principal", 600)

    def _patch_fetchers_to_fail(self, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError(
                "data fetch called even though preloaded data was provided")
        monkeypatch.setattr(session_store, "get_forecasts", boom)
        monkeypatch.setattr(inv_svc, "list_stock", boom)
        monkeypatch.setattr(inv_svc, "get_learned_lead_times", boom)

    def test_by_warehouse_preloaded_equals_self_fetching(
        self, test_tenant, completed_session, monkeypatch,
    ):
        tid, sid = test_tenant["id"], completed_session["id"]
        self._seed(tid, sid)

        baseline = inv_svc.get_inventory_status_by_warehouse(tid, sid)
        forecasts = session_store.get_forecasts(tid, sid) or {}
        stock_rows = inv_svc.list_stock(tid)
        learned = inv_svc.get_learned_lead_times(tid)

        self._patch_fetchers_to_fail(monkeypatch)
        preloaded = inv_svc.get_inventory_status_by_warehouse(
            tid, sid,
            forecasts=forecasts, stock_rows=stock_rows,
            learned_lead_times=learned,
        )
        assert preloaded == baseline
        assert any(i["signal"] != "SIN_DATOS" for i in preloaded)  # non-trivial result

    def test_aggregate_impl_preloaded_equals_public_call(
        self, test_tenant, completed_session, monkeypatch,
    ):
        """_compute_inventory_status with preloaded data must equal the public
        self-fetching get_inventory_status (same alert-loop guarantee for the
        aggregated view)."""
        tid, sid = test_tenant["id"], completed_session["id"]
        self._seed(tid, sid)

        baseline = inv_svc.get_inventory_status(tid, sid)
        forecasts = session_store.get_forecasts(tid, sid) or {}
        stock_rows = inv_svc.list_stock(tid)
        learned = inv_svc.get_learned_lead_times(tid)

        self._patch_fetchers_to_fail(monkeypatch)
        preloaded = inv_svc._compute_inventory_status(
            tid, sid,
            forecasts=forecasts, stock_rows=stock_rows,
            learned_lead_times=learned,
        )
        assert preloaded == baseline
        assert any(i["signal"] != "SIN_DATOS" for i in preloaded)


class TestBriefingFold:
    def test_briefing_carries_transfer_suggestions(self, test_tenant, completed_session):
        """/hoy reads suggestions from the briefing instead of re-running the
        by-warehouse status — the payloads must match."""
        tid, sid = test_tenant["id"], completed_session["id"]
        session_store.set_forecasts(tid, sid, {
            f"A{SERIES_SEPARATOR}Norte": _forecast_entry(10.0),
            f"A{SERIES_SEPARATOR}principal": _forecast_entry(10.0),
        })
        _seed_stock(tid, "A", "Norte", 5)
        _seed_stock(tid, "A", "principal", 600)

        briefing = inv_svc.get_morning_briefing(tid, sid)
        suggestions = briefing["transfer_suggestions"]
        assert len(suggestions) == 1
        assert suggestions[0]["sku"] == "A"
        assert suggestions[0]["warehouse"] == "Norte"
        assert suggestions[0]["transfer_suggestion"]["from_warehouse"] == "principal"

    def test_mono_warehouse_briefing_has_empty_suggestions(self, test_tenant, completed_session):
        tid, sid = test_tenant["id"], completed_session["id"]
        session_store.set_forecasts(tid, sid, {"A": _forecast_entry(10.0)})
        _seed_stock(tid, "A", "principal", 100)
        briefing = inv_svc.get_morning_briefing(tid, sid)
        assert briefing["transfer_suggestions"] == []


class TestApi:
    def test_by_warehouse_param(self, client, auth_headers, test_tenant, completed_session):
        tid, sid = test_tenant["id"], completed_session["id"]
        session_store.set_forecasts(tid, sid, {"A": _forecast_entry(10.0)})
        _seed_stock(tid, "A", "principal", 100)
        r = client.get(
            f"/api/v1/inventory/status?session_id={sid}&by_warehouse=true",
            headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert all("warehouse" in i for i in data["items"])
        assert "transfers_suggested" in data["summary"]
