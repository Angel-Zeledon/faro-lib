"""
Layer 4 — Model compatibility gate.

Per-SKU check: is a given model suitable for this series?

Rules:
  - arima / ets / prophet → need min_rows, not too many NaNs
  - ets (multiplicative)  → needs positive-only series
  - croston               → designed for intermittent; wastes capacity on stable
  - lstm                  → needs >= WINDOW + horizon rows
  - lightgbm / xgboost    → need feature columns after engineering
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from forecasting_core.validation.exceptions import CompatibilityError
from forecasting_core.validation.modes import ValidationMode, ValidationResult

_STAT_MODELS  = {"arima", "ets", "prophet"}
_ML_MODELS    = {"lightgbm", "xgboost"}
_CROSTON_MODELS = {"croston"}
_DL_MODELS    = {"lstm"}

LSTM_WINDOW = 14


def _series_for_sku(df, group_col, target_col, sku):
    if group_col and group_col in df.columns:
        return df[df[group_col].astype(str) == str(sku)][target_col].astype(float)
    return df[target_col].astype(float)


def check_model_compatibility(
    df: pd.DataFrame,
    config: Dict[str, Any],
    mode: ValidationMode = ValidationMode.STRICT,
) -> ValidationResult:
    """
    Returns per-SKU, per-model compatibility issues.
    In AUTO_FIX / WARNING modes these become routing hints (skip the model for that SKU)
    rather than hard failures.
    """
    errors: List[CompatibilityError] = []
    warnings: List[CompatibilityError] = []
    corrections: List[dict] = []

    target_col  = config.get("target")
    group_col   = config.get("group_id")
    min_history = int(config.get("min_history", 20))
    horizon     = int(config.get("prediction_horizon", 7))
    models      = config.get("models", {})
    interp_thr  = float(config.get("intermittency_threshold", 0.4))

    if not target_col or target_col not in df.columns:
        return ValidationResult(valid=True)

    model_names = {m.lower() for m in models}
    groups = (
        [(str(sku), g) for sku, g in df.groupby(group_col)]
        if group_col and group_col in df.columns
        else [("__all__", df)]
    )

    for sku, g in groups:
        series = g[target_col].astype(float).dropna()
        n      = len(series)
        zeros  = (series == 0).mean()
        has_neg = (series < 0).any()

        for model in model_names:
            issue = _check_one(model, series, n, zeros, has_neg, min_history, horizon, interp_thr)
            if issue:
                err = CompatibilityError(
                    f"SKU '{sku}' / model '{model}': {issue}",
                    error_id="MODEL_INCOMPATIBLE",
                    severity="warning" if mode != ValidationMode.STRICT else "error",
                    suggestions=[f"Use routing to skip '{model}' for SKU '{sku}'."],
                    context={"sku": sku, "model": model, "reason": issue},
                )
                if mode == ValidationMode.STRICT:
                    errors.append(err)
                elif mode == ValidationMode.AUTO_FIX:
                    corrections.append({
                        "action": "skip_model_for_sku",
                        "sku": sku,
                        "model": model,
                        "reason": issue,
                    })
                else:
                    warnings.append(err)

    valid = len(errors) == 0
    return ValidationResult(valid=valid, errors=errors, warnings=warnings, corrections=corrections)


def _check_one(
    model: str,
    series: pd.Series,
    n: int,
    zero_ratio: float,
    has_neg: bool,
    min_history: int,
    horizon: int,
    interp_thr: float,
) -> str:
    """Return a non-empty reason string if incompatible, else empty string."""
    if model in _STAT_MODELS:
        if n < min_history:
            return f"needs >= {min_history} rows, only {n} available"
        if model == "ets" and has_neg:
            return "ETS with additive seasonal requires non-negative values"

    if model in _CROSTON_MODELS:
        if zero_ratio < interp_thr * 0.5:
            return (f"Croston is designed for intermittent series "
                    f"(zero_ratio={zero_ratio:.0%} < {interp_thr * 0.5:.0%}); "
                    "use a different model for dense demand")

    if model in _DL_MODELS:
        required = LSTM_WINDOW + horizon
        if n < required:
            return f"LSTM needs >= {required} rows (window={LSTM_WINDOW} + horizon={horizon}), only {n}"

    if model in _ML_MODELS:
        if n < max(min_history, 30):
            return f"ML model needs >= 30 rows for reliable feature engineering, only {n}"

    return ""
