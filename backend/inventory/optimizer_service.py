"""
Assembles a forecasting_core MILP OptimizationInput from live DB state
(inventory_stock across warehouses, session forecasts, business_cfg), and
collapses an OptimizationResult back into one actionable total per line.

The model itself — variables, constraints, objective — is documented in
`forecasting_core/business/optimizer.py`'s module docstring. This module only
does the translation between the database and that model.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Optional

from forecasting_core.business.optimizer import OptimizationInput

import math as _math

from backend.db import session_store
from backend.inventory import transfer_lane_service as lane_svc
from backend.inventory.defaults import DEFAULT_LEAD_TIME_DAYS as _DEFAULT_LEAD_TIME_DAYS
from backend.inventory.service import list_stock, _avg_forecast_curve, _days_per_period

_DEFAULT_UNIT_COST = 1.0

# A MILP solve is CPU-bound and can take tens of seconds. FastAPI runs sync
# endpoints on a bounded thread pool, so an unthrottled burst of /optimize
# requests can occupy every worker thread at once and wedge the whole process
# (QA drove /health to time out for 8+ minutes). This bounded gate caps how
# many solves run concurrently; requests beyond the cap are rejected fast
# (OptimizerBusy -> 503 retry) instead of piling onto the thread pool.
_MAX_CONCURRENT_SOLVES = 2
_solve_gate = threading.BoundedSemaphore(_MAX_CONCURRENT_SOLVES)


class OptimizerBusy(Exception):
    """Raised when all concurrent-solve slots are taken; caller should 503."""


@contextmanager
def solve_slot():
    """Non-blocking concurrency gate around a MILP solve. Raises OptimizerBusy
    immediately if every slot is in use — never queues, so a burst can't tie
    up threads waiting."""
    if not _solve_gate.acquire(blocking=False):
        raise OptimizerBusy()
    try:
        yield
    finally:
        _solve_gate.release()


def skus_missing_stock(forecasts, stock_rows: list[dict]) -> list[str]:
    """Forecast SKUs with no stock on file — the ones nothing can be decided for.

    How much to buy is a function of how much is left, and how much is left is
    exactly what nobody told us. There is no honest quantity to compute, so
    these are named rather than guessed at.
    """
    measurable = {
        r["sku"] for r in stock_rows
        if r.get("sku") and r.get("current_stock") is not None
    }
    return sorted(sku for sku in forecasts if sku not in measurable)


def build_optimization_input(
    tenant_id: str,
    session_id: str,
    horizon_days: int = 14,
    stock_rows: Optional[list[dict]] = None,
    period: str = "daily",
    lanes: Optional[dict] = None,
) -> Optional[OptimizationInput]:
    """
    `lanes`: preloaded transfer_lane_service.lane_map ({(from,to): lane}); when
    omitted it is fetched here. Lanes are what give a transfer a price and a
    transit time in the MILP — the engine stays DB-free, so this function
    resolves them and hands over plain dicts. Pairs with no configured lane fall
    back to transfer_lane_service's documented default (1 day, free).

    `stock_rows` lets the caller pass an already-fetched inventory snapshot so
    the optimize path reads inventory_stock once instead of twice (build here +
    serialize at the endpoint). Fewer pooled-connection checkouts per request
    matters under a concurrent burst: the DB pool (ThreadedConnectionPool,
    max=10) raises PoolError rather than blocking once every connection is in
    use, so trimming redundant queries reduces the chance this path tips it.
    Omit it and the function fetches the snapshot itself, as before.
    """
    forecasts: dict = session_store.get_forecasts(tenant_id, session_id) or {}

    if stock_rows is None:
        stock_rows = list_stock(tenant_id)
    warehouses = sorted({r["warehouse"] for r in stock_rows if r.get("warehouse")})

    # A SKU with no stock on file is not optimized at all.
    #
    # It used to be: `float(current_stock or 0)` — assume the shelf is empty,
    # which is the assumption that produces the LARGEST possible order. Measured
    # on a real session, this endpoint told the buyer to order 130 units of a
    # SKU the inventory screen was simultaneously refusing to give any signal to
    # ("SIN_DATOS — agrega stock actual para ver la señal"). Two screens, one
    # product, opposite advice, and the one with the buy button was the one that
    # had invented its input.
    #
    # There is no honest quantity to compute here: how much to buy is a function
    # of how much is left, and how much is left is precisely what nobody told
    # us. So the SKU is left out and named in `needs_stock`, and the screens say
    # what is missing instead of printing a number about nothing.
    skus = sorted(set(forecasts) - set(skus_missing_stock(forecasts, stock_rows)))

    if not skus or not warehouses:
        return None

    business_cfg: dict = session_store.get_field(tenant_id, session_id, "business_cfg") or {}
    holding_cost_pct = float(business_cfg.get("holding_cost_pct", 0.20))
    stockout_cost_multiplier = float(business_cfg.get("stockout_cost_multiplier", 3.0))

    # rows_by_sku[sku] -> {warehouse: row}, only for warehouses that actually have a row.
    rows_by_sku: dict[str, dict[str, dict]] = {}
    for r in stock_rows:
        rows_by_sku.setdefault(r["sku"], {})[r["warehouse"]] = r

    stock0: dict[tuple[str, str], float] = {}
    demand: dict[tuple[str, str], list[float]] = {}
    lead_time_buckets: dict[str, int] = {}
    holding_cost: dict[str, float] = {}
    stockout_cost: dict[str, float] = {}
    order_cost: dict[str, float] = {}

    for sku in skus:
        sku_rows = rows_by_sku.get(sku, {})

        for w in warehouses:
            stock0[(sku, w)] = float(sku_rows[w]["current_stock"] or 0) if w in sku_rows else 0.0

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

        lead_times = [int(row["lead_time_days"]) for row in sku_rows.values() if row.get("lead_time_days") is not None]
        raw_lead = max(lead_times) if lead_times else _DEFAULT_LEAD_TIME_DAYS
        # Lead time in the horizon's own buckets: for daily this is the day
        # count (unchanged); for weekly/monthly it is the lead time rounded up
        # to whole periods, staying commensurable with horizon_days (which the
        # endpoint expresses in that period's buckets when a coarser period is
        # active).
        lead_time_buckets[sku] = max(1, _math.ceil(raw_lead / _days_per_period(period)))

        costs = [float(row["unit_cost"]) for row in sku_rows.values() if row.get("unit_cost") is not None]
        unit_cost = max(costs) if costs else _DEFAULT_UNIT_COST

        order_cost[sku] = unit_cost
        holding_cost[sku] = unit_cost * holding_cost_pct / 365
        # short[i,w,t] in the optimizer is a PER-BUCKET unmet-demand penalty,
        # not an accumulating backorder — each day's shortfall is evaluated
        # independently, it doesn't compound across days. So the right
        # comparison is "cost of leaving one day's demand unmet" against
        # "cost of having bought that same day's worth of units," not against
        # some multi-day accumulation. An earlier version of this divided by
        # lead_time_buckets (modeling "a shortage lasting a full lead time
        # costs `multiplier`x"), but that made the per-day stockout penalty
        # smaller than order_cost by a factor of lead_time/multiplier —
        # meaning ongoing daily demand ALWAYS looked cheaper to leave unmet
        # than to fulfill, so the solver never recommended buying at all.
        # Confirmed by direct testing: with real demo data (stock=40,
        # lead_time=10, cost=8.5/unit, ~40 units/day demand), the /lead_time
        # formula produced zero orders even though the SKU had 1 day of
        # stock left; dropping the division produces the expected 800-unit
        # order. Sanity-checked against a well-stocked SKU (correctly orders
        # nothing) and a transfer-preferred scenario (still prefers the
        # cheaper transfer over a new order) before locking this in.
        stockout_cost[sku] = order_cost[sku] * stockout_cost_multiplier

    # Per-lane money and time. The old model priced EVERY move at a single
    # hardcoded 0.5/unit and let it teleport within the bucket; now each
    # ordered pair carries its own per-unit cost, its transit time in buckets
    # and its per-shipment fixed cost, so a slow lane can no longer beat a
    # purchase for free and splitting one move into many is no longer free
    # either. The engine turns each lane with a positive fixed_cost into a
    # binary per (lane, bucket); lanes left at the default 0 add no variable,
    # so a tenant that never configures a fixed cost solves exactly as before.
    if lanes is None:
        lanes = lane_svc.lane_map(tenant_id)
    transfer_cost_by_lane: dict[tuple[str, str], float] = {}
    transfer_lead_buckets: dict[tuple[str, str], int] = {}
    transfer_fixed_cost_by_lane: dict[tuple[str, str], float] = {}
    for a in warehouses:
        for b in warehouses:
            if a == b:
                continue
            lane = lane_svc.lane_for(lanes, a, b)
            transfer_cost_by_lane[(a, b)] = float(lane["cost_per_unit"])
            transfer_lead_buckets[(a, b)] = int(
                _math.ceil(int(lane["lead_time_days"]) / _days_per_period(period)))
            transfer_fixed_cost_by_lane[(a, b)] = float(lane["fixed_cost"])

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
        # Global fallback only: every real pair is in transfer_cost_by_lane.
        transfer_cost=lane_svc.DEFAULT_LANE_COST_PER_UNIT,
        transfer_cost_by_lane=transfer_cost_by_lane,
        transfer_lead_buckets=transfer_lead_buckets,
        transfer_fixed_cost_by_lane=transfer_fixed_cost_by_lane,
    )


def serialize_optimization_result(inp, result, stock_rows: list[dict]) -> dict:
    """
    Collapses an OptimizationResult into one actionable total per (sku, warehouse) order
    and per (sku, from_warehouse, to_warehouse) transfer, dropping any with qty == 0.

    Args:
        inp: OptimizationInput (used for horizon_days)
        result: OptimizationResult from MILP solver
        stock_rows: list of dicts with {sku, warehouse, unit_cost, supplier}

    Returns:
        dict with keys: status, total_cost, horizon_days, orders[], transfers[]
    """
    row_by_sku_warehouse = {(r["sku"], r["warehouse"]): r for r in stock_rows}

    order_totals: dict[tuple[str, str], float] = {}
    for (sku, w, t), qty in result.orders.items():
        if qty > 0:
            order_totals[(sku, w)] = order_totals.get((sku, w), 0.0) + qty

    transfer_totals: dict[tuple[str, str, str], float] = {}
    for (sku, a, b, t), qty in result.transfers.items():
        if qty > 0:
            transfer_totals[(sku, a, b)] = transfer_totals.get((sku, a, b), 0.0) + qty

    # Quantities are whole units: you cannot order or move a fraction of a unit.
    # In daily mode the solve already lands on integers, so ceil is a no-op there;
    # coarser periods (weekly/monthly) carry larger per-bucket demand and can
    # produce fractional totals, which must round up so we never under-order.
    orders = []
    for (sku, w) in sorted(order_totals):
        row = row_by_sku_warehouse.get((sku, w), {})
        # What the quantity RESTS ON, carried out with it.
        #
        # The optimizer has to assume something when a SKU has no stock row, and
        # `float(current_stock or 0)` assumes zero — which is the assumption that
        # produces the LARGEST possible order. Measured on a real session: the
        # inventory screen refused to give SKU A a signal at all ("SIN_DATOS —
        # agrega stock actual para ver la señal") while this endpoint told the
        # same buyer to order 130 units of it, with a Convertir-en-OC button
        # next to the number. Two screens, one SKU, opposite advice, and the one
        # with the buy button was the one that had invented its input.
        #
        # `unit_cost` rides along for the same reason: with no cost on file the
        # solve runs on _DEFAULT_UNIT_COST = 1.0, so `total_cost` is a number
        # about nothing. The flags say which, so the screen can be honest
        # instead of the caller having to infer it from a null.
        orders.append({
            "sku": sku, "warehouse": w, "qty": int(_math.ceil(order_totals[(sku, w)])),
            "unit_cost": row.get("unit_cost"),
            "supplier": row.get("supplier"),
            # The solve ran on _DEFAULT_UNIT_COST = 1.0 for this line, so its
            # share of `total_cost` is a number about nothing. Stock is not
            # flagged here because a SKU with no stock on file never reaches
            # the solve at all — see skus_missing_stock.
            "assumed_unit_cost": row.get("unit_cost") is None,
        })

    transfers = []
    for (sku, a, b) in sorted(transfer_totals):
        transfers.append({
            "sku": sku, "from_warehouse": a, "to_warehouse": b,
            "qty": int(_math.ceil(transfer_totals[(sku, a, b)])),
        })

    return {
        "status": result.status,
        "total_cost": round(result.total_cost, 2),
        "horizon_days": inp.horizon,
        "orders": orders,
        "transfers": transfers,
    }
