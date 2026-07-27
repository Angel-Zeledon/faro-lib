"""Infinite target values must be rejected before training.

A corrupted export ("1e309", an overflowing spreadsheet formula) parses as a
perfectly valid float(inf). It is not NaN, not constant and not negative, so it
passed every other check and only showed up later as inf/NaN error metrics on a
chart nobody could explain.
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.validation.data_validator import validate_data
from forecasting_core.validation.modes import ValidationMode


MIN_HISTORY = 20  # validate_data skips SKUs with fewer rows than this


def _pad(values):
    """Grow a series past min_history without changing what it demonstrates."""
    filler = [10.0, 11.0, 12.0, 9.0, 13.0]
    out = list(values)
    while len(out) < MIN_HISTORY + 4:
        out.append(filler[len(out) % len(filler)])
    return out


def _frame(values, sku="SKU-A"):
    values = _pad(values)
    return pd.DataFrame({
        "sku": [sku] * len(values),
        "date": pd.date_range("2025-01-01", periods=len(values), freq="D"),
        "demand": values,
    })


# validate_data reads the grouping column as "group_id" and the date as "dt".
CONFIG = {"target": "demand", "group_id": "sku", "dt": "date"}


def _errors(result):
    return [e for e in result.errors] if hasattr(result, "errors") else []


class TestInfiniteTarget:
    def test_positive_infinity_is_reported(self):
        df = _frame([10.0, 12.0, float("inf"), 11.0, 9.0, 13.0, 10.0, 12.0])
        result = validate_data(df, CONFIG, mode=ValidationMode.WARNING)
        ids = [e.error_id for e in _errors(result)]
        assert "INFINITE_TARGET" in ids

    def test_negative_infinity_is_reported(self):
        df = _frame([10.0, 12.0, float("-inf"), 11.0, 9.0, 13.0, 10.0, 12.0])
        result = validate_data(df, CONFIG, mode=ValidationMode.WARNING)
        ids = [e.error_id for e in _errors(result)]
        assert "INFINITE_TARGET" in ids

    def test_error_counts_how_many_and_names_the_sku(self):
        df = _frame([float("inf"), 12.0, float("inf"), 11.0, 9.0, 13.0, 10.0, 12.0],
                    sku="SKU-BAD")
        result = validate_data(df, CONFIG, mode=ValidationMode.WARNING)
        err = next(e for e in _errors(result) if e.error_id == "INFINITE_TARGET")
        assert err.context["n_infinite"] == 2
        assert err.context["sku"] == "SKU-BAD"

    def test_clean_series_is_not_flagged(self):
        df = _frame([10.0, 12.0, 14.0, 11.0, 9.0, 13.0, 10.0, 12.0])
        result = validate_data(df, CONFIG, mode=ValidationMode.WARNING)
        ids = [e.error_id for e in _errors(result)]
        assert "INFINITE_TARGET" not in ids

    def test_nan_alone_is_not_reported_as_infinite(self):
        """NaN has its own error; the two must not be conflated."""
        df = _frame([10.0, np.nan, 14.0, 11.0, 9.0, 13.0, 10.0, 12.0])
        result = validate_data(df, CONFIG, mode=ValidationMode.WARNING)
        ids = [e.error_id for e in _errors(result)]
        assert "INFINITE_TARGET" not in ids

    def test_a_huge_but_finite_value_is_allowed_through(self):
        """Only overflow is rejected here — an unusually large but real number
        is the outlier treatment's job, not the validator's."""
        df = _frame([10.0, 12.0, 1e18, 11.0, 9.0, 13.0, 10.0, 12.0])
        result = validate_data(df, CONFIG, mode=ValidationMode.WARNING)
        ids = [e.error_id for e in _errors(result)]
        assert "INFINITE_TARGET" not in ids
