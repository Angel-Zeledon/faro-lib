"""get_metrics() on a run where every SKU-model pair failed.

Series that are too short, all-zero, or one row per SKU make the pipeline skip
every pair and leave an EMPTY metrics frame. Grouping that frame raised
KeyError('model'), which reached the user verbatim as "El entrenamiento falló:
'model'". An empty run is a legitimate outcome — the caller decides what to say
about it, the engine must not explode.
"""

import pandas as pd
import pytest

from forecasting_core.engine import ForecastEngine


def _engine_with_metrics(df: pd.DataFrame) -> ForecastEngine:
    engine = ForecastEngine.__new__(ForecastEngine)
    engine._metrics_df = df
    engine._fitted_models = {}
    # get_metrics() guards on training having happened; this test is about what
    # comes after, so neutralize just that check.
    engine._require_trained = lambda: None  # type: ignore[method-assign]
    return engine


class TestEmptyMetrics:
    def test_completely_empty_frame_does_not_raise(self):
        out = _engine_with_metrics(pd.DataFrame()).get_metrics()
        assert out["rows"] == []
        assert out["by_model"] == {}

    def test_frame_with_rows_but_no_model_column_does_not_raise(self):
        df = pd.DataFrame([{"sku": "A", "mae": 1.0}])
        out = _engine_with_metrics(df).get_metrics()
        assert out["by_model"] == {}
        assert len(out["rows"]) == 1


class TestPopulatedMetricsStillAggregate:
    def test_by_model_averages_are_unchanged(self):
        df = pd.DataFrame([
            {"model": "lightgbm", "sku": "A", "mae": 2.0, "rmse": 3.0,
             "wape": 0.2, "bias": 0.0, "mape": 0.1, "smape": 0.1},
            {"model": "lightgbm", "sku": "B", "mae": 4.0, "rmse": 5.0,
             "wape": 0.4, "bias": 0.0, "mape": 0.3, "smape": 0.3},
        ])
        out = _engine_with_metrics(df).get_metrics()
        assert out["by_model"]["lightgbm"]["avg_mae"] == pytest.approx(3.0)
        assert out["by_model"]["lightgbm"]["avg_wape"] == pytest.approx(0.3)
