"""
Order cadence per supplier — the review period (docs/stability.md 17 item 1,
19 item 3).

Without a review period the product sized the order-up-to level on the
reorder point itself: `qty = avg_daily*L + safety_stock - stock - incoming`.
The moment a shipment landed, the inventory position was back at the reorder
point and the next look re-fired — the product recommends a top-up every
day, and only the buyer's own batching (one order per supplier per week, by
hand) kept that from showing up as forty purchase orders. A buyer places one
order to one supplier on a cadence; the quantity has to cover demand until
the NEXT order arrives, not just until this one does.

`suppliers.review_period_days` (migration `add_suppliers_review_period_days`,
additive, `NOT NULL DEFAULT 0`) is how many days this buyer actually waits
between orders to a given supplier. `backend/inventory/service.py` adds it to
the lead time to form the PROTECTION INTERVAL an order has to last through —
see `_calc_recommended`'s docstring for the formula. 0 (unset) means "no
declared cadence" and collapses the protection interval back to the lead
time alone, which is what pins every existing tenant's numbers exactly.

These tests cover, in order:
  1. review_period=0 reproduces today's arithmetic to the digit (pure
     functions AND the real `get_inventory_status`).
  2. A positive review period strictly increases the recommendation and
     matches the textbook formula exactly.
  3. The days -> periods conversion: the same supplier's cadence under a
     weekly planning grain must not be read as a daily figure — the exact
     bug class `_lead_time_in_periods`'s own docstring warns about.
  4. `_calc_signal` and the reorder point stay aligned once the reorder
     point carries the review period.
  5. A SKU with no supplier is unaffected, even when other suppliers in the
     same tenant have a cadence configured.
  6. The supplier CRUD API exposes the field, with a viewer/analyst
     permission pair on the PATCH endpoint.
"""

import math
from datetime import date, timedelta
from uuid import uuid4

import pytest

from backend.db import session_store
from backend.db.connection import query_one
from backend.inventory import service as inv_svc
from backend.inventory import supplier_service as sup_svc
from backend.inventory.service import (
    _calc_recommended,
    _calc_signal,
    _lead_time_in_periods,
    _resolve_review_period_days,
    _safety_stock,
    build_explanation,
)
from backend.sessions.service import create_session


# ─────────────────────────────────────────────────────────────────────────────
# 1 & 2 — _calc_recommended: review_period=0 is the identity; review_period>0
# matches the textbook protection-interval formula exactly.
# ─────────────────────────────────────────────────────────────────────────────

