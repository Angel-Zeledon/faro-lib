"""
Both layers must crown the same model.

The engine picks each SKU's champion in `Pipeline._select_champions` and the
backend picks it again in `best_model_by_sku`. They answer the same question and
they are two lists of metric names, so they drift the moment someone adds a
metric to one of them.

That already happened. The engine gained `cost_horizon` — the only error column
measured the same way for every model family — while this layer still started at
`cost`. Measured on a real 13-SKU training run, the two then disagreed on 8:
the engine computed its inventory recommendations from one model, the semáforo
and the purchase quantity came from another, and the accuracy shown on screen
described a third. Nothing errored.

The list is duplicated rather than imported because the layering keeps
forecasting_core out of the inventory module. This test is the price of that
duplication.
"""

import pandas as pd
import pytest

from backend.inventory.service import _CHAMPION_METRICS, best_model_by_sku


def test_the_two_layers_walk_the_same_metric_order():
    from forecasting_core.evaluation.metrics import CHAMPION_METRIC_ORDER

    assert tuple(_CHAMPION_METRICS) == tuple(CHAMPION_METRIC_ORDER), (
        "the backend and the engine rank candidate models differently, so the "
        "model a SKU is planned from is not the model it is bought from"
    )


def test_the_engine_pipeline_uses_that_same_order():
    from forecasting_core.evaluation.metrics import CHAMPION_METRIC_ORDER
    from forecasting_core.pipelines.pipeline import Pipeline

    assert tuple(Pipeline.CHAMPION_METRICS) == tuple(CHAMPION_METRIC_ORDER)


class TestBothLayersAgreeOnRealShapedRows:
    """A metrics frame carrying every column, ranked by both implementations."""

    ROWS = [
        # cost_horizon disagrees with cost on purpose: the honest h-step number
        # favours prophet, the one-step number favours xgboost.
        {"sku": "A", "model": "xgboost", "type": "ml",
         "wape": 0.10, "mae": 2.0, "cost": 3.0, "cost_horizon": 9.0},
        {"sku": "A", "model": "prophet", "type": "stat",
         "wape": 0.18, "mae": 4.0, "cost": 7.0, "cost_horizon": 4.0},
        {"sku": "A", "model": "naive", "type": "baseline",
         "wape": 0.05, "mae": 1.0, "cost": 1.0, "cost_horizon": 1.0},
    ]

    def _engine_pick(self, rows):
        from forecasting_core.config.config import SessionConfig
        from forecasting_core.pipelines.pipeline import Pipeline

        pipeline = Pipeline(SessionConfig.from_dict({
            "columns": {"target": "d", "date": "dt", "group_keys": ["sku"]},
            "models": {"lightgbm": {}},
        }))
        return pipeline._select_champions(pd.DataFrame(rows), lambda s: str(s).strip())

    def test_same_pick_when_every_column_is_present(self):
        assert self._engine_pick(self.ROWS)["A"] == best_model_by_sku(self.ROWS)["A"]

    def test_the_pick_is_the_horizon_wide_winner(self):
        """Not the one-step winner — that is the comparison that was unfair."""
        assert best_model_by_sku(self.ROWS)["A"] == "prophet"

    def test_neither_layer_crowns_the_baseline(self):
        assert "naive" not in {self._engine_pick(self.ROWS)["A"],
                               best_model_by_sku(self.ROWS)["A"]}

    def test_same_pick_on_a_pre_cost_horizon_frame(self):
        """Older sessions must keep selecting, and keep agreeing."""
        rows = [{k: v for k, v in r.items() if k != "cost_horizon"} for r in self.ROWS]
        assert self._engine_pick(rows)["A"] == best_model_by_sku(rows)["A"] == "xgboost"

    def test_same_pick_on_a_frame_with_only_wape(self):
        rows = [{k: v for k, v in r.items() if k in ("sku", "model", "type", "wape")}
                for r in self.ROWS]
        assert self._engine_pick(rows)["A"] == best_model_by_sku(rows)["A"]
