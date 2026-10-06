"""Purchase budgets: a cap on what the company (or one warehouse, supplier or
category) spends on purchase orders in a period, and what to order first when
the recommendations cost more than what is left.

What this module does NOT do (owner's rule, 2026-10-05):
  * it creates no order and changes no recommended quantity anywhere. The
    purchase screens only ANNOTATE each line "funded within budget" or
    "deferred: budget"; the quantity on the line stays the engine's;
  * it never treats a missing cost as zero: such lines are listed apart.

The ledger is append-only (see `purchase_budget_migrations.py`): every change is
a new revision of the same lineage (`root_id`), the replaced row is only stamped
`superseded_by`. The allocation itself is the pure function in
`forecasting_core/business/budget_allocation.py`; the arithmetic that needs no
database is in `purchase_budget_math.py`.

Warehouse scope: a user limited to some warehouses sees and edits only budgets
scoped to THEIR warehouses. Company, supplier and category budgets span
warehouses they cannot see, so they do not exist for them (404, not 403).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from backend.db.connection import query, query_one, transaction
from backend.errors import AppError
from backend.inventory import purchase_budget_math as bm

MAX_AMOUNT = 1e12
MAX_NOTE_LENGTH = 1000
MAX_PARENT_DEPTH = 4
_ORDERED_STATUSES = ("approved", "modified")


# ── Cleaning ─────────────────────────────────────────────────────────────────

def _as_date(value: Any, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        raise AppError("purchase_budget_invalid", "Invalid date", status_code=422,
                       params={"field": field}) from None


def clean_terms(raw: dict, current: Optional[dict] = None) -> dict:
    """Validate the terms of a budget. `raw` may be partial when `current`
    (the row being revised) supplies the rest."""
    base = dict(current or {})
    for k, v in raw.items():
        base[k] = v
    period_type = base.get("period_type")
    if period_type not in bm.PERIOD_TYPES:
        raise AppError("purchase_budget_invalid", "Unknown period type", status_code=422,
                       params={"field": "period_type"})
    start = _as_date(base.get("period_start"), "period_start")
    end = None if period_type != "custom" else _as_date(base.get("period_end"), "period_end")
    try:
        start, end = bm.period_bounds(period_type, start, end)
    except ValueError:
        raise AppError("purchase_budget_invalid", "Invalid period", status_code=422,
                       params={"field": "period_end"}) from None
    try:
        amount = float(base.get("amount"))
    except (TypeError, ValueError):
        raise AppError("purchase_budget_invalid", "Invalid amount", status_code=422,
                       params={"field": "amount"}) from None
    if not (0 <= amount <= MAX_AMOUNT) or amount != amount:
        raise AppError("purchase_budget_invalid", "Invalid amount", status_code=422,
                       params={"field": "amount"})
    scope_type = base.get("scope_type") or "company"
    if scope_type not in bm.SCOPE_TYPES:
        raise AppError("purchase_budget_invalid", "Unknown scope", status_code=422,
                       params={"field": "scope_type"})
    scope_value = (str(base.get("scope_value")).strip() if base.get("scope_value") else None)
    if scope_type == "company":
        scope_value = None
    elif not scope_value:
        raise AppError("purchase_budget_invalid", "A scope value is required", status_code=422,
                       params={"field": "scope_value"})
    elif len(scope_value) > 200:
        raise AppError("purchase_budget_invalid", "Scope value too long", status_code=422,
                       params={"field": "scope_value"})
    note = (str(base.get("note")).strip() if base.get("note") else None) or None
    if note and len(note) > MAX_NOTE_LENGTH:
        raise AppError("purchase_budget_invalid", "Note too long", status_code=422,
                       params={"field": "note"})
    parent = (str(base.get("parent_root_id")).strip() if base.get("parent_root_id") else None) or None
    currency = str(base.get("currency") or "").strip().upper()
    if not currency or len(currency) > 8:
        raise AppError("purchase_budget_invalid", "Invalid currency", status_code=422,
                       params={"field": "currency"})
    return {
        "period_type": period_type, "period_start": start, "period_end": end,
        "amount": round(amount, 2), "currency": currency,
        "scope_type": scope_type, "scope_value": scope_value, "parent_root_id": parent,
        "hard_cap": bool(base.get("hard_cap", False)),
        "active": bool(base.get("active", True)),
        "note": note,
    }


# ── Visibility ───────────────────────────────────────────────────────────────

def visible(scope: Optional[frozenset], row: dict) -> bool:
    """`scope` = the caller's warehouse ids (None = unrestricted)."""
    if scope is None:
        return True
    return row["scope_type"] == "warehouse" and row["scope_value"] in scope