class TestCalcRecommendedReviewPeriod:

    def test_default_review_period_is_zero_and_changes_nothing(self):
        """No `review_period` argument at all — every pre-existing caller in
        this codebase and every test written before this feature — must give
        the exact number it always did."""
        legacy = _calc_recommended(50, 10.0, 1.0, 14, 10, 0.95)
        explicit_zero = _calc_recommended(50, 10.0, 1.0, 14, 10, 0.95, review_period=0.0)
        assert legacy == explicit_zero == 97.0  # pinned in test_calculation_audit.py too

    def test_review_period_zero_reproduces_today_exactly_across_scenarios(self):
        """Pins the identity across several realistic (demand, sigma, lead
        time, stock, moq, service level) combinations, not just one — this is
        the test that protects every existing tenant, who has set no cadence
        on any supplier."""
        scenarios = [
            (200.0, 10.0, 15.0, 15, 1, 0.95),
            (0.0, 52.0, 0.0, 10, 500, 0.95),
            (95.0, 10.0, 0.0, 10, 100, 0.95),
            (10_000.0, 10.0, 1.0, 14, 1, 0.95),
        ]
        for stock, demand, sigma, lead, moq, sl in scenarios:
            without_param = _calc_recommended(stock, demand, sigma, lead, moq, sl)
            with_zero = _calc_recommended(stock, demand, sigma, lead, moq, sl, review_period=0.0)
            assert without_param == with_zero, (stock, demand, sigma, lead, moq, sl)

    def test_seven_day_review_on_fifteen_day_lead_is_strictly_larger(self):
        """A realistic supplier: 15-day lead time, ordered from roughly every
        7 days. The quantity has to last the whole protection interval, so it
        must be strictly larger than sizing on the lead time alone."""
        stock, demand, sigma, lead, moq, sl = 200.0, 10.0, 15.0, 15, 1, 0.95
        r_no_review = _calc_recommended(stock, demand, sigma, lead, moq, sl)
        r_with_review = _calc_recommended(stock, demand, sigma, lead, moq, sl, review_period=7)
        assert r_with_review > r_no_review, (
            f"a 7-day review period must order MORE than no cadence at all: "
            f"{r_with_review} vs {r_no_review}"
        )

    def test_review_period_matches_the_protection_interval_formula_exactly(self):
        """qty = avg_daily*(L+R) + safety_stock(L+R) - stock - incoming, hand
        computed against `_safety_stock` directly (not re-derived here) so
        this test cannot drift from the production formula by copying it."""
        stock, demand, sigma, lead, moq, sl, incoming = 50.0, 10.0, 2.0, 15, 1, 0.95, 5.0
        review = 7
        protection_interval = lead + review

        expected_safety = _safety_stock(sigma, protection_interval, sl)
        expected_raw = max(
            0.0, demand * protection_interval + expected_safety - stock - incoming
        )
        expected = float(math.ceil(expected_raw)) if expected_raw > 0 else 0.0

        result = _calc_recommended(
            stock, demand, sigma, lead, moq, sl, incoming=incoming, review_period=review,
        )
        assert result == pytest.approx(expected)

    def test_review_period_also_grows_the_safety_stock_not_only_the_mean_demand(self):
        """The cushion has to cover the LONGER wait too, not just the bigger
        mean — that is why `_calc_recommended` feeds `lead+review` into
        `_safety_stock`'s own `lead_time` argument rather than only scaling
        the demand term."""
        stock, demand, sigma, lead, moq, sl = 0.0, 10.0, 5.0, 15, 1, 0.95
        no_review = _calc_recommended(stock, demand, sigma, lead, moq, sl)
        with_review = _calc_recommended(stock, demand, sigma, lead, moq, sl, review_period=7)

        demand_growth = demand * (lead + 7) - demand * lead  # the demand term alone
        actual_growth = with_review - no_review
        assert actual_growth > demand_growth, (
            "the whole growth in the recommendation was explained by the demand "
            "term alone — the safety-stock term did not grow with the longer "
            "protection interval"
        )

    def test_negative_review_period_is_clamped_not_subtracted(self):
        """Defensive: a caller must never be able to SHRINK the protection
        interval below the lead time by passing a negative review period."""
        stock, demand, sigma, lead, moq, sl = 50.0, 10.0, 1.0, 14, 1, 0.95
        baseline = _calc_recommended(stock, demand, sigma, lead, moq, sl)
        negative = _calc_recommended(stock, demand, sigma, lead, moq, sl, review_period=-100)
        assert negative == baseline


# ─────────────────────────────────────────────────────────────────────────────
# 3 — Days -> periods: the supplier's cadence is stored in DAYS; the planning
# grain can be weekly or monthly, and the conversion must go through
# `_lead_time_in_periods` exactly like the lead time itself does.
# ─────────────────────────────────────────────────────────────────────────────

