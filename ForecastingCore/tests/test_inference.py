"""Tests for forecasting_core.inference.predictor: recursive_ml_predict, predict_all_skus."""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.inference.predictor import recursive_ml_predict, predict_all_skus, _calendar_row


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_features_cfg(lags=(1, 7), rolling=(7,), diffs=(1,), ewm_spans=(), calendar=True):
    class Cfg:
        pass
    c = Cfg()
    c.lags       = list(lags)
    c.rolling    = list(rolling)
    c.diffs      = list(diffs)
    c.ewm_spans  = list(ewm_spans)
    c.calendar   = calendar
    return c


def _dummy_model(predict_value=50.0):
    """Minimal sklearn-API model that always returns a constant."""
    class _DummyModel:
        def predict(self, X):
            return np.full(len(X), predict_value)
    return _DummyModel()


def _feature_names_for_cfg(cfg):
    """Generate the feature names that recursive_ml_predict would build."""
    names = []
    if cfg.calendar:
        names += ["year", "month", "day", "dow", "week", "quarter", "is_weekend",
                  "sin_month", "cos_month", "sin_dow", "cos_dow",
                  "is_holiday", "is_easter", "is_christmas",
                  "days_to_holiday", "days_to_easter", "days_to_xmas",
                  "holiday_intensity", "christmas_season"]
    for l in cfg.lags:
        names.append(f"lag_{l}")
    for d in cfg.diffs:
        names += [f"diff_{d}", f"pct_change_{d}"]
    for w in cfg.rolling:
        names += [f"roll_mean_{w}", f"roll_std_{w}", f"roll_min_{w}", f"roll_max_{w}", f"cv_{w}"]
    for s in cfg.ewm_spans:
        names.append(f"ewm_{s}")
    return names


# ---------------------------------------------------------------------------
# _calendar_row
# ---------------------------------------------------------------------------

class TestCalendarRow:

    def test_returns_dict(self):
        dt = pd.Timestamp("2024-03-31")
        row = _calendar_row(dt)
        assert isinstance(row, dict)

    def test_required_keys_present(self):
        row = _calendar_row(pd.Timestamp("2024-06-15"))
        for key in ["year", "month", "day", "dow", "week", "quarter",
                    "is_weekend", "sin_month", "cos_month", "sin_dow", "cos_dow",
                    "is_holiday", "is_easter", "is_christmas",
                    "days_to_holiday", "days_to_easter", "days_to_xmas",
                    "holiday_intensity", "christmas_season"]:
            assert key in row, f"Missing key: {key}"

    def test_is_weekend_correct(self):
        sat = pd.Timestamp("2024-06-15")  # Saturday
        sun = pd.Timestamp("2024-06-16")  # Sunday
        mon = pd.Timestamp("2024-06-17")  # Monday
        assert _calendar_row(sat)["is_weekend"] == 1
        assert _calendar_row(sun)["is_weekend"] == 1
        assert _calendar_row(mon)["is_weekend"] == 0

    def test_christmas_flag(self):
        xmas = pd.Timestamp("2024-12-25")
        row = _calendar_row(xmas)
        assert row["is_christmas"] == 1
        assert row["days_to_xmas"] == 0

    def test_sin_cos_range(self):
        row = _calendar_row(pd.Timestamp("2024-07-04"))
        assert -1.0 <= row["sin_month"] <= 1.0
        assert -1.0 <= row["cos_month"] <= 1.0

    def test_christmas_season_november_december(self):
        nov = _calendar_row(pd.Timestamp("2024-11-01"))
        dec = _calendar_row(pd.Timestamp("2024-12-01"))
        jan = _calendar_row(pd.Timestamp("2024-01-01"))
        assert nov["christmas_season"] == 1
        assert dec["christmas_season"] == 1
        assert jan["christmas_season"] == 0


# ---------------------------------------------------------------------------
# recursive_ml_predict
# ---------------------------------------------------------------------------

