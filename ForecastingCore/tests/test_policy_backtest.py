"""
The policy backtest: does the forecast produce better PURCHASES?

Accuracy metrics cannot answer that. A forecast that runs uniformly high posts a
respectable WAPE and doubles the inventory a tenant carries; a forecast that
runs uniformly low posts the same WAPE and empties the shelf. These tests pin
the simulator that tells the two apart.
"""

import numpy as np
import pytest

from forecasting_core.business.policy_backtest import (
    PolicyOutcome, aggregate, backtest_policy, naive_forecast, simulate_policy,
)


FLAT = [100.0] * 60


class TestMechanics:

    def test_a_perfect_forecast_with_stock_never_stocks_out(self):
        out = simulate_policy(demand=FLAT, forecast=FLAT, lead_time=7,
                              safety_stock=50.0, initial_stock=800.0)
        assert out.stockout_buckets == 0
        assert out.fill_rate == 1.0

    def test_no_stock_and_no_ordering_stocks_out_every_bucket(self):
        out = simulate_policy(demand=FLAT, forecast=[0.0] * 60, lead_time=0,
                              safety_stock=0.0, initial_stock=0.0)
        assert out.stockout_buckets == 60
        assert out.fill_rate == 0.0

    def test_unmet_demand_is_lost_not_backordered(self):
        """A distributor's customer buys elsewhere that day."""
        out = simulate_policy(demand=[100.0, 100.0], forecast=[0.0, 0.0],
                              lead_time=0, safety_stock=0.0, initial_stock=50.0)
        assert out.units_short == pytest.approx(150.0)
        assert out.total_demand == pytest.approx(200.0)

    def test_in_transit_stock_is_counted_before_reordering(self):
        """
        Without this the policy re-orders the same shortfall on every bucket of
        the lead time and buries the warehouse — inventory that the simulator
        invented, not that the decision caused.
        """
        out = simulate_policy(demand=FLAT, forecast=FLAT, lead_time=10,
                              safety_stock=0.0, initial_stock=0.0)
        # Total demand is 6000. Ignoring in-transit stock would re-order the
        # same shortfall on each of the 10 lead-time buckets, ordering several
        # times what the window actually consumes.
        assert out.units_ordered <= out.total_demand * 1.5, (
            f"ordered {out.units_ordered:.0f} units to cover "
            f"{out.total_demand:.0f} of demand — in-transit stock is not being "
            f"counted"
        )

    def test_lead_time_delays_arrival(self):
        out = simulate_policy(demand=[0.0] * 5 + [100.0], forecast=[100.0] * 6,
                              lead_time=3, safety_stock=0.0, initial_stock=0.0)
        assert out.units_ordered > 0
        assert out.stockout_buckets == 0, "the order had 5 buckets to arrive in"

    def test_moq_rounds_orders_up(self):
        out = simulate_policy(demand=FLAT, forecast=FLAT, lead_time=1,
                              safety_stock=0.0, initial_stock=0.0, moq=250)
        assert out.units_ordered % 250 == pytest.approx(0.0)

    def test_empty_demand_is_not_an_error(self):
        out = simulate_policy(demand=[], forecast=[1.0], lead_time=3,
                              safety_stock=0.0)
        assert out.n_buckets == 0
        assert out.fill_rate == 1.0


class TestReportedQuantities:

    def test_fill_rate_is_share_of_demand_served(self):
        out = PolicyOutcome(n_buckets=10, total_demand=1000.0, units_short=150.0)
        assert out.fill_rate == pytest.approx(0.85)

    def test_days_of_cover_is_inventory_over_daily_demand(self):
        out = PolicyOutcome(n_buckets=10, total_demand=1000.0, avg_inventory=300.0)
        assert out.days_of_cover == pytest.approx(3.0)

    def test_capital_is_none_without_a_unit_cost(self):
        """Absent rather than zero: zero would read as 'no capital tied up'."""
        assert PolicyOutcome(n_buckets=5, avg_inventory=100.0).capital_tied_up is None

    def test_capital_uses_the_unit_cost(self):
        out = PolicyOutcome(n_buckets=5, avg_inventory=100.0, unit_cost=3.5)
        assert out.capital_tied_up == pytest.approx(350.0)


class TestItDetectsWhatAccuracyCannot:

    def _seasonal(self, n=120):
        rng = np.random.default_rng(11)
        base = 100.0 + 60.0 * np.sin(2 * np.pi * np.arange(n) / 7.0)
        return np.clip(base * rng.normal(1.0, 0.05, n), 0, None)

    def test_a_biased_high_forecast_ties_up_capital(self):
        demand = self._seasonal()
        inflated = demand * 1.30
        good = simulate_policy(demand, demand, lead_time=7, safety_stock=30.0,
                               initial_stock=700.0, unit_cost=2.0)
        biased = simulate_policy(demand, inflated, lead_time=7, safety_stock=30.0,
                                 initial_stock=700.0, unit_cost=2.0)
        assert biased.avg_inventory > good.avg_inventory * 1.1, (
            "a forecast 30% high must show up as inventory, and accuracy "
            "metrics alone would not have shown it"
        )
        assert biased.capital_tied_up > good.capital_tied_up

    def test_a_biased_low_forecast_empties_the_shelf(self):
        demand = self._seasonal()
        starved = demand * 0.65
        good = simulate_policy(demand, demand, lead_time=7, safety_stock=30.0,
                               initial_stock=700.0)
        biased = simulate_policy(demand, starved, lead_time=7, safety_stock=30.0,
                                 initial_stock=700.0)
        assert biased.stockout_buckets > good.stockout_buckets
        assert biased.fill_rate < good.fill_rate

    def test_both_biases_can_share_the_same_accuracy_but_not_the_same_outcome(self):
        """
        The argument for this whole module, asserted. Two forecasts with nearly
        identical WAPE, opposite business consequences.
        """
        from forecasting_core.evaluation.metrics import wape

        demand = self._seasonal()
        high, low = demand * 1.25, demand * 0.75
        assert wape(demand, high) == pytest.approx(wape(demand, low), rel=0.05)

        out_high = simulate_policy(demand, high, 7, 30.0, 700.0)
        out_low = simulate_policy(demand, low, 7, 30.0, 700.0)
        assert out_high.avg_inventory > out_low.avg_inventory
        assert out_low.stockout_buckets > out_high.stockout_buckets


