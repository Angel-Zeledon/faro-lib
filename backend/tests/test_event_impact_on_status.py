"""
A declared event reaching the standing recommendation (docs/stability.md 19.5).

Before this, `inventory_events` / `inventory_event_multipliers` only fed the
event SIMULATOR (`simulate_event_impact`): a tenant could declare "Semana
Santa, x1.8, 24th to 31st", save it, and `_compute_inventory_status` asked for
the same quantity the next day regardless — the multiplier never reached the
reorder point or the recommended quantity.

These tests exercise the real function against a real Postgres row (no
mocking of `get_inventory_status`/`_resolve_multiplier`/`_index_overrides` —
those already have pure-Python coverage in test_event_multipliers.py) and pin,
on the actual numbers:

  * a SKU inside a saved event's window gets a LARGER recommendation than the
    same SKU with no event at all;
  * partial overlap blends the multiplier by how much of the lead-time window
    the event actually covers, instead of scaling the whole window;
  * a per-SKU override beats the event's own multiplier, and a category
    override never leaks onto a SKU in a different category;
  * a past event and a future event past the lead-time window change nothing;
  * a daily-period tenant and a weekly-period tenant describing the SAME
    underlying demand and the SAME calendar event reach the SAME total
    lead-time demand and recommended quantity — the grain trap
    `lead_time_demand`'s own comment documents (a per-period figure
    multiplied by a raw calendar-day count is off by 7x/30x) must not
    reappear for events;
  * the explanation names the event, not just a changed number.

Every forecast point below carries no `upper`/`q90` key, so `_point_sigma`
(and with it `avg_std`, and the whole classical safety-stock term) is exactly
0 — the tests isolate the event multiplier's effect on the DEMAND term
without a variance term muddying the hand-computed expectations. No supplier
name is set either, so the lead-time-variance term is 0 too (no learned or
configured lead_time_std to resolve).
"""
from datetime import date, timedelta
from uuid import uuid4

import pytest

from backend.db import session_store
from backend.inventory import service as inv_svc
from backend.sessions.service import create_session


def _sku() -> str:
    return f"SKU-{uuid4().hex[:8].upper()}"


def _flat_forecast(period_demand: float, n_points: int = 40, step_days: int = 1) -> dict:
    """A flat forecast of `period_demand` units per bucket, `n_points` buckets
    long, spaced `step_days` apart (1 for a daily-grain session, 7 for a
    weekly-grain one). No `upper`/`q90` — see module docstring."""
    start = date.today()
    pts = [
        {
            "date": (start + timedelta(days=i * step_days)).isoformat(),
            "value": period_demand,
        }
        for i in range(n_points)
    ]
    return {"lightgbm": {"forecast": pts}}


def _new_session(test_tenant) -> str:
    return create_session(
        test_tenant["id"], "usr_test", f"event-status-{uuid4().hex[:6]}",
    )["id"]


def _put_sku(
    test_tenant, session_id, sku, period_demand, *,
    lead_time_days=20, current_stock=50.0, moq=1.0,
    category=None, family=None, period="daily",
):
    """Stock + forecast for one SKU, then the computed status row for it."""
    tid = test_tenant["id"]
    inv_svc.upsert_stock(tid, sku, {
        "current_stock": current_stock, "lead_time_days": lead_time_days, "moq": moq,
        "category": category, "family": family,
    })
    step_days = 7 if period == "weekly" else (30 if period == "monthly" else 1)
    session_store.set_forecasts(tid, session_id, {
        **(session_store.get_forecasts(tid, session_id) or {}),
        sku: _flat_forecast(period_demand, step_days=step_days),
    })


def _status_of(test_tenant, session_id, sku, period="daily") -> dict:
    items = inv_svc.get_inventory_status(test_tenant["id"], session_id, period=period)
    by_sku = {i["sku"]: i for i in items}
    assert sku in by_sku, f"{sku} missing from status: {sorted(by_sku)}"
    return by_sku[sku]


