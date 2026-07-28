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
from backend.errors import AppError
from backend.sessions.family_service import GENEROUS_REACH
from backend.tenants import service as tenant_svc

log = logging.getLogger(__name__)

# Preserves today's behavior exactly for a tenant with no family / no setting.
DEFAULT_PLANNING = {"period": "daily", "horizon": 14}
# Finest -> coarsest; the order available_periods is returned in.
_PERIOD_ORDER = ["daily", "weekly", "monthly"]

# Who decided the active grain. Only an explicit set_planning call is MANUAL;
# everything else is the app's own choice and says so.
SOURCE_AUTO = "auto"
SOURCE_MANUAL = "manual"

# Why that grain, as a stable code the frontend renders in Spanish.
REASON_NATURAL_FREQUENCY = "natural_frequency"   # finest grain the data supports
REASON_ONLY_OPTION = "only_option"               # the history affords no other
REASON_MANUAL = "manual_choice"                  # someone picked it in Settings
REASON_UNAVAILABLE = "chosen_grain_unavailable"  # their pick no longer exists


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


def _period_choice(stored: dict, available: list[str]) -> tuple[str, str, str]:
    """(period, source, reason_code) — WHY the app is planning at this grain.

    The choice was always automatic (the finest grain the data supports, which
    is its natural reporting frequency), but nothing said so: the UI offered a
    bare dropdown, so a tenant-wide setting that also drives the daily alerts
    read like a per-user view toggle. The reason travels as a code; the
    frontend renders the sentence.

    Replaces the old `_coerce_period`, whose contract this keeps: the period
    get_planning reports and the one resolve_active_session picks a session at
    are decided HERE, by one function, so the grain the UI announces and the
    grain behind the numbers can never disagree.

    Deliberately NOT chosen by forecast error. Aggregating always lowers error,
    so "whichever scores best" would pick the coarsest grain almost every time
    and hand back a plan too coarse to reorder against. The finest grain the
    data can support is the one that keeps the decision actionable.
    """
    # A stored period with no `source` predates that field, and back then only
    # an explicit set_planning call ever wrote one. Treating it as automatic
    # would quietly revert the choice of every tenant who had already picked a
    # grain, so absence of `source` means manual, not auto.
    chosen_by_user = bool(stored.get("period")) and stored.get("source") != SOURCE_AUTO

    if chosen_by_user:
        if stored["period"] in available:
            return stored["period"], SOURCE_MANUAL, REASON_MANUAL
        # Their grain is gone: the newest upload no longer spans enough buckets
        # for it. This used to happen in silence, so the numbers changed unit
        # under them with nothing to point at.
        return available[0], SOURCE_AUTO, REASON_UNAVAILABLE

    if len(available) == 1:
        return available[0], SOURCE_AUTO, REASON_ONLY_OPTION
    return available[0], SOURCE_AUTO, REASON_NATURAL_FREQUENCY


def get_planning(tenant_id: str) -> dict:
    """Resolve the active setting against the newest family. period is coerced
    into available_periods and horizon clamped into 1..reach(period)."""
    stored = tenant_svc.get_settings(tenant_id).get("planning") or {}
    available = _available_periods(tenant_id, _newest_family_id(tenant_id))
    period, source, reason = _period_choice(stored, available)
    max_horizon = GENEROUS_REACH.get(period, 90)
    try:
        horizon = int(stored.get("horizon", DEFAULT_PLANNING["horizon"]))
    except (TypeError, ValueError):
        horizon = DEFAULT_PLANNING["horizon"]
    horizon = max(1, min(horizon, max_horizon))
    return {"period": period, "horizon": horizon,
            "available_periods": available, "max_horizon": max_horizon,
            # Why this grain — so the UI can state the choice instead of
            # offering an unexplained dropdown. `requested_period` is only set
            # when someone's explicit pick could not be honored.
            "period_source": source, "period_reason": reason,
            "requested_period": (
                stored.get("period") if reason == REASON_UNAVAILABLE else None)}


def set_planning(tenant_id: str, period: str, horizon: int) -> dict:
    """Validate + persist the active planning setting. Raises an ``AppError``
    (422) on an unavailable period or an out-of-reach horizon — an admin reads
    both of these on the Configuración screen, so the wording is a code the
    frontend localizes, not English prose."""
    available = _available_periods(tenant_id, _newest_family_id(tenant_id))
    if period not in available:
        raise AppError(
            "planning_period_unavailable",
            f"Period '{period}' is not available for this data; "
            f"choose one of: {', '.join(available)}.",
            params={"period": period, "available": available},
        )
    max_horizon = GENEROUS_REACH.get(period, 90)
    if not (1 <= int(horizon) <= max_horizon):
        raise AppError(
            "planning_horizon_out_of_range",
            f"Horizon must be between 1 and {max_horizon} for period '{period}'.",
            params={"period": period, "horizon": int(horizon),
                    "max_horizon": max_horizon},
        )
    # `source` is what separates "someone chose this" from "the app defaulted
    # here". Without it a stored period is indistinguishable from an automatic
    # one, and the UI cannot honestly say which of the two it is looking at.
    tenant_svc.update_settings(
        tenant_id,
        {"planning": {"period": period, "horizon": int(horizon),
                      "source": SOURCE_MANUAL}})
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
        period, _source, _reason = _period_choice(
            stored, _available_periods(tenant_id, family_id))
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
