"""Committed demand: orders customers have already placed, or promised, ahead of time.

A large customer orders months, sometimes years, in advance. The statistical
forecast cannot see that: it extrapolates history, so a signed order for 4,000
units due in ten weeks is invisible to it until the sales arrive. This ledger
lets a person record those orders (SKU, delivery date, quantity, customer, how
sure it is) and lets the purchase recommendation plan against them.

Rules that keep it honest, the same ones the manual adjustments follow:

1. **Transparent.** A commitment reaches the recommendation through the one place
   demand is decided (`inventory/service.py`, beside events and adjustments) and
   the row names every commitment that moved it (`committed_applied`: customer,
   date, units). Nothing moves silently.
2. **Units, not a rate.** What counts is `quantity x probability` of the
   commitments due inside the protection interval (lead time plus review
   period). The service turns those units into a per-period rate over that same
   interval, so `rate x interval` returns exactly the committed units and the
   breakdown the buyer redoes by hand still adds up.
3. **On top of the baseline, unless said otherwise.** A commitment is added to
   the statistical forecast. If the person knows the history already contains
   that customer's demand, they mark it `on_top_of_base = False` and it is kept
   for the record but never added (counting it twice would over-buy).
4. **No data, no change.** With no open commitments every function here returns
   the neutral value and the product behaves exactly as before.
5. **Overdue is not forgotten.** An open commitment whose delivery date has
   passed still counts (the customer still expects it) and is flagged `overdue`
   so somebody closes it as fulfilled or cancelled instead of it lingering.
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Optional

from backend.db.connection import execute, query, query_one, transaction
from backend.errors import AppError

log = logging.getLogger(__name__)

STATUSES = ("open", "fulfilled", "cancelled")
MAX_NOTE_LENGTH = 300
MAX_CUSTOMER_LENGTH = 200
# One commitment cannot claim more than this many units (a typo guard, not a
# business limit: 1e9 is more than any catalogue sells).
MAX_QUANTITY = 1e9
# Furthest a delivery date may sit from today. Corporate contracts run for
# years; ten is generous and still catches a year typed as 2206.
MAX_YEARS_AHEAD = 10
# Most rows one bulk call accepts, so a pasted file cannot hold a transaction open.
MAX_BULK_ROWS = 1000


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _as_date(value, field: str) -> date:
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except ValueError:
        raise AppError("date_invalid_iso", f"{field} must be an ISO date (YYYY-MM-DD)",
                       params={"field": field})


def _clean(*, sku, delivery_date, quantity, customer, probability, warehouse_id,
           on_top_of_base, note, today: Optional[date] = None) -> dict:
    """Validate one commitment's fields and return them normalised."""
    today = today or date.today()
    sku = (sku or "").strip()
    if not sku:
        raise AppError("committed_demand_sku_required", "Choose a product")
    delivery = _as_date(delivery_date, "delivery_date")
    if delivery > today + timedelta(days=365 * MAX_YEARS_AHEAD):
        raise AppError("committed_demand_date_too_far",
                       "That delivery date is more than ten years away",
                       params={"delivery_date": delivery.isoformat()})
    try:
        qty = float(quantity)
    except (TypeError, ValueError):
        raise AppError("committed_demand_quantity_invalid", "Quantity must be a number")
    if not math.isfinite(qty) or qty <= 0 or qty > MAX_QUANTITY:
        raise AppError("committed_demand_quantity_invalid",
                       "Quantity must be greater than zero", params={"quantity": quantity})
    try:
        prob = 1.0 if probability is None else float(probability)
    except (TypeError, ValueError):
        raise AppError("committed_demand_probability_invalid",
                       "Probability must be a number between 0 and 1")
    if not math.isfinite(prob) or not 0 < prob <= 1:
        raise AppError("committed_demand_probability_invalid",
                       "Probability must be above 0 and at most 1",
                       params={"probability": probability})
    return {
        "sku": sku,
        "delivery_date": delivery,
        "quantity": qty,
        "customer": (customer or "").strip()[:MAX_CUSTOMER_LENGTH] or None,
        "probability": prob,
        "warehouse_id": (warehouse_id or "").strip() or None,
        "on_top_of_base": bool(True if on_top_of_base is None else on_top_of_base),
        "note": (note or "").strip()[:MAX_NOTE_LENGTH] or None,
    }