def require_writable(scope: Optional[frozenset], terms: dict, tenant_id: Optional[str] = None) -> None:
    """Refuse TERMS (a budget about to be written) outside the caller's scope.

    No object is revealed here, so the codebase's 403s apply: a warehouse the
    caller cannot use is `warehouse_out_of_scope`; a company, supplier or
    category budget governs every warehouse, so it is a company-wide setting
    (`warehouse_scope_company_setting`). An EXISTING budget outside the scope is
    a different case: it does not exist for the caller (404, see `revise`)."""
    if visible(scope, terms):
        return
    if terms["scope_type"] == "warehouse":
        from backend.auth import warehouse_scope as wscope
        row = query_one("SELECT name FROM warehouses WHERE id = %s AND tenant_id = %s",
                        (terms["scope_value"], tenant_id)) if tenant_id else None
        raise wscope.denied(row["name"] if row else terms["scope_value"])
    raise AppError(
        "warehouse_scope_company_setting",
        "Only a user with access to every warehouse can change this setting.",
        status_code=403)


# ── Reading / names ──────────────────────────────────────────────────────────

def _names(tenant_id: str, rows: list[dict]) -> dict[tuple[str, str], str]:
    wh = {r["scope_value"] for r in rows if r["scope_type"] == "warehouse"}
    sup = {r["scope_value"] for r in rows if r["scope_type"] == "supplier"}
    ccs = {r["scope_value"] for r in rows if r["scope_type"] == "cost_center"}
    out: dict[tuple[str, str], str] = {}
    if ccs:
        for r in query("SELECT id, code, name FROM cost_centers WHERE tenant_id = %s AND id = ANY(%s)",
                       (tenant_id, list(ccs))):
            out[("cost_center", r["id"])] = f"{r['code']} {r['name']}"
    if wh:
        for r in query("SELECT id, name FROM warehouses WHERE tenant_id = %s AND id = ANY(%s)",
                       (tenant_id, list(wh))):
            out[("warehouse", r["id"])] = r["name"]
    if sup:
        for r in query("SELECT id, name FROM suppliers WHERE tenant_id = %s AND id = ANY(%s)",
                       (tenant_id, list(sup))):
            out[("supplier", r["id"])] = r["name"]
    return out


def _fmt(row: dict, names: dict) -> dict:
    out = dict(row)
    for k in ("period_start", "period_end"):
        out[k] = out[k].isoformat() if hasattr(out[k], "isoformat") else out[k]
    for k in ("created_at", "superseded_at"):
        if out.get(k) is not None and hasattr(out[k], "isoformat"):
            out[k] = out[k].isoformat()
    out["scope_label"] = (names.get((row["scope_type"], row["scope_value"]))
                          if row["scope_type"] in ("warehouse", "supplier", "cost_center")
                          else row["scope_value"])
    return out


def _current_rows(tenant_id: str, conn=None, lock_root: Optional[str] = None) -> list[dict]:
    if lock_root:
        return query(
            "SELECT * FROM purchase_budgets WHERE tenant_id = %s AND root_id = %s "
            "AND superseded_by IS NULL FOR UPDATE", (tenant_id, lock_root), conn=conn)
    return query(
        "SELECT * FROM purchase_budgets WHERE tenant_id = %s AND superseded_by IS NULL "
        "ORDER BY period_start DESC, created_at DESC", (tenant_id,), conn=conn)


