"""The one boundary function that takes a DataFrame in (ForecastingCore's)."""
from __future__ import annotations

from typing import Optional

import pandas as pd


def last_row_per_group(df, group_col: Optional[str], date_col: str,
                       columns: list[str]) -> list[tuple[str, dict]]:
    """Latest row per group, projected to `columns` (NaN cells dropped), as
    plain (sku, dict) pairs. No flooring/typing — the caller owns that."""
    if df is None or df.empty:
        return []
    present = [c for c in df.columns if c in columns]
    if not present:
        return []
    work = df.copy()
    if date_col in work.columns:
        work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
        work = work.sort_values(date_col)
    groups = (work.groupby(group_col) if group_col and group_col in work.columns
              else [("__all__", work)])
    out: list[tuple[str, dict]] = []
    for sku, g in groups:
        last = g.iloc[-1]
        data: dict = {}
        for col in present:
            val = last[col]
            if pd.isna(val):
                continue
            data[col] = val
        out.append((str(sku), data))
    return out