def _create_event(test_tenant, start: date, end: date, multiplier: float, name="Semana Santa"):
    return inv_svc.create_event(test_tenant["id"], {
        "name": name, "start_date": start, "end_date": end, "multiplier": multiplier,
    })


# ── 1. A saved event changes the decision, not just the simulator ──────────────

class TestEventChangesTheStandingRecommendation:
    def test_sku_inside_event_window_orders_more_than_the_same_sku_with_no_event(
        self, test_tenant,
    ):
        sku = _sku()
        session_id = _new_session(test_tenant)
        # lead_time=20 days, flat demand=10/day, stock=50 -> full-window
        # demand_lt=200, recommended=150 with no event at all.
        _put_sku(test_tenant, session_id, sku, 10.0, lead_time_days=20, current_stock=50.0)

        before = _status_of(test_tenant, session_id, sku)
        assert before["recommended_qty"] == 150.0
        assert before["calc_explanation"]["events_applied"] == []

        # An event covering the WHOLE lead-time window: overlap = window, so
        # the blended multiplier equals the raw one (2.0) exactly.
        today = date.today()
        _create_event(test_tenant, today, today + timedelta(days=30), 2.0)

        after = _status_of(test_tenant, session_id, sku)
        # demand_lt = 10*2.0*20 = 400; recommended = 400-50 = 350.
        assert after["recommended_qty"] == pytest.approx(350.0)
        assert after["recommended_qty"] > before["recommended_qty"], (
            "a declared event that overlaps the decision window must make "
            "StockAI ask for MORE, not the same quantity as a day with no event"
        )
        assert after["calc_explanation"]["events_applied"], (
            "the number moved silently: no event was named in the explanation"
        )

    def test_past_event_and_far_future_event_change_nothing(self, test_tenant):
        sku = _sku()
        session_id = _new_session(test_tenant)
        _put_sku(test_tenant, session_id, sku, 10.0, lead_time_days=20, current_stock=50.0)
        baseline = _status_of(test_tenant, session_id, sku)
        assert baseline["recommended_qty"] == 150.0

        today = date.today()
        # Fully in the past: end_date < today.
        _create_event(test_tenant, today - timedelta(days=30), today - timedelta(days=10), 5.0,
                       name="Old promo")
        # Starts after the 20-day decision window has already closed.
        _create_event(test_tenant, today + timedelta(days=25), today + timedelta(days=30), 5.0,
                       name="Far future promo")

        after = _status_of(test_tenant, session_id, sku)
        assert after["recommended_qty"] == baseline["recommended_qty"] == 150.0
        assert after["calc_explanation"]["lead_time_demand"] == baseline["calc_explanation"]["lead_time_demand"]
        assert after["calc_explanation"]["events_applied"] == [], (
            "a past or too-far-future event must not even be named — it did "
            "not move anything"
        )


# ── 2. Partial overlap: the blended answer, not the full multiplier ───────────

