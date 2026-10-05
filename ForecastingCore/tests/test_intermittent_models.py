"""
TSB / ADIDA / IMAPA and the opt-in count objective (tweedie / poisson) for the
ML models on intermittent series.

What is pinned here, and why it matters to a purchase decision:

  * each model's arithmetic on a series small enough to check by hand;
  * no demand means a forecast of exactly zero, and no forecast is negative;
  * TSB decays on a SKU that stopped selling, where Croston keeps its level —
    the reason TSB exists;
  * the models are wired as selectable stat models with the same result
    contract as Croston, and survive the re-forecast round trip;
  * NOTHING changes for a user who did not select them: the router's table and
    the default routing are untouched, and an ML config without the option
    trains exactly the L2 model it trained before;
  * the count objective is applied only where it was asked for AND the series
    is intermittent with a non-negative target, survives hyperparameter tuning,
    and every result says whether it was applied.
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.models import intermittent as im
from forecasting_core.models.croston import croston_forecast


# ---------------------------------------------------------------------------
# Hand-checkable arithmetic
# ---------------------------------------------------------------------------

class TestTSB:

    def test_known_small_series(self):
        # p0 = 2/4 = 0.5, z0 = mean(2, 4) = 3; alpha = beta = 0.5
        # t1 y=0: p = 0.25
        # t2 y=2: p = 0.625, z = 2.5
        # t3 y=0: p = 0.3125
        # t4 y=4: p = 0.65625, z = 3.25  -> 0.65625 * 3.25
        out = im.tsb_forecast([0, 2, 0, 4], alpha=0.5, beta=0.5, n_ahead=3)
        assert out == pytest.approx([0.65625 * 3.25] * 3)

    def test_all_zero_is_zero(self):
        assert np.array_equal(im.tsb_forecast(np.zeros(30), n_ahead=4), np.zeros(4))

    def test_single_nonzero_is_small_positive_and_decays_with_distance(self):
        recent = im.tsb_forecast(np.r_[np.zeros(29), 5.0])[0]
        old = im.tsb_forecast(np.r_[5.0, np.zeros(29)])[0]
        assert recent > 0 and old > 0
        assert old < recent, "a sale 29 periods ago must weigh less than one last period"

    def test_obsolescence_decays_where_croston_does_not(self):
        """The defining property: 40 periods selling every other period, then
        30 periods of nothing. Croston only updates on a demand, so it still
        forecasts the old rate; TSB's probability has decayed towards zero."""
        dead = np.r_[np.tile([0.0, 2.0], 20), np.zeros(30)]
        croston = croston_forecast(dead)[0]
        tsb = im.tsb_forecast(dead)[0]
        assert croston > 0.9                 # ~0.95: unchanged since the last sale
        assert tsb < 0.1 * croston

    def test_dense_constant_series_returns_the_level(self):
        assert im.tsb_forecast(np.full(40, 7.0))[0] == pytest.approx(7.0)

    def test_negatives_and_nan_are_not_demand(self):
        out = im.tsb_forecast([np.nan, -5, 0, 3, 0, 3], n_ahead=2)
        assert np.all(out >= 0)
        assert out == pytest.approx(im.tsb_forecast([0, 0, 0, 3, 0, 3], n_ahead=2))


class TestADIDA:

    def test_known_small_series(self):
        # ADI = 8 / 4 = 2 -> k = 2; buckets [2, 2, 2, 2]; SES of a constant is
        # the constant -> 2 per bucket -> 1 per period.
        out = im.adida_forecast([0, 2, 0, 2, 2, 0, 0, 2], n_ahead=3)
        assert im.adida_level([0, 2, 0, 2, 2, 0, 0, 2]) == 2
        assert out == pytest.approx([1.0] * 3)

    def test_buckets_align_to_the_end_and_drop_the_oldest_remainder(self):
        # 7 periods, k = 2: the OLDEST period is dropped, the newest bucket is whole.
        agg = im._aggregate(np.array([9.0, 1, 1, 2, 2, 3, 3]), 2)
        assert agg.tolist() == [2.0, 4.0, 6.0]

    def test_level_is_capped_so_enough_buckets_remain(self):
        s = np.r_[5.0, np.zeros(11)]                  # ADI = 12, n = 12
        assert im.adida_level(s) == 12 // im.MIN_AGG_BUCKETS

    def test_all_zero_is_zero(self):
        assert np.array_equal(im.adida_forecast(np.zeros(20), n_ahead=3), np.zeros(3))

    def test_single_nonzero_is_positive(self):
        assert im.adida_forecast(np.r_[np.zeros(19), 4.0])[0] > 0

    def test_dense_constant_series_returns_the_level(self):
        assert im.adida_forecast(np.full(30, 4.0))[0] == pytest.approx(4.0)


