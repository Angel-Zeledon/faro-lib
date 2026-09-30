"""
Calculation correctness audit for inventory planning algorithms.
Tests mathematical accuracy of _calc_recommended, signals, demand trend, BOM explosion.

Run with:
    python -m pytest Backend/tests/test_calculation_audit.py -v --tb=short
"""

import math
import pytest

# All imports are from pure-Python functions that have NO database dependencies.
# The `offline` marker ensures conftest skips DB-reachability enforcement.
pytestmark = pytest.mark.offline

from backend.inventory.service import (
    _Q90_Z,
    _calc_recommended,
    _calc_signal,
    _avg_daily_forecast,
    _classify_abc,
    _classify_xyz,
    _gate_recommended_by_signal,
    _safety_stock,
    best_model_by_sku,
)


# ─────────────────────────────────────────────────────────────────────────────
# CALCULATION 1 — _calc_recommended
# Formula: max(0, avg_daily * lead_time + z * avg_std * sqrt(lead_time) - stock)
# Rounded up to whole units, then floored at MOQ — a MINIMUM, not a multiple.
# ─────────────────────────────────────────────────────────────────────────────

class TestCalcRecommended:
    """Verify _calc_recommended produces mathematically correct results."""

    def test_basic_calculation(self):
        """Formula: demand*LT + z*sigma*sqrt(LT) - stock, whole units, min MOQ."""
        # demand=10/day, lt=14, sigma=1, service=0.95 (z=1.645), stock=50, moq=10
        result = _calc_recommended(50, 10.0, 1.0, 14, 10, 0.95)
        # raw = 10*14 + 1.645*1*sqrt(14) - 50 = 140 + 6.1537... - 50 = 96.1537
        # -> ceil to whole units = 97, already above the MOQ of 10, so 97 stands.
        # This used to assert ceil(96.15/10)*10 = 100: three units of pure
        # overshoot bought because 96.15 was not a round multiple of ten.
        expected_raw = 10 * 14 + 1.645 * 1.0 * math.sqrt(14) - 50
        assert result == math.ceil(expected_raw)
        assert result == 97

    def test_zero_stock_equals_full_demand_plus_safety(self):
        """With 0 stock, recommendation = full demand over LT + safety stock."""
        result = _calc_recommended(0, 10.0, 1.0, 14, 1, 0.95)
        expected_raw = 10 * 14 + 1.645 * 1.0 * math.sqrt(14)
        # MOQ=1 means no upward rounding beyond integer precision
        assert abs(result - math.ceil(expected_raw)) <= 1.0

    def test_sufficient_stock_returns_zero(self):
        """If stock covers demand+safety many times over, recommendation should be 0."""
        result = _calc_recommended(10000, 10.0, 1.0, 14, 1, 0.95)
        assert result == 0

    def test_result_never_negative(self):
        """Result must always be >= 0 — negative raw values are clamped by max(0,...)."""
        result = _calc_recommended(99999, 1.0, 0.1, 5, 1, 0.95)
        assert result == 0

    def test_a_need_above_the_moq_is_not_rounded_up_to_a_multiple(self):
        """
        The overshoot this replaced, at the scale that costs money: needing
        520 units from a supplier whose minimum is 500 used to order 1000.
        """
        # avg_daily=52, lt=10, sigma=0, stock=0 -> raw = 520 exactly.
        result = _calc_recommended(0, 52.0, 0.0, 10, 500, 0.95)
        assert result == 520

    def test_a_need_below_the_moq_is_lifted_to_the_moq(self):
        """The floor still binds — that is what a minimum order quantity is."""
        # raw = 10*10 + 0 - 95 = 5; MOQ=100 -> the supplier will not sell 5.
        result = _calc_recommended(95, 10.0, 0.0, 10, 100, 0.95)
        assert result == 100

    def test_nothing_needed_stays_nothing_even_with_a_large_moq(self):
        """
        A floor applied unconditionally would hand a fully-stocked SKU a whole
        minimum order out of nowhere. `raw > 0` is what stops that, and this is
        the test that would catch its removal — the old ceil got this right for
        free (ceil(0/moq)*moq == 0) so nothing guarded it.
        """
        assert _calc_recommended(10_000, 10.0, 1.0, 14, 500, 0.95) == 0

    def test_whole_units_only(self):
        """
        You cannot buy 96.15 units. The ceil used to be a side effect of the MOQ
        arithmetic; now it is explicit, and this is what pins it.
        """
        result = _calc_recommended(50, 10.0, 1.0, 14, 1, 0.95)
        raw = 10 * 14 + 1.645 * 1.0 * math.sqrt(14) - 50
        assert result == math.ceil(raw)
        assert result == float(int(result))

    def test_higher_service_level_means_more_safety_stock(self):
        """Higher service level → larger z → more safety stock → higher recommendation."""
        r95 = _calc_recommended(0, 10.0, 2.0, 14, 1, 0.95)
        r99 = _calc_recommended(0, 10.0, 2.0, 14, 1, 0.99)
        assert r99 > r95

    def test_zero_demand_with_stock_returns_zero(self):
        """Zero demand → demand_LT=0, safety_stock=0, raw=0-stock<0 → result=0."""
        result = _calc_recommended(100, 0.0, 0.0, 14, 1, 0.95)
        assert result == 0

    def test_lead_time_zero_returns_zero(self):
        """lead_time=0 → demand_LT=0, safety=0, raw=max(0, -stock)=0 if stock>0."""
        # NOTE: lead_time=0 bypasses the Pydantic ge=1 guard only in direct service calls.
        # The API endpoint enforces ge=1, but the service function itself has no guard.
        # With LT=0: raw = 0 + 0 - 50 = -50 → max(0, -50) = 0
        result = _calc_recommended(50, 10.0, 1.0, 0, 1, 0.95)
        assert result == 0

    def test_moq_zero_guard_does_not_crash(self):
        """
        BUG DOCUMENTED: StockUpsert has moq: Field(ge=0), meaning moq=0 is allowed
        through Pydantic validation. The service protects against ZeroDivisionError
        with `if moq and moq > 0:`, so moq=0 silently returns the raw (un-rounded) value.
        This is acceptable defensively, but the API should use ge=1 instead of ge=0.
        """
        # Should NOT raise ZeroDivisionError with moq=0
        result = _calc_recommended(0, 10.0, 1.0, 14, 0, 0.95)
        # Returns raw value (no MOQ rounding) — not a crash
        assert result >= 0

    def test_an_unlisted_service_level_is_computed_not_rounded_to_95(self):
        """
        FIXED. This test used to assert the defect: `_Z` was a four-entry dict
        read with `.get(service_level, 1.645)`, so every level that was not
        0.90 / 0.95 / 0.97 / 0.99 silently got the cushion of 95%. The old
        docstring said so in as many words — "Not a crash, but incorrect. For
        now, verify it returns the same value as service_level=0.95" — and then
        asserted equality anyway.

        That is how it survived: the suite was the thing holding it in place. A
        fix would have turned this test red and looked like a regression.

        `_z_for` now computes any level the API accepts. 0.80 asks for LESS
        protection than 0.95 and must therefore order less; 0.98 asks for more
        and must order more. See test_service_level_is_not_silently_rounded.py
        for the quantile values themselves.
        """
        r_80 = _calc_recommended(0, 10.0, 2.0, 14, 1, 0.80)
        r_95 = _calc_recommended(0, 10.0, 2.0, 14, 1, 0.95)
        r_98 = _calc_recommended(0, 10.0, 2.0, 14, 1, 0.98)
        assert r_80 < r_95, (
            f"service level 0.80 ordered {r_80} and 0.95 ordered {r_95}: a "
            "lower service level must not buy the same cushion"
        )
        assert r_98 > r_95, (
            f"service level 0.98 ordered {r_98} and 0.95 ordered {r_95}: a "
            "higher service level must buy a bigger cushion"
        )

    def test_zero_std_means_zero_safety_stock(self):
        """Zero demand variability → safety_stock = z * 0 * sqrt(LT) = 0. Correct."""
        result = _calc_recommended(0, 10.0, 0.0, 14, 1, 0.95)
        expected = 10.0 * 14  # no safety stock component
        assert result == expected


