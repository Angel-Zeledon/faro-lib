"""Reference implementation of the commitment fulfillment outlook.

The Rust module `backend-rs/src/fulfillment/core.rs` is the product code. This
file is the SPEC it is tested against: the same rules written the plainest way
possible, in Python, so that a differential test (`gen_fulfillment_fixtures.py`
-> `backend-rs/tests/fixtures/fulfillment_cases.json` -> `cargo test`) can
demand EXACT equality over thousands of seeded cases.

Question answered per open commitment: will it be met on its delivery date,
given stock, the open purchase orders with their expected arrival dates and the
other commitments competing for the same stock?

Rules (the order matters, it decides which reason a row gets):

* Units of a commitment are `quantity * probability`, the same expected units
  `committed_demand_service.allocate_risk` plans on. Commitments of one SKU are
  served earliest delivery first (ties: the input order). Commitment i competes
  with every earlier one: `cumulative_i` is the units due through i.
* Supply at delivery (`as_of = max(delivery, today)`) is stock plus every
  arrival with a KNOWN date on or before `as_of`. An arrival with no usable date
  (`day is None`: overdue, or no lead time to date it with) is never counted as
  certain: it only decides whether the answer is "unknown".
* No stock figure -> `insufficient_data` (`no_stock_figure`). Stock is never
  assumed to be zero.
* Covered by known supply -> `on_track`, unless the cover hangs on a purchase
  order landing fewer than `AT_RISK_SLACK_DAYS` days before delivery
  (`at_risk`, `covered_tight`).
* Short on known supply but the undated units would close the gap ->
  `insufficient_data` (`unknown_arrival`).
* Otherwise a real shortfall (counting undated units as arriving, so it is the
  MINIMUM miss when there are any). Then the lead time decides whether a new
  order placed today still arrives in time: delivery already passed ->
  `will_miss`; no lead time -> `insufficient_data` (`lead_time_unknown`, the
  shortfall is still shown); `today + lead <= delivery` -> `at_risk`
  (`order_in_time`); else `will_miss` (`order_too_late`).

Days are plain integers (`date.toordinal()`), floats are IEEE doubles and every
sum is taken in the order written here, so the Rust port can match bit for bit.
"""

from __future__ import annotations

import math
from typing import Optional

EPS = 1e-9
AT_RISK_SLACK_DAYS = 3
MIN_LEAD_TIME_OBSERVATIONS = 3

ON_TRACK, AT_RISK, WILL_MISS, INSUFFICIENT = "on_track", "at_risk", "will_miss", "insufficient_data"
VERDICTS = (ON_TRACK, AT_RISK, WILL_MISS, INSUFFICIENT)


def _sum_known(arrivals, as_of: int) -> float:
    total = 0.0
    for a in arrivals:
        if a["day"] is not None and a["day"] <= as_of:
            total += max(0.0, a["qty"])
    return total


def _undated(arrivals) -> float:
    total = 0.0
    for a in arrivals:
        if a["day"] is None:
            total += max(0.0, a["qty"])
    return total


def _cover(base: float, need: float, arrivals, today: int):
    """Earliest day known supply reaches `need`: (day, source, arrival index)
    or None. Source: stock | incoming (a transfer) | purchase_order (the
    arrival that closed the gap, whose position in `arrivals` is the index)."""
    if base >= need - EPS:
        return today, "stock", None
    known = [(a["day"], i, a) for i, a in enumerate(arrivals) if a["day"] is not None]
    known.sort(key=lambda t: (t[0], t[1]))
    acc = base
    for day, _i, a in known:
        acc += max(0.0, a["qty"])
        if acc >= need - EPS:
            return max(day, today), ("purchase_order" if a["kind"] == "po" else "incoming"), _i
    return None


