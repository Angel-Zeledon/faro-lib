"""
Persisted models and re-forecasting without retraining.

One small catalogue is trained once (module fixture) with every family that can
carry state: per-SKU LightGBM and XGBoost, the global direct model, ETS, ARIMA
and Croston. The assertions are about behaviour the user depends on:

  * unchanged history reproduces the parent's forecast EXACTLY, for every family;
  * new history moves the forecast, and nothing was refitted to move it;
  * a tampered artifact is refused before it is parsed;
  * data the models cannot answer for is refused with a stable code.
"""

import copy
import time

import numpy as np
import pandas as pd
import pytest

from forecasting_core.engine import ForecastEngine
from forecasting_core.reforecast import (
    ArtifactIntegrityError, ReforecastRefused, load_artifact_set,
)
from forecasting_core.reforecast import codec

HORIZON = 5
FAMILIES = ["lightgbm", "xgboost", "global_lgbm", "ets", "arima", "croston"]


def _catalogue(n_skus=4, days=120, seed=11, end="2026-06-30") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(pd.Timestamp(end) - pd.Timedelta(days=days - 1), periods=days, freq="D")
    frames = []
    for i in range(n_skus):
        level = 20.0 * (i + 1)
        weekly = 1.0 + 0.35 * np.sin(2 * np.pi * np.arange(days) / 7.0)
        frames.append(pd.DataFrame({
            "date": dates, "sku": f"SKU_{i:02d}",
            "demand": np.clip(level * weekly * rng.normal(1, 0.06, days), 0, None),
        }))
    return pd.concat(frames, ignore_index=True)


def _config() -> dict:
    return {
        "name": "reforecast-test",
        "columns": {"target": "demand", "date": "date", "group_keys": ["sku"]},
        "features": {"lags": [1, 7], "rolling": [7], "diffs": [1], "calendar": True},
        "models": {
            "lightgbm": {"n_estimators": 20}, "xgboost": {"n_estimators": 20},
            "global_lgbm": {"n_estimators": 30},
            "ets": {}, "arima": {}, "croston": {},
        },
        "training": {"train_ratio": 0.8, "walk_forward": True, "wfv_splits": 2,
                     "min_history": 20},
        "forecast": {"horizon": HORIZON, "quantiles": [0.1, 0.5, 0.9]},
        "business": {"service_level": 0.95, "lead_time_days": 5},
        # Run every model on every SKU so each family has units to restore.
        "routing": {"enabled": False},
    }


def _extend(df: pd.DataFrame, days: int, factor: float = 1.0, seed=3) -> pd.DataFrame:
    """`days` more daily rows per SKU, demand scaled by `factor`."""
    rng = np.random.default_rng(seed)
    frames = [df]
    for sku, g in df.groupby("sku"):
        last = g["date"].max()
        dates = pd.date_range(last + pd.Timedelta(days=1), periods=days, freq="D")
        base = g["demand"].tail(14).mean()
        frames.append(pd.DataFrame({
            "date": dates, "sku": sku,
            "demand": np.clip(base * factor * rng.normal(1, 0.05, days), 0, None),
        }))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture(scope="module")
def trained():
    df = _catalogue()
    engine = ForecastEngine.from_dict(_config())
    engine.load_data(df)
    started = time.perf_counter()
    engine.train()
    train_seconds = time.perf_counter() - started
    artifact_set = engine.export_artifacts()
    return {"df": df, "engine": engine, "artifact_set": artifact_set,
            "train_seconds": train_seconds}


def _load(artifact_set):
    return load_artifact_set([(f.family, f.data, f.sha256) for f in artifact_set.files])


def _run(trained, history, **options):
    engine = ForecastEngine.from_dict(_config())
    engine.load_data(history)
    started = time.perf_counter()
    result = engine.reforecast(_load(trained["artifact_set"]), **options)
    return engine, result, time.perf_counter() - started


def _by_key(forecast_df):
    return (forecast_df.sort_values(["sku", "model", "step"])
            .set_index(["sku", "model", "step"]))


# ---------------------------------------------------------------------------

