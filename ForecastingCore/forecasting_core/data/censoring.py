"""
Censored demand recovery — what the shelf recorded is not what the market wanted.

A sales file records what was SOLD. On a day the SKU was out of stock, what was
sold is a lower bound on what was demanded, and on a day it was out of stock
from open to close, the recorded zero means "we could not sell", not "nobody
wanted it". Trained on that, a model learns the stockout as a demand collapse
and forecasts the SKU down — so it gets reordered less, stocks out again, and
the次 forecast is lower still. The error feeds itself, and every symptom of it
(a fast-mover that keeps sliding down the semáforo) looks like normal decline.

This module marks the affected buckets and lifts them back to what demand
plausibly was.

What it needs, and what it refuses to do without
------------------------------------------------
Historical inventory. Nothing else in the file can distinguish "sold zero" from
"had none to sell". The canonical schema broadcasts `inventory = 0` into every
session where the user did NOT map an inventory column, so a naive `inventory
== 0` test would flag the entire dataset as censored and inflate every forecast.
The recovery therefore runs only when the caller passes a column the user
actually mapped, and `recover_censored_demand` is a no-op otherwise. A silent
no-op is the right failure here: inventing demand from evidence we do not have
would be worse than the bias we are correcting.

Method
------
For each flagged bucket, demand is estimated from the series' own uncensored
behaviour — a trailing level (median of recent uncensored buckets) times the
series' day-of-week shape — and the observation is raised to that estimate. It
is never lowered: the units that DID sell are a fact, and the estimate is only
evidence about the units that could not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Trailing window (in buckets) used to estimate the level a censored bucket
# would have had. Long enough to be stable, short enough to track a real trend.
LEVEL_WINDOW = 28

# A series needs at least this many uncensored buckets before its own history
# can say anything about what a censored bucket should have been. Below it, the
# bucket is flagged for transparency but left untouched.
#
# Two weeks, because that is where the method's own parts become estimable: the
# day-of-week shape needs two observations per weekday to exist at all, so on a
# shorter series the estimate silently degenerates to a bare median — a number
# that would still be produced, and still be published as recovered demand.
MIN_UNCENSORED = 14

# Cap on how far a single observation may be lifted, as a multiple of the
# estimated level. Without it, one mis-flagged bucket in a spiky series can be
# raised to an outlier that then drives the whole forecast.
MAX_LIFT_FACTOR = 3.0

CENSORED_FLAG = "demand_censored"


@dataclass
class CensoringReport:
    """What was found and what was changed — never just logged."""
    inventory_column: Optional[str] = None
    n_rows: int = 0
    n_flagged: int = 0
    n_recovered: int = 0
    units_recovered: float = 0.0
    skus_affected: List[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "action": "censored_demand_recovered",
            # A plain-English sentence, not decoration. Consumers fall back to
            # `description` when they have no localized copy for the action, and
            # an empty string there renders as a blank bullet — a UI claiming
            # something was changed and then refusing to say what. That is the
            # worst possible outcome for a correction that REWRITES the user's
            # own sales figures.
            "description": (
                f"Recovered {self.n_recovered} stockout-censored observation(s) "
                f"across {len(self.skus_affected)} SKU(s), raising demand by "
                f"{self.units_recovered:.1f} unit(s) in total."
                if self.n_recovered else
                f"Flagged {self.n_flagged} stockout observation(s); none were "
                f"altered."
            ),
            "inventory_column": self.inventory_column,
            "n_rows": self.n_rows,
            "n_flagged": self.n_flagged,
            "n_recovered": self.n_recovered,
            "units_recovered": round(float(self.units_recovered), 2),
            "skus_affected": self.skus_affected[:20],
            "n_skus_affected": len(self.skus_affected),
            "skipped_reason": self.skipped_reason,
        }

    @property
    def changed_anything(self) -> bool:
        return self.n_recovered > 0


def _dow_shape(dates: pd.Series, values: pd.Series) -> Dict[int, float]:
    """
    Day-of-week multipliers from uncensored buckets only.

    Uses the seasonal SHAPE of the whole series rather than a trailing window:
    a weekly profile is stable and needs the whole history to be estimated at
    all, while the LEVEL — the part that actually drifts — is estimated from the
    trailing window instead.
    """
    if values.empty:
        return {}
    overall = float(np.median(values))
    if overall <= 0:
        return {}
    shape: Dict[int, float] = {}
    for dow, group in values.groupby(dates.dt.dayofweek):
        if len(group) >= 2:
            shape[int(dow)] = float(np.median(group)) / overall
    return shape


def recover_censored_demand(
    df: pd.DataFrame,
    date_col: str,
    target_col: str,
    group_col: Optional[str],
    inventory_col: Optional[str],
    *,
    level_window: int = LEVEL_WINDOW,
    min_uncensored: int = MIN_UNCENSORED,
) -> tuple[pd.DataFrame, CensoringReport]:
    """
    Lift stockout-truncated observations back to plausible demand.

    Args:
        inventory_col: Column holding end-of-bucket stock. MUST be a column the
                       user actually mapped — see the module docstring for why
                       passing the canonical default is a bug, not a shortcut.

    Returns:
        (frame, report). The frame gains a `demand_censored` flag column so the
        rest of the pipeline (and the UI) can see which observations are
        estimates rather than measurements.
    """
    report = CensoringReport(inventory_column=inventory_col, n_rows=len(df))

    if not inventory_col:
        report.skipped_reason = "no_inventory_column_mapped"
        return df, report
    if inventory_col not in df.columns:
        report.skipped_reason = "inventory_column_missing"
        return df, report
    if target_col not in df.columns or date_col not in df.columns:
        report.skipped_reason = "columns_missing"
        return df, report

    out = df.copy()
    inventory = pd.to_numeric(out[inventory_col], errors="coerce")
    demand = pd.to_numeric(out[target_col], errors="coerce")

    if inventory.notna().sum() == 0:
        report.skipped_reason = "inventory_all_missing"
        return df, report
    # An inventory column that never moves off zero is the canonical default
    # broadcast into an unmapped session, not a warehouse that is permanently
    # empty. Treating it as evidence would flag every row in the dataset.
    if float(np.nanmax(inventory.to_numpy())) <= 0:
        report.skipped_reason = "inventory_never_positive"
        return df, report

    censored = (inventory <= 0) & inventory.notna()
    out[CENSORED_FLAG] = censored.astype(int)
    report.n_flagged = int(censored.sum())
    if report.n_flagged == 0:
        return out, report

    dates = pd.to_datetime(out[date_col])
    groups = (out[group_col].astype(str) if group_col and group_col in out.columns
              else pd.Series(["__all__"] * len(out), index=out.index))

    recovered = demand.copy()
    affected: List[str] = []

    for key, idx in groups.groupby(groups).groups.items():
        idx = pd.Index(idx)
        order = dates.loc[idx].sort_values().index
        series_demand = demand.loc[order]
        series_dates = dates.loc[order]
        series_censored = censored.loc[order]

        clean = series_demand[~series_censored].dropna()
        if len(clean) < min_uncensored:
            # Not enough honest history to say what the missing days held. The
            # rows stay flagged so the user can see them; nothing is invented.
            continue

        shape = _dow_shape(series_dates[~series_censored].loc[clean.index], clean)
        fallback_level = float(np.median(clean))

        # Trailing level: censored buckets are excluded from the window and the
        # window is shifted, so an estimate never uses the bucket it is
        # estimating nor any bucket after it.
        masked = series_demand.where(~series_censored)
        level = (masked.shift(1)
                       .rolling(level_window, min_periods=max(2, min_uncensored // 2))
                       .median())

        changed_here = 0
        for position in order[series_censored.loc[order].to_numpy()]:
            base = level.get(position, np.nan)
            if not np.isfinite(base) or base <= 0:
                base = fallback_level
            factor = shape.get(int(series_dates.loc[position].dayofweek), 1.0)
            estimate = float(base) * float(factor)
            observed = float(series_demand.loc[position] or 0.0)
            # Never below what actually sold, never a runaway above the level.
            lifted = min(max(observed, estimate), float(base) * MAX_LIFT_FACTOR)
            if lifted > observed:
                recovered.loc[position] = lifted
                report.units_recovered += lifted - observed
                changed_here += 1

        if changed_here:
            affected.append(str(key))
            report.n_recovered += changed_here

    out[target_col] = recovered
    report.skus_affected = affected
    if report.n_recovered:
        log.info(
            "Censored demand: lifted %d of %d stockout buckets across %d SKU(s), "
            "+%.1f units",
            report.n_recovered, report.n_flagged, len(affected), report.units_recovered,
        )
    return out, report