def evaluate_sku(commitments: list, stock: Optional[float], lead_days: Optional[int],
                 arrivals: list, today: int) -> list:
    """`commitments`: [{id, delivery_day, quantity, probability}]; `arrivals`:
    [{day|None, qty, kind: po|transfer}]. Returns one outcome per commitment, in
    served order."""
    ordered = sorted(enumerate(commitments), key=lambda t: (t[1]["delivery_day"], t[0]))
    out = []
    cumulative = 0.0
    base = None if stock is None else max(0.0, stock)
    undated = _undated(arrivals)
    for _i, c in ordered:
        units = c["quantity"] * c["probability"]
        cumulative += units
        delivery = c["delivery_day"]
        row = {
            "id": c["id"], "verdict": None, "reason": None, "units": units,
            "cumulative_units": cumulative, "supply_units": None,
            "shortfall_units": None, "shortfall_is_minimum": False,
            "shortfall_worst_case": None, "undated_units": undated,
            "cover_day": None, "cover_source": None, "slack_days": None,
            "late_days": None, "latest_safe_order_day": None, "order_date_passed": None,
            "driver_index": None,
        }
        out.append(row)
        if base is None:
            row["verdict"], row["reason"] = INSUFFICIENT, "no_stock_figure"
            continue
        as_of = max(delivery, today)
        supply_known = base + _sum_known(arrivals, as_of)
        row["supply_units"] = supply_known
        short_known = min(units, max(0.0, cumulative - supply_known))
        if short_known <= EPS:
            row["shortfall_units"] = 0.0
            cover = _cover(base, cumulative, arrivals, today)
            if cover is None:  # float dust only: covered at the as_of date
                cover = (as_of, "incoming", None)
            row["cover_day"], row["cover_source"], row["driver_index"] = cover
            if cover[1] == "purchase_order":
                row["slack_days"] = delivery - cover[0]
            if cover[1] == "purchase_order" and delivery - cover[0] < AT_RISK_SLACK_DAYS:
                row["verdict"], row["reason"] = AT_RISK, "covered_tight"
            else:
                row["verdict"] = ON_TRACK
                row["reason"] = "covered_by_stock" if cover[1] == "stock" else "covered_by_arrivals"
            continue
        supply_best = supply_known + undated
        short_best = min(units, max(0.0, cumulative - supply_best))
        if short_best <= EPS:
            row["verdict"], row["reason"] = INSUFFICIENT, "unknown_arrival"
            row["shortfall_worst_case"] = short_known
            continue
        row["shortfall_units"] = short_best
        row["shortfall_is_minimum"] = undated > 0
        row["shortfall_worst_case"] = short_known
        if lead_days is not None:
            row["latest_safe_order_day"] = delivery - lead_days
            row["order_date_passed"] = delivery - lead_days < today
        candidates = []
        late = _cover(base, cumulative, arrivals, today)
        if late is not None:
            candidates.append(late)
        if lead_days is not None:
            candidates.append((today + lead_days, "new_order", None))
        if candidates:
            best = min(candidates, key=lambda t: (t[0], 0 if t[1] == "purchase_order" else 1))
            row["cover_day"], row["cover_source"], row["driver_index"] = best
            if best[0] > delivery:
                row["late_days"] = best[0] - delivery
        if delivery < today:
            row["verdict"], row["reason"] = WILL_MISS, "delivery_passed"
        elif lead_days is None:
            row["verdict"], row["reason"] = INSUFFICIENT, "lead_time_unknown"
        elif today + lead_days <= delivery:
            row["verdict"], row["reason"] = AT_RISK, "order_in_time"
        else:
            row["verdict"], row["reason"] = WILL_MISS, "order_too_late"
    return out


# ── Arrival of one purchase-order line ───────────────────────────────────────

def po_lead_days(obs_avg: Optional[float], obs_n: int, card_days: Optional[int],
                 card_declared: bool) -> Optional[int]:
    """The supplier's lead time for dating an open order: learned from at least
    three real receptions (average above zero, rounded up), else the supplier
    card's figure when somebody declared it, else None. Never the 15-day system
    default: an assumed number is not a date."""
    if obs_n >= MIN_LEAD_TIME_OBSERVATIONS and obs_avg is not None and obs_avg > 0:
        return int(math.ceil(obs_avg))
    if card_days is not None and card_declared:
        return int(card_days)
    return None


def po_line_arrival(generated_day: int, lead_days: Optional[int],
                    promise_day: Optional[int], today: int) -> dict:
    """Where one order line stands: a date a person accepted from the supplier
    wins; else generated + lead time; no lead time -> no date. A date already
    in the past while the goods have not arrived is not a date to plan on."""
    if promise_day is not None:
        expected, source = promise_day, "supplier_promise"
    elif lead_days is not None:
        expected, source = generated_day + lead_days, "lead_time"
    else:
        return {"day": None, "expected_day": None, "source": "no_lead_time"}
    if expected < today:
        return {"day": None, "expected_day": expected, "source": "overdue"}
    return {"day": expected, "expected_day": expected, "source": source}


# ── The lead time of a SKU (what a new order would take) ─────────────────────

def py_round_half_even(x: float) -> int:
    return int(round(x))


def sku_lead_days(row_days: Optional[int], row_declared: bool, rule_days: Optional[int],
                  learned_avg: Optional[float]):
    """(days, source) or None. Same precedence as the semaforo's cascade
    (`resolve_planning_inputs`): the lead time learned from the supplier's real
    receptions, else a value somebody set on the SKU, else the narrowest
    supplier / category / global rule. The system default is None: nobody told us."""
    if learned_avg is not None and learned_avg > 0:
        return max(1, py_round_half_even(learned_avg)), "learned"
    if row_declared and row_days is not None:
        return max(1, int(row_days)), "sku"
    if rule_days is not None:
        return max(1, int(rule_days)), "rule"
    return None


# ── Roll-up ───────────────────────────────────────────────────────────────────

def summarize(rows: list) -> dict:
    """`rows`: [{verdict, units, shortfall_units|None, shortfall_is_minimum,
    delivery_day}]."""
    out = {"total": len(rows), "on_track": 0, "at_risk": 0, "will_miss": 0,
           "insufficient_data": 0, "units": 0.0, "shortfall_units": 0.0,
           "shortfall_has_minimum": False, "first_problem_day": None}
    for r in rows:
        out[r["verdict"]] += 1
        out["units"] += r["units"]
        if r["shortfall_units"] is not None:
            out["shortfall_units"] += r["shortfall_units"]
            if r["shortfall_is_minimum"]:
                out["shortfall_has_minimum"] = True
        if r["verdict"] in (AT_RISK, WILL_MISS):
            d = r["delivery_day"]
            if out["first_problem_day"] is None or d < out["first_problem_day"]:
                out["first_problem_day"] = d
    return out
