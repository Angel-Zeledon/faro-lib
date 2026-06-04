"""
Layer 2 — Semantic validation.

Checks that config values make sense in the context of the actual data:
  - Column names exist in the DataFrame
  - Date column is parseable
  - Lag values don't exceed series length
  - Horizon doesn't exceed available test data
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from forecasting_core.validation.exceptions import SemanticValidationError
from forecasting_core.validation.modes import ValidationMode, ValidationResult


def validate_semantic(
    config: Dict[str, Any],
    df: pd.DataFrame,
    mode: ValidationMode = ValidationMode.STRICT,
) -> ValidationResult:
    errors: List[SemanticValidationError] = []
    warnings: List[SemanticValidationError] = []
    corrections: List[dict] = []

    dt_col     = config.get("dt")
    target_col = config.get("target")
    group_col  = config.get("group_id")
    cols       = set(df.columns)

    # 1. Required columns exist in data
    for role, col in [("dt", dt_col), ("target", target_col), ("group_id", group_col)]:
        if col and col not in cols:
            errors.append(SemanticValidationError(
                f"Column '{col}' (role={role}) not found in data. Available: {sorted(cols)}",
                error_id="COLUMN_NOT_FOUND",
                suggestions=[f"Check spelling. Available columns: {sorted(cols)}"],
                context={"role": role, "column": col, "available": sorted(cols)},
            ))

    # 2. Date column is parseable
    if dt_col and dt_col in cols:
        try:
            parsed = pd.to_datetime(df[dt_col], errors="coerce")
            bad_ratio = parsed.isna().mean()
            if bad_ratio > 0.05:
                errors.append(SemanticValidationError(
                    f"Date column '{dt_col}': {bad_ratio:.0%} of values could not be parsed.",
                    error_id="UNPARSEABLE_DATE",
                    suggestions=["Ensure date column uses a consistent format (YYYY-MM-DD recommended)."],
                    context={"column": dt_col, "bad_ratio": round(bad_ratio, 3)},
                ))
        except Exception as e:
            errors.append(SemanticValidationError(
                f"Failed to parse date column '{dt_col}': {e}",
                error_id="DATE_PARSE_ERROR",
                context={"column": dt_col},
            ))

    # 3. Target column is numeric
    if target_col and target_col in cols:
        if not pd.api.types.is_numeric_dtype(df[target_col]):
            err = SemanticValidationError(
                f"Target column '{target_col}' is not numeric (dtype={df[target_col].dtype}).",
                error_id="NON_NUMERIC_TARGET",
                suggestions=["Cast target to float: df[target] = pd.to_numeric(df[target], errors='coerce')"],
                context={"column": target_col, "dtype": str(df[target_col].dtype)},
            )
            if mode == ValidationMode.AUTO_FIX:
                try:
                    df[target_col] = pd.to_numeric(df[target_col], errors="coerce")
                    corrections.append({"action": "cast_target_to_numeric", "column": target_col})
                except Exception:
                    errors.append(err)
            else:
                errors.append(err)

    # 4. Lag values reasonable vs series length
    features = config.get("features", {})
    if isinstance(features, dict):
        lags = features.get("lags", [])
        if lags and dt_col and group_col and dt_col in cols and group_col in cols:
            min_len = df.groupby(group_col)[dt_col].count().min() if group_col in cols else len(df)
            max_lag = max(lags)
            if max_lag >= min_len * 0.8:
                msg = (f"Maximum lag ({max_lag}) exceeds 80% of the shortest series "
                       f"({min_len} rows). Some SKUs will lose most training data.")
                w_or_e = SemanticValidationError(
                    msg, error_id="LAGS_TOO_LARGE",
                    severity="warning" if mode != ValidationMode.STRICT else "error",
                    suggestions=[f"Reduce max lag below {int(min_len * 0.5)}."],
                    context={"max_lag": max_lag, "min_series_len": int(min_len)},
                )
                (errors if mode == ValidationMode.STRICT else warnings).append(w_or_e)

    # 5. Horizon sanity
    horizon = config.get("prediction_horizon")
    train_ratio = config.get("train_ratio", 0.8)
    if horizon and dt_col and dt_col in cols and group_col and group_col in cols:
        min_len = int(df.groupby(group_col)[dt_col].count().min())
        test_size = int(min_len * (1 - train_ratio))
        if horizon > test_size:
            warnings.append(SemanticValidationError(
                f"prediction_horizon={horizon} exceeds available test data ({test_size} rows) "
                f"for shortest series. Evaluation will cover fewer steps.",
                error_id="HORIZON_TOO_LARGE",
                severity="warning",
                suggestions=[f"Set prediction_horizon <= {test_size} or increase train data."],
                context={"horizon": horizon, "test_size": test_size},
            ))

    valid = len(errors) == 0
    return ValidationResult(valid=valid, errors=errors, warnings=warnings, corrections=corrections)
