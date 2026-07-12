import pandas as pd
import pytest
from forecasting_core.data.profiler import DataProfiler


def _dates(start: str, periods: int, freq: str) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(start, periods=periods, freq=freq)]


def _df(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["date", "sku", "sales"])


class TestDetectGranularity:
    def test_homogeneous_daily(self):
        rows = []
        for sku in ("A", "B"):
            for d in _dates("2024-01-01", 10, "D"):
                rows.append((d, sku, 5.0))
        result = DataProfiler().detect_granularity(_df(rows), "date", "sku")
        assert result["status"] == "homogeneous"
        assert result["detected"] == ["D"]
        assert set(result["skus_by_frequency"]["D"]) == {"A", "B"}
        assert result["suggested_target"] == "D"

    def test_conflict_daily_and_weekly_sorted_fine_to_coarse(self):
        rows = []
        for d in _dates("2024-01-01", 10, "D"):
            rows.append((d, "DAILY_SKU", 5.0))
        for d in _dates("2024-01-01", 6, "W"):
            rows.append((d, "WEEKLY_SKU", 20.0))
        result = DataProfiler().detect_granularity(_df(rows), "date", "sku")
        assert result["status"] == "conflict"
        assert result["detected"] == ["D", "W"]
        assert result["skus_by_frequency"]["D"] == ["DAILY_SKU"]
        assert result["skus_by_frequency"]["W"] == ["WEEKLY_SKU"]
        assert result["suggested_target"] == "W"  # coarsest present

    def test_conflict_three_frequencies_target_is_coarsest(self):
        rows = []
        for d in _dates("2024-01-01", 10, "D"):
            rows.append((d, "D_SKU", 5.0))
        for d in _dates("2024-01-01", 6, "W"):
            rows.append((d, "W_SKU", 20.0))
        for d in _dates("2024-01-01", 4, "MS"):
            rows.append((d, "MS_SKU", 90.0))
        result = DataProfiler().detect_granularity(_df(rows), "date", "sku")
        assert result["status"] == "conflict"
        assert result["detected"] == ["D", "W", "MS"]
        assert result["suggested_target"] == "MS"

    def test_irregular_sku_excluded_from_conflict(self):
        rows = []
        for d in _dates("2024-01-01", 10, "D"):
            rows.append((d, "REGULAR", 5.0))
        # Only 2 erratic points — insufficient history, must not create a false conflict.
        rows.append(("2024-01-01", "SHORT", 1.0))
        rows.append(("2024-03-15", "SHORT", 1.0))
        result = DataProfiler().detect_granularity(_df(rows), "date", "sku")
        assert result["status"] == "homogeneous"
        assert result["detected"] == ["D"]
        assert "SHORT" not in result["skus_by_frequency"].get("D", [])

    def test_single_series_no_group_column_is_always_homogeneous(self):
        rows = [(d, "__all__", 5.0) for d in _dates("2024-01-01", 10, "D")]
        result = DataProfiler().detect_granularity(_df(rows), "date", None)
        assert result["status"] == "homogeneous"
        assert result["detected"] == ["D"]

    def test_missing_date_column_returns_unknown(self):
        df = _df([("2024-01-01", "A", 5.0)])
        result = DataProfiler().detect_granularity(df, None, "sku")
        assert result["status"] == "unknown"
        assert result["detected"] == []
        assert result["suggested_target"] is None
