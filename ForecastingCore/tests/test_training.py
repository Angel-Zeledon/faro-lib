"""
Tests for forecasting_core.training: Trainer, WalkForwardSplitter, ModelRouter.
"""

import pytest
import numpy as np
import pandas as pd

from forecasting_core.training.trainer import Trainer, WalkForwardSplitter, _series_scale
from forecasting_core.training.router import ModelRouter
from forecasting_core.models.factory import ModelFactory
from forecasting_core.config.config import FeaturesConfig
from forecasting_core.data.quality import (
    SKUReport, SERIES_STABLE, SERIES_SEASONAL,
    SERIES_INTERMITTENT, SERIES_VOLATILE, SERIES_SHORT,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_feature_df(n=80, skus=("A", "B"), seed=0):
    """Simple feature DataFrame with lag_1, roll_mean_7, calendar features."""
    rng = np.random.default_rng(seed)
    rows = []
    for sku in skus:
        sales = np.maximum(1, rng.normal(50, 8, n)).round(2)
        for i, d in enumerate(pd.date_range("2021-01-01", periods=n, freq="D")):
            rows.append({
                "date": d, "sku": sku, "sales": float(sales[i]),
                "lag_1": float(sales[i - 1]) if i > 0 else float(sales[0]),
                "roll_mean_7": float(sales[max(0, i - 7):i + 1].mean()),
                "month": d.month, "dow": d.dayofweek,
            })
    return pd.DataFrame(rows)


def _make_sku_reports(**kwargs):
    """Create a dict of SKUReport objects."""
    base = dict(n_rows=80, missing_dates=0, outlier_count=0, zero_ratio=0.0,
                is_intermittent=False, has_min_history=True, quality_score=90.0, warnings=[])
    base.update(kwargs)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# WalkForwardSplitter
# ─────────────────────────────────────────────────────────────────────────────

class TestWalkForwardSplitter:

    def test_returns_list_of_tuples(self):
        splitter = WalkForwardSplitter(n_splits=3)
        splits = splitter.split(100)
        assert isinstance(splits, list)
        assert len(splits) > 0
        for tr, te in splits:
            assert len(tr) > 0

    def test_train_test_disjoint(self):
        splitter = WalkForwardSplitter(n_splits=3)
        for tr, te in splitter.split(100):
            assert len(set(tr) & set(te)) == 0

    def test_train_always_before_test(self):
        splitter = WalkForwardSplitter(n_splits=3)
        for tr, te in splitter.split(100):
            assert max(tr) < min(te)

    def test_train_size_increases_across_folds(self):
        splitter = WalkForwardSplitter(n_splits=4)
        splits = splitter.split(120)
        train_sizes = [len(tr) for tr, _ in splits]
        for i in range(1, len(train_sizes)):
            assert train_sizes[i] >= train_sizes[i - 1]

    def test_too_few_points_returns_empty(self):
        splitter = WalkForwardSplitter(n_splits=3)
        splits = splitter.split(5)  # too small: fold_size=0 → all empty te filtered out
        assert all(len(te) > 0 for _, te in splits), "All test folds must be non-empty"

    def test_single_split(self):
        splitter = WalkForwardSplitter(n_splits=1)
        splits = splitter.split(50)
        assert len(splits) == 1

    def test_no_overlap_between_consecutive_test_folds(self):
        splitter = WalkForwardSplitter(n_splits=3)
        splits = splitter.split(100)
        for i in range(1, len(splits)):
            prev_te = set(splits[i - 1][1])
            curr_te = set(splits[i][1])
            assert len(prev_te & curr_te) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Trainer
# ─────────────────────────────────────────────────────────────────────────────

class TestTrainer:

    def _get_models(self):
        return ModelFactory({"lightgbm": {"n_estimators": 20}}).build_ml()

    def test_simple_train_returns_results(self):
        df = _make_feature_df(n=60, skus=["A"])
        models = self._get_models()
        trainer = Trainer(train_ratio=0.8, walk_forward=False)
        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        assert len(results) > 0

    def test_result_key_format(self):
        df = _make_feature_df(n=60, skus=["A"])
        models = self._get_models()
        trainer = Trainer(train_ratio=0.8, walk_forward=False)
        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        for key in results:
            assert "_" in key  # format: {model}_{sku}

    def test_result_contains_metrics(self):
        df = _make_feature_df(n=60, skus=["A"])
        models = self._get_models()
        trainer = Trainer(train_ratio=0.8, walk_forward=False)
        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        for v in results.values():
            assert "mae" in v and "rmse" in v and "wape" in v

    def test_result_contains_fitted_model(self):
        df = _make_feature_df(n=60, skus=["A"])
        models = self._get_models()
        trainer = Trainer(train_ratio=0.8, walk_forward=False)
        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        for v in results.values():
            assert v.get("fitted_model") is not None
            assert hasattr(v["fitted_model"], "predict")

    def test_result_contains_residuals(self):
        df = _make_feature_df(n=60, skus=["A"])
        models = self._get_models()
        trainer = Trainer(train_ratio=0.8, walk_forward=False)
        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        for v in results.values():
            assert isinstance(v["residuals"], np.ndarray)

    def test_result_contains_feature_names(self):
        df = _make_feature_df(n=60, skus=["A"])
        models = self._get_models()
        trainer = Trainer(train_ratio=0.8, walk_forward=False)
        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        for v in results.values():
            assert isinstance(v["feature_names"], list)
            assert len(v["feature_names"]) > 0

    def test_wfv_train_returns_results(self):
        df = _make_feature_df(n=100, skus=["A"])
        models = self._get_models()
        trainer = Trainer(train_ratio=0.7, walk_forward=True, wfv_splits=3)
        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        assert len(results) > 0

    def test_wfv_result_has_n_folds(self):
        df = _make_feature_df(n=100, skus=["A"])
        models = self._get_models()
        trainer = Trainer(train_ratio=0.7, walk_forward=True, wfv_splits=3)
        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        for v in results.values():
            assert "n_folds" in v

    def test_multi_sku_trains_all(self):
        df = _make_feature_df(n=60, skus=["A", "B", "C"])
        models = self._get_models()
        trainer = Trainer(train_ratio=0.8, walk_forward=False)
        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        skus_in_results = {v["sku"] for v in results.values()}
        assert skus_in_results == {"A", "B", "C"}

    def test_too_small_sku_skipped(self):
        # B has only 2 rows — int(2*0.8)=1 which is < 2 → _simple returns {}
        df_a = _make_feature_df(n=60, skus=["A"])
        small = pd.DataFrame({
            "date": pd.date_range("2021-01-01", periods=2),
            "sku": "B", "sales": [1.0, 2.0],
            "lag_1": [0.0, 1.0], "roll_mean_7": [1.0, 1.5],
            "month": [1, 1], "dow": [0, 1],
        })
        df = pd.concat([df_a, small], ignore_index=True)
        models = self._get_models()
        trainer = Trainer(train_ratio=0.8, walk_forward=False)
        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        skus = {v["sku"] for v in results.values()}
        assert "B" not in skus

    def test_no_trainable_models_returns_empty(self):
        df = _make_feature_df(n=60)
        trainer = Trainer(train_ratio=0.8, walk_forward=False)
        results = trainer.train(df, {}, group_col="sku", target="sales", dt="date")
        assert results == {}

    def test_mae_is_non_negative(self):
        df = _make_feature_df(n=80, skus=["A"])
        models = self._get_models()
        trainer = Trainer(train_ratio=0.8, walk_forward=False)
        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        for v in results.values():
            assert v["mae"] >= 0.0

    def test_simple_residuals_are_validation_set(self):
        """_simple must store residuals from the held-out test split, not in-sample."""
        df = _make_feature_df(n=80, skus=["A"])
        models = self._get_models()
        trainer = Trainer(train_ratio=0.8, walk_forward=False)
        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        for v in results.values():
            r = v["residuals"]
            assert isinstance(r, np.ndarray)
            assert len(r) > 0
            # Validation-set residuals: length ≈ n * (1 - train_ratio)
            # (80 rows, 0.8 ratio → ~16 validation rows)
            assert len(r) <= 20  # must NOT be 64 (the training set size)

    def test_wfv_residuals_are_oof(self):
        """_wfv must store concatenated OOF residuals, not in-sample residuals."""
        df = _make_feature_df(n=100, skus=["A"])
        models = self._get_models()
        trainer = Trainer(train_ratio=0.7, walk_forward=True, wfv_splits=3)
        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        for v in results.values():
            r = v["residuals"]
            assert isinstance(r, np.ndarray)
            assert len(r) > 0
            # OOF residuals come from test folds, so length < full training set (70 rows)
            assert len(r) < 70

    def test_wfv_residuals_non_empty_with_sufficient_data(self):
        df = _make_feature_df(n=100, skus=["A"])
        models = self._get_models()
        trainer = Trainer(train_ratio=0.7, walk_forward=True, wfv_splits=3)
        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        for v in results.values():
            assert len(v["residuals"]) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Trainer parallelism (per-SKU worker threads)
# ─────────────────────────────────────────────────────────────────────────────

class TestTrainerParallelism:

    def _get_models(self):
        return ModelFactory({"lightgbm": {"n_estimators": 20}}).build_ml()

    def test_resolve_max_workers_never_parallelizes_single_group(self):
        trainer = Trainer(train_ratio=0.8, walk_forward=False)
        assert trainer._resolve_max_workers(1) == 1
        assert trainer._resolve_max_workers(0) == 1

    def test_resolve_max_workers_auto_caps_to_group_count(self):
        trainer = Trainer(train_ratio=0.8, walk_forward=False)
        n_workers = trainer._resolve_max_workers(3)
        assert 1 <= n_workers <= 3

    def test_resolve_max_workers_respects_explicit_cap(self):
        trainer = Trainer(train_ratio=0.8, walk_forward=False, max_workers=2)
        assert trainer._resolve_max_workers(10) == 2
        assert trainer._resolve_max_workers(1) == 1

    def test_parallel_and_sequential_produce_same_skus(self):
        df = _make_feature_df(n=60, skus=["A", "B", "C", "D", "E", "F"])
        seq = Trainer(train_ratio=0.8, walk_forward=False, max_workers=1)
        par = Trainer(train_ratio=0.8, walk_forward=False, max_workers=4)
        res_seq = seq.train(df, self._get_models(), group_col="sku", target="sales", dt="date")
        res_par = par.train(df, self._get_models(), group_col="sku", target="sales", dt="date")
        assert set(res_seq.keys()) == set(res_par.keys())

    def test_parallel_metrics_match_sequential(self):
        """
        Regression guard for shared-model-state corruption: Trainer._wfv/_simple
        call .fit() directly on model instances across fold iterations, and
        those instances used to be shared across every SKU's call. If a future
        change reintroduced that sharing, concurrent .fit() calls from
        different worker threads on the same object would corrupt each
        other's state and metrics would diverge between max_workers=1 and
        max_workers>1 (or vary from run to run). n_estimators=20 with no
        early stopping keeps LightGBM deterministic across both runs.
        """
        df = _make_feature_df(n=80, skus=["A", "B", "C", "D", "E", "F", "G", "H"])
        seq = Trainer(train_ratio=0.8, walk_forward=False, max_workers=1)
        par = Trainer(train_ratio=0.8, walk_forward=False, max_workers=8)
        res_seq = seq.train(df, self._get_models(), group_col="sku", target="sales", dt="date")
        res_par = par.train(df, self._get_models(), group_col="sku", target="sales", dt="date")
        assert set(res_seq.keys()) == set(res_par.keys())
        for key in res_seq:
            assert res_seq[key]["sku"] == res_par[key]["sku"]
            assert res_seq[key]["mae"] == pytest.approx(res_par[key]["mae"], rel=1e-6)

    def test_group_failure_does_not_abort_other_groups(self, monkeypatch):
        df = _make_feature_df(n=60, skus=["A", "B", "C"])
        trainer = Trainer(train_ratio=0.8, walk_forward=False, max_workers=3)
        models = self._get_models()

        original = trainer._train_one_group

        def flaky(group_val, g, dt, target, exclude, trainable):
            if group_val == "B":
                raise RuntimeError("synthetic failure for SKU B")
            return original(group_val, g, dt, target, exclude, trainable)

        monkeypatch.setattr(trainer, "_train_one_group", flaky)

        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        skus = {v["sku"] for v in results.values()}
        assert skus == {"A", "C"}

    def test_group_failure_does_not_abort_other_groups_sequential(self, monkeypatch):
        # max_workers=1 takes the sequential branch, not the ThreadPoolExecutor
        # one — this is the path every low-core-count machine actually uses
        # (_resolve_max_workers caps at n_groups and at the backend's per-
        # session core budget), so it needs the same failure isolation.
        df = _make_feature_df(n=60, skus=["A", "B", "C"])
        trainer = Trainer(train_ratio=0.8, walk_forward=False, max_workers=1)
        models = self._get_models()

        original = trainer._train_one_group

        def flaky(group_val, g, dt, target, exclude, trainable):
            if group_val == "B":
                raise RuntimeError("synthetic failure for SKU B")
            return original(group_val, g, dt, target, exclude, trainable)

        monkeypatch.setattr(trainer, "_train_one_group", flaky)

        results = trainer.train(df, models, group_col="sku", target="sales", dt="date")
        skus = {v["sku"] for v in results.values()}
        assert skus == {"A", "C"}


# ─────────────────────────────────────────────────────────────────────────────
# ModelRouter
# ─────────────────────────────────────────────────────────────────────────────

class TestModelRouter:

    def _make_report(self, sku, series_type):
        return SKUReport(
            sku=sku,
            **_make_sku_reports(series_type=series_type),
        )

    def test_route_stable_assigns_lgb_xgb(self):
        router = ModelRouter({"lightgbm": {}, "xgboost": {}}, enabled=True)
        reports = {"S1": self._make_report("S1", SERIES_STABLE)}
        routing = router.route(reports)
        assert "lightgbm" in routing["S1"] or "xgboost" in routing["S1"]

    def test_route_short_falls_back_to_declared(self):
        # SHORT's table only lists naive models; when none of them is declared
        # the SKU falls back to the full user selection instead of being
        # silently dropped (or worse: training undeclared models).
        router = ModelRouter({"lightgbm": {}}, enabled=True)
        reports = {"S1": self._make_report("S1", SERIES_SHORT)}
        routing = router.route(reports)
        assert routing["S1"] == {"lightgbm"}

    def test_route_intermittent_narrows_to_declared_stat_models(self):
        router = ModelRouter({"lightgbm": {}, "croston": {}}, enabled=True)
        reports = {"S1": self._make_report("S1", SERIES_INTERMITTENT)}
        routing = router.route(reports)
        # croston is declared AND suited to intermittent → selected;
        # lightgbm is declared but not in the intermittent table → excluded.
        assert routing["S1"] == {"croston"}

    def test_route_never_assigns_undeclared_models(self):
        # Regression (2026-07-05): stat models from the routing table (lstm,
        # sarimax, ets…) used to run even when the user never selected them —
        # the quick-start flow selected 4 fast models and still trained a
        # Keras LSTM on CPU, turning a ~1-min training into 10+ minutes.
        declared = {"lightgbm": {}, "prophet": {}, "croston": {}, "xgboost": {}}
        router = ModelRouter(declared, enabled=True)
        reports = {
            "A": self._make_report("A", SERIES_STABLE),
            "B": self._make_report("B", SERIES_SEASONAL),
            "C": self._make_report("C", SERIES_VOLATILE),
        }
        routing = router.route(reports)
        for sku, models in routing.items():
            assert models <= set(declared), (
                f"{sku} routed to undeclared models: {models - set(declared)}"
            )
            assert models, f"{sku} left without any model"

    def test_disabled_routing_runs_all_declared(self):
        router = ModelRouter({"lightgbm": {}, "xgboost": {}}, enabled=False)
        reports = {"A": self._make_report("A", SERIES_SHORT),
                   "B": self._make_report("B", SERIES_STABLE)}
        routing = router.route(reports)
        # Disabled routing = every DECLARED model on every SKU — not the
        # union with all stat models, which trained models nobody selected.
        for sku in ["A", "B"]:
            assert routing[sku] == {"lightgbm", "xgboost"}

    def test_skus_for_model(self):
        router = ModelRouter({"lightgbm": {}, "xgboost": {}}, enabled=True)
        reports = {
            "S1": self._make_report("S1", SERIES_STABLE),
            "S2": self._make_report("S2", SERIES_STABLE),
        }
        routing = router.route(reports)
        skus = router.skus_for_model(routing, "lightgbm")
        assert isinstance(skus, list)

    def test_series_for_model(self):
        router = ModelRouter({"lightgbm": {}, "xgboost": {}}, enabled=True)
        reports = {
            "S1": self._make_report("S1", SERIES_STABLE),
            "S2": self._make_report("S2", SERIES_STABLE),
        }
        routing = router.route(reports)
        series_keys = router.series_for_model(routing, "lightgbm")
        assert isinstance(series_keys, list)
        # Currently series_for_model returns the same keys as routing (SKU strings)
        # In future versions it will convert to series_key(sku, store) format

    def test_summary_string_non_empty(self):
        router = ModelRouter({"lightgbm": {}}, enabled=True)
        reports = {"A": self._make_report("A", SERIES_STABLE)}
        routing = router.route(reports)
        summary = router.summary(routing)
        assert "lightgbm" in summary or len(summary) > 0

    def test_empty_reports_returns_empty_routing(self):
        router = ModelRouter({"lightgbm": {}}, enabled=True)
        routing = router.route({})
        assert routing == {}

    def test_declared_models_intersect_routing(self):
        # Only declared models should appear in routing for ML
        router = ModelRouter({"lightgbm": {}}, enabled=True)  # no xgboost
        reports = {"A": self._make_report("A", SERIES_STABLE)}
        routing = router.route(reports)
        assert "xgboost" not in routing["A"]


# ─────────────────────────────────────────────────────────────────────────────
# The graded model and the served model are not the same model
# ─────────────────────────────────────────────────────────────────────────────

class _RecordingModel:
    """Records the row count of every fit it is asked to perform.

    `__deepcopy__` returns self so that the Trainer's per-group and per-fold
    copies all report into one place — which is the whole point of the fixture.
    """

    def __init__(self):
        self.fit_sizes: list[int] = []
        self._mean = 0.0

    def __deepcopy__(self, memo):
        return self

    def fit(self, X, y):
        self.fit_sizes.append(len(X))
        self._mean = float(np.mean(y))
        return self

    def predict(self, X):
        return np.full(len(X), self._mean)


class TestServingModelSeesEverything:
    """The model that forecasts must be fitted on the whole history.

    The model that is GRADED has to stop at the cutoff — it is scored on what
    comes after it. The model that is SERVED has no such constraint, and
    leaving it at the cutoff meant the forecast a user receives came from a
    model blind to the newest fifth of their own data. Every statistical model
    in this pipeline already refits (`ets.py`, `arima.py`, `prophet.py`,
    `croston.py` each build a second model on the full series); the ML path did
    not, and the two sat in the same metrics table.
    """

    def _train(self, walk_forward: bool):
        df = _make_feature_df(n=100, skus=["A"])
        recorder = _RecordingModel()
        trainer = Trainer(train_ratio=0.8, walk_forward=walk_forward, wfv_splits=3)
        results = trainer.train(df, {"rec": recorder}, group_cols=["sku"],
                                target="sales", dt="date")
        return recorder, results

    @pytest.mark.parametrize("walk_forward", [False, True])
    def test_the_last_fit_uses_every_row(self, walk_forward):
        recorder, results = self._train(walk_forward)
        assert results, "the trainer produced no results"
        assert recorder.fit_sizes[-1] == 100

    @pytest.mark.parametrize("walk_forward", [False, True])
    def test_a_model_was_still_graded_at_the_cutoff(self, walk_forward):
        """80 rows of 100. Without this, the refit would have become a leak."""
        recorder, _ = self._train(walk_forward)
        assert 80 in recorder.fit_sizes[:-1]

    def test_a_failed_refit_still_serves_the_validated_model(self):
        """No forecast at all is worse than one from a model trained on less."""
        class _RefusesTheSecondFit(_RecordingModel):
            def fit(self, X, y):
                if len(X) == 100:
                    raise RuntimeError("no")
                return super().fit(X, y)

        df = _make_feature_df(n=100, skus=["A"])
        trainer = Trainer(train_ratio=0.8, walk_forward=False)
        results = trainer.train(df, {"rec": _RefusesTheSecondFit()},
                                group_cols=["sku"], target="sales", dt="date")
        entry = next(iter(results.values()))
        assert entry["fitted_model"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# Pooled cumulative-residual bank (stability.md 17b)
# ─────────────────────────────────────────────────────────────────────────────
#
# The product promises a service level on the demand that accumulates over the
# lead time, not on any single bucket, and `z * sigma_1 * sqrt(L)` understates
# that sum's variance when per-bucket errors are autocorrelated (measured on
# the demo catalogue at 1.53-1.82x, stability.md 17(b)). `Trainer` now banks
# the cumulative error of every walk-forward fold's recursive forecast — no
# extra `fit()` call, per `_bank_fold_cumulative_residuals`' docstring — pooled
# across the whole catalogue in scaled units. These tests pin the two
# properties that make the bank trustworthy: it costs nothing beyond what
# `_horizon_metrics` was already paying, and it actually measures a bigger
# cushion than the classical approximation on the series that approximation
# gets wrong.

BANK_HORIZON = 14
BANK_FEATURES_CFG = FeaturesConfig(lags=[1], rolling=[7], diffs=[1], calendar=True, ewm_spans=[])


def _trending_catalogue(n_days=110, n_skus=14, seed=3):
    """
    Many SKUs with a strong, roughly-linear trend and light noise.

    A tree model trained on lag/rolling features cannot extrapolate past the
    range it saw in training, so its recursive forecast on a trending series
    runs on one side of the truth for the whole horizon within any given
    fold — an error that is highly autocorrelated by construction, which is
    exactly the shape `sqrt(L)` assumes does not happen.
    """
    rng = np.random.default_rng(seed)
    frames = []
    for i in range(n_skus):
        dates = pd.date_range("2025-01-01", periods=n_days, freq="D")
        trend = rng.uniform(0.5, 2.5) * (1 if rng.random() < 0.5 else -1)
        base = 100.0 + trend * np.arange(n_days)
        noise = rng.normal(0, 3, n_days)
        demand = np.clip(base + noise, 0, None)
        frames.append(pd.DataFrame({"date": dates, "sku": f"S{i}", "demand": demand}))
    return pd.concat(frames, ignore_index=True)


def _train_with_bank(df, horizon=BANK_HORIZON, **kwargs):
    from forecasting_core.features.engineer import FeatureEngineer

    df_ml = FeatureEngineer(
        BANK_FEATURES_CFG, dt_col="date", target="demand", group_cols=["sku"],
    ).transform(df)
    trainer = Trainer(
        train_ratio=0.8, walk_forward=True, wfv_splits=3,
        gap=horizon, horizon=horizon, features_cfg=BANK_FEATURES_CFG, **kwargs,
    )
    results = trainer.train(
        df_ml, ModelFactory({"lightgbm": {"n_estimators": 60}}).build_ml(),
        group_cols=["sku"], target="demand", dt="date",
    )
    return trainer, results


class TestCumulativeResidualBankCosts:
    """`_bank_fold_cumulative_residuals` must reuse the fold's already-fitted
    model and only call `predict()` — see its docstring and
    `Trainer._horizon_metrics`', which the bank shares the constraint with."""

    def test_enabling_the_bank_adds_no_fit_calls(self):
        """
        Same data, same walk-forward geometry (`gap` fixed explicitly so the
        splitter's fold sizes cannot differ), one run with the bank off
        (`horizon=0`) and one with it on. `_RecordingModel.__deepcopy__`
        returns `self`, so every `fit()` call anywhere in the Trainer —
        per-fold, the graded refit, the full-history serving refit — lands in
        one list. `max_workers=1` keeps group order deterministic so the two
        lists are comparable position-by-position, not just by count.
        """
        df = _make_feature_df(n=100, skus=["A", "B", "C"])
        gap = 5

        recorder_off = _RecordingModel()
        off = Trainer(train_ratio=0.7, walk_forward=True, wfv_splits=3,
                      gap=gap, horizon=0, max_workers=1)
        results_off = off.train(df, {"rec": recorder_off}, group_cols=["sku"],
                                target="sales", dt="date")

        recorder_on = _RecordingModel()
        on = Trainer(train_ratio=0.7, walk_forward=True, wfv_splits=3,
                     gap=gap, horizon=5, features_cfg=BANK_FEATURES_CFG,
                     max_workers=1)
        results_on = on.train(df, {"rec": recorder_on}, group_cols=["sku"],
                              target="sales", dt="date")

        assert results_off and results_on, "both runs must actually train"
        assert recorder_off.fit_sizes == recorder_on.fit_sizes, (
            f"turning the cumulative-residual bank on changed fit() calls: "
            f"{recorder_off.fit_sizes} (off) vs {recorder_on.fit_sizes} (on) "
            "— the bank must reuse each fold's already-fitted model"
        )

    def test_fit_count_is_exactly_folds_plus_graded_plus_serving(self):
        """
        Direct accounting with the bank ON: one `fit()` per walk-forward
        fold (`entry["n_folds"]` of them), one for the model graded at the
        train cutoff, one for the model refit on the full history and
        served. A future change that fits a fresh model inside the bank
        builder — instead of reusing the fold's model — would add one fit
        per fold here and this assertion would catch it directly, not just
        via a diff against another run.
        """
        df = _make_feature_df(n=100, skus=["A"])
        recorder = _RecordingModel()
        trainer = Trainer(train_ratio=0.7, walk_forward=True, wfv_splits=3,
                          gap=5, horizon=5, features_cfg=BANK_FEATURES_CFG)
        results = trainer.train(df, {"rec": recorder}, group_cols=["sku"],
                                target="sales", dt="date")
        entry = next(iter(results.values()))
        assert len(recorder.fit_sizes) == entry["n_folds"] + 2


class TestCumulativeResidualBankAutocorrelation:
    """
    stability.md 17(b)'s entire premise: `sqrt(L)` assumes independent
    per-bucket errors, and understates the sum's spread when they are not.
    On a catalogue designed to produce persistent, same-direction errors
    across a recursive forecast, the bank's own measured spread at the full
    horizon must come out bigger than the classical bound built from its
    own 1-step spread — never smaller, which is the failure this bank exists
    to correct.
    """

    def test_measured_cumulative_spread_beats_the_independent_errors_bound(self):
        from scipy.stats import norm

        trainer, results = _train_with_bank(_trending_catalogue())
        assert results, "fixture must actually train"
        # The bank is keyed {model: {series stratum: {horizon: residuals}}}.
        # Per MODEL because a champion's cushion has to be built from that
        # champion's own errors rather than a mixture with whatever was trained
        # beside it; per STRATUM because a residual from a 70%-zero SKU and one
        # from a smooth daily seller do not describe the same uncertainty.
        # This fixture trains one model over one kind of series, so both levels
        # collapse to one — assert that, so a future change that silently
        # re-mixes them is visible here.
        banks = trainer.pooled_cumulative_residuals
        assert len(banks) == 1, f"fixture trains one model, got {sorted(banks)}"
        by_stratum = next(iter(banks.values()))
        assert by_stratum, "the model's bank has no stratum at all"
        # This assertion is about the SHAPE of the error as the horizon grows,
        # not about how the strata divide, so the strata are pooled back
        # together here. `classify_series` splits this fixture into more than
        # one, which is the stratification doing its job.
        #
        # EXCEPT the intermittent stratum: a strong enough downward trend
        # clips to zero for the back half of one SKU's history (see
        # `_trending_catalogue`'s own docstring — this is deliberate, not a
        # fixture bug), and `classify_series` correctly calls that SKU
        # intermittent. Its bank is now built by `_bank_fold_compound_
        # residuals` (stability.md 17b's compound model — see
        # `TestCompoundIntermittentBand`), which describes a DIFFERENT
        # phenomenon (a count-times-size distribution estimated from the
        # SKU's own history) than the one this test measures (autocorrelated
        # trend-extrapolation error). Pooling the two answers a mixed
        # question neither number was meant to answer.
        from forecasting_core.data.quality import SERIES_INTERMITTENT
        bank = {}
        for stratum, per_h in by_stratum.items():
            if stratum == SERIES_INTERMITTENT:
                continue
            for h, values in per_h.items():
                bank.setdefault(h, []).extend(np.asarray(values, dtype=float).tolist())
        assert 1 in bank and BANK_HORIZON in bank, "fixture must fund both horizons"
        assert len(bank[1]) >= 30 and len(bank[BANK_HORIZON]) >= 30, (
            "fixture must fund enough origins for a stable std estimate"
        )

        sigma1 = float(np.std(bank[1]))
        measured = float(np.std(bank[BANK_HORIZON]))
        classic = norm.ppf(0.95) * sigma1 * np.sqrt(BANK_HORIZON)

        assert measured > classic, (
            f"measured cumulative spread {measured:.3f} at L={BANK_HORIZON} "
            f"did not exceed the independent-errors bound {classic:.3f} — "
            "autocorrelated errors must widen the cushion, not narrow it"
        )
        # A comfortable margin, not a coin flip against numerical noise —
        # stability.md measured 1.53-1.82x on the demo catalogue; this
        # fixture's errors are far more persistent than that.
        assert measured > 1.5 * classic, (
            f"measured spread {measured:.3f} only barely beat the classical "
            f"bound {classic:.3f} — the effect should be large on a series "
            "built specifically to have persistent errors"
        )

    def test_series_scale_and_bank_are_attached_to_every_entry(self):
        """`train()` hands every entry its own level as `series_scale` and the
        bank belonging to ITS model AND ITS stratum — the shape
        `Pipeline._demand_risk` depends on to read a per-SKU champion's band
        at all.

        The bank is per model on purpose. A single bank shared across families
        pools one model's errors with another's and publishes the mixture as
        the cushion for whichever of them wins the SKU — measured end to end,
        that delivered 83.3% against a nominal 95%, no better than the
        classical formula it replaces (stability.md 17b)."""
        trainer, results = _train_with_bank(_trending_catalogue(n_skus=4))
        assert results
        banks = trainer.pooled_cumulative_residuals
        for entry in results.values():
            assert entry.get("series_scale") is not None
            assert entry.get("series_scale") > 0
            assert entry.get("series_stratum")
            model = str(entry.get("model"))
            stratum = str(entry.get("series_stratum"))
            assert entry.get("cumulative_residuals_by_horizon") is banks[model][stratum], (
                f"entry for {model}/{stratum} was handed a bank that is not "
                f"that (model, stratum) pair's own"
            )


# ─────────────────────────────────────────────────────────────────────────────
# What the cumulative-residual bank divides by (stability.md 17b, second half)
# ─────────────────────────────────────────────────────────────────────────────
#
# The series' own MEAN used to be the denominator. On a zero-inflated series
# it is set mostly by how OFTEN demand happens, not by how BIG an order is
# when it does — so a fixture with a fixed order size and a low hit rate makes
# the old denominator visibly wrong: it reports a "typical" size far smaller
# than every single nonzero observation actually was.

class TestSeriesScale:

    def test_mean_of_positives_replaces_the_series_mean(self):
        """
        20% of days sell exactly 10 units, the rest sell 0. The mean (2.0) is
        not the size of an order — no day ever sold 2 units — it is the size
        diluted by how rarely one happens. The new denominator reports the
        one number that actually describes an order: 10.0.
        """
        y = pd.Series([10.0, 0, 0, 0, 0] * 20)
        old_denominator = max(abs(float(y.mean())), 1e-3)
        assert old_denominator == pytest.approx(2.0), "fixture assumption changed"

        new_denominator = _series_scale(y)
        assert new_denominator == pytest.approx(10.0), (
            f"expected the mean of the NONZERO observations (10.0), got "
            f"{new_denominator} — the old mean-based scale (2.0) would have "
            f"been 5x too small, exactly the amplification stability.md 17b "
            f"measured on real intermittent series"
        )

    def test_different_frequency_same_order_size_now_scale_alike(self):
        """
        The defect in one sentence: two series with the SAME typical order
        size but different order FREQUENCY used to be scaled by numbers far
        apart, so residuals from one described neither series once pooled.
        A frequent seller (40% hit rate) and a rare one (10% hit rate), both
        selling exactly 8 units when they sell at all, must now scale alike.
        """
        frequent = pd.Series(([8.0] * 4 + [0.0] * 6) * 10)   # 40% hit rate
        rare = pd.Series(([8.0] * 1 + [0.0] * 9) * 10)        # 10% hit rate

        old_frequent = max(abs(float(frequent.mean())), 1e-3)
        old_rare = max(abs(float(rare.mean())), 1e-3)
        assert old_frequent / old_rare == pytest.approx(4.0), (
            "the old mean-based scale differs 4x between series that sell "
            "the identical 8 units whenever they sell at all"
        )

        new_frequent = _series_scale(frequent)
        new_rare = _series_scale(rare)
        assert new_frequent == pytest.approx(8.0)
        assert new_rare == pytest.approx(8.0)
        assert new_frequent == pytest.approx(new_rare), (
            "series with the same order size must scale alike regardless of "
            "how often that order happens"
        )

    def test_all_zero_series_falls_back_to_the_old_floor(self):
        """No positive observation to speak of — the only sane answer is the
        same floor the old mean-based scale used, not a division by zero."""
        y = pd.Series([0.0] * 30)
        assert _series_scale(y) == pytest.approx(1e-3)


# ─────────────────────────────────────────────────────────────────────────────
# Stratifying the cumulative-residual bank by series class (stability.md 17b,
# second half)
# ─────────────────────────────────────────────────────────────────────────────
#
# A residual from a 70%-zero SKU and one from a smooth daily seller do not
# belong in the same bank: one empirical quantile pooled across both describes
# neither. `_cumulative_bank` is now keyed by MODEL and then by
# `classify_series` stratum, so a busy stratum's abundance of residuals can
# never quietly rescue a thin one — and a thin stratum's cushion must stay
# absent rather than being computed from a mixture that isn't its own.

def _dense_catalogue(n_skus=12, n_days=110, seed=5):
    """Smooth, always-positive series — `classify_series` labels these
    `stable` at the default thresholds (no zeros, low CV, no strong weekly
    seasonality)."""
    rng = np.random.default_rng(seed)
    frames = []
    for i in range(n_skus):
        dates = pd.date_range("2025-01-01", periods=n_days, freq="D")
        demand = np.clip(50.0 + rng.normal(0, 4, n_days), 1, None)
        frames.append(pd.DataFrame({"date": dates, "sku": f"D{i}", "demand": demand}))
    return pd.concat(frames, ignore_index=True)


def _slow_mover_catalogue(n_skus=2, n_days=110, seed=7):
    """Zero-heavy series — `classify_series` labels these `intermittent` at
    the default 40% zero-ratio threshold, regardless of their CV."""
    rng = np.random.default_rng(seed)
    frames = []
    for i in range(n_skus):
        dates = pd.date_range("2025-01-01", periods=n_days, freq="D")
        hit = rng.random(n_days) < 0.25          # ~75% zero buckets
        size = rng.poisson(4.0, n_days) + 1.0
        demand = np.where(hit, size, 0.0)
        frames.append(pd.DataFrame({"date": dates, "sku": f"S{i}", "demand": demand}))
    return pd.concat(frames, ignore_index=True)


def _thin_volatile_catalogue(n_skus=2, n_days=110, seed=13):
    """Always-positive but wildly swingy series — `classify_series` labels
    these `volatile` (CV >= 1.5) at the default thresholds, never crossing the
    zero-ratio threshold that would make them `intermittent` instead.

    Used in place of `_slow_mover_catalogue` for the general "a thin stratum
    is never rescued" tests: since `_bank_fold_compound_residuals` now funds
    the INTERMITTENT stratum from a parametric model instead of more real
    origins (see `TestCompoundIntermittentBand` below), an intermittent
    fixture no longer stays thin the way it used to, and would make those
    tests assert something no longer true rather than test the general
    stratification invariant they exist for.
    """
    rng = np.random.default_rng(seed)
    frames = []
    for i in range(n_skus):
        dates = pd.date_range("2025-01-01", periods=n_days, freq="D")
        # sigma=1.8, not 1.2. The asymptotic CV of a lognormal at 1.2 is
        # ~1.79, comfortably past the 1.5 threshold — but the SAMPLE CV
        # at n=110 comes out around 1.16, so these series classified as
        # `stable` and the fixture silently stopped testing two strata.
        # Measured, not reasoned: 1.8 gives a sample CV around 3.1.
        demand = rng.lognormal(mean=1.0, sigma=1.8, size=n_days)
        frames.append(pd.DataFrame({"date": dates, "sku": f"V{i}", "demand": demand}))
    return pd.concat(frames, ignore_index=True)


class TestCumulativeResidualBankStratification:

    def test_a_thin_stratum_is_not_rescued_by_a_busy_one(self):
        """
        The catalogue mixes 12 well-funded `stable` SKUs (enough folds to
        clear `MIN_RESIDUALS_PER_HORIZON` on their own) with 2 `volatile`
        SKUs (too few folds to clear it on their own, and no compound model
        applies to this stratum — see `_thin_volatile_catalogue`). Pooled
        into ONE bank — the pre-stratification behaviour — the mixture would
        total well past the floor and the volatile SKUs would silently
        borrow the stable SKUs' abundance. Stratified, the volatile stratum
        must stay thin on its own.
        """
        from forecasting_core.evaluation.conformal import MIN_RESIDUALS_PER_HORIZON
        from forecasting_core.data.quality import SERIES_STABLE, SERIES_VOLATILE

        catalogue = pd.concat(
            [_dense_catalogue(), _thin_volatile_catalogue()], ignore_index=True,
        )
        trainer, results = _train_with_bank(catalogue)
        assert results, "fixture must actually train"

        banks = trainer.pooled_cumulative_residuals
        model = next(iter(banks))
        strata = banks[model]
        assert SERIES_STABLE in strata, f"expected a stable stratum, got {sorted(strata)}"
        assert SERIES_VOLATILE in strata, (
            f"expected a volatile stratum, got {sorted(strata)}"
        )

        stable_n = len(strata[SERIES_STABLE].get(1, []))
        volatile_n = len(strata[SERIES_VOLATILE].get(1, []))

        assert stable_n >= MIN_RESIDUALS_PER_HORIZON, (
            f"fixture must fund the stable stratum on its own, got {stable_n}"
        )
        assert volatile_n < MIN_RESIDUALS_PER_HORIZON, (
            f"fixture must leave the volatile stratum thin on its own, "
            f"got {volatile_n} — increase n_skus in _dense_catalogue or "
            f"reduce it in _thin_volatile_catalogue if this fixture drifted"
        )
        # The property stratification exists for: the pooled TOTAL clears the
        # floor even though the volatile stratum alone does not — proving a
        # mixed pool would have hidden the shortfall this test is about.
        assert stable_n + volatile_n >= MIN_RESIDUALS_PER_HORIZON

    def test_thin_stratum_publishes_no_band_end_to_end(self):
        """
        stability.md 17b: 'a stratum too thin to quantify publishes nothing
        rather than a confident number from six points.' Runs the thin
        volatile stratum's own entry through `Pipeline._demand_risk` (the
        actual reader, see TestDemandRiskBank in test_pipeline.py) and checks
        it is dropped, while the well-funded stable stratum's entry survives.

        Uses `_thin_volatile_catalogue`, not an intermittent one — the
        compound model in `TestCompoundIntermittentBand` now funds the
        intermittent stratum from far fewer real origins than this floor
        needs, which is a deliberate exception to "thin stays thin", not a
        counterexample to it. Volatile has no such exception.
        """
        from forecasting_core.pipelines.pipeline import Pipeline

        catalogue = pd.concat(
            [_dense_catalogue(), _thin_volatile_catalogue()], ignore_index=True,
        )
        trainer, results = _train_with_bank(catalogue)
        assert results

        stable_entry = next(
            e for e in results.values() if e.get("sku", "").startswith("D")
        )
        thin_entry = next(
            e for e in results.values() if e.get("sku", "").startswith("V")
        )
        assert stable_entry["series_stratum"] != thin_entry["series_stratum"]

        pipeline = Pipeline.__new__(Pipeline)
        pipeline._champion_by_sku = {
            stable_entry["sku"]: stable_entry["model"],
            thin_entry["sku"]: thin_entry["model"],
        }
        risk = pipeline._demand_risk(
            {
                f"k_{stable_entry['sku']}": stable_entry,
                f"k_{thin_entry['sku']}": thin_entry,
            },
            [0.95],
        )

        assert stable_entry["sku"] in risk, (
            "the well-funded stable stratum's champion should publish a band"
        )
        assert thin_entry["sku"] not in risk, (
            "the thin volatile stratum's champion published a band built "
            "from fewer than MIN_RESIDUALS_PER_HORIZON of its own residuals"
        )


# ─────────────────────────────────────────────────────────────────────────────
# The compound model for the intermittent stratum (stability.md 17b, third
# refutation's follow-up)
# ─────────────────────────────────────────────────────────────────────────────
#
# Three attempts at re-keying the SAME empirical-quantile instrument for
# intermittent demand were all measured neutral or worse end to end (per-
# horizon bands twice, then stratifying the pooled bank by series class).
# stability.md's own conclusion: "the next idea should not be another key."
# This changes the INSTRUMENT instead: a lead-time sum of intermittent demand
# is a count of demand occasions times a size per occasion, and
# `Trainer._bank_fold_compound_residuals` estimates that compound
# distribution from each SKU's OWN history (see `evaluation/compound.py`)
# rather than from the catalogue's rolling origins. The property these tests
# pin — a bank funded well past `MIN_RESIDUALS_PER_HORIZON` from a catalogue
# too small to fund it any other way — did NOT hold before this existed: it
# is exactly the situation `test_thin_stratum_publishes_no_band_end_to_end`
# used to demonstrate WITH an intermittent fixture, before this module moved
# that demonstration to a stratum the compound model does not touch.

class TestCompoundIntermittentBand:

    def test_intermittent_bank_is_funded_from_far_fewer_real_origins(self):
        """
        `_slow_mover_catalogue`'s 2 SKUs x 3 walk-forward folds can produce at
        most 6 REAL rolling-origin residuals at horizon 1 — the same fixture
        `TestCumulativeResidualBankStratification` used to show the
        intermittent stratum staying thin. With the compound model wired in,
        the SAME fixture funds the bank past `MIN_RESIDUALS_PER_HORIZON`
        because most of the residuals are simulated from each SKU's own
        estimated (occasion rate, size distribution), not counted from folds.
        """
        from forecasting_core.evaluation.conformal import MIN_RESIDUALS_PER_HORIZON
        from forecasting_core.data.quality import SERIES_INTERMITTENT

        trainer, results = _train_with_bank(_slow_mover_catalogue())
        assert results, "fixture must actually train"

        banks = trainer.pooled_cumulative_residuals
        model = next(iter(banks))
        strata = banks[model]
        assert SERIES_INTERMITTENT in strata, (
            f"expected an intermittent stratum, got {sorted(strata)}"
        )
        intermittent_n = len(strata[SERIES_INTERMITTENT].get(1, []))
        assert intermittent_n > 6, (
            f"only {intermittent_n} residuals at horizon 1 — no more than the "
            "real rolling origins alone could fund, which means the compound "
            "model did not fire"
        )
        assert intermittent_n >= MIN_RESIDUALS_PER_HORIZON, (
            f"got {intermittent_n}, still short of the publishing floor — "
            "this is the exact shortfall stability.md 17b measured for "
            "intermittent demand before the compound model existed"
        )

    def test_intermittent_champion_now_publishes_a_band_end_to_end(self):
        """
        The reversal of `test_thin_stratum_publishes_no_band_end_to_end`,
        pinned for the stratum that test used to cover: an intermittent
        champion built from a catalogue too small to fund a real empirical
        quantile now publishes a monotonically non-decreasing cumulative
        band anyway, because `Pipeline._demand_risk` needs no new vocabulary
        to read a compound-funded bank — it looks identical in shape.
        """
        from forecasting_core.pipelines.pipeline import Pipeline

        trainer, results = _train_with_bank(_slow_mover_catalogue())
        assert results
        entry = next(iter(results.values()))

        pipeline = Pipeline.__new__(Pipeline)
        pipeline._champion_by_sku = {entry["sku"]: entry["model"]}
        risk = pipeline._demand_risk({f"k_{entry['sku']}": entry}, [0.95])

        assert entry["sku"] in risk, (
            "an intermittent champion trained on a 2-SKU catalogue published "
            "no band — the compound model exists precisely so this is no "
            "longer gated on the catalogue's own rolling-origin count"
        )
        offsets = risk[entry["sku"]]["cumulative_offsets"]
        horizons = sorted(int(h) for h in offsets)
        assert horizons, "band must cover at least one horizon"
        values = [offsets[str(h)]["0.95"] for h in horizons]
        assert values == sorted(values), (
            "cumulative offsets must be non-decreasing in the horizon — "
            "see conformal.enforce_horizon_monotonic"
        )

    def test_compound_bank_is_not_used_outside_the_intermittent_stratum(self):
        """`_bank_fold_compound_residuals` is gated on `stratum ==
        SERIES_INTERMITTENT` — a stable, always-selling catalogue's bank must
        be built ONLY from real rolling origins, never inflated by the
        compound model. `_dense_catalogue` funds the floor on real origins
        alone (see `TestCumulativeResidualBankStratification`), so this pins
        the ceiling instead: an upper bound consistent with
        folds x SKUs x n_sku_per_fold, not the thousands per fold the
        compound model would add."""
        from forecasting_core.data.quality import SERIES_STABLE

        trainer, results = _train_with_bank(_dense_catalogue(n_skus=3))
        assert results
        banks = trainer.pooled_cumulative_residuals
        model = next(iter(banks))
        stable_n = len(banks[model][SERIES_STABLE].get(1, []))
        # 3 SKUs x up to 3 folds each = 9 real residuals at most, nowhere
        # near what n_sim (thousands per fold) would produce if the compound
        # model fired for a stable stratum by mistake.
        assert stable_n <= 9, (
            f"got {stable_n} residuals for a 3-SKU stable catalogue — the "
            "compound model must be gated to the intermittent stratum only"
        )