class TestReviewPeriodDaysToPeriods:

    def test_seven_days_is_one_whole_period_under_weekly_planning(self):
        assert _lead_time_in_periods(7, "weekly") == 1.0
        assert _lead_time_in_periods(7, "daily") == 7.0

    def test_weekly_grain_does_not_read_the_daily_review_period(self):
        """The exact bug class this file's history warns about: feeding a
        DAY count where a PERIOD count belongs inflates the protection
        interval by the days-per-period factor (7x here). A supplier ordered
        weekly (review_period_days=7) must add ONE period under weekly
        planning, not seven."""
        review_days = 7
        periods_weekly = _lead_time_in_periods(review_days, "weekly")
        periods_daily = _lead_time_in_periods(review_days, "daily")
        assert periods_weekly != periods_daily
        assert periods_weekly == pytest.approx(1.0)

    def test_daily_and_weekly_sessions_describing_the_same_demand_and_cadence_agree(self):
        """Two tenants, one on a daily grain and one on a weekly grain,
        describing the SAME real demand (10/day == 70/week) and the SAME
        real supplier cadence (14-day lead, weekly ordering) must reach the
        SAME recommendation — not one inflated by the days-per-period
        factor. Mirrors the day/week parity test in
        test_event_impact_on_status.py, extended to the review period."""
        stock, sigma, moq, sl = 50.0, 0.0, 1.0, 0.95

        # Daily grain: 10 units/day, 14-day lead, 7-day (1 week) cadence.
        lead_days, review_days = 14, 7
        daily_result = _calc_recommended(
            stock, 10.0, sigma,
            _lead_time_in_periods(lead_days, "daily"), moq, sl,
            review_period=_lead_time_in_periods(review_days, "daily"),
        )

        # Weekly grain: the SAME demand expressed per week (70/week), the
        # SAME lead time and cadence converted into whole/fractional weeks.
        weekly_result = _calc_recommended(
            stock, 70.0, sigma,
            _lead_time_in_periods(lead_days, "weekly"), moq, sl,
            review_period=_lead_time_in_periods(review_days, "weekly"),
        )

        assert daily_result == pytest.approx(weekly_result), (
            daily_result, weekly_result,
        )
        # And both must differ from the buggy reading that feeds raw DAYS
        # into the weekly formula (review "7" treated as 7 whole weeks).
        buggy_weekly = _calc_recommended(
            stock, 70.0, sigma, _lead_time_in_periods(lead_days, "weekly"), moq, sl,
            review_period=float(review_days),  # the bug: days, not periods
        )
        assert buggy_weekly != pytest.approx(weekly_result)


# ─────────────────────────────────────────────────────────────────────────────
# 4 — The signal and the reorder point stay consistent with each other, even
# once the reorder point carries the review period (stability.md 17c).
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalStaysAlignedWithReviewPeriod:

    @staticmethod
    def _rop_days(avg_daily, avg_std, lead_time, review_period, service_level=0.95):
        """The reorder point in days, built the same way the real call sites
        in service.py build it: demand + safety stock over the PROTECTION
        INTERVAL, divided by the daily rate."""
        protection_interval = lead_time + review_period
        safety = _safety_stock(avg_std, protection_interval, service_level)
        return (avg_daily * protection_interval + safety) / avg_daily

    def test_reorder_point_grows_with_the_review_period(self):
        lead, avg_daily, avg_std = 15, 10.0, 1.0
        rop_no_review = self._rop_days(avg_daily, avg_std, lead, review_period=0)
        rop_with_review = self._rop_days(avg_daily, avg_std, lead, review_period=7)
        assert rop_with_review > rop_no_review

    def test_boundary_sits_exactly_at_the_grown_reorder_point(self):
        """Whatever the reorder point becomes, `_calc_signal`'s PEDIR_PRONTO
        <-> OK boundary must sit exactly there — it takes reorder_point_days
        as an argument and never recomputes it, so this pins that the two
        call sites in service.py (the reorder point itself, and the signal
        judged against it) cannot drift apart."""
        lead, avg_daily, avg_std = 15, 10.0, 1.0
        rop_days = self._rop_days(avg_daily, avg_std, lead, review_period=7)

        assert _calc_signal(rop_days - 0.01, lead, rop_days) == "PEDIR_PRONTO"
        assert _calc_signal(rop_days, lead, rop_days) == "PEDIR_PRONTO"
        assert _calc_signal(rop_days + 0.01, lead, rop_days) == "OK"

    def test_pedir_ya_still_means_half_a_lead_time_regardless_of_review_period(self):
        """PEDIR_YA's own boundary (`_calc_signal`'s `lead_time` argument) is
        deliberately fed the PLAIN lead time, not the protection interval —
        a buyer's order cadence must not change what "already in trouble"
        means. The invariant from stability.md 17c
        (reorder_point_days >= lead_time > 0.5*lead_time) only strengthens
        once the reorder point also carries a review period, so PEDIR_YA
        stays strictly inside "at or below the reorder point"."""
        lead, avg_daily, avg_std = 15, 10.0, 1.0
        rop_days = self._rop_days(avg_daily, avg_std, lead, review_period=30)  # large cadence

        assert _calc_signal(7, lead, rop_days) == "PEDIR_YA"          # 7 < 15*0.5
        assert _calc_signal(7.6, lead, rop_days) == "PEDIR_PRONTO"    # >= 15*0.5, < rop
        assert rop_days > lead * 3, "the scenario should need the ROP*2 floor, not the flat 3L one"


