"""
Tests for forecasting_core.training: Trainer, WalkForwardSplitter, ModelRouter.
"""

import pytest
import numpy as np
import pandas as pd

from forecasting_core.training.trainer import Trainer, WalkForwardSplitter
from forecasting_core.training.router import ModelRouter
from forecasting_core.models.factory import ModelFactory
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