def _current(tenant_id: str, root_id: str, conn=None, lock: bool = False) -> dict:
    rows = (_current_rows(tenant_id, conn, lock_root=root_id) if lock else
            query("SELECT * FROM purchase_budgets WHERE tenant_id = %s AND root_id = %s "
                  "AND superseded_by IS NULL", (tenant_id, root_id), conn=conn))
    if not rows:
        raise AppError("purchase_budget_not_found", "Budget not found", status_code=404)
    return rows[0]


def get_visible(tenant_id: str, scope: Optional[frozenset], root_id: str) -> dict:
    """The current revision, or the same 404 a missing one gets."""
    row = _current(tenant_id, root_id)
    if not visible(scope, row):
        raise AppError("purchase_budget_not_found", "Budget not found", status_code=404)
    return row


def list_budgets(tenant_id: str, scope: Optional[frozenset], *,
                 include_inactive: bool = False) -> list[dict]:
    rows = [r for r in _current_rows(tenant_id) if visible(scope, r)
            and (include_inactive or r["active"])]
    names = _names(tenant_id, rows)
    return [_fmt(r, names) for r in rows]


def history(tenant_id: str, scope: Optional[frozenset], root_id: str) -> list[dict]:
    get_visible(tenant_id, scope, root_id)
    rows = query("SELECT * FROM purchase_budgets WHERE tenant_id = %s AND root_id = %s "
                 "ORDER BY revision DESC", (tenant_id, root_id))
    names = _names(tenant_id, rows)
    return [_fmt(r, names) for r in rows]


# ── Writing ──────────────────────────────────────────────────────────────────

def _validate_scope_target(tenant_id: str, terms: dict, conn=None) -> None:
    st, sv = terms["scope_type"], terms["scope_value"]
    if st == "warehouse":
        ok_ = query_one("SELECT 1 AS x FROM warehouses WHERE id = %s AND tenant_id = %s",
                        (sv, tenant_id), conn=conn)
    elif st == "supplier":
        ok_ = query_one("SELECT 1 AS x FROM suppliers WHERE id = %s AND tenant_id = %s",
                        (sv, tenant_id), conn=conn)
    elif st == "cost_center":
        # An inactive center cannot get a new budget: nothing may be ordered to it.
        ok_ = query_one("SELECT 1 AS x FROM cost_centers WHERE id = %s AND tenant_id = %s "
                        "AND active", (sv, tenant_id), conn=conn)
    else:
        ok_ = True
    if not ok_:
        raise AppError("purchase_budget_scope_not_found", "That warehouse, supplier or cost center does not exist",
                       status_code=422, params={"scope_type": st})


def _validate_parent(tenant_id: str, root_id: str, parent: Optional[str], conn=None) -> None:
    if not parent:
        return
    seen, cursor = {root_id}, parent
    for _ in range(MAX_PARENT_DEPTH + 1):
        if cursor in seen:
            raise AppError("purchase_budget_parent_invalid",
                           "A budget cannot sit inside itself", status_code=422)
        seen.add(cursor)
        row = query_one(
            "SELECT parent_root_id FROM purchase_budgets WHERE tenant_id = %s AND root_id = %s "
            "AND superseded_by IS NULL", (tenant_id, cursor), conn=conn)
        if row is None:
            raise AppError("purchase_budget_parent_invalid", "Parent budget not found",
                           status_code=422)
        cursor = row["parent_root_id"]
        if not cursor:
            return
    raise AppError("purchase_budget_parent_invalid", "Budgets are nested too deep", status_code=422)


def _check_overlap(tenant_id: str, root_id: str, terms: dict, conn) -> None:
    """Two ACTIVE budgets of the same scope may not cover the same days: the
    cap an order is measured against would be ambiguous."""
    if not terms["active"]:
        return
    clash = query_one(
        """SELECT root_id FROM purchase_budgets
            WHERE tenant_id = %s AND superseded_by IS NULL AND active
              AND root_id <> %s AND scope_type = %s
              AND scope_value IS NOT DISTINCT FROM %s
              AND period_start <= %s AND period_end >= %s""",
        (tenant_id, root_id, terms["scope_type"], terms["scope_value"],
         terms["period_end"], terms["period_start"]), conn=conn)
    if clash:
        raise AppError("purchase_budget_overlap",
                       "An active budget of the same scope already covers part of that period",
                       status_code=409, params={"root_id": clash["root_id"]})