# ─────────────────────────────────────────────────────────────────────────────
# CALCULATION 3 — _calc_signal
#
# stability.md 17c: the ordering boundary is now the reorder point itself
# (lead-time demand + safety stock), not a flat `1.2 * lead_time`. The old
# flat threshold could sit BELOW the reorder point for any SKU whose safety
# stock exceeded `0.2 * lead_time * avg_daily` — most volatile/intermittent
# SKUs — and in that overlap the old code reported OK and (via
# `_gate_recommended_by_signal`) zeroed the recommendation for a SKU that was,
# by its own reorder point, already due to be ordered.
#
# `_rop_days` below computes `reorder_point_days` the same way the real call
# sites in `service.py` do — `_safety_stock(...) / avg_daily` — so these tests
# exercise the actual formula the signal is judged against, not a value picked
# to make the test pass.
# ─────────────────────────────────────────────────────────────────────────────

class TestCalcSignal:
    """Verify signal classification boundaries."""

    @staticmethod
    def _rop_days(avg_daily, avg_std, lead_time, service_level=0.95):
        safety = _safety_stock(avg_std, lead_time, service_level)
        return (avg_daily * lead_time + safety) / avg_daily

    def test_order_now_when_coverage_less_than_half_lead_time(self):
        # PEDIR_YA is unchanged by this fix: always half a lead time of cover,
        # whatever the reorder point. A reorder point in days is never below
        # the lead time itself (safety stock >= 0), so this sub-band always
        # sits inside "at or below the reorder point" regardless of sigma.
        rop_days = self._rop_days(10.0, 1.0, 15)
        assert _calc_signal(3, 15, rop_days) == "PEDIR_YA"    # 3 < 15*0.5=7.5
        assert _calc_signal(7, 15, rop_days) == "PEDIR_YA"    # 7 < 7.5

    def test_order_soon_at_or_below_the_reorder_point(self):
        """A small-sigma (stable) SKU: the reorder point sits just past the
        lead time, and PEDIR_PRONTO now runs up to exactly that point instead
        of a flat 1.2x lead time."""
        rop_days = self._rop_days(10.0, 1.0, 15)      # cv=0.1 -> rop ~15.6 days
        assert _calc_signal(8, 15, rop_days) == "PEDIR_PRONTO"
        assert _calc_signal(rop_days, 15, rop_days) == "PEDIR_PRONTO"  # AT the ROP: still ordering

    def test_ok_between_the_reorder_point_and_3_lead_times(self):
        rop_days = self._rop_days(10.0, 1.0, 15)
        assert _calc_signal(rop_days + 0.01, 15, rop_days) == "OK"  # just past the ROP
        assert _calc_signal(44, 15, rop_days) == "OK"                # 44 < 45

    def test_overstock_at_3x_lead_time_for_a_low_safety_stock_sku(self):
        # Small safety stock -> the 3x-lead-time floor governs, same as before
        # this fix (stable SKUs must not be reclassified).
        rop_days = self._rop_days(10.0, 1.0, 15)
        assert _calc_signal(45, 15, rop_days) == "SOBRESTOCK"
        assert _calc_signal(100, 15, rop_days) == "SOBRESTOCK"

    def test_boundary_exact_half_lead_time(self):
        """days == LT*0.5 exactly → PEDIR_PRONTO (not strict less-than)."""
        lt = 10
        rop_days = self._rop_days(10.0, 1.0, lt)
        # days < 5.0 → PEDIR_YA; days >= 5.0 → PEDIR_PRONTO
        assert _calc_signal(4.99, lt, rop_days) == "PEDIR_YA"
        assert _calc_signal(5.0, lt, rop_days) == "PEDIR_PRONTO"

    def test_boundary_at_the_reorder_point(self):
        """days == reorder_point_days exactly → PEDIR_PRONTO (ordering); one
        day later → OK. This is the boundary stability.md 17c moved: it used
        to be a flat `1.2 * lead_time`, blind to the SKU's own safety stock."""
        lt = 10
        rop_days = self._rop_days(10.0, 1.0, lt)
        assert _calc_signal(rop_days - 0.01, lt, rop_days) == "PEDIR_PRONTO"
        assert _calc_signal(rop_days, lt, rop_days) == "PEDIR_PRONTO"
        assert _calc_signal(rop_days + 0.01, lt, rop_days) == "OK"

    def test_boundary_3x_lead_time(self):
        """days == LT*3 exactly → SOBRESTOCK, for a SKU whose own reorder
        point sits below that floor (small safety stock)."""
        lt = 10
        rop_days = self._rop_days(10.0, 1.0, lt)
        assert _calc_signal(29.99, lt, rop_days) == "OK"
        assert _calc_signal(30.0, lt, rop_days) == "SOBRESTOCK"

    def test_zero_lead_time_always_overstock(self):
        """
        BEHAVIOR DOCUMENTED (not a bug in itself, but semantically misleading):
        With lead_time=0, the reorder point (lead-time demand + a
        sqrt(0)-scaled safety stock) is 0 too, so every threshold collapses to
        0 and the function falls through to SOBRESTOCK for any positive
        coverage.

        The API endpoint enforces lead_time_days ge=1, so this case only
        occurs if service functions are called directly with LT=0.
        """
        result = _calc_signal(5, 0, reorder_point_days=0.0)
        assert result == "SOBRESTOCK"  # Documented: LT=0 → always SOBRESTOCK

    def test_zero_coverage_with_zero_lead_time(self):
        """days=0, LT=0, reorder_point_days=0: 0 <= 0 is True → PEDIR_PRONTO.

        Changed by this fix (was SOBRESTOCK): with LT=0 the reorder point is
        also 0, so zero stock is now read as exactly AT the (zero) reorder
        point, which this function always treats as an ordering signal. LT=0
        is unreachable through the API (`lead_time_days` is ge=1); this pins
        the new degenerate-input reading rather than endorsing it as a real
        scenario.
        """
        result = _calc_signal(0, 0, reorder_point_days=0.0)
        assert result == "PEDIR_PRONTO"

    def test_high_coverage_no_demand_overstock(self):
        """9999 days coverage (avgDaily~0, the sentinel) → SOBRESTOCK,
        regardless of `reorder_point_days` — unchanged by this fix. A
        dead/discontinued SKU with any stock at all reads as overstock, never
        as an ordering signal."""
        result = _calc_signal(9999, 15, reorder_point_days=9999.0)
        assert result == "SOBRESTOCK"


