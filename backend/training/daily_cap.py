"""The daily training ceiling (`max_trainings_per_day`).

A *training* is one launch of the models for a tenant: a manual launch, the
wizard's Quick start, the demo quickstart or a scheduled retrain that refits.
A launch fans out into one job per granularity, so the unit counted here is the
HEAD of a family (the base session, `family_id = id`), never every sibling job:
counting jobs would make a one-training-a-day plan refuse its own daily/weekly/
monthly fan-out.

Not counted, on purpose:

- back-tests (`sessions.is_backtest`): verification runs, not forecasts;
- re-forecasts (`sessions.is_reforecast`): they reload stored models and fit
  nothing (a scheduled run that falls back to a full refit goes through the
  normal launch and IS counted);
- a job that failed or was cancelled before a worker ever started it
  (`started_at IS NULL`): it never trained.

A queued job counts from the moment it is created.

The day is the TENANT's calendar day (`api/v1/timezone.py`), so "today" is the
day its users see, not UTC's.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from backend.db.connection import query_one

log = logging.getLogger(__name__)

LIMIT_KEY = "max_trainings_per_day"


def day_bounds_utc(tenant_id: str, now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """[start, end) of the tenant's current calendar day, as UTC instants."""
    from backend.api.v1.timezone import zoneinfo_of
    tz = zoneinfo_of(tenant_id)
    local_now = (now or datetime.now(timezone.utc)).astimezone(tz)
    start_local = datetime.combine(local_now.date(), time.min, tzinfo=tz)
    # Next local midnight, built from the calendar date so a 23h/25h DST day
    # still ends at midnight.
    end_local = datetime.combine(local_now.date() + timedelta(days=1), time.min, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def count_trainings_today(tenant_id: str, conn=None, now: Optional[datetime] = None) -> int:
    """Trainings launched today, in ONE query on `jobs (tenant_id, created_at)`."""
    start, end = day_bounds_utc(tenant_id, now)
    row = query_one(
        """SELECT COUNT(*) AS cnt
             FROM jobs j
             JOIN sessions s ON s.id = j.session_id AND s.tenant_id = j.tenant_id
            WHERE j.tenant_id = %s
              AND j.created_at >= %s AND j.created_at < %s
              AND NOT s.is_backtest
              AND NOT s.is_reforecast
              AND (s.family_id IS NULL OR s.family_id = s.id)
              AND NOT (j.started_at IS NULL AND j.status IN ('FAILED', 'CANCELLED'))""",
        (tenant_id, start, end), conn=conn,
    )
    return int(row["cnt"]) if row else 0


def ensure_can_train(tenant_id: str, conn=None) -> None:
    """Raise 403 PLAN_LIMIT_REACHED (`limit` = `max_trainings_per_day`, with
    `current` = used today and `max`) when today's ceiling is spent. No-op in
    testing mode and on unlimited plans (`enforce_limit` owns both).

    Pass the connection of `limit_guard` for the authoritative, race-free check
    taken together with the base job's insert; without one it is a plain
    pre-check, good for refusing BEFORE anything has been created."""
    from backend.entitlements.service import enforce_limit
    enforce_limit(tenant_id, LIMIT_KEY, count_trainings_today(tenant_id, conn=conn),
                  conn=conn)


def over_cap(tenant_id: str) -> Optional[dict]:
    """`{"max": ceiling, "used": n}` when today's ceiling is spent, else None.

    The non-raising twin of `ensure_can_train`, for the callers nobody is
    watching (a scheduled retrain), which must record a skip instead of failing.
    Same bypasses: testing mode and an unlimited plan never refuse."""
    from backend.config import settings
    from backend.entitlements.service import tenant_limits
    from backend.tenants.service import get_tenant
    if settings.testing_mode:
        return None
    ceiling = tenant_limits(get_tenant(tenant_id) or {"quota": {}}).get(LIMIT_KEY)
    if ceiling is None:
        return None
    used = count_trainings_today(tenant_id)
    return {"max": int(ceiling), "used": used} if used + 1 > ceiling else None


def remaining_today(tenant_id: str, limits: dict) -> Optional[int]:
    """Trainings left today, or None when the plan is unlimited."""
    ceiling = limits.get(LIMIT_KEY)
    if ceiling is None:
        return None
    return max(0, int(ceiling) - count_trainings_today(tenant_id))