_COLS = """c.id, c.sku, c.warehouse_id, c.delivery_date, c.quantity, c.customer,
           c.probability, c.on_top_of_base, c.status, c.note, c.created_by,
           c.created_at, c.updated_at, c.status_changed_by, c.status_changed_at,
           c.source, c.contract_id, c.contract_root_id, c.contract_release_date,
           c.contract_withdrawn_at"""

# Fields a commitment materialised from a blanket contract takes from the
# contract. Changing them belongs on the contract (a revision); on the row they
# would also desynchronise its release key. Quantity, date and note stay
# editable: they are how a person records the call-off that really happened.
CONTRACT_LOCKED_FIELDS = ("sku", "warehouse_id", "customer", "probability", "on_top_of_base")


def _fmt(row: dict, today: Optional[date] = None) -> dict:
    d = dict(row)
    delivery = d.get("delivery_date")
    today = today or date.today()
    d["overdue"] = bool(d.get("status") == "open" and delivery is not None and delivery < today)
    for k in ("delivery_date", "created_at", "updated_at", "status_changed_at",
              "contract_release_date", "contract_withdrawn_at"):
        d[k] = _iso(d.get(k))
    return d


def _check_warehouse(tenant_id: str, warehouse_id: Optional[str]) -> None:
    if not warehouse_id:
        return
    if not query_one("SELECT 1 FROM warehouses WHERE id = %s AND tenant_id = %s",
                     (warehouse_id, tenant_id)):
        raise AppError("committed_demand_warehouse_unknown",
                       "That warehouse does not exist", status_code=404,
                       params={"warehouse_id": warehouse_id})


_INSERT = """INSERT INTO committed_demand
                 (tenant_id, sku, warehouse_id, delivery_date, quantity, customer,
                  probability, on_top_of_base, note, created_by)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id"""


def _insert(conn, tenant_id: str, user_id: str, c: dict) -> str:
    row = query_one(_INSERT, (
        tenant_id, c["sku"], c["warehouse_id"], c["delivery_date"], c["quantity"],
        c["customer"], c["probability"], c["on_top_of_base"], c["note"], user_id,
    ), conn=conn)
    return row["id"]


def create(tenant_id: str, user_id: str, *, sku, delivery_date, quantity,
           customer=None, probability=1.0, warehouse_id=None,
           on_top_of_base=True, note=None) -> dict:
    c = _clean(sku=sku, delivery_date=delivery_date, quantity=quantity, customer=customer,
               probability=probability, warehouse_id=warehouse_id,
               on_top_of_base=on_top_of_base, note=note)
    _check_warehouse(tenant_id, c["warehouse_id"])
    with transaction() as conn:
        new_id = _insert(conn, tenant_id, user_id, c)
    return get(tenant_id, new_id)


def create_many(tenant_id: str, user_id: str, rows: list[dict]) -> dict:
    """All-or-nothing bulk entry (a corporate customer arrives with a file, not a
    form). Every row is validated BEFORE anything is written and the refusal names
    each bad row, so one typo on row 140 never leaves 139 half-imported."""
    if not rows:
        raise AppError("committed_demand_bulk_empty", "There are no rows to import")
    if len(rows) > MAX_BULK_ROWS:
        raise AppError("committed_demand_bulk_too_large",
                       "Import at most 1,000 rows at a time",
                       params={"max": MAX_BULK_ROWS, "rows": len(rows)})
    cleaned, errors = [], []
    for i, raw in enumerate(rows, start=1):
        try:
            cleaned.append(_clean(
                sku=raw.get("sku"), delivery_date=raw.get("delivery_date"),
                quantity=raw.get("quantity"), customer=raw.get("customer"),
                probability=raw.get("probability"), warehouse_id=raw.get("warehouse_id"),
                on_top_of_base=raw.get("on_top_of_base"), note=raw.get("note")))
        except AppError as e:
            errors.append({"row": i, "code": e.code, "params": e.params or {}})
    if errors:
        raise AppError("committed_demand_bulk_invalid",
                       "Some rows are not valid; nothing was imported",
                       status_code=422, params={"errors": errors[:50], "bad_rows": len(errors)})
    for w in {c["warehouse_id"] for c in cleaned if c["warehouse_id"]}:
        _check_warehouse(tenant_id, w)
    with transaction() as conn:
        ids = [_insert(conn, tenant_id, user_id, c) for c in cleaned]
    return {"created": len(ids), "ids": ids}


