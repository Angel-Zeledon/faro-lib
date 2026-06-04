"""Tests for forecasting_core.models.sarimax: run_sarimax_core, _safe_order."""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.models.sarimax import run_sarimax_core, _safe_order


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(n=80, skus=("A",), seed=0, add_exog=False):
    rng = np.random.default_rng(seed)
    rows = []
    for sku in skus:
        vals = np.abs(rng.normal(50, 8, n))
        for i, d in enumerate(pd.date_range("2021-01-01", periods=n, freq="D")):
            row = {"date": d, "sku": sku, "sales": float(vals[i])}
            if add_exog:
                row["promo"] = float(rng.integers(0, 2))
            rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# _safe_order
# ---------------------------------------------------------------------------

class TestSafeOrder:

    def test_tuple_input_returned_as_tuple(self):
        assert _safe_order((1, 1, 1), (0, 0, 0)) == (1, 1, 1)

    def test_list_input_converted(self):
        assert _safe_order([1, 1, 1], (0, 0, 0)) == (1, 1, 1)

    def test_string_input_parsed(self):
        assert _safe_order("1,1,1", (0, 0, 0)) == (1, 1, 1)

    def test_invalid_input_returns_default(self):
        assert _safe_order("bad-input", (1, 0, 1)) == (1, 0, 1)

    def test_none_input_returns_default(self):
        assert _safe_order(None, (2, 1, 2)) == (2, 1, 2)

    def test_non_integer_values_return_default(self):
        assert _safe_order(["a", "b", "c"], (1, 0, 1)) == (1, 0, 1)


# ---------------------------------------------------------------------------
# run_sarimax_core — happy path
# ---------------------------------------------------------------------------

class TestRunSarimaxCore:

    def _run(self, n=80, skus=("A",), horizon=0, exog=False, **kwargs):
        df = _make_df(n=n, skus=skus, add_exog=exog)
        exog_cols = ["promo"] if exog else None
        return run_sarimax_core(
            df, dt="date", target="sales", group="sku",
            train_ratio=0.8, min_rows=20, seasonal_period=7,
            exog_cols=exog_cols, order=(1, 0, 1),
            seasonal_order=(0, 0, 0, 7),
            horizon=horizon, **kwargs
        )

    def test_returns_dict_with_sku_key(self):
        results = self._run()
        assert "A" in results

    def test_result_contains_mae(self):
        results = self._run()
        assert "mae" in results["A"]
        assert isinstance(results["A"]["mae"], float)

    def test_mae_non_negative(self):
        results = self._run()
        assert results["A"]["mae"] >= 0.0

    def test_multi_sku_all_appear(self):
        results = self._run(skus=("A", "B"))
        assert "A" in results and "B" in results

    def test_horizon_returns_forecast(self):
        results = self._run(horizon=7)
        assert "forecast" in results["A"]
        assert len(results["A"]["forecast"]) == 7

    def test_horizon_returns_residuals(self):
        results = self._run(horizon=7)
        assert "residuals" in results["A"]
        assert len(results["A"]["residuals"]) > 0

    def test_forecast_values_finite(self):
        results = self._run(horizon=7)
        assert np.all(np.isfinite(results["A"]["forecast"]))

    def test_horizon_zero_no_forecast_key(self):
        results = self._run(horizon=0)
        assert "forecast" not in results["A"]

    def test_no_group_single_series(self):
        df = _make_df(n=80, skus=["A"]).drop(columns=["sku"])
        results = run_sarimax_core(
            df, dt="date", target="sales", group=None,
            train_ratio=0.8, min_rows=20, seasonal_period=7,
            order=(1, 0, 1), seasonal_order=(0, 0, 0, 7), horizon=0,
        )
        assert "__all__" in results

    def test_with_exogenous_columns(self):
        results = self._run(exog=True, horizon=7)
        assert "A" in results
        assert "mae" in results["A"]

    def test_forecast_length_matches_horizon(self):
        for h in [3, 7, 14]:
            results = self._run(horizon=h)
            assert len(results["A"]["forecast"]) == h


# ---------------------------------------------------------------------------
# Edge / error cases
# ---------------------------------------------------------------------------

class TestRunSarimaxEdgeCases:

    def test_too_few_rows_skipped(self):
        df = _make_df(n=5, skus=["A"])
        results = run_sarimax_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7,
                                   order=(1, 0, 1), seasonal_order=(0, 0, 0, 7))
        assert "A" not in results

    def test_empty_df_returns_empty(self):
        df = pd.DataFrame(columns=["date", "sku", "sales"])
        results = run_sarimax_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7,
                                   order=(1, 0, 1), seasonal_order=(0, 0, 0, 7))
        assert results == {}

    def test_nonexistent_exog_cols_ignored(self):
        df = _make_df(n=80, skus=["A"])
        results = run_sarimax_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7,
                                   exog_cols=["nonexistent"],
                                   order=(1, 0, 1), seasonal_order=(0, 0, 0, 7))
        # nonexistent filtered out → runs as SARIMA
        assert "A" in results

    def test_order_as_string_parsed(self):
        df = _make_df(n=80, skus=["A"])
        results = run_sarimax_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7,
                                   order="1,0,1", seasonal_order=(0, 0, 0, 7))
        assert "A" in results

    def test_cut_too_small_sku_skipped(self):
        # cut < seasonal_period * 2 = 14 → should skip
        df = _make_df(n=22, skus=["A"])
        results = run_sarimax_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.5, min_rows=20, seasonal_period=7,
                                   order=(1, 0, 1), seasonal_order=(0, 0, 0, 7))
        # cut = int(22*0.5) = 11 < max(5, 7*2=14) → skip
        assert "A" not in results

    def test_bad_order_falls_back_gracefully(self):
        # Providing a degenerate order — SARIMAX may fail and log warning, but no crash
        df = _make_df(n=80, skus=["A"])
        try:
            results = run_sarimax_core(df, dt="date", target="sales", group="sku",
                                       train_ratio=0.8, min_rows=20, seasonal_period=7,
                                       order=(0, 0, 0), seasonal_order=(0, 0, 0, 7))
            # Either returns a result or gracefully skips
            assert isinstance(results, dict)
        except Exception:
            pytest.fail("run_sarimax_core should not raise — it should log and continue")