def _insert(conn, new_id: str, tenant_id: str, user_id: str, root_id: str, revision: int,
            t: dict) -> None:
    query_one(
        """INSERT INTO purchase_budgets
               (id, tenant_id, root_id, revision, period_type, period_start, period_end,
                amount, currency, scope_type, scope_value, parent_root_id, hard_cap,
                active, note, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id""",
        (new_id, tenant_id, root_id, revision, t["period_type"], t["period_start"],
         t["period_end"], t["amount"], t["currency"], t["scope_type"], t["scope_value"],
         t["parent_root_id"], t["hard_cap"], t["active"], t["note"], user_id), conn=conn)


def _lock_tenant(conn, tenant_id: str) -> None:
    query("SELECT pg_advisory_xact_lock(hashtext(%s))", ("purchase_budgets:" + tenant_id,), conn=conn)


def create(tenant_id: str, user_id: str, scope: Optional[frozenset], raw: dict) -> dict:
    t = clean_terms(raw)
    require_writable(scope, t, tenant_id)
    if scope is not None:
        t["parent_root_id"] = None  # a scoped user cannot see a company-wide parent
    new_id = uuid.uuid4().hex
    with transaction() as conn:
        _lock_tenant(conn, tenant_id)
        _validate_scope_target(tenant_id, t, conn)
        _validate_parent(tenant_id, new_id, t["parent_root_id"], conn)
        _check_overlap(tenant_id, new_id, t, conn)
        _insert(conn, new_id, tenant_id, user_id, new_id, 1, t)
    return list_one(tenant_id, scope, new_id)


def revise(tenant_id: str, user_id: str, scope: Optional[frozenset], root_id: str,
           expected_revision: int, changes: dict) -> dict:
    """New terms: a new revision, the old one stamped, both in one transaction."""
    with transaction() as conn:
        _lock_tenant(conn, tenant_id)
        cur = _current(tenant_id, root_id, conn=conn, lock=True)
        if not visible(scope, cur):  # out of scope = does not exist for this caller
            raise AppError("purchase_budget_not_found", "Budget not found", status_code=404)
        if int(expected_revision) != int(cur["revision"]):
            raise AppError("purchase_budget_stale",
                           "Somebody changed this budget meanwhile; reload it and try again",
                           status_code=409, params={"revision": cur["revision"]})
        if scope is not None:
            changes = {k: v for k, v in changes.items() if k != "parent_root_id"}
        t = clean_terms(changes, current=cur)
        require_writable(scope, t, tenant_id)
        _validate_scope_target(tenant_id, t, conn)
        _validate_parent(tenant_id, root_id, t["parent_root_id"], conn)
        _check_overlap(tenant_id, root_id, t, conn)
        new_id = uuid.uuid4().hex
        done = query_one(
            """UPDATE purchase_budgets SET superseded_by = %s, superseded_at = NOW()
                WHERE id = %s AND tenant_id = %s AND superseded_by IS NULL RETURNING id""",
            (new_id, cur["id"], tenant_id), conn=conn)
        if not done:
            raise AppError("purchase_budget_stale",
                           "Somebody changed this budget meanwhile; reload it and try again",
                           status_code=409)
        _insert(conn, new_id, tenant_id, user_id, root_id, int(cur["revision"]) + 1, t)
    return list_one(tenant_id, scope, root_id)


def list_one(tenant_id: str, scope: Optional[frozenset], root_id: str) -> dict:
    row = get_visible(tenant_id, scope, root_id)
    return _fmt(row, _names(tenant_id, [row]))


# ── Usage: what has been ordered against a budget ────────────────────────────