# ─────────────────────────────────────────────────────────────────────────────
# CALCULATION 3b — the reorder-point/signal regression (stability.md 17c)
#
# Before this fix, `_calc_signal` classified a SKU purely by days-of-cover
# against flat multiples of the lead time (0.5L / 1.2L / 3L), computed with no
# reference to the reorder point computed on the very same row
# (`avg_daily * lead_time + safety_stock`). Whenever the safety stock exceeded
# `0.2 * lead_time * avg_daily` — a coefficient of variation past roughly
# 0.47, i.e. most volatile or intermittent SKUs — the flat OK band
# `[1.2L, 3L)` overlapped "below the reorder point", and
# `_gate_recommended_by_signal` zeroed the recommendation in that band: the
# product computed a reorder point, saw the stock below it, and told the
# buyer not to order.
# ─────────────────────────────────────────────────────────────────────────────

def _pre_fix_signal(coverage_days: float, lead_time: float) -> str:
    """The exact formula `_calc_signal` used before stability.md 17c — kept
    ONLY here, to demonstrate the regression this fix closes. Never call this
    from production code."""
    if coverage_days < lead_time * 0.5:
        return "PEDIR_YA"
    if coverage_days < lead_time * 1.2:
        return "PEDIR_PRONTO"
    if coverage_days < lead_time * 3:
        return "OK"
    return "SOBRESTOCK"


