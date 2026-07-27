"""Infinite quantities must never reach training.

A corrupted cell ("1e309", an overflowing spreadsheet formula) parses as a
valid float(inf). It is not NaN and not an outlier, so it survives gap fill and
outlier treatment untouched and every error metric for that SKU comes back
inf/NaN. The pipeline validates in WARNING mode (log only), so the data has to
be made safe in the runner.
"""

import numpy as np
import pandas as pd
import pytest

from backend.workers.runner import _neutralize_infinities


def _frame(values):
    return pd.DataFrame({
        "sku": ["A"] * len(values),
        "date": pd.date_range("2025-01-01", periods=len(values), freq="D"),
        "demand": values,
    })


class TestNeutralizeInfinities:
    @pytest.mark.offline
    def test_positive_infinity_becomes_nan(self):
        out = _neutralize_infinities(_frame([10.0, float("inf"), 12.0]), "demand")
        assert pd.isna(out["demand"].iloc[1])
        assert out["demand"].iloc[0] == 10.0
        assert out["demand"].iloc[2] == 12.0

    @pytest.mark.offline
    def test_negative_infinity_becomes_nan(self):
        out = _neutralize_infinities(_frame([10.0, float("-inf"), 12.0]), "demand")
        assert pd.isna(out["demand"].iloc[1])

    @pytest.mark.offline
    def test_no_infinities_returns_the_same_object_untouched(self):
        """The healthy path must not pay for a defensive copy."""
        df = _frame([10.0, 11.0, 12.0])
        out = _neutralize_infinities(df, "demand")
        assert out is df

    @pytest.mark.offline
    def test_original_frame_is_not_mutated(self):
        df = _frame([10.0, float("inf"), 12.0])
        _neutralize_infinities(df, "demand")
        assert np.isinf(df["demand"].iloc[1]), "caller's frame must be left alone"

    @pytest.mark.offline
    def test_existing_nan_is_preserved_not_resurrected(self):
        out = _neutralize_infinities(_frame([10.0, np.nan, float("inf")]), "demand")
        assert pd.isna(out["demand"].iloc[1])
        assert pd.isna(out["demand"].iloc[2])

    @pytest.mark.offline
    def test_missing_target_column_is_a_no_op(self):
        df = _frame([10.0, 11.0])
        assert _neutralize_infinities(df, "not_a_column") is df

    @pytest.mark.offline
    def test_non_numeric_target_does_not_explode(self):
        df = pd.DataFrame({"demand": ["diez", "once"]})
        out = _neutralize_infinities(df, "demand")
        assert len(out) == 2

    @pytest.mark.offline
    def test_string_infinity_from_a_csv_is_also_caught(self):
        """pandas reads a bare `inf` cell as the string 'inf' in an object
        column; to_numeric is what exposes it."""
        df = pd.DataFrame({"demand": ["10", "inf", "12"]})
        out = _neutralize_infinities(df, "demand")
        assert pd.isna(pd.to_numeric(out["demand"], errors="coerce").iloc[1])