def _po_item_rows(tenant_id: str, start: date, end: date) -> list[dict]:
    """Every ordered (approved / modified) line of every non-cancelled order
    generated in [start, end], with the order's destination and whether it is
    still open."""
    from backend.auth.warehouse_scope import _tenant_default
    default = _tenant_default(tenant_id)
    rows = query(
        """SELECT l.destination_warehouse, l.reception_status, l.cost_center_id,
                  i.sku, i.supplier, i.supplier_id, i.final_qty, i.unit_cost,
                  i.currency, i.fx_base_currency, i.value_base
             FROM inventory_po_log l
             JOIN inventory_po_items i ON i.po_log_id = l.id
            WHERE l.tenant_id = %s AND l.cancelled_at IS NULL
              AND l.generated_at >= %s::date AND l.generated_at < (%s::date + 1)
              AND i.status = ANY(%s)""",
        (tenant_id, start, end, list(_ORDERED_STATUSES)))
    cats = {r["sku"]: r["category"] for r in query(
        "SELECT DISTINCT ON (sku) sku, category FROM inventory_stock "
        "WHERE tenant_id = %s AND category IS NOT NULL ORDER BY sku", (tenant_id,))}
    out = []
    for r in rows:
        cost = r["unit_cost"]
        entry = {
            "warehouse": (r["destination_warehouse"] or "").strip() or default,
            "supplier": r["supplier"], "supplier_id": r["supplier_id"],
            "category": cats.get(r["sku"]), "cost_center_id": r["cost_center_id"],
            "value": None if cost is None else float(r["final_qty"] or 0) * float(cost),
            "open": (r["reception_status"] or "pending") in bm.OPEN_RECEPTION,
        }
        if r.get("currency"):
            # A line priced in another currency counts for the value recorded
            # when the order was written (in the currency it was converted
            # into), or not at all when it had no rate: never qty x cost in a
            # currency that is not the budget's.
            entry["value_currency"] = r.get("fx_base_currency")
            entry["value"] = (None if cost is None or r.get("value_base") is None
                              else float(r["value_base"]))
            entry["unconverted"] = cost is not None and r.get("value_base") is None
        out.append(entry)
    return out


def _in_budget_currency(rows: list[dict], budget_currency: str) -> list[dict]:
    """Rows as a budget in `budget_currency` may sum them: a converted line is
    only usable when it was converted INTO that currency; otherwise it is
    unconverted for this budget (counted, not summed)."""
    out = []
    for r in rows:
        vc = r.get("value_currency")
        if vc is not None and vc != budget_currency and r.get("value") is not None:
            r = {**r, "value": None, "unconverted": True}
        out.append(r)
    return out


def _scope_names(tenant_id: str, row: dict) -> dict[str, Any]:
    st, sv = row["scope_type"], row["scope_value"]
    if st == "warehouse":
        r = query_one("SELECT name FROM warehouses WHERE id = %s AND tenant_id = %s", (sv, tenant_id))
        return {"warehouse": r["name"] if r else None}
    if st == "supplier":
        r = query_one("SELECT name FROM suppliers WHERE id = %s AND tenant_id = %s", (sv, tenant_id))
        return {"supplier_id": sv, "supplier": r["name"] if r else None}
    if st == "category":
        return {"category": sv}
    if st == "cost_center":
        # The center and everything below it: a parent's budget covers its children.
        from backend.inventory import cost_center_chain_core as cc
        centers = [dict(r) for r in query(
            "SELECT id, parent_id, active FROM cost_centers WHERE tenant_id = %s", (tenant_id,))]
        return {"cost_center_ids": cc.descendants(centers, sv)}
    return {}


def usage(tenant_id: str, row: dict, today: date, _po_rows: Optional[list[dict]] = None) -> dict:
    """spent (received) + committed (open orders) of one budget row, its burn
    against elapsed time, and the money still free."""
    start, end = row["period_start"], row["period_end"]
    start = start if isinstance(start, date) else _as_date(start, "period_start")
    end = end if isinstance(end, date) else _as_date(end, "period_end")
    rows = _po_rows if _po_rows is not None else _po_item_rows(tenant_id, start, end)
    matcher = bm.scope_matcher(row["scope_type"], _scope_names(tenant_id, row))
    ordered = bm.ordered_value(_in_budget_currency(rows, row["currency"]), matcher)
    used = ordered["spent"] + ordered["committed"]
    own_free = bm.remaining(row["amount"], ordered["spent"], ordered["committed"])
    return {
        "spent": ordered["spent"], "committed": ordered["committed"], "ordered": round(used, 2),
        "remaining": own_free,
        "unknown_cost_lines": ordered["unknown_cost_lines"],
        "unconverted_lines": ordered.get("unconverted_lines", 0),
        "burn": bm.burn(row["amount"], used, start, end, today),
    }