class TestIMAPA:

    def test_is_the_mean_of_the_adida_levels(self):
        s = np.array([0, 3, 0, 0, 2, 0, 1, 0, 0, 4, 0, 0, 2, 0, 0, 1.0])
        top = im.adida_level(s)
        expected = np.mean([im.adida_forecast(s, k=k)[0] for k in range(1, top + 1)])
        assert im.imapa_forecast(s)[0] == pytest.approx(expected)

    def test_all_zero_is_zero(self):
        assert np.array_equal(im.imapa_forecast(np.zeros(20), n_ahead=2), np.zeros(2))

    def test_dense_constant_series_returns_the_level(self):
        assert im.imapa_forecast(np.full(30, 4.0))[0] == pytest.approx(4.0)


@pytest.mark.parametrize("fc", [im.tsb_forecast, im.adida_forecast, im.imapa_forecast])
def test_never_negative_on_random_intermittent_series(fc):
    rng = np.random.default_rng(5)
    for _ in range(50):
        s = rng.choice([0, 0, 0, 1, 2, 7], size=rng.integers(6, 80)).astype(float)
        s[rng.integers(0, len(s))] = -3.0      # a booked return
        out = fc(s, n_ahead=5)
        assert out.shape == (5,) and np.all(np.isfinite(out)) and np.all(out >= 0)


# ---------------------------------------------------------------------------
# The runner contract (the same one croston.py honours)
# ---------------------------------------------------------------------------

def _intermittent_df(n=80, skus=("A", "B"), seed=3):
    rng = np.random.default_rng(seed)
    frames = []
    for i, sku in enumerate(skus):
        y = np.where(rng.random(n) < 0.3, rng.integers(1, 9, n), 0).astype(float)
        frames.append(pd.DataFrame({
            "date": pd.date_range("2025-01-06", periods=n, freq="W-MON"),
            "sku": sku, "sales": y}))
    return pd.concat(frames, ignore_index=True)


RUNNERS = {"tsb": im.run_tsb_core, "adida": im.run_adida_core, "imapa": im.run_imapa_core}


@pytest.mark.parametrize("name", sorted(RUNNERS))
class TestRunnerContract:

    def test_metrics_forecast_residuals_state(self, name):
        df = _intermittent_df()
        out = RUNNERS[name](df, "date", "sales", "sku", 0.8, 20, 7, horizon=6)
        assert set(out) == {"A", "B"}
        for sku, r in out.items():
            for k in ("mae", "rmse", "wape", "bias", "cost", "cost_horizon"):
                assert k in r and np.isfinite(r[k]), (sku, k)
            assert r["horizon_steps"] == 6
            assert len(r["forecast"]) == 6 and np.all(r["forecast"] >= 0)
            assert len(r["residuals"]) == int(80 * 0.8)
            assert r["state"]["kind"] == name

    def test_cost_horizon_is_windowed_to_the_horizon(self, name):
        from forecasting_core.evaluation.metrics import evaluate_all
        df = _intermittent_df(skus=("A",))
        r = RUNNERS[name](df, "date", "sales", "sku", 0.8, 20, 7, horizon=3)["A"]
        series = df["sales"].to_numpy()
        cut = int(len(series) * 0.8)
        fc = {"tsb": im.tsb_forecast, "adida": im.adida_forecast,
              "imapa": im.imapa_forecast}[name]
        preds = fc(series[:cut], n_ahead=len(series) - cut)
        assert r["cost_horizon"] == pytest.approx(evaluate_all(series[cut:cut + 3], preds[:3])["cost"])

    def test_no_horizon_means_metrics_only(self, name):
        r = RUNNERS[name](_intermittent_df(), "date", "sales", "sku", 0.8, 20, 7)["A"]
        assert r["cost_horizon"] is None and "forecast" not in r

    def test_short_series_skipped(self, name):
        assert RUNNERS[name](_intermittent_df(n=10), "date", "sales", "sku", 0.8, 20, 7) == {}

    def test_all_zero_series_forecasts_zero(self, name):
        df = _intermittent_df(skus=("A",)).assign(sales=0.0)
        r = RUNNERS[name](df, "date", "sales", "sku", 0.8, 20, 7, horizon=4)["A"]
        assert np.array_equal(r["forecast"], np.zeros(4))

    def test_state_reproduces_the_forecast(self, name):
        df = _intermittent_df(skus=("A",))
        r = RUNNERS[name](df, "date", "sales", "sku", 0.8, 20, 7, horizon=5)["A"]
        again = im.forecast_from_state(name, r["state"], df["sales"].to_numpy(), 5)
        assert np.array_equal(again, r["forecast"])