class TestPartialOverlap:
    def test_partial_overlap_blends_by_the_fraction_of_the_window_covered(
        self, test_tenant,
    ):
        sku = _sku()
        session_id = _new_session(test_tenant)
        lead_time_days = 20
        _put_sku(test_tenant, session_id, sku, 10.0, lead_time_days=lead_time_days,
                  current_stock=50.0)

        # Decision window is [today, today+20). The event covers only 8 of
        # those 20 calendar days (today+5 .. today+12 inclusive).
        today = date.today()
        ev = _create_event(
            test_tenant, today + timedelta(days=5), today + timedelta(days=12), 1.8,
            name="Semana Santa",
        )

        item = _status_of(test_tenant, session_id, sku)
        exp = item["calc_explanation"]
        applied = exp["events_applied"]
        assert len(applied) == 1
        row = applied[0]
        assert row["event_name"] == "Semana Santa"
        assert row["overlap_days"] == 8
        assert row["window_days"] == 20
        assert row["multiplier"] == pytest.approx(1.8)
        # blended = 1 + (8/20)*(1.8-1) = 1.32 — NOT 1.8.
        expected_blended = 1.0 + (8 / 20) * (1.8 - 1.0)
        assert row["blended_multiplier"] == pytest.approx(expected_blended)
        assert expected_blended < 1.8, "sanity: the blended factor is below the raw one"

        # demand_lt = 10 * 1.32 * 20 = 264; recommended = 264 - 50 = 214.
        expected_demand_lt = 10.0 * expected_blended * lead_time_days
        assert exp["lead_time_demand"] == pytest.approx(expected_demand_lt)
        assert item["recommended_qty"] == pytest.approx(expected_demand_lt - 50.0)

        # The answer a FULL (unblended) multiplier would have produced, so a
        # regression that drops the blending is caught even if it still
        # "moves" the number in the right direction.
        full_multiplier_recommended = 10.0 * 1.8 * lead_time_days - 50.0
        assert item["recommended_qty"] < full_multiplier_recommended


# ── 3. Override resolution reaches the standing recommendation too ────────────

class TestOverrideResolutionOnTheStandingRecommendation:
    def test_sku_override_beats_event_and_category_override_does_not_leak(
        self, test_tenant,
    ):
        sku_override = _sku()
        sku_category = _sku()
        sku_plain = _sku()
        session_id = _new_session(test_tenant)
        lead_time_days = 20

        for sku, category in (
            (sku_override, None),
            (sku_category, "electronica"),
            (sku_plain, "lacteos"),
        ):
            _put_sku(test_tenant, session_id, sku, 10.0, lead_time_days=lead_time_days,
                      current_stock=50.0, category=category)

        # Full-window event so the blended factor equals the raw multiplier —
        # isolates override resolution from the partial-overlap math above.
        today = date.today()
        ev = _create_event(test_tenant, today, today + timedelta(days=30), 1.8,
                            name="Black Friday")
        inv_svc.set_event_multiplier(test_tenant["id"], ev["id"], "sku", sku_override, 3.0)
        inv_svc.set_event_multiplier(test_tenant["id"], ev["id"], "category", "Electronica", 4.0)

        item_override = _status_of(test_tenant, session_id, sku_override)
        item_category = _status_of(test_tenant, session_id, sku_category)
        item_plain = _status_of(test_tenant, session_id, sku_plain)

        # sku_override: x3.0 (its own override), not the event's x1.8.
        applied = item_override["calc_explanation"]["events_applied"][0]
        assert applied["multiplier"] == pytest.approx(3.0)
        assert applied["multiplier_source"] == "sku"
        assert item_override["recommended_qty"] == pytest.approx(10.0 * 3.0 * lead_time_days - 50.0)

        # sku_category (category=electronica): the electronica override, x4.0.
        applied = item_category["calc_explanation"]["events_applied"][0]
        assert applied["multiplier"] == pytest.approx(4.0)
        assert applied["multiplier_source"] == "category"
        assert item_category["recommended_qty"] == pytest.approx(10.0 * 4.0 * lead_time_days - 50.0)

        # sku_plain (category=lacteos): the electronica override must NOT
        # apply here — falls back to the event's own x1.8.
        applied = item_plain["calc_explanation"]["events_applied"][0]
        assert applied["multiplier"] == pytest.approx(1.8)
        assert applied["multiplier_source"] == "event"
        assert item_plain["recommended_qty"] == pytest.approx(10.0 * 1.8 * lead_time_days - 50.0)


# ── 4. The tenant's grain: a weekly and a daily tenant must agree ─────────────

