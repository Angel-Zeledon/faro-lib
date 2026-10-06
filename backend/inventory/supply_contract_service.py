"""Blanket supply contracts ("contratos marco"): a volume a customer agreed to
take over a period, called off in releases.

A large customer signs for, say, 120,000 units of SKU X over twelve months,
delivered in monthly call-offs with a tolerance. The purchase maths already
knows how to plan against a customer order with a date and a quantity
(`committed_demand_service`). A contract therefore never enters the purchase
maths itself: its releases are MATERIALISED into ordinary `committed_demand`
rows as their dates approach, and the semaphore, the optimizer, the at-risk
check and the assistant see them through the one code path they already use.

Rules that keep it honest:

1. **Append-only.** A contract row is never edited or deleted. Every change -
   a revision of its terms, activating a draft, closing, cancelling - inserts
   the next revision of the same lineage (`root_id`) and stamps the previous
   one `superseded_by`. One current revision per lineage (a partial unique
   index backs it).
2. **Idempotent materialisation.** One live commitment per lineage, SKU and
   release date, guarded by a partial unique index on `committed_demand`
   (`contract_root_id, sku, contract_release_date` WHERE not withdrawn) and
   inserted with ON CONFLICT DO NOTHING. Running it twice, or the daily pass
   racing a save, never duplicates a release.
3. **Withdraw, never delete.** Revising, closing or cancelling a contract
   cancels the lineage's still-OPEN commitments and stamps them
   `contract_withdrawn_at`; fulfilled ones are history and stay. The new
   revision then materialises its own releases; a release already fulfilled
   keeps its slot, so it is not called off twice.
4. **Never invent data.** A release without a quantity is refused, an
   explicit schedule that does not add up to the line's total is refused, and
   a release in the past that was never fulfilled is shown as overdue, not
   hidden. A projection needs at least one release already due; before that
   it is None, not a guess.
5. **No contracts, no change.** With no contract recorded nothing here runs a
   write and every existing number is exactly what it was.
"""

from __future__ import annotations

import calendar
import logging
import math
import uuid
from datetime import date, timedelta
from typing import Optional

import psycopg2.extras

from backend.db.connection import query, query_one, transaction
from backend.errors import AppError
from backend.inventory import contract_renewal as renewal

log = logging.getLogger(__name__)

STATUSES = ("draft", "active", "closed", "cancelled")
CREATE_STATUSES = ("draft", "active")
SCHEDULE_KINDS = ("monthly", "weekly", "explicit")
# Which status a contract may move to from each status. Closed and cancelled
# are final: a contract that ended is history, a new one is a new contract.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("active", "cancelled"),
    "active": ("closed", "cancelled"),
    "closed": (),
    "cancelled": (),
}
REVISABLE = ("draft", "active")

MAX_LINES = 50
MAX_RELEASES = 2000
MAX_YEARS = 10
MAX_QUANTITY = 1e9
MAX_PRICE = 1e12
MAX_CUSTOMER_LENGTH = 200
MAX_REFERENCE_LENGTH = 100
MAX_NOTE_LENGTH = 300
MAX_SKU_LENGTH = 200
MAX_RELEASE_ERRORS = 50

# How far ahead releases become commitments. At least this many days, and
# always the longest lead time of the contract's SKUs plus a review margin: a
# release the protection interval can see but that was not materialised yet
# would be under-bought, silently. Capped so a typo'd lead time cannot dump a
# decade of releases into the ledger.
MATERIALISE_HORIZON_DAYS = 180
HORIZON_REVIEW_MARGIN_DAYS = 60
MAX_HORIZON_DAYS = 730

_EPS = 1e-6


# ── Small helpers ────────────────────────────────────────────────────────────

