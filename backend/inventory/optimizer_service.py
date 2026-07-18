"""
Assembles a forecasting_core MILP OptimizationInput from live DB state
(inventory_stock across bodegas, session forecasts, business_cfg), and
collapses an OptimizationResult back into one actionable total per line —
see docs/superpowers/specs/2026-07-12-multi-warehouse-milp-design.md,
"Sub-proyecto 6", for the design this implements.
"""

from __future__ import annotations

from typing import Optional

from forecasting_core.business.optimizer import OptimizationInput, OptimizationResult

from backend.db import session_store
from backend.inventory.service import list_stock, _avg_forecast_curve

_DEFAULT_UNIT_COST = 1.0
_DEFAULT_TRANSFER_COST_PER_UNIT = 0.5
_DEFAULT_LEAD_TIME_DAYS = 15


def build_optimization_input(
    tenant_id: str, session_id: str, horizon_days: int = 14,
) -> Optional[OptimizationInput]:
    forecasts: dict = session_store.get_forecasts(tenant_id, session_id) or {}
    skus = sorted(forecasts.keys())

    stock_rows = list_stock(tenant_id)
    warehouses = sorted({r["bodega"] for r in stock_rows if r.get("bodega")})

    if not skus or not warehouses:
        return None

    business_cfg: dict = session_store.get_field(tenant_id, session_id, "business_cfg") or {}
    holding_cost_pct = float(business_cfg.get("holding_cost_pct", 0.20))
    stockout_cost_multiplier = float(business_cfg.get("stockout_cost_multiplier", 3.0))

    # rows_by_sku[sku] -> {bodega: row}, only for bodegas that actually have a row.
    rows_by_sku: dict[str, dict[str, dict]] = {}
    for r in stock_rows:
        rows_by_sku.setdefault(r["sku"], {})[r["bodega"]] = r

    stock0: dict[tuple[str, str], float] = {}
    demand: dict[tuple[str, str], list[float]] = {}
    lead_time_buckets: dict[str, int] = {}
    holding_cost: dict[str, float] = {}
    stockout_cost: dict[str, float] = {}
    order_cost: dict[str, float] = {}

    for sku in skus:
        sku_rows = rows_by_sku.get(sku, {})

        for w in warehouses:
            stock0[(sku, w)] = float(sku_rows[w]["stock_actual"] or 0) if w in sku_rows else 0.0

        total_stock = sum(stock0[(sku, w)] for w in warehouses)

        model_forecasts = forecasts.get(sku, {})
        curve = _avg_forecast_curve(model_forecasts, max_steps=horizon_days)
        daily_total = [0.0] * horizon_days
        for point in curve:
            step = point["step"]
            if step < horizon_days:
                daily_total[step] = point["value"]

        for w in warehouses:
            if total_stock > 0:
                share = stock0[(sku, w)] / total_stock
            else:
                share = 1.0 / len(warehouses)
            demand[(sku, w)] = [v * share for v in daily_total]

        lead_times = [int(row["lead_time_dias"]) for row in sku_rows.values() if row.get("lead_time_dias") is not None]
        lead_time_buckets[sku] = max(lead_times) if lead_times else _DEFAULT_LEAD_TIME_DAYS

        costs = [float(row["costo_unitario"]) for row in sku_rows.values() if row.get("costo_unitario") is not None]
        unit_cost = max(costs) if costs else _DEFAULT_UNIT_COST

        order_cost[sku] = unit_cost
        holding_cost[sku] = unit_cost * holding_cost_pct / 365
        stockout_cost[sku] = holding_cost[sku] * stockout_cost_multiplier

    return OptimizationInput(
        skus=skus,
        warehouses=warehouses,
        horizon=horizon_days,
        demand=demand,
        stock0=stock0,
        lead_time_buckets=lead_time_buckets,
        holding_cost=holding_cost,
        stockout_cost=stockout_cost,
        order_cost=order_cost,
        transfer_cost=_DEFAULT_TRANSFER_COST_PER_UNIT,
    )


def serialize_optimization_result(inp, result, stock_rows: list[dict]) -> dict:
    """
    Collapses an OptimizationResult into one actionable total per (sku, bodega) order
    and per (sku, from_bodega, to_bodega) transfer, dropping any with qty == 0.

    Args:
        inp: OptimizationInput (used for horizon_days)
        result: OptimizationResult from MILP solver
        stock_rows: list of dicts with {sku, bodega, costo_unitario, proveedor}

    Returns:
        dict with keys: status, total_cost, horizon_days, orders[], transfers[]
    """
    row_by_sku_bodega = {(r["sku"], r["bodega"]): r for r in stock_rows}

    order_totals: dict[tuple[str, str], float] = {}
    for (sku, w, t), qty in result.orders.items():
        if qty > 0:
            order_totals[(sku, w)] = order_totals.get((sku, w), 0.0) + qty

    transfer_totals: dict[tuple[str, str, str], float] = {}
    for (sku, a, b, t), qty in result.transfers.items():
        if qty > 0:
            transfer_totals[(sku, a, b)] = transfer_totals.get((sku, a, b), 0.0) + qty

    orders = []
    for (sku, w) in sorted(order_totals):
        row = row_by_sku_bodega.get((sku, w), {})
        orders.append({
            "sku": sku, "bodega": w, "qty": round(order_totals[(sku, w)], 2),
            "costo_unitario": row.get("costo_unitario"),
            "proveedor": row.get("proveedor"),
        })

    transfers = []
    for (sku, a, b) in sorted(transfer_totals):
        transfers.append({
            "sku": sku, "from_bodega": a, "to_bodega": b,
            "qty": round(transfer_totals[(sku, a, b)], 2),
        })

    return {
        "status": result.status,
        "total_cost": round(result.total_cost, 2),
        "horizon_days": inp.horizon,
        "orders": orders,
        "transfers": transfers,
    }
