"""Renewal and expiry tracking for blanket supply contracts: the PURE half.

A blanket contract (`supply_contract_service.py`) ends on `period_end`. Whoever
manages the account has to decide before that day whether to renew it, and a
notice period (say 60 days) can make the real deadline earlier than the end
date. This module holds the arithmetic every renewal screen, alert and action
shares; nothing here touches the database.

The same maths is implemented a second time in Rust
(`backend-rs/src/contract_renewal.rs`, which serves the renewals list, the
commitment comparison and the renew action). THIS file is the reference of the
differential test: `tests/contract/renewal_diff_cases.json` is generated from
it by `backend/tests/test_contract_renewal_pure.py` (and the test fails when
the file is stale), and the Rust unit tests replay every case and demand the
same numbers. Change a rule here and both sides move, or the suite is red.

Rules that keep it honest:

1. **A default is not a choice.** `renewal_lead_days` is NULL until somebody
   sets it; NULL reads as the product default 60/30/7 and the screen says so.
2. **Only an active contract is judged.** A draft has not started, a closed or
   cancelled one is history.
3. **Never invent a delivery date.** A fulfilled commitment with no
   fulfilment time is counted in `fulfilled_undated`, never as on time and
   never as late.
4. **A renewal is a new contract, not a new revision of the old one.** The
   fulfilment of a lineage is every fulfilled commitment of that lineage, so
   moving the same lineage into the next term would show last term's deliveries
   as this term's. The renewal is a NEW lineage (revision 1, a draft) that
   points at the contract it renews; nothing about the old one is edited.
5. **A renewal never invents quantities or dates.** The term is the same
   length, an explicit schedule is shifted by the same amount, and a shifted
   release that does not fit the new term refuses the renewal.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Optional

from backend.errors import AppError

DEFAULT_LEAD_DAYS: tuple[int, ...] = (60, 30, 7)
MAX_LEAD_DAYS = 730
MAX_LEAD_ENTRIES = 6
MAX_NOTICE_DAYS = 730
MAX_WITHIN_DAYS = 730
DEFAULT_WITHIN_DAYS = 90
MAX_YEARS = 10
RENEWABLE_STATUSES = ("active", "closed")

_EPS = 1e-6


def _add_months(start: date, k: int) -> date:
    m = start.month - 1 + k
    y = start.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(start.day, calendar.monthrange(y, m)[1]))


# ── Renewal terms ────────────────────────────────────────────────────────────

def clean_lead_days(raw) -> Optional[list[int]]:
    """The configured alert lead times, as distinct positive whole days sorted
    from the farthest to the nearest, or None when nothing was configured
    (None means "the default", see `effective_lead_days`)."""
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or not raw:
        raise AppError("supply_contract_lead_days_invalid",
                       "Alert lead times must be a list of days, such as 60, 30, 7",
                       params={"max_days": MAX_LEAD_DAYS, "max_entries": MAX_LEAD_ENTRIES})
    out: set[int] = set()
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, int) and not (isinstance(v, float) and v.is_integer()):
            raise AppError("supply_contract_lead_days_invalid",
                           "Alert lead times must be whole numbers of days",
                           params={"max_days": MAX_LEAD_DAYS, "max_entries": MAX_LEAD_ENTRIES})
        n = int(v)
        if not 1 <= n <= MAX_LEAD_DAYS:
            raise AppError("supply_contract_lead_days_invalid",
                           "Each alert lead time must be between 1 and 730 days",
                           params={"max_days": MAX_LEAD_DAYS, "max_entries": MAX_LEAD_ENTRIES})
        out.add(n)
    if len(out) > MAX_LEAD_ENTRIES:
        raise AppError("supply_contract_lead_days_invalid",
                       "Too many alert lead times",
                       params={"max_days": MAX_LEAD_DAYS, "max_entries": MAX_LEAD_ENTRIES})
    return sorted(out, reverse=True)


def effective_lead_days(stored) -> list[int]:
    """What the alerts actually use: the configured list, else 60/30/7."""
    if stored:
        return sorted({int(x) for x in stored}, reverse=True)
    return list(DEFAULT_LEAD_DAYS)


def clean_notice_days(raw) -> Optional[int]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or (
            isinstance(raw, float) and not raw.is_integer()):
        raise AppError("supply_contract_notice_days_invalid",
                       "The notice period must be a whole number of days",
                       params={"max_days": MAX_NOTICE_DAYS})
    n = int(raw)
    if not 0 <= n <= MAX_NOTICE_DAYS:
        raise AppError("supply_contract_notice_days_invalid",
                       "The notice period must be between 0 and 730 days",
                       params={"max_days": MAX_NOTICE_DAYS})
    return n


# ── The renewal state of one contract ────────────────────────────────────────

def renewal_view(period_end: date, notice_days: Optional[int], auto_renew: bool,
                 lead_days: list[int], today: date) -> dict:
    """Where an ACTIVE contract stands against its end date.

    `bucket`: `expired` (the end date passed and it is still active),
    `notice_passed` (the notice deadline passed, the term has not ended),
    `due_soon` (inside the farthest alert lead time) or `upcoming`.
    """
    days_to_expiry = (period_end - today).days
    notice_deadline = None if notice_days is None else period_end - timedelta(days=int(notice_days))
    days_to_notice = None if notice_deadline is None else (notice_deadline - today).days
    if days_to_expiry < 0:
        bucket = "expired"
    elif days_to_notice is not None and days_to_notice < 0:
        bucket = "notice_passed"
    elif days_to_expiry <= max(lead_days):
        bucket = "due_soon"
    else:
        bucket = "upcoming"
    return {
        "expiry_date": period_end.isoformat(),
        "days_to_expiry": days_to_expiry,
        "notice_days": notice_days,
        "notice_deadline": None if notice_deadline is None else notice_deadline.isoformat(),
        "days_to_notice": days_to_notice,
        "auto_renew": bool(auto_renew),
        "bucket": bucket,
    }


def due_alert(period_end: date, notice_days: Optional[int], lead_days: list[int],
              today: date, sent: set) -> Optional[dict]:
    """The one alert an active contract owes today, or None.

    `sent` holds the `(expiry_date_iso, lead_days_or_None)` pairs already
    emitted for this contract. The alert counts down to the date a person must
    ACT by: the notice deadline when there is a notice period, the end date
    otherwise. Of the lead times already crossed only the nearest one is
    raised, so a contract entered 5 days before the end is one alert, not
    three. After the end date a still-active contract is raised once as
    expired (`lead_days` None).
    """
    expiry = period_end.isoformat()
    days_to_expiry = (period_end - today).days
    if days_to_expiry < 0:
        key = (expiry, None)
        if key in sent:
            return None
        return {"reason": "contract_expired", "lead_days": None, "expiry_date": expiry,
                "days_left": days_to_expiry}
    action_date = period_end if notice_days is None else period_end - timedelta(days=int(notice_days))
    action_days = (action_date - today).days
    crossed = [t for t in lead_days if action_days <= t]
    if not crossed:
        return None
    lead = min(crossed)
    if (expiry, lead) in sent:
        return None
    return {"reason": "contract_expiring" if notice_days is None else "contract_notice_deadline",
            "lead_days": lead, "expiry_date": expiry, "days_left": days_to_expiry}


# ── The next term ────────────────────────────────────────────────────────────

def _whole_months(start: date, end: date) -> Optional[int]:
    """n when `end` is the day before `start` plus n calendar months (a term
    that runs Jan 1 to Dec 31, or Feb 15 to Mar 14), else None."""
    n = (end.year - start.year) * 12 + end.month - start.month + 1
    for k in (n, n - 1, n + 1):
        if k >= 1 and _add_months(start, k) - timedelta(days=1) == end:
            return k
    return None


def next_term(start: date, end: date) -> tuple[date, date, Optional[int], int]:
    """The term after `start..end`: it starts the day after `end` and has the
    same length. Returns `(new_start, new_end, months, shift_days)`: `months`
    is the whole-month length when the term has one (the renewal then keeps
    month boundaries: 2027-01-01..2027-12-31 renews to 2028-01-01..2028-12-31
    whatever the leap years do), else None and the length is in days."""
    new_start = end + timedelta(days=1)
    months = _whole_months(start, end)
    if months is not None:
        new_end = _add_months(new_start, months) - timedelta(days=1)
    else:
        new_end = new_start + (end - start)
    return new_start, new_end, months, (new_start - start).days


def shift_release_date(d: date, start: date, new_start: date, months: Optional[int]) -> date:
    """An explicit release moved into the next term: by the same number of
    months as the term when it has a whole-month length (clamped to the month's
    last day), else by the same number of days."""
    if months is not None:
        k = (new_start.year - start.year) * 12 + new_start.month - start.month
        return _add_months(d, k)
    return d + timedelta(days=(new_start - start).days)


def renewal_terms(start: date, end: date, releases: Optional[list[dict]]):
    """The renewed term and, for an explicit schedule, the shifted releases.
    Raises `supply_contract_renewal_release_out_of_period` when a shifted
    release does not fall inside the new term, and `supply_contract_period_too_long`
    past the ten-year limit; the caller has already refused a contract whose
    new term is over (`supply_contract_renewal_period_elapsed`)."""
    new_start, new_end, months, _ = next_term(start, end)
    if new_end > _add_months(new_start, 12 * MAX_YEARS):
        raise AppError("supply_contract_period_too_long",
                       "A contract period is limited to ten years", params={"years": MAX_YEARS})
    shifted = None
    if releases is not None:
        shifted = []
        for r in releases:
            nd = shift_release_date(r["date"], start, new_start, months)
            if not new_start <= nd <= new_end:
                raise AppError("supply_contract_renewal_release_out_of_period",
                               "A release does not fit the renewed term; create the next "
                               "contract by hand",
                               status_code=422,
                               params={"sku": r["sku"], "date": r["date"].isoformat(),
                                       "shifted": nd.isoformat()})
            shifted.append({"sku": r["sku"], "date": nd, "quantity": float(r["quantity"])})
        shifted.sort(key=lambda r: (r["date"], r["sku"]))
    return new_start, new_end, shifted


# ── Committed against delivered ──────────────────────────────────────────────

def _r4(x: float) -> float:
    return round(float(x), 4)


def _pct(num: float, den: float) -> Optional[float]:
    return round(num / den * 100, 1) if den > 0 else None


def commitment_comparison(releases: list[dict], commitments: list[dict], today: date) -> dict:
    """Committed units against delivered units over the contract term.

    `releases`: `supply_contract_service.expand_releases` of the current
    revision (`{sku, date, quantity}`). `commitments`: the lineage's LIVE
    commitments as `{sku, contract_release_date, quantity, status,
    fulfilled_on}` where `fulfilled_on` is the date the commitment was marked
    fulfilled (None when unknown or not fulfilled).

    * committed: every scheduled release of the term; due_to_date: those dated
      today or earlier; delivered: the quantity of the fulfilled commitments.
    * fill rate: delivered / due_to_date (None when nothing is due yet, never
      an invented 100%); it is not capped, an over-delivery reads above 100.
    * late: a fulfilled commitment marked fulfilled after the release date it
      stands for (the CONTRACT's date, not the delivery date a person may have
      moved). Fulfilled with no fulfilment date: `fulfilled_undated`, in
      neither group.
    * overdue_open: releases dated before today whose commitment is not
      fulfilled or cancelled (still open, or never materialised).
    """
    by_key = {(c["sku"], c["contract_release_date"]): c for c in commitments
              if c.get("contract_release_date") is not None}
    line_order: list[str] = []
    committed: dict[str, float] = {}
    due: dict[str, float] = {}
    overdue_n = 0
    overdue_units = 0.0
    for r in releases:
        sku = r["sku"]
        if sku not in committed:
            line_order.append(sku)
            committed[sku] = 0.0
            due[sku] = 0.0
        committed[sku] += float(r["quantity"])
        if r["date"] <= today:
            due[sku] += float(r["quantity"])
        c = by_key.get((sku, r["date"]))
        state = c["status"] if c is not None else "missing"
        if r["date"] < today and state in ("open", "missing"):
            overdue_n += 1
            overdue_units += float(r["quantity"])

    delivered: dict[str, float] = {}
    late_n: dict[str, int] = {}
    late_units: dict[str, float] = {}
    max_late = 0
    undated = 0
    for c in commitments:
        if c.get("status") != "fulfilled":
            continue
        sku = c["sku"]
        if sku not in committed:
            continue          # a SKU no longer on the schedule is not this term's
        q = float(c["quantity"])
        delivered[sku] = delivered.get(sku, 0.0) + q
        rel = c.get("contract_release_date")
        done = c.get("fulfilled_on")
        if rel is None:
            continue
        if done is None:
            undated += 1
            continue
        if done > rel:
            late_n[sku] = late_n.get(sku, 0) + 1
            late_units[sku] = late_units.get(sku, 0.0) + q
            max_late = max(max_late, (done - rel).days)

    lines = []
    for sku in line_order:
        d = delivered.get(sku, 0.0)
        lines.append({
            "sku": sku,
            "committed": _r4(committed[sku]),
            "due_to_date": _r4(due[sku]),
            "delivered": _r4(d),
            "fill_rate_pct": _pct(d, due[sku]),
            "late_deliveries": late_n.get(sku, 0),
            "late_units": _r4(late_units.get(sku, 0.0)),
        })
    committed_total = sum(committed[s] for s in line_order)
    due_total = sum(due[s] for s in line_order)
    delivered_total = sum(delivered.get(s, 0.0) for s in line_order)
    return {
        "committed_units": _r4(committed_total),
        "due_to_date": _r4(due_total),
        "delivered_units": _r4(delivered_total),
        "remaining_units": _r4(max(0.0, committed_total - delivered_total)),
        "shortfall_to_date": _r4(max(0.0, due_total - delivered_total)),
        "fill_rate_pct": _pct(delivered_total, due_total),
        "term_fill_pct": _pct(delivered_total, committed_total),
        "late_deliveries": sum(late_n.get(s, 0) for s in line_order),
        "late_units": _r4(sum(late_units.get(s, 0.0) for s in line_order)),
        "max_days_late": max_late,
        "fulfilled_undated": undated,
        "overdue_open": overdue_n,
        "overdue_open_units": _r4(overdue_units),
        "lines": lines,
    }
