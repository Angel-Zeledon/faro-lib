"""
A baseline must never be crowned champion — and a SKU must never vanish quietly.

Baselines exist to be beaten. They were nonetheless in the champion race, and a
baseline that won produced no forecast rows downstream, so `Pipeline._inventory`
dropped the SKU with no recommendation, no warning and no log line. On screen a
SKU with no recommendation is indistinguishable from one that is well stocked.

Making the local models' scores honest (they are now graded h-step-ahead rather
than one step) makes baselines win more often, which turned a latent bug into a
likely one. These tests pin both halves of the fix: baselines are out of the
race, and every remaining way a SKU can drop out leaves a trace.
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.config.config import SessionConfig
from forecasting_core.pipelines.pipeline import Pipeline


def _pipeline():
    return Pipeline(SessionConfig.from_dict({
        "columns": {"target": "demand", "date": "date", "group_keys": ["sku"]},
        "models": {"lightgbm": {}},
    }))


def _norm(x):
    return str(x).strip()


class TestBaselinesAreNotCandidates:

    def test_a_winning_baseline_is_not_crowned(self):
        """The exact defect: naive scores best, and used to be selected."""
        metrics = pd.DataFrame([
            {"sku": "A", "model": "naive", "type": "baseline", "cost": 1.0},
            {"sku": "A", "model": "lightgbm", "type": "ml", "cost": 5.0},
            {"sku": "A", "model": "arima", "type": "stat", "cost": 7.0},
        ])
        champions = _pipeline()._select_champions(metrics, _norm)
        assert champions["A"] == "lightgbm", (
            "a naive forecast was selected to buy from"
        )

    def test_every_baseline_family_is_excluded(self):
        metrics = pd.DataFrame([
            {"sku": "A", "model": "naive", "type": "baseline", "cost": 0.1},
            {"sku": "A", "model": "seasonal_naive", "type": "baseline", "cost": 0.2},
            {"sku": "A", "model": "historical_avg", "type": "baseline", "cost": 0.3},
            {"sku": "A", "model": "lightgbm", "type": "ml", "cost": 9.0},
        ])
        assert _pipeline()._select_champions(metrics, _norm)["A"] == "lightgbm"

    def test_the_best_real_model_still_wins_normally(self):
        metrics = pd.DataFrame([
            {"sku": "A", "model": "naive", "type": "baseline", "cost": 9.0},
            {"sku": "A", "model": "lightgbm", "type": "ml", "cost": 5.0},
            {"sku": "A", "model": "global_lgbm", "type": "global", "cost": 2.0},
        ])
        assert _pipeline()._select_champions(metrics, _norm)["A"] == "global_lgbm"

    def test_a_sku_with_only_baselines_gets_no_champion(self):
        """Nothing real to buy from — better absent than a naive forecast."""
        metrics = pd.DataFrame([
            {"sku": "A", "model": "naive", "type": "baseline", "cost": 1.0},
        ])
        assert _pipeline()._select_champions(metrics, _norm) == {}

    def test_frames_without_a_type_column_still_select(self):
        """Metrics frames produced before `type` existed must keep working."""
        metrics = pd.DataFrame([
            {"sku": "A", "model": "m1", "cost": 5.0},
            {"sku": "A", "model": "m2", "cost": 2.0},
        ])
        assert _pipeline()._select_champions(metrics, _norm)["A"] == "m2"


class TestTheLossIsRecorded:
    """Excluding baselines from the race must not throw the finding away."""

    def test_a_sku_no_model_can_beat_is_reported(self):
        metrics = pd.DataFrame([
            {"sku": "A", "model": "naive", "type": "baseline", "cost": 1.0},
            {"sku": "A", "model": "lightgbm", "type": "ml", "cost": 5.0},
        ])
        pipeline = _pipeline()
        pipeline._select_champions(metrics, _norm)

        reported = pipeline._outperformed_by_baseline
        assert len(reported) == 1
        assert reported[0]["sku"] == "A"
        assert reported[0]["baseline"] == "naive"
        assert reported[0]["model"] == "lightgbm"

    def test_a_sku_the_models_win_is_not_reported(self):
        metrics = pd.DataFrame([
            {"sku": "A", "model": "naive", "type": "baseline", "cost": 9.0},
            {"sku": "A", "model": "lightgbm", "type": "ml", "cost": 2.0},
        ])
        pipeline = _pipeline()
        pipeline._select_champions(metrics, _norm)
        assert pipeline._outperformed_by_baseline == []

    def test_the_record_resets_between_selections(self):
        """A stale list would report last run's SKUs on this run."""
        losing = pd.DataFrame([
            {"sku": "A", "model": "naive", "type": "baseline", "cost": 1.0},
            {"sku": "A", "model": "lightgbm", "type": "ml", "cost": 5.0},
        ])
        winning = pd.DataFrame([
            {"sku": "B", "model": "naive", "type": "baseline", "cost": 9.0},
            {"sku": "B", "model": "lightgbm", "type": "ml", "cost": 2.0},
        ])
        pipeline = _pipeline()
        pipeline._select_champions(losing, _norm)
        assert pipeline._outperformed_by_baseline
        pipeline._select_champions(winning, _norm)
        assert pipeline._outperformed_by_baseline == []


class TestSilentDropsLeaveATrace:

    def test_a_champion_with_no_forecast_rows_is_recorded(self, caplog):
        """
        The remaining way a SKU can end a run with no recommendation. Pooling
        every model's forecast instead would answer with a number belonging to
        no model, so dropping is right — dropping SILENTLY is not.
        """
        import logging

        pipeline = _pipeline()
        c = pipeline.config.columns
        b = pipeline.config.business

        # The champion by cost is `ghost`, which has no rows in forecast_df.
        metrics = pd.DataFrame([
            {"sku": "A", "model": "ghost", "type": "ml", "cost": 1.0},
            {"sku": "A", "model": "lightgbm", "type": "ml", "cost": 8.0},
        ])
        forecast = pd.DataFrame([
            {"sku": "A", "model": "lightgbm", "step": i + 1, "forecast": 10.0}
            for i in range(5)
        ])
        df = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=5, freq="D"),
            "sku": "A", "demand": [10.0] * 5,
        })

        with caplog.at_level(logging.WARNING):
            pipeline._inventory(df, c, b, horizon=5,
                                forecast_df=forecast, metrics_df=metrics)

        assert pipeline._skipped_no_forecast == [{"sku": "A", "model": "ghost"}]
        assert any("no forecast rows" in r.message for r in caplog.records), (
            "the SKU was dropped without a word in the log"
        )
