"""
Tests for forecasting_core.business.inventory.InventoryAdvisor.
"""

import pytest
import numpy as np
import pandas as pd

from forecasting_core.business.inventory import InventoryAdvisor, InventoryRecommendation


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────

class TestInventoryAdvisorRecommend:

    def _advisor(self, **kwargs):
        defaults = dict(service_level=0.95, lead_time_days=7,
                        holding_cost_pct=0.20, stockout_cost_multiplier=3.0)
        defaults.update(kwargs)
        return InventoryAdvisor(**defaults)

    def test_returns_recommendation_object(self):
        adv = self._advisor()
        rec = adv.recommend("SKU_A", np.full(14, 100.0), current_stock=500.0)
        assert isinstance(rec, InventoryRecommendation)

    def test_action_reorder_when_stock_below_rop(self):
        adv = self._advisor()
        rec = adv.recommend("S", np.full(14, 100.0), current_stock=1.0)
        assert rec.action == "REORDER"

    def test_action_ok_when_well_stocked(self):
        adv = self._advisor()
        rec = adv.recommend("S", np.full(14, 10.0), current_stock=10_000.0)
        assert rec.action in {"OK", "OVERSTOCK"}

    def test_action_overstock_when_excess_inventory(self):
        adv = self._advisor()
        # Tiny demand, huge stock → overstock
        rec = adv.recommend("S", np.full(14, 1.0), current_stock=100_000.0)
        assert rec.action == "OVERSTOCK"

    def test_stockout_risk_in_0_1(self):
        adv = self._advisor()
        rec = adv.recommend("S", np.full(14, 100.0), current_stock=0.0)
        assert 0.0 <= rec.stockout_risk <= 1.0

    def test_zero_stock_high_stockout_risk(self):
        adv = self._advisor()
        rec = adv.recommend("S", np.full(14, 100.0), current_stock=0.0)
        assert rec.stockout_risk > 0.5

    def test_massive_stock_low_stockout_risk(self):
        adv = self._advisor()
        rec = adv.recommend("S", np.full(14, 10.0), current_stock=1_000_000.0)
        assert rec.stockout_risk < 0.01

    def test_unknown_stock_reports_none_not_a_number(self):
        """current_stock omitted (the engine's own call shape — Pipeline
        never has a stock source) must not be silently treated as 0.0. Under
        the old default, this SKU would get stockout_risk~1.0,
        days_of_coverage=0.0 and action="REORDER" — a specific, confident,
        wrong answer for a quantity nobody supplied."""
        adv = self._advisor()
        rec = adv.recommend("S", np.full(14, 100.0))
        assert rec.stockout_risk is None
        assert rec.days_of_coverage is None
        assert rec.action == "UNKNOWN"
        assert rec.overstock_alert is False

    def test_explicit_zero_stock_is_not_treated_as_unknown(self):
        """current_stock=0.0 is a caller telling us the shelf is genuinely
        empty, not an absence of information — it must take the normal
        REORDER/high-risk path, not UNKNOWN. Guards against a `not
        current_stock` check, which is also true for 0.0 and would collapse
        the two cases."""
        adv = self._advisor()
        rec = adv.recommend("S", np.full(14, 100.0), current_stock=0.0)
        assert rec.action != "UNKNOWN"
        assert rec.stockout_risk is not None
        assert rec.days_of_coverage is not None

    def test_reorder_point_positive(self):
        adv = self._advisor()
        rec = adv.recommend("S", np.full(14, 50.0))
        assert rec.reorder_point > 0

    def test_safety_stock_positive(self):
        adv = self._advisor()
        rec = adv.recommend("S", np.full(14, 50.0))
        assert rec.safety_stock >= 0

    def test_forecast_total_is_sum(self):
        fc = np.array([10.0, 20.0, 30.0])
        adv = self._advisor()
        rec = adv.recommend("S", fc)
        assert rec.forecast_total == pytest.approx(60.0)

    def test_forecast_mean_is_average(self):
        fc = np.array([10.0, 20.0, 30.0])
        adv = self._advisor()
        rec = adv.recommend("S", fc)
        assert rec.forecast_mean == pytest.approx(20.0)

    def test_single_point_forecast(self):
        adv = self._advisor()
        rec = adv.recommend("S", np.array([50.0]), current_stock=100.0)
        assert isinstance(rec, InventoryRecommendation)

    def test_demand_std_provided(self):
        adv = self._advisor()
        rec = adv.recommend("S", np.full(14, 100.0), demand_std=10.0)
        assert rec.details["demand_std"] == pytest.approx(10.0)

    def test_demand_std_estimated_from_forecast(self):
        # Constant forecast → std=0, then uses daily_demand * 0.3
        adv = self._advisor()
        rec = adv.recommend("S", np.full(14, 100.0))
        # demand_std should be estimated automatically
        assert rec.details["demand_std"] >= 0

    def test_details_contain_required_fields(self):
        adv = self._advisor()
        rec = adv.recommend("S", np.full(7, 50.0))
        for key in ["z_score", "lead_time_days", "service_level", "daily_demand"]:
            assert key in rec.details

    def test_days_of_coverage_zero_demand(self):
        # Zero demand → coverage should be very large (no division by zero crash)
        adv = self._advisor()
        rec = adv.recommend("S", np.zeros(14), current_stock=100.0)
        assert np.isfinite(rec.days_of_coverage)

    # These two used to read:
    #
    #     # ⚠️ CRITICAL FIX REQUIRED: service_level=1.0 causes ppf(1.0)=inf
    #     def test_service_level_boundary_1_causes_inf(self):
    #         with pytest.raises(Exception):
    #             ...
    #
    # which is a test of the SYMPTOM, not of the behaviour: it passes while the
    # bug is alive, keeps passing after the bug is fixed (the guard raises too),
    # and would keep passing if a renamed argument made the constructor raise
    # TypeError before reaching any inventory logic at all. `Exception` catches
    # every one of those. The fix it demanded had in fact already been made —
    # the constructor validates — and nobody could tell from the suite.
    #
    # What the advisor actually promises, asserted: the open interval is
    # enforced, the error says which argument and why, and nothing infinite
    # ever reaches the recommendation.

    @pytest.mark.parametrize("bad", [1.0, 0.0, -0.1, 1.5])
    def test_a_service_level_outside_the_open_interval_is_refused(self, bad):
        with pytest.raises(ValueError, match="service_level"):
            InventoryAdvisor(service_level=bad)

    @pytest.mark.parametrize("good", [0.5, 0.9, 0.95, 0.99, 0.999])
    def test_every_accepted_service_level_yields_a_finite_z(self, good):
        adv = InventoryAdvisor(service_level=good)
        assert np.isfinite(adv._z)
        rec = adv.recommend("S", np.full(7, 10.0))
        assert np.isfinite(rec.details["z_score"])
        assert np.isfinite(rec.reorder_point)
        assert np.isfinite(rec.safety_stock)

    def test_a_higher_service_level_never_buys_less_cushion(self):
        """The property a buyer actually relies on. A z lookup that silently
        collapsed distinct levels onto one value — the defect the backend had
        in `_z_for` — is invisible to a single-point assertion and obvious
        here."""
        levels = [0.80, 0.90, 0.95, 0.99]
        cushions = [
            InventoryAdvisor(service_level=s).recommend(
                "S", np.full(14, 10.0), demand_std=3.0
            ).safety_stock
            for s in levels
        ]
        assert cushions == sorted(cushions), cushions
        assert cushions[0] < cushions[-1], "distinct service levels collapsed onto one cushion"


