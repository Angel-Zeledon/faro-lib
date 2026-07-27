"""Gap fill must not be weaponized by an absurd date.

`pd.date_range(min, max)` has no ceiling. One typo'd 1900 row next to a 2099
one spans ~73,000 daily buckets — PER SKU. On a 40-SKU catalog that is nearly
3 million rows materialized in the worker just to fill gaps that do not exist.
The series is left unfilled instead; the profiler already reports the gaps and
the out-of-range dates.
"""

import pandas as pd
import pytest

from backend.workers.runner import MAX_GAP_FILL_BUCKETS, _apply_gap_fill


def _series(dates, qty=10):
    return pd.DataFrame({
        "sku": ["A"] * len(dates),
        "date": pd.to_datetime(dates),
        "demand": [qty] * len(dates),
    })


class TestNormalGapFillStillWorks:
    @pytest.mark.offline
    def test_a_real_gap_is_filled(self):
        # Enough consecutive days that the detected native frequency is daily
        # (it is the MEDIAN gap), then a hole at Jan 6-7.
        df = _series(["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04",
                      "2025-01-05", "2025-01-08"])
        out = _apply_gap_fill(df, "date", "demand", "sku", "zero")
        assert len(out) == 8, "Jan 6 and 7 should be materialized"
        assert out["demand"].tolist().count(0) == 2

    @pytest.mark.offline
    def test_leave_strategy_is_untouched(self):
        df = _series(["2025-01-01", "2025-01-05"])
        assert _apply_gap_fill(df, "date", "demand", "sku", "leave") is df


class TestAbsurdRangeIsRefused:
    @pytest.mark.offline
    def test_century_wide_span_is_not_filled(self):
        """A 1900 typo inside otherwise-normal history."""
        dates = ["1900-01-01"] + [f"2025-01-{d:02d}" for d in range(1, 11)]
        df = _series(dates)
        out = _apply_gap_fill(df, "date", "demand", "sku", "zero")
        assert len(out) == len(dates), "must return the original rows, unfilled"
        assert len(out) < MAX_GAP_FILL_BUCKETS

    @pytest.mark.offline
    def test_future_dated_row_does_not_explode_either(self):
        dates = [f"2025-01-{d:02d}" for d in range(1, 11)] + ["2099-12-31"]
        df = _series(dates)
        out = _apply_gap_fill(df, "date", "demand", "sku", "zero")
        assert len(out) == len(dates)

    @pytest.mark.offline
    def test_only_the_offending_sku_is_skipped(self):
        """A bad date on one SKU must not deny gap fill to the healthy ones."""
        bad = pd.DataFrame({
            "sku": ["BAD"] * 3,
            "date": pd.to_datetime(["1900-01-01", "2025-01-01", "2025-01-02"]),
            "demand": [1, 2, 3],
        })
        good_dates = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04",
                      "2025-01-05", "2025-01-08"]
        good = pd.DataFrame({
            "sku": ["GOOD"] * len(good_dates),
            "date": pd.to_datetime(good_dates),
            "demand": list(range(len(good_dates))),
        })
        out = _apply_gap_fill(pd.concat([bad, good]), "date", "demand", "sku", "zero")
        assert len(out[out["sku"] == "BAD"]) == 3, "left as-is"
        assert len(out[out["sku"] == "GOOD"]) == 8, "Jan 6 and 7 filled"

    @pytest.mark.offline
    def test_a_long_but_plausible_history_is_still_filled(self):
        """Ten years of daily history sits under the cap and must be filled."""
        dates = pd.date_range("2015-01-01", "2025-01-01", freq="D")
        # Drop a handful of days so there is something to fill, while keeping
        # the median gap at one day.
        kept = dates.delete([100, 500, 900])
        out = _apply_gap_fill(_series(kept), "date", "demand", "sku", "zero")
        assert len(out) == len(dates) > 3600
        assert out["demand"].tolist().count(0) == 3
