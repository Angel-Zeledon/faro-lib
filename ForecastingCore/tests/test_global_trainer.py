"""
The global cross-learning model.

The claim being tested is not "it runs" but the two properties that justify it:
a series too short to train on its own still gets a usable forecast, and the
forecast does not degrade into the catalogue's average — the model still knows
which SKU it is looking at.
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.config.config import FeaturesConfig
from forecasting_core.features.engineer import FeatureEngineer
from forecasting_core.training.global_trainer import (
    GlobalTrainer, MODEL_NAME, SeriesProfile, scaled_feature_columns,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _catalogue(n_long=10, long_days=400, short_days=25, seed=3) -> pd.DataFrame:
    """
    A realistic small catalogue: several SKUs with years of history sharing a
    weekly shape, plus one SKU that launched three weeks ago.

    Two properties are deliberate. Levels differ by two orders of magnitude —
    that is what the per-series scaling has to survive. And each series carries
    a slowly drifting level (a random walk), not just iid noise around a
    constant: without it, a recent observation tells you nothing a calendar
    could not, one step ahead is exactly as hard as seven, and every test about
    horizons measures nothing.
    """
    rng = np.random.default_rng(seed)
    rows = []
    end = pd.Timestamp("2026-06-30")

    def _drift(n):
        return np.exp(np.cumsum(rng.normal(0.0, 0.02, n)))

    for i in range(n_long):
        level = 10.0 * (10 ** (i % 3))          # 10, 100, 1000, 10, ...
        dates = pd.date_range(end - pd.Timedelta(days=long_days - 1), periods=long_days, freq="D")
        weekly = 1.0 + 0.45 * np.sin(2 * np.pi * np.arange(long_days) / 7.0)
        noise = rng.normal(1.0, 0.05, long_days)
        rows.append(pd.DataFrame({
            "date": dates,
            "sku": f"LONG_{i}",
            "demand": np.clip(level * weekly * _drift(long_days) * noise, 0, None),
        }))

    # Same weekly shape, only three weeks of it, at its own level.
    dates = pd.date_range(end - pd.Timedelta(days=short_days - 1), periods=short_days, freq="D")
    offset = long_days - short_days
    weekly = 1.0 + 0.45 * np.sin(2 * np.pi * (np.arange(short_days) + offset) / 7.0)
    rows.append(pd.DataFrame({
        "date": dates,
        "sku": "NEWBIE",
        "demand": np.clip(55.0 * weekly * _drift(short_days)
                          * rng.normal(1.0, 0.05, short_days), 0, None),
    }))

    return pd.concat(rows, ignore_index=True)


def _features(df: pd.DataFrame) -> pd.DataFrame:
    cfg = FeaturesConfig(lags=[1, 7], diffs=[1], rolling=[7], calendar=True)
    eng = FeatureEngineer(cfg, dt_col="date", target="demand", group_cols=["sku"])
    return eng.transform(df)


@pytest.fixture(scope="module")
def trained():
    df_ml = _features(_catalogue())
    trainer = GlobalTrainer(horizon=7, train_ratio=0.8, wfv_splits=3)
    results = trainer.train(df_ml, group_cols=["sku"], target="demand", dt="date")
    return results


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

class TestResultShape:

    def test_one_entry_per_series(self, trained):
        assert len(trained) == 11, f"expected 11 series, got {sorted(trained)}"

    def test_entry_keys_match_the_per_sku_trainer_contract(self, trained):
        entry = next(iter(trained.values()))
        for field in ("mae", "rmse", "wape", "bias", "sku", "store", "model",
                      "n", "n_folds", "validation", "feature_names", "residuals"):
            assert field in entry, f"missing {field} — downstream consumers read it"
        assert entry["model"] == MODEL_NAME

    def test_declares_the_direct_strategy(self, trained):
        """Without this flag the recursive predictor would try to call .predict
        on an object that is not an sklearn model."""
        entry = next(iter(trained.values()))
        assert entry["forecast_strategy"] == "direct"
        assert entry["fitted_model"] is None
        assert entry["direct_forecaster"] is not None

    def test_reports_per_horizon_metrics(self, trained):
        entry = next(iter(trained.values()))
        by_h = entry["horizon_metrics"]["by_horizon"]
        assert set(by_h) >= {"1", "7"}

    def test_cumulative_uncertainty_grows_with_the_horizon(self, trained):
        """
        The property inventory actually depends on.

        A reorder point is exposed to the SUM of demand over the lead time, and
        the uncertainty of a sum grows with the number of terms. Per-STEP error
        need not grow visibly — for a series whose level barely moves, day 7 is
        about as predictable as day 1 — but the cumulative error must, and it is
        the cumulative one the safety stock is built from.
        """
        forecaster = next(iter(trained.values()))["direct_forecaster"]
        cumulative = forecaster.cumulative_residuals_by_horizon
        assert set(cumulative) >= {1, 7}
        spread_1 = float(np.std(cumulative[1]))
        spread_7 = float(np.std(cumulative[7]))
        assert spread_7 > 2 * spread_1, (
            f"cumulative uncertainty barely moved: h1={spread_1:.3f} h7={spread_7:.3f}"
        )


# ---------------------------------------------------------------------------
# The reason the model exists
# ---------------------------------------------------------------------------

class TestCrossLearning:

    def test_short_series_gets_a_forecast(self, trained):
        """25 days is below the per-SKU min_history — this SKU used to get
        `naive` or nothing at all."""
        key = next(k for k in trained if k.startswith(f"{MODEL_NAME}_NEWBIE"))
        forecast = trained[key]["direct_forecaster"].point(7)
        assert len(forecast) == 7
        assert np.all(np.isfinite(forecast))
        assert np.all(forecast >= 0)

    def test_short_series_forecast_is_at_its_own_level(self, trained):
        """The failure mode a global model has to avoid: regressing a small SKU
        toward the catalogue's mean because a big SKU dominates the loss."""
        key = next(k for k in trained if k.startswith(f"{MODEL_NAME}_NEWBIE"))
        forecast = trained[key]["direct_forecaster"].point(7)
        assert 25.0 < float(np.mean(forecast)) < 100.0, (
            f"NEWBIE sells ~55/day; forecast averaged {np.mean(forecast):.1f}"
        )

    def test_series_at_different_scales_keep_their_scales(self, trained):
        """10/day and 1000/day must not converge on a shared answer."""
        means = {}
        for key, entry in trained.items():
            means[entry["sku"]] = float(np.mean(entry["direct_forecaster"].point(7)))
        small = means["LONG_0"]     # level 10
        large = means["LONG_2"]     # level 1000
        assert large / max(small, 1e-9) > 20, (
            f"scale collapsed: LONG_0={small:.1f} LONG_2={large:.1f}"
        )

    def test_learns_the_weekly_shape(self, trained):
        """A flat forecast would pass every test above and still be useless."""
        key = next(k for k in trained if k.startswith(f"{MODEL_NAME}_LONG_1"))
        forecast = trained[key]["direct_forecaster"].point(7)
        spread = float(np.max(forecast) - np.min(forecast))
        assert spread > 0.1 * float(np.mean(forecast)), (
            "forecast is flat — the weekly seasonality was not learned"
        )


