"""
Trainer — trains ML models per SKU using walk-forward validation.

Walk-forward validation (expanding window):
  - Prevents data leakage in time series
  - Produces more reliable out-of-sample estimates than a single split
  - Averages metrics across N folds

Example:
    trainer = Trainer(train_ratio=0.8, walk_forward=True, wfv_splits=3)
    results = trainer.train(df_ml, models, group_col="sku", target="sales", dt="date")
"""

import copy
import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple

from forecasting_core.evaluation.metrics import evaluate_all

log = logging.getLogger(__name__)


class WalkForwardSplitter:
    """Expanding window cross-validation for time series."""

    def __init__(self, n_splits: int = 3, min_train_ratio: float = 0.5):
        self.n_splits = n_splits
        self.min_train_ratio = min_train_ratio

    def split(self, n: int) -> List[Tuple[np.ndarray, np.ndarray]]:
        min_train = max(2, int(n * self.min_train_ratio))
        remaining = n - min_train
        if remaining < self.n_splits:
            return []
        fold_size = remaining // (self.n_splits + 1)
        splits = []
        for i in range(1, self.n_splits + 1):
            te = min_train + (i - 1) * fold_size
            te_end = te + fold_size
            if te_end > n:
                break
            tr_idx, te_idx = np.arange(te), np.arange(te, te_end)
            if len(tr_idx) < 2 or len(te_idx) == 0:
                continue
            splits.append((tr_idx, te_idx))
        return splits


