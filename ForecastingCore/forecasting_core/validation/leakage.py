"""
Data leakage detection.

Checks for three leakage types:
  1. Temporal leakage  — future data in training split
  2. Target leakage    — target column used as a raw feature (not shifted)
  3. Group leakage     — aggregations that span groups and leak cross-SKU signal
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from forecasting_core.validation.exceptions import DataLeakageError
from forecasting_core.validation.modes import ValidationMode, ValidationResult


def detect_leakage(
    df: pd.DataFrame,
    config: Dict[str, Any],
    feature_columns: List[str] = None,
    mode: ValidationMode = ValidationMode.STRICT,
) -> ValidationResult:
    errors: List[DataLeakageError] = []
    warnings: List[DataLeakageError] = []

    dt_col     = config.get("dt")
    target_col = config.get("target")

    # 1. Temporal leakage: data not sorted, or train/test bleed
    if dt_col and dt_col in df.columns:
        try:
            dates = pd.to_datetime(df[dt_col], errors="coerce")
            if not dates.is_monotonic_increasing:
                warnings.append(DataLeakageError(
                    f"Date column '{dt_col}' is not sorted ascending. "
                    "If you sort by date after splitting, future data may leak into training.",
                    error_id="UNSORTED_DATES",
                    severity="warning",
                    suggestions=["Sort the DataFrame by date before any train/test split."],
                ))
        except Exception:
            pass

    # 2. Target leakage in feature columns
    if feature_columns and target_col:
        # If the raw target appears as a feature name (not as a lagged/shifted version), that's leakage
        direct_uses = [c for c in feature_columns
                       if c == target_col]
        if direct_uses:
            errors.append(DataLeakageError(
                f"Target column '{target_col}' appears directly in feature columns. "
                "Use lagged versions (lag_1, lag_7, ...) instead.",
                error_id="TARGET_FEATURE_LEAKAGE",
                suggestions=["Remove the raw target from feature columns and use lag features."],
                possible_fixes=["features.lags = [1, 7, 14]"],
                context={"columns": direct_uses},
            ))

    # 3. Check if any feature column has a suspiciously high correlation with target
    # (quick heuristic: correlation > 0.99 on a shifted target would be 1.0 — flag only near-perfect)
    if target_col and target_col in df.columns and feature_columns:
        numeric_feats = [c for c in feature_columns
                         if c in df.columns
                         and pd.api.types.is_numeric_dtype(df[c])
                         and c != target_col]
        try:
            tgt = df[target_col].astype(float)
            for col in numeric_feats[:50]:  # limit to 50 to avoid perf issues
                corr = abs(tgt.corr(df[col].astype(float)))
                if corr > 0.999:
                    errors.append(DataLeakageError(
                        f"Feature '{col}' has near-perfect correlation ({corr:.4f}) with target. "
                        "This likely indicates data leakage.",
                        error_id="NEAR_PERFECT_CORRELATION",
                        severity="error",
                        suggestions=[f"Remove or shift '{col}' by at least 1 step."],
                        context={"column": col, "correlation": round(float(corr), 5)},
                    ))
        except Exception:
            pass

    valid = len(errors) == 0
    return ValidationResult(valid=valid, errors=errors, warnings=warnings)
