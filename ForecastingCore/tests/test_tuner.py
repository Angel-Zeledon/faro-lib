"""Tests for forecasting_core.training.tuner: HyperparamTuner, SEARCH_SPACES."""

import numpy as np
import pandas as pd
import pytest

import forecasting_core.training.tuner as tuner_module
from forecasting_core.training.tuner import (
    HyperparamTuner,
    SEARCH_SPACES,
    _make_model,
)
from forecasting_core.evaluation.metrics import DEFAULT_STOCKOUT_MULTIPLIER


# ---------------------------------------------------------------------------
# SEARCH_SPACES
# ---------------------------------------------------------------------------

class TestSearchSpaces:

    def test_lightgbm_defined(self):
        assert "lightgbm" in SEARCH_SPACES

    def test_xgboost_defined(self):
        assert "xgboost" in SEARCH_SPACES

    def test_each_entry_has_four_parts(self):
        for model, space in SEARCH_SPACES.items():
            for entry in space:
                name, kind, *args = entry
                assert isinstance(name, str)
                assert kind in ("int", "float", "float_log", "categorical"), \
                    f"Unknown kind '{kind}' in {model}.{name}"
                assert len(args) >= 2


# ---------------------------------------------------------------------------
# _make_model
# ---------------------------------------------------------------------------

class TestMakeModel:

    def test_lightgbm_returns_fittable_model(self):
        model = _make_model("lightgbm", {"n_estimators": 5})
        assert hasattr(model, "fit") and hasattr(model, "predict")

    def test_xgboost_returns_fittable_model(self):
        model = _make_model("xgboost", {"n_estimators": 5})
        assert hasattr(model, "fit") and hasattr(model, "predict")

    def test_unsupported_model_raises(self):
        with pytest.raises(ValueError, match="No search space"):
            _make_model("unknown_model", {})

    def test_empty_params_uses_defaults(self):
        # Should not raise with empty dict
        model = _make_model("lightgbm", {})
        assert model is not None


# ---------------------------------------------------------------------------
# HyperparamTuner._make_splits — backed by the shared WalkForwardSplitter,
# not a private copy, and honouring `gap` the same way Trainer._wfv does.
# ---------------------------------------------------------------------------

class TestMakeSplits:

    def test_enough_data_returns_splits(self):
        tuner = HyperparamTuner("lightgbm", n_trials=1, cv_splits=3)
        splits = tuner._make_splits(100)
        assert len(splits) == 3

    def test_too_few_data_returns_empty(self):
        tuner = HyperparamTuner("lightgbm", n_trials=1, cv_splits=3)
        splits = tuner._make_splits(5)
        assert splits == []

    def test_train_before_test_in_all_splits(self):
        tuner = HyperparamTuner("lightgbm", n_trials=1, cv_splits=3)
        for tr, te in tuner._make_splits(100):
            assert len(tr) > 0 and len(te) > 0
            assert max(tr) < min(te)

    def test_train_size_increases_across_folds(self):
        tuner = HyperparamTuner("lightgbm", n_trials=1, cv_splits=4)
        splits = tuner._make_splits(120)
        sizes = [len(tr) for tr, _ in splits]
        for i in range(1, len(sizes)):
            assert sizes[i] >= sizes[i - 1]

    def test_single_split(self):
        tuner = HyperparamTuner("lightgbm", n_trials=1, cv_splits=1)
        splits = tuner._make_splits(50)
        assert len(splits) == 1

    def test_uses_the_shared_walk_forward_splitter(self):
        """Regression guard for the private _wfv_splits copy: the tuner must
        build its folds from the same WalkForwardSplitter Trainer uses, so a
        change to the splitter's geometry cannot silently diverge between the
        two callers again."""
        from forecasting_core.training.trainer import WalkForwardSplitter

        tuner = HyperparamTuner("lightgbm", n_trials=1, cv_splits=3, gap=5)
        expected = WalkForwardSplitter(3, min_train_ratio=0.5, gap=5).split(100)
        actual = tuner._make_splits(100)
        assert len(actual) == len(expected)
        for (tr_a, te_a), (tr_e, te_e) in zip(actual, expected):
            assert list(tr_a) == list(tr_e)
            assert list(te_a) == list(te_e)


# ---------------------------------------------------------------------------
# HyperparamTuner gap — must actually shrink the training windows the
# objective fits on, the way Trainer._wfv's gap shrinks its own folds.
# ---------------------------------------------------------------------------