def test_state_of_another_model_is_refused():
    assert im.forecast_from_state("tsb", {"kind": "croston", "alpha": 0.1}, np.ones(5), 3) is None


# ---------------------------------------------------------------------------
# Registry wiring and "nothing changes unless selected"
# ---------------------------------------------------------------------------

def _report(sku, series_type):
    from forecasting_core.data.quality import SKUReport
    return SKUReport(sku=sku, n_rows=100, missing_dates=0, outlier_count=0,
                     zero_ratio=0.0, is_intermittent=series_type == "intermittent",
                     has_min_history=True, series_type=series_type, quality_score=1.0,
                     series_flags={series_type})


class TestRegistryAndRouting:

    def test_engine_trains_all_three_when_a_config_names_them(self):
        from forecasting_core.models.factory import ModelFactory, STAT_MODELS
        assert {"tsb", "adida", "imapa"} <= STAT_MODELS
        f = ModelFactory({"tsb": {}, "adida": {}, "lightgbm": {}})
        assert f.stat_names() == ["tsb", "adida"]
        assert set(f.build_ml()) == {"lightgbm"}

    def test_only_tsb_is_offered_to_the_product(self):
        """available_models() is what the backend validates a selection
        against and what mode "all" trains: TSB in, ADIDA/IMAPA out."""
        from forecasting_core.config.config import SessionConfig
        from forecasting_core.models.factory import ModelFactory
        offered = set(ModelFactory.available_models())
        assert "tsb" in offered and not offered & {"adida", "imapa"}
        schema = set(SessionConfig.schema()["available_models"])
        assert "tsb" in schema and not schema & {"adida", "imapa"}

    def test_routing_table_is_unchanged(self):
        from forecasting_core.training.router import ROUTING_TABLE
        assert ROUTING_TABLE == {
            "short":        {"naive", "seasonal_naive"},
            "intermittent": {"croston", "ets"},
            "seasonal":     {"prophet", "ets", "lightgbm", "sarimax", "lstm"},
            "stable":       {"lightgbm", "xgboost", "arima", "sarimax", "lstm"},
            "volatile":     {"lightgbm", "xgboost", "prophet", "arima", "ets", "sarimax"},
        }

    def test_router_never_adds_them_when_not_selected(self):
        from forecasting_core.training.router import ModelRouter
        reports = {s: _report(s, t) for s, t in
                   [("I", "intermittent"), ("S", "stable"), ("V", "volatile"),
                    ("X", "seasonal"), ("Z", "short")]}
        declared = {"lightgbm": {}, "xgboost": {}, "ets": {}, "croston": {}}
        for enabled in (True, False):
            routing = ModelRouter(declared, enabled=enabled).route(reports)
            assigned = set().union(*routing.values())
            assert not assigned & {"tsb", "adida", "imapa"}

    def test_selected_alone_they_still_reach_an_intermittent_sku(self):
        """Not in the table, so on an intermittent SKU the intersection is empty
        and the router falls back to the user's full selection — the model they
        picked runs rather than the SKU being dropped."""
        from forecasting_core.training.router import ModelRouter
        routing = ModelRouter({"tsb": {}}, enabled=True).route(
            {"I": _report("I", "intermittent")})
        assert routing["I"] == {"tsb"}

    def test_compatibility_warns_on_dense_series(self):
        from forecasting_core.validation.compatibility import _check_one
        msg = _check_one("tsb", pd.Series(np.ones(50)), 50, 0.0, False, 20, 7, 0.4)
        assert "TSB is designed for intermittent" in msg
        # Croston's sentence is exactly what it was.
        assert _check_one("croston", pd.Series(np.ones(50)), 50, 0.0, False, 20, 7, 0.4) \
            .startswith("Croston is designed for intermittent series")