# ---------------------------------------------------------------------------
# Direct multi-horizon
# ---------------------------------------------------------------------------

class TestDirectMultiHorizon:

    def test_each_step_is_predicted_independently(self, trained):
        """Recursion would make step 3 depend on the value produced for step 2.
        Asking for 3 steps and for 7 must return the same first 3."""
        entry = next(iter(trained.values()))
        short = entry["direct_forecaster"].point(3)
        long = entry["direct_forecaster"].point(7)
        assert np.allclose(short, long[:3])

    def test_each_horizon_is_calibrated_from_its_own_residuals(self, trained):
        """Per-horizon calibration means per-horizon numbers — not one band
        copied H times, which is what the normal approximation did."""
        from forecasting_core.evaluation.conformal import horizon_bands
        entry = next(iter(trained.values()))
        bands = horizon_bands(
            entry["direct_forecaster"].residuals_by_horizon, [0.1, 0.9],
            horizons=range(1, 8),
        )
        widths = [bands[h][0.9] - bands[h][0.1] for h in range(1, 8)]
        assert all(w > 0 for w in widths)
        assert len(set(round(w, 6) for w in widths)) > 1, (
            "every horizon got an identical band — they were not computed "
            "separately"
        )

    def test_step_one_forecasts_the_day_after_the_last_observation(self):
        """
        The off-by-one that hides in plain sight.

        Feature rows condition on the PREVIOUS observation, so the freshest
        origin is one bucket behind the data. Ignoring that offset publishes a
        forecast for the last observed day as if it were the first future day —
        aggregate error looks fine and every single date is wrong.

        Demand here is a pure day-of-week lookup with well-separated levels, so
        the day the forecast belongs to is readable off the value itself.
        """
        by_dow = {0: 10.0, 1: 30.0, 2: 50.0, 3: 70.0, 4: 90.0, 5: 110.0, 6: 130.0}
        dates = pd.date_range("2025-01-01", periods=240, freq="D")
        frames = [
            pd.DataFrame({"date": dates, "sku": f"S{i}",
                          "demand": [by_dow[d.dayofweek] for d in dates]})
            for i in range(4)
        ]
        df = pd.concat(frames, ignore_index=True)

        results = GlobalTrainer(horizon=3, wfv_splits=2).train(
            _features(df), group_cols=["sku"], target="demand", dt="date",
        )
        forecaster = next(iter(results.values()))["direct_forecaster"]
        assert forecaster.origin_lag == 1, (
            "the newest usable origin is exactly one observation behind"
        )

        forecast = forecaster.point(3)
        last_date = dates[-1]
        expected = [by_dow[(last_date + pd.Timedelta(days=k)).dayofweek]
                    for k in (1, 2, 3)]
        for k, (got, want) in enumerate(zip(forecast, expected), start=1):
            assert abs(got - want) < 0.25 * want, (
                f"step {k} should forecast "
                f"{(last_date + pd.Timedelta(days=k)).date()} (~{want}), got {got:.1f}"
            )


