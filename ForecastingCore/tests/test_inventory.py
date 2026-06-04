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

    # ⚠️ CRITICAL FIX REQUIRED: service_level=1.0 causes ppf(1.0)=inf
    def test_service_level_boundary_1_causes_inf(self):
        with pytest.raises(Exception):
            adv = InventoryAdvisor(service_level=1.0)
            adv.recommend("S", np.full(7, 10.0))

    # ⚠️ CRITICAL FIX REQUIRED: service_level=0.0 causes ppf(0.0)=-inf
    def test_service_level_boundary_0_causes_neg_inf(self):
        with pytest.raises(Exception):
            adv = InventoryAdvisor(service_level=0.0)
            adv.recommend("S", np.full(7, 10.0))


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

    def test_missing_stock_defaults_to_zero(self):
        adv = InventoryAdvisor()
        recs = adv.batch_recommend({"A": np.full(7, 50.0)})
        assert recs[0].details["current_stock"] == 0.0