def _parent_remaining(tenant_id: str, row: dict, today: date, depth: int = 0) -> Optional[float]:
    """The tightest remainder among the budget's ancestors."""
    parent_id = row.get("parent_root_id")
    if not parent_id or depth > MAX_PARENT_DEPTH:
        return None
    parent = query_one("SELECT * FROM purchase_budgets WHERE tenant_id = %s AND root_id = %s "
                       "AND superseded_by IS NULL", (tenant_id, parent_id))
    if not parent or not parent["active"]:
        return None
    mine = usage(tenant_id, parent, today)["remaining"]
    above = _parent_remaining(tenant_id, parent, today, depth + 1)
    return mine if above is None else min(mine, above)


def free_money(tenant_id: str, row: dict, today: date, use: Optional[dict] = None) -> dict:
    use = use or usage(tenant_id, row, today)
    parent_free = _parent_remaining(tenant_id, row, today)
    free, limited_by = bm.effective_remaining(use["remaining"], parent_free)
    return {"free": round(free, 2), "limited_by": limited_by, "parent_remaining": parent_free}


def _tenant_currency(tenant_id: str) -> str:
    from backend.api.v1.currency import currency_of
    return currency_of(tenant_id)["code"]


def status(tenant_id: str, scope: Optional[frozenset], today: date,
           root_id: Optional[str] = None) -> dict:
    """The budget the screen should show, with its usage and burn, plus a short
    summary of every other active budget the caller may see."""
    rows = [r for r in _current_rows(tenant_id) if visible(scope, r) and r["active"]]
    names = _names(tenant_id, rows)
    pool = {r["root_id"]: r for r in rows}
    chosen = None
    if root_id:
        if root_id not in pool:
            raise AppError("purchase_budget_not_found", "Budget not found", status_code=404)
        chosen = pool[root_id]
    else:
        running = [r for r in rows if r["period_start"] <= today <= r["period_end"]]
        # Company-wide first, then the narrower ones; newest period inside a kind.
        rank = {"company": 0, "warehouse": 1, "supplier": 2, "category": 3, "cost_center": 4}
        running.sort(key=lambda r: (rank[r["scope_type"]], -r["period_start"].toordinal()))
        chosen = running[0] if running else None

    summaries = []
    for r in rows:
        summaries.append({
            "root_id": r["root_id"], "scope_type": r["scope_type"],
            "scope_label": _fmt(r, names)["scope_label"], "amount": r["amount"],
            "currency": r["currency"], "period_type": r["period_type"],
            "period_start": r["period_start"].isoformat(), "period_end": r["period_end"].isoformat(),
            "running": r["period_start"] <= today <= r["period_end"],
        })
    out: dict[str, Any] = {"budget": None, "budgets": summaries, "today": today.isoformat(),
                           "warnings": []}
    if chosen is None:
        return out
    use = usage(tenant_id, chosen, today)
    fm = free_money(tenant_id, chosen, today, use)
    b = _fmt(chosen, names)
    out["budget"] = b
    out["usage"] = {**use, **fm}
    cur = _tenant_currency(tenant_id)
    if chosen["currency"] != cur:
        out["warnings"].append({"code": "currency_mismatch",
                                "params": {"budget": chosen["currency"], "tenant": cur}})
    if use["unknown_cost_lines"]:
        out["warnings"].append({"code": "ordered_lines_without_cost",
                                "params": {"count": use["unknown_cost_lines"]}})
    if use["unconverted_lines"]:
        out["warnings"].append({"code": "ordered_lines_unconverted",
                                "params": {"count": use["unconverted_lines"]}})
    if fm["limited_by"] == "parent":
        out["warnings"].append({"code": "limited_by_parent", "params": {}})
    return out


