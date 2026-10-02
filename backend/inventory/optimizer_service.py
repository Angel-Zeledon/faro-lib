"""
Assembles a forecasting_core MILP OptimizationInput from live DB state
(inventory_stock across warehouses, session forecasts, business_cfg), and
collapses an OptimizationResult back into one actionable total per line.

The model itself — variables, constraints, objective — is documented in
`forecasting_core/business/optimizer.py`'s module docstring. This module only
does the translation between the database and that model.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Optional

from forecasting_core.business.optimizer import OptimizationInput

import math as _math

from backend.db import session_store
from backend.inventory import stock_defaults_service as sd_svc
from backend.inventory import supplier_service as sup_svc
from backend.inventory import transfer_lane_service as lane_svc
from backend.inventory import warehouse_service as wh_svc
from backend.inventory.defaults import DEFAULT_LEAD_TIME_DAYS as _DEFAULT_LEAD_TIME_DAYS
from backend.inventory.defaults import DEFAULT_MOQ as _DEFAULT_MOQ
from backend.inventory.series import for_store, rollup_by_sku, stores_in
from backend.inventory.service import (
    _aggregate_stock_rows_by_sku,
    _avg_forecast_curve,
    _days_per_period,
    _resolve_review_period_days,
    get_learned_lead_times,
    list_stock,
    resolve_lead_time,
)

log = logging.getLogger(__name__)

_DEFAULT_UNIT_COST = 1.0


def _usable_unit_cost(value) -> Optional[float]:
    """The unit cost the optimizer can actually reason with, or None.

    Zero is not a price, it is a blank that happens to be a number — and to a
    cost minimizer the difference is total. With unit_cost = 0 the SKU's order
    cost, holding cost and stockout penalty are ALL zero, so leaving its demand
    unmet costs the objective nothing: the solver drops it, `/planning` shows
    no line for it, and `/hoy` goes on painting it PEDIR_YA. The old check was
    `if row["unit_cost"] is not None`, and a stored 0.0 sailed through it, so
    the SKU vanished from the plan without ever being flagged as assumed.
    Negative costs are refused for the same reason: they would pay the solver
    to buy.
    """
    if value is None:
        return None
    cost = float(value)
    return cost if cost > 0 else None

# A MILP solve is CPU-bound and can take tens of seconds. FastAPI runs sync
# endpoints on a bounded thread pool, so an unthrottled burst of /optimize
# requests can occupy every worker thread at once and wedge the whole process
# (QA drove /health to time out for 8+ minutes). This bounded gate caps how
# many solves run concurrently; requests beyond the cap are rejected fast
# (OptimizerBusy -> 503 retry) instead of piling onto the thread pool.
#
# ONE, because one is how many solves the engine can actually run.
#
# Every HiGHS solve in the process is submitted to a single dedicated thread
# (`forecasting_core.business.optimizer._SOLVE_EXECUTOR`, max_workers=1 — see the
# long comment there; entering HiGHS from different OS threads is what deadlocked
# it, and that fix must not be undone). The cap was 2, so a second admitted
# caller did not solve in parallel: it QUEUED behind the first on that thread.
# And the caller's wait — `.result(timeout=time_limit_s + grace)` — starts when
# the future is SUBMITTED, so the queue time is spent out of it. A first solve
# that runs near its 10s limit expires the second caller's 15s wait before its
# own solve has begun, and `optimize()` treats a timed-out wait like any other
# unsolved case: it returns the greedy `status="fallback"` plan, which ignores
# transfers entirely.
#
# The result was two buyers hitting /planning seconds apart and getting two
# different answers for the same catalogue, each with a button that turns it
# into a purchase order — and the divergence depended on who arrived second.
#
# With the cap at 1 the second caller is refused immediately and honestly (503,
# retry) instead of being served a silently different plan. Nothing is lost:
# the second slot never bought concurrency, only a wait that ended in a
# downgrade. `test_solver_gate_is_total.py` pins the cap to the executor's real
# width, so raising one without the other fails there.
_MAX_CONCURRENT_SOLVES = 1
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


def _net_transfer_moves(
    transfer_totals: dict[tuple[str, str, str], float],
) -> list[dict]:
    """Per-bucket transfer variables → the smallest set of moves that means the same.

    A lane the tenant has not configured defaults to free, and free movement
    makes CIRCULATION cost the solver nothing. Measured on a three-warehouse
    tenant: "mover 2126 uds de SKU-001 de Cartago a Heredia" printed directly
    above "mover 1518 de Heredia a Cartago", and SKU-005 came out as a clean
    three-way ring (Cartago→principal→Heredia→Cartago, 831 each) that returns
    every unit where it started. Summed over buckets the quantities also
    exceeded the stock that exists, because opposite moves accumulate on both
    sides. A warehouse worker would have driven all of it.

    Cancelling pairs only removes 2-cycles, so this works from what physically
    matters instead: each warehouse's NET change for that SKU. Warehouses that
    end up net-negative ship, net-positive receive, and they are paired off
    largest-first. The result carries the same net effect per location, can
    contain no cycle of any length, and never moves a unit twice.
    """
    by_sku: dict[str, dict[str, float]] = {}
    for (sku, a, b), qty in transfer_totals.items():
        if qty <= 0:
            continue
        balance = by_sku.setdefault(sku, {})
        balance[a] = balance.get(a, 0.0) - qty      # ships
        balance[b] = balance.get(b, 0.0) + qty      # receives

    moves: list[dict] = []
    for sku in sorted(by_sku):
        balance = by_sku[sku]
        # Whole units, and rounded the way each side is used: a shipper rounds
        # DOWN (never send more than the plan says leaves) and a receiver rounds
        # DOWN too, so the pairing can never invent stock.
        senders = sorted(
            ((w, -q) for w, q in balance.items() if q < -0.5),
            key=lambda pair: -pair[1])
        receivers = sorted(
            ((w, q) for w, q in balance.items() if q > 0.5),
            key=lambda pair: -pair[1])
        si = ri = 0
        send_left = senders[si][1] if senders else 0.0
        recv_left = receivers[ri][1] if receivers else 0.0
        while si < len(senders) and ri < len(receivers):
            qty = min(send_left, recv_left)
            whole = int(_math.floor(qty))
            if whole > 0:
                moves.append({
                    "sku": sku,
                    "from_warehouse": senders[si][0],
                    "to_warehouse": receivers[ri][0],
                    "qty": whole,
                })
            send_left -= qty
            recv_left -= qty
            if send_left <= 0.5:
                si += 1
                send_left = senders[si][1] if si < len(senders) else 0.0
            if recv_left <= 0.5:
                ri += 1
                recv_left = receivers[ri][1] if ri < len(receivers) else 0.0
    return moves


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


def _demand_shares_for(tenant_id: str, warehouses: list[str]) -> dict[str, float]:
    """How a SKU's demand divides across `warehouses` — fractions summing to 1.

    Demand used to be split by where the stock already SAT
    (`stock0[(sku,w)] / total_stock`), which made the transfer half of the model
    self-defeating: a warehouse's need was defined as proportional to what it
    already had, so a store holding 0 units of a SKU it sells was assigned 0
    demand and could never be a transfer destination, while the central depot
    that already held everything got 100% of the demand and was told to buy
    more. The one real signal the product has for "where does this sell" is
    `warehouses.demand_share` (set in /bodegas), which is what the per-warehouse
    semáforo reads — so the optimizer reads the same thing and the two screens
    can no longer contradict each other.

    Restricted and renormalized to the warehouses the optimizer is planning for
    (those with stock rows): a share configured for a location that has no
    inventory row at all would otherwise silently swallow part of the demand.
    """
    raw = wh_svc.get_demand_shares(tenant_id)
    shares = {w: float(raw.get(w, 0.0)) for w in warehouses}
    total = sum(shares.values())
    if total > 0:
        return {w: s / total for w, s in shares.items()}
    # Every configured share belongs to a warehouse with no stock rows, so the
    # tenant has told us nothing about the locations we can actually plan for.
    # The whole demand goes to the default warehouse — the same answer
    # get_demand_shares itself gives when nobody has configured anything, and
    # the same name precedence the rest of the codebase uses. Deliberately NOT
    # an even split: spreading demand across warehouses on no evidence orders a
    # SKU into locations that have never stocked it.
    default = sorted(warehouses, key=wh_svc.name_precedence_key)[0]
    return {w: (1.0 if w == default else 0.0) for w in warehouses}


def resolve_planning_inputs(
    tenant_id: str,
    stock_rows: list[dict],
    *,
    learned_lead_times: Optional[dict] = None,
) -> dict[str, dict]:
    """Per-SKU lead time and MOQ — the SAME numbers the semáforo plans on.

    `{sku: {"supplier", "lead_time_days", "lead_time_source", "moq",
    "review_period_days"}}`.

    `review_period_days` is the supplier's declared order cadence, read the
    way the semáforo reads it (`service._resolve_review_period_days`): it is
    what decides how far past the lead time this SKU's plan has to reach (see
    `effective_horizon_buckets`).

    This function exists because the optimizer used to answer these two
    questions by itself, and its answers were not the product's answers. It read
    `inventory_stock.lead_time_days` RAW — the column whose value is 15 for
    every row nobody has ever edited, because that is the schema default — and
    it never read an MOQ at all. Meanwhile `/hoy` resolves both through the
    cascade in `stock_defaults_service.resolve_field` (SKU row that somebody
    actually set > supplier rule > category rule > global rule > system default)
    and then lets real receptions override the lead time
    (`service.resolve_lead_time`).

    Measured on one SKU: `/hoy` planned on 45 days *"aprendido de tus
    recepciones"* with a supplier MOQ of 500, while `/planning` solved on 15 days
    and offered 137 units. Both screens print a "convertir en OC" button. Two
    screens, two different purchase orders, same product, same afternoon.

    So there is no second resolution path here: this calls the semáforo's own
    resolvers, on the same representative row per SKU
    (`service._aggregate_stock_rows_by_sku`, anchored on the tenant's default
    warehouse) and the same supplier fallback (the stock row's free-text
    supplier, else the SKU's configured primary). Any future change to the
    cascade moves both screens at once, which is the only way they can stay
    equal.

    Every query here is once per request, never once per SKU — the same
    discipline `_compute_inventory_status` follows.
    """
    stock_map = _aggregate_stock_rows_by_sku(
        stock_rows, wh_svc.get_default_warehouse_name(tenant_id),
    )
    if learned_lead_times is None:
        learned_lead_times = get_learned_lead_times(tenant_id)
    try:
        primary_suppliers = sup_svc.get_primary_suppliers_map(tenant_id)
    except Exception as e:
        log.debug("primary supplier map lookup failed tenant=%s: %s", tenant_id, e)
        primary_suppliers = {}
    rule_index = sd_svc.build_rule_index(tenant_id)
    try:
        review_period_map = sup_svc.get_review_period_map(tenant_id)
    except Exception as e:
        log.debug("review period map lookup failed tenant=%s: %s", tenant_id, e)
        review_period_map = {}

    resolved: dict[str, dict] = {}
    for sku, stock in stock_map.items():
        primary = primary_suppliers.get(sku) or {}
        supplier = (stock.get("supplier") or None) or primary.get("supplier_name")
        category = stock.get("category")

        lt_cfg, lt_cfg_source, _scope = sd_svc.resolve_field(
            "lead_time_days", stock, rule_index, supplier=supplier, category=category,
        )
        lead_time_config = int(lt_cfg if lt_cfg is not None else _DEFAULT_LEAD_TIME_DAYS)
        lead_time, lead_time_source, _learned = resolve_lead_time(
            lead_time_config, supplier, learned_lead_times, lt_cfg_source,
        )

        moq_val, _moq_source, _moq_scope = sd_svc.resolve_field(
            "moq", stock, rule_index, supplier=supplier, category=category,
        )
        moq = float(moq_val if moq_val is not None else _DEFAULT_MOQ)

        resolved[sku] = {
            "supplier": supplier,
            "lead_time_days": max(1, int(lead_time)),
            "lead_time_source": lead_time_source,
            "moq": moq if moq > 0 else 1.0,
            "review_period_days": _resolve_review_period_days(supplier, review_period_map),
        }
    return resolved


def effective_horizon_buckets(
    horizon_buckets: int, lead_time_buckets: int, review_period_buckets: int,
) -> int:
    """How many buckets one SKU's plan must span: the configured horizon, or
    long enough to reach the next order's arrival, whichever is longer.

    Owner's decision for math audit O1 (2026-10-01). In the model an order
    arrives at bucket t only for t > lead time, so a SKU whose lead time is at
    least the horizon had every arrival bucket gated and was planned at 0 —
    with the default 15-day lead time and 14-day horizon, that was every SKU
    nobody had configured, on a screen that said the plan covers the next 14
    days.

    The order placed today arrives after `lead_time_buckets`; the one after it
    can be placed at the next review and arrives `review_period_buckets`
    later. So the plan has to reach `lead + review`. A review period of 0
    means no cadence was declared, and the next chance to order is then the
    next bucket — so it counts as 1, otherwise the horizon would end exactly
    on the lead time and still admit no arrival.

    `horizon > lead` with no declared cadence gives back the configured
    horizon unchanged, which is the case every existing plan was solved on.
    """
    reach = int(lead_time_buckets) + max(1, int(review_period_buckets))
    return max(int(horizon_buckets), reach)


def build_optimization_input(
    tenant_id: str,
    session_id: str,
    horizon_days: int = 14,
    stock_rows: Optional[list[dict]] = None,
    period: str = "daily",
    lanes: Optional[dict] = None,
    planning: Optional[dict[str, dict]] = None,
    incoming: Optional[dict] = None,
) -> Optional[OptimizationInput]:
    """
    `incoming`: preloaded `service.get_incoming_qty` ({(sku, warehouse): qty});
    fetched here when omitted.

    `horizon_days` is in CALENDAR DAYS, whatever the active period is — it is
    the caller's natural unit and the endpoint's query parameter. The MILP's
    buckets, however, are the ACTIVE PERIOD's buckets, because that is the unit
    every other input already speaks: a period-trained session forecasts
    per-period demand (a monthly session's values are units/month), and lead
    times are converted with `ceil(days / days_per_period)`. So the conversion
    happens here, once, and everything downstream is in buckets.

    This used to be the other way round and nothing agreed. The endpoint passed
    `horizon * days_per_period` (120 for a monthly plan of 4) and it was used
    as the bucket count, while the buckets were filled from a per-MONTH forecast
    curve and lead time was divided DOWN into 1 bucket. A monthly tenant got 120
    buckets of which four carried any demand, a 30-day supplier that appeared to
    deliver within one bucket, holding cost charged per DAY across buckets that
    were really months (understating carrying cost ~30x), and `n_vars` inflated
    30x — enough for six SKUs to cross the engine's `max_vars_before_fallback`
    and land every real weekly/monthly tenant permanently in the transfer-blind
    greedy fallback.

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

    `planning`: preloaded `resolve_planning_inputs` output ({sku: {lead_time_days,
    moq, ...}}) — the semáforo's own resolution of the supplier inputs. Same
    reason as `stock_rows`: the endpoint resolves it once and hands it to both
    this function and `serialize_optimization_result`, so the plan is BUILT and
    REPORTED on one set of numbers. Omit it and it is resolved here.
    """
    raw_forecasts: dict = session_store.get_forecasts(tenant_id, session_id) or {}
    # A session trained on sales history with a store column is keyed
    # "sku│store", not "sku". Read raw, those keys match no stock row, so every
    # SKU looked uncounted and the optimizer planned nothing at all. The rollup
    # is the SKU-level total (what `skus` and the shares path need); the raw
    # dict is kept because the per-store split below is the best demand signal
    # this product has.
    forecasts: dict = rollup_by_sku(raw_forecasts)

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

    if incoming is None:
        from backend.inventory.service import get_incoming_qty
        incoming = get_incoming_qty(tenant_id)

    # Lead time and MOQ come from the semáforo's cascade, not from the raw
    # column — see resolve_planning_inputs for what reading the column raw cost.
    if planning is None:
        planning = resolve_planning_inputs(tenant_id, stock_rows)

    business_cfg: dict = session_store.get_field(tenant_id, session_id, "business_cfg") or {}
    holding_cost_pct = float(business_cfg.get("holding_cost_pct", 0.20))
    stockout_cost_multiplier = float(business_cfg.get("stockout_cost_multiplier", 3.0))

    # ONE unit for the whole model: buckets of the active period. See the
    # docstring — the forecast curve and the lead-time conversion both speak it
    # already, so the horizon is what has to move.
    days_per_period = _days_per_period(period)
    horizon_buckets = max(1, _math.ceil(horizon_days / days_per_period))

    # Where each SKU's demand lives, in preference order — the SAME order the
    # per-warehouse semáforo uses (service.get_inventory_status_by_warehouse),
    # so a warehouse's need means the same thing on both screens:
    #   1. store-keyed forecasts, matched to warehouse names case-insensitively
    #      — a real per-location measurement of what sells there;
    #   2. the SKU-global forecast split by warehouses.demand_share.
    wh_by_lower = {w.lower().strip(): w for w in warehouses}
    per_wh_forecasts: dict[str, dict] = {}
    for store in stores_in(raw_forecasts):
        wh = wh_by_lower.get(store.lower().strip())
        # A store with no warehouse of that name has no stock rows either, so
        # there is nothing to plan for it — it is not a location this model can
        # buy into. Its demand is left out rather than reassigned to a
        # warehouse that does not serve it.
        if wh is not None:
            per_wh_forecasts[wh] = for_store(raw_forecasts, store)
    # If not one store name matched a warehouse, the store split would zero out
    # every location and the optimizer would confidently recommend nothing.
    # Fall back to the configured shares instead.
    shares = {} if per_wh_forecasts else _demand_shares_for(tenant_id, warehouses)

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

    # Each SKU's own horizon (O1): at least the configured one, and long enough
    # to reach the arrival of the order after today's. Resolved before the
    # demand is laid out because the curve has to reach that far.
    #
    # The lead time is the one this SKU is planned on, resolved ONCE for the
    # whole product (see resolve_planning_inputs). It used to be
    # `max(raw lead_time_days across the SKU's rows)`, which answered a
    # different question from every other screen: it ignored supplier and
    # category rules, ignored what the supplier's real receptions have taught
    # us, and could not tell a lead time somebody typed from the schema's
    # untouched 15. In the model's own buckets: for daily it is the day count;
    # for weekly/monthly the lead time rounded up to whole periods —
    # commensurable with `horizon_buckets`, both counts of the same bucket.
    own_horizon: dict[str, int] = {}
    for sku in skus:
        sku_planning = planning.get(sku) or {}
        raw_lead = int(sku_planning.get("lead_time_days") or _DEFAULT_LEAD_TIME_DAYS)
        lead_time_buckets[sku] = max(1, _math.ceil(raw_lead / days_per_period))
        review_days = float(sku_planning.get("review_period_days") or 0.0)
        review_buckets = _math.ceil(review_days / days_per_period) if review_days > 0 else 0
        own_horizon[sku] = effective_horizon_buckets(
            horizon_buckets, lead_time_buckets[sku], review_buckets)
    model_horizon = max(own_horizon.values()) if own_horizon else horizon_buckets

    def _bucketed(model_forecasts: dict, sku_horizon: int) -> tuple[list[float], bool]:
        """One forecast curve laid into the model's buckets.

        The curve's points are one per bucket of the active period already —
        a monthly session's step 0 is next month's units — so no rescaling
        happens here; step index IS bucket index.

        Inside the configured horizon a missing step stays 0, as it always
        has. In the EXTENSION (configured horizon .. the SKU's own horizon) a
        step the forecast does not reach is filled with the mean of the steps
        it does: a 45-day supplier on a 30-day forecast would otherwise be
        extended into zeros and planned at 0 again — the very defect the
        extension exists to remove. That is the same flat-rate assumption the
        semáforo makes for the whole protection interval (d * (L + R)), and the
        second return value says it was made so the line can say so.
        """
        series = [0.0] * model_horizon
        curve = _avg_forecast_curve(model_forecasts, max_steps=model_horizon)
        seen: set[int] = set()
        for point in curve:
            step = point["step"]
            if step < model_horizon:
                series[step] = point["value"]
                seen.add(step)
        extrapolated = False
        if curve and sku_horizon > horizon_buckets:
            mean = sum(p["value"] for p in curve) / len(curve)
            for step in range(horizon_buckets, sku_horizon):
                if step not in seen:
                    series[step] = mean
                    extrapolated = True
        return series, extrapolated

    demand_extrapolated: set[str] = set()

    for sku in skus:
        sku_rows = rows_by_sku.get(sku, {})

        # The MILP needs an opening balance for every (sku, warehouse) pair it
        # indexes, and a warehouse with no row for this SKU has no counted
        # quantity. 0 is the only assumption available — and it is now
        # harmless where it used to compound, because demand is no longer
        # derived from these numbers: a location with no share of the demand
        # gets no order regardless of what its opening balance says. A SKU
        # counted NOWHERE never reaches this loop (see skus_missing_stock).
        for w in warehouses:
            stock0[(sku, w)] = float(sku_rows[w]["current_stock"] or 0) if w in sku_rows else 0.0
            # The INVENTORY POSITION, not the shelf: what is already on its way
            # (sent POs, transfers in transit) — the same netting `/hoy`
            # applies in `service._calc_recommended`. Without it the plan
            # bought again, on the same screen, every unit the buyer had
            # ordered last week and the semáforo had already netted out: stock
            # 40 with 200 on order planned a 200-unit purchase (math audit
            # 2026-10-01). Counted as available from the first bucket, which is
            # exactly the inventory-position convention the semáforo uses.
            stock0[(sku, w)] += max(0.0, float(incoming.get((sku, w), 0.0)))

        if per_wh_forecasts:
            for w in warehouses:
                series, extrapolated = _bucketed(
                    per_wh_forecasts.get(w, {}).get(sku, {}), own_horizon[sku])
                demand[(sku, w)] = series
                if extrapolated:
                    demand_extrapolated.add(sku)
        else:
            total_curve, extrapolated = _bucketed(forecasts.get(sku, {}), own_horizon[sku])
            if extrapolated:
                demand_extrapolated.add(sku)
            for w in warehouses:
                share = shares.get(w, 0.0)
                demand[(sku, w)] = [v * share for v in total_curve]

        costs =[c for c in (_usable_unit_cost(row.get("unit_cost"))
                             for row in sku_rows.values()) if c is not None]
        unit_cost = max(costs) if costs else _DEFAULT_UNIT_COST

        order_cost[sku] = unit_cost
        # Carrying cost per unit per BUCKET, not per day: the objective charges
        # holding_cost once per bucket, and for a monthly plan a bucket is 30
        # days of warehousing. Left as the daily rate it understated the cost of
        # sitting on stock by exactly days_per_period (~30x monthly), which is
        # the side of the trade-off that decides between buying now and buying
        # later.
        holding_cost[sku] = unit_cost * holding_cost_pct / 365 * days_per_period
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
                _math.ceil(int(lane["lead_time_days"]) / days_per_period))
            transfer_fixed_cost_by_lane[(a, b)] = float(lane["fixed_cost"])

    inp = OptimizationInput(
        skus=skus,
        warehouses=warehouses,
        horizon=model_horizon,
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
        # Only the SKUs whose horizon is shorter than the model's. An empty
        # dict is the engine's pre-feature path, bit for bit, and that is what
        # every plan in which no SKU needed extending gets.
        horizon_by_sku={s: h for s, h in own_horizon.items() if h != model_horizon},
    )
    # Carried for serialize_optimization_result, which reports what each line
    # covers. Not engine input: the engine stays a pure function of the
    # dataclass fields.
    inp.configured_horizon_buckets = horizon_buckets
    inp.days_per_period = days_per_period
    inp.demand_extrapolated = demand_extrapolated
    return inp


def serialize_optimization_result(inp, result, stock_rows: list[dict],
                                  horizon_days: Optional[int] = None,
                                  planning: Optional[dict[str, dict]] = None) -> dict:
    """
    Collapses an OptimizationResult into one actionable total per (sku, warehouse) order
    and per (sku, from_warehouse, to_warehouse) transfer, dropping any with qty == 0.

    Args:
        inp: OptimizationInput — `inp.horizon` is a count of BUCKETS
        result: OptimizationResult from MILP solver
        stock_rows: list of dicts with {sku, warehouse, unit_cost, supplier}
        horizon_days: the horizon in CALENDAR DAYS, as the caller asked for it.
        planning: `resolve_planning_inputs` output. It carries the MOQ, which
            the MILP itself cannot express (the model has no minimum-order
            variable), so the floor is applied here — exactly as `/hoy` applies
            it, in `service._calc_recommended`. Omit it and no floor is applied,
            which is only correct for a caller that has no tenant to resolve
            one for; both endpoints pass it.

    `horizon_days` has to be passed in rather than read off `inp`, because
    `inp.horizon` counts buckets and a bucket is a month on a monthly plan. The
    response key is named `horizon_days` and the screen renders it straight into
    "cubrir los próximos {n} días" — so returning `inp.horizon` would have told a
    tenant on a four-month plan that the plan covers the next 4 DAYS. Falls back
    to the bucket count only when the caller gives nothing, which is the daily
    case where the two are equal anyway.

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
    # Whole units first, then the supplier's minimum. A SKU's MOQ is the floor
    # under ONE purchase order to that supplier — not a floor per warehouse and
    # not a pack multiple (`service._calc_recommended` says why: applied as
    # `ceil(need/moq)*moq` a need of 520 against a MOQ of 500 ordered 1000). So
    # the SKU's whole planned quantity is compared against the MOQ once, and any
    # shortfall is added to its largest line — the line most likely to be the
    # one actually placed, and a deterministic choice either way.
    qty_by_line: dict[tuple[str, str], int] = {
        line: int(_math.ceil(total)) for line, total in order_totals.items()
    }
    lines_by_sku: dict[str, list[tuple[str, str]]] = {}
    for line in qty_by_line:
        lines_by_sku.setdefault(line[0], []).append(line)
    for sku, lines in lines_by_sku.items():
        moq = int(_math.ceil(float((planning or {}).get(sku, {}).get("moq") or 0)))
        planned = sum(qty_by_line[line] for line in lines)
        # `planned > 0` keeps "nothing to order" meaning nothing: a well-stocked
        # SKU must not be handed a full minimum order out of nowhere.
        if planned > 0 and moq > planned:
            biggest = sorted(lines, key=lambda line: (-qty_by_line[line], line[1]))[0]
            qty_by_line[biggest] += moq - planned

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
            "sku": sku, "warehouse": w, "qty": qty_by_line[(sku, w)],
            "unit_cost": row.get("unit_cost"),
            "supplier": row.get("supplier"),
            # The solve ran on _DEFAULT_UNIT_COST = 1.0 for this line, so its
            # share of `total_cost` is a number about nothing. Stock is not
            # flagged here because a SKU with no stock on file never reaches
            # the solve at all — see skus_missing_stock.
            #
            # `_usable_unit_cost`, not `is None`: a stored 0.0 is a blank that
            # happens to be a number, and the solve substitutes the placeholder
            # for it exactly like a NULL (see that function). Checking `is None`
            # here reported those lines as priced on a real cost — the screen
            # printed a total and no warning, over a plan built on 1.0.
            "assumed_unit_cost": _usable_unit_cost(row.get("unit_cost")) is None,
            **_line_coverage(inp, sku),
        })

    transfers = _net_transfer_moves(transfer_totals)

    configured = getattr(inp, "configured_horizon_buckets", inp.horizon)
    return {
        "status": result.status,
        "total_cost": round(result.total_cost, 2),
        "horizon_days": horizon_days if horizon_days is not None else configured,
        # How many lines reach past the configured horizon, so the screen can
        # say once, above the list, that some lines cover more than it.
        "extended_lines": sum(1 for o in orders if o["horizon_extended"]),
        "orders": orders,
        "transfers": transfers,
    }


def _line_coverage(inp, sku: str) -> dict:
    """What one order line covers, said truthfully (O1).

    The screen used to print "cubre los próximos {horizon} días" over every
    line. For a SKU whose lead time reaches the horizon that was false twice
    over: the plan covered nothing, and when it did order, the units only
    start covering once they arrive. `effective_horizon_days` is how far this
    line's plan reaches (calendar days); `horizon_extended` says it was
    stretched past the configured horizon to reach the next order's arrival;
    `demand_extrapolated` says part of that stretch had no forecast and was
    planned at the forecast's average rate.
    """
    from forecasting_core.business.optimizer import sku_horizon
    dpp = getattr(inp, "days_per_period", 1)
    configured = getattr(inp, "configured_horizon_buckets", inp.horizon)
    own = sku_horizon(inp, sku)
    return {
        "effective_horizon_days": int(own * dpp),
        "horizon_extended": own > configured,
        "demand_extrapolated": sku in getattr(inp, "demand_extrapolated", set()),
    }