class TestArtifactsAreWrittenAndGuarded:

    def test_every_family_that_trained_has_an_artifact_and_there_is_a_context(self, trained):
        families = {f.family for f in trained["artifact_set"].files}
        assert "context" in families
        assert set(FAMILIES) <= families, f"missing {set(FAMILIES) - families}"

    def test_artifacts_carry_the_metadata_a_registry_row_needs(self, trained):
        for f in trained["artifact_set"].files:
            m = f.metadata
            assert m["model_family"] == f.family
            assert m["engine_version"] and m["library_versions"]["numpy"]
            assert len(m["input_schema_hash"]) == 64
            assert m["training_window"]["first"] and m["training_window"]["last"]
            assert m["feature_spec"]["lags"] == [1, 7]
            assert f.size_bytes == len(f.data) and len(f.sha256) == 64

    def test_the_format_is_not_pickle(self, trained):
        import gzip
        import json
        for f in trained["artifact_set"].files:
            body = json.loads(gzip.decompress(f.data))      # plain JSON or this raises
            assert body["format"] == codec.FORMAT and body["family"] == f.family

    def test_the_same_content_hashes_the_same(self, trained):
        again = trained["engine"].export_artifacts()
        old = {f.family: f.sha256 for f in trained["artifact_set"].files}
        new = {f.family: f.sha256 for f in again.files}
        # `created_at` differs, so the context legitimately does; the model
        # families must be byte-identical for the hash to mean "this model".
        for family in FAMILIES:
            assert old[family] == new[family], family

    def test_a_tampered_artifact_is_refused_before_it_is_parsed(self, trained):
        f = next(x for x in trained["artifact_set"].files if x.family == "lightgbm")
        tampered = bytearray(f.data)
        tampered[len(tampered) // 2] ^= 0xFF
        with pytest.raises(ArtifactIntegrityError):
            load_artifact_set([("lightgbm", bytes(tampered), f.sha256)])

    def test_a_valid_file_under_the_wrong_digest_is_refused(self, trained):
        f = next(x for x in trained["artifact_set"].files if x.family == "lightgbm")
        with pytest.raises(ArtifactIntegrityError):
            load_artifact_set([("lightgbm", f.data, "0" * 64)])

    def test_a_file_registered_under_the_wrong_family_is_refused(self, trained):
        f = next(x for x in trained["artifact_set"].files if x.family == "lightgbm")
        with pytest.raises(ArtifactIntegrityError):
            load_artifact_set([("xgboost", f.data, f.sha256)])

    def test_a_set_with_no_context_is_refused(self, trained):
        f = next(x for x in trained["artifact_set"].files if x.family == "lightgbm")
        with pytest.raises(ValueError):
            load_artifact_set([("lightgbm", f.data, f.sha256)])


class TestUnchangedHistoryReproducesTheParent:

    def test_every_model_gives_exactly_the_parents_forecast(self, trained):
        _engine, result, _ = _run(trained, trained["df"])
        parent = _by_key(trained["engine"]._forecast_df)
        child = _by_key(result.forecast_df)
        assert set(parent.index) == set(child.index)
        worst = (parent["forecast"] - child["forecast"]).abs().groupby(level="model").max()
        assert (worst < 1e-9).all(), worst.to_dict()

    def test_the_bands_are_the_parents_too(self, trained):
        _engine, result, _ = _run(trained, trained["df"])
        parent = _by_key(trained["engine"]._forecast_df)
        child = _by_key(result.forecast_df)
        for col in ("p10", "p90", "lower", "upper"):
            both = pd.concat([parent[col], child[col]], axis=1, keys=["a", "b"]).dropna()
            assert ((both["a"] - both["b"]).abs() < 1e-9).all(), col

    def test_the_inventory_recommendations_match_the_parents(self, trained):
        engine, _result, _ = _run(trained, trained["df"])
        parent = trained["engine"].get_inventory_report()["recommendations"]
        child = engine.get_inventory_report()["recommendations"]
        assert len(parent) == len(child) > 0
        key = lambda r: str(r.get("sku"))
        for a, b in zip(sorted(parent, key=key), sorted(child, key=key)):
            for field, value in a.items():
                if isinstance(value, float):
                    assert b[field] == pytest.approx(value, rel=1e-9, abs=1e-9), field

    def test_every_unit_was_updated_not_refitted(self, trained):
        _engine, result, _ = _run(trained, trained["df"])
        assert result.summary["by_mode"] == {"updated": len(result.statuses)}
        assert not result.summary["refit_needed"]

    def test_metrics_and_explanations_carry_over(self, trained):
        engine, _result, _ = _run(trained, trained["df"])
        parent = trained["engine"].get_metrics()
        child = engine.get_metrics()
        assert child["by_model"] == parent["by_model"]
        assert len(child["rows"]) == len(parent["rows"])


class TestNewHistoryMovesTheForecastWithoutRefitting:

    @pytest.fixture(scope="class")
    def grown(self, trained):
        return _extend(trained["df"], days=3, factor=2.5)

    def test_it_is_a_different_forecast_on_later_dates(self, trained, grown):
        _engine, result, _ = _run(trained, grown)
        parent = trained["engine"]._forecast_df
        parent_first = parent.groupby("sku")["date"].min()
        child_first = result.forecast_df.groupby("sku")["date"].min()
        assert (child_first > parent_first).all(), "the origin did not advance"
        # The same STEP is a different number, because the lags now include the surge.
        a = _by_key(parent)["forecast"]
        b = _by_key(result.forecast_df)["forecast"]
        moved = (a - b).abs().groupby(level="model").max()
        for model in ("lightgbm", "xgboost", "global_lgbm", "ets", "arima"):
            assert moved[model] > 1e-6, f"{model} ignored the new actuals"

    def test_no_model_was_refitted_to_get_there(self, trained, grown, monkeypatch):
        import lightgbm
        import xgboost
        from statsmodels.tsa.arima.model import ARIMA

        from forecasting_core.training.global_trainer import GlobalTrainer
        from forecasting_core.training.trainer import Trainer

        def boom(*_a, **_k):
            raise AssertionError("a fit/train ran during a re-forecast")

        for target in (lightgbm.LGBMRegressor, xgboost.XGBRegressor):
            monkeypatch.setattr(target, "fit", boom)
        monkeypatch.setattr(ARIMA, "fit", boom)
        monkeypatch.setattr(Trainer, "train", boom)
        monkeypatch.setattr(GlobalTrainer, "train", boom)
        monkeypatch.setattr(GlobalTrainer, "_fit", boom)

        _engine, result, _ = _run(trained, grown)
        assert result.summary["by_mode"] == {"updated": len(result.statuses)}
        assert len(result.forecast_df) > 0

    def test_the_parent_forecast_is_not_modified(self, trained, grown):
        before = trained["engine"]._forecast_df.copy()
        _run(trained, grown)
        pd.testing.assert_frame_equal(trained["engine"]._forecast_df, before)

    def test_horizon_can_be_longer_for_models_that_allow_it(self, trained, grown):
        _engine, result, _ = _run(trained, grown, horizon=HORIZON + 3)
        rows = result.forecast_df
        assert rows[rows["model"] == "lightgbm"]["step"].max() == HORIZON + 3
        # The direct global model was fitted for HORIZON steps and says so.
        skipped = {(s.model, s.reason) for s in result.statuses if s.mode == "skipped"}
        assert ("global_lgbm", "horizon_exceeds_training") in skipped
        assert "global_lgbm" not in set(rows["model"])

    def test_a_series_missing_from_the_new_history_is_skipped_and_named(self, trained, grown):
        trimmed = grown[grown["sku"] != "SKU_03"]
        _engine, result, _ = _run(trained, trimmed)
        gone = [s for s in result.statuses if s.sku == "SKU_03"]
        assert gone and all(s.mode == "skipped" and s.reason == "series_missing" for s in gone)
        assert "SKU_03" not in set(result.forecast_df["sku"])

    def test_a_new_series_is_reported_not_forecast(self, trained, grown):
        extra = grown[grown["sku"] == "SKU_00"].assign(sku="SKU_NEW")
        _engine, result, _ = _run(trained, pd.concat([grown, extra], ignore_index=True))
        assert "SKU_NEW" in result.new_series
        assert "SKU_NEW" not in set(result.forecast_df["sku"])

    def test_it_is_much_faster_than_training(self, trained, grown):
        _engine, _result, seconds = _run(trained, grown)
        print(f"\ntrain {trained['train_seconds']:.1f}s  reforecast {seconds:.1f}s")
        assert seconds < trained["train_seconds"]


class TestAStateThatCannotBeCarriedIsRefittedAndSaysSo:

    def test_ets_whose_series_start_moved_is_refitted_and_flagged(self, trained):
        # Trim the first week: ETS's initial states belong to where the series began.
        start = trained["df"]["date"].min() + pd.Timedelta(days=7)
        shifted = _extend(trained["df"][trained["df"]["date"] >= start], days=2)
        _engine, result, _ = _run(trained, shifted)
        modes = {(s.model, s.mode) for s in result.statuses}
        assert ("ets", "refit") in modes
        assert ("lightgbm", "updated") in modes
        assert result.summary["refit_needed"]
        assert "ets" in set(result.forecast_df["model"])

    def test_without_permission_to_refit_it_is_skipped_with_a_reason(self, trained):
        start = trained["df"]["date"].min() + pd.Timedelta(days=7)
        shifted = _extend(trained["df"][trained["df"]["date"] >= start], days=2)
        _engine, result, _ = _run(trained, shifted, allow_refit=False)
        ets = [s for s in result.statuses if s.model == "ets"]
        assert ets and all(s.mode == "skipped" and s.reason == "refit_required" for s in ets)


class TestWhatItRefuses:

    def _refused(self, trained, history, config=None, **options):
        engine = ForecastEngine.from_dict(config or _config())
        engine.load_data(history)
        with pytest.raises(ReforecastRefused) as info:
            engine.reforecast(_load(trained["artifact_set"]), **options)
        return info.value

    def test_a_changed_feature_spec_is_an_incompatible_schema(self, trained):
        cfg = copy.deepcopy(_config())
        cfg["features"]["lags"] = [1, 7, 14]
        err = self._refused(trained, trained["df"], config=cfg)
        assert err.code == "schema_incompatible" and "features" in err.params["fields"]

    def test_a_missing_target_column_is_an_incompatible_schema(self, trained):
        err = self._refused(trained, trained["df"].rename(columns={"demand": "units"}))
        assert err.code == "schema_incompatible"
        assert "demand" in err.params["missing_columns"]

    def test_history_that_ends_before_the_models_did_is_refused(self, trained):
        older = trained["df"][trained["df"]["date"] < trained["df"]["date"].max()
                              - pd.Timedelta(days=10)]
        assert self._refused(trained, older).code == "history_older_than_models"

    def test_history_that_runs_too_far_past_the_models_is_refused(self, trained):
        far = _extend(trained["df"], days=HORIZON + 20)
        err = self._refused(trained, far)
        assert err.code == "window_shifted_too_far"
        assert err.params["shift_buckets"] > err.params["limit_buckets"] == HORIZON

    def test_the_limit_can_be_widened_explicitly(self, trained):
        far = _extend(trained["df"], days=HORIZON + 3)
        _engine, result, _ = _run(trained, far, max_shift_buckets=HORIZON + 5)
        assert len(result.forecast_df) > 0

    def test_a_different_cadence_is_refused(self, trained):
        weekly = trained["df"][trained["df"]["date"].dt.dayofweek == 0]
        assert self._refused(trained, weekly).code == "cadence_changed"

    def test_a_session_that_scales_columns_is_refused(self, trained):
        cfg = copy.deepcopy(_config())
        cfg["transforms"] = {"columns": {"demand": {"scale": "log"}}}
        assert self._refused(trained, trained["df"], config=cfg).code == "unsupported_transforms"

    def test_nothing_restorable_is_refused_not_answered_empty(self, trained):
        # Every series gone from the new history: no unit can forecast.
        other = trained["df"].assign(sku=lambda d: "OTHER_" + d["sku"])
        err = self._refused(trained, other)
        assert err.code == "nothing_to_forecast"