# ─────────────────────────────────────────────────────────────────────────────
# 5 — _resolve_review_period_days: no supplier -> always 0, even when other
# suppliers in the map have a cadence configured.
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveReviewPeriodDays:

    def test_no_supplier_name_is_zero(self):
        assert _resolve_review_period_days(None, {"acme": 7.0}) == 0.0
        assert _resolve_review_period_days("", {"acme": 7.0}) == 0.0

    def test_unknown_supplier_is_zero(self):
        assert _resolve_review_period_days("Other Co", {"acme": 7.0}) == 0.0

    def test_known_supplier_is_case_insensitive(self):
        assert _resolve_review_period_days("ACME Imports", {"acme imports": 7.0}) == 7.0
        assert _resolve_review_period_days("  acme imports  ", {"acme imports": 7.0}) == 7.0

    def test_negative_configured_value_is_clamped(self):
        """Defensive: the DB CHECK (review_period_days >= 0) should make this
        unreachable, but the resolver does not trust that alone."""
        assert _resolve_review_period_days("acme", {"acme": -5.0}) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 6 — build_explanation: review_period=0 is byte-identical to before this
# feature; a positive one names the protection interval and switches to a
# new code so an older frontend build degrades to the English text instead
# of rendering nothing (the same graceful-degradation contract the function
# already documents for an unknown code).
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildExplanationReviewPeriod:

    def _explain(self, review_period_days=0.0):
        return build_explanation(
            current_stock=100.0, daily_demand=10.0, coverage_days=10.0,
            lead_time=15, lead_time_source="user", reorder_point=200.0,
            signal="PEDIR_PRONTO", review_period_days=review_period_days,
        )

    def test_zero_review_period_keeps_the_old_code_and_text(self):
        zero = self._explain(0.0)
        legacy = build_explanation(
            current_stock=100.0, daily_demand=10.0, coverage_days=10.0,
            lead_time=15, lead_time_source="user", reorder_point=200.0,
            signal="PEDIR_PRONTO",
        )
        assert zero == legacy
        assert zero["code"] == "inventory_explain_reorder"
        assert "review_period_days" not in zero["params"]

    def test_positive_review_period_switches_code_and_names_the_interval(self):
        result = self._explain(7.0)
        assert result["code"] == "inventory_explain_reorder_review"
        assert result["params"]["review_period_days"] == 7.0
        assert result["params"]["protection_interval_days"] == 22.0
        assert "22" in result["text"] or "22 days" in result["text"]
        assert "next order" in result["text"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# DB-backed helpers, mirroring test_event_impact_on_status.py's own.
# ─────────────────────────────────────────────────────────────────────────────

def _sku() -> str:
    return f"SKU-{uuid4().hex[:8].upper()}"


def _flat_forecast(period_demand: float, n_points: int = 40, step_days: int = 1) -> dict:
    """A flat forecast with no `upper`/`q90` key, so `avg_std` (and the whole
    classical safety-stock term) is exactly 0 — isolating the review period's
    effect on the numbers from a variance term muddying hand-computed
    expectations, same discipline as test_event_impact_on_status.py."""
    start = date.today()
    pts = [
        {"date": (start + timedelta(days=i * step_days)).isoformat(), "value": period_demand}
        for i in range(n_points)
    ]
    return {"lightgbm": {"forecast": pts}}


def _new_session(test_tenant) -> str:
    return create_session(
        test_tenant["id"], "usr_test", f"review-period-{uuid4().hex[:6]}",
    )["id"]


def _status_of(test_tenant, session_id, sku, period="daily") -> dict:
    items = inv_svc.get_inventory_status(test_tenant["id"], session_id, period=period)
    by_sku = {i["sku"]: i for i in items}
    assert sku in by_sku, f"{sku} missing from status: {sorted(by_sku)}"
    return by_sku[sku]


# ─────────────────────────────────────────────────────────────────────────────
# 7 — supplier_service: the field persists, round-trips, and
# get_review_period_map reads it back the way _resolve_review_period_days
# expects (lower-cased name -> float).
# ─────────────────────────────────────────────────────────────────────────────

class TestSupplierServiceReviewPeriod:

    def test_create_without_the_field_defaults_to_zero(self, test_tenant):
        """Mirrors the column's own DEFAULT 0 — a supplier nobody has told a
        cadence to must not silently start with one."""
        name = f"Prov-{uuid4().hex[:6]}"
        row = sup_svc.create_supplier(test_tenant["id"], {"name": name})
        assert row["review_period_days"] == 0

        persisted = query_one(
            "SELECT review_period_days FROM suppliers WHERE id = %s", (row["id"],),
        )
        assert persisted["review_period_days"] == 0

    def test_create_and_update_persist_the_value(self, test_tenant):
        name = f"Prov-{uuid4().hex[:6]}"
        row = sup_svc.create_supplier(
            test_tenant["id"], {"name": name, "review_period_days": 7},
        )
        assert row["review_period_days"] == 7

        updated = sup_svc.update_supplier(
            test_tenant["id"], row["id"], {"review_period_days": 14},
        )
        assert updated["review_period_days"] == 14

        persisted = query_one(
            "SELECT review_period_days FROM suppliers WHERE id = %s", (row["id"],),
        )
        assert persisted["review_period_days"] == 14

    def test_get_review_period_map_is_lower_cased_and_active_only(self, test_tenant):
        live = sup_svc.create_supplier(
            test_tenant["id"], {"name": f"Live-{uuid4().hex[:6]}", "review_period_days": 10},
        )
        dropped = sup_svc.create_supplier(
            test_tenant["id"], {"name": f"Dropped-{uuid4().hex[:6]}", "review_period_days": 20},
        )
        sup_svc.delete_supplier(test_tenant["id"], dropped["id"])

        m = sup_svc.get_review_period_map(test_tenant["id"])
        assert m[live["name"].lower()] == 10.0
        assert dropped["name"].lower() not in m, "a deactivated supplier must not feed the map"


# ─────────────────────────────────────────────────────────────────────────────
# 8 — End to end through get_inventory_status: the real migration, the real
# resolver, the real formula, on a real Postgres row.
# ─────────────────────────────────────────────────────────────────────────────

class TestGetInventoryStatusReviewPeriod:

    def test_no_supplier_row_reproduces_todays_numbers_exactly(self, test_tenant):
        """A SKU whose supplier name matches no row in `suppliers` at all
        (the overwhelming majority of today's data) must be completely
        unaffected — `_resolve_review_period_days` falls through to 0."""
        sku = _sku()
        session_id = _new_session(test_tenant)
        inv_svc.upsert_stock(test_tenant["id"], sku, {
            "current_stock": 100.0, "lead_time_days": 15, "moq": 1.0,
            "supplier": "Unregistered Supplier Co",
        })
        session_store.set_forecasts(test_tenant["id"], session_id, {
            sku: _flat_forecast(10.0),
        })

        status = _status_of(test_tenant, session_id, sku)
        assert status["calc_explanation"]["review_period_days"] == 0
        # demand_lt = 10*15 = 150, safety_stock = 0 (no upper/q90) -> raw=50
        assert status["recommended_qty"] == pytest.approx(50.0)
        assert status["calc_explanation"]["lead_time_demand"] == pytest.approx(150.0)
        assert status["explanation_code"] == "inventory_explain_reorder"

    def test_other_suppliers_review_period_does_not_leak_onto_this_sku(self, test_tenant):
        """The map has entries (another supplier in the same tenant has a
        cadence configured); this SKU's own supplier does not, and must stay
        at review_period=0."""
        sku = _sku()
        session_id = _new_session(test_tenant)
        sup_svc.create_supplier(test_tenant["id"], {
            "name": f"OtherSupplier-{uuid4().hex[:6]}", "review_period_days": 30,
        })
        inv_svc.upsert_stock(test_tenant["id"], sku, {
            "current_stock": 100.0, "lead_time_days": 15, "moq": 1.0,
            "supplier": "A Totally Different Supplier",
        })
        session_store.set_forecasts(test_tenant["id"], session_id, {
            sku: _flat_forecast(10.0),
        })

        status = _status_of(test_tenant, session_id, sku)
        assert status["calc_explanation"]["review_period_days"] == 0
        assert status["recommended_qty"] == pytest.approx(50.0)

    def test_configuring_a_review_period_raises_the_recommendation(self, test_tenant):
        """Before: no supplier row, today's arithmetic. After: the same
        supplier gets a 7-day review period, and the SAME SKU (same session,
        same forecast, same stock) orders strictly more, matching the
        protection-interval formula exactly."""
        sku = _sku()
        session_id = _new_session(test_tenant)
        supplier_name = f"Prov-{uuid4().hex[:6]}"
        inv_svc.upsert_stock(test_tenant["id"], sku, {
            "current_stock": 100.0, "lead_time_days": 15, "moq": 1.0,
            "supplier": supplier_name,
        })
        session_store.set_forecasts(test_tenant["id"], session_id, {
            sku: _flat_forecast(10.0),
        })

        before = _status_of(test_tenant, session_id, sku)
        assert before["recommended_qty"] == pytest.approx(50.0)   # 10*15 - 100
        assert before["calc_explanation"]["review_period_days"] == 0
        assert before["explanation_code"] == "inventory_explain_reorder"

        # lead_time_std=0 and lead_time_days=15 (matching the stock row) so
        # only the review period moves the number — isolates its effect.
        sup_svc.create_supplier(test_tenant["id"], {
            "name": supplier_name, "lead_time_days": 15, "lead_time_std": 0,
            "review_period_days": 7,
        })

        after = _status_of(test_tenant, session_id, sku)
        # protection_interval = 22 -> demand_lt = 220, safety=0 -> raw = 120
        assert after["recommended_qty"] == pytest.approx(120.0)
        assert after["recommended_qty"] > before["recommended_qty"]
        assert after["calc_explanation"]["review_period_days"] == 7
        assert after["calc_explanation"]["protection_interval_days"] == pytest.approx(22.0)
        assert after["calc_explanation"]["lead_time_demand"] == pytest.approx(220.0)
        assert after["explanation_code"] == "inventory_explain_reorder_review"
        assert "review_period_days" in after["explanation_params"]

    def test_signal_and_reorder_point_move_together_end_to_end(self, test_tenant):
        """A SKU whose coverage sits between the no-review reorder point and
        the with-review one: PEDIR_PRONTO with the cadence configured, OK
        without it — the signal must follow the reorder point it is judged
        against, not lag behind it."""
        sku = _sku()
        session_id = _new_session(test_tenant)
        supplier_name = f"Prov-{uuid4().hex[:6]}"
        # avg_daily=10, lead=10 -> demand_lt(no review)=100; stock=110 covers
        # it (OK/SOBRESTOCK territory). With a 7-day review the protection
        # interval is 17 days -> demand_lt=170 > 110 (ordering territory).
        inv_svc.upsert_stock(test_tenant["id"], sku, {
            "current_stock": 110.0, "lead_time_days": 10, "moq": 1.0,
            "supplier": supplier_name,
        })
        session_store.set_forecasts(test_tenant["id"], session_id, {
            sku: _flat_forecast(10.0),
        })

        before = _status_of(test_tenant, session_id, sku)
        assert before["signal"] in ("OK", "SOBRESTOCK")
        assert before["recommended_qty"] == 0.0

        sup_svc.create_supplier(test_tenant["id"], {
            "name": supplier_name, "lead_time_days": 10, "lead_time_std": 0,
            "review_period_days": 7,
        })

        after = _status_of(test_tenant, session_id, sku)
        assert after["signal"] == "PEDIR_PRONTO", after
        assert after["recommended_qty"] > 0.0

    def test_daily_and_weekly_grain_agree_on_the_same_underlying_cadence(self, test_tenant):
        """Same real demand (10/day == 70/week), same real supplier (14-day
        lead, 7-day/1-week cadence) described to a daily-grain session and a
        weekly-grain session: both must reach the same total protection-
        interval demand, guarding the exact days/periods conversion bug this
        file's history warns about."""
        supplier_name = f"Prov-{uuid4().hex[:6]}"
        sup_svc.create_supplier(test_tenant["id"], {
            "name": supplier_name, "lead_time_days": 14, "lead_time_std": 0,
            "review_period_days": 7,
        })

        daily_sku, weekly_sku = _sku(), _sku()
        daily_session = _new_session(test_tenant)
        weekly_session = _new_session(test_tenant)

        inv_svc.upsert_stock(test_tenant["id"], daily_sku, {
            "current_stock": 50.0, "lead_time_days": 14, "moq": 1.0,
            "supplier": supplier_name,
        })
        session_store.set_forecasts(test_tenant["id"], daily_session, {
            daily_sku: _flat_forecast(10.0, step_days=1),
        })

        inv_svc.upsert_stock(test_tenant["id"], weekly_sku, {
            "current_stock": 50.0, "lead_time_days": 14, "moq": 1.0,
            "supplier": supplier_name,
        })
        session_store.set_forecasts(test_tenant["id"], weekly_session, {
            weekly_sku: _flat_forecast(70.0, step_days=7),
        })

        daily_status = _status_of(test_tenant, daily_session, daily_sku, period="daily")
        weekly_status = _status_of(test_tenant, weekly_session, weekly_sku, period="weekly")

        assert daily_status["calc_explanation"]["lead_time_demand"] == pytest.approx(
            weekly_status["calc_explanation"]["lead_time_demand"], rel=1e-6,
        )
        assert daily_status["recommended_qty"] == pytest.approx(
            weekly_status["recommended_qty"], rel=1e-6,
        )
        # protection_interval = 14+7 = 21 days = 3 weeks -> demand_lt = 210
        assert daily_status["calc_explanation"]["lead_time_demand"] == pytest.approx(210.0)


# ─────────────────────────────────────────────────────────────────────────────
# 9 — Supplier CRUD API: the field is exposed on create/update, with the
# mandatory viewer-denied / analyst-allowed permission pair on the mutating
# endpoint (backend/tests/conftest.py's testing standard).
# ─────────────────────────────────────────────────────────────────────────────

class TestSupplierApiReviewPeriod:

    def test_create_exposes_the_field(self, client, auth_headers):
        name = f"Prov-{uuid4().hex[:6]}"
        r = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": name, "review_period_days": 7,
        })
        assert r.status_code == 201
        assert r.json()["data"]["review_period_days"] == 7

        row = query_one(
            "SELECT review_period_days FROM suppliers WHERE id = %s",
            (r.json()["data"]["id"],),
        )
        assert row["review_period_days"] == 7

    def test_create_without_the_field_defaults_to_zero_through_the_api(self, client, auth_headers):
        name = f"Prov-{uuid4().hex[:6]}"
        r = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={"name": name})
        assert r.status_code == 201
        assert r.json()["data"]["review_period_days"] == 0

    def test_out_of_range_value_is_rejected(self, client, auth_headers):
        name = f"Prov-{uuid4().hex[:6]}"
        r = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": name, "review_period_days": -1,
        })
        assert r.status_code == 422

    def test_viewer_cannot_patch_review_period(self, client, auth_headers, viewer_headers):
        name = f"Prov-{uuid4().hex[:6]}"
        cr = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": name, "review_period_days": 3,
        })
        assert cr.status_code == 201
        sup_id = cr.json()["data"]["id"]

        r = client.patch(
            f"/api/v1/inventory/suppliers/{sup_id}",
            headers=viewer_headers,
            json={"review_period_days": 21},
        )
        assert r.status_code == 403

        row = query_one("SELECT review_period_days FROM suppliers WHERE id = %s", (sup_id,))
        assert row["review_period_days"] == 3, "a denied PATCH must not change the DB"

    def test_analyst_can_patch_review_period(self, client, auth_headers, analyst_headers):
        name = f"Prov-{uuid4().hex[:6]}"
        cr = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": name, "review_period_days": 3,
        })
        assert cr.status_code == 201
        sup_id = cr.json()["data"]["id"]

        r = client.patch(
            f"/api/v1/inventory/suppliers/{sup_id}",
            headers=analyst_headers,
            json={"review_period_days": 21},
        )
        assert r.status_code == 200
        assert r.json()["data"]["review_period_days"] == 21

        row = query_one("SELECT review_period_days FROM suppliers WHERE id = %s", (sup_id,))
        assert row["review_period_days"] == 21