def get(tenant_id: str, commitment_id: str) -> dict:
    row = query_one(f"SELECT {_COLS} FROM committed_demand c WHERE c.id = %s AND c.tenant_id = %s",
                    (commitment_id, tenant_id))
    if not row:
        raise AppError("committed_demand_not_found", "Commitment not found", status_code=404)
    return _fmt(row)


def list_for_tenant(tenant_id: str, *, sku: Optional[str] = None,
                    status: Optional[str] = None, limit: int = 500,
                    warehouse_ids: Optional[frozenset] = None) -> list[dict]:
    """`warehouse_ids`: None = every commitment (a company-wide caller); a set =
    only commitments naming one of those warehouses — unassigned (company-wide)
    commitments are excluded, and an empty set returns nothing."""
    clauses, params = ["c.tenant_id = %s"], [tenant_id]
    if warehouse_ids is not None:
        if not warehouse_ids:
            return []
        clauses.append("c.warehouse_id = ANY(%s)")
        params.append(sorted(warehouse_ids))
    if sku:
        clauses.append("c.sku = %s")
        params.append(sku)
    if status:
        if status not in STATUSES:
            raise AppError("committed_demand_status_invalid", "Unknown status",
                           params={"status": status})
        clauses.append("c.status = %s")
        params.append(status)
    rows = query(
        f"""SELECT {_COLS} FROM committed_demand c
             WHERE {' AND '.join(clauses)}
             ORDER BY c.delivery_date, c.created_at LIMIT %s""",
        tuple(params + [max(1, min(int(limit), 2000))]))
    today = date.today()
    return [_fmt(r, today) for r in rows]


def update(tenant_id: str, commitment_id: str, user_id: str, **fields) -> dict:
    """Edit an OPEN commitment. A closed one is history and is not rewritten."""
    current = get(tenant_id, commitment_id)
    if current["status"] != "open":
        raise AppError("committed_demand_closed",
                       "A fulfilled or cancelled commitment cannot be edited",
                       status_code=409, params={"status": current["status"]})
    if current.get("source") == "contract":
        locked = [k for k in CONTRACT_LOCKED_FIELDS
                  if k in fields and fields[k] != current.get(k)]
        if locked:
            raise AppError("committed_demand_contract_locked",
                           "This commitment comes from a contract; change the contract instead",
                           status_code=409, params={"field": locked[0]})
    merged = {
        "sku": current["sku"], "delivery_date": current["delivery_date"],
        "quantity": current["quantity"], "customer": current["customer"],
        "probability": current["probability"], "warehouse_id": current["warehouse_id"],
        "on_top_of_base": current["on_top_of_base"], "note": current["note"],
    }
    merged.update({k: v for k, v in fields.items() if k in merged})
    c = _clean(**merged)
    _check_warehouse(tenant_id, c["warehouse_id"])
    execute(
        """UPDATE committed_demand
              SET sku = %s, warehouse_id = %s, delivery_date = %s, quantity = %s,
                  customer = %s, probability = %s, on_top_of_base = %s, note = %s,
                  updated_at = NOW()
            WHERE id = %s AND tenant_id = %s AND status = 'open'""",
        (c["sku"], c["warehouse_id"], c["delivery_date"], c["quantity"], c["customer"],
         c["probability"], c["on_top_of_base"], c["note"], commitment_id, tenant_id))
    return get(tenant_id, commitment_id)