# ── The plan: what to fund first ─────────────────────────────────────────────

def _risk_annotate(rows: list[dict], period: str) -> None:
    """Money at risk per actionable row, exactly as the morning briefing computes
    it (`money_at_risk.py`); the row's signal and quantity are untouched."""
    from backend.inventory import money_at_risk as mar
    from backend.inventory import service as svc
    for it in rows:
        if it.get("signal") not in ("PEDIR_YA", "PEDIR_PRONTO"):
            continue
        stock_now = it.get("current_stock")
        has_stock = it.get("has_stock", stock_now is not None)
        stocked_out = bool(has_stock) and stock_now is not None and stock_now <= 0
        amount, basis = mar.money_at_risk(
            it.get("daily_demand"),
            svc._lead_time_in_periods(it.get("lead_time_days") or 0, period),
            it.get("coverage_days"), it.get("sale_price"), it.get("unit_cost"),
            in_stockout=stocked_out)
        it["money_at_risk"] = amount
        it["money_at_risk_basis"] = basis


def candidate_rows(user, session_id: str, row: dict) -> tuple[list[dict], str]:
    """The status rows a budget is planned over: the same rows the order flow
    derives its list from (the Panel's), restricted to the budget's scope and to
    the caller's warehouses. Returns (rows, period)."""
    from backend.auth import warehouse_scope as wscope
    from backend.inventory import service as svc
    from backend.sessions import planning_service

    tenant_id = user.tenant_id
    period = planning_service.get_planning(tenant_id).get("period", "daily")
    scoped = wscope.is_scoped(user)
    per_warehouse = scoped or row["scope_type"] == "warehouse"
    if per_warehouse:
        items = wscope.scoped_status_rows(
            user, svc.get_inventory_status_by_warehouse(tenant_id, session_id, period=period))
        # Per-warehouse rows carry no price, class or category: take them from
        # the company row of the same SKU.
        company = {i["sku"]: i for i in svc.get_inventory_status(tenant_id, session_id, period=period)}
        for it in items:
            c = company.get(it["sku"]) or {}
            for k in ("sale_price", "abc", "category", "supplier_id"):
                if it.get(k) is None:
                    it[k] = c.get(k)
    else:
        items = svc.get_inventory_status(tenant_id, session_id, period=period)
    matcher = bm.scope_matcher(row["scope_type"], _scope_names(tenant_id, row))
    items = [i for i in items if matcher(i)
             and i.get("signal") in ("PEDIR_YA", "PEDIR_PRONTO")
             and (i.get("recommended_qty") or 0) > 0]
    _risk_annotate(items, period)
    return items, period


def plan(user, scope: Optional[frozenset], today: date, root_id: Optional[str] = None,
         session_id: Optional[str] = None) -> dict:
    """Funded / deferred split of the CURRENT recommendations under a budget.
    Creates nothing and changes no quantity."""
    from forecasting_core.business.budget_allocation import allocate_budget, line_from_mapping
    from backend.sessions import planning_service

    tenant_id = user.tenant_id
    st = status(tenant_id, scope, today, root_id)
    if st["budget"] is None:
        return {**st, "lines": [], "summary": None,
                "warnings": [*st["warnings"], {"code": "no_budget", "params": {}}]}
    if not session_id:
        session_id = planning_service.resolve_active_session(tenant_id)
        if not session_id:
            raise AppError("no_completed_session", "No completed session for this tenant yet",
                           status_code=400)
    row = _current(tenant_id, st["budget"]["root_id"])
    if row["scope_type"] == "cost_center":
        # Recommendations belong to no cost center (an order is attributed to
        # one when it is placed), so there is nothing to allocate here. Say so
        # rather than present an empty plan as "nothing to fund".
        return {**st, "lines": [], "summary": None, "session_id": session_id,
                "warnings": [*st["warnings"], {"code": "cost_center_plan_unavailable", "params": {}}]}
    items, _period = candidate_rows(user, session_id, row)
    by_key: dict[str, dict] = {}
    lines = []
    for it in items:
        ln = line_from_mapping(it)
        by_key[ln.key] = it
        lines.append(ln)
    result = allocate_budget(lines, st["usage"]["free"])
    out_lines = []
    for al in result.lines:
        d = al.as_dict()
        src = by_key.get(al.key, {})
        d["display_name"] = src.get("display_name")
        d["category"] = src.get("category")
        out_lines.append(d)
    warnings = [*st["warnings"], *result.warnings]
    if st["budget"]["period_start"] > today.isoformat() or st["budget"]["period_end"] < today.isoformat():
        warnings.append({"code": "budget_period_not_running", "params": {}})
    return {**st, "lines": out_lines, "summary": result.summary, "warnings": warnings,
            "session_id": session_id}


