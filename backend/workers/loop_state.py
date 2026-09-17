"""When each recurring loop last fired, so a restart cannot skip a day.

Every cron loop in `worker.py` used to compute its next run from
`datetime.now()` and keep nothing. A worker killed at 07:55 and restarted at
08:02 therefore asked for "the next 08:00 boundary strictly after now" and got
**tomorrow**: that day nobody received a stockout digest, a lead-time alert or a
freshness reminder, no activity row was written, and the whole thing looked like
a calm day (stability 11.28). The monthly pass had the louder version — a
missed 1st skips the overstock snapshot, and that month's "capital freed" figure
is broken permanently, because the measurement it needed no longer exists.

The fix is one row per loop, keyed by the loop's name, holding the BOUNDARY it
last completed — not the wall-clock moment it finished. A boundary is the thing
being tracked ("the 08:00 pass of 2026-09-16"), and comparing boundaries is what
makes "have we already done this one?" answerable after a restart, a crash or a
redeploy.

Catch-up is deliberately bounded. Running the pass that was missed twenty
minutes ago is right; sending yesterday's digest today is not — the numbers in
it describe a day the buyer has already lived through. Past the window the
boundary is recorded as skipped, so the gap is visible instead of silent.

This is deployment state, not tenant state: there is one scheduler, and these
rows answer "did this instance do its rounds", which is why nothing here is
scoped by tenant.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.db.connection import execute, query_one

log = logging.getLogger(__name__)

# Loop names. Declared here rather than spelled at each call site so a typo
# cannot quietly create a second, permanently-empty row.
INVENTORY_ALERTS = "inventory_alerts"
INTEGRATION_SYNC = "integration_sync"
MONTHLY_OVERSTOCK = "monthly_overstock"

LOOPS = (INVENTORY_ALERTS, INTEGRATION_SYNC, MONTHLY_OVERSTOCK)

# How late a missed boundary may still be run.
#
# Daily: six hours. The 08:00 digest fired at 09:30 after a restart is the same
# information about the same day; fired at 23:00 it is a message about a day
# that is over. Monthly: three days, because the snapshot it takes is the
# closing measurement of the month that just ended and nothing else can produce
# it afterwards — a late snapshot is worth far more than a missing one.
DAILY_CATCHUP = timedelta(hours=6)
MONTHLY_CATCHUP = timedelta(days=3)

STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"


def last_boundary(loop: str) -> Optional[datetime]:
    """The most recent boundary this loop recorded, or None if it never ran."""
    row = query_one(
        "SELECT last_boundary FROM system_loop_runs WHERE loop = %s", (loop,))
    if not row or not row.get("last_boundary"):
        return None
    value = row["last_boundary"]
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def mark_run(loop: str, boundary: datetime, status: str = STATUS_COMPLETED,
             error: Optional[str] = None) -> None:
    """Record that `boundary` was handled.

    Written whether the pass succeeded or was skipped for being too late: the
    point is that the boundary is accounted for. A loop that could not record
    its run would repeat that boundary on the next restart, which for a digest
    means sending it twice.
    """
    try:
        execute(
            """INSERT INTO system_loop_runs (loop, last_boundary, last_run_at,
                                             last_status, last_error)
               VALUES (%s, %s, NOW(), %s, %s)
               ON CONFLICT (loop) DO UPDATE
                   SET last_boundary = EXCLUDED.last_boundary,
                       last_run_at   = EXCLUDED.last_run_at,
                       last_status   = EXCLUDED.last_status,
                       last_error    = EXCLUDED.last_error""",
            (loop, boundary, status, (error or None)),
        )
    except Exception:
        # Never take the loop down over its own bookkeeping: the pass it just
        # ran is worth more than the marker. The cost of losing this write is a
        # repeated boundary after a restart, which is why it is logged loudly.
        log.exception("loop_state: could not record %s boundary=%s", loop, boundary)


def missed_boundary(
    loop: str, boundary: datetime, now: datetime, catchup: timedelta,
) -> Optional[datetime]:
    """The boundary to run RIGHT NOW, or None when there is nothing to catch up.

    `boundary` is the most recent scheduled moment at or before `now`. It is
    returned when this loop has not recorded it (or anything later) and it is
    still inside the catch-up window. A boundary older than the window is
    recorded as skipped here and not returned — the gap becomes a row somebody
    can read rather than a day that silently produced nothing.
    """
    recorded = last_boundary(loop)
    if recorded is not None and recorded >= boundary:
        return None
    if now - boundary > catchup:
        log.warning(
            "loop_state: %s boundary %s missed by more than %s — skipping it",
            loop, boundary.isoformat(), catchup,
        )
        mark_run(loop, boundary, status=STATUS_SKIPPED,
                 error="missed_beyond_catchup_window")
        return None
    # A first-ever boundary is NOT a catch-up: a fresh install would otherwise
    # fire the digest the moment it boots, before anybody has uploaded a file.
    if recorded is None:
        mark_run(loop, boundary, status=STATUS_SKIPPED, error="first_boot")
        return None
    return boundary


def status() -> list[dict]:
    """Every loop's marker, for /health. A loop that has not fired in days is a
    fact the operator should be able to read without a log file."""
    from backend.db.connection import query
    rows = query(
        "SELECT loop, last_boundary, last_run_at, last_status, last_error "
        "FROM system_loop_runs ORDER BY loop") or []
    by_loop = {r["loop"]: r for r in rows}
    return [{
        "loop":          name,
        "last_boundary": (by_loop[name]["last_boundary"].isoformat()
                          if by_loop.get(name) and by_loop[name].get("last_boundary") else None),
        "last_run_at":   (by_loop[name]["last_run_at"].isoformat()
                          if by_loop.get(name) and by_loop[name].get("last_run_at") else None),
        "last_status":   by_loop[name]["last_status"] if by_loop.get(name) else None,
        "last_error":    by_loop[name]["last_error"] if by_loop.get(name) else None,
    } for name in LOOPS]
