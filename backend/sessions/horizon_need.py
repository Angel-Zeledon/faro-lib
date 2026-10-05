"""
The forecast horizon a tenant's buying actually needs.

A buyer who orders from a supplier with a 90-day lead time and a 30-day review
period places an order today that has to last 120 days. A forecast that stops at
90 daily steps (`family_service.GENEROUS_REACH`) leaves the last 30 days of that
decision with no forecast at all: the planner pads missing steps with zero
demand (`optimizer_service._bucketed`), so the order is sized against a future
that was never forecast.

This module derives that need from data the tenant already declared, and only
that:

    need_days = ceil((lead time + review period) * (1 + SAFETY_MARGIN))

taken as the MAXIMUM over the tenant's SKUs, because one forecast run serves
every SKU. It only ever RAISES a horizon (see `family_service.plan_family`); a
larger configured horizon is never lowered.

What counts as "declared". A lead time whose source is the system default (the
untouched 15 days the schema invents, see `backend.inventory.defaults`) is an
assumption, not a requirement, and is ignored — otherwise every tenant that has
never configured a supplier would have its explicit horizon raised on the
strength of a number nobody chose. A review period has one source only (the
supplier's own setting; 0 means "none declared") so any positive value counts.
With no declared lead time and no declared review period the result is None and
nothing changes: that is the "no new data, same behaviour" guarantee.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from backend.inventory.defaults import is_assumed

log = logging.getLogger(__name__)

# Headroom over lead time + review period, as a fraction. A supplier that is
# late by 15% must not push the end of the protection interval off the forecast.
SAFETY_MARGIN = 0.15

# The most the engine is asked to forecast: the API's own `user_horizon_days`
# ceiling (ge=1, le=365). The engine has no hard limit of its own (verified:
# every model forecasts `horizon` steps, and the global model's stacked training
# frame is thinned by a stride past `MAX_STACKED_ROWS`), but cost grows with the
# number of steps, and past a year a forecast stops being a plan. A need beyond
# this is reported as capped, never silently honoured as if it were met.
HORIZON_CEILING_DAYS = 365


def derive_need(planning: dict[str, dict]) -> Optional[dict]:
    """The binding supplier/SKU of the tenant, or None when nothing is declared.

    `planning` is `optimizer_service.resolve_planning_inputs` output:
    `{sku: {"supplier", "lead_time_days", "lead_time_source",
    "review_period_days", ...}}` — the same numbers the semáforo plans on, so
    this never contradicts what `/hoy` shows.

    Returns `{"need_days", "required_days", "capped", "lead_time_days",
    "review_period_days", "supplier", "sku"}`; `required_days` is the raw
    lead time + review period of the binding row, `need_days` adds the margin
    and is clamped to `HORIZON_CEILING_DAYS`.
    """
    best: Optional[dict] = None
    for sku, p in (planning or {}).items():
        try:
            review = max(0.0, float(p.get("review_period_days") or 0.0))
            lead = 0.0 if is_assumed(p.get("lead_time_source")) else max(
                0.0, float(p.get("lead_time_days") or 0.0))
        except (TypeError, ValueError):
            continue
        required = lead + review
        if required <= 0:
            continue
        # Deterministic on ties: the lexically first supplier/SKU wins, so the
        # sentence the run prints does not change between two identical runs.
        who = (str(p.get("supplier") or ""), str(sku))
        if best is None or required > best["required_days"] or (
                required == best["required_days"]
                and who < (best["supplier"] or "", best["sku"])):
            best = {
                "required_days": required,
                "lead_time_days": lead,
                "review_period_days": review,
                "supplier": p.get("supplier") or None,
                "sku": str(sku),
            }
    if best is None:
        return None
    raw = math.ceil(best["required_days"] * (1.0 + SAFETY_MARGIN))
    best["capped"] = raw > HORIZON_CEILING_DAYS
    best["need_days"] = min(raw, HORIZON_CEILING_DAYS)
    best["required_days"] = int(math.ceil(best["required_days"]))
    best["lead_time_days"] = int(math.ceil(best["lead_time_days"]))
    best["review_period_days"] = int(math.ceil(best["review_period_days"]))
    return best


def tenant_need(tenant_id: str) -> Optional[dict]:
    """`derive_need` over the tenant's stock rows. DB reads only."""
    from backend.inventory import service as inv_svc
    from backend.inventory.optimizer_service import resolve_planning_inputs

    rows = inv_svc.list_stock(tenant_id)
    if not rows:
        return None
    return derive_need(resolve_planning_inputs(tenant_id, rows))
