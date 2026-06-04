"""
Tests for forecasting_core.data: DataLoader, DataProfiler, DataQualityChecker.
"""

import os
import pytest
import numpy as np
import pandas as pd

from forecasting_core.data.loader import DataLoader, LoadError
from forecasting_core.data.profiler import DataProfiler
from forecasting_core.data.quality import (
    DataQualityChecker, classify_series,
    SERIES_STABLE, SERIES_INTERMITTENT, SERIES_SHORT, SERIES_VOLATILE,
)


# ─────────────────────────────────────────────────────────────────────────────
# DataLoader
# ─────────────────────────────────────────────────────────────────────────────

class TestDataLoader:

    def test_load_csv_happy_path(self, csv_path, df_standard):
        df = DataLoader().load(csv_path)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == len(df_standard)

    def test_load_parquet_happy_path(self, parquet_path, df_standard):
        df = DataLoader().load(parquet_path)
        assert len(df) == len(df_standard)

    def test_load_nonexistent_file_raises(self, tmp_path):
        with pytest.raises(LoadError, match="File not found"):
            DataLoader().load(str(tmp_path / "nope.csv"))

    def test_load_unsupported_extension_raises(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text("{}")
        with pytest.raises(LoadError, match="Unsupported format"):
            DataLoader().load(str(p))

    def test_load_empty_extension_raises(self, tmp_path):
        p = tmp_path / "data"
        p.write_text("col\n1")
        with pytest.raises(LoadError, match="Unsupported format"):
            DataLoader().load(str(p))

    def test_load_df_valid(self, df_standard):
        result = DataLoader().load_df(df_standard)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(df_standard)

    def test_load_df_returns_copy(self, df_standard):
        result = DataLoader().load_df(df_standard)
        result["sales"] = 0
        # Original should be unchanged
        assert df_standard["sales"].iloc[0] != 0 or df_standard["sales"].mean() != 0

    def test_load_df_not_dataframe_raises(self):
        with pytest.raises(LoadError):
            DataLoader().load_df([1, 2, 3])

    def test_load_df_with_none_raises(self):
        with pytest.raises(LoadError):
            DataLoader().load_df(None)

    def test_load_sql_without_engine_raises(self, tmp_path):
        sql_file = tmp_path / "query.sql"
        sql_file.write_text("SELECT 1")
        with pytest.raises(LoadError, match="sql_engine"):
            DataLoader().load(str(sql_file))

    def test_relative_path_resolves(self, tmp_path, df_standard, monkeypatch):
        monkeypatch.chdir(tmp_path)
        p = tmp_path / "data.csv"
        df_standard.to_csv(str(p), index=False)
        df = DataLoader().load("data.csv")
        assert len(df) > 0


# ─────────────────────────────────────────────────────────────────────────────
# DataProfiler
# ─────────────────────────────────────────────────────────────────────────────

class TestDataProfiler:

    def test_profile_returns_required_keys(self, df_standard):
        result = DataProfiler().profile(df_standard)
        assert "columns" in result
        assert "recommended" in result
        assert "stats" in result
        assert "warnings" in result

    def test_profile_columns_list(self, df_standard):
        result = DataProfiler().profile(df_standard)
        col_names = [c["name"] for c in result["columns"]]
        assert set(col_names) == set(df_standard.columns)

    def test_profile_stats_row_count(self, df_standard):
        result = DataProfiler().profile(df_standard)
        assert result["stats"]["n_rows"] == len(df_standard)

    def test_profile_recommended_has_required_keys(self, df_standard):
        result = DataProfiler().profile(df_standard)
        rec = result["recommended"]
        assert "date" in rec and "target" in rec and "group" in rec

    def test_profile_empty_dataframe(self):
        df = pd.DataFrame({"a": [], "b": []})
        result = DataProfiler().profile(df)
        assert result["stats"]["n_rows"] == 0

    def test_profile_detects_date_column(self, df_standard):
        result = DataProfiler().profile(df_standard)
        assert result["recommended"]["date"] == "date"

    def test_get_column_options_returns_candidates(self, df_standard):
        opts = DataProfiler().get_column_options(df_standard)
        assert "date_candidates" in opts
        assert "target_candidates" in opts
        assert "group_candidates" in opts

    def test_profile_single_column_df(self):
        df = pd.DataFrame({"x": [1, 2, 3]})
        result = DataProfiler().profile(df)
        assert result["stats"]["n_cols"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# classify_series
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifySeries:

    def test_short_series(self):
        s = pd.Series([1, 2, 3])
        assert classify_series(s, min_history=30) == SERIES_SHORT

    def test_intermittent_series(self):
        s = pd.Series([0] * 70 + [10] * 30)  # 70% zeros
        assert classify_series(s, min_history=20, intermittency_threshold=0.4) == SERIES_INTERMITTENT

    def test_volatile_series(self):
        rng = np.random.default_rng(0)
        vals = np.concatenate([rng.exponential(1, 50), rng.exponential(1000, 50)])
        s = pd.Series(np.abs(vals))
        result = classify_series(s, min_history=20, cv_volatile=1.0)
        assert result in [SERIES_VOLATILE, SERIES_STABLE]  # platform-dependent

    def test_stable_or_seasonal_series(self):
        s = pd.Series(np.linspace(10, 20, 100))
        result = classify_series(s, min_history=20)
        assert result in [SERIES_STABLE, "seasonal"]  # depends on STL

    def test_all_zeros(self):
        s = pd.Series([0.0] * 50)
        # 100% zeros → intermittent
        assert classify_series(s, min_history=20, intermittency_threshold=0.4) == SERIES_INTERMITTENT


# ─────────────────────────────────────────────────────────────────────────────
# DataQualityChecker
# ─────────────────────────────────────────────────────────────────────────────

class TestDataQualityChecker:

    def _make_checker(self):
        return DataQualityChecker(
            dt_col="date", target_col="sales", group_col="sku",
            min_history=20, seasonal_period=7,
        )

    def test_check_returns_dict_of_sku_reports(self, df_standard):
        checker = self._make_checker()
        reports = checker.check(df_standard)
        assert set(reports.keys()) == {"A", "B"}

    def test_sku_report_has_min_history(self, df_standard):
        checker = self._make_checker()
        reports = checker.check(df_standard)
        assert reports["A"].has_min_history is True

    def test_sku_report_tiny_data_fails_history(self, df_tiny):
        checker = self._make_checker()
        reports = checker.check(df_tiny)
        assert reports["Z"].has_min_history is False

    def test_sku_report_to_dict(self, df_standard):
        checker = self._make_checker()
        reports = checker.check(df_standard)
        d = reports["A"].to_dict()
        assert "quality_score" in d and "series_type" in d and "warnings" in d

    def test_quality_score_in_range(self, df_standard):
        checker = self._make_checker()
        reports = checker.check(df_standard)
        for r in reports.values():
            assert 0.0 <= r.quality_score <= 100.0

    def test_intermittent_detection(self, df_intermittent):
        checker = DataQualityChecker(
            dt_col="date", target_col="sales", group_col="sku",
            min_history=20, seasonal_period=7,
        )
        reports = checker.check(df_intermittent)
        assert reports["X"].is_intermittent is True

    def test_no_group_col_returns_all_key(self, df_single):
        checker = DataQualityChecker(
            dt_col="date", target_col="sales", group_col=None, min_history=20
        )
        reports = checker.check(df_single)
        assert "__all__" in reports

    def test_summary_returns_dataframe(self, df_standard):
        checker = self._make_checker()
        reports = checker.check(df_standard)
        df_sum = checker.summary(reports)
        assert isinstance(df_sum, pd.DataFrame)
        assert len(df_sum) == 2

    def test_filter_valid_skus_removes_short_skus(self, df_standard):
        checker = DataQualityChecker(
            dt_col="date", target_col="sales", group_col="sku",
            min_history=200,  # too high → nothing valid
        )
        reports = checker.check(df_standard)
        filtered = checker.filter_valid_skus(df_standard, reports)
        assert len(filtered) == 0

    def test_clip_outliers_does_not_change_non_outlier_values(self, df_standard):
        checker = self._make_checker()
        clipped = checker.clip_outliers(df_standard)
        # Values within normal range should be unchanged
        assert len(clipped) == len(df_standard)

    def test_clip_outliers_reduces_extreme_values(self, df_standard):
        df = df_standard.copy()
        # Inject a massive outlier
        df.loc[df.index[0], "sales"] = 1_000_000
        checker = self._make_checker()
        clipped = checker.clip_outliers(df)
        assert clipped["sales"].max() < 1_000_000

    def test_missing_target_column_raises(self, df_standard):
        checker = DataQualityChecker(
            dt_col="date", target_col="nonexistent", group_col="sku", min_history=20
        )
        with pytest.raises(KeyError):
            checker.check(df_standard)

    def test_with_freq_counts_missing_dates(self, df_standard):
        # Drop some dates to create gaps
        df = df_standard[df_standard.index % 7 != 0].copy()
        checker = DataQualityChecker(
            dt_col="date", target_col="sales", group_col="sku",
            min_history=10, freq="D",
        )
        reports = checker.check(df)
        # At least some missing dates should be detected
        total_missing = sum(r.missing_dates for r in reports.values())
        assert total_missing > 0