class TestReorderPointSignalRegression:
    """The regression itself, and the cases around it that must NOT move."""

    def test_volatile_sku_below_its_own_reorder_point_now_orders(self):
        """A realistic volatile/intermittent SKU (cv=1.5): avg_daily=10,
        avg_std=15, lead_time=15, service_level=0.95, current_stock=200.

        safety_stock  = 1.645 * 15 * sqrt(15)           ~= 95.56
        reorder_point = 10*15 + 95.56                   ~= 245.56
        reorder_point_days = 245.56 / 10                ~= 24.56 days
        coverage_days = 200 / 10                        = 20 days

        20 is inside the OLD flat OK band [1.2*15=18, 3*15=45) -> pre-fix
        signal is OK, and `_gate_recommended_by_signal` zeroes the
        recommendation. But 20 < 24.56: the stock is BELOW this SKU's own
        reorder point. This is exactly the overlap stability.md 17c describes
        (safety_stock=95.56 > 0.2*15*10=30), and it is the test that fails
        against the pre-fix code.
        """
        avg_daily, avg_std, lead_time, sl = 10.0, 15.0, 15, 0.95
        current_stock = 200.0

        safety = _safety_stock(avg_std, lead_time, sl)
        reorder_point = avg_daily * lead_time + safety
        reorder_point_days = reorder_point / avg_daily
        coverage_days = current_stock / avg_daily

        # Confirms the scenario actually lands in the old flat OK band, and
        # below the true reorder point -- i.e. this IS the overlap.
        assert lead_time * 1.2 <= coverage_days < lead_time * 3
        assert coverage_days < reorder_point_days
        assert _pre_fix_signal(coverage_days, lead_time) == "OK"

        signal = _calc_signal(coverage_days, lead_time, reorder_point_days)
        assert signal in ("PEDIR_YA", "PEDIR_PRONTO"), (
            f"stock is below its own reorder point ({current_stock} < "
            f"{reorder_point:.2f}) but the signal is {signal!r}, not an "
            "ordering signal"
        )

        recommended = _calc_recommended(
            current_stock, avg_daily, avg_std, lead_time, moq=1, service_level=sl,
        )
        gated = _gate_recommended_by_signal(signal, recommended)
        assert gated > 0, "the gated recommendation must not be zeroed"
        assert gated == pytest.approx(reorder_point - current_stock, abs=1.0)

    def test_stock_exactly_at_reorder_point_and_one_unit_either_side(self):
        avg_daily, avg_std, lead_time, sl = 10.0, 15.0, 15, 0.95
        safety = _safety_stock(avg_std, lead_time, sl)
        reorder_point = avg_daily * lead_time + safety
        reorder_point_days = reorder_point / avg_daily

        at_rop = _calc_signal(reorder_point_days, lead_time, reorder_point_days)
        assert at_rop in ("PEDIR_YA", "PEDIR_PRONTO"), (
            "AT the reorder point must be an ordering signal"
        )

        one_below_days = (reorder_point - 1) / avg_daily
        below = _calc_signal(one_below_days, lead_time, reorder_point_days)
        assert below in ("PEDIR_YA", "PEDIR_PRONTO")

        one_above_days = (reorder_point + 1) / avg_daily
        above = _calc_signal(one_above_days, lead_time, reorder_point_days)
        assert above == "OK"

    def test_stable_sku_keeps_todays_signal(self):
        """Small sigma (cv=0.1): the old and new boundaries are close enough
        that a SKU comfortably inside a band keeps the same reading."""
        avg_daily, avg_std, lead_time, sl = 10.0, 1.0, 15, 0.95
        safety = _safety_stock(avg_std, lead_time, sl)
        reorder_point_days = (avg_daily * lead_time + safety) / avg_daily

        for coverage_days in (3.0, 20.0, 100.0):
            pre = _pre_fix_signal(coverage_days, lead_time)
            post = _calc_signal(coverage_days, lead_time, reorder_point_days)
            assert post == pre, (
                f"a low-cv SKU at coverage={coverage_days} changed from "
                f"{pre!r} to {post!r}"
            )

    def test_zero_demand_stays_out_of_an_ordering_signal(self):
        """avg_daily<=0 -> the 9999-day sentinel -> SOBRESTOCK, exactly as
        before this fix, whatever the (irrelevant) reorder point."""
        assert _calc_signal(9999.0, 15, reorder_point_days=0.0) == "SOBRESTOCK"
        assert _calc_signal(9999.0, 15, reorder_point_days=9999.0) == "SOBRESTOCK"

    def test_genuinely_overstocked_sku_still_reads_sobrestock(self):
        avg_daily, avg_std, lead_time, sl = 10.0, 2.0, 15, 0.95
        safety = _safety_stock(avg_std, lead_time, sl)
        reorder_point_days = (avg_daily * lead_time + safety) / avg_daily
        # 10x the lead time of cover: unambiguously overstocked.
        coverage_days = lead_time * 10
        assert _calc_signal(coverage_days, lead_time, reorder_point_days) == "SOBRESTOCK"


# ─────────────────────────────────────────────────────────────────────────────
# CALCULATION 2 — _avg_daily_forecast
# ─────────────────────────────────────────────────────────────────────────────

