"""
A declared event must change the per-warehouse recommendation the same way it
changes the aggregate one (docs/stability.md 19.5, per-warehouse follow-up).

`_compute_inventory_status` (the aggregate `/inventario` "Todas" view) was
taught to read `inventory_events` / `inventory_event_multipliers`
(test_event_impact_on_status.py). `get_inventory_status_by_warehouse` (the
per-warehouse tabs on the SAME screen) was not, so a tenant with more than one
warehouse saw one quantity under "Todas" and a DIFFERENT, event-blind quantity
under a warehouse tab for the same SKU during the same declared event — two
answers to one question, one click apart.

These tests exercise the real function against a real Postgres row, the same
discipline as test_event_impact_on_status.py: no mocking of
`_event_demand_multiplier` / `_active_events_window` / `_resolve_multiplier`,
which already have pure-Python coverage of their own (test_event_multipliers.py).

Every forecast point below carries no `upper`/`q90` key, so `avg_std` (and the
classical safety-stock term) is exactly 0, and no supplier is set on either
warehouse's stock row, so the lead-time-variance term is 0 too — the same
isolation test_event_impact_on_status.py uses, so the expected numbers can be
hand-computed exactly:

    avg_daily_eff_wh = avg_daily * share * event_multiplier
    reorder_point_wh = avg_daily_eff_wh * lead_time_days      (no safety term)
    recommended_wh   = max(0, reorder_point_wh - current_stock_wh)
"""
from datetime import date, timedelta
from uuid import uuid4

import pytest

from backend.db import session_store
from backend.inventory import service as inv_svc
from backend.inventory import warehouse_service as wh_svc
from backend.sessions.service import create_session


def _sku() -> str:
    return f"SKUWH-{uuid4().hex[:8].upper()}"


def _flat_forecast(period_demand: float, n_points: int = 40) -> dict:
    """A flat daily forecast of `period_demand` units/day, no upper/q90 (see
    module docstring) — same shape as test_event_impact_on_status.py's helper."""
    start = date.today()
    pts = [
        {"date": (start + timedelta(days=i)).isoformat(), "value": period_demand}
        for i in range(n_points)
    ]
    return {"lightgbm": {"forecast": pts}}


def _new_session(test_tenant) -> str:
    return create_session(
        test_tenant["id"], "usr_test", f"event-wh-status-{uuid4().hex[:6]}",
    )["id"]


def _seed_two_warehouses(
    test_tenant, session_id, sku, period_demand, *,
    lead_time_days=20, stock_norte=30.0, stock_sur=20.0,
    share_norte=60, share_sur=40,
):
    """One SKU, one flat SKU-global forecast (share-split demand mode, no
    store-keyed series), split 60/40 across two warehouses with no supplier
    set on either — isolates the event multiplier from lead-time-variance and
    from which warehouse the aggregate view picks as its 'representative'
    row (both warehouses carry the SAME lead_time_days, so it does not
    matter which one wins that tie)."""
    tid = test_tenant["id"]
    inv_svc.upsert_stock(tid, sku, {
        "current_stock": stock_norte, "lead_time_days": lead_time_days,
        "moq": 1, "warehouse": "Norte",
    })
    inv_svc.upsert_stock(tid, sku, {
        "current_stock": stock_sur, "lead_time_days": lead_time_days,
        "moq": 1, "warehouse": "Sur",
    })
    wh_svc.set_demand_share(tid, "Norte", share_norte)
    wh_svc.set_demand_share(tid, "Sur", share_sur)
    session_store.set_forecasts(tid, session_id, {
        **(session_store.get_forecasts(tid, session_id) or {}),
        sku: _flat_forecast(period_demand),
    })


def _create_event(test_tenant, start: date, end: date, multiplier: float, name="Semana Santa"):
    return inv_svc.create_event(test_tenant["id"], {
        "name": name, "start_date": start, "end_date": end, "multiplier": multiplier,
    })


def _agg_row(test_tenant, session_id, sku) -> dict:
    items = inv_svc.get_inventory_status(test_tenant["id"], session_id)
    by_sku = {i["sku"]: i for i in items}
    assert sku in by_sku
    return by_sku[sku]