def set_status(tenant_id: str, commitment_id: str, user_id: str, status: str) -> dict:
    """Close an open commitment as fulfilled or cancelled, or reopen a closed one.
    Closing is how it stops counting; the row stays so the history is whole."""
    if status not in STATUSES:
        raise AppError("committed_demand_status_invalid", "Unknown status",
                       params={"status": status})
    current = get(tenant_id, commitment_id)
    if current.get("contract_withdrawn_at"):
        # Withdrawn by a revision or the end of its contract: the release it
        # stood for now belongs to the contract's current terms.
        raise AppError("committed_demand_withdrawn",
                       "This commitment was withdrawn when its contract changed",
                       status_code=409)
    if status == "open" and current.get("source") == "contract":
        live = query_one(
            """SELECT status FROM supply_contracts
                WHERE tenant_id = %s AND root_id = %s AND superseded_by IS NULL""",
            (tenant_id, current.get("contract_root_id")))
        if not live or live["status"] != "active":
            raise AppError("committed_demand_contract_inactive",
                           "Its contract is no longer active, so it cannot be reopened",
                           status_code=409)
    execute(
        """UPDATE committed_demand
              SET status = %s, status_changed_by = %s, status_changed_at = NOW(),
                  updated_at = NOW()
            WHERE id = %s AND tenant_id = %s AND contract_withdrawn_at IS NULL""",
        (status, user_id, commitment_id, tenant_id))
    updated = get(tenant_id, commitment_id)
    if status == "fulfilled" and current.get("status") != "fulfilled":
        # Once per real transition: closing an already-fulfilled row emits nothing.
        from backend.webhooks.service import emit_commitment_event
        emit_commitment_event(tenant_id, "commitment.fulfilled", updated,
                              fulfilled_at=updated.get("status_changed_at"))
    return updated


# ── Reaching the recommendation ──────────────────────────────────────────────

def active_by_sku(tenant_id: str) -> dict[str, list[dict]]:
    """Open commitments that can add demand, keyed by SKU. One query for the
    whole tenant, never per SKU. Rows marked as already contained in the baseline
    are left out here: they are a record, not demand to add."""
    rows = query(
        """SELECT id, sku, warehouse_id, delivery_date, quantity, customer, probability
             FROM committed_demand
            WHERE tenant_id = %s AND status = 'open' AND on_top_of_base""",
        (tenant_id,))
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["sku"], []).append(dict(r))
    return out


def committed_units(commitments: Optional[list[dict]], today: date, window_days: float,
                    warehouse_id: Optional[str] = None,
                    share: float = 1.0) -> tuple[float, list[dict]]:
    """Units the commitments add inside the window starting today, and one entry
    per commitment that counted.

    The window is `[today, today + window_days)`, the same half-open window the
    event and adjustment multipliers use. A commitment already overdue counts.

    Scope: in the company-wide view (`warehouse_id=None`) every commitment counts
    in full. In one warehouse's view a commitment naming that warehouse counts in
    full, one naming ANOTHER warehouse does not count, and an unassigned one is
    split by `share` (the warehouse's share of the SKU's demand, the same split
    its forecast already gets).
    """
    if not commitments or window_days <= 0:
        return 0.0, []
    window_end = today + timedelta(days=window_days)
    total, applied = 0.0, []
    for c in commitments:
        delivery = c["delivery_date"]
        if delivery >= window_end:
            continue
        c_wh = c.get("warehouse_id")
        if warehouse_id is None:
            fraction, scope = 1.0, ("warehouse" if c_wh else "company")
        elif c_wh == warehouse_id:
            fraction, scope = 1.0, "warehouse"
        elif c_wh:
            continue
        else:
            fraction, scope = max(0.0, min(1.0, float(share))), "shared"
        units = float(c["quantity"]) * float(c["probability"]) * fraction
        if units <= 0:
            continue
        total += units
        applied.append({
            "commitment_id": c["id"],
            "customer": c.get("customer"),
            "delivery_date": _iso(delivery),
            "quantity": round(float(c["quantity"]), 2),
            "probability": round(float(c["probability"]), 4),
            "units": round(units, 2),
            "scope": scope,
            "overdue": delivery < today,
        })
    return total, applied


