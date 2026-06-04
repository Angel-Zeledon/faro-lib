"""SHAP-based feature importance for tree models (LightGBM / XGBoost)."""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd
from typing import List, Dict

log = logging.getLogger(__name__)


def compute_shap(model, X_test: pd.DataFrame, top_n: int = 10) -> List[Dict]:
    """
    Compute SHAP feature importances using TreeExplainer.

    Returns top_n features sorted by mean absolute SHAP value, with a
    direction indicator ('positive' if mean signed SHAP >= 0).

    Args:
        model:   Fitted LightGBM or XGBoost model.
        X_test:  Feature DataFrame used for explanation (training set).
        top_n:   Number of top features to return.

    Returns:
        [{"feature": str, "mean_abs_shap": float, "direction": str}, ...]
    """
    try:
        import shap
    except ImportError:
        log.warning("shap not installed — skipping SHAP explainability. pip install shap")
        return []

    try:
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_test)

        # For multi-output models shap_values is a list; take first output
        if isinstance(shap_values, list):
            shap_values = shap_values[0]

        shap_arr = np.array(shap_values)
        mean_abs = np.abs(shap_arr).mean(axis=0)
        mean_signed = shap_arr.mean(axis=0)

        features = list(X_test.columns)
        n = min(top_n, len(features))
        top_idx = np.argsort(mean_abs)[::-1][:n]

        return [
            {
                "feature":       features[i],
                "mean_abs_shap": round(float(mean_abs[i]), 6),
                "direction":     "positive" if mean_signed[i] >= 0 else "negative",
            }
            for i in top_idx
        ]
    except Exception as e:
        log.warning(f"compute_shap failed: {e}")
        return []