# ─────────────────────────────────────────────────────────────────────────────
# batch_recommend
# ─────────────────────────────────────────────────────────────────────────────

class TestBatchRecommend:

    def test_returns_sorted_by_stockout_risk(self):
        adv = InventoryAdvisor()
        forecasts = {
            "risky":  np.full(7, 100.0),  # stock=0
            "safe":   np.full(7, 10.0),   # stock=10000
        }
        stocks = {"risky": 0.0, "safe": 10_000.0}
        recs = adv.batch_recommend(forecasts, stocks_by_sku=stocks)
        assert recs[0].sku == "risky"

    def test_batch_empty_input(self):
        adv = InventoryAdvisor()
        recs = adv.batch_recommend({})
        assert recs == []

    def test_summary_df_returns_dataframe(self):
        adv = InventoryAdvisor()
        recs = adv.batch_recommend({
            "A": np.full(7, 50.0),
            "B": np.full(7, 80.0),
        })
        df = adv.summary_df(recs)
        assert isinstance(df, pd.DataFrame)
        assert "sku" in df.columns
        assert "action" in df.columns
        assert len(df) == 2

    def test_missing_stock_is_unknown_not_zero(self):
        """The engine never receives stock levels, so this is the path every
        engine-generated recommendation takes. Silently defaulting to 0.0
        made every SKU look like it was about to stock out (stockout_risk
        ~1.0, action REORDER, confidently wrong) — see
        backend/ai/rag_service.py ~line 723 for where that reached a tenant.
        """
        adv = InventoryAdvisor()
        recs = adv.batch_recommend({"A": np.full(7, 50.0)})
        assert recs[0].details["current_stock"] is None
        assert recs[0].stockout_risk is None
        assert recs[0].days_of_coverage is None
        assert recs[0].action == "UNKNOWN"

    def test_batch_recommend_sorts_unknown_stock_last_without_crashing(self):
        """stockout_risk=None (unknown stock) used to be compared directly
        against float stockout_risk values by `sorted(..., reverse=True)`,
        which raises TypeError in Python 3. Mixing a known and an unknown SKU
        reproduces it."""
        adv = InventoryAdvisor()
        forecasts = {
            "known_risky": np.full(7, 100.0),
            "unknown":     np.full(7, 50.0),
        }
        recs = adv.batch_recommend(forecasts, stocks_by_sku={"known_risky": 0.0})
        assert [r.sku for r in recs] == ["known_risky", "unknown"]
        assert recs[-1].stockout_risk is None
