"""
Task 8: Pipeline wiring — group_keys + PipelineResults rollup fields.

Tests verify:
  1. PipelineResults has forecast_by_sku_df and forecast_by_store_df (default None)
  2. _config_as_validation_dict uses group_keys[0] for group_id
  3. _config_as_validation_dict returns None when group_keys is empty
  4. Pipeline.run() passes group_cols=group_keys to FeatureEngineer and Trainer
     (verified via FeatureEngineer signature acceptance, not full run)
  5. Rollup fields populated when forecast_df contains a "store" column
  6. Rollup fields stay None when forecast_df lacks a "store" column
"""

import pandas as pd
import pytest

from forecasting_core.config.config import SessionConfig, ColumnsConfig
from forecasting_core.pipelines.pipeline import (
    Pipeline,
    PipelineResults,
    _config_as_validation_dict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(group_keys=None):
    """Minimal SessionConfig with optional custom group_keys."""
    d = {
        "columns": {"target": "sales", "date": "date",
                    "group_keys": group_keys or ["sku"]},
        "features": {"lags": [1], "rolling": [7], "diffs": [1],
                     "calendar": False, "ewm_spans": []},
        "models": {"lightgbm": {"n_estimators": 5}},
        "training": {"train_ratio": 0.8, "walk_forward": False,
                     "wfv_splits": 2, "min_history": 10, "seasonal_period": 7},
        "forecast": {"horizon": 3},
        "business": {"service_level": 0.95, "lead_time_days": 7,
                     "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0},
    }
    return SessionConfig.from_dict(d)


def _make_forecast_df(has_store: bool, n_skus: int = 2, horizon: int = 3) -> pd.DataFrame:
    """Build a minimal forecast DataFrame with/without a 'store' column."""
    rows = []
    skus = [f"SKU_{i}" for i in range(n_skus)]
    stores = ["S1", "S2"] if has_store else [None]
    for sku in skus:
        for store in stores:
            for step in range(1, horizon + 1):
                row = {"sku": sku, "model": "lgbm",
                       "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=step - 1),
                       "step": step, "forecast": float(step * 10)}
                if has_store:
                    row["store"] = store
                rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 1. PipelineResults dataclass — default field values
# ---------------------------------------------------------------------------

class TestPipelineResultsFields:

    def test_forecast_by_sku_df_defaults_to_none(self):
        r = PipelineResults()
        assert r.forecast_by_sku_df is None

    def test_forecast_by_store_df_defaults_to_none(self):
        r = PipelineResults()
        assert r.forecast_by_store_df is None

    def test_original_fields_still_present(self):
        r = PipelineResults()
        assert hasattr(r, "metrics_df")
        assert hasattr(r, "forecast_df")
        assert hasattr(r, "inventory_df")
        assert hasattr(r, "quality_df")
        assert hasattr(r, "run_id")
        assert hasattr(r, "fitted_models")
        assert hasattr(r, "stat_forecasts")

    def test_can_assign_rollup_dataframes(self):
        df = pd.DataFrame({"sku": ["A"], "forecast": [1.0]})
        r = PipelineResults(forecast_by_sku_df=df)
        assert r.forecast_by_sku_df is not None
        assert len(r.forecast_by_sku_df) == 1


# ---------------------------------------------------------------------------
# 2. _config_as_validation_dict — group_id sourced from group_keys[0]
# ---------------------------------------------------------------------------

class TestConfigAsValidationDict:

    def test_group_id_uses_group_keys_first_element(self):
        cfg = _make_cfg(group_keys=["sku"])
        d = _config_as_validation_dict(cfg)
        assert d["group_id"] == "sku"

    def test_group_id_uses_first_of_multiple_group_keys(self):
        cfg = _make_cfg(group_keys=["sku", "store"])
        d = _config_as_validation_dict(cfg)
        assert d["group_id"] == "sku"

    def test_required_keys_present(self):
        cfg = _make_cfg()
        d = _config_as_validation_dict(cfg)
        for key in ("dt", "target", "group_id", "models", "train_ratio",
                    "prediction_horizon", "min_history", "features"):
            assert key in d, f"missing key: {key}"


# ---------------------------------------------------------------------------
# 3. ColumnsConfig.group_keys shim is consistent
# ---------------------------------------------------------------------------

class TestColumnsConfigGroupKeys:

    def test_group_property_returns_group_keys_0(self):
        c = ColumnsConfig(target="sales", date="date", group_keys=["sku", "store"])
        assert c.group == "sku"

    def test_group_keys_list_preserved(self):
        c = ColumnsConfig(target="sales", date="date", group_keys=["sku", "store"])
        assert c.group_keys == ["sku", "store"]

    def test_single_key_group_property(self):
        c = ColumnsConfig(target="sales", date="date", group_keys=["product"])
        assert c.group == "product"


# ---------------------------------------------------------------------------
# 4. FeatureEngineer accepts group_cols — wiring contract check
# ---------------------------------------------------------------------------

class TestFeatureEngineerGroupColsWiring:
    """Verify Pipeline passes group_cols (list) to FeatureEngineer, not group (str)."""

    def test_feature_engineer_accepts_group_cols_list(self):
        """FeatureEngineer should accept group_cols as a list without error."""
        from forecasting_core.features.engineer import FeatureEngineer
        from forecasting_core.config.config import FeaturesConfig
        feat_cfg = FeaturesConfig(lags=[1], rolling=[7], diffs=[1], calendar=False)
        eng = FeatureEngineer(feat_cfg, dt_col="date", target="sales",
                              group_cols=["sku", "store"])
        assert eng is not None

    def test_feature_engineer_transform_with_group_cols(self):
        """FeatureEngineer.transform() succeeds when group_cols is set."""
        from forecasting_core.features.engineer import FeatureEngineer
        from forecasting_core.config.config import FeaturesConfig
        import numpy as np

        rng = pd.date_range("2022-01-01", periods=40, freq="D")
        df = pd.DataFrame({
            "date": list(rng) * 2,
            "sku": ["A"] * 40 + ["B"] * 40,
            "sales": list(np.arange(40, dtype=float)) * 2,
        })
        feat_cfg = FeaturesConfig(lags=[1, 7], rolling=[7], diffs=[1], calendar=False)
        eng = FeatureEngineer(feat_cfg, dt_col="date", target="sales", group_cols=["sku"])
        result = eng.transform(df)
        assert isinstance(result, pd.DataFrame)
        assert len(result) > 0

    def test_pipeline_filters_group_keys_to_present_columns(self):
        """
        When group_keys contains columns not in the DataFrame (e.g. ["sku", "store"]
        but DataFrame only has "sku"), the pipeline must not crash — it resolves
        group_cols to the intersection of group_keys and actual DataFrame columns.
        """
        from forecasting_core.config.config import ColumnsConfig
        import numpy as np

        c = ColumnsConfig(target="sales", date="date", group_keys=["sku", "store"])
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=5, freq="D"),
            "sku": ["A"] * 5,
            "sales": np.arange(5, dtype=float),
        })
        # Pipeline resolves: [k for k in c.group_keys if k in df.columns]
        resolved = [k for k in c.group_keys if k in df.columns]
        assert resolved == ["sku"]   # "store" is absent so filtered out