# ── The order hook ───────────────────────────────────────────────────────────

def check_order(user, scope: Optional[frozenset], lines: list[dict], destination: Optional[str],
                today: date, cost_center_id: Optional[str] = None) -> list[dict]:
    """Which active budgets running today would the order push past what is
    left? `lines`: {sku, qty, unit_cost, supplier, supplier_id, warehouse,
    category}. Returns one entry per exceeded budget (empty = fits or none apply).

    A budget the caller may not see is reported WITHOUT its figures."""
    from backend.auth.warehouse_scope import _tenant_default
    tenant_id = user.tenant_id
    live = [r for r in _current_rows(tenant_id)
            if r["active"] and r["period_start"] <= today <= r["period_end"]]
    if not live:
        return []  # no budget in force: the order flow is exactly what it was
    default = _tenant_default(tenant_id)
    cats = {r["sku"]: r["category"] for r in query(
        "SELECT DISTINCT ON (sku) sku, category FROM inventory_stock "
        "WHERE tenant_id = %s AND category IS NOT NULL ORDER BY sku", (tenant_id,))}
    # Lines priced in another currency are converted at the rate in force today
    # (the same rule the order itself will be written with); a line with no rate
    # is unconverted, never valued at 1.0.
    from backend.fx import service as fx_service
    priced = [dict(ln) for ln in lines]
    fx_service.price_lines(tenant_id, priced, as_of=today)
    shaped = []
    for ln, pl in zip(lines, priced):
        cost = ln.get("unit_cost")
        qty = float(ln.get("qty") or 0)
        entry = {
            "warehouse": (ln.get("warehouse") or destination or "").strip() or default,
            "supplier": ln.get("supplier"), "supplier_id": ln.get("supplier_id"),
            "category": ln.get("category") or cats.get(ln.get("sku")),
            "cost_center_id": cost_center_id,
            "value": None if cost is None else qty * float(cost), "open": True,
        }
        if pl.get("currency"):
            entry["value_currency"] = pl.get("fx_base_currency")
            entry["value"] = (None if cost is None or pl.get("value_base") is None
                              else float(pl["value_base"]))
            entry["unconverted"] = cost is not None and pl.get("value_base") is None
        shaped.append(entry)
    out = []
    for row in live:
        matcher = bm.scope_matcher(row["scope_type"], _scope_names(tenant_id, row))
        mine = bm.ordered_value(_in_budget_currency(shaped, row["currency"]), matcher)
        if not mine["lines"]:
            continue
        order_value = mine["spent"] + mine["committed"]
        fm = free_money(tenant_id, row, today)
        chk = bm.order_check(order_value, fm["free"], mine["unknown_cost_lines"])
        fx_missing = mine.get("unconverted_lines", 0)
        # An order whose value cannot be fully known is flagged even when the
        # part that can be valued fits: a hard cap must not wave it through.
        if not chk["exceeds"] and not fx_missing:
            continue
        shown = visible(scope, row)
        entry = {"root_id": row["root_id"] if shown else None,
                 "scope_type": row["scope_type"], "hard_cap": row["hard_cap"],
                 "visible": shown, "currency": row["currency"],
                 "exceeds": chk["exceeds"]}
        if shown:
            entry.update({k: chk[k] for k in ("order_value", "remaining", "over_by",
                                              "unknown_cost_lines")})
        if fx_missing:
            entry["unconverted_lines"] = fx_missing
        out.append(entry)
    return out
