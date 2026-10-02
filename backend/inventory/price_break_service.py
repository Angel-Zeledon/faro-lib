"""
Supplier price breaks (feature 3.5).

Two responsibilities:

1. CRUD over `supplier_price_breaks` — the quantity scale a supplier offers for
   a SKU ("from 100 units on, each unit costs 7.80 instead of 8.50").

2. Deciding whether stepping UP to the next break is actually worth it. That
   decision is the whole point of the feature and it is deliberately NOT "the
   unit price went down, therefore buy more".

The criterion — why it is not just the unit price
-------------------------------------------------
Buying past what you need immobilizes cash and can turn into overstock, so a
lower unit price is only half the trade. We compare, in money, both halves:

  GROSS SAVING. Going from q0 to q1 units (q1 > q0, unit price p1 < p0) is not
  "buying more"; those extra units are units the business will consume anyway
  at demand `d`. The honest comparison is therefore against buying them LATER
  at the current price p0:
      buy later : q0*p0 + (q1-q0)*p0 = q1*p0
      buy now   : q1*p1                          (ALL-UNITS discount)
      gross_saving = q1 * (p0 - p1)

  HOLDING COST. Against the same "buy them later" alternative, done the way
  the buyer already buys: in the q0-sized orders they would otherwise keep
  placing. Stock falls linearly at d, so a batch landing on S units left holds
  (S + q)^2 / 2d unit-days until it is gone.
      buy now   : (S + q1)^2 / 2d
      buy later : (S + q0)^2 / 2d  +  ((q1-q0)/q0) batches of q0^2 / 2d
      extra unit-days = (q1-q0) * (2S + q1) / 2d
  S is the stock left when the order lands — current stock minus lead-time
  demand, never below 0. Priced at the daily holding rate
  p1 * holding_cost_pct / 365, the standard carrying rate, which already
  includes the cost of capital ("immobilizing cash" in money terms).
      holding_cost = (q1-q0) * (2S + q1) / (2d) * p1 * holding_cost_pct / 365

  This used to be (q1-q0)^2 / 2d — the special case S = 0 AND q0 = 0. It
  ignored that the extra units cannot start selling until the shelf and the
  order already being placed are gone, so it understated the cost of stepping
  up exactly when the shelf was full (math audit 2026-10-01).

  net_saving = gross_saving - holding_cost

Three guardrails on top of a positive net saving, because money is not the only
way a bigger order can be wrong:

  a) NO DEMAND, NO DEAL. d <= 0 means the forecast projects no sales; coverage
     would be infinite and the "we'd buy them later anyway" premise collapses.
     Never recommended, whatever the discount.

  b) NEVER RECOMMEND WHAT THE SEMÁFORO WILL CALL OVERSTOCK. StockAI paints a SKU
     SOBRESTOCK at coverage >= lead_time * overstock_factor (service._calc_signal;
     the factor is the SKU's resolved `signal_thresholds`, 3 by default). Advising a
     purchase this same product would flag as overstock tomorrow destroys the
     semáforo's credibility, which is the product. We also cap at 90 days in
     absolute terms for obsolescence/perishability risk on long lead times.
     limit = min(lead_time * overstock_factor, MAX_COVERAGE_DAYS)

  c) MATERIALITY. A net saving of a few colones is not worth a nudge, an extra
     decision or the risk of being wrong about the forecast. It must be at
     least MIN_NET_SAVING_PCT of the stepped-up order value.

Every rejected opportunity is still returned, with `worth_it = False` and a
`reason_code` naming which rule killed it — the UI can then explain "you'd save
8% but it becomes 140 days of stock" instead of silently hiding the scale.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.db.connection import query, query_one, execute
from backend.inventory.service import _days_per_period
from backend.inventory import signal_thresholds

log = logging.getLogger(__name__)

# Fallback annual inventory-carrying rate when the session carries no
# business_cfg. Same default as optimizer_service.build_optimization_input, so
# both features price holding the same way.
DEFAULT_HOLDING_COST_PCT = 0.20

# Absolute ceiling on the coverage a price-break recommendation may create,
# independent of lead time. A 60-day lead time would otherwise allow 180 days of
# stock, which is obsolescence risk no discount pays for.
MAX_COVERAGE_DAYS = 90.0

# Minimum net saving, as a fraction of the stepped-up order value, for the
# opportunity to be worth interrupting the buyer.
MIN_NET_SAVING_PCT = 0.01


# ── CRUD ──────────────────────────────────────────────────────────────────────

def list_price_breaks(
    tenant_id: str,
    supplier_id: Optional[str] = None,
    sku: Optional[str] = None,
) -> list[dict]:
    """Price breaks for a tenant, optionally narrowed to a supplier and/or SKU.
    Always ordered by ascending min_qty — the evaluation logic below relies on
    that order to walk the scale from cheapest quantity upward."""
    sql = """SELECT pb.id, pb.supplier_id, pb.sku, pb.min_qty, pb.unit_price,
                    pb.notes, pb.created_at, s.name AS supplier_name
               FROM supplier_price_breaks pb
               JOIN suppliers s ON s.id = pb.supplier_id
              WHERE pb.tenant_id = %s AND s.active = TRUE"""
    params: list = [tenant_id]
    if supplier_id:
        sql += " AND pb.supplier_id = %s"
        params.append(supplier_id)
    if sku:
        sql += " AND pb.sku = %s"
        params.append(sku)
    sql += " ORDER BY pb.sku, pb.min_qty"
    return query(sql, tuple(params))


def upsert_price_break(
    tenant_id: str, supplier_id: str, sku: str,
    min_qty: float, unit_price: float, notes: Optional[str] = None,
) -> dict:
    """Creates or updates one rung of the scale. Re-sending the same
    (supplier, sku, min_qty) overwrites its price instead of duplicating the
    rung, so a scale can be corrected without deleting it first."""
    row = query_one(
        """INSERT INTO supplier_price_breaks
               (tenant_id, supplier_id, sku, min_qty, unit_price, notes)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, supplier_id, sku, min_qty) DO UPDATE
               SET unit_price = EXCLUDED.unit_price,
                   notes      = EXCLUDED.notes
           RETURNING *""",
        (tenant_id, supplier_id, sku, min_qty, unit_price, notes),
    )
    return row  # type: ignore[return-value]


def get_price_break(tenant_id: str, price_break_id: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM supplier_price_breaks WHERE tenant_id = %s AND id = %s",
        (tenant_id, price_break_id),
    )


def delete_price_break(tenant_id: str, price_break_id: str) -> None:
    execute(
        "DELETE FROM supplier_price_breaks WHERE tenant_id = %s AND id = %s",
        (tenant_id, price_break_id),
    )


# ── Pricing ───────────────────────────────────────────────────────────────────

def effective_unit_price(
    breaks: list[dict], quantity: float, base_cost: Optional[float],
) -> Optional[float]:
    """
    The unit price actually paid for `quantity` units under ALL-UNITS semantics:
    the highest rung whose min_qty the quantity reaches. Below the first rung
    (or with no scale at all) the SKU's own unit_cost applies, which may be
    None when the SKU has no cost captured.
    """
    price = base_cost
    for b in sorted(breaks, key=lambda x: float(x["min_qty"])):
        if quantity >= float(b["min_qty"]):
            price = float(b["unit_price"])
    return price


def _reject(candidate: dict, reason_code: str) -> dict:
    candidate["worth_it"] = False
    candidate["reason_code"] = reason_code
    return candidate


def evaluate_step_up(
    *,
    sku: str,
    supplier_name: Optional[str],
    current_quantity: float,
    base_cost: Optional[float],
    breaks: list[dict],
    daily_demand: Optional[float],
    current_stock: float,
    lead_time_days: int,
    holding_cost_pct: float = DEFAULT_HOLDING_COST_PCT,
    overstock_factor: Optional[float] = None,
) -> Optional[dict]:
    """
    Best price-break opportunity above `current_quantity` for one SKU, or None
    when the SKU has no rung above what is already being ordered (nothing to
    say) or lacks the data to reason about it.

    Every rung above the current quantity is evaluated and the one with the
    highest NET saving wins — not the nearest rung and not the cheapest unit
    price, both of which can be worse deals once holding cost is priced in.

    See the module docstring for the criterion and why each guardrail exists.
    """
    if base_cost is None or current_quantity <= 0 or not breaks:
        return None

    current_price = effective_unit_price(breaks, current_quantity, base_cost)
    if current_price is None:
        return None

    higher = [b for b in breaks if float(b["min_qty"]) > current_quantity]
    if not higher:
        return None

    # The SAME overstock multiple the semáforo judged this SKU by (the status
    # row's resolved `signal_thresholds`), never a private copy of it.
    if overstock_factor is None:
        overstock_factor = signal_thresholds.DEFAULT_OVERSTOCK_FACTOR
    coverage_limit = min(float(lead_time_days) * float(overstock_factor), MAX_COVERAGE_DAYS)
    demand = float(daily_demand or 0.0)

    candidates: list[dict] = []
    for b in higher:
        step_quantity = float(b["min_qty"])
        step_price = float(b["unit_price"])
        extra_units = step_quantity - current_quantity

        gross_saving = step_quantity * (current_price - step_price)

        if demand > 0:
            extra_coverage_days = extra_units / demand
            total_coverage_days = (current_stock + step_quantity) / demand
            # Unit-days the step-up adds over buying the same units later in
            # q0-sized orders — see the module docstring for the derivation.
            stock_at_arrival = max(0.0, float(current_stock) - demand * float(lead_time_days))
            extra_unit_days = (
                extra_units * (2.0 * stock_at_arrival + step_quantity) / (2.0 * demand)
            )
            holding_cost = extra_unit_days * step_price * holding_cost_pct / 365.0
        else:
            extra_coverage_days = None
            total_coverage_days = None
            holding_cost = 0.0

        net_saving = gross_saving - holding_cost

        candidate = {
            "sku": sku,
            "supplier_name": supplier_name,
            "current_quantity": round(current_quantity, 2),
            "step_quantity": round(step_quantity, 2),
            "extra_units": round(extra_units, 2),
            "current_unit_price": round(current_price, 4),
            "step_unit_price": round(step_price, 4),
            "unit_price_drop_pct": (
                round((current_price - step_price) / current_price, 4)
                if current_price > 0 else None
            ),
            "gross_saving": round(gross_saving, 2),
            "holding_cost": round(holding_cost, 2),
            "net_saving": round(net_saving, 2),
            "extra_coverage_days": (
                round(extra_coverage_days, 1) if extra_coverage_days is not None else None
            ),
            "total_coverage_days": (
                round(total_coverage_days, 1) if total_coverage_days is not None else None
            ),
            "coverage_limit_days": round(coverage_limit, 1),
            "extra_cash_now": round(step_quantity * step_price - current_quantity * current_price, 2),
            "worth_it": True,
            "reason_code": "worth_it",
        }

        # Guardrails, in the order they invalidate the reasoning.
        if step_price >= current_price:
            candidates.append(_reject(candidate, "no_discount"))
            continue
        if demand <= 0:
            candidates.append(_reject(candidate, "no_demand"))
            continue
        if total_coverage_days is not None and total_coverage_days > coverage_limit:
            candidates.append(_reject(candidate, "would_overstock"))
            continue
        if net_saving <= 0:
            candidates.append(_reject(candidate, "holding_exceeds_saving"))
            continue
        if net_saving < step_quantity * step_price * MIN_NET_SAVING_PCT:
            candidates.append(_reject(candidate, "saving_immaterial"))
            continue
        candidates.append(candidate)

    # Prefer a real opportunity; among those, the largest net saving. When none
    # qualifies, still return the closest rung so the UI can explain why the
    # visible discount is not being recommended.
    worth = [c for c in candidates if c["worth_it"]]
    if worth:
        return max(worth, key=lambda c: c["net_saving"])
    return min(candidates, key=lambda c: c["step_quantity"]) if candidates else None


def _ladders_for_status(
    ladders: list[dict], status: dict, cart_supplier_id: str | None = None,
) -> list[dict]:
    """
    The ladders that may legitimately be quoted for this SKU: the one belonging
    to the SKU's own supplier when there is one, otherwise all of them.

    `cart_supplier_id` is the supplier the BUYER has on the line right now, and
    it wins over everything else. The panel used to read the supplier off the
    status row only, so switching supplier on a line kept quoting the previous
    one's ladder — "Andina: order 500 and save ~1,400" about a price only Norte
    ever quoted, which is the exact defect this function's docstring says it
    fixed, reintroduced through the supplier-switch path (stability 11.14).

    `supplier_id` on the status row is only set when the stock's supplier name
    and the primary supplier agree (service.get_inventory_status), so the name
    is matched too — case-insensitively, which is how every other
    supplier-by-name lookup in the product resolves.
    """
    if cart_supplier_id:
        owned = [l for l in ladders if l["supplier_id"] == cart_supplier_id]
        # An empty list is the honest answer when the chosen supplier quotes no
        # ladder for this SKU: there is no offer to show, and falling through to
        # "all of them" is how another supplier's price gets printed under this
        # one's name.
        return owned

    supplier_id = status.get("supplier_id")
    if supplier_id:
        owned = [l for l in ladders if l["supplier_id"] == supplier_id]
        if owned:
            return owned

    name = (status.get("supplier") or "").strip().lower()
    if name:
        owned = [l for l in ladders if (l["supplier_name"] or "").strip().lower() == name]
        if owned:
            return owned

    return ladders


def evaluate_cart(
    tenant_id: str,
    cart_items: list[dict],
    status_items: list[dict],
    holding_cost_pct: float = DEFAULT_HOLDING_COST_PCT,
    period: str = "daily",
) -> list[dict]:
    """
    Runs evaluate_step_up over a cart the browser sends in.

    `period` is the tenant's planning grain. `status["daily_demand"]` is the
    forecast PER BUCKET of that grain (per week on a weekly tenant), while every
    figure in evaluate_step_up is in days — lead time, the coverage limit, the
    /365 holding rate. It is converted here; without it a weekly tenant's
    coverage came out 7x short and its holding cost 7x low, so step-ups past
    the overstock line were recommended (math audit 2026-10-01).

    `cart_items` are {sku, quantity} as the buyer currently has them (which may
    differ from what StockAI recommended — the buyer can edit quantities); the
    demand/stock/lead-time inputs come from `status_items`, i.e. from
    service.get_inventory_status, never from the client.

    A ladder belongs to a (SKU, SUPPLIER) pair, never to a SKU
    ----------------------------------------------------------
    Two suppliers quoting the same SKU are two independent scales. Grouping the
    rungs by SKU alone merged them into one imaginary ladder and then credited
    the whole thing to whichever supplier happened to own the lowest rung, so
    the panel could say "Andina: order 500 and save ~1400" about a price only
    Norte ever quoted — a number the buyer cannot act on and a supplier who will
    deny it on the phone.

    Each supplier's ladder is therefore evaluated on its own. When the SKU has a
    known supplier, only that supplier's ladder is considered: this panel
    compares two quantities from the SAME supplier, not two suppliers (nothing
    in it prices a supplier switch — see the docstring at the top of the
    module). When it does not, every ladder is evaluated separately and the best
    opportunity wins, still named after the supplier that actually quoted it.
    """
    status_by_sku = {i["sku"]: i for i in status_items}
    ladders_by_sku: dict[str, dict[str, dict]] = {}
    for b in list_price_breaks(tenant_id):
        ladder = ladders_by_sku.setdefault(b["sku"], {}).setdefault(
            b["supplier_id"],
            {"supplier_id": b["supplier_id"],
             "supplier_name": b.get("supplier_name"),
             "breaks": []},
        )
        ladder["breaks"].append(b)

    results: list[dict] = []
    for line in cart_items:
        sku = line.get("sku")
        quantity = float(line.get("quantity") or 0)
        # The supplier the buyer has on the line, when the client sends one.
        cart_supplier_id = line.get("supplier_id")
        status = status_by_sku.get(sku or "")
        ladders = list(ladders_by_sku.get(sku or "", {}).values())
        if not sku or not ladders or not status:
            continue

        opportunities = [
            opportunity
            for ladder in _ladders_for_status(ladders, status, cart_supplier_id)
            if (opportunity := evaluate_step_up(
                sku=sku,
                supplier_name=ladder["supplier_name"],
                current_quantity=quantity,
                base_cost=status.get("unit_cost"),
                breaks=ladder["breaks"],
                daily_demand=(
                    float(status["daily_demand"]) / _days_per_period(period)
                    if status.get("daily_demand") is not None else None
                ),
                current_stock=float(status.get("current_stock") or 0),
                lead_time_days=int(status.get("lead_time_days") or 15),
                holding_cost_pct=holding_cost_pct,
                overstock_factor=(status.get("signal_thresholds") or {}).get(
                    "overstock_factor"),
            )) is not None
        ]
        if opportunities:
            # One line of the cart, one recommendation: the actionable ones
            # first, then the largest net saving — the same order the results
            # list uses below.
            results.append(
                min(opportunities, key=lambda o: (not o["worth_it"], -o["net_saving"])))

    # Actionable ones first, then by net saving.
    results.sort(key=lambda r: (not r["worth_it"], -r["net_saving"]))
    return results
