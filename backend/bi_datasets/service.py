"""Loaders for the flat BI datasets.

Every loader returns `(rows, paging)`: raw dicts keyed by the dataset's column
names (render.py shapes them) and the paging facts. They are THIN. Each one calls
the same service function the screen it mirrors calls and only reshapes the
result, so a number in a dataset is the number on the screen:

    inventory-status      inventory.service.get_inventory_status_by_warehouse
                          (+ money_at_risk, the briefing's own function)
    purchase-order-lines  inventory_po_log / inventory_po_items, as the PO screens
    receptions            the same tables, lines with units received
    forecast-points       session_store.get_forecasts + best_model_by_sku
    accuracy              session_store.get_training_result metrics + best_model_by_sku
    committed-demand      inventory.committed_demand_service

Warehouse scope is applied HERE, in every loader, through
`backend.auth.warehouse_scope`: a caller limited to some warehouses gets only the
rows of those warehouses, and a dataset that cannot be cut by warehouse is refused
(`warehouse_scope_company_totals`) rather than served with company-wide numbers.
Every query carries the caller's tenant id.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Optional

from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser
from backend.bi_datasets import render
from backend.bi_datasets.spec import Dataset
from backend.db import session_store
from backend.db.connection import query, query_one
from backend.errors import AppError

log = logging.getLogger(__name__)

# The service level the Inventory status screen uses unless told otherwise.
DEFAULT_SERVICE_LEVEL = 0.95

# Mirrors inventory.service._WAPE_UNDEFINED: a WAPE this large is the engine's
# `sum|e| / (0 + 1e-8)` for a window with no demand, not an error rate.
_WAPE_UNDEFINED = 1e6

_ORDERED_STATUSES = ("approved", "modified")


# ── Sessions ─────────────────────────────────────────────────────────────────

def resolve_session(tenant_id: str, requested: Optional[str]) -> dict:
    """The completed session a dataset reads: the one asked for, else the
    tenant's active one. Same refusals as the results screens."""
    from backend.sessions import planning_service
    from backend.sessions import service as session_svc

    session_id = requested or planning_service.resolve_active_session(tenant_id)
    if not session_id:
        raise AppError(
            "no_completed_session", "No completed session for this tenant yet",
            status_code=400,
        )
    session = session_svc.get_session(tenant_id, session_id)
    if not session:
        raise AppError("session_not_found", "Session not found", status_code=404)
    if session["status"] != "COMPLETED":
        raise AppError(
            "session_still_training",
            f"Session must be COMPLETED. Current: {session['status']}",
            status_code=409, params={"status": session["status"]},
        )
    return session


# ── inventory-status ─────────────────────────────────────────────────────────

def _sku_attributes(tenant_id: str, session_id: str, period: str) -> dict[str, dict]:
    """{sku: the aggregated row} for the SKU-level facts a per-warehouse row does
    not carry (forecast source, sale price). Read from the status snapshot, or
    computed live when there is none, exactly as the screen does. Only SKU
    properties are taken from it, never a stock or demand figure, so nothing
    company-wide reaches a warehouse-scoped caller through it."""
    from backend.inventory import service as svc
    from backend.inventory import status_snapshot

    snap = status_snapshot.read_status(
        tenant_id, session_id, DEFAULT_SERVICE_LEVEL, period)
    items = snap["items"] if snap is not None else svc.get_inventory_status(
        tenant_id, session_id, DEFAULT_SERVICE_LEVEL, period)
    return {i["sku"]: i for i in items}


def inventory_status(user: CurrentUser, params: dict, page: int, limit: int):
    from backend.inventory import money_at_risk as mar
    from backend.inventory import service as svc
    from backend.sessions import planning_service

    session = resolve_session(user.tenant_id, params.get("session_id"))
    session_id = session["id"]
    period = planning_service.get_planning(user.tenant_id).get("period", "daily")

    rows = wscope.scoped_status_rows(user, svc.get_inventory_status_by_warehouse(
        user.tenant_id, session_id, DEFAULT_SERVICE_LEVEL, period))
    attrs = _sku_attributes(user.tenant_id, session_id, period)

    out = []
    for r in rows:
        sku_row = attrs.get(r["sku"]) or {}
        amount = basis = None
        if r.get("signal") in ("PEDIR_YA", "PEDIR_PRONTO"):
            stock_now = r.get("current_stock")
            # The briefing's own call (inventory.service.morning_briefing).
            amount, basis = mar.money_at_risk(
                r.get("daily_demand"),
                svc._lead_time_in_periods(r.get("lead_time_days") or 0, period),
                r.get("coverage_days"),
                sku_row.get("sale_price"),
                r.get("unit_cost"),
                in_stockout=stock_now is not None and stock_now <= 0,
            )
        out.append({
            **r,
            "session_id": session_id,
            "period": period,
            "forecast_source": sku_row.get("forecast_source"),
            "money_at_risk": amount,
            "money_at_risk_basis": basis,
        })
    out.sort(key=render.sort_key("sku", "warehouse"))
    return render.slice_page(out, page, limit)


