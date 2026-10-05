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


def stock_summed_over_stores(df, group_col: Optional[str], store_col: Optional[str],
                             date_col: str, stock_col: Optional[str]) -> dict:
    """Company-wide stock per SKU for a file with several stores per SKU.

    `last_row_per_group` reads the latest ROW per SKU, which in a multi-store
    file is one store's row: the SKU's stock became that one store's stock and
    the purchase covered the whole network's demand against it. The SKU's
    stock is instead each store's LATEST reading, summed — not every historical
    row summed, and not one row.

    Only SKUs sold in two or more stores are returned; a single-store SKU keeps
    the latest-row reading it always had. A store with no stock reading for a
    SKU adds nothing and is counted, so the caller can say so.

    Returns plain Python:
      {"by_sku": {sku: total}, "n_skus": int, "n_stores": int,
       "missing_pairs": int, "n_skus_with_missing": int}
    """
    empty = {"by_sku": {}, "n_skus": 0, "n_stores": 0,
             "missing_pairs": 0, "n_skus_with_missing": 0}
    if (df is None or df.empty or not group_col or not store_col or not stock_col
            or any(c not in df.columns for c in (group_col, store_col, stock_col))):
        return empty
    work = df[[c for c in {group_col, store_col, stock_col, date_col} if c in df.columns]].copy()
    work = work[work[store_col].notna()]
    stores_per_sku = work.groupby(group_col)[store_col].nunique()
    multi = stores_per_sku[stores_per_sku > 1].index
    if len(multi) == 0:
        return empty
    work = work[work[group_col].isin(multi)]
    work[stock_col] = pd.to_numeric(work[stock_col], errors="coerce")
    if date_col in work.columns:
        work["__d"] = pd.to_datetime(work[date_col], errors="coerce")
        work = work.sort_values("__d", kind="stable")
    readings = work[work[stock_col].notna()]
    latest = readings.groupby([group_col, store_col], sort=False)[stock_col].last()
    # A negative count is not a reading (the single-row path drops it too);
    # falling back to an OLDER positive one would invent a stale stock.
    latest = latest[latest >= 0]
    by_sku = latest.groupby(level=0).sum()

    pairs = work[[group_col, store_col]].drop_duplicates()
    have = set(latest.index)
    missing = [(s, st) for s, st in zip(pairs[group_col], pairs[store_col])
               if (s, st) not in have]
    return {
        "by_sku": {str(sku): float(v) for sku, v in by_sku.items()},
        "n_skus": int(len(by_sku)),
        "n_stores": int(work[store_col].nunique()),
        "missing_pairs": len(missing),
        "n_skus_with_missing": len({s for s, _ in missing}),
    }