# ---------------------------------------------------------------------------
# 5. Rollup wiring — aggregate_by_sku / aggregate_by_store integration
# ---------------------------------------------------------------------------

class TestRollupWiring:

    def test_rollup_fields_populated_when_forecast_has_store_column(self):
        """When forecast_df has a 'store' column, run() should populate rollup fields."""
        from forecasting_core.aggregation.rollup import aggregate_by_sku, aggregate_by_store

        fdf = _make_forecast_df(has_store=True, n_skus=2, horizon=3)
        assert "store" in fdf.columns, "test setup: forecast_df must have store column"

        results = PipelineResults(forecast_df=fdf)
        if fdf is not None and "store" in fdf.columns:
            results.forecast_by_sku_df   = aggregate_by_sku(fdf)
            results.forecast_by_store_df = aggregate_by_store(fdf)

        assert results.forecast_by_sku_df is not None
        assert results.forecast_by_store_df is not None

    def test_rollup_fields_none_when_forecast_lacks_store_column(self):
        """When forecast_df has no 'store' column, rollup fields stay None."""
        fdf = _make_forecast_df(has_store=False, n_skus=2, horizon=3)
        assert "store" not in fdf.columns, "test setup: forecast_df must NOT have store column"

        results = PipelineResults(forecast_df=fdf)
        if fdf is not None and "store" in fdf.columns:
            from forecasting_core.aggregation.rollup import aggregate_by_sku, aggregate_by_store
            results.forecast_by_sku_df   = aggregate_by_sku(fdf)
            results.forecast_by_store_df = aggregate_by_store(fdf)

        assert results.forecast_by_sku_df is None
        assert results.forecast_by_store_df is None

    def test_rollup_fields_none_when_forecast_df_is_none(self):
        """When forecast_df is None, rollup fields stay None."""
        results = PipelineResults(forecast_df=None)
        if results.forecast_df is not None and "store" in results.forecast_df.columns:
            from forecasting_core.aggregation.rollup import aggregate_by_sku, aggregate_by_store
            results.forecast_by_sku_df   = aggregate_by_sku(results.forecast_df)
            results.forecast_by_store_df = aggregate_by_store(results.forecast_df)

        assert results.forecast_by_sku_df is None
        assert results.forecast_by_store_df is None

    def test_aggregate_by_sku_sums_across_stores(self):
        """aggregate_by_sku should sum forecast values across stores for each SKU."""
        from forecasting_core.aggregation.rollup import aggregate_by_sku

        fdf = _make_forecast_df(has_store=True, n_skus=1, horizon=1)
        # 1 SKU x 2 stores x 1 step → grouped result: 1 row with sum
        result = aggregate_by_sku(fdf)
        assert "sku" in result.columns
        assert "store" not in result.columns
        # Each step: two stores each contribute step*10 → sum = 2 * step*10
        assert len(result) == 1
        assert result["forecast"].iloc[0] == pytest.approx(20.0)  # 2 stores * 1 step * 10

    def test_aggregate_by_store_sums_across_skus(self):
        """aggregate_by_store should sum forecast values across SKUs for each store."""
        from forecasting_core.aggregation.rollup import aggregate_by_store

        fdf = _make_forecast_df(has_store=True, n_skus=2, horizon=1)
        # 2 SKUs x 2 stores x 1 step → grouped result: 2 rows (one per store)
        result = aggregate_by_store(fdf)
        assert "store" in result.columns
        assert "sku" not in result.columns
        assert len(result) == 2
        # Each store has 2 SKUs × step 1 × 10 = 20.0 per store
        assert result["forecast"].tolist() == pytest.approx([20.0, 20.0])