class TestGrainConsistency:
    def test_weekly_and_daily_tenant_reach_the_same_total_lead_time_demand(
        self, test_tenant,
    ):
        """
        Same underlying business — 10 units/day of demand, a 20-day supplier
        lead time, 50 units in stock, and one x1.8 event covering 8 of the 20
        decision-window days — described two ways: a daily-grain session
        (10 units/day) and a weekly-grain session (70 units/week, the same
        rate). Both must land on the SAME total lead-time demand and the SAME
        recommended quantity: the event's calendar days must be converted
        into a FRACTION of the (calendar-day) lead time, not multiplied
        directly against a per-period figure — the exact trap
        `lead_time_demand`'s own comment in the source documents ("a weekly
        tenant's per-week demand was multiplied by the event's CALENDAR
        days").
        """
        lead_time_days = 20
        today = date.today()
        _create_event(test_tenant, today + timedelta(days=5), today + timedelta(days=12), 1.8,
                       name="Semana Santa")

        sku_daily = _sku()
        session_daily = _new_session(test_tenant)
        _put_sku(test_tenant, session_daily, sku_daily, 10.0,
                  lead_time_days=lead_time_days, current_stock=50.0, period="daily")
        item_daily = _status_of(test_tenant, session_daily, sku_daily, period="daily")

        sku_weekly = _sku()
        session_weekly = _new_session(test_tenant)
        _put_sku(test_tenant, session_weekly, sku_weekly, 70.0,
                  lead_time_days=lead_time_days, current_stock=50.0, period="weekly")
        item_weekly = _status_of(test_tenant, session_weekly, sku_weekly, period="weekly")

        assert item_daily["calc_explanation"]["lead_time_demand"] == pytest.approx(
            item_weekly["calc_explanation"]["lead_time_demand"], rel=1e-6,
        )
        assert item_daily["recommended_qty"] == pytest.approx(
            item_weekly["recommended_qty"], rel=1e-6,
        )
        # And both must actually be the blended (not the raw x1.8) answer.
        expected_blended = 1.0 + (8 / 20) * (1.8 - 1.0)
        expected_demand_lt = 10.0 * expected_blended * lead_time_days
        assert item_daily["calc_explanation"]["lead_time_demand"] == pytest.approx(expected_demand_lt)
        assert item_weekly["calc_explanation"]["lead_time_demand"] == pytest.approx(expected_demand_lt)

        for item in (item_daily, item_weekly):
            row = item["calc_explanation"]["events_applied"][0]
            # overlap/window are calendar DAYS regardless of the tenant's
            # planning grain — the whole point of the fix.
            assert row["overlap_days"] == 8
            assert row["window_days"] == 20


# ── 5. The explanation names the event ─────────────────────────────────────────

class TestExplanationNamesTheEvent:
    def test_explanation_identifies_event_id_name_and_the_resolved_multiplier(
        self, test_tenant,
    ):
        sku = _sku()
        session_id = _new_session(test_tenant)
        _put_sku(test_tenant, session_id, sku, 10.0, lead_time_days=20, current_stock=50.0)

        today = date.today()
        ev = _create_event(test_tenant, today, today + timedelta(days=3), 1.8,
                            name="Aguinaldo")

        item = _status_of(test_tenant, session_id, sku)
        applied = item["calc_explanation"]["events_applied"]
        assert len(applied) == 1
        row = applied[0]
        assert row["event_id"] == ev["id"]
        assert row["event_name"] == "Aguinaldo"
        assert row["multiplier"] == pytest.approx(1.8)
        assert row["multiplier_source"] == "event"
        assert row["overlap_days"] == 4  # today .. today+3 inclusive
        assert row["window_days"] == 20


# ── 6. Both views answer the same question the same way ──────────────────────