class TestBaselineComparison:

    def test_naive_forecast_repeats_the_last_observation(self):
        assert naive_forecast([1.0, 2.0, 7.0], 4).tolist() == [7.0] * 4

    def test_naive_forecast_is_never_negative(self):
        assert naive_forecast([-5.0], 3).tolist() == [0.0] * 3

    def test_both_policies_face_identical_conditions(self):
        """Every difference has to be attributable to the forecast alone."""
        rng = np.random.default_rng(5)
        n = 120
        demand = np.clip(100.0 + 60.0 * np.sin(2 * np.pi * np.arange(n) / 7.0)
                         + rng.normal(0, 5, n), 0, None)
        comparison = backtest_policy(
            demand=demand, forecast=demand, history=demand[:30],
            lead_time=7, safety_stock=25.0, initial_stock=600.0,
            unit_cost=2.0, model_name="global_lgbm",
        )
        assert comparison.model.total_demand == comparison.baseline.total_demand
        assert comparison.model.n_buckets == comparison.baseline.n_buckets

    def test_a_good_forecast_beats_naive_on_a_growing_series(self):
        """
        Where naive genuinely fails: a trend. Repeating the last observation
        under-orders every bucket, and the gap compounds over the lead time.

        Note what is NOT claimed. On a stationary seasonal series naive can post
        a HIGHER fill rate than a perfect forecast, simply by happening to
        repeat a peak and over-ordering — it buys the service level with
        inventory. That is why fill rate alone is not the headline and the
        comparison reports the inventory alongside it.
        """
        n = 140
        demand = 50.0 + 1.5 * np.arange(n)          # steady growth
        comparison = backtest_policy(
            demand=demand, forecast=demand, history=demand[:30],
            lead_time=5, safety_stock=20.0, initial_stock=400.0, unit_cost=2.0,
        )
        payload = comparison.to_dict()
        assert payload["fill_rate_gain"] > 0, (
            f"a perfect forecast must beat 'repeat the last value' on a trend: "
            f"{payload['policy']['fill_rate']} vs {payload['baseline']['fill_rate']}"
        )
        assert payload["stockouts_avoided"] > 0

    def test_comparison_payload_has_what_the_screen_needs(self):
        comparison = backtest_policy(
            demand=FLAT, forecast=FLAT, history=FLAT[:10], lead_time=5,
            safety_stock=20.0, initial_stock=600.0, unit_cost=1.5,
            model_name="global_lgbm",
        )
        payload = comparison.to_dict()
        for key in ("model", "policy", "baseline", "fill_rate_gain",
                    "stockouts_avoided", "inventory_delta", "capital_freed"):
            assert key in payload
        assert payload["model"] == "global_lgbm"


class TestAggregate:

    def _comparison(self, demand, forecast):
        # initial_stock left to the default (lead-time demand), so a SKU selling
        # 1000/day does not start the window with a fixed 400 units and post a
        # startup shortfall that has nothing to do with its forecast.
        return backtest_policy(demand=demand, forecast=forecast,
                               history=demand[:10], lead_time=5,
                               safety_stock=10.0, unit_cost=1.0)

    def test_empty_input(self):
        assert aggregate([]) == {}

    def test_fill_rate_is_weighted_by_demand(self):
        """
        A stockout on the SKU that carries the business is not the same event as
        one on a SKU that sells twice a month. An unweighted mean says it is.
        """
        big = np.full(60, 1000.0)
        small = np.full(60, 1.0)
        comparisons = [
            self._comparison(big, big),                 # served perfectly
            self._comparison(small, np.zeros(60)),      # starved
        ]
        result = aggregate(comparisons)
        assert result["n_series"] == 2
        assert result["fill_rate"] > 0.98, (
            "the tiny starved SKU must not drag the headline down as if it "
            "were half the business"
        )

    def test_reports_both_sides_of_the_tradeoff(self):
        comparisons = [self._comparison(np.full(60, 100.0), np.full(60, 100.0))]
        result = aggregate(comparisons)
        for key in ("fill_rate", "baseline_fill_rate", "stockout_buckets",
                    "baseline_stockout_buckets", "stockouts_avoided",
                    "avg_inventory", "baseline_avg_inventory",
                    "capital_tied_up", "baseline_capital_tied_up"):
            assert key in result