class TestGapShrinksTrainingWindows:

    def test_nonzero_gap_shrinks_every_training_window(self):
        n = 150
        no_gap = HyperparamTuner("lightgbm", cv_splits=3, gap=0)._make_splits(n)
        with_gap = HyperparamTuner("lightgbm", cv_splits=3, gap=10)._make_splits(n)

        assert len(no_gap) == len(with_gap)  # gap must not cost a fold here
        assert len(with_gap) > 0
        for (tr_no_gap, te_no_gap), (tr_gap, te_gap) in zip(no_gap, with_gap):
            assert len(tr_gap) < len(tr_no_gap)
            # the test window itself is untouched by the gap
            assert list(te_gap) == list(te_no_gap)

    def test_gap_reaches_the_rows_fit_actually_receives(self, monkeypatch):
        """Not just the split indices: the model .fit() call in the objective
        must be handed the shrunk window, not the full one."""
        X, y = _make_xy(n=150)
        received_train_sizes = []

        class _RecordingModel:
            def fit(self, X_tr, y_tr):
                received_train_sizes.append(len(X_tr))
                return self

            def predict(self, X_te):
                return np.zeros(len(X_te))

        monkeypatch.setattr(tuner_module, "_make_model", lambda name, params: _RecordingModel())

        tuner_no_gap = HyperparamTuner("lightgbm", cv_splits=3, gap=0)
        splits_no_gap = tuner_no_gap._make_splits(len(X))
        tuner_no_gap._fold_cost({}, X, y, splits_no_gap)
        sizes_no_gap = list(received_train_sizes)

        received_train_sizes.clear()
        tuner_gap = HyperparamTuner("lightgbm", cv_splits=3, gap=20)
        splits_gap = tuner_gap._make_splits(len(X))
        tuner_gap._fold_cost({}, X, y, splits_gap)
        sizes_gap = list(received_train_sizes)

        assert len(sizes_gap) == len(sizes_no_gap) and len(sizes_gap) > 0
        for gap_size, no_gap_size in zip(sizes_gap, sizes_no_gap):
            assert gap_size < no_gap_size


# ---------------------------------------------------------------------------
# HyperparamTuner.tune
# ---------------------------------------------------------------------------

def _make_xy(n=60):
    rng = np.random.default_rng(42)
    X = pd.DataFrame({
        "lag_1": rng.normal(50, 5, n),
        "lag_7": rng.normal(50, 5, n),
        "roll_mean_7": rng.normal(50, 5, n),
    })
    y = pd.Series(rng.normal(50, 5, n), name="sales")
    return X, y


