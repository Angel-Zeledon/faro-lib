"""Dataset analysis (columns, temporal, seasonality, sku stats) as plain Python."""
from __future__ import annotations

from typing import Optional

import pandas as pd

from backend.dataframes.io import _csv_sep


def _read(path: str) -> pd.DataFrame:
    if str(path).endswith(".csv"):
        return pd.read_csv(path, sep=_csv_sep(path), encoding="utf-8-sig")
    return pd.read_excel(path)


def read_error_message(exc: Exception) -> Optional[str]:
    """Classify a pandas read failure so a caller can keep its own 422 copy
    without importing ``pandas.errors``. Returns ``"empty"`` for an empty file,
    ``"parser"`` for a malformed one, or ``None`` for anything else."""
    if isinstance(exc, pd.errors.EmptyDataError):
        return "empty"
    if isinstance(exc, pd.errors.ParserError):
        return "parser"
    return None


def accuracy_actuals(path: str, date_col: str, target_col: str) -> list[dict]:
    """[{date, value}] actuals for accuracy comparison; value is None when the
    target cell is NaN, date normalized to 'YYYY-MM-DD'."""
    df = _read(path)
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
    out = []
    for _, row in df.iterrows():
        val = row[target_col]
        out.append({"date": row[date_col],
                    "value": float(val) if pd.notna(val) else None})
    return out


def analyze_dataset(path: str, dt_col: Optional[str] = None,
                    target_col: Optional[str] = None, group_col: Optional[str] = None,
                    freq_label: str = "unknown") -> dict:
    """Rich dataset analysis (columns, temporal, seasonality, sku stats). The
    pandas body was moved verbatim from configuration.py's analysis endpoint;
    the resolved column roles and the frequency label are passed in by the
    caller (they come from session config, not the file)."""
    import numpy as np
    df = _read(path)

    # Column-level analysis
    col_analysis = []
    for col in df.columns:
        s_col = df[col]
        role = "numeric" if pd.api.types.is_numeric_dtype(s_col) else "categorical"
        col_analysis.append({
            "name":     col,
            "dtype":    str(s_col.dtype),
            "role":     role,
            "null_pct": round(float(s_col.isna().mean() * 100), 1),
            "n_unique": int(s_col.nunique()),
        })

    n_duplicates = int(df.duplicated().sum())
    memory_mb    = round(float(df.memory_usage(deep=True).sum() / 1024 / 1024), 2)

    # Temporal analysis
    temporal: dict = {}
    if dt_col and dt_col in df.columns:
        try:
            dates = pd.to_datetime(df[dt_col], errors="coerce").dropna().sort_values()
            diffs = dates.diff().dropna()
            if len(diffs) > 0:
                med_td     = diffs.median()
                freq_days  = int(round(med_td.total_seconds() / 86400))
                gap_thresh = med_td * 2.5
                gap_count  = int((diffs > gap_thresh).sum())
            else:
                freq_days = gap_count = 0
            temporal = {
                "date_min":   str(dates.min().date()),
                "date_max":   str(dates.max().date()),
                "n_periods":  int(dates.nunique()),
                "freq_days":  freq_days,
                "gap_count":  gap_count,
                "freq_label": freq_label,
            }
        except Exception:
            pass

    # Aggregate seasonality on target
    seasonality_info: Optional[dict] = None
    if target_col and target_col in df.columns and dt_col and dt_col in df.columns:
        try:
            ts   = df.groupby(dt_col)[target_col].sum().sort_index()
            vals = ts.values.astype(float)
            vals = vals[~np.isnan(vals)]
            if len(vals) >= 12:
                from forecasting_core.analysis.seasonality import detect_seasonality
                res = detect_seasonality(vals)
                dp = res.get("dominant_period")
                seasonality_info = {
                    "dominant_period":   int(dp) if dp is not None else None,
                    "top_periods":       [int(p) for p in res.get("top_periods", [])],
                    "seasonal_strength": round(float(res.get("seasonal_strength", 0)), 3),
                    "classification":    str(res.get("classification", "none")),
                }
        except Exception:
            pass

    # SKU-level stats
    sku_stats: dict = {}
    if group_col and group_col in df.columns and target_col and target_col in df.columns:
        try:
            grp            = df.groupby(group_col)
            sizes          = grp.size()
            zero_pcts      = grp[target_col].apply(lambda s: float((s == 0).mean()) * 100)
            sku_stats = {
                "n_skus":             int(df[group_col].nunique()),
                "intermittent_count": int((zero_pcts > 30).sum()),
                "short_series_count": int((sizes < 20).sum()),
                "avg_zero_pct":       round(float(zero_pcts.mean()), 1),
                "min_series_len":     int(sizes.min()),
                "max_series_len":     int(sizes.max()),
            }
        except Exception:
            pass

    return {
        "columns":      col_analysis,
        "n_rows":       int(len(df)),
        "n_cols":       int(len(df.columns)),
        "n_duplicates": n_duplicates,
        "memory_mb":    memory_mb,
        "temporal":     temporal,
        "seasonality":  seasonality_info,
        "sku_stats":    sku_stats,
    }
