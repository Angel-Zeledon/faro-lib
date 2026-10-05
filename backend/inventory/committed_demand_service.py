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
           c.created_at, c.updated_at, c.status_changed_by, c.status_changed_at"""


def _fmt(row: dict, today: Optional[date] = None) -> dict:
    d = dict(row)
    delivery = d.get("delivery_date")
    today = today or date.today()
    d["overdue"] = bool(d.get("status") == "open" and delivery is not None and delivery < today)
    for k in ("delivery_date", "created_at", "updated_at", "status_changed_at"):
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
                    status: Optional[str] = None, limit: int = 500) -> list[dict]:
    clauses, params = ["c.tenant_id = %s"], [tenant_id]
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
    get(tenant_id, commitment_id)
    execute(
        """UPDATE committed_demand
              SET status = %s, status_changed_by = %s, status_changed_at = NOW(),
                  updated_at = NOW()
            WHERE id = %s AND tenant_id = %s""",
        (status, user_id, commitment_id, tenant_id))
    return get(tenant_id, commitment_id)


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
