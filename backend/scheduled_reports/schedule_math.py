"""When a scheduled report is due, and which period a firing belongs to.

Pure functions, no database. The Rust API computes `next_run_at` for a new or
edited schedule with its port of croniter (`routes/schedule/cron.rs`); this is
the Python half, with the real `croniter`, used by the worker to advance a
schedule after a run. `test_scheduled_reports_math.py` and the Rust unit tests
pin the SAME vectors, DST boundaries included.

The period key is the LOCAL wall-clock minute of the firing, not the UTC
instant. That is the double-send guard for daylight saving time: when clocks
go back, 02:00 happens twice and croniter fires at both instants (a different
UTC instant each time, which a key made of the instant would treat as two
periods and send twice). Both map to the same local text, so the second one
finds the first one's `report_schedule_runs` row and does nothing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


def cron_for(frequency: str, weekday: Optional[int], day_of_month: Optional[int], hour: int) -> str:
    """ISO weekday 1 (Monday) .. 7 (Sunday) -> cron day-of-week (Sunday is 0)."""
    if frequency == "weekly":
        if weekday is None or not 1 <= weekday <= 7:
            raise ValueError("weekly needs a weekday from 1 to 7")
        return f"0 {int(hour)} * * {weekday % 7}"
    if frequency == "monthly":
        if day_of_month is None or not 1 <= day_of_month <= 28:
            raise ValueError("monthly needs a day of the month from 1 to 28")
        return f"0 {int(hour)} {day_of_month} * *"
    raise ValueError(f"unknown frequency {frequency!r}")


def next_run_after(cron_expr: str, tz_name: str, after_utc: datetime) -> datetime:
    """The next firing strictly after `after_utc`, as a UTC instant, with the
    cron read in `tz_name`. Raises (never invents a time) when croniter cannot
    answer: a schedule that cannot compute its next run must say so."""
    from croniter import croniter
    zone = ZoneInfo(tz_name)
    base = after_utc.astimezone(zone)
    nxt = croniter(cron_expr, base).get_next(datetime)
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=zone)
    return nxt.astimezone(timezone.utc)


def local_key(due_utc: datetime, tz_name: str) -> str:
    """The period identity: the local wall clock of the firing, to the minute."""
    return due_utc.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%dT%H:%M")


def advance(cron_expr: str, tz_name: str, due_utc: datetime, now_utc: datetime) -> datetime:
    """The next firing after a run, never the same local period again.

    Starts from whichever is later of the due instant and now (a worker that
    was down must not schedule the past), and steps over a firing whose local
    key equals the one just handled (the repeated hour at the end of DST).
    """
    key = local_key(due_utc, tz_name)
    base = max(due_utc, now_utc)
    for _ in range(4):
        nxt = next_run_after(cron_expr, tz_name, base)
        if local_key(nxt, tz_name) != key:
            return nxt
        base = nxt
    raise ValueError("could not find a later firing")


def window_for(frequency: str, local_date: date) -> tuple[date, date]:
    """The days a report covers, inclusive, relative to the local date it is
    produced on: weekly = the 7 days before it, monthly = the same date last
    month through yesterday. Today is excluded: it is not over."""
    end = local_date - timedelta(days=1)
    if frequency == "weekly":
        return local_date - timedelta(days=7), end
    first = local_date.replace(day=1)
    prev_last = first - timedelta(days=1)
    day = min(local_date.day, prev_last.day)
    return prev_last.replace(day=day), end


def window_bounds_utc(start: date, end: date, tz_name: str) -> tuple[datetime, datetime]:
    """[start 00:00 local, end+1 00:00 local) as UTC instants."""
    zone = ZoneInfo(tz_name)
    lo = datetime(start.year, start.month, start.day, tzinfo=zone)
    nxt = end + timedelta(days=1)
    hi = datetime(nxt.year, nxt.month, nxt.day, tzinfo=zone)
    return lo.astimezone(timezone.utc), hi.astimezone(timezone.utc)