def extra_rate(units: float, protection_periods: float) -> float:
    """The per-period demand that, over the protection interval, adds exactly
    `units`. Added to the effective demand rate so coverage, reorder point,
    signal and quantity all see the same number."""
    if units <= 0 or protection_periods <= 0:
        return 0.0
    return units / float(protection_periods)


# ── A commitment on a SKU with no forecast or no stock row ───────────────────

def cover_without_forecast(commitments: Optional[list[dict]], today: date,
                           lead_time_days: float, review_period_days: float,
                           stock: Optional[float], incoming: float,
                           moq: float = 1.0, warehouse_id: Optional[str] = None,
                           share: float = 1.0) -> dict:
    """What a SKU's open commitments need when the statistical forecast (or the
    stock row) is missing, so the semaphore branch cannot run.

    No forecast is invented: only the committed units inside the protection
    interval are compared with what is on hand plus on its way, earliest date
    first (the same allocation `allocate_risk` uses).

    Returns `units`, `applied` (the `committed_units` entries), `shortfall`,
    `signal` and `recommended`. `signal` is None when nothing needs ordering (the
    caller keeps its own signal), `PEDIR_YA` when the first commitment the stock
    cannot cover is due before a new order could arrive (inside the lead time or
    already overdue), `PEDIR_PRONTO` otherwise. `stock_unknown` is true when no
    stock figure exists: it is then counted as zero for the signal, but no
    quantity is recommended, because how much to buy is a function of how much is
    left and nobody told us.
    """
    window = float(lead_time_days) + max(0.0, float(review_period_days or 0.0))
    units, applied = committed_units(commitments, today, window, warehouse_id, share)
    out = {"units": round(units, 2), "applied": applied, "shortfall": 0.0,
           "signal": None, "recommended": None, "stock_unknown": stock is None}
    if units <= 0:
        return out
    available = max(0.0, float(stock or 0.0)) + max(0.0, float(incoming or 0.0))
    shortfall = max(0.0, units - available)
    if shortfall <= 1e-9:
        return out
    out["shortfall"] = round(shortfall, 2)
    cutoff = today + timedelta(days=float(lead_time_days))
    running, urgent = 0.0, False
    for a in sorted(applied, key=lambda e: e["delivery_date"]):
        running += a["units"]
        if running > available + 1e-9:
            urgent = date.fromisoformat(a["delivery_date"]) < cutoff
            break
    out["signal"] = "PEDIR_YA" if urgent else "PEDIR_PRONTO"
    if stock is not None:
        out["recommended"] = float(math.ceil(max(shortfall, float(moq or 0.0))))
    return out