def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _as_date(value, field: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        raise AppError("date_invalid_iso", f"{field} must be an ISO date (YYYY-MM-DD)",
                       params={"field": field})


def _number(value) -> Optional[float]:
    """A finite float, or None when the value is missing or not a number."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _fold(name: Optional[str]) -> str:
    return (name or "").strip().casefold()


def _add_months(start: date, k: int) -> date:
    """`start` moved k months, the day clamped to the month's last day (a
    contract starting on Jan 31 calls off on Feb 28/29, Mar 31, ...)."""
    m = start.month - 1 + k
    y = start.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(start.day, calendar.monthrange(y, m)[1]))


# ── Pure: the schedule ───────────────────────────────────────────────────────

def release_dates(kind: str, start: date, end: date) -> list[date]:
    """The call-off dates of an even schedule: every month (or week) from
    `start`, while on or before `end`."""
    out: list[date] = []
    k = 0
    while True:
        d = _add_months(start, k) if kind == "monthly" else start + timedelta(days=7 * k)
        if d > end:
            return out
        out.append(d)
        k += 1
        if len(out) > MAX_RELEASES:
            return out


def split_evenly(total: float, n: int) -> list[float]:
    """`total` over `n` releases, adding up to exactly `total`.

    A whole number of units stays whole: the remainder goes one unit at a time
    to the earliest releases (120 over 7 = 18, 17, 17, 17, 17, 17, 17). A
    fractional total is split to 4 decimals and the last release absorbs the
    rounding.
    """
    if n <= 0:
        return []
    if abs(total - round(total)) < _EPS:
        base, rem = divmod(int(round(total)), n)
        return [float(base + (1 if i < rem else 0)) for i in range(n)]
    each = math.floor(total / n * 10000) / 10000
    return [each] * (n - 1) + [round(total - each * (n - 1), 4)]


def expand_releases(contract: dict) -> list[dict]:
    """Every scheduled release of a (cleaned) contract as
    `{sku, date, quantity}`, sorted by date then SKU.

    Even schedules split each line's total over the period's month (week)
    dates; a release that would carry zero units (fewer units than dates) is
    not a release and is left out. An explicit schedule is taken as written.
    """
    out: list[dict] = []
    if contract["schedule_kind"] == "explicit":
        for r in contract.get("releases") or []:
            out.append({"sku": r["sku"], "date": _as_date(r["date"], "date"),
                        "quantity": float(r["quantity"])})
    else:
        dates = release_dates(contract["schedule_kind"], contract["period_start"],
                              contract["period_end"])
        for line in contract["lines"]:
            for d, q in zip(dates, split_evenly(float(line["total_quantity"]), len(dates))):
                if q > 0:
                    out.append({"sku": line["sku"], "date": d, "quantity": q})
    out.sort(key=lambda r: (r["date"], r["sku"]))
    return out


def materialise_horizon_days(longest_lead_days: Optional[float]) -> int:
    lead = max(0.0, float(longest_lead_days or 0.0))
    want = max(MATERIALISE_HORIZON_DAYS, int(math.ceil(lead)) + HORIZON_REVIEW_MARGIN_DAYS)
    return min(MAX_HORIZON_DAYS, want)


def releases_to_materialise(releases: list[dict], existing_keys: set, today: date,
                            horizon_days: int) -> list[dict]:
    """The releases that should be a commitment now and are not one yet.

    Every release dated up to `today + horizon_days` - including past ones: an
    overdue call-off the customer still expects is demand, and it must exist as
    a commitment for somebody to close it as fulfilled. `existing_keys` is the
    set of `(sku, release_date)` already held by a live (non-withdrawn)
    commitment of the lineage, whatever its status.
    """
    limit = today + timedelta(days=int(horizon_days))
    return [r for r in releases
            if r["date"] <= limit and (r["sku"], r["date"]) not in existing_keys]


# ── Pure: validation ─────────────────────────────────────────────────────────

def _clean_lines(raw_lines, explicit: bool) -> list[dict]:
    if not isinstance(raw_lines, list) or not raw_lines:
        raise AppError("supply_contract_lines_required", "Add at least one product line")
    if len(raw_lines) > MAX_LINES:
        raise AppError("supply_contract_too_many_lines",
                       "A contract can have at most 50 product lines",
                       params={"max": MAX_LINES})
    lines, seen = [], set()
    for i, raw in enumerate(raw_lines, start=1):
        raw = raw if isinstance(raw, dict) else {}
        sku = str(raw.get("sku") or "").strip()
        if not sku or len(sku) > MAX_SKU_LENGTH:
            raise AppError("supply_contract_line_sku_required",
                           "Every line needs a product", params={"line": i})
        if sku in seen:
            raise AppError("supply_contract_line_duplicate",
                           "A product appears on two lines; use one line per product",
                           params={"sku": sku})
        seen.add(sku)
        total = _number(raw.get("total_quantity"))
        if total is None and raw.get("total_quantity") not in (None, ""):
            raise AppError("supply_contract_line_quantity_invalid",
                           "The total quantity must be a number greater than zero",
                           params={"line": i, "sku": sku})
        if total is None and not explicit:
            # An even split of nothing is nothing: the total is what is split.
            raise AppError("supply_contract_line_quantity_invalid",
                           "The total quantity must be a number greater than zero",
                           params={"line": i, "sku": sku})
        if total is not None and not (0 < total <= MAX_QUANTITY):
            raise AppError("supply_contract_line_quantity_invalid",
                           "The total quantity must be a number greater than zero",
                           params={"line": i, "sku": sku})
        price = _number(raw.get("unit_price"))
        if raw.get("unit_price") not in (None, "") and (price is None or not 0 <= price <= MAX_PRICE):
            raise AppError("supply_contract_line_price_invalid",
                           "The unit price must be a number of zero or more",
                           params={"line": i, "sku": sku})
        lines.append({"sku": sku, "total_quantity": total, "unit_price": price})
    return lines


def _clean_release(raw, row: int, lines: list[dict], start: date, end: date) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    try:
        d = _as_date(raw.get("date"), "date") if raw.get("date") not in (None, "") else None
    except AppError:
        d = None
    if d is None:
        raise AppError("supply_contract_release_date_invalid",
                       "The release needs a valid date (YYYY-MM-DD)", params={"row": row})
    if raw.get("quantity") is None or (isinstance(raw.get("quantity"), str)
                                       and raw["quantity"].strip() == ""):
        raise AppError("supply_contract_release_quantity_missing",
                       "The release has no quantity", params={"row": row})
    qty = _number(raw.get("quantity"))
    if qty is None or not 0 < qty <= MAX_QUANTITY:
        raise AppError("supply_contract_release_quantity_invalid",
                       "The release quantity must be a number greater than zero",
                       params={"row": row})
    sku = str(raw.get("sku") or "").strip()
    if not sku:
        if len(lines) != 1:
            raise AppError("supply_contract_release_sku_required",
                           "Say which product the release is for", params={"row": row})
        sku = lines[0]["sku"]
    if sku not in {ln["sku"] for ln in lines}:
        raise AppError("supply_contract_release_sku_unknown",
                       "The release names a product that is not on the contract",
                       params={"row": row, "sku": sku})
    if not start <= d <= end:
        raise AppError("supply_contract_release_out_of_period",
                       "The release date is outside the contract period",
                       params={"row": row, "date": d.isoformat()})
    return {"sku": sku, "date": d, "quantity": qty}


def clean_contract(raw: dict, today: Optional[date] = None) -> dict:
    """Validate a contract's terms and return them normalised. Raises AppError
    with a code (and, for a pasted schedule, every bad row at once)."""
    today = today or date.today()
    customer = str(raw.get("customer") or "").strip()[:MAX_CUSTOMER_LENGTH]
    if not customer:
        raise AppError("supply_contract_customer_required", "Name the customer")
    kind = raw.get("schedule_kind")
    if kind not in SCHEDULE_KINDS:
        raise AppError("supply_contract_schedule_invalid",
                       "The schedule must be monthly, weekly or an explicit list",
                       params={"schedule_kind": str(kind)[:20]})
    explicit = kind == "explicit"
    lines = _clean_lines(raw.get("lines"), explicit)

    start = _as_date(raw.get("period_start"), "period_start")
    end = _as_date(raw.get("period_end"), "period_end")
    if end < start:
        raise AppError("supply_contract_period_invalid",
                       "The contract ends before it starts",
                       params={"period_start": start.isoformat(), "period_end": end.isoformat()})
    if end > _add_months(start, 12 * MAX_YEARS) or start > _add_months(today, 12 * MAX_YEARS):
        raise AppError("supply_contract_period_too_long",
                       "A contract period is limited to ten years",
                       params={"years": MAX_YEARS})

    tol = _number(raw.get("tolerance_pct"))
    if raw.get("tolerance_pct") in (None, ""):
        tol = 0.0
    if tol is None or not 0 <= tol <= 100:
        raise AppError("supply_contract_tolerance_invalid",
                       "The tolerance must be a percentage between 0 and 100")

    releases = None
    if explicit:
        raw_rel = raw.get("releases")
        if not isinstance(raw_rel, list) or not raw_rel:
            raise AppError("supply_contract_releases_required",
                           "List at least one release (date and quantity)")
        if len(raw_rel) > MAX_RELEASES:
            raise AppError("supply_contract_too_many_releases",
                           "A contract can have at most 2,000 releases",
                           params={"max": MAX_RELEASES})
        cleaned, errors, seen = [], [], set()
        for i, r in enumerate(raw_rel, start=1):
            try:
                c = _clean_release(r, i, lines, start, end)
                if (c["sku"], c["date"]) in seen:
                    raise AppError("supply_contract_release_duplicate",
                                   "The same product has two releases on that date",
                                   params={"row": i, "date": c["date"].isoformat()})
                seen.add((c["sku"], c["date"]))
                cleaned.append(c)
            except AppError as e:
                errors.append({"row": i, "code": e.code, "params": e.params or {}})
        if errors:
            raise AppError("supply_contract_releases_invalid",
                           "Some releases are not valid; nothing was saved",
                           status_code=422,
                           params={"errors": errors[:MAX_RELEASE_ERRORS], "bad_rows": len(errors)})
        for line in lines:
            scheduled = sum(r["quantity"] for r in cleaned if r["sku"] == line["sku"])
            if scheduled <= 0:
                raise AppError("supply_contract_line_without_releases",
                               "A product on the contract has no release",
                               params={"sku": line["sku"]})
            if line["total_quantity"] is None:
                # The list IS the volume: its sum is the line total, not a guess.
                line["total_quantity"] = round(scheduled, 6)
            elif abs(scheduled - line["total_quantity"]) > _EPS * max(1.0, line["total_quantity"]):
                raise AppError("supply_contract_schedule_total_mismatch",
                               "The releases do not add up to the line's total",
                               params={"sku": line["sku"], "total": line["total_quantity"],
                                       "scheduled": round(scheduled, 4)})
        releases = sorted(cleaned, key=lambda r: (r["date"], r["sku"]))

    contract = {
        "customer": customer,
        "reference": str(raw.get("reference") or "").strip()[:MAX_REFERENCE_LENGTH] or None,
        "lines": lines,
        "period_start": start,
        "period_end": end,
        "schedule_kind": kind,
        "releases": releases,
        "tolerance_pct": float(tol),
        "warehouse_id": str(raw.get("warehouse_id") or "").strip() or None,
        "on_top_of_base": bool(True if raw.get("on_top_of_base") is None else raw["on_top_of_base"]),
        "note": str(raw.get("note") or "").strip()[:MAX_NOTE_LENGTH] or None,
        # Renewal tracking (contract_renewal.py). Absent = nothing recorded; a
        # revision that omits them keeps the current revision's (see `revise`).
        "notice_days": renewal.clean_notice_days(raw.get("notice_days")),
        "auto_renew": bool(raw.get("auto_renew") or False),
        "renewal_lead_days": renewal.clean_lead_days(raw.get("renewal_lead_days")),
        "renewed_from_root_id": None,
    }
    if not explicit:
        n = len(expand_releases(contract))
        if n == 0:
            raise AppError("supply_contract_no_releases",
                           "The period and schedule produce no release")
        if n > MAX_RELEASES:
            raise AppError("supply_contract_too_many_releases",
                           "A contract can have at most 2,000 releases",
                           params={"max": MAX_RELEASES})
    return contract


# ── Pure: fulfilment ─────────────────────────────────────────────────────────

def contract_progress(releases: list[dict], commitments: list[dict], today: date,
                      tolerance_pct: float, horizon_days: int, status: str) -> dict:
    """Delivered against scheduled for one contract lineage.

    `releases`: the current revision's `expand_releases`. `commitments`: the
    lineage's LIVE (non-withdrawn) commitments as `{sku, contract_release_date,
    quantity, status}`. Delivered is the quantity of the fulfilled ones (a
    person who records a short delivery edits the quantity before closing it).

    Each release gets a state: `fulfilled` / `open` / `cancelled` from its
    commitment; `missing` when it should already be a commitment (inside the
    horizon) and is not one; `scheduled` when it is further out.

    Only an ACTIVE contract is judged: `overdue` lists every past release
    still open or missing (never hidden), `behind_schedule` is delivered below
    what was due to date by more than the tolerance, and
    `projected_shortfall` assumes the remaining releases are delivered at the
    pace so far (delivered / due, capped at 1). With nothing due yet there is
    no pace, and the projection is None rather than an invented number.
    """
    tol = max(0.0, min(100.0, float(tolerance_pct or 0.0))) / 100.0
    live = status == "active"
    horizon_end = today + timedelta(days=int(horizon_days))
    by_key = {(c["sku"], c["contract_release_date"]): c for c in commitments
              if c.get("contract_release_date") is not None}

    delivered_by_sku: dict[str, float] = {}
    for c in commitments:
        if c.get("status") == "fulfilled":
            delivered_by_sku[c["sku"]] = delivered_by_sku.get(c["sku"], 0.0) + float(c["quantity"])

    out_releases, overdue = [], []
    lines: dict[str, dict] = {}
    future_open = 0.0
    for r in releases:
        c = by_key.get((r["sku"], r["date"]))
        if c is not None:
            state = c["status"]
        elif r["date"] <= horizon_end:
            state = "missing"
        else:
            state = "scheduled"
        is_overdue = live and r["date"] < today and state in ("open", "missing")
        entry = {"sku": r["sku"], "date": r["date"].isoformat(),
                 "quantity": round(float(r["quantity"]), 4), "state": state,
                 "overdue": is_overdue}
        out_releases.append(entry)
        if is_overdue:
            overdue.append(entry)
        ln = lines.setdefault(r["sku"], {"sku": r["sku"], "scheduled": 0.0, "due_to_date": 0.0})
        ln["scheduled"] += float(r["quantity"])
        if r["date"] <= today:
            ln["due_to_date"] += float(r["quantity"])
        elif state not in ("fulfilled", "cancelled"):
            future_open += float(r["quantity"])

    line_out = []
    for sku, ln in lines.items():
        delivered = delivered_by_sku.get(sku, 0.0)
        due = ln["due_to_date"]
        line_out.append({
            "sku": sku,
            "scheduled": round(ln["scheduled"], 4),
            "due_to_date": round(due, 4),
            "delivered": round(delivered, 4),
            "remaining": round(max(0.0, ln["scheduled"] - delivered), 4),
            "behind_schedule": bool(live and due > 0 and delivered + _EPS < due * (1 - tol)),
        })

    scheduled_total = sum(ln["scheduled"] for ln in lines.values())
    due_total = sum(ln["due_to_date"] for ln in lines.values())
    delivered_total = sum(delivered_by_sku.get(s, 0.0) for s in lines)
    projected_delivered = projected_shortfall = None
    beyond_tolerance = None
    if live and due_total > 0:
        pace = min(1.0, delivered_total / due_total)
        projected_delivered = delivered_total + future_open * pace
        projected_shortfall = max(0.0, scheduled_total - projected_delivered)
        beyond_tolerance = projected_shortfall > scheduled_total * tol + _EPS
    elif not live and status in ("closed", "cancelled"):
        # Ended: what was not delivered is the final shortfall, no projection.
        projected_delivered = delivered_total
        projected_shortfall = max(0.0, scheduled_total - delivered_total)
        beyond_tolerance = projected_shortfall > scheduled_total * tol + _EPS

    next_release = next((e for e in out_releases
                         if e["date"] >= today.isoformat()
                         and e["state"] not in ("fulfilled", "cancelled")), None)
    return {
        "scheduled_total": round(scheduled_total, 4),
        "due_to_date": round(due_total, 4),
        "delivered": round(delivered_total, 4),
        "remaining": round(max(0.0, scheduled_total - delivered_total), 4),
        "progress_pct": (round(min(100.0, delivered_total / scheduled_total * 100), 1)
                         if scheduled_total > 0 else None),
        "behind_schedule": any(ln["behind_schedule"] for ln in line_out),
        "projected_delivered": None if projected_delivered is None else round(projected_delivered, 4),
        "projected_shortfall": None if projected_shortfall is None else round(projected_shortfall, 4),
        "shortfall_beyond_tolerance": beyond_tolerance,
        "overdue_count": len(overdue),
        "overdue_units": round(sum(e["quantity"] for e in overdue), 4),
        "unmaterialised_due": sum(1 for e in out_releases if live and e["state"] == "missing"),
        "next_release": next_release,
        "lines": line_out,
        "releases": out_releases,
    }


# ── Warehouse scope (pure over the resolved names) ───────────────────────────
# Same rule as every warehouse-dimensioned row: a user limited to some
# warehouses sees and writes only contracts naming one of them. A contract
# with no warehouse is company-wide, so only a company-wide user has it.

def visible(scope: Optional[frozenset], warehouse_name: Optional[str]) -> bool:
    if scope is None:
        return True
    if not (warehouse_name or "").strip():
        return False
    return _fold(warehouse_name) in {_fold(n) for n in scope}


def require_writable(scope: Optional[frozenset], warehouse_name: Optional[str]) -> None:
    if scope is None:
        return
    if not (warehouse_name or "").strip():
        raise AppError("supply_contract_scope_company_wide",
                       "A contract for every warehouse is only for users who see every warehouse",
                       status_code=403)
    if not visible(scope, warehouse_name):
        raise AppError("warehouse_out_of_scope",
                       "This user is not allowed to work with that warehouse.",
                       status_code=403, params={"warehouse": warehouse_name})


# ── The ledger ───────────────────────────────────────────────────────────────

_COLS = """s.id, s.root_id, s.revision, s.customer, s.reference, s.lines,
           s.period_start, s.period_end, s.schedule_kind, s.releases,
           s.tolerance_pct, s.status, s.warehouse_id, s.on_top_of_base, s.note,
           s.created_by, s.created_at, s.superseded_by, s.superseded_at,
           s.notice_days, s.auto_renew, s.renewal_lead_days, s.renewed_from_root_id,
           w.name AS warehouse_name,
           COALESCE(NULLIF(u.full_name, ''), split_part(u.email, '@', 1)) AS created_by_name"""

_FROM = """FROM supply_contracts s
           LEFT JOIN warehouses w ON w.id = s.warehouse_id AND w.tenant_id = s.tenant_id
           LEFT JOIN users u ON u.id = s.created_by"""


def _terms(row: dict) -> dict:
    """A stored row back into the shape `expand_releases` reads."""
    releases = None
    if row.get("releases") is not None:
        releases = [{"sku": r["sku"], "date": _as_date(r["date"], "date"),
                     "quantity": float(r["quantity"])} for r in row["releases"]]
    return {
        "customer": row["customer"], "reference": row.get("reference"),
        "lines": [dict(ln) for ln in (row.get("lines") or [])],
        "period_start": _as_date(row["period_start"], "period_start"),
        "period_end": _as_date(row["period_end"], "period_end"),
        "schedule_kind": row["schedule_kind"], "releases": releases,
        "tolerance_pct": float(row.get("tolerance_pct") or 0.0),
        "warehouse_id": row.get("warehouse_id"),
        "on_top_of_base": bool(row.get("on_top_of_base", True)),
        "note": row.get("note"),
        "notice_days": row.get("notice_days"),
        "auto_renew": bool(row.get("auto_renew", False)),
        "renewal_lead_days": (None if row.get("renewal_lead_days") is None
                              else [int(x) for x in row["renewal_lead_days"]]),
        "renewed_from_root_id": row.get("renewed_from_root_id"),
    }


def _fmt(row: dict) -> dict:
    d = dict(row)
    for k in ("period_start", "period_end", "created_at", "superseded_at"):
        d[k] = _iso(d.get(k))
    d["lines"] = list(d.get("lines") or [])
    return d


def _warehouse_name(tenant_id: str, warehouse_id: Optional[str], conn=None) -> Optional[str]:
    if not warehouse_id:
        return None
    row = query_one("SELECT name FROM warehouses WHERE id = %s AND tenant_id = %s",
                    (warehouse_id, tenant_id), conn=conn)
    if not row:
        raise AppError("supply_contract_warehouse_unknown",
                       "That warehouse does not exist", status_code=404,
                       params={"warehouse_id": warehouse_id})
    return row["name"]


def _current(tenant_id: str, root_id: str, conn=None, lock: bool = False) -> dict:
    row = query_one(
        f"""SELECT s.* FROM supply_contracts s
             WHERE s.tenant_id = %s AND s.root_id = %s AND s.superseded_by IS NULL
             {'FOR UPDATE' if lock else ''}""", (tenant_id, root_id), conn=conn)
    if not row:
        raise AppError("supply_contract_not_found", "Contract not found", status_code=404)
    return row


def _insert_revision(conn, new_id: str, tenant_id: str, user_id: str, root_id: str,
                     revision: int, c: dict, status: str) -> None:
    releases = None
    if c.get("releases") is not None:
        releases = [{"sku": r["sku"], "date": _iso(r["date"]), "quantity": r["quantity"]}
                    for r in c["releases"]]
    query_one(
        """INSERT INTO supply_contracts
               (id, tenant_id, root_id, revision, customer, reference, lines,
                period_start, period_end, schedule_kind, releases, tolerance_pct,
                status, warehouse_id, on_top_of_base, note, created_by,
                notice_days, auto_renew, renewal_lead_days, renewed_from_root_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   %s, %s, %s, %s)
        RETURNING id""",
        (new_id, tenant_id, root_id, revision, c["customer"], c["reference"],
         psycopg2.extras.Json(c["lines"]), c["period_start"], c["period_end"],
         c["schedule_kind"], None if releases is None else psycopg2.extras.Json(releases),
         c["tolerance_pct"], status, c["warehouse_id"], c["on_top_of_base"], c["note"],
         user_id, c.get("notice_days"), bool(c.get("auto_renew")),
         c.get("renewal_lead_days"), c.get("renewed_from_root_id")), conn=conn)


def _supersede(conn, tenant_id: str, old_id: str, new_id: str) -> None:
    done = query_one(
        """UPDATE supply_contracts SET superseded_by = %s, superseded_at = NOW()
            WHERE id = %s AND tenant_id = %s AND superseded_by IS NULL RETURNING id""",
        (new_id, old_id, tenant_id), conn=conn)
    if not done:
        raise AppError("supply_contract_stale",
                       "Somebody changed this contract meanwhile; reload it and try again",
                       status_code=409)


def _withdraw_open(conn, tenant_id: str, root_id: str, user_id: str) -> int:
    rows = query(
        """UPDATE committed_demand
              SET status = 'cancelled', contract_withdrawn_at = NOW(),
                  status_changed_by = %s, status_changed_at = NOW(), updated_at = NOW()
            WHERE tenant_id = %s AND contract_root_id = %s AND status = 'open'
              AND contract_withdrawn_at IS NULL
        RETURNING id""", (user_id, tenant_id, root_id), conn=conn)
    return len(rows)


def _longest_lead(tenant_id: str, skus: list[str], conn=None) -> float:
    if not skus:
        return 0.0
    row = query_one(
        """SELECT GREATEST(
                  COALESCE((SELECT MAX(lead_time_days) FROM inventory_stock
                             WHERE tenant_id = %s AND sku = ANY(%s)), 0),
                  COALESCE((SELECT MAX(lead_time_days) FROM sku_suppliers
                             WHERE tenant_id = %s AND sku = ANY(%s)), 0)) AS lead""",
        (tenant_id, skus, tenant_id, skus), conn=conn)
    return float((row or {}).get("lead") or 0.0)


_INSERT_COMMITMENT = """
    INSERT INTO committed_demand
        (tenant_id, sku, warehouse_id, delivery_date, quantity, customer,
         probability, on_top_of_base, note, created_by, source, contract_id,
         contract_root_id, contract_release_date)
    VALUES (%s, %s, %s, %s, %s, %s, 1.0, %s, %s, %s, 'contract', %s, %s, %s)
    ON CONFLICT (tenant_id, contract_root_id, sku, contract_release_date)
        WHERE contract_root_id IS NOT NULL AND contract_withdrawn_at IS NULL
    DO NOTHING
    RETURNING id"""


def _materialise(conn, tenant_id: str, row: dict, today: date) -> int:
    """Insert the commitments the current revision `row` is owed. Returns how
    many were created. A no-op unless the contract is active."""
    if row["status"] != "active":
        return 0
    terms = _terms(row)
    releases = expand_releases(terms)
    horizon = materialise_horizon_days(
        _longest_lead(tenant_id, [ln["sku"] for ln in terms["lines"]], conn=conn))
    existing = {(r["sku"], r["contract_release_date"]) for r in query(
        """SELECT sku, contract_release_date FROM committed_demand
            WHERE tenant_id = %s AND contract_root_id = %s
              AND contract_withdrawn_at IS NULL""",
        (tenant_id, row["root_id"]), conn=conn)}
    created = 0
    note = row.get("reference")
    for r in releases_to_materialise(releases, existing, today, horizon):
        got = query_one(_INSERT_COMMITMENT, (
            tenant_id, r["sku"], row["warehouse_id"], r["date"], r["quantity"],
            row["customer"], row["on_top_of_base"], note, row["created_by"],
            row["id"], row["root_id"], r["date"]), conn=conn)
        if got:
            created += 1
    return created


def preview(raw: dict) -> dict:
    """The releases a set of terms produces, without saving anything."""
    c = clean_contract(raw)
    releases = expand_releases(c)
    return {"releases": [{"sku": r["sku"], "date": r["date"].isoformat(),
                          "quantity": r["quantity"]} for r in releases],
            "lines": [{"sku": ln["sku"], "total_quantity": ln["total_quantity"]}
                      for ln in c["lines"]]}


def create(tenant_id: str, user_id: str, scope: Optional[frozenset], raw: dict,
           status: str = "draft") -> dict:
    if status not in CREATE_STATUSES:
        raise AppError("supply_contract_status_invalid",
                       "A new contract starts as a draft or active", params={"status": status})
    c = clean_contract(raw)
    name = _warehouse_name(tenant_id, c["warehouse_id"])
    require_writable(scope, name)
    new_id = uuid.uuid4().hex                      # the first revision names the lineage
    with transaction() as conn:
        _insert_revision(conn, new_id, tenant_id, user_id, new_id, 1, c, status)
        row = _current(tenant_id, new_id, conn=conn)
        created = _materialise(conn, tenant_id, row, date.today())
    out = get(tenant_id, scope, new_id)
    out["materialised"] = created
    return out


def revise(tenant_id: str, user_id: str, scope: Optional[frozenset], root_id: str,
           expected_revision: int, raw: dict) -> dict:
    """New terms for a draft or active contract: a new revision, the open
    commitments of the old terms withdrawn, the new ones materialised - all in
    one transaction, so a failure leaves the old revision fully in place."""
    c = clean_contract(raw)
    new_name = _warehouse_name(tenant_id, c["warehouse_id"])
    with transaction() as conn:
        cur = _current(tenant_id, root_id, conn=conn, lock=True)
        # A form that does not know the renewal fields must not wipe them: what
        # the request did not send stays as the current revision has it.
        for k in ("notice_days", "auto_renew", "renewal_lead_days"):
            if k not in raw:
                c[k] = _terms(cur)[k]
        c["renewed_from_root_id"] = cur.get("renewed_from_root_id")
        require_writable(scope, _warehouse_name(tenant_id, cur["warehouse_id"], conn=conn))
        require_writable(scope, new_name)
        if int(expected_revision) != int(cur["revision"]):
            raise AppError("supply_contract_stale",
                           "Somebody changed this contract meanwhile; reload it and try again",
                           status_code=409, params={"revision": cur["revision"]})
        if cur["status"] not in REVISABLE:
            raise AppError("supply_contract_final",
                           "A closed or cancelled contract cannot be changed",
                           status_code=409, params={"status": cur["status"]})
        new_id = uuid.uuid4().hex
        _supersede(conn, tenant_id, cur["id"], new_id)
        # Supersede FIRST: the current-revision unique index would refuse the
        # new row while the old one is still current. Both are one transaction.
        _insert_revision(conn, new_id, tenant_id, user_id, root_id,
                         int(cur["revision"]) + 1, c, cur["status"])
        withdrawn = _withdraw_open(conn, tenant_id, root_id, user_id)
        row = _current(tenant_id, root_id, conn=conn)
        created = _materialise(conn, tenant_id, row, date.today())
    out = get(tenant_id, scope, root_id)
    out.update({"withdrawn": withdrawn, "materialised": created})
    return out


def set_status(tenant_id: str, user_id: str, scope: Optional[frozenset], root_id: str,
               expected_revision: int, status: str) -> dict:
    """Activate, close or cancel: a new revision carrying the same terms.
    Closing and cancelling withdraw the open commitments; activating
    materialises the near releases."""
    if status not in STATUSES:
        raise AppError("supply_contract_status_invalid", "Unknown status",
                       params={"status": status})
    with transaction() as conn:
        cur = _current(tenant_id, root_id, conn=conn, lock=True)
        require_writable(scope, _warehouse_name(tenant_id, cur["warehouse_id"], conn=conn))
        if int(expected_revision) != int(cur["revision"]):
            raise AppError("supply_contract_stale",
                           "Somebody changed this contract meanwhile; reload it and try again",
                           status_code=409, params={"revision": cur["revision"]})
        if status not in TRANSITIONS[cur["status"]]:
            raise AppError("supply_contract_transition_invalid",
                           "A contract cannot move to that status from its current one",
                           status_code=409, params={"from": cur["status"], "to": status})
        new_id = uuid.uuid4().hex
        _supersede(conn, tenant_id, cur["id"], new_id)
        _insert_revision(conn, new_id, tenant_id, user_id, root_id,
                         int(cur["revision"]) + 1, _terms(cur), status)
        withdrawn = 0
        if status in ("closed", "cancelled"):
            withdrawn = _withdraw_open(conn, tenant_id, root_id, user_id)
        row = _current(tenant_id, root_id, conn=conn)
        created = _materialise(conn, tenant_id, row, date.today())
    out = get(tenant_id, scope, root_id)
    out.update({"withdrawn": withdrawn, "materialised": created})
    return out


# ── Reading ──────────────────────────────────────────────────────────────────

def _lineage_commitments(tenant_id: str, root_ids: Optional[list[str]] = None) -> dict:
    clauses = ["tenant_id = %s", "contract_root_id IS NOT NULL", "contract_withdrawn_at IS NULL"]
    params: list = [tenant_id]
    if root_ids is not None:
        clauses.append("contract_root_id = ANY(%s)")
        params.append(root_ids)
    rows = query(
        f"""SELECT contract_root_id, sku, contract_release_date, quantity, status
              FROM committed_demand WHERE {' AND '.join(clauses)}""", tuple(params))
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["contract_root_id"], []).append(dict(r))
    return out


def _lead_by_sku(tenant_id: str) -> dict[str, float]:
    rows = query(
        """SELECT sku, MAX(lead_time_days) AS lead FROM (
               SELECT sku, lead_time_days FROM inventory_stock WHERE tenant_id = %s
               UNION ALL
               SELECT sku, lead_time_days FROM sku_suppliers
                WHERE tenant_id = %s AND lead_time_days IS NOT NULL) x
            GROUP BY sku""", (tenant_id, tenant_id))
    return {r["sku"]: float(r["lead"] or 0.0) for r in rows}


def _with_progress(row: dict, commitments: list[dict], leads: dict[str, float],
                   today: date) -> dict:
    terms = _terms(row)
    horizon = materialise_horizon_days(
        max([leads.get(ln["sku"], 0.0) for ln in terms["lines"]] or [0.0]))
    out = _fmt(row)
    out["progress"] = contract_progress(expand_releases(terms), commitments, today,
                                        terms["tolerance_pct"], horizon, row["status"])
    out["horizon_days"] = horizon
    out["period_ended"] = terms["period_end"] < today
    lead = renewal.effective_lead_days(terms["renewal_lead_days"])
    out["renewal_lead_days_effective"] = lead
    out["renewal_lead_days_is_default"] = terms["renewal_lead_days"] is None
    out["renewal"] = (renewal.renewal_view(terms["period_end"], terms["notice_days"],
                                            terms["auto_renew"], lead, today)
                      if row["status"] == "active" else None)
    return out


def list_contracts(tenant_id: str, scope: Optional[frozenset],
                   status: Optional[str] = None) -> list[dict]:
    clauses, params = ["s.tenant_id = %s", "s.superseded_by IS NULL"], [tenant_id]
    if status:
        if status not in STATUSES:
            raise AppError("supply_contract_status_invalid", "Unknown status",
                           params={"status": status})
        clauses.append("s.status = %s")
        params.append(status)
    rows = query(f"SELECT {_COLS} {_FROM} WHERE {' AND '.join(clauses)} "
                 "ORDER BY s.period_start, s.created_at", tuple(params))
    rows = [r for r in rows if visible(scope, r.get("warehouse_name"))]
    if not rows:
        return []
    commitments = _lineage_commitments(tenant_id, [r["root_id"] for r in rows])
    leads = _lead_by_sku(tenant_id)
    today = date.today()
    return [_with_progress(r, commitments.get(r["root_id"], []), leads, today) for r in rows]


def get(tenant_id: str, scope: Optional[frozenset], root_id: str) -> dict:
    """The current revision with its progress and the whole revision history."""
    revisions = query(f"SELECT {_COLS} {_FROM} WHERE s.tenant_id = %s AND s.root_id = %s "
                      "ORDER BY s.revision", (tenant_id, root_id))
    current = next((r for r in revisions if r["superseded_by"] is None), None)
    if current is None:
        raise AppError("supply_contract_not_found", "Contract not found", status_code=404)
    if not visible(scope, current.get("warehouse_name")):
        require_writable(scope, current.get("warehouse_name"))   # raises the right 403
    commitments = _lineage_commitments(tenant_id, [root_id]).get(root_id, [])
    out = _with_progress(current, commitments, _lead_by_sku(tenant_id), date.today())
    out["revisions"] = [
        {"id": r["id"], "revision": r["revision"], "status": r["status"],
         "created_by": r["created_by"], "created_by_name": r.get("created_by_name"),
         "created_at": _iso(r["created_at"])} for r in revisions]
    return out


# ── The daily pass ───────────────────────────────────────────────────────────

def materialise_active(tenant_id: Optional[str] = None, today: Optional[date] = None) -> dict:
    """Materialise the releases now inside the horizon for every active
    contract (of one tenant, or all). One transaction per contract, so one
    failure never stops the others; each failure is logged with its contract,
    and the contract screen shows the releases still missing
    (`unmaterialised_due`) instead of pretending they exist."""
    today = today or date.today()
    clauses, params = ["superseded_by IS NULL", "status = 'active'"], []
    if tenant_id:
        clauses.append("tenant_id = %s")
        params.append(tenant_id)
    targets = query(f"SELECT tenant_id, root_id FROM supply_contracts "
                    f"WHERE {' AND '.join(clauses)}", tuple(params))
    summary = {"contracts": len(targets), "created": 0, "failed": 0}
    for t in targets:
        try:
            with transaction() as conn:
                row = _current(t["tenant_id"], t["root_id"], conn=conn, lock=True)
                summary["created"] += _materialise(conn, t["tenant_id"], row, today)
        except Exception as e:  # noqa: BLE001 - one contract must not stop the pass
            summary["failed"] += 1
            log.error("Contract materialisation failed tenant=%s contract=%s: %s",
                      t["tenant_id"], t["root_id"], e, exc_info=True)
    return summary


def run_daily_contract_materialisation() -> dict:
    summary = materialise_active()
    log.info("Contract materialisation: %s", summary)
    return summary
