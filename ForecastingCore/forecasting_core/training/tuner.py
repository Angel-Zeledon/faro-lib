"""
HyperparamTuner — Optuna-based hyperparameter optimization for ML models.

Uses walk-forward CV as the objective to prevent look-ahead bias.

Example:
    tuner = HyperparamTuner("lightgbm", n_trials=30)
    best_params = tuner.tune(X_train, y_train, splitter)
    # best_params: {"n_estimators": 450, "learning_rate": 0.04, ...}
"""

from __future__ import annotations

import logging
from typing import Dict, Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Search spaces
# ---------------------------------------------------------------------------

SEARCH_SPACES: Dict[str, list] = {
    "lightgbm": [
        # (param_name, type, *args)  — type ∈ int | float | float_log | categorical
        ("n_estimators",      "int",       100, 1000),
        ("learning_rate",     "float_log", 1e-3, 0.3),
        ("num_leaves",        "int",       20,  200),
        ("min_child_samples", "int",       5,   100),
        ("subsample",         "float",     0.5, 1.0),
        ("colsample_bytree",  "float",     0.5, 1.0),
        ("reg_alpha",         "float_log", 1e-4, 10.0),
        ("reg_lambda",        "float_log", 1e-4, 10.0),
    ],
    "xgboost": [
        ("n_estimators",     "int",       100, 1000),
        ("learning_rate",    "float_log", 1e-3, 0.3),
        ("max_depth",        "int",       3,   10),
        ("subsample",        "float",     0.5, 1.0),
        ("colsample_bytree", "float",     0.5, 1.0),
        ("reg_alpha",        "float_log", 1e-4, 10.0),
        ("reg_lambda",       "float_log", 1e-4, 10.0),
        ("min_child_weight", "int",       1,   20),
    ],
}


def _suggest(trial, model_name: str) -> Dict[str, Any]:
    """Sample hyperparameters from the search space for `model_name`."""
    space = SEARCH_SPACES.get(model_name, [])
    params: Dict[str, Any] = {}
    for entry in space:
        name, kind, *args = entry
        if kind == "int":
            params[name] = trial.suggest_int(name, *args)
        elif kind == "float":
            params[name] = trial.suggest_float(name, *args)
        elif kind == "float_log":
            params[name] = trial.suggest_float(name, *args, log=True)
        elif kind == "categorical":
            params[name] = trial.suggest_categorical(name, args)
    return params


def _make_model(model_name: str, params: Dict[str, Any]):
    """Instantiate an ML model from a params dict."""
    p = params or {}
    if model_name == "lightgbm":
        from lightgbm import LGBMRegressor
        return LGBMRegressor(**{"n_jobs": 1, **p, "verbosity": -1})
    if model_name == "xgboost":
        from xgboost import XGBRegressor
        return XGBRegressor(**{"n_jobs": 1, **p, "verbosity": 0})
    raise ValueError(f"No search space defined for model '{model_name}'")


# ---------------------------------------------------------------------------
# Tuner
# ---------------------------------------------------------------------------

class HyperparamTuner:
    """
    Optuna-based hyperparameter tuner for walk-forward time-series CV.

    Args:
        model_name:  One of the keys in SEARCH_SPACES ("lightgbm", "xgboost").
        n_trials:    Number of Optuna trials (default 30).
        timeout:     Maximum wall time in seconds (default 300 = 5 min).
        cv_splits:   Walk-forward splits used in the objective (default 3).
    """

    def __init__(
        self,
        model_name: str,
        n_trials: int = 30,
        timeout: int = 300,
        cv_splits: int = 3,
    ):
        self.model_name = model_name
        self.n_trials   = n_trials
        self.timeout    = timeout
        self.cv_splits  = cv_splits

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tune(self, X: pd.DataFrame, y: pd.Series) -> Dict[str, Any]:
        """
        Find best hyperparameters using Optuna + walk-forward CV.

        Args:
            X: Feature matrix (training portion only — no test leakage).
            y: Target series aligned with X.

        Returns:
            Dict of best hyperparameters. Empty dict if Optuna not installed
            or tuning fails, so the caller can fall back to defaults.
        """
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
        except ImportError:
            log.warning(
                "optuna not installed — skipping hyperparameter tuning. "
                "Install with: pip install optuna"
            )
            return {}

        if self.model_name not in SEARCH_SPACES:
            log.debug(f"No search space for '{self.model_name}' — skipping tuning")
            return {}

        from forecasting_core.evaluation.metrics import mae as mae_fn

        splits = self._wfv_splits(len(X))
        if not splits:
            log.debug("Not enough data for CV in tuner — skipping")
            return {}

        def objective(trial):
            params = _suggest(trial, self.model_name)
            fold_maes = []
            for tr_idx, te_idx in splits:
                try:
                    m = _make_model(self.model_name, params)
                    m.fit(X.iloc[tr_idx], y.iloc[tr_idx])
                    preds = m.predict(X.iloc[te_idx])
                    fold_maes.append(mae_fn(y.iloc[te_idx].values, preds))
                except Exception:
                    fold_maes.append(float("inf"))
            return float(np.mean(fold_maes))

        try:
            study = optuna.create_study(direction="minimize")
            study.optimize(
                objective,
                n_trials=self.n_trials,
                timeout=self.timeout,
                show_progress_bar=False,
                gc_after_trial=True,
            )
            best = study.best_params
            log.info(
                f"Tuner [{self.model_name}]: best MAE={study.best_value:.4f} "
                f"after {len(study.trials)} trials — {best}"
            )
            return best
        except Exception as e:
            log.warning(f"Tuner failed for '{self.model_name}': {e}")
            return {}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _wfv_splits(self, n: int):
        """Lightweight expanding-window splits for tuning objective."""
        min_train = max(2, int(n * 0.5))
        remaining = n - min_train
        if remaining < self.cv_splits:
            return []
        fold_size = remaining // (self.cv_splits + 1)
        splits = []
        for i in range(1, self.cv_splits + 1):
            te = min_train + (i - 1) * fold_size
            te_end = te + fold_size
            if te_end > n:
                break
            tr_idx, te_idx = np.arange(te), np.arange(te, te_end)
            if len(te_idx) == 0:
                continue
            splits.append((tr_idx, te_idx))
        return splits