def _wh_rows(test_tenant, session_id, sku) -> dict:
    items = inv_svc.get_inventory_status_by_warehouse(test_tenant["id"], session_id)
    by_wh = {i["warehouse"]: i for i in items if i["sku"] == sku}
    assert set(by_wh) == {"Norte", "Sur"}, by_wh
    return by_wh


# ── 1. The headline: the two views must agree ──────────────────────────────

class TestPerWarehouseAgreesWithAggregate:
    def test_event_moves_both_views_the_same_way_and_rows_sum_to_the_aggregate(
        self, test_tenant,
    ):
        sku = _sku()
        session_id = _new_session(test_tenant)
        lead_time_days = 20
        # 10 units/day, 60/40 split, 30 + 20 = 50 units in stock total.
        _seed_two_warehouses(test_tenant, session_id, sku, 10.0,
                              lead_time_days=lead_time_days)

        # Baseline (no event): the aggregate is deterministic and already
        # pinned by test_event_impact_on_status.py's own math for this exact
        # shape (avg_daily=10, lt=20, stock=50 -> demand_lt=200, rec=150).
        agg_before = _agg_row(test_tenant, session_id, sku)
        assert agg_before["recommended_qty"] == 150.0
        wh_before = _wh_rows(test_tenant, session_id, sku)
        # No event anywhere yet: both warehouse rows must report no event.
        assert wh_before["Norte"]["events_applied"] == []
        assert wh_before["Sur"]["events_applied"] == []
        assert sum(r["recommended_qty"] for r in wh_before.values()) == pytest.approx(
            agg_before["recommended_qty"]
        )

        # Full-window event (x2.0 over 30 days, lead time is 20) -> the
        # blended multiplier equals the raw one exactly.
        today = date.today()
        ev = _create_event(test_tenant, today, today + timedelta(days=30), 2.0)

        agg_after = _agg_row(test_tenant, session_id, sku)
        # demand_lt = 10*2.0*20 = 400; recommended = 400-50 = 350.
        assert agg_after["recommended_qty"] == pytest.approx(350.0)
        assert agg_after["recommended_qty"] > agg_before["recommended_qty"]
        assert agg_after["calc_explanation"]["events_applied"], (
            "aggregate: the number moved silently, no event named"
        )

        wh_after = _wh_rows(test_tenant, session_id, sku)
        norte, sur = wh_after["Norte"], wh_after["Sur"]
        # avg_daily_eff: Norte = 10*0.6*2.0 = 12; Sur = 10*0.4*2.0 = 8.
        # reorder_point (no safety term, see module docstring):
        #   Norte = 12*20 = 240; Sur = 8*20 = 160.
        # recommended = reorder_point - current_stock:
        #   Norte = 240-30 = 210; Sur = 160-20 = 140.
        assert norte["reorder_point"] == pytest.approx(240.0)
        assert sur["reorder_point"] == pytest.approx(160.0)
        assert norte["recommended_qty"] == pytest.approx(210.0)
        assert sur["recommended_qty"] == pytest.approx(140.0)

        # The two per-warehouse recommendations sum EXACTLY to the aggregate
        # one: both warehouses resolved the same lead time (20 days, neither
        # has a supplier), so the event multiplier is identical on both rows
        # and factors out of the sum — see the comment in
        # get_inventory_status_by_warehouse for when that stops being exact.
        assert norte["recommended_qty"] + sur["recommended_qty"] == pytest.approx(
            agg_after["recommended_qty"]
        )
        assert norte["reorder_point"] + sur["reorder_point"] == pytest.approx(
            agg_after["reorder_point"]
        )

        # Both views must have moved: a per-warehouse tab that still shows
        # the pre-event quantity is exactly the defect this file guards
        # against.
        assert norte["recommended_qty"] > 210.0 - 1e-9  # sanity: computed, not accidental
        for row in (norte, sur):
            assert row["events_applied"], "per-warehouse row: event not named"
            applied = row["events_applied"][0]
            assert applied["event_id"] == ev["id"]
            assert applied["multiplier"] == pytest.approx(2.0)
            assert applied["blended_multiplier"] == pytest.approx(2.0)


