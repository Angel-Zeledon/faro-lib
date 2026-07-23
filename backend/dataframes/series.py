"""Series extraction / date filtering over files, returning plain Python.

A few functions here take or return a pandas DataFrame (``filter_dataframe_by_date``)
so a ForecastingCore component that requires a DataFrame (TimeSeriesAnalyzer) can
be fed from a consumer that never imports pandas itself. Everything else returns
plain Python (``list[dict]``).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


def historical_series(path: str, date_col: str, target_col: str,
                      sku_col: Optional[str], sku: Optional[str] = None) -> list[dict]:
    """[{date: 'YYYY-MM-DD', value: float|None}] from a file, optionally for one
    SKU, sorted ascending by date."""
    df = pd.read_csv(path) if str(path).endswith(".csv") else pd.read_excel(path)
    if sku is not None and sku_col and sku_col in df.columns:
        df = df[df[sku_col].astype(str) == str(sku)]
    df = df.sort_values(date_col)
    out: list[dict] = []
    for _, row in df.iterrows():
        val = row[target_col]
        out.append({
            "date": str(row[date_col])[:10],
            "value": float(val) if pd.notna(val) else None,
        })
    return out


def filter_rows_by_date(rows: list[dict], date_col: str,
                        date_from: Optional[str], date_to: Optional[str]) -> list[dict]:
    """Inclusive date-range filter on plain rows; unparseable dates are dropped."""
    if not rows:
        return []
    lo = pd.Timestamp(date_from) if date_from else None
    hi = pd.Timestamp(date_to) if date_to else None
    out: list[dict] = []
    for r in rows:
        ts = pd.to_datetime(r.get(date_col), errors="coerce")
        if pd.isna(ts):
            continue
        if lo is not None and ts < lo:
            continue
        if hi is not None and ts > hi:
            continue
        out.append(r)
    return out


def normalize_date_column(rows: list[dict], date_col: str) -> list[dict]:
    """Coerce ``date_col`` in each row to 'YYYY-MM-DD', dropping rows whose date
    cannot be parsed (mirrors ``to_datetime(errors='coerce').dt.strftime`` +
    ``dropna``). Other columns are preserved untouched."""
    out: list[dict] = []
    for r in rows:
        ts = pd.to_datetime(r.get(date_col), errors="coerce")
        if pd.isna(ts):
            continue
        new = dict(r)
        new[date_col] = ts.strftime("%Y-%m-%d")
        out.append(new)
    return out


def filter_dataframe_by_date(df, date_col: str, date_from: Optional[str],
                             date_to: Optional[str]):
    """ForecastingCore bridge: coerce ``date_col`` to datetime and apply an
    inclusive date-range filter, returning a DataFrame for TimeSeriesAnalyzer.
    Always converts the date column even when no range is given (as the callers
    did inline before this refactor)."""
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    if date_from:
        df = df[df[date_col] >= pd.Timestamp(date_from)]
    if date_to:
        df = df[df[date_col] <= pd.Timestamp(date_to)]
    return df


def dataframe_series(df, date_col: str, target_col: str,
                     sku_col: Optional[str], sku_id: Optional[str]) -> list[dict]:
    """[{date, value}] from an in-memory DataFrame, filtered to one SKU when
    ``sku_col`` is given, sorted ascending by date; NaN value -> None."""
    if sku_col and sku_col in df.columns:
        sub = df[df[sku_col].astype(str) == sku_id].sort_values(date_col)
    else:
        sub = df.sort_values(date_col)
    return [
        {"date": str(d)[:10], "value": float(v) if pd.notna(v) else None}
        for d, v in zip(sub[date_col], sub[target_col])
    ]


def detect_outliers(series: list[dict]) -> list[dict]:
    """Tukey IQR-fence outliers (Q1 - 1.5*IQR .. Q3 + 1.5*IQR) over a
    [{date, value}] series, with z-score against the series mean/std."""
    import numpy as np

    vals = [pt["value"] for pt in series if pt["value"] is not None]
    outliers: list[dict] = []
    if len(vals) >= 4:
        arr = np.array(vals, dtype=float)
        q1, q3 = float(np.percentile(arr, 25)), float(np.percentile(arr, 75))
        iqr = q3 - q1
        lo = q1 - 1.5 * iqr
        hi = q3 + 1.5 * iqr
        mean = float(arr.mean())
        std = float(arr.std()) if float(arr.std()) > 0 else 1.0
        for pt in series:
            v = pt["value"]
            if v is None:
                continue
            fv = float(v)
            if fv < lo or fv > hi:
                outliers.append({
                    "date": pt["date"],
                    "value": fv,
                    "z_score": round((fv - mean) / std, 2),
                    "lower_bound": round(lo, 4),
                    "upper_bound": round(hi, 4),
                    "reason": (
                        f"Exceeds upper fence {hi:.2f} (Q3 + 1.5×IQR)"
                        if fv > hi
                        else f"Below lower fence {lo:.2f} (Q1 − 1.5×IQR)"
                    ),
                })
    return outliers