# ---------------------------------------------------------------------------
# Scaling helper
# ---------------------------------------------------------------------------

class TestScaling:

    def test_only_unit_denominated_features_are_scaled(self):
        cols = ["lag_1", "roll_mean_7", "roll_std_7", "ewm_7", "diff_1",
                "cv_7", "pct_change_1", "dow", "sin_month", "is_holiday"]
        scaled = set(scaled_feature_columns(cols))
        assert scaled == {"lag_1", "roll_mean_7", "roll_std_7", "ewm_7", "diff_1"}
        assert "cv_7" not in scaled, "a ratio is already scale-free"
        assert "pct_change_1" not in scaled
        assert "is_holiday" not in scaled

    def test_profile_log_level_is_finite_for_a_zero_series(self):
        profile = SeriesProfile(key="k", sku="s", store="t", codes=(0,),
                                scale=1e-3, cv=0.0, n_rows=5)
        assert np.isfinite(profile.log_level)


class TestDegradation:

    def test_empty_frame_returns_no_entries(self):
        trainer = GlobalTrainer(horizon=7)
        assert trainer.train(pd.DataFrame(), group_cols=["sku"],
                             target="demand", dt="date") == {}

    def test_single_series_still_trains(self):
        df = _catalogue(n_long=1, long_days=120)
        df = df[df["sku"] == "LONG_0"]
        results = GlobalTrainer(horizon=5, wfv_splits=2).train(
            _features(df), group_cols=["sku"], target="demand", dt="date",
        )
        assert len(results) == 1
        assert len(next(iter(results.values()))["direct_forecaster"].point(5)) == 5
