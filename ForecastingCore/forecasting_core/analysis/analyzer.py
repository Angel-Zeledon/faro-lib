"""
TimeSeriesAnalyzer — orchestrates all statistical analysis per SKU.

Runs decomposition, stationarity, seasonality, autocorrelation, trend,
and distribution analysis, returning a rich dict per SKU or a flat
summary DataFrame across all SKUs.

Example:
    analyzer = TimeSeriesAnalyzer(df, date_col="date", target_col="sales",
                                   group_col="sku", seasonal_period=7)

    # Full analysis for one SKU
    report = analyzer.analyze("SKU_A")

    # All SKUs at once
    all_reports = analyzer.analyze()     # → {sku: report_dict}

    # Flat summary table
    df = analyzer.summary()              # → DataFrame, one row per SKU
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


class TimeSeriesAnalyzer:
    """
    Full statistical analysis of one or many time series.

    Args:
        df:              DataFrame containing the time series data.
        date_col:        Name of the date column.
        target_col:      Name of the target (numeric) column.
        group_col:       Name of the SKU / group column (None = single series).
        seasonal_period: Override seasonal period. If None, auto-detected per SKU.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        date_col: str,
        target_col: str,
        group_col: Optional[str] = None,
        seasonal_period: Optional[int] = None,
    ):
        self._df             = df.copy()
        self._date_col       = date_col
        self._target_col     = target_col
        self._group_col      = group_col
        self._seasonal_period = seasonal_period

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def analyze(self, sku: Optional[str] = None):
        """
        Run full analysis.

        Args:
            sku: If provided, analyze only that SKU and return a single dict.
                 If None, analyze all SKUs and return {sku: dict}.

        Returns:
            dict  when sku is specified.
            dict[str, dict]  when sku is None.
        """
        if sku is not None:
            return self._analyze_one(sku)

        if not self._group_col:
            return self._analyze_one(None)

        skus = self._df[self._group_col].astype(str).unique().tolist()
        result = {}
        for s in skus:
            try:
                result[s] = self._analyze_one(s)
            except Exception as e:
                log.warning(f"Analysis failed for SKU {s}: {e}")
                result[s] = {"sku": s, "error": str(e)}
        return result

    def summary(self) -> pd.DataFrame:
        """
        Flat DataFrame with one row per SKU and all key metrics as columns.

        Useful for comparing SKUs, sorting by seasonality strength,
        trend significance, intermittency class, etc.

        Columns include:
            sku, n, mean, std, cv, skewness, kurtosis,
            zero_pct, outlier_pct, best_distribution,
            croston_class, adi, cv2,
            stationarity, diff_order,
            dominant_period, seasonal_strength, seasonality_class,
            trend_strength,
            trend_direction, trend_pvalue, sens_slope, n_change_points,
            suggested_ar_order, suggested_ma_order, is_white_noise
        """
        if not self._group_col:
            reports = {"__all__": self._analyze_one(None)}
        else:
            reports = self.analyze()

        rows = []
        for sku, r in reports.items():
            if "error" in r:
                rows.append({"sku": sku, "error": r["error"]})
                continue
            rows.append(self._flatten_report(r))
        return pd.DataFrame(rows)

    # ──────────────────────────────────────────────────────────────────────
    # Core analysis of one SKU
    # ──────────────────────────────────────────────────────────────────────

    def _get_series(self, sku: Optional[str]) -> tuple[np.ndarray, pd.Series]:
        """Return (values_array, date_series) for the given SKU."""
        if sku is not None and self._group_col:
            sub = self._df[self._df[self._group_col].astype(str) == sku]
        else:
            sub = self._df
        sub = sub.sort_values(self._date_col)
        values = sub[self._target_col].astype(float).values
        dates  = pd.to_datetime(sub[self._date_col])
        return values, dates

    def _infer_period(self, series: np.ndarray) -> int:
        """Return seasonal period to use — configured or auto-detected."""
        if self._seasonal_period:
            return int(self._seasonal_period)
        from forecasting_core.analysis.seasonality import detect_seasonality
        r = detect_seasonality(series)
        return int(r.get("dominant_period") or 7)

    def _date_range_info(self, dates: pd.Series) -> dict:
        if len(dates) < 2:
            return {}
        delta_days = (dates.iloc[-1] - dates.iloc[-2]).days
        if delta_days == 1:   freq = "daily"
        elif delta_days == 7: freq = "weekly"
        elif 28 <= delta_days <= 31: freq = "monthly"
        elif 88 <= delta_days <= 93: freq = "quarterly"
        else:                 freq = f"~{delta_days}d"
        return {
            "start":          str(dates.iloc[0])[:10],
            "end":            str(dates.iloc[-1])[:10],
            "n_days":         int((dates.iloc[-1] - dates.iloc[0]).days),
            "freq_detected":  freq,
        }

    def _analyze_one(self, sku: Optional[str]) -> dict:
        from forecasting_core.analysis import (
            decomposition as dec_mod,
            stationarity  as stat_mod,
            autocorrelation as acf_mod,
            seasonality   as seas_mod,
            trend         as trend_mod,
            distribution  as dist_mod,
        )

        series, dates = self._get_series(sku)
        period = self._infer_period(series)
        n = len(series)

        report: dict = {
            "sku":           str(sku) if sku else "__all__",
            "n_observations": n,
            "date_range":    self._date_range_info(dates),
            "seasonal_period_used": period,
        }

        # ── Distribution & descriptive stats (always runs) ──────────────
        report["distribution"] = dist_mod.distribution_report(series)

        # ── Stationarity ─────────────────────────────────────────────────
        if n >= 8:
            try:
                report["stationarity"] = stat_mod.stationarity_report(series)
            except Exception as e:
                report["stationarity"] = {"error": str(e)}

        # ── Seasonality ───────────────────────────────────────────────────
        if n >= 6:
            try:
                report["seasonality"] = seas_mod.detect_seasonality(
                    series, max_period=min(n // 3, 365)
                )
            except Exception as e:
                report["seasonality"] = {"error": str(e)}

        # ── STL Decomposition (needs >= 2 × period) ───────────────────────
        if n >= 2 * period:
            try:
                report["decomposition"] = dec_mod.decompose(series, period)
            except Exception as e:
                report["decomposition"] = {"error": str(e)}

        # ── Autocorrelation ───────────────────────────────────────────────
        if n >= 6:
            try:
                report["autocorrelation"] = acf_mod.autocorrelation_report(series)
            except Exception as e:
                report["autocorrelation"] = {"error": str(e)}

        # ── Trend ─────────────────────────────────────────────────────────
        if n >= 4:
            try:
                report["trend"] = trend_mod.trend_report(series)
            except Exception as e:
                report["trend"] = {"error": str(e)}

        return report

    # ──────────────────────────────────────────────────────────────────────
    # Flatten one report to a summary row
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _flatten_report(r: dict) -> dict:
        row: dict = {
            "sku": r.get("sku"),
            "n":   r.get("n_observations", 0),
        }

        # Distribution
        d = r.get("distribution", {})
        row.update({
            "mean":              d.get("mean"),
            "std":               d.get("std"),
            "min":               d.get("min"),
            "max":               d.get("max"),
            "median":            d.get("median"),
            "q1":                d.get("q1"),
            "q3":                d.get("q3"),
            "iqr":               d.get("iqr"),
            "cv":                d.get("cv"),
            "skewness":          d.get("skewness"),
            "kurtosis":          d.get("kurtosis"),
            "zero_pct":          d.get("zero_pct"),
            "missing_pct":       d.get("missing_pct"),
            "outlier_count":     d.get("outlier_count"),
            "outlier_pct":       d.get("outlier_pct"),
            "best_distribution": d.get("best_distribution"),
            "is_normal":         d.get("normality", {}).get("is_normal"),
        })
        cr = d.get("croston", {})
        row.update({
            "croston_class": cr.get("classification"),
            "adi":           cr.get("adi"),
            "cv2":           cr.get("cv2"),
        })

        # Stationarity
        s = r.get("stationarity", {})
        row["stationarity"]  = s.get("verdict")
        row["diff_order"]    = s.get("diff_order")
        row["adf_pvalue"]    = s.get("adf", {}).get("pvalue")
        row["kpss_pvalue"]   = s.get("kpss", {}).get("pvalue")

        # Seasonality
        seas = r.get("seasonality", {})
        row["dominant_period"]   = seas.get("dominant_period")
        row["seasonal_strength"] = seas.get("seasonal_strength")
        row["seasonality_class"] = seas.get("classification")

        # Decomposition strengths
        dec = r.get("decomposition", {})
        row["trend_strength_stl"]    = dec.get("trend_strength")
        row["seasonal_strength_stl"] = dec.get("seasonal_strength")

        # Trend
        tr = r.get("trend", {})
        mk = tr.get("mann_kendall", {})
        row["trend_direction"]  = mk.get("direction")
        row["trend_pvalue"]     = mk.get("pvalue")
        row["sens_slope"]       = tr.get("sens_slope")
        row["linear_r2"]        = tr.get("linear", {}).get("r2")
        row["n_change_points"]  = len(tr.get("change_points", []))

        # Autocorrelation
        ac = r.get("autocorrelation", {})
        row["suggested_ar_order"] = ac.get("suggested_ar_order")
        row["suggested_ma_order"] = ac.get("suggested_ma_order")
        lb = ac.get("ljung_box", {})
        row["is_white_noise"]     = lb.get("is_white_noise")
        row["ljung_box_pvalue"]   = lb.get("pvalue")

        return row