# ── 2. A per-SKU override resolves the same way in both views ──────────────

class TestOverrideResolutionAgreesAcrossViews:
    def test_sku_override_wins_on_both_the_aggregate_and_every_warehouse_row(
        self, test_tenant,
    ):
        sku = _sku()
        session_id = _new_session(test_tenant)
        lead_time_days = 20
        _seed_two_warehouses(test_tenant, session_id, sku, 10.0,
                              lead_time_days=lead_time_days)

        today = date.today()
        ev = _create_event(test_tenant, today, today + timedelta(days=30), 1.8,
                            name="Black Friday")
        # The SKU's own override beats the event's blanket x1.8.
        inv_svc.set_event_multiplier(test_tenant["id"], ev["id"], "sku", sku, 3.0)

        agg = _agg_row(test_tenant, session_id, sku)
        agg_applied = agg["calc_explanation"]["events_applied"][0]
        assert agg_applied["multiplier"] == pytest.approx(3.0)
        assert agg_applied["multiplier_source"] == "sku"

        wh = _wh_rows(test_tenant, session_id, sku)
        for name, row in wh.items():
            applied = row["events_applied"][0]
            assert applied["multiplier"] == pytest.approx(3.0), name
            assert applied["multiplier_source"] == "sku", name

        # And the resolved multiplier is consistent enough that the two
        # views still sum to each other under the override too.
        assert wh["Norte"]["recommended_qty"] + wh["Sur"]["recommended_qty"] == pytest.approx(
            agg["recommended_qty"]
        )


# ── 3. Invisible when nothing is declared ───────────────────────────────────

class TestNoEventIsInvisible:
    def test_no_declared_event_leaves_the_per_warehouse_numbers_exactly_as_before(
        self, test_tenant,
    ):
        sku = _sku()
        session_id = _new_session(test_tenant)
        lead_time_days = 20
        _seed_two_warehouses(test_tenant, session_id, sku, 10.0,
                              lead_time_days=lead_time_days)

        wh = _wh_rows(test_tenant, session_id, sku)
        norte, sur = wh["Norte"], wh["Sur"]

        # Plain share-split math, event_multiplier == 1.0 throughout:
        # Norte: avg_daily=6, reorder=6*20=120, recommended=120-30=90.
        # Sur:   avg_daily=4, reorder=4*20=80,  recommended=80-20=60.
        assert norte["daily_demand"] == pytest.approx(6.0)
        assert sur["daily_demand"] == pytest.approx(4.0)
        assert norte["reorder_point"] == pytest.approx(120.0)
        assert sur["reorder_point"] == pytest.approx(80.0)
        assert norte["recommended_qty"] == pytest.approx(90.0)
        assert sur["recommended_qty"] == pytest.approx(60.0)
        assert norte["signal"] == "PEDIR_YA"
        assert sur["signal"] == "PEDIR_YA"

        # The new field is present and empty, never missing — a caller that
        # reads it unconditionally must not KeyError just because nothing was
        # declared.
        assert norte["events_applied"] == []
        assert sur["events_applied"] == []


# ── 4. The per-warehouse explanation names the event ────────────────────────

class TestPerWarehouseExplanationNamesTheEvent:
    def test_events_applied_identifies_event_id_name_and_resolved_multiplier(
        self, test_tenant,
    ):
        sku = _sku()
        session_id = _new_session(test_tenant)
        _seed_two_warehouses(test_tenant, session_id, sku, 10.0, lead_time_days=20)

        today = date.today()
        ev = _create_event(test_tenant, today, today + timedelta(days=3), 1.8,
                            name="Aguinaldo")

        wh = _wh_rows(test_tenant, session_id, sku)
        for name, row in wh.items():
            applied = row["events_applied"]
            assert len(applied) == 1, name
            entry = applied[0]
            assert entry["event_id"] == ev["id"]
            assert entry["event_name"] == "Aguinaldo"
            assert entry["multiplier"] == pytest.approx(1.8)
            assert entry["multiplier_source"] == "event"
            assert entry["overlap_days"] == 4  # today .. today+3 inclusive
            assert entry["window_days"] == 20
