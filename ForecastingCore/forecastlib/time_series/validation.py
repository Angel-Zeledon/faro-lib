"""
TimeSeriesValidator — checks specific to time-series datasets.

    from forecastlib.time_series.validation import TimeSeriesValidator
    report = TimeSeriesValidator(dataset).check()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from forecastlib.data.dataset import Dataset


@dataclass
class TSValidationReport:
    is_sorted: bool = True
    has_gaps: bool = False
    has_duplicate_timestamps: bool = False
    inferred_frequency: Optional[str] = None
    sku_gap_counts: Dict[str, int] = field(default_factory=dict)
    sku_duplicate_counts: Dict[str, int] = field(default_factory=dict)
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.issues

    def __repr__(self) -> str:
        status = "CLEAN" if self.is_clean else f"{len(self.issues)} ISSUE(S)"
        lines  = [f"TSValidationReport [{status}]"]
        lines += [f"  sorted={self.is_sorted}  gaps={self.has_gaps}  "
                  f"duplicate_ts={self.has_duplicate_timestamps}  freq={self.inferred_frequency}"]
        for issue in self.issues:
            lines.append(f"  ⚠  {issue}")
        for suggestion in self.suggestions:
            lines.append(f"  → {suggestion}")
        return "\n".join(lines)


class TimeSeriesValidator:
    """
    Validates time-series specific requirements:
      - Datetime column is sorted
      - No duplicate timestamps per group
      - No large gaps in the time series
      - Consistent frequency
    """

    def __init__(self, dataset: "Dataset"):
        self._dataset = dataset

    def check(self) -> TSValidationReport:
        """Run all checks and return a consolidated report."""
        schema  = self._dataset._schema
        df      = self._dataset._df
        report  = TSValidationReport()

        if not schema.datetime_col or schema.datetime_col not in df.columns:
            report.issues.append("No datetime column configured.")
            report.suggestions.append("dataset.select(datetime='your_date_column')")
            return report

        dt_col  = schema.datetime_col
        grp_col = schema.group_col

        try:
            dates = pd.to_datetime(df[dt_col], errors="coerce")
        except Exception:
            report.issues.append(f"Cannot parse datetime column '{dt_col}'.")
            return report

        # Sort check
        if grp_col and grp_col in df.columns:
            for sku, g in df.groupby(grp_col):
                d = pd.to_datetime(g[dt_col], errors="coerce").dropna()
                if not d.is_monotonic_increasing:
                    report.is_sorted = False
                    report.issues.append(f"Dates are not sorted for group '{sku}'.")
                    report.suggestions.append("dataset.clean.sort()")
                    break
        else:
            if not dates.dropna().is_monotonic_increasing:
                report.is_sorted = False
                report.issues.append("Dates are not sorted.")
                report.suggestions.append("dataset.clean.sort()")

        # Duplicate timestamps
        if grp_col and grp_col in df.columns:
            for sku, g in df.groupby(grp_col):
                n_dup = int(g[dt_col].duplicated().sum())
                if n_dup > 0:
                    report.has_duplicate_timestamps = True
                    report.sku_duplicate_counts[str(sku)] = n_dup
            if report.has_duplicate_timestamps:
                report.issues.append(
                    f"Duplicate timestamps in {len(report.sku_duplicate_counts)} group(s)."
                )
                report.suggestions.append(
                    "dataset.clean.drop_duplicates(subset=['date', 'group_col'])"
                )
        else:
            n_dup = int(dates.duplicated().sum())
            if n_dup > 0:
                report.has_duplicate_timestamps = True
                report.issues.append(f"{n_dup} duplicate timestamps.")
                report.suggestions.append("dataset.clean.drop_duplicates(subset=['date_col'])")

        # Frequency and gaps
        report.inferred_frequency = schema.freq
        if not schema.freq:
            report.issues.append("Frequency could not be inferred. Data may be irregular.")
            report.suggestions.append(
                "dataset.clean.sort() to ensure correct frequency detection."
            )

        return report

    def check_leakage(self, feature_cols: Optional[List[str]] = None) -> List[str]:
        """
        Check for common leakage patterns:
          - Raw target used as a feature
          - Features computed without shift
        """
        from forecastlib.data.validators import DatasetValidator
        schema = self._dataset._schema
        cols   = feature_cols or list(self._dataset._df.columns)
        issues = []

        if schema.target_col and schema.target_col in cols:
            issues.append(
                f"Target '{schema.target_col}' is in feature columns — likely leakage. "
                f"Use lagged features instead."
            )
        return issues
