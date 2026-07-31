"""
End-to-end: the global model through the real Pipeline.

Every piece of it is unit-tested elsewhere. What this file checks is that the
pieces are actually wired together — that selecting `global_lgbm` produces
forecast rows, a metrics row that can win a SKU, calibrated bands and a policy
backtest, rather than a silently skipped step and a run that still reports
success.
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.config.config import SessionConfig
from forecasting_core.pipelines.pipeline import Pipeline


def _catalogue(n_skus=8, days=260, seed=17) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frames = []
    end = pd.Timestamp("2026-06-30")
    dates = pd.date_range(end - pd.Timedelta(days=days - 1), periods=days, freq="D")
    for i in range(n_skus):
        level = 20.0 * (i + 1)
        weekly = 1.0 + 0.35 * np.sin(2 * np.pi * np.arange(days) / 7.0)
        drift = np.exp(np.cumsum(rng.normal(0.0, 0.01, days)))
        frames.append(pd.DataFrame({
            "date": dates,
            "sku": f"SKU_{i:02d}",
            "demand": np.clip(level * weekly * drift * rng.normal(1, 0.06, days), 0, None),
        }))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture(scope="module")
def run():
    cfg = SessionConfig.from_dict({
        "name": "global-e2e",
        "columns": {"target": "demand", "date": "date", "group_keys": ["sku"]},
        "features": {"lags": [1, 7], "rolling": [7], "diffs": [1], "calendar": True},
        "models": {"global_lgbm": {"n_estimators": 120}},
        "training": {"train_ratio": 0.8, "walk_forward": True, "wfv_splits": 2,
                     "min_history": 20},
        "forecast": {"horizon": 7, "quantiles": [0.1, 0.5, 0.9, 0.95]},
        "business": {"service_level": 0.95, "lead_time_days": 5},
    })
    return Pipeline(cfg, df=_catalogue()).run()


class TestItActuallyRuns:

    def test_metrics_include_the_global_model(self, run):
        models = set(run.metrics_df["model"])
        assert "global_lgbm" in models, f"only got {sorted(models)}"

    def test_it_is_labelled_as_its_own_type(self, run):
        rows = run.metrics_df[run.metrics_df["model"] == "global_lgbm"]
        assert set(rows["type"]) == {"global"}

    def test_every_series_gets_a_row(self, run):
        rows = run.metrics_df[run.metrics_df["model"] == "global_lgbm"]
        assert rows["sku"].nunique() == 8

    def test_forecast_rows_are_produced(self, run):
        fc = run.forecast_df
        assert fc is not None
        rows = fc[fc["model"] == "global_lgbm"]
        assert not rows.empty
        assert rows["sku"].nunique() == 8
        assert set(rows["step"]) == set(range(1, 8))

    def test_forecasts_are_non_negative_and_finite(self, run):
        rows = run.forecast_df[run.forecast_df["model"] == "global_lgbm"]
        assert (rows["forecast"] >= 0).all()
        assert np.isfinite(rows["forecast"]).all()

    def test_the_asymmetric_cost_is_reported(self, run):
        """Without it the champion selection silently falls back to MAE."""
        rows = run.metrics_df[run.metrics_df["model"] == "global_lgbm"]
        assert "cost" in rows.columns
        assert rows["cost"].notna().any()


class TestCalibratedBands:

    def test_quantile_columns_are_present_and_ordered(self, run):
        rows = run.forecast_df[run.forecast_df["model"] == "global_lgbm"]
        for _, row in rows.iterrows():
            assert row["p10"] <= row["p50"] + 1e-6 <= row["p90"] + 1e-6, (
                f"crossed quantiles on step {row['step']}: "
                f"{row['p10']}/{row['p50']}/{row['p90']}"
            )

    def test_bands_are_not_degenerate(self, run):
        rows = run.forecast_df[run.forecast_df["model"] == "global_lgbm"]
        widths = (rows["p90"] - rows["p10"]).to_numpy()
        assert (widths > 0).mean() > 0.8, (
            "most forecasts came back with a zero-width interval"
        )


class TestDemandRisk:

    def test_cumulative_offsets_are_published(self, run):
        assert run.demand_risk, "the reorder point has nothing measured to use"
        entry = next(iter(run.demand_risk.values()))
        assert entry["model"] == "global_lgbm"
        assert entry["cumulative_offsets"]

    def test_offsets_grow_with_the_lead_time(self, run):
        """Uncertainty about a SUM grows with the number of terms."""
        entry = next(iter(run.demand_risk.values()))
        offsets = entry["cumulative_offsets"]
        level = str(max(entry["quantiles"]))
        short = float(offsets["1"][level])
        long = float(offsets[str(max(int(k) for k in offsets))][level])
        assert long > short, f"L=1 offset {short}, longest {long}"

    def test_every_series_is_covered(self, run):
        assert len(run.demand_risk) == 8


class TestPolicyBacktest:

    def test_a_summary_is_produced(self, run):
        assert run.policy_backtest, "no purchasing outcome was simulated"
        assert "summary" in run.policy_backtest
        assert "by_sku" in run.policy_backtest

    def test_summary_states_its_own_coverage(self, run):
        """The headline must never read as catalogue-wide when it is not."""
        summary = run.policy_backtest["summary"]
        assert summary["n_series"] >= 1
        assert summary["n_series"] <= 8

    def test_it_reports_both_sides_of_the_tradeoff(self, run):
        summary = run.policy_backtest["summary"]
        for key in ("fill_rate", "baseline_fill_rate",
                    "stockout_buckets", "baseline_stockout_buckets",
                    "avg_inventory", "baseline_avg_inventory"):
            assert key in summary

    def test_fill_rate_is_a_proportion(self, run):
        summary = run.policy_backtest["summary"]
        assert 0.0 <= summary["fill_rate"] <= 1.0
        assert 0.0 <= summary["baseline_fill_rate"] <= 1.0


class TestTheShortSeriesActuallyReachesIt:
    """
    The model's entire reason for being, guarded.

    A series shorter than the widest configured lag loses every row to the
    feature warm-up drop. With the wizard's own defaults — `lag_28` — a SKU
    launched three weeks ago produces an empty frame and never reaches the
    model built to serve it. Found on a real run: the one new SKU in a
    13-series catalogue came back "no_forecast" while the global model trained
    happily on the other twelve.
    """

    @pytest.fixture(scope="class")
    def run_with_a_new_sku(self):
        rng = np.random.default_rng(7)
        days, end = 260, pd.Timestamp("2026-06-30")
        dates = pd.date_range(end - pd.Timedelta(days=days - 1), periods=days, freq="D")
        rows = []
        for i in range(6):
            level = 8.0 * (3 ** (i % 3))
            weekly = 1 + 0.4 * np.sin(2 * np.pi * np.arange(days) / 7)
            drift = np.exp(np.cumsum(rng.normal(0, 0.015, days)))
            rows.append(pd.DataFrame({
                "date": dates, "sku": f"P{i}",
                "demand": np.clip(level * weekly * drift * rng.normal(1, .07, days), 0, None),
            }))
        short = 24
        sd = dates[-short:]
        weekly = 1 + 0.4 * np.sin(2 * np.pi * (np.arange(short) + days - short) / 7)
        rows.append(pd.DataFrame({
            "date": sd, "sku": "NEWBIE",
            "demand": np.clip(40 * weekly * rng.normal(1, .07, short), 0, None),
        }))

        cfg = SessionConfig.from_dict({
            "columns": {"target": "demand", "date": "date", "group_keys": ["sku"]},
            # The wizard's real defaults, including a lag wider than the new SKU.
            "features": {"lags": [1, 7, 14, 28], "rolling": [7, 14, 28], "diffs": [1]},
            "models": {"global_lgbm": {"n_estimators": 80}, "lightgbm": {}},
            "training": {"train_ratio": 0.8, "wfv_splits": 2, "min_history": 20},
            "forecast": {"horizon": 7},
        })
        return Pipeline(cfg, df=pd.concat(rows, ignore_index=True)).run()

    def test_a_sku_shorter_than_the_widest_lag_is_still_modelled(self, run_with_a_new_sku):
        metrics = run_with_a_new_sku.metrics_df
        served = set(metrics[metrics["model"] == "global_lgbm"]["sku"])
        assert "NEWBIE" in served, (
            f"the 24-day SKU never reached the global model; it served {sorted(served)}"
        )

    def test_the_per_sku_model_still_cannot_serve_it(self, run_with_a_new_sku):
        """The contrast that makes the global model worth having."""
        metrics = run_with_a_new_sku.metrics_df
        assert "NEWBIE" not in set(metrics[metrics["model"] == "lightgbm"]["sku"])

    def test_it_gets_a_real_forecast_not_a_flat_line(self, run_with_a_new_sku):
        forecast = run_with_a_new_sku.forecast_df
        rows = forecast[(forecast["sku"] == "NEWBIE")
                        & (forecast["model"] == "global_lgbm")].sort_values("step")
        assert len(rows) == 7
        values = rows["forecast"].to_numpy()
        assert np.all(values > 0)
        assert values.max() - values.min() > 0.15 * values.mean(), (
            "the borrowed forecast is flat — no weekly shape was transferred"
        )


class TestPayloadsSurvivePersistence:
    """
    Both new payloads are written into a JSONB column by the training job.

    A numpy float or int64 anywhere inside them serialises fine in Python and
    then fails at psycopg2, which surfaces as the whole training job failing
    AFTER the model has been fitted — the most expensive possible place to find
    out. The engine's own tests are the cheap place.
    """

    def test_demand_risk_is_json_serialisable(self, run):
        import json
        json.dumps(run.demand_risk)

    def test_policy_backtest_is_json_serialisable(self, run):
        import json
        json.dumps(run.policy_backtest)

    def test_no_numpy_scalars_leak_into_demand_risk(self, run):
        import numpy as np

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    assert isinstance(k, str), f"non-string key {k!r}"
                    walk(v)
            elif isinstance(node, (list, tuple)):
                for v in node:
                    walk(v)
            else:
                assert not isinstance(node, np.generic), (
                    f"numpy scalar {node!r} would fail at the DB boundary"
                )
                assert not isinstance(node, np.ndarray)

        walk(run.demand_risk)
        walk(run.policy_backtest)
