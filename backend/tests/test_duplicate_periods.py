"""Per-transaction exports must become per-period totals before training.

ERPs export one row per SALE, not per day. Treated as separate points, three
transactions of 4+3+3 on one day train a model that predicts ~3 per step
instead of 10 per day, and the purchase suggestion comes out at a third of what
the business needs — a silent stockout. Reproduced in the browser before this
guard existed: the forecast returned 3-4 units for a SKU selling 10/day, with
the first forecast points all carrying the same date.
"""

import pandas as pd
import pytest

from backend.workers.runner import _collapse_duplicate_periods


def _erp_export():
    """60 days, 3 transactions per day (4+3+3) — real demand is 10/day."""
    rows = []
    for d in pd.date_range("2025-01-01", periods=60, freq="D"):
        for q in (4, 3, 3):
            rows.append({"sku": "SKU-TX", "date": d, "demand": q})
    return pd.DataFrame(rows)


class TestCollapseDuplicatePeriods:
    @pytest.mark.offline
    def test_transactions_become_daily_totals(self):
        out = _collapse_duplicate_periods(_erp_export(), "date", "demand", "sku")
        assert len(out) == 60, "one row per day, not per transaction"
        assert (out["demand"] == 10).all(), "the day's sales are the sum of its sales"

    @pytest.mark.offline
    def test_each_date_appears_once_per_sku(self):
        out = _collapse_duplicate_periods(_erp_export(), "date", "demand", "sku")
        assert not out.duplicated(subset=["date", "sku"]).any()

    @pytest.mark.offline
    def test_two_skus_stay_separate(self):
        df = pd.DataFrame([
            {"sku": "A", "date": "2025-01-01", "demand": 4},
            {"sku": "A", "date": "2025-01-01", "demand": 6},
            {"sku": "B", "date": "2025-01-01", "demand": 5},
        ])
        out = _collapse_duplicate_periods(df, "date", "demand", "sku")
        assert len(out) == 2
        assert out.set_index("sku")["demand"].to_dict() == {"A": 10, "B": 5}

    @pytest.mark.offline
    def test_clean_daily_file_is_returned_untouched(self):
        """The healthy path must not pay for a groupby."""
        df = pd.DataFrame([
            {"sku": "A", "date": "2025-01-01", "demand": 4},
            {"sku": "A", "date": "2025-01-02", "demand": 6},
        ])
        assert _collapse_duplicate_periods(df, "date", "demand", "sku") is df

    @pytest.mark.offline
    def test_non_target_columns_are_not_summed(self):
        """Summing a unit price would invent money that does not exist."""
        df = pd.DataFrame([
            {"sku": "A", "date": "2025-01-01", "demand": 4, "price": 100.0},
            {"sku": "A", "date": "2025-01-01", "demand": 6, "price": 100.0},
        ])
        out = _collapse_duplicate_periods(df, "date", "demand", "sku")
        assert out["demand"].iloc[0] == 10
        assert out["price"].iloc[0] == 100.0

    @pytest.mark.offline
    def test_column_order_is_preserved(self):
        df = _erp_export()
        out = _collapse_duplicate_periods(df, "date", "demand", "sku")
        assert list(out.columns) == list(df.columns)

    @pytest.mark.offline
    def test_works_without_a_group_column(self):
        df = pd.DataFrame([
            {"date": "2025-01-01", "demand": 4},
            {"date": "2025-01-01", "demand": 6},
            {"date": "2025-01-02", "demand": 7},
        ])
        out = _collapse_duplicate_periods(df, "date", "demand", None)
        assert len(out) == 2
        assert out.sort_values("date")["demand"].tolist() == [10, 7]

    @pytest.mark.offline
    def test_missing_columns_are_a_no_op(self):
        df = pd.DataFrame([{"date": "2025-01-01", "demand": 4}])
        assert _collapse_duplicate_periods(df, "nope", "demand", None) is df
        assert _collapse_duplicate_periods(df, "date", "nope", None) is df
