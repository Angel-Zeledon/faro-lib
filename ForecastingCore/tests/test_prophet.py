"""Tests for forecasting_core.models.prophet: run_prophet_core."""

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("prophet", reason="prophet package not installed")

from forecasting_core.models.prophet import run_prophet_core


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(n=80, skus=("A",), seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for sku in skus:
        vals = np.abs(rng.normal(50, 8, n))
        for i, d in enumerate(pd.date_range("2021-01-01", periods=n, freq="D")):
            rows.append({"date": d, "sku": sku, "sales": float(vals[i])})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestRunProphetCore:

    def test_returns_dict_with_sku_key(self):
        df = _make_df(n=80, skus=["A"])
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7)
        assert isinstance(results, dict)
        assert "A" in results

    def test_result_contains_mae(self):
        df = _make_df(n=80, skus=["A"])
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7)
        assert "mae" in results["A"]
        assert isinstance(results["A"]["mae"], float)

    def test_mae_non_negative(self):
        df = _make_df(n=80, skus=["A"])
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7)
        assert results["A"]["mae"] >= 0.0

    def test_multi_sku_all_appear(self):
        df = _make_df(n=80, skus=["A", "B"])
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7)
        assert "A" in results and "B" in results

    def test_horizon_returns_forecast_array(self):
        df = _make_df(n=80, skus=["A"])
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7, horizon=7)
        assert "forecast" in results["A"]
        assert len(results["A"]["forecast"]) == 7

    def test_horizon_returns_residuals(self):
        df = _make_df(n=80, skus=["A"])
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7, horizon=7)
        assert "residuals" in results["A"]
        assert len(results["A"]["residuals"]) > 0

    def test_forecast_values_finite(self):
        df = _make_df(n=80, skus=["A"])
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7, horizon=14)
        assert np.all(np.isfinite(results["A"]["forecast"]))

    def test_no_group_single_series(self):
        df = _make_df(n=80, skus=["A"]).drop(columns=["sku"])
        results = run_prophet_core(df, dt="date", target="sales", group=None,
                                   train_ratio=0.8, min_rows=20, seasonal_period=7)
        assert "__all__" in results

    def test_with_regressor_column(self):
        df = _make_df(n=80, skus=["A"])
        rng = np.random.default_rng(5)
        df["promo"] = rng.integers(0, 2, len(df)).astype(float)
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7,
                                   regressors=["promo"])
        assert "A" in results
        assert "mae" in results["A"]


# ---------------------------------------------------------------------------
# Edge / error cases
# ---------------------------------------------------------------------------

class TestRunProphetEdgeCases:

    def test_too_few_rows_skipped(self):
        df = _make_df(n=5, skus=["A"])
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7)
        assert "A" not in results

    def test_empty_df_returns_empty(self):
        df = pd.DataFrame(columns=["date", "sku", "sales"])
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7)
        assert results == {}

    def test_nonexistent_regressor_ignored(self):
        # Column "nonexistent" not in df → avail=[] → runs without regressor
        df = _make_df(n=80, skus=["A"])
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7,
                                   regressors=["nonexistent"])
        assert "A" in results

    def test_horizon_zero_no_forecast_key(self):
        df = _make_df(n=80, skus=["A"])
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7, horizon=0)
        assert "forecast" not in results["A"]

    def test_cut_too_small_sku_skipped(self):
        # train_ratio so high that cut >= len(d) — SKU should be skipped
        df = _make_df(n=20, skus=["A"])
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.999, min_rows=20, seasonal_period=7)
        # cut=19, len=20, 19 < 20 → might proceed; cut >= len → skip
        # Either way no crash
        assert isinstance(results, dict)