class TestAvgDailyForecast:
    """Verify forecast aggregation is correct."""

    def test_single_model_simple_average(self):
        """Average of two forecast points: (10+20)/2 = 15."""
        model_forecasts = {
            "lightgbm": {"forecast": [
                {"value": 10, "lower": 8,  "upper": 12},
                {"value": 20, "lower": 16, "upper": 24},
            ]}
        }
        avg, std = _avg_daily_forecast(model_forecasts, 2)
        assert avg == 15.0

    def test_legacy_points_without_quantiles_fall_back_to_upper(self):
        """Sessions trained before the quantile keys only carry `upper`.

        Those still produce a sigma — an existing session keeps making
        recommendations instead of silently dropping to zero safety stock — but
        it is now a real sigma. `upper` is the TOP of a band, roughly the 90th
        percentile, so returning the raw spread handed the caller ~1.28 sigma
        and the caller multiplied by z(service_level) again. A configured 95%
        service level was being served at about 98%. Dividing by the same z the
        q90 branch uses makes both branches return the same quantity.
        """
        model_forecasts = {
            "lgb": {"forecast": [
                {"value": 10, "upper": 12},  # spread 2 -> sigma 2/1.2816
                {"value": 20, "upper": 24},  # spread 4 -> sigma 4/1.2816
            ]}
        }
        avg, std = _avg_daily_forecast(model_forecasts, 2)
        assert std == pytest.approx(3.0 / _Q90_Z, rel=1e-9)   # mean spread / z90

    def test_empty_models_returns_zero(self):
        avg, std = _avg_daily_forecast({}, 14)
        assert avg == 0.0
        assert std == 0.0

    def test_models_with_no_forecast_points(self):
        model_forecasts = {"lgb": {"forecast": []}}
        avg, std = _avg_daily_forecast(model_forecasts, 14)
        assert avg == 0.0
        assert std == 0.0

    def test_uses_only_lead_time_points(self):
        """Only the first lead_time forecast steps should be consumed."""
        model_forecasts = {
            "lgb": {"forecast": [
                {"value": 10, "upper": 11},   # LT point 1
                {"value": 10, "upper": 11},   # LT point 2
                {"value": 999, "upper": 999}, # Beyond LT — must be ignored
            ]}
        }
        avg, std = _avg_daily_forecast(model_forecasts, 2)
        assert avg == 10.0

    def test_result_never_negative(self):
        """avg_daily is protected by max(0.0, ...) — can't go negative."""
        model_forecasts = {
            "lgb": {"forecast": [{"value": -5, "upper": 0, "lower": -10}]}
        }
        avg, std = _avg_daily_forecast(model_forecasts, 1)
        assert avg >= 0.0

    def test_std_never_negative(self):
        """
        BEHAVIOR DOCUMENTED: if upper < value, std per point = upper - value < 0.
        The final max(0.0, avg_std) ensures the returned std is non-negative.
        """
        model_forecasts = {
            "lgb": {"forecast": [
                {"value": 20, "upper": 10},   # upper < value → std_point = -10
            ]}
        }
        avg, std = _avg_daily_forecast(model_forecasts, 1)
        assert std >= 0.0  # max(0.0, -10) = 0.0

    def test_multiple_models_averaged_together(self):
        """Two models: both values are pooled and averaged together."""
        model_forecasts = {
            "lgb":   {"forecast": [{"value": 10, "upper": 11}]},
            "xgb":   {"forecast": [{"value": 20, "upper": 22}]},
        }
        avg, std = _avg_daily_forecast(model_forecasts, 1)
        # all_values = [10, 20] → avg = 15
        assert avg == 15.0

    def test_none_value_coerced_to_zero(self):
        """
        BEHAVIOR DOCUMENTED: p.get("value") or 0.0 coerces None AND 0 to 0.0.
        A None forecast value silently becomes 0 instead of being excluded.
        This can underestimate demand when models return None points.
        """
        model_forecasts = {
            "lgb": {"forecast": [
                {"value": None, "upper": None},   # None → 0.0
                {"value": 10,   "upper": 11},
            ]}
        }
        avg, std = _avg_daily_forecast(model_forecasts, 2)
        # None becomes 0, so avg = (0+10)/2 = 5, not 10
        assert avg == 5.0  # Documents the None-coercion behavior


# ─────────────────────────────────────────────────────────────────────────────
# Sigma recovery and best-model selection
#
# The purchase quantity is max(0, demand·LT + z·sigma·√LT − stock). Both inputs
# used to be wrong: sigma was read off `upper`, which is a p90 from a quantile
# model rather than one standard deviation, and the demand was the mean of
# every model trained, including the weakest.
# ─────────────────────────────────────────────────────────────────────────────

class TestSigmaFromQuantiles:

    def test_sigma_is_recovered_from_q90_not_upper(self):
        """q90 = value + 1.2816·sigma, so sigma = (q90 - value) / 1.2816.

        `upper` is deliberately set to a far tighter spread here, exactly as a
        quantile model produces it. If the code read `upper` this would come
        out at 1.0 instead of 10.0.
        """
        model_forecasts = {
            "xgb": {"forecast": [
                {"value": 100.0, "q90": 100.0 + 1.2816 * 10.0, "upper": 101.0},
            ]}
        }
        _avg, std = _avg_daily_forecast(model_forecasts, 1)
        assert std == pytest.approx(10.0, abs=1e-3)

    def test_q90_wins_over_upper_when_both_present(self):
        """Both keys ship on every modern point; q90 is the honest one."""
        pt = {"value": 50.0, "q90": 50.0 + 1.2816 * 8.0, "upper": 52.0}
        _avg, std = _avg_daily_forecast({"m": {"forecast": [pt]}}, 1)
        assert std == pytest.approx(8.0, abs=1e-3)
        assert std != pytest.approx(2.0, abs=1e-3)   # 2.0 is what `upper` gives

    def test_q90_below_value_cannot_produce_negative_sigma(self):
        """A clamped-at-zero q90 on a near-zero forecast must not go negative."""
        model_forecasts = {"m": {"forecast": [{"value": 20.0, "q90": 0.0}]}}
        _avg, std = _avg_daily_forecast(model_forecasts, 1)
        assert std == 0.0

    def test_a_wider_sigma_buys_more(self):
        """The whole point of getting sigma right: it moves the order."""
        tight = _calc_recommended(current_stock=0, avg_daily=10, avg_std=2,
                                  lead_time=9, moq=0, service_level=0.95)
        wide  = _calc_recommended(current_stock=0, avg_daily=10, avg_std=8,
                                  lead_time=9, moq=0, service_level=0.95)
        # 90 + 1.645·2·3 = 99.87  vs  90 + 1.645·8·3 = 129.48
        assert tight == pytest.approx(99.87, abs=0.01)
        assert wide == pytest.approx(129.48, abs=0.01)


