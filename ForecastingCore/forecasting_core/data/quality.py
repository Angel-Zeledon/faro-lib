"""
DataQualityChecker — validates data integrity per SKU.

Detects: missing dates, outliers, intermittent series, insufficient history.
Classifies series with multi-label flags: stable / seasonal / intermittent / volatile / short.
A series can carry multiple flags simultaneously (e.g. seasonal + volatile).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

SERIES_STABLE       = "stable"
SERIES_SEASONAL     = "seasonal"
SERIES_INTERMITTENT = "intermittent"
SERIES_VOLATILE     = "volatile"
SERIES_SHORT        = "short"


def classify_series(
    series: pd.Series,
    min_history: int = 30,
    intermittency_threshold: float = 0.4,
    cv_volatile: float = 1.5,
    seasonal_period: int = 7,
) -> str:
    n = len(series)
    if n < min_history:
        return SERIES_SHORT
    if (series == 0).mean() >= intermittency_threshold:
        return SERIES_INTERMITTENT
    cv = series.std() / (abs(series.mean()) + 1e-8)
    if cv >= cv_volatile:
        return SERIES_VOLATILE
    if n >= seasonal_period * 2 + 1:
        try:
            from statsmodels.tsa.seasonal import STL
            res = STL(series.values, period=seasonal_period, robust=True).fit()
            strength = max(0, 1 - np.var(res.resid) / (np.var(res.seasonal + res.resid) + 1e-8))
            if strength > 0.3:
                return SERIES_SEASONAL
        except Exception:
            pass
    return SERIES_STABLE


def classify_series_multi(
    series: pd.Series,
    min_history: int = 30,
    intermittency_threshold: float = 0.4,
    cv_volatile: float = 1.5,
    seasonal_strength_threshold: float = 0.3,
    seasonal_period: int = 7,
) -> Tuple[Set[str], Dict[str, str]]:
    """
    Multi-label series classification. A series can receive more than one flag.

    Returns:
        (flags, reasons) where flags is a set of SERIES_* constants and
        reasons is {flag: human-readable explanation}.

    Examples:
        A Christmas-spike retail series → {"seasonal", "volatile"}
        A spare-parts series → {"intermittent"}
        A steady daily series → {"stable"}
    """
    n = len(series)
    if n < min_history:
        return {SERIES_SHORT}, {SERIES_SHORT: f"{n} points < {min_history} required"}

    flags: Set[str] = set()
    reasons: Dict[str, str] = {}

    zero_ratio = float((series == 0).mean())
    if zero_ratio >= intermittency_threshold:
        flags.add(SERIES_INTERMITTENT)
        reasons[SERIES_INTERMITTENT] = f"zero_ratio={zero_ratio:.0%} ≥ {intermittency_threshold:.0%}"

    cv = float(series.std() / (abs(series.mean()) + 1e-8))
    if cv >= cv_volatile:
        flags.add(SERIES_VOLATILE)
        reasons[SERIES_VOLATILE] = f"CV={cv:.2f} ≥ {cv_volatile}"

    if n >= seasonal_period * 2 + 1:
        try:
            from statsmodels.tsa.seasonal import STL
            res = STL(series.values, period=seasonal_period, robust=True).fit()
            strength = max(0.0, 1.0 - np.var(res.resid) / (np.var(res.seasonal + res.resid) + 1e-8))
            if strength > seasonal_strength_threshold:
                flags.add(SERIES_SEASONAL)
                reasons[SERIES_SEASONAL] = f"STL strength={strength:.2f} > {seasonal_strength_threshold}"
        except Exception:
            pass

    if not flags:
        flags.add(SERIES_STABLE)
        reasons[SERIES_STABLE] = f"CV={cv:.2f}, zero_ratio={zero_ratio:.0%}, no seasonal pattern detected"

    return flags, reasons


# Primary flag priority order (used for backward-compat series_type field)
_FLAG_PRIORITY = [SERIES_SHORT, SERIES_INTERMITTENT, SERIES_VOLATILE, SERIES_SEASONAL, SERIES_STABLE]


def _primary_flag(flags: Set[str]) -> str:
    for f in _FLAG_PRIORITY:
        if f in flags:
            return f
    return SERIES_STABLE


@dataclass
class SKUReport:
    sku: str
    n_rows: int
    missing_dates: int
    outlier_count: int
    zero_ratio: float
    is_intermittent: bool
    has_min_history: bool
    series_type: str           # primary flag — kept for backward compat
    quality_score: float
    warnings: List[str] = field(default_factory=list)
    series_flags: Set[str] = field(default_factory=set)     # multi-label flags
    series_reasons: Dict[str, str] = field(default_factory=dict)  # flag → explanation

    def to_dict(self) -> dict:
        total_expected = self.n_rows + self.missing_dates
        missing_pct = round(self.missing_dates / total_expected, 4) if total_expected > 0 else 0.0
        return {
            "sku": self.sku,
            "n_rows": self.n_rows,
            "series_type": self.series_type,
            "series_flags": sorted(self.series_flags),
            "series_reasons": self.series_reasons,
            "missing_dates": self.missing_dates,
            "missing_pct": missing_pct,
            "n_outliers": self.outlier_count,
            "zero_ratio": round(self.zero_ratio, 3),
            "quality_score": round(self.quality_score / 100.0, 4),
            "is_valid": self.has_min_history,
            "has_min_history": self.has_min_history,
            "warnings": self.warnings,
        }


class DataQualityChecker:
    """Per-SKU data quality validation, scoring and series classification."""

    def __init__(
        self,
        dt_col: str,
        target_col: str,
        group_col: Optional[str],
        min_history: int = 20,
        intermittency_threshold: float = 0.4,
        outlier_iqr_factor: float = 3.0,
        freq: Optional[str] = None,
        seasonal_period: int = 7,
        cv_volatile: float = 1.5,
        seasonal_strength_threshold: float = 0.3,
    ):
        self.dt_col = dt_col
        self.target_col = target_col
        self.group_col = group_col
        self.min_history = min_history
        self.intermittency_threshold = intermittency_threshold
        self.outlier_iqr_factor = outlier_iqr_factor
        self.freq = freq
        self.seasonal_period = seasonal_period
        self.cv_volatile = cv_volatile
        self.seasonal_strength_threshold = seasonal_strength_threshold

    def check(self, df: pd.DataFrame) -> Dict[str, SKUReport]:
        if self.group_col and self.group_col in df.columns:
            return {str(sku): self._check_sku(str(sku), g)
                    for sku, g in df.groupby(self.group_col)}
        return {"__all__": self._check_sku("__all__", df)}

    def summary(self, reports: Dict[str, SKUReport]) -> pd.DataFrame:
        return pd.DataFrame([r.to_dict() for r in reports.values()])

    def filter_valid_skus(self, df: pd.DataFrame, reports: Dict[str, SKUReport]) -> pd.DataFrame:
        valid = {sku for sku, r in reports.items() if r.has_min_history}
        if self.group_col:
            return df[df[self.group_col].astype(str).isin(valid)].copy()
        return df.copy()

    def clip_outliers(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        groups = df.groupby(self.group_col).groups if self.group_col else {"__all__": df.index}
        for _, idx in groups.items():
            s = df.loc[idx, self.target_col]
            q1, q3 = s.quantile(0.25), s.quantile(0.75)
            iqr = q3 - q1
            df.loc[idx, self.target_col] = s.clip(
                q1 - self.outlier_iqr_factor * iqr,
                q3 + self.outlier_iqr_factor * iqr,
            )
        return df

    def _check_sku(self, sku: str, g: pd.DataFrame) -> SKUReport:
        warnings: List[str] = []
        g = g.sort_values(self.dt_col).reset_index(drop=True)
        series = g[self.target_col].astype(float)
        n = len(g)

        missing = self._missing_dates(g)
        outliers = self._count_outliers(series)
        zero_ratio = float((series == 0).mean())
        has_history = n >= self.min_history
        is_intermittent = zero_ratio >= self.intermittency_threshold
        series_flags, series_reasons = classify_series_multi(
            series,
            min_history=self.min_history,
            intermittency_threshold=self.intermittency_threshold,
            cv_volatile=self.cv_volatile,
            seasonal_strength_threshold=self.seasonal_strength_threshold,
            seasonal_period=self.seasonal_period,
        )
        series_type = _primary_flag(series_flags)

        if not has_history:
            warnings.append(f"Only {n} rows (min={self.min_history})")
        if is_intermittent:
            warnings.append(f"Intermittent: {zero_ratio:.0%} zeros")
        if missing > 0:
            warnings.append(f"{missing} missing dates")
        if outliers > 0:
            warnings.append(f"{outliers} outliers")

        score = 100.0
        if not has_history: score -= 30
        score -= min(missing * 2, 20)
        score -= min(outliers * 3, 15)
        score -= min(zero_ratio * 20, 20)

        return SKUReport(sku=sku, n_rows=n, missing_dates=missing, outlier_count=outliers,
                         zero_ratio=zero_ratio, is_intermittent=is_intermittent,
                         has_min_history=has_history, series_type=series_type,
                         quality_score=max(0.0, score), warnings=warnings,
                         series_flags=series_flags, series_reasons=series_reasons)

    def _missing_dates(self, g: pd.DataFrame) -> int:
        if not self.freq:
            return 0
        try:
            dates = pd.to_datetime(g[self.dt_col], errors="coerce").dropna()
            full = pd.date_range(dates.min(), dates.max(), freq=self.freq)
            return max(0, len(full) - len(dates))
        except Exception:
            return 0

    def _count_outliers(self, s: pd.Series) -> int:
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        return int(((s < q1 - self.outlier_iqr_factor * iqr) |
                    (s > q3 + self.outlier_iqr_factor * iqr)).sum())