def _engine_config(models, routing=False):
    return {
        "name": "intermittent-test",
        "columns": {"target": "sales", "date": "date", "group_keys": ["sku"]},
        "features": {"lags": [1, 2], "rolling": [4], "diffs": [], "calendar": False},
        "models": models,
        "training": {"train_ratio": 0.8, "walk_forward": True, "wfv_splits": 2,
                     "min_history": 20},
        "forecast": {"horizon": 4},
        "business": {"service_level": 0.95, "lead_time_days": 7},
        "routing": {"enabled": routing},
    }


@pytest.fixture(scope="module")
def stat_engine():
    from forecasting_core.engine import ForecastEngine
    df = _intermittent_df(n=90, skus=("A", "B", "C"))
    engine = ForecastEngine.from_dict(_engine_config({"tsb": {}, "adida": {}, "imapa": {}}))
    engine.load_data(df)
    engine.train()
    return engine, df


class TestEndToEnd:

    def test_forecast_and_metrics_rows_exist_for_each_selected_model(self, stat_engine):
        engine, _ = stat_engine
        rows = pd.DataFrame(engine.get_forecast()["rows"])
        assert {"tsb", "adida", "imapa"} <= set(rows["model"])
        assert (rows["forecast"] >= 0).all()
        m = pd.DataFrame(engine.get_metrics()["rows"])
        for name in ("tsb", "adida", "imapa"):
            sub = m[m["model"] == name]
            assert len(sub) == 3 and sub["cost_horizon"].notna().all()

    def test_reforecast_round_trip(self, stat_engine):
        from forecasting_core.engine import ForecastEngine
        from forecasting_core.reforecast import load_artifact_set
        engine, df = stat_engine
        arts = engine.export_artifacts()
        loaded = load_artifact_set([(f.family, f.data, f.sha256) for f in arts.files])

        # Unchanged history -> the parent's forecast, exactly, and every unit
        # updated from its state (nothing refitted).
        child = ForecastEngine.from_dict(_engine_config({"tsb": {}, "adida": {}, "imapa": {}}))
        child.load_data(df)
        result = child.reforecast(loaded)
        assert {(s.model, s.mode) for s in result.statuses} == {
            (m, "updated") for m in ("tsb", "adida", "imapa")}
        key = ["sku", "model", "step"]
        parent = pd.DataFrame(engine.get_forecast()["rows"])
        parent = parent[parent["model"].isin(["tsb", "adida", "imapa"])].sort_values(key)
        new = result.forecast_df[result.forecast_df["model"].isin(["tsb", "adida", "imapa"])]
        new = new.sort_values(key)
        assert np.allclose(parent["forecast"].to_numpy(), new["forecast"].to_numpy())

        # Two more empty weeks -> TSB's forecast goes DOWN (obsolescence), via
        # the carried state, not a refit.
        last = df["date"].max()
        extra = pd.DataFrame([{"date": last + pd.Timedelta(weeks=k), "sku": s, "sales": 0.0}
                              for s in ("A", "B", "C") for k in (1, 2)])
        child2 = ForecastEngine.from_dict(_engine_config({"tsb": {}, "adida": {}, "imapa": {}}))
        child2.load_data(pd.concat([df, extra], ignore_index=True))
        r2 = child2.reforecast(loaded)
        assert all(s.mode == "updated" for s in r2.statuses)
        tsb_old = parent[parent["model"] == "tsb"].groupby("sku")["forecast"].first()
        f2 = r2.forecast_df
        tsb_new = f2[f2["model"] == "tsb"].groupby("sku")["forecast"].first()
        assert (tsb_new < tsb_old).all()


# ---------------------------------------------------------------------------
# The opt-in count objective for LightGBM / XGBoost
# ---------------------------------------------------------------------------

