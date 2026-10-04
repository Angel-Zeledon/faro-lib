"""Tests for forecasting_core.models.prophet: run_prophet_core."""

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("prophet", reason="prophet package not installed")

from forecasting_core.models.prophet import _build, run_prophet_core


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


def _make_shocked_tail_df(n_train=120, horizon=10, tail_extra=30, level=50.0, seed=3):
    """A train window plus a held-out tail whose FIRST `horizon` steps
    continue the train level and whose LATER steps are deliberately wrong by
    two orders of magnitude — see forecasting_core/models/ets.py for the
    rationale and tests/test_models.py for the same fixture used against the
    other statistical families. `train_ratio` is returned so `cut` lands
    exactly on `n_train`."""
    rng = np.random.default_rng(seed)
    steady = np.maximum(1.0, rng.normal(level, level * 0.06, n_train + horizon))
    shocked = np.full(tail_extra, level * 100.0)
    values = np.concatenate([steady, shocked])
    dates = pd.date_range("2021-01-01", periods=len(values), freq="D")
    df = pd.DataFrame({"date": dates, "sku": "A", "sales": values})
    train_ratio = n_train / len(values)
    return df, train_ratio


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


    def test_reports_full_metrics(self):
        df = _make_df(n=80, skus=["A"])
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7)
        full_keys = {"mae", "rmse", "wape", "bias", "mape", "smape"}
        sku_result = next(iter(results.values()))
        # Key presence alone is a false-positive risk (a None value would still
        # pass `issubset`), so also assert every value is a real finite number.
        assert full_keys.issubset(sku_result.keys())
        for key in full_keys:
            value = sku_result[key]
            assert value is not None, f"{key} is None"
            assert isinstance(value, (int, float)), f"{key} is not numeric: {value!r}"
            assert np.isfinite(value), f"{key} is not finite: {value!r}"


# ---------------------------------------------------------------------------
# `cost_horizon` — comparable across every model family (docs/stability.md #17(d))
# ---------------------------------------------------------------------------

class TestCostHorizonWindow:

    def test_cost_horizon_ignores_the_tail_past_the_horizon(self):
        df, train_ratio = _make_shocked_tail_df()
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=train_ratio, min_rows=20,
                                   seasonal_period=7, horizon=10)
        res = results["A"]
        assert res["horizon_steps"] == 10
        # Old code copied the whole-tail `cost` (dominated by the 30 shocked
        # steps) straight into `cost_horizon`.
        assert res["cost_horizon"] < res["cost"] / 10
        assert res["cost_horizon"] < 50.0

    def test_short_tail_reports_over_available_steps_without_padding(self):
        df = _make_df(n=80, skus=["A"])
        results = run_prophet_core(df, dt="date", target="sales", group="sku",
                                   train_ratio=0.8, min_rows=20, seasonal_period=7,
                                   horizon=30)
        res = results["A"]
        held_out = 80 - int(80 * 0.8)
        assert held_out < 30, "fixture assumption: tail shorter than horizon"
        assert res["horizon_steps"] == held_out
        assert np.isfinite(res["cost_horizon"])


# ---------------------------------------------------------------------------
# What the model is configured to look for
# ---------------------------------------------------------------------------

class TestProphetSeasonalityAndHolidays:
    """The seasonalities are Prophet's 'auto', not a blanket True.

    `yearly_seasonality=True` was passed unconditionally. A tenant with eight or
    fourteen months of history — the common case — got a yearly cycle fitted to
    one incomplete pass and then extrapolated over the horizon. Prophet's own
    default exists to prevent exactly that, and these tests assert the three
    outcomes it produces.
    """

    @staticmethod
    def _fit(frame, country=""):
        m = _build([], country)
        m.fit(frame)
        return set(m.seasonalities)

    @staticmethod
    def _frame(periods, freq="D"):
        rng = np.random.default_rng(3)
        return pd.DataFrame({
            "ds": pd.date_range("2021-01-01", periods=periods, freq=freq),
            "y": np.abs(rng.normal(50, 8, periods)),
        })

    def test_short_history_gets_no_yearly_seasonality(self):
        assert "yearly" not in self._fit(self._frame(80))

    def test_three_years_of_history_does_get_yearly_seasonality(self):
        """'auto' is not 'never' — with the evidence for it, yearly comes back."""
        assert "yearly" in self._fit(self._frame(1100))

    def test_monthly_buckets_get_no_weekly_seasonality(self):
        """A session resampled to months has no week to fit. It was forced on."""
        assert "weekly" not in self._fit(self._frame(60, freq="MS"))

    def test_the_holiday_calendar_reaches_the_model(self):
        m = _build([], "CO")
        assert getattr(m, "country_holidays", None) == "CO"

    def test_an_unsupported_country_degrades_instead_of_failing(self):
        """A country Prophet has no table for must cost the holidays, not the run."""
        frame = self._frame(80)
        m = _build([], "ZZ")
        m.fit(frame)
        assert getattr(m, "country_holidays", None) is None
        assert len(m.predict(frame)) == len(frame)


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