class TestRecursiveMLPredict:

    def _run(self, horizon=7, predict_value=50.0, lags=(1,), rolling=(7,),
             diffs=(1,), ewm_spans=(), calendar=True, history_n=30):
        cfg = _mock_features_cfg(lags=lags, rolling=rolling,
                                  diffs=diffs, ewm_spans=ewm_spans, calendar=calendar)
        model = _dummy_model(predict_value)
        feature_names = _feature_names_for_cfg(cfg)
        history = list(np.random.default_rng(0).normal(50, 5, history_n))
        residuals = np.random.default_rng(1).normal(0, 3, history_n)
        last_date = pd.Timestamp("2024-01-01")
        future_dates = [last_date + pd.Timedelta(days=i+1) for i in range(horizon)]
        return recursive_ml_predict(
            fitted_model=model,
            feature_names=feature_names,
            residuals=residuals,
            history=history,
            features_cfg=cfg,
            horizon=horizon,
            future_dates=future_dates,
        )

    def test_returns_correct_horizon_length(self):
        results = self._run(horizon=14)
        assert len(results) == 14

    def test_returns_list_of_dicts(self):
        results = self._run(horizon=5)
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, dict)

    def test_each_result_has_required_keys(self):
        results = self._run(horizon=3)
        for r in results:
            assert "date" in r and "value" in r
            assert "lower" in r and "upper" in r

    def test_value_matches_dummy_model(self):
        results = self._run(horizon=5, predict_value=77.0)
        for r in results:
            assert r["value"] == pytest.approx(77.0, abs=1e-3)

    def test_lower_leq_value_leq_upper(self):
        results = self._run(horizon=7, predict_value=50.0)
        for r in results:
            assert r["lower"] <= r["value"] <= r["upper"]

    def test_non_negative_outputs(self):
        results = self._run(horizon=5, predict_value=50.0)
        for r in results:
            assert r["value"] >= 0.0
            assert r["lower"] >= 0.0

    def test_date_strings_correct_format(self):
        results = self._run(horizon=3)
        for r in results:
            # Must be parseable date string (YYYY-MM-DD)
            pd.Timestamp(r["date"])

    def test_dates_are_sequential(self):
        results = self._run(horizon=5)
        dates = [pd.Timestamp(r["date"]) for r in results]
        for i in range(1, len(dates)):
            assert dates[i] > dates[i - 1]

    def test_empty_residuals_uses_zero_std(self):
        # Zero std → all quantile bounds equal the point forecast
        cfg = _mock_features_cfg()
        model = _dummy_model(30.0)
        feature_names = _feature_names_for_cfg(cfg)
        results = recursive_ml_predict(
            fitted_model=model,
            feature_names=feature_names,
            residuals=np.array([]),
            history=list(np.ones(20) * 30),
            features_cfg=cfg,
            horizon=3,
            future_dates=[pd.Timestamp("2024-01-01") + pd.Timedelta(days=i+1) for i in range(3)],
            quantiles=[0.1, 0.9],
        )
        assert len(results) == 3
        for r in results:
            assert r["lower"] == r["value"] == r["upper"]

    def test_broken_model_falls_back_to_last_value(self):
        class _BrokenModel:
            def predict(self, X):
                raise RuntimeError("model broken")

        cfg = _mock_features_cfg(lags=(1,), rolling=(), diffs=(), calendar=False)
        feature_names = ["lag_1"]
        history = [10.0, 20.0, 30.0]
        residuals = np.zeros(5)
        future_dates = [pd.Timestamp("2024-01-01") + pd.Timedelta(days=i+1) for i in range(2)]
        results = recursive_ml_predict(
            fitted_model=_BrokenModel(),
            feature_names=feature_names,
            residuals=residuals,
            history=history,
            features_cfg=cfg,
            horizon=2,
            future_dates=future_dates,
        )
        assert len(results) == 2
        assert results[0]["value"] == pytest.approx(30.0)

    def test_ewm_features_included(self):
        results = self._run(horizon=3, ewm_spans=(7,), calendar=False)
        assert len(results) == 3  # doesn't crash with EWM features

    def test_short_history_no_crash(self):
        cfg = _mock_features_cfg(lags=(1,), rolling=(7,), diffs=(1,), calendar=False)
        model = _dummy_model(5.0)
        feature_names = _feature_names_for_cfg(cfg)
        results = recursive_ml_predict(
            fitted_model=model,
            feature_names=feature_names,
            residuals=np.zeros(3),
            history=[5.0, 6.0],  # very short history
            features_cfg=cfg,
            horizon=3,
            future_dates=[pd.Timestamp("2024-01-01") + pd.Timedelta(days=i+1) for i in range(3)],
        )
        assert len(results) == 3

    def test_default_quantiles_produce_q10_q90_keys(self):
        results = self._run(horizon=3)
        for r in results:
            assert "q10" in r
            assert "q90" in r

    def test_q90_geq_value_with_positive_std(self):
        results = self._run(horizon=5, predict_value=50.0)
        for r in results:
            assert r["q90"] >= r["value"]

    def test_q10_leq_value_with_positive_std(self):
        results = self._run(horizon=5, predict_value=50.0)
        for r in results:
            assert r["q10"] <= r["value"]

    def test_custom_quantiles_produce_correct_keys(self):
        cfg = _mock_features_cfg(lags=(1,), rolling=(7,), diffs=(1,), calendar=False)
        model = _dummy_model(40.0)
        feature_names = _feature_names_for_cfg(cfg)
        future_dates = [pd.Timestamp("2024-01-01") + pd.Timedelta(days=i+1) for i in range(3)]
        results = recursive_ml_predict(
            fitted_model=model,
            feature_names=feature_names,
            residuals=np.ones(20),
            history=list(np.ones(20) * 40),
            features_cfg=cfg,
            horizon=3,
            future_dates=future_dates,
            quantiles=[0.5, 0.9, 0.95],
        )
        for r in results:
            assert "q50" in r
            assert "q90" in r
            assert "q95" in r
            assert "q10" not in r
            assert r["q95"] >= r["q90"] >= r["q50"]

    def test_q50_equals_value(self):
        # z-score for 0.5 is 0, so q50 == value exactly
        cfg = _mock_features_cfg(lags=(1,), rolling=(), diffs=(), calendar=False)
        model = _dummy_model(60.0)
        feature_names = _feature_names_for_cfg(cfg)
        future_dates = [pd.Timestamp("2024-01-01") + pd.Timedelta(days=1)]
        results = recursive_ml_predict(
            fitted_model=model,
            feature_names=feature_names,
            residuals=np.ones(20),
            history=list(np.ones(20) * 60),
            features_cfg=cfg,
            horizon=1,
            future_dates=future_dates,
            quantiles=[0.1, 0.5, 0.9],
        )
        assert results[0]["q50"] == pytest.approx(results[0]["value"], abs=1e-3)

    def test_quantile_bounds_non_negative(self):
        results = self._run(horizon=5, predict_value=1.0)  # low value, std might make bounds negative
        for r in results:
            for k, v in r.items():
                if k.startswith("q") and k[1:].isdigit():
                    assert v >= 0.0, f"{k} = {v} is negative"


