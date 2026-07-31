"""
Which model drives each SKU's purchase recommendation.

Selection used to be `mae.idxmin()`. MAE is symmetric, so it treats "ten units
short" and "ten units spare" as the same mistake. For a distributor they are
not, and the model that wins on MAE can be the one that stocks you out.
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.config.config import SessionConfig
from forecasting_core.evaluation.metrics import (
    asymmetric_cost, evaluate_all, mae, pinball_loss,
)
from forecasting_core.pipelines.pipeline import Pipeline


def _pipeline():
    return Pipeline(SessionConfig.from_dict({
        "columns": {"target": "demand", "date": "date", "group_keys": ["sku"]},
        "models": {"lightgbm": {}},
    }))


class TestAsymmetricCost:

    def test_a_shortfall_costs_more_than_a_surplus(self):
        short = asymmetric_cost([100], [90], stockout_multiplier=3.0)
        spare = asymmetric_cost([100], [110], stockout_multiplier=3.0)
        assert short == pytest.approx(30.0)
        assert spare == pytest.approx(10.0)
        assert short > spare

    def test_mae_cannot_tell_them_apart(self):
        """The reason the metric had to change, stated as a test."""
        assert mae([100], [90]) == mae([100], [110])

    def test_perfect_forecast_costs_nothing(self):
        assert asymmetric_cost([10, 20], [10, 20]) == 0.0

    def test_multiplier_of_one_reduces_to_mae(self):
        y, yhat = [10, 20, 30], [12, 17, 33]
        assert asymmetric_cost(y, yhat, stockout_multiplier=1.0) == pytest.approx(mae(y, yhat))

    def test_evaluate_all_carries_it(self):
        assert "cost" in evaluate_all([1, 2, 3], [1, 2, 4])


class TestPinballLoss:

    def test_high_quantile_punishes_under_forecasting(self):
        """A q95 forecast exists to sit ABOVE demand most of the time."""
        under = pinball_loss([100], [80], quantile=0.95)
        over = pinball_loss([100], [120], quantile=0.95)
        assert under > over

    def test_low_quantile_punishes_over_forecasting(self):
        under = pinball_loss([100], [80], quantile=0.05)
        over = pinball_loss([100], [120], quantile=0.05)
        assert over > under

    def test_median_quantile_is_symmetric(self):
        assert pinball_loss([100], [80], 0.5) == pytest.approx(pinball_loss([100], [120], 0.5))

    def test_zero_at_a_perfect_forecast(self):
        assert pinball_loss([50, 60], [50, 60], 0.9) == 0.0


class TestChampionSelection:

    def _metrics(self, rows):
        return pd.DataFrame(rows)

    def test_picks_the_cheaper_model_not_the_lower_mae(self):
        """
        The concrete regression. `timid` has the better MAE but gets there by
        under-forecasting; `generous` errs high by the same amount. On this
        business the second one is the right pick.
        """
        y = np.array([100.0] * 20)
        timid = y - 12          # always 12 short
        generous = y + 14       # always 14 over

        timid_m = evaluate_all(y, timid)
        generous_m = evaluate_all(y, generous)
        assert timid_m["mae"] < generous_m["mae"], "fixture assumption"

        metrics = self._metrics([
            {"sku": "SKU_A", "model": "timid", **timid_m},
            {"sku": "SKU_A", "model": "generous", **generous_m},
        ])
        champions = _pipeline()._select_champions(metrics, lambda s: str(s).strip())
        assert champions["SKU_A"] == "generous"

    def test_falls_back_to_mae_when_cost_is_absent(self):
        """A metrics frame from before `cost` existed must still select."""
        metrics = self._metrics([
            {"sku": "SKU_A", "model": "a", "mae": 5.0},
            {"sku": "SKU_A", "model": "b", "mae": 2.0},
        ])
        champions = _pipeline()._select_champions(metrics, lambda s: str(s).strip())
        assert champions["SKU_A"] == "b"

    def test_one_champion_per_sku(self):
        metrics = self._metrics([
            {"sku": "A", "model": "m1", "cost": 5.0},
            {"sku": "A", "model": "m2", "cost": 2.0},
            {"sku": "B", "model": "m1", "cost": 1.0},
            {"sku": "B", "model": "m2", "cost": 9.0},
        ])
        champions = _pipeline()._select_champions(metrics, lambda s: str(s).strip())
        assert champions == {"A": "m2", "B": "m1"}

    def test_models_with_no_score_are_ignored(self):
        metrics = self._metrics([
            {"sku": "A", "model": "broken", "cost": np.nan},
            {"sku": "A", "model": "works", "cost": 7.0},
        ])
        champions = _pipeline()._select_champions(metrics, lambda s: str(s).strip())
        assert champions["A"] == "works"

    def test_empty_metrics_selects_nothing(self):
        assert _pipeline()._select_champions(pd.DataFrame(), lambda s: s) == {}

    def test_the_global_model_can_win(self):
        """It has to be allowed to compete on the same table as the rest."""
        metrics = self._metrics([
            {"sku": "A", "model": "lightgbm", "cost": 8.0},
            {"sku": "A", "model": "global_lgbm", "cost": 3.0},
        ])
        champions = _pipeline()._select_champions(metrics, lambda s: str(s).strip())
        assert champions["A"] == "global_lgbm"
