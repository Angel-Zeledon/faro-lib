"""
Active planning setting + resolver (multi-period planning, Phase B).

The tenant has one active planning view: a `period` (daily/weekly/monthly) and a
`horizon` in that period's own unit, stored in `tenants.settings.planning`.
`resolve_active_session` is the single seam every screen and the daily alert loop
read through to pick the session the app should use now: the newest family's
COMPLETED session at the active period, falling back to the legacy latest-completed
session for pre-feature (family-less) tenants.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.db.connection import query, query_one
from backend.sessions.family_service import GENEROUS_REACH
from backend.tenants import service as tenant_svc

log = logging.getLogger(__name__)

# Preserves today's behavior exactly for a tenant with no family / no setting.
DEFAULT_PLANNING = {"period": "daily", "horizon": 14}
# Finest -> coarsest; the order available_periods is returned in.
_PERIOD_ORDER = ["daily", "weekly", "monthly"]


def _newest_family_id(tenant_id: str) -> Optional[str]:
    """The family_id of the most recently created family session, or None when
    the tenant has no family (pre-feature data)."""
    row = query_one(
        """SELECT family_id FROM sessions
           WHERE tenant_id = %s AND family_id IS NOT NULL
           ORDER BY created_at DESC LIMIT 1""",
        (tenant_id,))
    return row["family_id"] if row else None


def _available_periods(tenant_id: str, family_id: Optional[str]) -> list[str]:
    """Distinct granularities of the given family, ordered finest->coarsest.
    Family-less tenants get the daily-only default (today's behavior)."""
    if not family_id:
        return ["daily"]
    rows = query(
        """SELECT DISTINCT granularity FROM sessions
           WHERE tenant_id = %s AND family_id = %s AND granularity IS NOT NULL""",
        (tenant_id, family_id))
    grains = {r["granularity"] for r in rows}
    out = [g for g in _PERIOD_ORDER if g in grains]
    return out or ["daily"]


def _coerce_period(stored_period: Optional[str], available: list[str]) -> str:
    """The period the app actually shows: the stored one when the newest family
    still offers that grain, else the family's finest available grain. Pure, and
    shared by get_planning and resolve_active_session so the period in the top
    bar and the session behind the numbers can never disagree."""
    period = stored_period or DEFAULT_PLANNING["period"]
    return period if period in available else available[0]


def get_planning(tenant_id: str) -> dict:
    """Resolve the active setting against the newest family. period is coerced
    into available_periods and horizon clamped into 1..reach(period)."""
    stored = tenant_svc.get_settings(tenant_id).get("planning") or {}
    available = _available_periods(tenant_id, _newest_family_id(tenant_id))
    period = _coerce_period(stored.get("period"), available)
    max_horizon = GENEROUS_REACH.get(period, 90)
    try:
        horizon = int(stored.get("horizon", DEFAULT_PLANNING["horizon"]))
    except (TypeError, ValueError):
        horizon = DEFAULT_PLANNING["horizon"]
    horizon = max(1, min(horizon, max_horizon))
    return {"period": period, "horizon": horizon,
            "available_periods": available, "max_horizon": max_horizon}


def set_planning(tenant_id: str, period: str, horizon: int) -> dict:
    """Validate + persist the active planning setting. Raises ValueError on an
    unavailable period or an out-of-reach horizon (the API maps it to 422)."""
    available = _available_periods(tenant_id, _newest_family_id(tenant_id))
    if period not in available:
        raise ValueError(
            f"period '{period}' is not available; choose one of {available}")
    max_horizon = GENEROUS_REACH.get(period, 90)
    if not (1 <= int(horizon) <= max_horizon):
        raise ValueError(
            f"horizon must be between 1 and {max_horizon} for period '{period}'")
    tenant_svc.update_settings(
        tenant_id, {"planning": {"period": period, "horizon": int(horizon)}})
    return get_planning(tenant_id)


def resolve_active_session(tenant_id: str) -> Optional[str]:
    """The session the app should use now. Newest family's COMPLETED session at
    the active period — the SAME period get_planning reports, so the resolved
    session's granularity always matches what the UI displays. Falls back to the
    legacy latest-completed session when that is not found (family-less tenant,
    or the active grain hasn't finished training yet). None when the tenant has
    no completed session at all.

    Because the newest family wins, a training run that has just finished
    becomes the active session: this is the seam the Quick Start redirect relies
    on to land the user on the data they just trained."""
    from backend.inventory.service import get_latest_completed_session

    family_id = _newest_family_id(tenant_id)
    if family_id:
        stored = tenant_svc.get_settings(tenant_id).get("planning") or {}
        # Coerce exactly like get_planning: a stored period the newest family
        # does not offer (the tenant was on 'monthly' and then trained a family
        # without a monthly grain) must fall back to that family's finest grain,
        # NOT drop through to the legacy latest-completed session. Dropping
        # through picked whichever session was updated last — routinely a
        # coarser sibling of the same family — so the screens read the numbers
        # of one grain while the top bar announced another.
        period = _coerce_period(
            stored.get("period"), _available_periods(tenant_id, family_id))
        row = query_one(
            """SELECT id AS session_id FROM sessions
               WHERE tenant_id = %s AND family_id = %s AND granularity = %s
                 AND status = 'COMPLETED'
               ORDER BY updated_at DESC LIMIT 1""",
            (tenant_id, family_id, period))
        if row:
            return row["session_id"]
    legacy = get_latest_completed_session(tenant_id)
    return legacy["session_id"] if legacy else None