def demand_buckets_by_warehouse(commitments: Optional[list[dict]], today: date,
                                horizon_days: float, days_per_period: float,
                                horizon_buckets: int, warehouses: list[str],
                                shares: Optional[dict[str, float]],
                                default_warehouse: str,
                                warehouse_ids: Optional[dict[str, str]] = None,
                                ) -> dict[str, list[float]]:
    """Committed units laid into the optimizer's buckets, per warehouse.

    Reuses `committed_units` for every rule about WHAT counts (window, scope,
    probability, overdue), so the optimizer and the Panel cannot disagree. A
    commitment lands in the bucket of its delivery date (overdue ones in bucket
    0). `shares` is the demand split by warehouse; None means the forecast is
    store-keyed, where an unassigned commitment goes to `default_warehouse`
    instead of being repeated in every store. `warehouse_ids` maps a warehouse
    NAME (what the optimizer works in) to the ID a commitment names it by.
    """
    out: dict[str, list[float]] = {}
    if not commitments or horizon_buckets <= 0:
        return out
    today_ord = today.toordinal()

    def _lay(wh: str, entries: list[dict]) -> None:
        series = out.setdefault(wh, [0.0] * horizon_buckets)
        for e in entries:
            days = date.fromisoformat(e["delivery_date"]).toordinal() - today_ord
            idx = max(0, int(days // days_per_period))
            series[min(idx, horizon_buckets - 1)] += e["units"]

    taken: set = set()
    for wh in warehouses:
        _, entries = committed_units(
            commitments, today, horizon_days,
            warehouse_id=(warehouse_ids or {}).get(wh, wh),
            share=(shares or {}).get(wh, 0.0) if shares is not None else 0.0)
        taken.update(e["commitment_id"] for e in entries if e["scope"] == "warehouse")
        _lay(wh, entries)
    if shares is None and default_warehouse in warehouses:
        # Store mode: what no warehouse claimed and no store carries.
        _, company = committed_units(commitments, today, horizon_days)
        _lay(default_warehouse,
             [e for e in company if e["scope"] == "company" and e["commitment_id"] not in taken])
    return out


# ── Commitments at risk ──────────────────────────────────────────────────────

def allocate_risk(commitments: list[dict], stock: Optional[float],
                  arrivals: list[tuple[Optional[date], float]],
                  lead_time_days: float, today: date) -> dict:
    """Which of one SKU's open commitments the supply on hand will not cover.

    `commitments`: the SKU's open commitments (`id`, `delivery_date` as a date,
    `quantity`, `probability`). `arrivals`: units on their way as (date, units);
    a date of None counts as already available. Supply at a delivery date is
    stock + every arrival up to that date. Allocation is earliest date first:
    commitment i is short by `min(units_i, max(0, cumulative demand through i -
    supply at its date))`. A commitment already overdue is judged as of today.

    Units are `quantity x probability`, the same expected units the purchase
    recommendation plans on. It is a COMPANY-wide check: stock and arrivals are
    summed across warehouses and a commitment's warehouse is ignored.

    With `stock` None (no stock row) nothing can be said: every entry is
    `{"at_risk": None, ...}` rather than a guess.

    Returns {commitment_id: {at_risk, shortfall, covered_units,
    latest_safe_order_date, order_date_passed}}.
    """
    out: dict = {}
    ordered = sorted(commitments, key=lambda c: c["delivery_date"])
    if stock is None:
        for c in ordered:
            out[c["id"]] = {"at_risk": None, "shortfall": None, "covered_units": None,
                            "latest_safe_order_date": None, "order_date_passed": None}
        return out
    base = max(0.0, float(stock))
    cumulative = 0.0
    lead = int(math.ceil(max(0.0, float(lead_time_days))))
    for c in ordered:
        units = float(c["quantity"]) * float(c["probability"])
        cumulative += units
        as_of = max(c["delivery_date"], today)
        supply = base + sum(max(0.0, float(q)) for d, q in arrivals if d is None or d <= as_of)
        short = min(units, max(0.0, cumulative - supply))
        at_risk = short > 1e-9
        safe = c["delivery_date"] - timedelta(days=lead)
        out[c["id"]] = {
            "at_risk": at_risk,
            "shortfall": round(short, 2) if at_risk else 0.0,
            "covered_units": round(units - short, 2),
            "latest_safe_order_date": safe.isoformat() if at_risk else None,
            "order_date_passed": (safe < today) if at_risk else False,
        }
    return out


def summarize_by_customer(items: list[dict]) -> list[dict]:
    """One line per customer over the OPEN items: how many commitments, how many
    at risk, how many with no verdict (no stock row), units short and the
    earliest safe order date among the at-risk ones. Customers with nothing at
    risk are kept so a buyer sees who is fully covered; items with no customer
    group under None."""
    groups: dict = {}
    for it in items:
        if it.get("status") != "open":
            continue
        g = groups.setdefault(it.get("customer") or None, {
            "customer": it.get("customer") or None, "open": 0, "at_risk": 0,
            "unknown": 0, "shortfall": 0.0, "first_safe_order_date": None})
        g["open"] += 1
        if it.get("at_risk") is None:
            g["unknown"] += 1
        elif it["at_risk"]:
            g["at_risk"] += 1
            g["shortfall"] = round(g["shortfall"] + float(it.get("shortfall") or 0), 2)
            safe = it.get("latest_safe_order_date")
            if safe and (g["first_safe_order_date"] is None or safe < g["first_safe_order_date"]):
                g["first_safe_order_date"] = safe
    return sorted(groups.values(), key=lambda g: (-g["at_risk"], -g["shortfall"],
                                                  g["customer"] or "~"))


def _fold(name: Optional[str]) -> str:
    return (name or "").strip().casefold()


def annotate_risk(tenant_id: str, items: list[dict],
                  warehouse_names: Optional[frozenset] = None) -> list[dict]:
    """Add the risk verdict to `list_for_tenant` items, in place. Only OPEN items
    get one; closed ones carry `at_risk: None`. Reads stock, open purchase
    orders / transfers and the lead-time cascade ONCE for the tenant (the same
    resolvers the Panel and /planning use), never per SKU.

    Arrival dates: an open purchase order carries no promised date in this
    product, so it is assumed to land within the SKU's lead time from today (it
    was already placed, so that is the latest it should arrive); a transfer in
    transit counts as available now.

    `warehouse_names`: None = company-wide (stock and arrivals of every
    warehouse). A set = a warehouse-scoped caller: only those warehouses' stock
    and arrivals count, so the verdict never leans on stock the caller cannot
    see; a SKU with no stock row inside the scope gets no verdict (None), not
    the company's.
    """
    for i in items:
        i.update({"at_risk": None, "shortfall": None, "covered_units": None,
                  "latest_safe_order_date": None, "order_date_passed": None})
    open_items = [i for i in items if i.get("status") == "open"]
    if not open_items:
        return items
    from backend.inventory.defaults import DEFAULT_LEAD_TIME_DAYS
    from backend.inventory.optimizer_service import resolve_planning_inputs
    from backend.inventory.service import get_incoming_detail, list_stock

    today = date.today()
    stock_rows = list_stock(tenant_id)
    allowed = None if warehouse_names is None else {_fold(n) for n in warehouse_names}
    if allowed is not None:
        stock_rows = [r for r in stock_rows if _fold(r.get("warehouse")) in allowed]
    stock: dict[str, float] = {}
    for r in stock_rows:
        if r.get("sku") and r.get("current_stock") is not None:
            stock[r["sku"]] = stock.get(r["sku"], 0.0) + float(r["current_stock"])
    planning = resolve_planning_inputs(tenant_id, stock_rows)
    incoming: dict[str, list[dict]] = {}
    for d in get_incoming_detail(tenant_id):
        if allowed is not None and _fold(d.get("warehouse")) not in allowed:
            continue
        incoming.setdefault(d["sku"], []).append(d)

    by_sku: dict[str, list[dict]] = {}
    for i in open_items:
        by_sku.setdefault(i["sku"], []).append(i)
    for sku, rows in by_sku.items():
        lead = float((planning.get(sku) or {}).get("lead_time_days") or DEFAULT_LEAD_TIME_DAYS)
        arrivals = [
            (today + timedelta(days=int(math.ceil(lead))) if d["kind"] == "po" else None,
             float(d["qty"]))
            for d in incoming.get(sku, [])]
        verdicts = allocate_risk(
            [{"id": r["id"], "delivery_date": date.fromisoformat(r["delivery_date"]),
              "quantity": r["quantity"], "probability": r["probability"]} for r in rows],
            stock.get(sku), arrivals, lead, today)
        for r in rows:
            r.update(verdicts[r["id"]])
    return items
