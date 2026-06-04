"""Tests for forecasting_core.training.tuner: HyperparamTuner, SEARCH_SPACES, _wfv_splits."""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.training.tuner import (
    HyperparamTuner,
    SEARCH_SPACES,
    _make_model,
)


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
# HyperparamTuner._wfv_splits
# ---------------------------------------------------------------------------

class TestWFVSplits:

    def test_enough_data_returns_splits(self):
        tuner = HyperparamTuner("lightgbm", n_trials=1, cv_splits=3)
        splits = tuner._wfv_splits(100)
        assert len(splits) == 3

    def test_too_few_data_returns_empty(self):
        tuner = HyperparamTuner("lightgbm", n_trials=1, cv_splits=3)
        splits = tuner._wfv_splits(5)
        assert splits == []

    def test_train_before_test_in_all_splits(self):
        tuner = HyperparamTuner("lightgbm", n_trials=1, cv_splits=3)
        for tr, te in tuner._wfv_splits(100):
            assert len(tr) > 0 and len(te) > 0
            assert max(tr) < min(te)

    def test_train_size_increases_across_folds(self):
        tuner = HyperparamTuner("lightgbm", n_trials=1, cv_splits=4)
        splits = tuner._wfv_splits(120)
        sizes = [len(tr) for tr, _ in splits]
        for i in range(1, len(sizes)):
            assert sizes[i] >= sizes[i - 1]

    def test_single_split(self):
        tuner = HyperparamTuner("lightgbm", n_trials=1, cv_splits=1)
        splits = tuner._wfv_splits(50)
        assert len(splits) == 1


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