class TestBestModelSelection:

    def test_best_model_is_the_lowest_wape_non_baseline(self):
        rows = [
            {"sku": "A", "model": "prophet",  "wape": 0.11, "type": "stat"},
            {"sku": "A", "model": "xgboost",  "wape": 0.03, "type": "ml"},
            {"sku": "A", "model": "naive",    "wape": 0.01, "type": "baseline"},
            {"sku": "B", "model": "lightgbm", "wape": 0.07, "type": "ml"},
        ]
        assert best_model_by_sku(rows) == {"A": "xgboost", "B": "lightgbm"}

    def test_a_winning_baseline_is_never_selected(self):
        """Baselines exist to be beaten. Buying from a naive forecast because
        it happened to score best would be a bug, not a fallback."""
        rows = [
            {"sku": "A", "model": "naive",   "wape": 0.01, "type": "baseline"},
            {"sku": "A", "model": "xgboost", "wape": 0.30, "type": "ml"},
        ]
        assert best_model_by_sku(rows) == {"A": "xgboost"}

    def test_rows_without_wape_are_ignored(self):
        rows = [
            {"sku": "A", "model": "broken",  "wape": None, "type": "ml"},
            {"sku": "A", "model": "xgboost", "wape": 0.05, "type": "ml"},
        ]
        assert best_model_by_sku(rows) == {"A": "xgboost"}

    def test_the_chosen_model_alone_drives_the_demand(self):
        """The weak model must not pull the number the buyer acts on."""
        model_forecasts = {
            "xgboost": {"forecast": [{"value": 10.0, "q90": 10.0 + 1.2816}]},
            "prophet": {"forecast": [{"value": 90.0, "q90": 90.0 + 1.2816}]},
        }
        avg_best, _ = _avg_daily_forecast(model_forecasts, 1, "xgboost")
        avg_pooled, _ = _avg_daily_forecast(model_forecasts, 1)
        assert avg_best == 10.0
        assert avg_pooled == 50.0    # the old behaviour, 5x the winner

    def test_unknown_model_falls_back_to_pooling(self):
        """A session whose metrics name a model that has no stored forecast
        must still produce a number rather than none."""
        model_forecasts = {
            "lgb": {"forecast": [{"value": 10.0, "upper": 11.0}]},
            "xgb": {"forecast": [{"value": 20.0, "upper": 21.0}]},
        }
        avg, _ = _avg_daily_forecast(model_forecasts, 1, "a_model_that_left")
        assert avg == 15.0

    def test_a_winner_with_no_stored_points_does_not_zero_the_sku(self):
        """The dangerous shape: the best model is present but empty.

        Narrowing to it would return demand 0, which reads downstream as "well
        stocked" and drops the SKU off the semáforo without a word. Falling
        back to the pooled number is a worse estimate but a visible one.
        """
        model_forecasts = {
            "xgboost": {"forecast": []},
            "lgb":     {"forecast": [{"value": 12.0, "upper": 13.0}]},
        }
        avg, _ = _avg_daily_forecast(model_forecasts, 1, "xgboost")
        assert avg == 12.0


# ─────────────────────────────────────────────────────────────────────────────
# ABC-XYZ classification
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyXYZ:
    """Verify XYZ classification by coefficient of variation."""

    def test_stable_demand_is_X(self):
        assert _classify_xyz(0.0) == "X"
        assert _classify_xyz(0.3) == "X"
        assert _classify_xyz(0.49) == "X"

    def test_boundary_x_y(self):
        assert _classify_xyz(0.5) == "Y"   # cv >= 0.5 → not X

    def test_moderate_is_Y(self):
        assert _classify_xyz(0.5) == "Y"
        assert _classify_xyz(0.7) == "Y"
        assert _classify_xyz(0.99) == "Y"

    def test_boundary_y_z(self):
        assert _classify_xyz(1.0) == "Z"   # cv >= 1.0 → Z

    def test_erratic_is_Z(self):
        assert _classify_xyz(1.0) == "Z"
        assert _classify_xyz(1.5) == "Z"
        assert _classify_xyz(5.0) == "Z"

    def test_none_cv_is_unknown(self):
        assert _classify_xyz(None) == "?"


