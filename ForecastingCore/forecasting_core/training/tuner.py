"""
HyperparamTuner — Optuna-based hyperparameter optimization for ML models.

Uses walk-forward CV as the objective to prevent look-ahead bias.

Example:
    tuner = HyperparamTuner("lightgbm", n_trials=30)
    best_params = tuner.tune(X_train, y_train)
    # best_params: {"n_estimators": 450, "learning_rate": 0.04, ...}
"""

from __future__ import annotations

import logging
from typing import Dict, Any

import numpy as np
import pandas as pd

from forecasting_core.evaluation.metrics import asymmetric_cost, DEFAULT_STOCKOUT_MULTIPLIER
from forecasting_core.training.trainer import WalkForwardSplitter

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

    The objective is `asymmetric_cost`, not MAE: the champion each SKU ends up
    planned from is picked by `CHAMPION_METRIC_ORDER`, which leads with
    `cost_horizon` — the same asymmetric cost, because a stockout costs more
    than a surplus of the same size. A tuner minimising MAE cannot tell those
    two errors apart, so it can hand back hyperparameters that lose the
    selection it was supposedly tuning for.

    The walk-forward split is `forecasting_core.training.trainer.
    WalkForwardSplitter` — the same splitter `Trainer` uses, not a private
    copy — so the `gap` it accepts means the same thing here: buckets withheld
    between a training window and the window it is scored on, because a
    gap-less split measures an easier problem than production ever asks.

    Args:
        model_name:           One of the keys in SEARCH_SPACES ("lightgbm", "xgboost").
        n_trials:             Number of Optuna trials (default 30).
        timeout:              Maximum wall time in seconds (default 300 = 5 min).
        cv_splits:            Walk-forward splits used in the objective (default 3).
        gap:                  Buckets withheld before each test window (default 0 — off).
        stockout_multiplier:  How much worse a shortfall is than a surplus of the
                              same size (default DEFAULT_STOCKOUT_MULTIPLIER).
    """

    def __init__(
        self,
        model_name: str,
        n_trials: int = 30,
        timeout: int = 300,
        cv_splits: int = 3,
        gap: int = 0,
        stockout_multiplier: float = DEFAULT_STOCKOUT_MULTIPLIER,
        fixed_params: Dict[str, Any] = None,
    ):
        # Held constant in every trial and overriding any sampled value — the
        # count objective (tweedie/poisson) of an intermittent series. The
        # search tunes the trees; the score stays the asymmetric cost.
        self.fixed_params         = dict(fixed_params or {})
        self.model_name           = model_name
        self.n_trials             = n_trials
        self.timeout              = timeout
        self.cv_splits            = cv_splits
        self.gap                  = max(0, int(gap))
        self.stockout_multiplier  = stockout_multiplier

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

        splits = self._make_splits(len(X))
        if not splits:
            log.debug("Not enough data for CV in tuner — skipping")
            return {}

        def objective(trial):
            params = _suggest(trial, self.model_name)
            return self._fold_cost(params, X, y, splits)

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
                f"Tuner [{self.model_name}]: best cost={study.best_value:.4f} "
                f"after {len(study.trials)} trials — {best}"
            )
            return best
        except Exception as e:
            log.warning(f"Tuner failed for '{self.model_name}': {e}")
            return {}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _make_splits(self, n: int):
        """
        Walk-forward splits for the tuning objective — the same splitter and
        the same gap-backoff `Trainer._wfv` uses, so the folds the tuner scores
        on shrink exactly the way production training's folds do.
        """
        splitter = WalkForwardSplitter(self.cv_splits, min_train_ratio=0.5, gap=self.gap)
        gap = splitter.effective_gap(n)
        if gap != splitter.gap:
            log.info(
                f"Tuner [{self.model_name}]: walk-forward gap reduced {splitter.gap}→{gap} "
                f"— {n} rows cannot fund the full horizon"
            )
            splitter = WalkForwardSplitter(self.cv_splits, min_train_ratio=0.5, gap=gap)
        return splitter.split(n)

    def _fold_cost(self, params: Dict[str, Any], X: pd.DataFrame, y: pd.Series, splits) -> float:
        """
        Mean asymmetric cost across folds for one hyperparameter set — the
        value `objective()` hands to Optuna. Exposed as its own method so the
        objective can be exercised directly in tests without depending on
        Optuna's trial order to land on a particular parameter set.
        """
        fold_costs = []
        for tr_idx, te_idx in splits:
            try:
                m = _make_model(self.model_name, {**params, **self.fixed_params})
                m.fit(X.iloc[tr_idx], y.iloc[tr_idx])
                preds = m.predict(X.iloc[te_idx])
                fold_costs.append(
                    asymmetric_cost(y.iloc[te_idx].values, preds, self.stockout_multiplier)
                )
            except Exception:
                fold_costs.append(float("inf"))
        return float(np.mean(fold_costs))