# ── purchase orders and receptions ───────────────────────────────────────────

def _updated_since_clause(column_sql: str, since: Optional[datetime], params: list) -> str:
    if since is None:
        return ""
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    params.append(since)
    return f" AND {column_sql} >= %s"


_LAST_CHANGE_SQL = "GREATEST(p.generated_at, p.sent_at, p.paid_at, p.received_at, p.cancelled_at)"

_PO_LINE_FROM = """
    FROM inventory_po_items i
    JOIN inventory_po_log p ON p.id = i.po_log_id AND p.tenant_id = i.tenant_id
"""


def _po_scope(user: CurrentUser, where: list[str], params: list) -> bool:
    """Restrict to the orders a warehouse-scoped caller may see. False when that
    leaves nothing (the caller has no order they may read)."""
    allowed = wscope.allowed_po_ids(user)
    if allowed is None:
        return True
    if not allowed:
        return False
    where.append("p.id = ANY(%s)")
    params.append(sorted(allowed))
    return True


def _default_warehouse_name(tenant_id: str) -> str:
    from backend.inventory import warehouse_service as wh_svc
    return wh_svc.get_default_warehouse_name(tenant_id) or wh_svc.DEFAULT_WAREHOUSE


def _empty(page: int, limit: int):
    return [], render.page_bounds(0, page, limit)


def purchase_order_lines(user: CurrentUser, params: dict, page: int, limit: int):
    where = ["i.tenant_id = %s", "p.tenant_id = %s"]
    args: list = [user.tenant_id, user.tenant_id]
    if not _po_scope(user, where, args):
        return _empty(page, limit)
    clause = " AND ".join(where) + _updated_since_clause(
        _LAST_CHANGE_SQL, params.get("updated_since"), args)

    total = query_one(f"SELECT COUNT(*) AS n {_PO_LINE_FROM} WHERE {clause}", tuple(args))
    info = render.page_bounds(int(total["n"]) if total else 0, page, limit)
    rows = query(
        f"""SELECT p.po_number, p.id AS po_log_id, i.id AS line_id,
                   p.generated_at AS ordered_at, {_LAST_CHANGE_SQL} AS updated_at,
                   p.sent_at, p.paid_at, p.received_at, p.cancelled_at,
                   p.reception_status, p.approval_status, p.source,
                   p.destination_warehouse, i.warehouse AS line_warehouse,
                   i.supplier, i.sku, i.display_name, i.signal,
                   i.status AS line_status, i.recommended_qty, i.final_qty,
                   i.received_qty, i.unit_cost
              {_PO_LINE_FROM}
             WHERE {clause}
             ORDER BY p.generated_at, p.id, i.supplier NULLS LAST, i.sku, i.id
             LIMIT %s OFFSET %s""",
        (*args, limit, info["offset"]),
    )
    default_wh = _default_warehouse_name(user.tenant_id)
    out = []
    for r in rows:
        ordered = r["line_status"] in _ORDERED_STATUSES
        final = float(r["final_qty"] or 0)
        received = float(r["received_qty"] or 0)
        cost = r["unit_cost"]
        out.append({
            **r,
            "destination_warehouse": (r["destination_warehouse"] or "").strip() or default_wh,
            "is_ordered": ordered,
            "outstanding_qty": max(0.0, final - received) if ordered else 0.0,
            "line_value": round(final * float(cost), 2) if cost is not None else None,
        })
    return out, info


