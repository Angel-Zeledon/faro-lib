"""Temporal aggregation utilities for forecast/historical series."""
import math
import statistics as _stats
from typing import Any, Dict, List, Optional

import pandas as pd

FREQ_RULES: Dict[str, str] = {
    "daily":     "D",
    "weekly":    "W-MON",
    "monthly":   "MS",
    "quarterly": "QS",
    "yearly":    "YS",
}

_FREQ_ORDER = ["daily", "weekly", "monthly", "quarterly", "yearly"]

# User-facing PLANNING periods (multi-period feature). Distinct from the chart
# chip's _FREQ_ORDER, which also offers quarterly/yearly: planning only re-plans
# at day/week/month grains.
_PLANNING_ORDER = ["daily", "weekly", "monthly"]


def detect_frequency(dates: List[str]) -> str:
    if len(dates) < 2:
        return "daily"
    try:
        dts = pd.to_datetime(dates).sort_values()
        median_days = dts.diff().dropna().median().days
        if median_days <= 1:
            return "daily"
        if median_days <= 8:
            return "weekly"
        if median_days <= 35:
            return "monthly"
        if median_days <= 100:
            return "quarterly"
        return "yearly"
    except Exception:
        return "daily"


def available_granularities(base_freq: str, n_points: int = 0) -> List[str]:
    """Return valid coarser granularities (always includes base_freq itself).

    Used by the /skus chart chip (offers all coarser grains, incl.
    quarterly/yearly). Planning uses `planning_granularities` instead, which
    gates by real bucket counts and is restricted to day/week/month.
    """
    base_idx = _FREQ_ORDER.index(base_freq) if base_freq in _FREQ_ORDER else 0
    result = list(_FREQ_ORDER[base_idx:])
    return result


def _period_alias(rule: str) -> str:
    """pandas Period alias for each resample rule."""
    return {"D": "D", "W-MON": "W", "MS": "M", "QS": "Q", "YS": "Y"}.get(rule, "D")


def bucket_count(dates: List[str], granularity: str) -> int:
    """Number of distinct period-buckets the dates span at `granularity`."""
    if not dates:
        return 0
    rule = FREQ_RULES.get(granularity, "D")
    try:
        idx = pd.to_datetime(pd.Series(dates)).dropna()
        if idx.empty:
            return 0
        return int(idx.dt.to_period(_period_alias(rule)).nunique())
    except Exception:
        return 0


def planning_granularities(
    base_freq: str, dates: List[str], min_buckets: int = 20
) -> List[str]:
    """Planning grains (base_freq and coarser, within day/week/month) that the
    data can actually train at.

    A grain qualifies iff the history spans >= min_buckets buckets at it.
    base_freq is always included (you can plan at your native grain even with
    thin data); coarser grains are gated by the bucket count.
    """
    if base_freq not in _PLANNING_ORDER:
        base_freq = "daily"
    start = _PLANNING_ORDER.index(base_freq)
    out: List[str] = []
    for g in _PLANNING_ORDER[start:]:
        if g == base_freq or bucket_count(dates, g) >= min_buckets:
            out.append(g)
    return out


def _safe_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def aggregate_historical(
    points: List[Dict[str, Any]],
    granularity: str,
    agg: str = "sum",
) -> List[Dict[str, Any]]:
    if not points:
        return []
    df = pd.DataFrame(points)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    if "value" not in df.columns:
        return points

    rule = FREQ_RULES.get(granularity, "D")
    s = pd.to_numeric(df["value"], errors="coerce")
    if agg == "mean":
        agg_s = s.resample(rule).mean()
    else:
        agg_s = s.resample(rule).sum()

    return [
        {"date": ts.strftime("%Y-%m-%d"), "value": _safe_float(v)}
        for ts, v in agg_s.dropna().items()
    ]


def aggregate_forecast(
    points: List[Dict[str, Any]],
    granularity: str,
    agg: str = "sum",
) -> List[Dict[str, Any]]:
    if not points:
        return []
    df = pd.DataFrame(points)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    rule = FREQ_RULES.get(granularity, "D")
    numeric_cols = [c for c in df.columns if c != "date"]

    result: Dict[str, Any] = {}
    for col in numeric_cols:
        s = pd.to_numeric(df[col], errors="coerce")
        if col in ("lower",) or (col.startswith("q") and len(col) <= 4):
            # lower-bound quantiles: use min over the window
            if col == "lower" or _is_lower_quantile(col):
                result[col] = s.resample(rule).min()
            else:
                result[col] = s.resample(rule).max()
        elif col == "upper":
            result[col] = s.resample(rule).max()
        elif col == "value":
            if agg == "mean":
                result[col] = s.resample(rule).mean()
            else:
                result[col] = s.resample(rule).sum()
        else:
            if agg == "mean":
                result[col] = s.resample(rule).mean()
            else:
                result[col] = s.resample(rule).sum()

    if "value" not in result:
        return points

    ref_index = result["value"].dropna().index
    out = []
    for ts in ref_index:
        entry: Dict[str, Any] = {"date": ts.strftime("%Y-%m-%d")}
        for col, series in result.items():
            entry[col] = _safe_float(series.get(ts))
        if entry.get("value") is not None:
            out.append(entry)
    return out


def _is_lower_quantile(col: str) -> bool:
    """Return True if the quantile column represents a lower bound (q5..q45)."""
    try:
        n = int(col[1:])
        return n < 50
    except (ValueError, IndexError):
        return False


def compute_stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {}
    n = len(values)
    mean_v = sum(values) / n
    std_v = _stats.stdev(values) if n > 1 else 0.0
    sorted_v = sorted(values)
    median_v = sorted_v[n // 2] if n % 2 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    return {
        "mean":   round(mean_v, 4),
        "std":    round(std_v, 4),
        "min":    min(values),
        "max":    max(values),
        "median": median_v,
        "n":      n,
    }