# ---------------------------------------------------------------------------
# predict_all_skus
# ---------------------------------------------------------------------------

def _make_session_config():
    """Minimal SessionConfig-like object for predict_all_skus."""
    from forecasting_core.config.config import SessionConfig
    return SessionConfig.from_dict({
        "columns": {"target": "sales", "date": "date", "group": "sku"},
        "features": {"lags": [1, 7], "rolling": [7], "diffs": [1],
                     "calendar": False, "ewm_spans": []},
        "models": {"lightgbm": {}},
        "training": {"train_ratio": 0.8, "walk_forward": False, "wfv_splits": 3,
                     "min_history": 20, "seasonal_period": 7},
        "forecast": {"horizon": 7},
        "business": {"service_level": 0.95, "lead_time_days": 7,
                     "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0},
    })


def _make_raw_df(n=60, skus=("A",)):
    rows = []
    for sku in skus:
        vals = np.random.default_rng(42).normal(50, 5, n)
        for i, d in enumerate(pd.date_range("2022-01-01", periods=n, freq="D")):
            rows.append({"date": d, "sku": sku, "sales": float(vals[i])})
    return pd.DataFrame(rows)


class TestPredictAllSKUs:

    def _fitted_models(self, sku="A", model_name="lightgbm"):
        feature_names = ["lag_1", "lag_7"]
        return {
            f"{model_name}_{sku}": {
                "fitted_model":  _dummy_model(55.0),
                "feature_names": feature_names,
                "residuals":     np.random.default_rng(0).normal(0, 3, 40),
                "model":         model_name,
                "sku":           sku,
            }
        }

    def test_returns_dict_by_sku(self):
        cfg = _make_session_config()
        raw_df = _make_raw_df(n=60, skus=["A"])
        result = predict_all_skus(self._fitted_models(), {}, raw_df, cfg)
        assert isinstance(result, dict)
        assert "A" in result

    def test_model_name_in_sku_dict(self):
        cfg = _make_session_config()
        raw_df = _make_raw_df(n=60, skus=["A"])
        result = predict_all_skus(self._fitted_models(), {}, raw_df, cfg)
        assert "lightgbm" in result["A"]

    def test_forecast_length_matches_horizon(self):
        cfg = _make_session_config()
        raw_df = _make_raw_df(n=60, skus=["A"])
        result = predict_all_skus(self._fitted_models(), {}, raw_df, cfg)
        pts = result["A"]["lightgbm"]
        assert len(pts) == cfg.forecast.horizon

    def test_stat_forecasts_included(self):
        cfg = _make_session_config()
        raw_df = _make_raw_df(n=60, skus=["A"])
        stat = {
            "ets": {
                "A": {
                    "forecast": np.full(7, 48.0),
                    "residuals": np.zeros(10),
                }
            }
        }
        result = predict_all_skus({}, stat, raw_df, cfg)
        assert "A" in result
        assert "ets" in result["A"]

    def test_empty_fitted_models_and_stat_returns_empty_or_stat_only(self):
        cfg = _make_session_config()
        raw_df = _make_raw_df(n=60, skus=["A"])
        result = predict_all_skus({}, {}, raw_df, cfg)
        assert isinstance(result, dict)

    def test_no_feature_names_skips_model(self):
        cfg = _make_session_config()
        raw_df = _make_raw_df(n=60, skus=["A"])
        fitted = {
            "lightgbm_A": {
                "fitted_model": _dummy_model(50.0),
                "feature_names": [],  # empty → should skip
                "residuals": np.zeros(5),
                "model": "lightgbm",
                "sku": "A",
            }
        }
        result = predict_all_skus(fitted, {}, raw_df, cfg)
        assert "lightgbm" not in result.get("A", {})

    def test_multi_sku_both_appear(self):
        cfg = _make_session_config()
        raw_df = _make_raw_df(n=60, skus=["A", "B"])
        fitted = {
            **self._fitted_models("A", "lightgbm"),
            **self._fitted_models("B", "lightgbm"),
        }
        result = predict_all_skus(fitted, {}, raw_df, cfg)
        assert "A" in result and "B" in result

    def test_ml_forecast_points_have_quantile_keys(self):
        cfg = _make_session_config()
        raw_df = _make_raw_df(n=60, skus=["A"])
        result = predict_all_skus(self._fitted_models(), {}, raw_df, cfg)
        pts = result["A"]["lightgbm"]
        assert len(pts) > 0
        # Default ForecastConfig quantiles are [0.5, 0.9, 0.95]
        for r in pts:
            assert any(k.startswith("q") and k[1:].isdigit() for k in r)

    def test_stat_forecast_points_have_quantile_keys(self):
        cfg = _make_session_config()
        raw_df = _make_raw_df(n=60, skus=["A"])
        stat = {"ets": {"A": {"forecast": np.full(7, 48.0), "residuals": np.ones(10)}}}
        result = predict_all_skus({}, stat, raw_df, cfg)
        pts = result["A"]["ets"]
        for r in pts:
            assert any(k.startswith("q") and k[1:].isdigit() for k in r)

    def test_all_quantile_values_non_negative(self):
        cfg = _make_session_config()
        raw_df = _make_raw_df(n=60, skus=["A"])
        result = predict_all_skus(self._fitted_models(), {}, raw_df, cfg)
        for pt in result["A"]["lightgbm"]:
            for k, v in pt.items():
                if k.startswith("q") and k[1:].isdigit():
                    assert v >= 0.0