class TestTheTwoViewsAgreeUnderAnEvent:
    """A declared event must move the per-warehouse view too.

    `/inventario` serves both the aggregated rows and the per-warehouse ones,
    one click apart. When only the aggregate knew about events, the same
    product during the same Semana Santa reported one quantity on one tab and a
    different one on the other — the two-surfaces-one-question defect this
    repository spends most of `docs/stability.md` on, made worse by the two
    numbers being visible together.
    """

    def _two_warehouses(self, test_tenant, sku, *, per_day=10.0, lead_time=20):
        tid = test_tenant["id"]
        session_id = _new_session(test_tenant)
        for name, stock in (("Norte", 60.0), ("Sur", 40.0)):
            inv_svc.upsert_stock(tid, sku, {
                "current_stock": stock, "lead_time_days": lead_time,
                "moq": 1.0, "warehouse": name,
            })
        session_store.set_forecasts(tid, session_id, {
            sku: _flat_forecast(per_day),
        })
        return session_id

    def _warehouse_rows(self, test_tenant, session_id, sku):
        """The warehouse rows that actually carry demand.

        Demand is attributed to one warehouse rather than split across all of
        them, so the others come back SIN_DATOS with a null reorder point.
        Those rows have no decision in them to check.
        """
        rows = inv_svc.get_inventory_status_by_warehouse(test_tenant["id"], session_id)
        return [r for r in rows
                if r["sku"] == sku and r.get("reorder_point") is not None]

    def test_the_event_reaches_the_per_warehouse_rows_at_all(self, test_tenant):
        """The regression itself: without this the per-warehouse view ignored
        every declared event."""
        sku = _sku()
        session_id = self._two_warehouses(test_tenant, sku)
        before = self._warehouse_rows(test_tenant, session_id, sku)
        assert before, "fixture produced no per-warehouse rows"
        before_rop = sum(r["reorder_point"] for r in before)

        today = date.today()
        _create_event(test_tenant, today, today + timedelta(days=19), 2.0)

        after = self._warehouse_rows(test_tenant, session_id, sku)
        after_rop = sum(r["reorder_point"] for r in after)
        assert after_rop > before_rop, (
            f"a x2.0 event covering the whole window left the per-warehouse "
            f"reorder point at {after_rop} (was {before_rop})"
        )

    def test_both_views_name_the_same_event_with_the_same_blend(self, test_tenant):
        """One product, one event, one answer — in both views.

        The two rows are not required to carry identical quantities: the
        per-warehouse view splits a whole-SKU cushion across warehouses, so its
        numbers are shares. What must not differ is the EVENT resolution — the
        same event, the same multiplier and the same fraction of the decision
        window — because that is the part a buyer reads as "why is this bigger
        this week", and two different answers to it is the defect.
        """
        sku = _sku()
        session_id = self._two_warehouses(test_tenant, sku)
        today = date.today()
        _create_event(test_tenant, today, today + timedelta(days=9), 1.8)

        aggregate = _status_of(test_tenant, session_id, sku)
        agg_events = (aggregate.get("calc_explanation") or {}).get("events_applied") or []
        assert agg_events, "the aggregate did not apply the event at all"
        agg = agg_events[0]

        rows = self._warehouse_rows(test_tenant, session_id, sku)
        assert rows, "fixture produced no per-warehouse rows"
        for row in rows:
            applied = row.get("events_applied") or []
            assert applied, f"warehouse {row['warehouse']} ignored the event"
            wh = applied[0]
            for field in ("event_id", "multiplier", "overlap_days",
                              "window_days", "blended_multiplier"):
                expected, got = agg[field], wh[field]
                same = (got == pytest.approx(expected)
                        if isinstance(expected, float) else got == expected)
                assert same, (
                    f"warehouse {row['warehouse']} resolved {field}={got} "
                    f"against the aggregate's {expected}"
                )

    def test_no_event_leaves_the_per_warehouse_view_exactly_as_it_was(self, test_tenant):
        """This change must be invisible to a tenant that declares nothing."""
        sku = _sku()
        session_id = self._two_warehouses(test_tenant, sku)
        rows = self._warehouse_rows(test_tenant, session_id, sku)
        for row in rows:
            assert row.get("calc_explanation", {}).get("events_applied") in (None, []), (
                "a tenant with no declared event got an events_applied entry"
            )