class TestObjectiveSpec:

    def test_default_is_none(self):
        from forecasting_core.models.factory import intermittent_objective_spec
        assert intermittent_objective_spec("lightgbm", {}) is None
        assert intermittent_objective_spec("lightgbm", {"intermittent_objective": None}) is None

    def test_library_vocabulary(self):
        from forecasting_core.models.factory import intermittent_objective_spec as spec
        assert spec("lightgbm", {"intermittent_objective": "tweedie"}) == \
            {"objective": "tweedie", "tweedie_variance_power": 1.5}
        assert spec("xgboost", {"intermittent_objective": "tweedie",
                                "tweedie_variance_power": 1.2}) == \
            {"objective": "reg:tweedie", "tweedie_variance_power": 1.2}
        assert spec("lightgbm", {"intermittent_objective": "poisson"}) == {"objective": "poisson"}
        assert spec("xgboost", {"intermittent_objective": "poisson"}) == {"objective": "count:poisson"}

    @pytest.mark.parametrize("params,name", [
        ({"intermittent_objective": "tweedy"}, "lightgbm"),
        ({"intermittent_objective": "tweedie", "tweedie_variance_power": 2.0}, "lightgbm"),
        ({"intermittent_objective": "tweedie", "tweedie_variance_power": 1.0}, "xgboost"),
        ({"intermittent_objective": "tweedie"}, "ets"),
    ])
    def test_bad_values_fail_config_validation(self, params, name):
        from forecasting_core.config.config import ConfigError, SessionConfig
        with pytest.raises(ConfigError):
            SessionConfig.from_dict(_engine_config({name: params}))

    def test_reserved_keys_never_reach_the_estimator(self):
        from forecasting_core.models.factory import ModelFactory
        cfg = {"lightgbm": {"n_estimators": 7, "intermittent_objective": "tweedie",
                            "tweedie_variance_power": 1.3},
               "xgboost": {"intermittent_objective": "poisson"}}
        built = ModelFactory(cfg).build_ml()
        lp = built["lightgbm"].get_params()
        assert lp["n_estimators"] == 7 and lp["objective"] is None
        assert "intermittent_objective" not in lp and "intermittent_objective" not in built["xgboost"].get_params()
        assert ModelFactory(cfg).intermittent_objectives() == {
            "lightgbm": {"objective": "tweedie", "tweedie_variance_power": 1.3},
            "xgboost": {"objective": "count:poisson"},
        }
        assert ModelFactory({"lightgbm": {}}).intermittent_objectives() == {}


def _feature_frame(y, sku="A"):
    y = np.asarray(y, dtype=float)
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=len(y), freq="D"),
                       "sku": sku, "sales": y})
    for lag in (1, 2, 7):
        df[f"lag_{lag}"] = df["sales"].shift(lag)
    return df.dropna().reset_index(drop=True)


def _intermittent_y(n=120, seed=0):
    rng = np.random.default_rng(seed)
    return np.where(rng.random(n) < 0.25, rng.integers(1, 10, n), 0).astype(float)


def _smooth_y(n=120, seed=0):
    rng = np.random.default_rng(seed)
    return 20 + rng.normal(0, 2, n)


def _train(df, models, objectives, **kw):
    from forecasting_core.training.trainer import Trainer
    t = Trainer(train_ratio=0.8, walk_forward=True, wfv_splits=2, max_workers=1,
                intermittent_objectives=objectives, **kw)
    return t.train(df, models, group_cols=["sku"], target="sales", dt="date")


def _ml(name):
    from forecasting_core.models.factory import ModelFactory
    return ModelFactory({name: {"n_estimators": 20}}).build_ml()


