"""The money-at-risk rules in the plainest Python: the spec the Rust core
(`backend-rs/src/fulfillment/money.rs`) is held to, case by case, with exact equality.

Pure arithmetic over exact decimals; standard library only; no database.

The rules:

* Eligible rows are the commitments with a `will_miss` or `at_risk` verdict.
  Every other verdict is `not_applicable` and is not in any total.
* Units are the row's shortfall, rounded to 2 decimals (half to even, on the
  EXACT binary value of the float, which is what `Decimal(x)` gives). A row with
  no shortfall figure is `no_shortfall`: excluded, never counted as 0 units.
* The unit price and unit cost are rounded to 4 decimals the same way. A price
  or cost that is missing, not finite, not above zero (or that rounds to zero),
  or absurdly large is NOT AVAILABLE: a zero is not a price. A row with no price
  is `no_price`: excluded from every total, counted in `excluded_no_price`.
* amount = units x price, rounded to cents HALF UP (ties away from zero).
* margin = units x (price - cost), rounded to cents half away from zero. It is
  signed (a unit cost above the price is a negative margin, not hidden). Without
  a cost the margin is `no_cost`: the amount still counts, the margin does not.
* Totals are the exact sum of the rows' ROUNDED cents (so the rows on screen add
  up to the total on screen), never a re-rounding of a sum.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal

UNITS_PLACES = 2
PRICE_PLACES = 4
MAX_UNITS = 1e12
MAX_PRICE = 1e9

_CTX = Context(prec=60)
ELIGIBLE = ("at_risk", "will_miss")


def scaled(x, places, cap):
    """x rounded half-even to `places` decimals as an integer count of that
    unit, or None when x is missing, not finite, negative or above `cap`."""
    if x is None or not math.isfinite(x) or x < 0 or x > cap:
        return None
    q = Decimal(x).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN, context=_CTX)
    return int(q.scaleb(places))


def positive_price(x):
    s = scaled(x, PRICE_PLACES, MAX_PRICE)
    return s if s else None  # None and 0 both mean "not available"


def _to_cents(micro):
    """micro = units(1e-2) x price(1e-4) = 1e-6 of a currency unit; cents are
    1e-2, so divide by 1e4, half away from zero."""
    d = Decimal(micro) / Decimal(10000)
    return int(d.quantize(Decimal(1), rounding=ROUND_HALF_UP, context=_CTX))


def money(verdict, shortfall, shortfall_is_minimum, price, cost):
    if verdict not in ELIGIBLE:
        return {"status": "not_applicable", "amount_cents": None, "margin_cents": None,
                "margin_status": "not_applicable", "is_minimum": False}
    units = scaled(shortfall, UNITS_PLACES, MAX_UNITS)
    if units is None:
        return {"status": "no_shortfall", "amount_cents": None, "margin_cents": None,
                "margin_status": "no_shortfall", "is_minimum": False}
    p = positive_price(price)
    if p is None:
        return {"status": "no_price", "amount_cents": None, "margin_cents": None,
                "margin_status": "no_price", "is_minimum": False}
    c = positive_price(cost)
    amount = _to_cents(units * p)
    if c is None:
        margin, margin_status = None, "no_cost"
    else:
        margin, margin_status = _to_cents(units * (p - c)), "computed"
    return {"status": "computed", "amount_cents": amount, "margin_cents": margin,
            "margin_status": margin_status, "is_minimum": bool(shortfall_is_minimum)}


def rollup(rows):
    t = {"eligible": 0, "computed": 0, "excluded_no_price": 0, "excluded_no_shortfall": 0,
         "amount_cents": 0, "has_minimum": False, "margin_rows": 0, "margin_cents": 0,
         "margin_excluded": 0}
    for r in rows:
        if r["status"] == "not_applicable":
            continue
        t["eligible"] += 1
        if r["status"] == "computed":
            t["computed"] += 1
            t["amount_cents"] += r["amount_cents"]
            t["has_minimum"] = t["has_minimum"] or r["is_minimum"]
        elif r["status"] == "no_price":
            t["excluded_no_price"] += 1
        else:
            t["excluded_no_shortfall"] += 1
        if r["margin_status"] == "computed":
            t["margin_rows"] += 1
            t["margin_cents"] += r["margin_cents"]
        else:
            t["margin_excluded"] += 1
    return t


def fmt_cents(c):
    """Exact decimal text of an integer number of cents, e.g. -5 -> '-0.05'."""
    if c is None:
        return None
    sign = "-" if c < 0 else ""
    c = abs(c)
    return f"{sign}{c // 100}.{c % 100:02d}"
