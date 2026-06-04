"""
InspectAccessor — Dataset profiling and analysis.

Accessed via:
    dataset.inspect.summary()
    dataset.inspect.nulls()
    dataset.inspect.types()
    dataset.inspect.correlations()
    dataset.inspect.outliers()
    dataset.inspect.memory()
    dataset.inspect.time_series()
    dataset.inspect.report()
"""

from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from forecastlib.data.dataset import Dataset


class InspectAccessor:
    """
    Profiling and diagnostics for a Dataset.

    All methods return structured objects (DataFrames or dicts) and
    print a formatted summary to stdout by default.
    """

    def __init__(self, dataset: "Dataset"):
        self._dataset = dataset

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self, verbose: bool = True) -> pd.DataFrame:
        """
        Comprehensive column-by-column overview.

        Returns a DataFrame with one row per column including:
        dtype, type, role, n_unique, null%, min, max, mean.

        >>> dataset.inspect.summary()
        """
        df     = self._dataset._df
        schema = self._dataset._schema
        rows   = []

        for col in df.columns:
            s       = df[col]
            meta    = schema.columns.get(col)
            role    = "—"
            if col == schema.target_col:   role = "target"
            elif col == schema.datetime_col: role = "datetime"
            elif col == schema.group_col:    role = "group"

            row = {
                "column":   col,
                "dtype":    str(s.dtype),
                "type":     meta.col_type.value if meta else "?",
                "role":     role,
                "n_unique": int(s.nunique()),
                "null%":    round(s.isna().mean() * 100, 1),
                "n_rows":   len(s),
            }

            if pd.api.types.is_numeric_dtype(s):
                row.update({
                    "min":  round(float(s.min()), 4) if not s.isna().all() else None,
                    "mean": round(float(s.mean()), 4) if not s.isna().all() else None,
                    "max":  round(float(s.max()), 4) if not s.isna().all() else None,
                    "std":  round(float(s.std()), 4) if not s.isna().all() else None,
                })
            else:
                row.update({"min": None, "mean": None, "max": None, "std": None})

            if meta:
                row["is_constant"] = meta.is_constant
                row["sample"]      = str(meta.sample_values[:3])

            rows.append(row)

        result = pd.DataFrame(rows)

        if verbose:
            rows_n, cols_n = df.shape
            print(f"\n{'-'*60}")
            print(f"  Dataset: {rows_n:,} rows x {cols_n} columns  |  {self._dataset._source}")
            print(f"  Target: {schema.target_col or '-'}  "
                  f"Datetime: {schema.datetime_col or '-'}  "
                  f"Group: {schema.group_col or '-'}")
            print(f"{'-'*60}")
            print(result.to_string(index=False))
            print(f"{'-'*60}\n")

        return result

    # ------------------------------------------------------------------
    # Null analysis
    # ------------------------------------------------------------------

    def nulls(self, verbose: bool = True) -> pd.DataFrame:
        """
        Null-value analysis per column.

        Returns columns with null values sorted by null rate descending.

        >>> dataset.inspect.nulls()
        """
        df    = self._dataset._df
        total = len(df)
        rows  = []

        for col in df.columns:
            n_null = int(df[col].isna().sum())
            if n_null > 0:
                rows.append({
                    "column":    col,
                    "n_nulls":   n_null,
                    "null%":     round(n_null / total * 100, 2),
                    "dtype":     str(df[col].dtype),
                    "suggested": _null_suggestion(df[col]),
                })

        result = pd.DataFrame(rows).sort_values("null%", ascending=False) if rows else pd.DataFrame()

        if verbose:
            if result.empty:
                print("  No null values found in this dataset.")
            else:
                print(f"\n  {len(result)} column(s) with null values:")
                print(result.to_string(index=False))
                print()

        return result

    # ------------------------------------------------------------------
    # Types
    # ------------------------------------------------------------------

    def types(self, verbose: bool = True) -> pd.DataFrame:
        """
        Column type and role overview (compact).

        >>> dataset.inspect.types()
        """
        df     = self._dataset._df
        schema = self._dataset._schema
        rows   = []

        for col in df.columns:
            meta = schema.columns.get(col)
            role = ("target"   if col == schema.target_col   else
                    "datetime" if col == schema.datetime_col else
                    "group"    if col == schema.group_col    else "feature")
            rows.append({
                "column":   col,
                "dtype":    str(df[col].dtype),
                "type":     meta.col_type.value if meta else "?",
                "role":     role,
                "n_unique": int(df[col].nunique()),
                "null%":    round(df[col].isna().mean() * 100, 1),
            })

        result = pd.DataFrame(rows)
        if verbose:
            print(result.to_string(index=False))
        return result

    # ------------------------------------------------------------------
    # Correlations
    # ------------------------------------------------------------------

    def correlations(
        self,
        method: str = "pearson",
        threshold: float = 0.0,
        verbose: bool = True,
    ) -> pd.DataFrame:
        """
        Correlation matrix for all numeric columns.

        Parameters
        ----------
        method : {"pearson", "spearman", "kendall"}, default "pearson"
        threshold : float, default 0.0
            Only show correlations above this absolute value.
        verbose : bool, default True

        >>> dataset.inspect.correlations(threshold=0.5)
        """
        from forecastlib.exceptions import InspectionError

        df  = self._dataset._df
        num = df.select_dtypes(include="number")

        if num.empty:
            raise InspectionError("No numeric columns for correlation analysis.")

        corr = num.corr(method=method)

        if verbose and threshold > 0:
            # Show only high-correlation pairs
            pairs = []
            cols  = corr.columns.tolist()
            for i, c1 in enumerate(cols):
                for c2 in cols[i+1:]:
                    v = corr.loc[c1, c2]
                    if abs(v) >= threshold:
                        pairs.append({"col_1": c1, "col_2": c2, "correlation": round(v, 4)})
            pairs_df = pd.DataFrame(pairs).sort_values("correlation", ascending=False, key=abs)
            print(f"\n  Pairs with |corr| >= {threshold}:")
            print(pairs_df.to_string(index=False) if not pairs_df.empty else "  None found.")
            print()

        return corr

    # ------------------------------------------------------------------
    # Outlier detection
    # ------------------------------------------------------------------

    def outliers(
        self,
        method: str = "iqr",
        factor: float = 3.0,
        verbose: bool = True,
    ) -> pd.DataFrame:
        """
        Detect outliers in numeric columns.

        Parameters
        ----------
        method : {"iqr", "zscore"}, default "iqr"
        factor : float, default 3.0
            IQR multiplier (method="iqr") or z-score threshold (method="zscore").

        >>> dataset.inspect.outliers()
        >>> dataset.inspect.outliers(method="zscore", factor=2.5)
        """
        df   = self._dataset._df
        rows = []

        for col in df.select_dtypes(include="number").columns:
            s = df[col].dropna()
            if method == "iqr":
                q1, q3 = s.quantile(0.25), s.quantile(0.75)
                iqr    = q3 - q1
                mask   = (s < q1 - factor * iqr) | (s > q3 + factor * iqr)
            else:  # zscore
                z    = (s - s.mean()) / (s.std() + 1e-8)
                mask = z.abs() > factor

            n_out = int(mask.sum())
            if n_out > 0:
                rows.append({
                    "column":      col,
                    "n_outliers":  n_out,
                    "outlier%":    round(n_out / len(s) * 100, 2),
                    "min_outlier": round(float(s[mask].min()), 4),
                    "max_outlier": round(float(s[mask].max()), 4),
                    "suggested":   f"dataset.cols(['{col}']).clean.clip()",
                })

        result = pd.DataFrame(rows).sort_values("outlier%", ascending=False) if rows else pd.DataFrame()

        if verbose:
            if result.empty:
                print(f"  No outliers detected (method={method!r}, factor={factor}).")
            else:
                print(f"\n  {len(result)} column(s) with outliers (method={method!r}):")
                print(result.to_string(index=False))
                print()

        return result

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    def memory(self, verbose: bool = True) -> pd.DataFrame:
        """
        Memory usage per column.

        >>> dataset.inspect.memory()
        """
        df    = self._dataset._df
        usage = df.memory_usage(deep=True)
        total = usage.sum() / 1024 ** 2
        rows  = []

        for col in df.columns:
            kb = usage.get(col, 0) / 1024
            rows.append({
                "column": col,
                "dtype":  str(df[col].dtype),
                "KB":     round(kb, 2),
                "MB":     round(kb / 1024, 4),
            })

        result = pd.DataFrame(rows).sort_values("KB", ascending=False)

        if verbose:
            print(f"\n  Total memory: {total:.2f} MB")
            print(result.to_string(index=False))
            print()

        return result

    # ------------------------------------------------------------------
    # Time-series specific
    # ------------------------------------------------------------------

    def time_series(self, verbose: bool = True) -> dict:
        """
        Time-series health diagnostics.

        Checks: sorted dates, duplicates, gaps, frequency, n_skus, series length.

        >>> dataset.inspect.time_series()
        """
        from forecastlib.time_series.validation import TimeSeriesValidator
        report = TimeSeriesValidator(self._dataset).check()

        result = {
            "is_sorted":    report.is_sorted,
            "has_gaps":     report.has_gaps,
            "has_dupes":    report.has_duplicate_timestamps,
            "frequency":    report.inferred_frequency,
            "issues":       report.issues,
            "suggestions":  report.suggestions,
        }

        schema = self._dataset._schema
        df     = self._dataset._df
        if schema.group_col and schema.group_col in df.columns:
            result["n_groups"] = int(df[schema.group_col].nunique())
            lengths = df.groupby(schema.group_col).size()
            result["min_series_len"] = int(lengths.min())
            result["max_series_len"] = int(lengths.max())
            result["mean_series_len"] = round(float(lengths.mean()), 1)

        if verbose:
            print(report)

        return result

    def stats(
        self,
        sku: Optional[str] = None,
        seasonal_period: Optional[int] = None,
        verbose: bool = True,
    ):
        """
        Full statistical and time-series analysis using TimeSeriesAnalyzer.

        Runs: descriptive stats, normality, outliers, intermittency (ADI/CV²),
        distribution fitting, stationarity (ADF + KPSS), STL decomposition,
        seasonality (FFT + ACF), trend (Mann-Kendall + Sen's slope + change points),
        and autocorrelation (ACF/PACF + Ljung-Box).

        Parameters
        ----------
        sku : str or None
            If given, analyze only that group/SKU and return a single dict.
            If None, analyze all groups and return {sku: dict}.
        seasonal_period : int or None
            Override seasonal period for decomposition and seasonality detection.
            Defaults to auto-detection.
        verbose : bool, default True
            Print a compact summary to stdout.

        Returns
        -------
        dict (single SKU) or dict[str, dict] (all SKUs)

        >>> dataset.inspect.stats()
        >>> dataset.inspect.stats(sku="SKU_A")
        >>> dataset.inspect.stats(seasonal_period=7)
        """
        from forecasting_core.analysis.analyzer import TimeSeriesAnalyzer

        schema = self._dataset._schema
        df     = self._dataset._df

        if schema.datetime_col is None:
            raise ValueError("stats() requires a datetime column. "
                             "Set schema.datetime_col before calling.")
        if schema.target_col is None:
            raise ValueError("stats() requires a target column. "
                             "Set schema.target_col before calling.")

        analyzer = TimeSeriesAnalyzer(
            df=df,
            date_col=schema.datetime_col,
            target_col=schema.target_col,
            group_col=schema.group_col,
            seasonal_period=seasonal_period,
        )

        result = analyzer.analyze(sku)

        if verbose:
            self._print_stats_summary(result, sku)

        return result

    def stats_summary(
        self,
        seasonal_period: Optional[int] = None,
    ) -> "pd.DataFrame":
        """
        Flat DataFrame with one row per SKU covering all analysis metrics.

        Convenient for comparing all series at once: sort by seasonality_class,
        filter by croston_class, or rank by trend_strength.

        >>> df = dataset.inspect.stats_summary()
        >>> df.sort_values("seasonal_strength", ascending=False)
        """
        from forecasting_core.analysis.analyzer import TimeSeriesAnalyzer

        schema = self._dataset._schema
        df     = self._dataset._df

        if schema.datetime_col is None:
            raise ValueError("stats_summary() requires a datetime column.")
        if schema.target_col is None:
            raise ValueError("stats_summary() requires a target column.")

        analyzer = TimeSeriesAnalyzer(
            df=df,
            date_col=schema.datetime_col,
            target_col=schema.target_col,
            group_col=schema.group_col,
            seasonal_period=seasonal_period,
        )
        return analyzer.summary()

    @staticmethod
    def _print_stats_summary(result: dict, sku: Optional[str]) -> None:
        """Print a compact human-readable summary of the analysis result."""
        if sku is not None:
            reports = {str(sku): result}
        elif isinstance(result, dict) and result and not any(
            k in result for k in ("distribution", "stationarity", "trend")
        ):
            reports = result
        else:
            reports = {"__all__": result}

        print(f"\n{'─'*60}")
        print("  TIME-SERIES STATISTICS SUMMARY")
        print(f"{'─'*60}")
        for s, r in reports.items():
            if "error" in r:
                print(f"  [{s}] ERROR: {r['error']}")
                continue
            d = r.get("distribution", {})
            st = r.get("stationarity", {})
            seas = r.get("seasonality", {})
            tr = r.get("trend", {})
            mk = tr.get("mann_kendall", {})
            cr = d.get("croston", {})
            print(f"\n  SKU: {s}  |  n={r.get('n_observations', '?')}  "
                  f"|  {r.get('date_range', {}).get('freq_detected', '?')} series")
            print(f"  Distribution : mean={d.get('mean','?')}  std={d.get('std','?')}  "
                  f"cv={d.get('cv','?')}  skew={d.get('skewness','?')}")
            print(f"  Zeros        : {d.get('zero_pct','?')}%  "
                  f"outliers={d.get('outlier_pct','?')}%  "
                  f"dist={d.get('best_distribution','?')}")
            print(f"  Intermittency: class={cr.get('classification','?')}  "
                  f"ADI={cr.get('adi','?')}  CV²={cr.get('cv2','?')}")
            print(f"  Stationarity : {st.get('verdict','?')}  "
                  f"diff_order={st.get('diff_order','?')}")
            print(f"  Seasonality  : period={seas.get('dominant_period','?')}  "
                  f"strength={seas.get('seasonal_strength','?')}  "
                  f"class={seas.get('classification','?')}")
            print(f"  Trend        : {mk.get('direction','?')}  "
                  f"p={mk.get('pvalue','?')}  "
                  f"slope={tr.get('sens_slope','?')}")
        print(f"{'─'*60}\n")

    # ------------------------------------------------------------------
    # Full report
    # ------------------------------------------------------------------

    def report(self) -> None:
        """
        Print a comprehensive multi-section report.

        Sections: Overview, Types, Nulls, Outliers, Time-series (if configured).

        >>> dataset.inspect.report()
        """
        print("\n" + "═" * 60)
        print("  DATASET REPORT")
        print("═" * 60)

        self.summary(verbose=True)
        self.nulls(verbose=True)
        self.outliers(verbose=True)

        schema = self._dataset._schema
        if schema.datetime_col:
            print("\n── TIME-SERIES ANALYSIS ──")
            self.time_series(verbose=True)

        print("═" * 60 + "\n")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _null_suggestion(s: pd.Series) -> str:
    if pd.api.types.is_numeric_dtype(s):
        return "fill.median() or fill.interpolate()"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "fill.forward()"
    return "fill.mode() or fill.constant('unknown')"