class TestObjectiveInTraining:

    TWEEDIE = {"objective": "tweedie", "tweedie_variance_power": 1.5}

    def test_off_by_default_nothing_changes(self):
        df = _feature_frame(_intermittent_y())
        base = _train(df, _ml("lightgbm"), None)
        explicit_off = _train(df, _ml("lightgbm"), {})
        (r0,), (r1,) = base.values(), explicit_off.values()
        assert "intermittent_objective" not in r0
        assert r0["fitted_model"].get_params()["objective"] is None
        X = df[r0["feature_names"]]
        assert np.array_equal(r0["fitted_model"].predict(X), r1["fitted_model"].predict(X))

    def test_applied_on_an_intermittent_series(self):
        df = _feature_frame(_intermittent_y())
        (r,) = _train(df, _ml("lightgbm"), {"lightgbm": self.TWEEDIE}).values()
        assert r["intermittent_objective"]["applied"] is True
        assert r["intermittent_objective"]["objective"] == "tweedie"
        assert r["fitted_model"].get_params()["objective"] == "tweedie"
        preds = r["fitted_model"].predict(df[r["feature_names"]])
        assert np.all(preds >= 0)
        # ...and it is a different model from the L2 one on the same data.
        (l2,) = _train(df, _ml("lightgbm"), None).values()
        assert not np.allclose(preds, l2["fitted_model"].predict(df[r["feature_names"]]))

    def test_not_applied_on_a_smooth_series_and_says_why(self):
        df = _feature_frame(_smooth_y())
        (r,) = _train(df, _ml("lightgbm"), {"lightgbm": self.TWEEDIE}).values()
        d = r["intermittent_objective"]
        assert d["applied"] is False and d["reason"] == "not_intermittent"
        assert d["objective"] == "default"
        assert r["fitted_model"].get_params()["objective"] is None

    def test_not_applied_on_a_negative_target(self):
        y = _intermittent_y()
        y[50] = -2.0          # inside the frame _feature_frame keeps (it drops 7 warm-up rows)
        df = _feature_frame(y)
        assert (df["sales"] < 0).any(), "fixture assumption"
        (r,) = _train(df, _ml("lightgbm"), {"lightgbm": self.TWEEDIE}).values()
        assert r["intermittent_objective"] == {**r["intermittent_objective"],
                                               "applied": False, "reason": "negative_target"}
        assert r["fitted_model"].get_params()["objective"] is None

    def test_xgboost_reg_tweedie(self):
        df = _feature_frame(_intermittent_y(seed=4))
        spec = {"objective": "reg:tweedie", "tweedie_variance_power": 1.5}
        (r,) = _train(df, _ml("xgboost"), {"xgboost": spec}).values()
        assert r["intermittent_objective"]["applied"] is True
        assert r["fitted_model"].get_params()["objective"] == "reg:tweedie"
        assert np.all(r["fitted_model"].predict(df[r["feature_names"]]) >= 0)

    def test_survives_hyperparameter_tuning(self):
        """A tuned model used to be rebuilt from the tuned params alone; without
        carrying the objective it would silently serve L2 again."""
        pytest.importorskip("optuna")
        df = _feature_frame(_intermittent_y(n=150))
        (r,) = _train(df, _ml("lightgbm"), {"lightgbm": self.TWEEDIE},
                      tuning=True, tuning_trials=2).values()
        assert r["tuned_params"], "fixture assumption: tuning actually ran"
        assert r["fitted_model"].get_params()["objective"] == "tweedie"

    def test_the_metrics_rows_say_which_objective_trained_and_only_when_asked(self):
        from forecasting_core.engine import ForecastEngine
        df = _intermittent_df(n=90, skus=("A", "B"))
        rows = {}
        for label, params in (("off", {"n_estimators": 10}),
                              ("on", {"n_estimators": 10, "intermittent_objective": "tweedie"})):
            engine = ForecastEngine.from_dict(_engine_config({"lightgbm": params}))
            engine.load_data(df)
            engine.train()
            rows[label] = [r for r in engine.get_metrics()["rows"] if r["model"] == "lightgbm"]
        assert rows["off"] and all("objective" not in r for r in rows["off"])
        assert rows["on"] and all(r["objective"] == "tweedie" for r in rows["on"])

    def test_a_tuned_final_model_keeps_the_objective(self):
        """The path `test_survives_hyperparameter_tuning` exercises end to end,
        pinned without optuna: a tuned instance is rebuilt from the tuned
        params, and must still carry the objective."""
        from forecasting_core.training.trainer import Trainer
        base = _ml("lightgbm")["lightgbm"]
        t = Trainer()
        tuned = t._make_final("lightgbm", base, {"n_estimators": 5}, self.TWEEDIE)
        assert tuned.get_params()["objective"] == "tweedie"
        assert tuned.get_params()["n_estimators"] == 5
        # Without the option the tuned model is exactly what it was before.
        assert t._make_final("lightgbm", base, {"n_estimators": 5}).get_params()["objective"] is None

    def test_tuner_holds_the_objective_fixed_and_still_scores_asymmetric_cost(self, monkeypatch):
        from forecasting_core.training import tuner as tuner_mod
        seen = []
        real = tuner_mod._make_model

        def spy(name, params):
            seen.append(dict(params))
            return real(name, params)

        monkeypatch.setattr(tuner_mod, "_make_model", spy)
        df = _feature_frame(_intermittent_y())
        X, y = df[["lag_1", "lag_2", "lag_7"]], df["sales"]
        t = tuner_mod.HyperparamTuner("lightgbm", cv_splits=2, fixed_params=self.TWEEDIE)
        splits = t._make_splits(len(X))
        cost = t._fold_cost({"objective": "regression", "n_estimators": 10}, X, y, splits)
        assert seen and all(p["objective"] == "tweedie" for p in seen)
        # The returned number is the asymmetric cost of those very fits.
        from forecasting_core.evaluation.metrics import asymmetric_cost
        expected = []
        for tr, te in splits:
            m = real("lightgbm", {"n_estimators": 10, **self.TWEEDIE})
            m.fit(X.iloc[tr], y.iloc[tr])
            expected.append(asymmetric_cost(y.iloc[te].values, m.predict(X.iloc[te])))
        assert cost == pytest.approx(float(np.mean(expected)))