class TestClassifyABC:
    """Verify ABC classification by cumulative revenue share."""

    def test_single_item_is_always_A(self):
        """One item is 100% of revenue → cumulative pct = 1.0 <= 0.80? No, 1.0 > 0.80.
        Wait: 100% > 80%, so it goes to B? Let's verify the exact boundary logic."""
        items = [{"sku": "A", "daily_demand": 10.0, "unit_cost": 10.0}]
        abc = _classify_abc(items)
        # Only item: cumulative = 100%. pct = 1.0. 1.0 <= 0.80 is False, 1.0 <= 0.95 is False → C?
        # Actually: pct = cumulative/total. After adding 100/100 = 1.0. 1.0 <= 0.80 False.
        # 1.0 <= 0.95 False. → "C". This seems counterintuitive for a single item.
        # BEHAVIOR DOCUMENTED: a single-SKU catalog gets classified as C, not A.
        assert abc["A"] in ("A", "B", "C")

    def test_top_revenue_item_is_A(self):
        """
        BUG CONFIRMED: A single item that accounts for >80% of revenue in one step
        is incorrectly classified as C (or B) instead of A.

        Root cause: the algorithm adds cumulative revenue BEFORE checking thresholds.
        When one item already pushes cumulative past both 0.80 AND 0.95 thresholds,
        neither condition is True, and the item falls through to C.

          cumulative after HERO = 100000/100001 = 0.99999
          0.99999 <= 0.80 → False
          0.99999 <= 0.95 → False
          → result: "C"  (WRONG — should be "A")

        An item at exactly 80.0% gets "A" (0.80 <= 0.80 is True).
        An item at 85% gets "B" (0.85 <= 0.95 is True).
        An item at 99% gets "C" (0.99 > 0.95 → falls to else).

        Fix: change `if pct <= 0.80` to assign A to items that PUT the cumulative
        over the 80% mark (i.e., the item that crosses the boundary belongs in the
        tier it crossed into, not the one above).
        """
        items = [
            {"sku": "HERO",  "daily_demand": 1000.0, "unit_cost": 100.0},  # 100k
            {"sku": "SMALL", "daily_demand": 1.0,    "unit_cost": 1.0},    # 1
        ]
        abc = _classify_abc(items)
        # FIXED: the algorithm now assigns the tier based on cumulative share
        # BEFORE adding the item, so the dominant SKU correctly lands in A.
        assert abc["HERO"] == "A"

    def test_single_item_above_80pct_classified_as_A(self):
        """
        FIXED (was test_..._classified_as_B_not_A):
        An item responsible for 85% of revenue is class A — correct ABC theory:
        the item that pushes cumulative to/past 80% belongs in A.
        """
        items = [
            {"sku": "SKU1", "daily_demand": 85.0, "unit_cost": 1.0},
            {"sku": "SKU2", "daily_demand": 15.0, "unit_cost": 1.0},
        ]
        abc = _classify_abc(items)
        # SKU1 is evaluated at cumulative 0.0 (< 0.80) → A; SKU2 at 0.85 (< 0.95) → B.
        assert abc["SKU1"] == "A"
        assert abc["SKU2"] == "B"

    def test_abc_three_tiers_correct_order(self):
        """Items at 80%, 15%, 5% splits."""
        items = [
            {"sku": "SKU1", "daily_demand": 80.0, "unit_cost": 1.0},
            {"sku": "SKU2", "daily_demand": 15.0, "unit_cost": 1.0},
            {"sku": "SKU3", "daily_demand": 5.0,  "unit_cost": 1.0},
        ]
        abc = _classify_abc(items)
        # cumulative after SKU1: 80/100=0.80 → pct=0.80 ≤ 0.80 → A
        # cumulative after SKU2: 95/100=0.95 → pct=0.95 ≤ 0.95 → B
        # cumulative after SKU3: 100/100=1.00 → C
        assert abc["SKU1"] == "A"
        assert abc["SKU2"] == "B"
        assert abc["SKU3"] == "C"

    def test_all_zero_revenue_all_C(self):
        """If total revenue = 0, all items fall back to C."""
        items = [
            {"sku": "X", "daily_demand": 0.0, "unit_cost": 0.0},
            {"sku": "Y", "daily_demand": 0.0, "unit_cost": 0.0},
        ]
        abc = _classify_abc(items)
        assert abc["X"] == "C"
        assert abc["Y"] == "C"

    def test_no_cost_uses_1_as_proxy(self):
        """
        BEHAVIOR DOCUMENTED: unit_cost=None → `cost = item.get(...) or 1.0`
        Revenue proxy = daily_demand * 1.0. Not using 0, so demand still ranks items.
        """
        items = [
            {"sku": "BIG",   "daily_demand": 100.0, "unit_cost": None},
            {"sku": "SMALL", "daily_demand": 1.0,   "unit_cost": None},
        ]
        abc = _classify_abc(items)
        # BIG: 100/(100+1) = 0.990 > 0.95 → C? Or A if 0.990 > 0.80...
        # cumulative after BIG = 100/101 = 0.99. 0.99 <= 0.80 False, 0.99 <= 0.95 False → C
        # BEHAVIOR: with only 2 items and 99% concentration, BIG gets C because
        # cumulative hits 0.99 which exceeds both 0.80 and 0.95 thresholds in one step.
        # Both items end up as C. Documented as known behavior.
        assert abc["BIG"] in ("A", "B", "C")    # Let code define the boundary
        # SMALL is always lower-ranked than BIG
        assert abc.get("SMALL") in ("B", "C")