def receptions(user: CurrentUser, params: dict, page: int, limit: int):
    where = ["i.tenant_id = %s", "p.tenant_id = %s", "COALESCE(i.received_qty, 0) > 0"]
    args: list = [user.tenant_id, user.tenant_id]
    if not _po_scope(user, where, args):
        return _empty(page, limit)
    clause = " AND ".join(where) + _updated_since_clause(
        "p.received_at", params.get("updated_since"), args)

    total = query_one(f"SELECT COUNT(*) AS n {_PO_LINE_FROM} WHERE {clause}", tuple(args))
    info = render.page_bounds(int(total["n"]) if total else 0, page, limit)
    rows = query(
        f"""SELECT p.po_number, p.id AS po_log_id, i.id AS line_id,
                   p.received_at, p.generated_at AS ordered_at,
                   p.reception_status, p.cancelled_at,
                   i.supplier, i.sku, i.display_name, i.warehouse,
                   i.final_qty, i.received_qty, i.unit_cost
              {_PO_LINE_FROM}
             WHERE {clause}
             ORDER BY p.received_at NULLS FIRST, p.id, i.sku, i.id
             LIMIT %s OFFSET %s""",
        (*args, limit, info["offset"]),
    )
    out = []
    for r in rows:
        final = float(r["final_qty"] or 0)
        received = float(r["received_qty"] or 0)
        cost = r["unit_cost"]
        days = None
        if r["received_at"] is not None and r["ordered_at"] is not None:
            # The formula reception_service.receive_po learns lead times with.
            days = round(max(0.0, (r["received_at"] - r["ordered_at"]).total_seconds()
                             / 86400.0), 2)
        out.append({
            **r,
            "days_to_last_reception": days,
            "po_cancelled": r["cancelled_at"] is not None,
            "outstanding_qty": max(0.0, final - received),
            "received_value": round(received * float(cost), 2) if cost is not None else None,
        })
    return out, info


# ── forecast-points and accuracy ─────────────────────────────────────────────

def _scoped_keys(user: CurrentUser, keys) -> Optional[set[str]]:
    """For a warehouse-scoped caller: the series keys ('sku│store') they may read.
    None for an unrestricted caller (everything). Raises when the session was
    forecast company-wide, because then no series belongs to a warehouse and every
    number is a company total."""
    from backend.inventory.series import split_key

    if not wscope.is_scoped(user):
        return None
    keys = list(keys)
    if not any(split_key(k)[1] is not None for k in keys):
        wscope.require_company_wide(user)  # raises warehouse_scope_company_totals
    return {k for k in keys
            if split_key(k)[1] is not None and wscope.in_scope(user, split_key(k)[1])}


def _metrics_rows(tenant_id: str, session_id: str) -> list[dict]:
    result = session_store.get_training_result(tenant_id, session_id) or {}
    return (result.get("metrics") or {}).get("rows") or []


def _champion(champions: dict[str, str], key: str, sku: str) -> Optional[str]:
    return champions.get(key) or champions.get(sku)


def _points_of(entry) -> list[dict]:
    """A model's stored forecast points. New shape {historical, forecast}; the
    legacy shape is the bare list."""
    if isinstance(entry, dict):
        return entry.get("forecast") or []
    if isinstance(entry, list):
        return entry
    return []


def _first(point: dict, *names: str):
    for n in names:
        if point.get(n) is not None:
            return point[n]
    return None


def forecast_points(user: CurrentUser, params: dict, page: int, limit: int):
    from backend.inventory.series import split_key
    from backend.inventory.service import best_model_by_sku

    session = resolve_session(user.tenant_id, params.get("session_id"))
    session_id = session["id"]
    forecasts = session_store.get_forecasts(user.tenant_id, session_id) or {}
    allowed = _scoped_keys(user, forecasts.keys())
    champions = best_model_by_sku(_metrics_rows(user.tenant_id, session_id))

    out: list[dict] = []
    for key, models in forecasts.items():
        if allowed is not None and key not in allowed:
            continue
        if not isinstance(models, dict):
            continue
        sku, store = split_key(key)
        champion = _champion(champions, key, sku)
        with_points = {m: _points_of(e) for m, e in models.items() if _points_of(e)}
        if not with_points:
            continue
        is_champion = champion in with_points
        # The champion when it has points; else the first model that has some,
        # flagged, so a fallback is never presented as the champion.
        model = champion if is_champion else sorted(with_points)[0]
        for step, p in enumerate(with_points[model], start=1):
            out.append({
                "session_id": session_id,
                "period": session.get("granularity"),
                "sku": sku,
                "warehouse": store,
                "model": model,
                "model_is_champion": is_champion,
                "step": step,
                "date": p.get("date"),
                "forecast": _first(p, "value", "p50", "q50"),
                "lower": _first(p, "lower", "p10", "q10"),
                "upper": _first(p, "upper", "p90", "q90"),
            })
    out.sort(key=render.sort_key("sku", "warehouse", "date"))
    return render.slice_page(out, page, limit)


