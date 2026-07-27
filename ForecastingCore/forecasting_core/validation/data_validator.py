"""
Layer 3 — Data validation.

Validates data content per SKU:
  - Minimum rows
  - All-NaN or constant target
  - Duplicate timestamps
  - Negative demand where unexpected
  - Class imbalance (all zeros)
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from forecasting_core.validation.exceptions import (
    DataValidationError,
    InsufficientDataError,
)
from forecasting_core.validation.modes import ValidationMode, ValidationResult


def validate_data(
    df: pd.DataFrame,
    config: Dict[str, Any],
    mode: ValidationMode = ValidationMode.STRICT,
) -> ValidationResult:
    errors: List[DataValidationError] = []
    warnings: List[DataValidationError] = []
    corrections: List[dict] = []

    dt_col     = config.get("dt")
    target_col = config.get("target")
    group_col  = config.get("group_id")
    min_history = int(config.get("min_history", 20))

    if not target_col or target_col not in df.columns:
        return ValidationResult(valid=False, errors=[DataValidationError(
            f"Target column '{target_col}' not in DataFrame — cannot validate data.",
            error_id="MISSING_TARGET",
        )])

    groups = df.groupby(group_col) if group_col and group_col in df.columns else [("__all__", df)]

    sku_errors: List[str] = []
    sku_warnings: List[str] = []

    for sku, g in groups:
        series = g[target_col].astype(float)
        n = len(series)

        # Insufficient history
        if n < min_history:
            msg = f"SKU '{sku}': only {n} rows (min={min_history})"
            if mode == ValidationMode.STRICT:
                errors.append(InsufficientDataError(
                    msg, error_id="INSUFFICIENT_HISTORY",
                    context={"sku": str(sku), "n_rows": n, "min_history": min_history},
                ))
            else:
                sku_warnings.append(msg)
            continue

        # Infinite values. A corrupted export ("1e309", an overflowing formula)
        # parses as a perfectly valid float(inf), passes every other check here,
        # and then silently turns that SKU's error metrics into inf/NaN. Caught
        # before training rather than debugged afterwards from a broken chart.
        n_inf = int(np.isinf(series).sum())
        if n_inf:
            errors.append(DataValidationError(
                f"SKU '{sku}': {n_inf} infinite target value(s).",
                error_id="INFINITE_TARGET",
                suggestions=[
                    "A quantity overflowed to infinity — check that column for "
                    "corrupted numbers (e.g. 1e309) or misplaced identifiers.",
                ],
                context={"sku": str(sku), "n_infinite": n_inf},
            ))

        # All NaN
        if series.isna().all():
            errors.append(DataValidationError(
                f"SKU '{sku}': target column is entirely NaN.",
                error_id="ALL_NAN_TARGET",
                context={"sku": str(sku)},
            ))

        # Constant series
        elif series.dropna().nunique() == 1:
            msg = f"SKU '{sku}': target is constant (value={series.iloc[0]:.4g})"
            if mode == ValidationMode.STRICT:
                errors.append(DataValidationError(
                    msg, error_id="CONSTANT_TARGET",
                    severity="warning",
                    suggestions=["Constant series cannot be forecast — remove or exclude this SKU."],
                    context={"sku": str(sku)},
                ))
            else:
                sku_warnings.append(msg)

        # All zeros
        elif (series == 0).all():
            msg = f"SKU '{sku}': all target values are zero"
            sku_warnings.append(msg)

        # Duplicate timestamps
        if dt_col and dt_col in g.columns:
            dupes = g[dt_col].duplicated().sum()
            if dupes > 0:
                msg = f"SKU '{sku}': {dupes} duplicate timestamps"
                if mode == ValidationMode.AUTO_FIX:
                    corrections.append({
                        "action": "drop_duplicate_timestamps",
                        "sku": str(sku),
                        "count": int(dupes),
                    })
                else:
                    sku_warnings.append(msg)

        # Negative values (informational)
        neg_count = int((series < 0).sum())
        if neg_count > 0:
            sku_warnings.append(
                f"SKU '{sku}': {neg_count} negative target values — "
                "multiplicative models (ETS, SARIMA) may fail."
            )

    # Collect SKU-level issues as single validation items
    if sku_errors:
        errors.append(DataValidationError(
            f"{len(sku_errors)} SKU(s) failed data validation: {'; '.join(sku_errors[:3])}{'...' if len(sku_errors) > 3 else ''}",
            error_id="SKU_DATA_ERRORS",
            context={"sku_errors": sku_errors},
        ))
    if sku_warnings:
        for msg in sku_warnings:
            warnings.append(DataValidationError(
                msg, error_id="SKU_DATA_WARNING", severity="warning",
            ))

    valid = len(errors) == 0
    return ValidationResult(valid=valid, errors=errors, warnings=warnings, corrections=corrections)
