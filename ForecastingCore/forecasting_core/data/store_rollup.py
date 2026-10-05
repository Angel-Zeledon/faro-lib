"""Sum a multi-store sales history into one series per SKU before training.

Why this exists
---------------
The engine forecasts ONE series per value of the primary group key (the SKU).
Trainer, predictor, statistical models and the runner all key their output by
the bare SKU; nothing in the training path keys by `series_key(sku, store)`.
So a sales file with several stores per SKU, trained with
`group_keys=[sku, store]`, did not produce per-store forecasts — it produced a
wrong SKU forecast and reported success:

- tree models build features per (SKU, store) but the predictor writes every
  store under the same SKU key, so the LAST store wins (measured: 1.0/day for a
  SKU that sells 16/day across three stores);
- statistical models receive only the SKU column and fit on the stores' rows
  interleaved on the same dates, i.e. the per-row average, not the total;
- the history stored next to the forecast is keyed `sku│store` while the
  forecast is keyed `sku`, so it came back empty.

Until per-store forecasting is built for real (keyed end to end, with its own
champion per series), the safe reading is the one single-store tenants already
get: forecast each SKU on its TOTAL demand and let the backend's share mode
split it across warehouses. This module does that sum, and says it did.

Pure: pandas in, pandas out, no I/O. The original upload is never touched; the
caller passes its working copy.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

import numpy as np
import pandas as pd

# How each canonical field aggregates across stores. A field not listed here
# falls to the generic rule (numeric: mean, anything else: first value).
_SUM_FIELDS = ("demand", "inventory")
# Prices are per unit: the network's price on a day is the demand-weighted
# mean, so a store that sold 100 units at 10 outweighs one that sold 1 at 20.
_WEIGHTED_MEAN_FIELDS = ("price", "cost", "regular_price", "promo_price", "discount")
# A promotion running in ANY store is a promotion in the total.
_FLAG_FIELDS = ("promo",)
# Lead time: the longest one. Guessing long is the cheap mistake (see
# canonical.DEFAULT_LEAD_TIME_DAYS); a short guess shrinks the reorder point.
_MAX_FIELDS = ("lead_time",)

STORE_ROLLUP_CODE = "PREP_STORES_SUMMED"


@dataclass
class StoreRollup:
    """What the rollup did, for the run's findings and its lineage."""

    applied: bool = False
    n_stores: int = 0
    n_skus: int = 0
    n_skus_multi_store: int = 0
    rows_in: int = 0
    rows_out: int = 0
    demand_total: float = 0.0
    # Target cells that were empty in one store on a date another store
    # reported; they count as 0 in that date's total.
    n_missing_target_cells: int = 0
    store_columns: list = field(default_factory=list)
    stores: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def skus_with_several_stores(df: pd.DataFrame, sku_col: str, store_col: str) -> int:
    """How many SKUs appear under more than one distinct (non-empty) store."""
    if (df is None or not sku_col or not store_col
            or sku_col not in df.columns or store_col not in df.columns or df.empty):
        return 0
    per_sku = df.groupby(sku_col, dropna=False)[store_col].nunique(dropna=True)
    return int((per_sku > 1).sum())


def _unique(cols: Iterable[Optional[str]], present: Iterable[str]) -> list:
    present = set(present)
    out: list = []
    for c in cols:
        if c and c in present and c not in out:
            out.append(c)
    return out