class TestHyperparamTunerTune:

    def test_tune_returns_dict(self):
        optuna = pytest.importorskip("optuna", reason="optuna not installed")
        X, y = _make_xy(n=80)
        tuner = HyperparamTuner("lightgbm", n_trials=3, timeout=30, cv_splits=2)
        params = tuner.tune(X, y)
        assert isinstance(params, dict)

    def test_tune_returns_known_param_keys(self):
        pytest.importorskip("optuna", reason="optuna not installed")
        X, y = _make_xy(n=80)
        tuner = HyperparamTuner("lightgbm", n_trials=3, timeout=30, cv_splits=2)
        params = tuner.tune(X, y)
        if params:  # empty dict is valid when optuna fails
            for key in params:
                assert any(key == entry[0] for entry in SEARCH_SPACES["lightgbm"])

    def test_tune_unsupported_model_returns_empty(self):
        pytest.importorskip("optuna", reason="optuna not installed")
        X, y = _make_xy(n=80)
        tuner = HyperparamTuner("prophet", n_trials=3)
        params = tuner.tune(X, y)
        assert params == {}

    def test_tune_too_small_data_returns_empty(self):
        pytest.importorskip("optuna", reason="optuna not installed")
        X, y = _make_xy(n=4)
        tuner = HyperparamTuner("lightgbm", n_trials=3, cv_splits=3)
        params = tuner.tune(X, y)
        assert params == {}

    def test_tune_without_optuna_returns_empty(self, monkeypatch):
        """Simulate optuna not being installed."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "optuna":
                raise ImportError("mocked: optuna not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        X, y = _make_xy(n=80)
        tuner = HyperparamTuner("lightgbm", n_trials=3)
        params = tuner.tune(X, y)
        assert params == {}

    def test_n_trials_and_timeout_passed_to_optuna(self, monkeypatch):
        """`n_trials`/`timeout` must still reach optuna.Study.optimize unchanged —
        a refactor of the objective or the splitter must not lose them."""
        optuna = pytest.importorskip("optuna", reason="optuna not installed")
        X, y = _make_xy(n=80)
        captured = {}

        class _FakeStudy:
            best_params = {"n_estimators": 200}
            best_value = 1.0
            trials = []

            def optimize(self, objective, n_trials=None, timeout=None, **kwargs):
                captured["n_trials"] = n_trials
                captured["timeout"] = timeout

        monkeypatch.setattr(optuna, "create_study", lambda **kw: _FakeStudy())
        tuner = HyperparamTuner("lightgbm", n_trials=7, timeout=42, cv_splits=2)
        result = tuner.tune(X, y)

        assert captured["n_trials"] == 7
        assert captured["timeout"] == 42
        assert result == {"n_estimators": 200}


# ---------------------------------------------------------------------------
# The objective: it must be the asymmetric cost, not MAE. MAE scores an
# under-forecast and an over-forecast of the same magnitude identically; the
# champion selection (CHAMPION_METRIC_ORDER, led by cost_horizon) does not —
# a stockout is worth `stockout_multiplier` times a surplus of the same size.
# ---------------------------------------------------------------------------

class TestObjectiveIsAsymmetric:

    def test_fold_cost_prefers_overforecast_of_equal_magnitude(self, monkeypatch):
        """Two candidate parameter sets deviate from the truth by the same
        magnitude, one below (under-forecast) and one above (over-forecast).
        Under MAE these tie; under the asymmetric cost the over-forecaster
        must score strictly lower, because the tuner is meant to prefer it."""
        X, y = _make_xy(n=40)
        splits = [(np.arange(20), np.arange(20, 40))]
        shift_magnitude = 5.0

        class _ShiftedModel:
            """Ignores the training data entirely and always predicts the
            true test-fold value plus a fixed shift — isolates the objective's
            scoring rule from anything the real LightGBM/XGBoost model does."""

            def __init__(self, shift):
                self.shift = shift

            def fit(self, X_tr, y_tr):
                return self

            def predict(self, X_te):
                return y.loc[X_te.index].to_numpy() + self.shift

        def fake_make_model(model_name, params):
            return _ShiftedModel(params["shift"])

        monkeypatch.setattr(tuner_module, "_make_model", fake_make_model)

        tuner = HyperparamTuner("lightgbm", stockout_multiplier=3.0)
        cost_underforecast = tuner._fold_cost({"shift": -shift_magnitude}, X, y, splits)
        cost_overforecast = tuner._fold_cost({"shift": shift_magnitude}, X, y, splits)

        assert cost_overforecast < cost_underforecast
        # exact values: surplus costs 1x/unit, shortfall costs stockout_multiplier x/unit
        assert cost_overforecast == pytest.approx(shift_magnitude)
        assert cost_underforecast == pytest.approx(shift_magnitude * 3.0)

    def test_stockout_multiplier_is_forwarded_to_the_cost(self, monkeypatch):
        """A different stockout_multiplier must change the fold cost of an
        under-forecast — proof the parameter actually reaches asymmetric_cost
        rather than being accepted and ignored."""
        X, y = _make_xy(n=40)
        splits = [(np.arange(20), np.arange(20, 40))]

        class _AlwaysUnder:
            def fit(self, X_tr, y_tr):
                return self

            def predict(self, X_te):
                return y.loc[X_te.index].to_numpy() - 4.0

        monkeypatch.setattr(tuner_module, "_make_model", lambda name, params: _AlwaysUnder())

        low_multiplier = HyperparamTuner("lightgbm", stockout_multiplier=1.0)
        high_multiplier = HyperparamTuner("lightgbm", stockout_multiplier=5.0)

        cost_low = low_multiplier._fold_cost({}, X, y, splits)
        cost_high = high_multiplier._fold_cost({}, X, y, splits)

        assert cost_high > cost_low
        assert cost_low == pytest.approx(4.0)
        assert cost_high == pytest.approx(20.0)

    def test_default_stockout_multiplier_matches_metrics_module(self):
        """The tuner's default must track the product's own default rather than
        inventing a second one that can silently drift from it."""
        tuner = HyperparamTuner("lightgbm")
        assert tuner.stockout_multiplier == DEFAULT_STOCKOUT_MULTIPLIER