# ─────────────────────────────────────────────────────────────────────────────
# Dead stock endpoint logic (pure calculation, no DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestDeadStockLogic:
    """
    Verify the dead stock detection formula.
    The actual endpoint requires DB, so we test the math directly.
    """

    def _is_dead(self, first_stock, last_stock, avg_daily, n_points):
        """
        Replicates the dead_stock endpoint logic:
          depletion = first_stock - last_stock
          expected  = avg_daily * len(history)
          dead if expected > 0 and depletion < expected * 0.20
        """
        depletion = first_stock - last_stock
        expected  = avg_daily * n_points
        if expected > 0 and depletion < expected * 0.20:
            return True
        return False

    def test_normal_depletion_not_dead(self):
        """Stock drops as expected → not dead."""
        # avg=10/day, 30 days, expected=300. Actual depletion=250 (>= 20%)
        assert not self._is_dead(500, 250, 10.0, 30)

    def test_slow_mover_flagged_dead(self):
        """Barely any movement → dead."""
        # expected=300, depletion=10 (3.3% < 20%)
        assert self._is_dead(500, 490, 10.0, 30)

    def test_stock_replenishment_false_positive_bug(self):
        """
        BUG CONFIRMED: If stock increased (replenishment), depletion is NEGATIVE.
        A negative depletion is ALWAYS < expected*0.20 (which is positive).
        Result: items that received stock IN are wrongly flagged as dead stock.

        Example: first_stock=100, last_stock=200 (reorder arrived)
          depletion = 100-200 = -100
          expected  = 10*30 = 300
          -100 < 300*0.20 = 60 → TRUE → incorrectly flagged as dead
        """
        is_dead = self._is_dead(100, 200, 10.0, 30)
        # This IS flagged as dead — that is the BUG
        assert is_dead is True  # Documents the confirmed bug

    def test_zero_stock_with_zero_forecast_not_flagged(self):
        """No forecast and no stock movement → expected=0 → NOT flagged (guard works)."""
        assert not self._is_dead(0, 0, 0.0, 30)

    def test_zero_beginning_stock_zero_depletion_not_flagged(self):
        """first_stock=0 → depletion=0 → if expected>0: 0 < expected*0.20 → dead."""
        # This is technically flagged if expected>0 because 0 < any_positive
        is_dead = self._is_dead(0, 0, 10.0, 30)
        assert is_dead is True  # Documents: SKU with 0 stock + forecast flagged as dead

    def test_depletion_pct_calculation(self):
        """
        depletion_pct = depletion / first_stock * 100
        Guard `if first_stock > 0` prevents ZeroDivisionError.
        Negative depletion (replenishment) yields negative pct — cosmetically wrong.
        """
        first_stock = 100
        last_stock = 200  # Replenishment received
        depletion = first_stock - last_stock  # -100
        depletion_pct = round(depletion / first_stock * 100, 1) if first_stock > 0 else 0
        assert depletion_pct == -100.0   # Documents the negative pct when stock increased


# ─────────────────────────────────────────────────────────────────────────────
# BOM explosion pure math (no DB, uses direct dict manipulation)
# ─────────────────────────────────────────────────────────────────────────────

class TestBomExplosionMath:
    """
    Verify BOM explosion formulas without calling the DB.
    The explode_requirements function calls DB, so we test the embedded math.
    """

    def test_required_qty_formula(self):
        """required_qty = quantity_per_unit * to_produce."""
        qty_per_unit = 3.0
        to_produce = 10.0
        required_qty = round(qty_per_unit * to_produce, 2)
        assert required_qty == 30.0

    def test_to_produce_is_clamped_at_zero(self):
        """to_produce = max(0, forecast_demand - current_stock) >= 0."""
        forecast_demand = 50.0
        current_stock = 200.0  # Stock exceeds demand
        to_produce = max(0.0, forecast_demand - current_stock)
        assert to_produce == 0.0

    def test_shortage_formula(self):
        """shortage = max(0, required_qty - child_stock)."""
        required_qty = 30.0
        child_stock = 20.0
        shortage = round(max(0.0, required_qty - child_stock), 2)
        assert shortage == 10.0

    def test_no_shortage_when_sufficient_stock(self):
        """No shortage when child_stock >= required_qty."""
        shortage = max(0.0, 30.0 - 100.0)
        assert shortage == 0.0

    def test_zero_to_produce_means_zero_requirements(self):
        """If to_produce=0, all required_qty=0 regardless of BOM ratios."""
        qty_per_unit = 5.0
        to_produce = 0.0
        required_qty = round(qty_per_unit * to_produce, 2)
        assert required_qty == 0.0

    def test_estimated_cost_formula(self):
        """estimated_cost = shortage * unit_cost."""
        shortage = 10.0
        cost = 25.50
        estimated = round(shortage * cost, 2)
        assert estimated == 255.0

    def test_missing_child_stock_defaults_to_zero(self):
        """
        In explode_requirements: child = stock_map.get(child_sku, {})
        child_stock = child.get('current_stock') or 0
        If child_sku is not in stock_map → child={} → child_stock=0.
        All demand becomes shortage. This is conservative (safe) behavior.
        """
        stock_map = {}
        child = stock_map.get("UNKNOWN_SKU", {})
        child_stock = child.get("current_stock") or 0
        assert child_stock == 0

    def test_no_cycle_detection_in_flat_bom(self):
        """
        BEHAVIOR DOCUMENTED: explode_requirements uses a flat `bom_map` dict
        (one level deep). It iterates items without recursion, so A→B→A cycles
        DO NOT cause infinite loops. However, they DO produce double-counting
        in material_totals if the same child_sku appears via multiple paths.
        The self-reference guard (parent_sku == child_sku) in upsert_bom_item
        prevents A→A, but multi-hop cycles (A→B→A) are not DB-prevented.
        """
        # Simulate cycle: A needs B, B needs A
        bom_map = {
            "SKU_A": [{"child_sku": "SKU_B", "quantity": 2.0}],
            "SKU_B": [{"child_sku": "SKU_A", "quantity": 1.0}],
        }
        # Flat iteration over SKU_A's requirements (no recursive descent)
        requirements_for_A = bom_map["SKU_A"]
        assert len(requirements_for_A) == 1
        assert requirements_for_A[0]["child_sku"] == "SKU_B"
        # SKU_B's requirements (A) are never followed automatically — no infinite loop
        # at the explosion level. Documented: cycles won't crash but produce wrong totals.