def _defined(value) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def accuracy(user: CurrentUser, params: dict, page: int, limit: int):
    from backend.inventory import service as svc
    from backend.inventory.series import split_key

    session = resolve_session(user.tenant_id, params.get("session_id"))
    session_id = session["id"]
    rows = _metrics_rows(user.tenant_id, session_id)
    champions = svc.best_model_by_sku(rows)
    metric = svc._champion_metric(rows)

    def score(row: dict) -> float:
        value = _defined(row.get(metric))
        return value if value is not None else math.inf

    # The champion's own row per series: the lowest score on the ranking metric
    # when a model was scored more than once.
    best: dict[str, dict] = {}
    for r in rows:
        if r.get("type") == "baseline":
            continue
        key = str(r.get("sku"))
        if champions.get(key) != r.get("model"):
            continue
        if key not in best or score(r) < score(best[key]):
            best[key] = r

    allowed = _scoped_keys(user, best.keys())
    out = []
    for key, r in best.items():
        if allowed is not None and key not in allowed:
            continue
        sku, store = split_key(key)
        wape, bias, mae = _defined(r.get("wape")), _defined(r.get("bias")), _defined(r.get("mae"))
        # The same two refusals compute_session_accuracy makes: a window with no
        # demand scores 0/0 (not a perfect forecast), and the engine's epsilon
        # makes a dead SKU's WAPE astronomical. Neither is an error rate.
        if wape is not None and (wape >= _WAPE_UNDEFINED or (wape == 0.0 and (mae or 0.0) == 0.0)):
            wape = None
        out.append({
            "session_id": session_id,
            "sku": sku,
            "warehouse": store,
            "model": r.get("model"),
            "wape": wape,
            "bias": bias,
            "mae": mae,
            "rmse": _defined(r.get("rmse")),
        })
    out.sort(key=render.sort_key("sku", "warehouse"))
    return render.slice_page(out, page, limit)


# ── committed-demand ─────────────────────────────────────────────────────────

def committed_demand(user: CurrentUser, params: dict, page: int, limit: int):
    from backend.inventory import committed_demand_service as cd
    from backend.inventory import warehouse_service as wh_svc

    warehouses = wh_svc.list_warehouses(user.tenant_id)
    name_by_id = {str(w["id"]): w["name"] for w in warehouses}

    where = ["c.tenant_id = %s"]
    args: list = [user.tenant_id]
    scoped = wscope.is_scoped(user)
    if scoped:
        # A scoped caller reads the commitments of their warehouses. A
        # commitment with no warehouse is the whole company's, so it is not
        # theirs to see.
        ids = sorted(i for i, n in name_by_id.items() if wscope.in_scope(user, n))
        if not ids:
            return _empty(page, limit)
        where.append("c.warehouse_id = ANY(%s)")
        args.append(ids)
    clause = " AND ".join(where) + _updated_since_clause(
        "c.updated_at", params.get("updated_since"), args)

    total = query_one(f"SELECT COUNT(*) AS n FROM committed_demand c WHERE {clause}", tuple(args))
    info = render.page_bounds(int(total["n"]) if total else 0, page, limit)
    rows = query(
        f"""SELECT {cd._COLS} FROM committed_demand c
             WHERE {clause}
             ORDER BY c.delivery_date, c.created_at, c.id
             LIMIT %s OFFSET %s""",
        (*args, limit, info["offset"]),
    )
    today = datetime.now(timezone.utc).date()
    items = [cd._fmt(r, today) for r in rows]
    if not scoped:
        # The verdict is a company-wide check (stock summed over every
        # warehouse), so it is only computed for a caller who may see the company.
        cd.annotate_risk(user.tenant_id, items)
    else:
        for i in items:
            i.update({"at_risk": None, "shortfall": None, "latest_safe_order_date": None})
    for i in items:
        i["warehouse"] = name_by_id.get(str(i.get("warehouse_id"))) if i.get("warehouse_id") else None
    return items, info


LOADERS = {
    "inventory-status": inventory_status,
    "purchase-order-lines": purchase_order_lines,
    "receptions": receptions,
    "forecast-points": forecast_points,
    "accuracy": accuracy,
    "committed-demand": committed_demand,
}


def load(dataset: Dataset, user: CurrentUser, params: dict, page: int, limit: int):
    return LOADERS[dataset.name](user, params, page, limit)