def rollup_stores(
    df: pd.DataFrame,
    *,
    date_col: str,
    sku_col: str,
    store_cols: Iterable[str],
    sum_cols: Iterable[str],
    weight_col: Optional[str] = None,
    weighted_mean_cols: Iterable[str] = (),
    flag_cols: Iterable[str] = (),
    max_cols: Iterable[str] = (),
) -> "tuple[pd.DataFrame, StoreRollup]":
    """One row per (SKU, date): `sum_cols` summed across stores.

    The FIRST entry of `sum_cols` is the demand (the target): it is the one the
    run's findings report and the invariant below protects first.

    The first entry of `store_cols` decides whether anything happens: when no
    SKU appears under two or more stores, `df` is returned unchanged (the same
    object) and `applied` is False. Otherwise every column in `store_cols` is
    dropped — the result has no store dimension, and must not pretend to.

    Column rules:
      - `sum_cols`: summed; a (SKU, date) whose cells are ALL empty stays empty.
      - `weighted_mean_cols`: mean weighted by `weight_col` (the demand),
        falling back to the plain mean when no store sold anything that day.
      - `flag_cols`: logical OR for bool/numeric flags; first value otherwise.
      - `max_cols`: maximum.
      - every other column: numeric → mean, anything else → first value.

    Raises RuntimeError if the summed demand does not equal the input's: a
    rollup that loses or invents units must fail the run, not feed a purchase.
    """
    store_cols = [c for c in store_cols if c]
    info = StoreRollup(rows_in=int(len(df)) if df is not None else 0)
    if df is None or not store_cols or store_cols[0] not in df.columns:
        info.rows_out = info.rows_in
        return df, info
    primary_store = store_cols[0]
    if sku_col not in df.columns or date_col not in df.columns:
        info.rows_out = info.rows_in
        return df, info

    n_multi = skus_with_several_stores(df, sku_col, primary_store)
    if n_multi == 0:
        info.rows_out = info.rows_in
        return df, info

    cols = list(df.columns)
    store_cols = _unique(store_cols, cols)
    keys = [sku_col, date_col]
    sum_cols = [c for c in _unique(sum_cols, cols) if c not in keys + store_cols]
    taken = set(keys) | set(store_cols) | set(sum_cols)
    wm_cols = [c for c in _unique(weighted_mean_cols, cols) if c not in taken]
    taken |= set(wm_cols)
    flag_cols = [c for c in _unique(flag_cols, cols) if c not in taken]
    taken |= set(flag_cols)
    max_cols = [c for c in _unique(max_cols, cols) if c not in taken]
    taken |= set(max_cols)
    rest = [c for c in cols if c not in taken]

    work = df.copy()
    for c in sum_cols + wm_cols + max_cols:
        work[c] = pd.to_numeric(work[c], errors="coerce")

    grouped = work.groupby(keys, dropna=False, sort=False)
    parts: dict = {}

    for c in sum_cols:
        parts[c] = grouped[c].sum(min_count=1)

    if wm_cols:
        if weight_col and weight_col in work.columns:
            w = pd.to_numeric(work[weight_col], errors="coerce").clip(lower=0)
        else:
            w = pd.Series(np.nan, index=work.index)
        for c in wm_cols:
            v = work[c]
            ok = v.notna() & w.notna()
            num = (v * w).where(ok)
            den = w.where(ok)
            tmp = pd.DataFrame({k: work[k] for k in keys})
            tmp["_num"], tmp["_den"] = num, den
            g = tmp.groupby(keys, dropna=False, sort=False)
            s_num = g["_num"].sum(min_count=1)
            s_den = g["_den"].sum(min_count=1)
            weighted = s_num / s_den.where(s_den > 0)
            plain = grouped[c].mean()
            parts[c] = weighted.where(weighted.notna(), plain)

    for c in flag_cols:
        col = work[c]
        if pd.api.types.is_bool_dtype(col) or pd.api.types.is_numeric_dtype(col):
            parts[c] = grouped[c].max()
        else:
            parts[c] = grouped[c].first()

    for c in max_cols:
        parts[c] = grouped[c].max()

    for c in rest:
        if pd.api.types.is_numeric_dtype(work[c]) and not pd.api.types.is_bool_dtype(work[c]):
            parts[c] = grouped[c].mean()
        else:
            parts[c] = grouped[c].first()

    out = pd.DataFrame(parts).reset_index()
    out = out[[c for c in cols if c in out.columns]]
    try:
        out = out.sort_values(keys, kind="stable").reset_index(drop=True)
    except TypeError:  # mixed-type SKU labels: order is cosmetic, keep it
        out = out.reset_index(drop=True)

    # Invariant: units in == units out, for the demand column(s).
    for c in sum_cols:
        before = float(np.nansum(work[c].to_numpy(dtype=float)))
        after = float(np.nansum(out[c].to_numpy(dtype=float)))
        if not np.isclose(before, after, rtol=1e-9, atol=1e-6):
            raise RuntimeError(
                f"store rollup changed the total of '{c}': {before} -> {after}"
            )

    target = sum_cols[0] if sum_cols else None
    if target is not None:
        tgt = work[target]
        # Empty cells on a (SKU, date) where another store DID report: those
        # count as 0 in the total, so the reader is told how many there were.
        has_value = tgt.notna().groupby([work[k] for k in keys], dropna=False).transform("any")
        info.n_missing_target_cells = int((tgt.isna() & has_value).sum())
        info.demand_total = round(float(np.nansum(out[target].to_numpy(dtype=float))), 6)

    stores = df[primary_store].dropna().astype(str).unique().tolist()
    info.applied = True
    info.n_stores = len(stores)
    info.stores = sorted(stores)[:50]
    info.n_skus = int(df[sku_col].nunique(dropna=False))
    info.n_skus_multi_store = n_multi
    info.rows_out = int(len(out))
    info.store_columns = store_cols
    return out, info


def rollup_stores_canonical(
    df: pd.DataFrame,
    *,
    date_col: str,
    target_col: str,
    sku_col: str,
    store_col: str,
    canonical_mapping: Optional[dict] = None,
) -> "tuple[pd.DataFrame, StoreRollup]":
    """`rollup_stores` with the column roles read from a canonical mapping.

    Both the user's mapped column and its canonical alias (added by
    `apply_canonical_defaults`) get the field's rule, so the two copies can
    never disagree after the sum.
    """
    cmap = dict(canonical_mapping or {})

    def cols_for(fields: Iterable[str]) -> list:
        out: list = []
        for f in fields:
            out.extend([cmap.get(f), f])
        return out

    return rollup_stores(
        df,
        date_col=date_col,
        sku_col=sku_col,
        store_cols=[store_col, cmap.get("store"), "store"],
        sum_cols=[target_col] + cols_for(_SUM_FIELDS),
        weight_col=target_col,
        weighted_mean_cols=cols_for(_WEIGHTED_MEAN_FIELDS),
        flag_cols=cols_for(_FLAG_FIELDS),
        max_cols=cols_for(_MAX_FIELDS),
    )
