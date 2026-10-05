"""
Manual spike exclusions: a person's "this period was a one-off" applied as a
data-cleaning step.

An analyst who knows a past period was not ordinary demand (a one-time bulk
order, a promotion that will not repeat, a catch-up after a stockout) marks it,
and the next training treats those observations as NOT part of the baseline: each
is replaced by the typical value of the series around it. The original upload is
never touched; this runs on the in-memory copy the engine trains on.

The estimate is the median of the nearest observations that were NOT excluded
(up to `window` on each side, in date order), falling back to the median of every
non-excluded observation of the series. A median, not a mean, so a second spike
next to the first cannot drag the estimate up. When a series has no usable
observation left at all, nothing is replaced and the report says so: inventing a
number from nothing would be worse than leaving the spike.

Pure: a DataFrame in, a DataFrame and a report out. The caller (the training
runner) owns where the exclusions come from and where the report is recorded.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

DEFAULT_WINDOW = 8

STATUS_APPLIED = "applied"
STATUS_NO_MATCH = "no_match"        # no row of that product inside the period
STATUS_NO_BASELINE = "no_baseline"  # rows matched but nothing to estimate from


def apply_spike_exclusions(
    df: pd.DataFrame,
    date_col: str,
    target_col: str,
    group_cols: Sequence[str],
    exclusions: Sequence[dict],
    window: int = DEFAULT_WINDOW,
) -> tuple[pd.DataFrame, list[dict]]:
    """Replace the excluded observations by the typical neighbouring value.

    `exclusions` are ``{"id", "sku", "start_date", "end_date"}`` (inclusive
    dates). `sku` names the PRIMARY group column; every series that shares it
    (every warehouse of that product) is treated, each against its own
    neighbours. Returns ``(df, report)`` where `report` has one entry per
    exclusion: ``{id, sku, status, points_treated, original_total,
    replacement_total}``. With no exclusions the very same frame comes back.
    """
    if not exclusions:
        return df, []
    report = [
        {"id": e.get("id"), "sku": str(e.get("sku")), "status": STATUS_NO_MATCH,
         "points_treated": 0, "original_total": 0.0, "replacement_total": 0.0}
        for e in exclusions
    ]
    group_cols = [c for c in (group_cols or []) if c]
    if not group_cols or df is None or df.empty or target_col not in df.columns:
        return df, report

    out = df.copy()
    dates = pd.to_datetime(out[date_col], errors="coerce")
    values = pd.to_numeric(out[target_col], errors="coerce")
    primary = out[group_cols[0]].astype(str).str.strip()

    # Which exclusion claims which row. A row can be claimed by several; it is
    # replaced once and the credit goes to the first, so totals never double.
    claimed_by = np.full(len(out), -1, dtype=int)
    for i, e in enumerate(exclusions):
        lo, hi = pd.Timestamp(e["start_date"]), pd.Timestamp(e["end_date"])
        hit = (primary == str(e["sku"]).strip()) & (dates >= lo) & (dates <= hi)
        hit_arr = hit.to_numpy() & (claimed_by < 0)
        # Matched rows with an empty value are still "matched": there is simply
        # nothing to treat, which the report counts as no points.
        claimed_by[hit_arr] = i
    excluded = claimed_by >= 0
    if not excluded.any():
        return df, report

    new_values = values.to_numpy(dtype=float).copy()
    treated_rows: list[int] = []
    date_arr = dates.to_numpy()
    series_codes = out.groupby(group_cols, sort=False, dropna=False).ngroup().to_numpy()

    for code in np.unique(series_codes[excluded]):
        rows = np.flatnonzero(series_codes == code)
        order = rows[np.argsort(date_arr[rows], kind="stable")]
        ex_rows = [r for r in order if excluded[r] and not np.isnan(new_values[r])]
        clean = [r for r in order if not excluded[r] and not np.isnan(new_values[r])]
        clean_dates = date_arr[clean] if clean else np.array([], dtype="datetime64[ns]")
        clean_vals = new_values[clean] if clean else np.array([], dtype=float)
        series_median = float(np.median(clean_vals)) if len(clean_vals) else None
        for r in ex_rows:
            if series_median is None:
                report[claimed_by[r]]["_no_baseline"] = True
                continue
            pos = int(np.searchsorted(clean_dates, date_arr[r]))
            near = clean_vals[max(0, pos - window):pos + window]
            estimate = float(np.median(near)) if len(near) else series_median
            entry = report[claimed_by[r]]
            entry["points_treated"] += 1
            entry["original_total"] += float(new_values[r])
            entry["replacement_total"] += estimate
            new_values[r] = estimate
            treated_rows.append(int(r))

    # An exclusion that matched rows but treated none (all empty, or no baseline)
    # must not read as "applied".
    for i, entry in enumerate(report):
        if entry["points_treated"] > 0:
            entry["status"] = STATUS_APPLIED
        elif entry.pop("_no_baseline", False):
            entry["status"] = STATUS_NO_BASELINE
        else:
            entry["status"] = STATUS_NO_MATCH
        entry["original_total"] = round(entry["original_total"], 6)
        entry["replacement_total"] = round(entry["replacement_total"], 6)

    if any(e["points_treated"] for e in report):
        out[target_col] = new_values if not pd.api.types.is_integer_dtype(out[target_col]) \
            else new_values
    else:
        return df, report
    return out, report


def summarize_by_sku(report: Sequence[dict]) -> list[dict]:
    """One entry per product: points treated and the totals, for the run
    lineage. Products whose exclusions treated nothing are listed too, with
    zero points, so a mark that matched no data never disappears silently."""
    by_sku: dict[str, dict] = {}
    for e in report:
        s = by_sku.setdefault(e["sku"], {
            "sku": e["sku"], "points_treated": 0, "original_total": 0.0,
            "replacement_total": 0.0, "exclusions": 0, "unmatched": 0,
        })
        s["exclusions"] += 1
        s["points_treated"] += int(e["points_treated"])
        s["original_total"] += float(e["original_total"])
        s["replacement_total"] += float(e["replacement_total"])
        if e["status"] != STATUS_APPLIED:
            s["unmatched"] += 1
    return list(by_sku.values())
