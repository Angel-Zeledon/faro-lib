"""Date rules of a recurring delivery schedule: the PYTHON REFERENCE.

The product code that materialises schedules is Rust
(`backend-rs/src/recurring/dates.rs`). This module is the independent
statement of the same rules, written the way the Python side would write them,
and it is what the differential test compares the Rust port against
(`tests/contract/gen_recurring_fixtures.py` writes its answers; a `cargo test`
reads them). It is pure: no database, no clock.

The rules
---------
A schedule has a `frequency`:

* `weekly`       every 7 days on `weekday` (0 = Monday ... 6 = Sunday)
* `fortnightly`  every 14 days on `weekday`, counted from the first such
                 weekday on or after `start_date`
* `semimonthly`  the 15th and the last day of every month (a "quincena")
* `monthly`      `day_of_month` of every month, clamped to the month's last
                 day (31 means "the last day of the month")

Each nominal date must lie in `[start_date, end_date]`. A nominal date is a
NON-WORKING day when it is one of `holiday_dates`, or a Saturday / Sunday and
`avoid_weekends` is set. `shift_rule` then decides: `skip` drops the delivery,
`before` / `after` move it to the previous / next working day. The shifted date
may leave `[start_date, end_date]`: the end date bounds the nominal schedule,
not the day the truck goes.

The nominal date is the occurrence's identity (the idempotency key of the
commitment it becomes); the delivery date is where the commitment sits.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Optional

FREQUENCIES = ("weekly", "fortnightly", "semimonthly", "monthly")
SHIFT_RULES = ("skip", "before", "after")
# Longest search for a working day. The holiday list is capped well below this,
# so a working day is always found; the bound only keeps the loop finite.
MAX_SHIFT_DAYS = 60
# A delivery can move this far from its nominal date, so the nominal window
# examined for a delivery window is padded by it.
SHIFT_PAD_DAYS = MAX_SHIFT_DAYS + 2


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _month_dates(spec: dict, year: int, month: int) -> list[date]:
    if spec["frequency"] == "semimonthly":
        return [date(year, month, 15), date(year, month, _last_day(year, month))]
    return [date(year, month, min(int(spec["day_of_month"]), _last_day(year, month)))]


def nominal_dates(spec: dict, lo: date, hi: date) -> list[date]:
    """Nominal dates of the schedule inside `[lo, hi]` (and inside its own
    start/end), in order. `spec` keys: frequency, weekday, day_of_month,
    start_date, end_date (dates)."""
    start, end = spec["start_date"], spec["end_date"]
    lo, hi = max(lo, start), min(hi, end)
    if lo > hi:
        return []
    freq = spec["frequency"]
    out: list[date] = []
    if freq in ("weekly", "fortnightly"):
        step = 7 if freq == "weekly" else 14
        anchor = start + timedelta(days=(int(spec["weekday"]) - start.weekday()) % 7)
        k = 0
        if lo > anchor:
            k = -(-(lo - anchor).days // step)        # ceil
        d = anchor + timedelta(days=step * k)
        while d <= hi:
            if d >= lo:
                out.append(d)
            d += timedelta(days=step)
        return out
    y, m = lo.year, lo.month
    while (y, m) <= (hi.year, hi.month):
        for d in _month_dates(spec, y, m):
            if lo <= d <= hi:
                out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _non_working(d: date, holidays: set, avoid_weekends: bool) -> bool:
    return d in holidays or (avoid_weekends and d.weekday() >= 5)


def delivery_date(nominal: date, holidays: set, avoid_weekends: bool,
                  shift_rule: str) -> Optional[date]:
    """Where the delivery for `nominal` happens, or None when it is skipped."""
    if not _non_working(nominal, holidays, avoid_weekends):
        return nominal
    if shift_rule == "skip":
        return None
    step = 1 if shift_rule == "after" else -1
    d = nominal
    for _ in range(MAX_SHIFT_DAYS):
        d += timedelta(days=step)
        if not _non_working(d, holidays, avoid_weekends):
            return d
    return None


def occurrences(spec: dict, lo: date, hi: date) -> list[tuple[date, date]]:
    """`(nominal, delivery)` for every delivery whose DELIVERY date is in
    `[lo, hi]`, ordered by nominal date. Skipped occurrences are not listed."""
    holidays = {d for d in spec.get("holiday_dates") or []}
    avoid = bool(spec.get("avoid_weekends"))
    rule = spec.get("shift_rule") or "after"
    out: list[tuple[date, date]] = []
    pad = timedelta(days=SHIFT_PAD_DAYS)
    for nominal in nominal_dates(spec, lo - pad, hi + pad):
        delivery = delivery_date(nominal, holidays, avoid, rule)
        if delivery is not None and lo <= delivery <= hi:
            out.append((nominal, delivery))
    return out
