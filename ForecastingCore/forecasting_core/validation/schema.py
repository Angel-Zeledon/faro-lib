"""
Layer 1 — Schema validation.

Checks config dict structure before touching data:
  - Required keys present
  - Correct value types
  - Value ranges
"""

from __future__ import annotations

from typing import Any, Dict, List

from forecasting_core.validation.exceptions import SchemaValidationError
from forecasting_core.validation.modes import ValidationMode, ValidationResult

_REQUIRED_KEYS = {"data", "dt", "target", "group_id", "models", "features"}

_RULES: List[tuple] = [
    # (key, type_or_types, min, max, description)
    ("train_ratio",          float, 0.1, 0.95,  "float in (0.1, 0.95)"),
    ("prediction_horizon",   int,   1,   365,   "int in [1, 365]"),
    ("wfv_splits",           int,   1,   10,    "int in [1, 10]"),
    ("seasonal_period",      int,   2,   365,   "int in [2, 365]"),
    ("min_history",          int,   5,   10000, "int in [5, 10000]"),
]


def validate_schema(
    config: Dict[str, Any],
    mode: ValidationMode = ValidationMode.STRICT,
) -> ValidationResult:
    errors: List[SchemaValidationError] = []
    warnings: List[SchemaValidationError] = []
    corrections: List[dict] = []

    # 1. Required keys
    missing = _REQUIRED_KEYS - set(config.keys())
    for key in sorted(missing):
        err = SchemaValidationError(
            f"Required config key '{key}' is missing.",
            error_id="MISSING_KEY",
            suggestions=[f"Add '{key}' to your config dict."],
            possible_fixes=[f"config['{key}'] = <value>"],
            context={"key": key},
        )
        errors.append(err)

    # 2. Type + range checks
    for key, expected_type, lo, hi, desc in _RULES:
        if key not in config:
            continue
        val = config[key]
        if not isinstance(val, (int, float)):
            err = SchemaValidationError(
                f"'{key}' must be a number, got {type(val).__name__}.",
                error_id="WRONG_TYPE",
                context={"key": key, "got": type(val).__name__, "expected": desc},
            )
            if mode == ValidationMode.AUTO_FIX:
                try:
                    config[key] = expected_type(val)
                    corrections.append({"key": key, "from": val, "to": config[key]})
                except Exception:
                    errors.append(err)
            else:
                errors.append(err)
            continue

        num = float(val)
        if not (lo <= num <= hi):
            err = SchemaValidationError(
                f"'{key}' value {val!r} is out of range [{lo}, {hi}].",
                error_id="OUT_OF_RANGE",
                suggestions=[f"Set '{key}' to a value between {lo} and {hi}."],
                context={"key": key, "value": val, "min": lo, "max": hi},
            )
            if mode == ValidationMode.AUTO_FIX:
                clamped = max(lo, min(hi, num))
                config[key] = expected_type(clamped)
                corrections.append({"key": key, "from": val, "to": config[key]})
            elif mode in (ValidationMode.STRICT, ValidationMode.WARNING):
                (errors if mode == ValidationMode.STRICT else warnings).append(err)

    # 3. models dict check
    if "models" in config:
        if not isinstance(config["models"], dict) or not config["models"]:
            errors.append(SchemaValidationError(
                "'models' must be a non-empty dict mapping model names to hyperparameters.",
                error_id="INVALID_MODELS",
                suggestions=["e.g. {'lightgbm': {}, 'arima': {}}"],
            ))

    valid = len(errors) == 0
    return ValidationResult(valid=valid, errors=errors, warnings=warnings, corrections=corrections)