class Trainer:
    """Trains sklearn-compatible models per SKU with optional walk-forward validation."""

    def __init__(
        self,
        train_ratio: float = 0.8,
        walk_forward: bool = True,
        wfv_splits: int = 3,
        tuning: bool = False,
        tuning_trials: int = 30,
    ):
        self.train_ratio    = train_ratio
        self.walk_forward   = walk_forward
        self.wfv_splits     = wfv_splits
        self.tuning         = tuning
        self.tuning_trials  = tuning_trials

    def train(
        self,
        df: pd.DataFrame,
        models: dict,
        group_col: str,
        target: str,
        dt: str,
    ) -> Dict[str, dict]:
        """
        Train models per SKU.

        Args:
            df:        Feature-engineered DataFrame.
            models:    {model_name: sklearn_model}
            group_col: Column name of the SKU/group identifier.
            target:    Column name of the target variable.
            dt:        Column name of the date.

        Returns:
            {f"{model}_{sku}": {mae, rmse, wape, bias, sku, model, n, validation}}
        """
        has_group = group_col and group_col in df.columns
        exclude = {dt, target, group_col} if has_group else {dt, target}
        trainable = {n: m for n, m in models.items() if hasattr(m, "fit")}
        results = {}

        groups = df.groupby(group_col) if has_group else [("__all__", df)]
        for sku, g in groups:
            g = g.sort_values(dt).reset_index(drop=True)
            X = g.drop(columns=[c for c in exclude if c in g.columns])
            y = g[target].astype(float)

            if self.walk_forward:
                res = self._wfv(X, y, trainable, str(sku))
            else:
                res = self._simple(X, y, trainable, str(sku))
            results.update(res)

        return results

    def _wfv(self, X, y, models, sku):
        splitter = WalkForwardSplitter(self.wfv_splits, self.train_ratio / 2)
        splits = splitter.split(len(X))
        if not splits:
            return self._simple(X, y, models, sku)

        fold_metrics  = {n: [] for n in models}
        oof_residuals = {n: [] for n in models}   # validation-set residuals per fold

        for tr_idx, te_idx in splits:
            for name, model in models.items():
                try:
                    model.fit(X.iloc[tr_idx], y.iloc[tr_idx])
                    preds = model.predict(X.iloc[te_idx])
                    fold_metrics[name].append(evaluate_all(y.iloc[te_idx].values, preds))
                    oof_residuals[name].extend(
                        (y.iloc[te_idx].values - preds).tolist()
                    )
                except Exception as e:
                    log.warning(f"SKU {sku} | {name} | fold error: {e}")

        results = {}
        cut = int(len(X) * self.train_ratio)
        feature_names = list(X.columns)
        train_X = X.iloc[:cut] if cut < len(X) else X
        train_y = y.iloc[:cut] if cut < len(y) else y

        for name, folds in fold_metrics.items():
            if not folds:
                continue
            avg = {k: float(np.mean([f[k] for f in folds])) for k in folds[0]}

            # Optional hyperparameter tuning before final fit
            best_params = self._maybe_tune(name, train_X, train_y)

            # Final model fitted on full training portion for future inference
            fitted_model = None
            residuals = np.array([])
            shap_importance = []
            try:
                final = self._make_final(name, models[name], best_params)
                final.fit(train_X, train_y)
                # Use OOF (out-of-fold) residuals for honest prediction intervals;
                # fall back to in-sample if no OOF residuals were collected.
                if oof_residuals[name]:
                    residuals = np.array(oof_residuals[name])
                else:
                    residuals = train_y.values - final.predict(train_X)
                fitted_model = final
                from forecasting_core.explainability import compute_shap
                shap_importance = compute_shap(final, train_X)
            except Exception as e:
                log.warning(f"SKU {sku} | {name} | final fit failed: {e}")

            results[f"{name}_{sku}"] = {
                **avg, "sku": sku, "model": name, "n": len(X),
                "n_folds": len(folds), "validation": "wfv",
                "fitted_model": fitted_model,
                "feature_names": feature_names,
                "residuals": residuals,
                "tuned_params": best_params if best_params else None,
                "shap_importance": shap_importance,
            }
        return results

    def _simple(self, X, y, models, sku):
        cut = int(len(X) * self.train_ratio)
        if cut < 2 or cut >= len(X):
            return {}
        results = {}
        feature_names = list(X.columns)
        train_X, train_y = X.iloc[:cut], y.iloc[:cut]

        for name, model in models.items():
            try:
                best_params = self._maybe_tune(name, train_X, train_y)
                final = self._make_final(name, model, best_params)
                final.fit(train_X, train_y)
                preds     = final.predict(X.iloc[cut:])
                metrics   = evaluate_all(y.iloc[cut:].values, preds)
                residuals = y.iloc[cut:].values - preds   # validation-set (OOF) residuals
                from forecasting_core.explainability import compute_shap
                shap_importance = compute_shap(copy.deepcopy(final), train_X)
                results[f"{name}_{sku}"] = {
                    **metrics, "sku": sku, "model": name, "n": len(X),
                    "validation": "simple",
                    "fitted_model": copy.deepcopy(final),
                    "feature_names": feature_names,
                    "residuals": residuals,
                    "tuned_params": best_params if best_params else None,
                    "shap_importance": shap_importance,
                }
            except Exception as e:
                log.warning(f"SKU {sku} | {name} | error: {e}")
        return results

    # ------------------------------------------------------------------
    # Tuning helpers
    # ------------------------------------------------------------------

    def _maybe_tune(self, model_name: str, X_train, y_train) -> dict:
        """Run Optuna tuning if enabled and model is supported. Returns best params or {}."""
        if not self.tuning:
            return {}
        from forecasting_core.training.tuner import HyperparamTuner, SEARCH_SPACES
        if model_name not in SEARCH_SPACES:
            return {}
        log.info(f"Tuning {model_name} ({self.tuning_trials} trials)...")
        tuner = HyperparamTuner(model_name, n_trials=self.tuning_trials)
        return tuner.tune(X_train, y_train)

    def _make_final(self, model_name: str, base_model, best_params: dict):
        """Create the final model: deepcopy base if no tuning, new instance if tuned."""
        if best_params:
            from forecasting_core.training.tuner import _make_model
            return _make_model(model_name, best_params)
        return copy.deepcopy(base_model)
