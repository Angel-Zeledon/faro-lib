"""The conversion rules of multi-currency, as a plain Python reference.

This module is the SPEC of the Rust conversion core (`backend-rs/src/fx.rs`):
the two are kept in step by a differential test (`tests/test_fx_reference.py`
replays seeded cases against the committed vectors, and
`tests/contract/fx_differential.py` runs fresh seeds against the Rust binary).
Python paths that sum money (purchase budgets, PO totals, the cash calendar)
use THESE functions, never their own arithmetic.

The rules, all of them:

1. **Exact arithmetic.** No float touches a conversion. A float that comes from
   the database or the API is first turned into the decimal its shortest
   round-trip text spells (`Decimal(repr(x))`), so `0.1` is one tenth, not
   `0.1000000000000000055...`. Intermediate products are exact (no rounding).
2. **One rounding, at the end, explicit.** A converted line value is
   `qty x unit_cost x rate` rounded ONCE to 2 decimals, half up (ties go away
   from zero; every amount here is non-negative). Two decimals are the
   product's storage precision for money in every currency; the display
   precision of a currency (0 for CRC) is a rendering concern, not a storage one.
3. **The rate is looked up as of the document's date.** Among the tenant's
   rates for (currency -> base) the one with the latest `effective_date` that is
   not after the date wins. A rate dated in the future is not used. There is no
   fallback of any kind: no rate means no conversion.
4. **A missing rate is never 1.0.** `resolve_rate` returns None and callers
   carry "unconverted" forward (a count and a warning), they do not substitute.
5. **A line already in the base currency is not converted** (and never needs a
   rate). A document's total is the exact sum of its 2-decimal line values.
6. **History is not re-converted.** The rate used is recorded on the document
   when it is written; later rates, edits and deletions of rate rows do not move
   it.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional, Sequence

# Wide enough that no product of three in-range values is ever rounded by the
# context itself (qty and cost up to ~1e17 digits, rate up to 24 digits).
_CTX = Context(prec=200)
_TWO_PLACES = Decimal("0.01")
# Precision a stored rate carries: NUMERIC(24, 10).
RATE_DECIMALS = 10
RATE_MAX = Decimal("1000000000")          # 1e9: no real exchange rate is beyond this
RATE_MIN = Decimal("0.0000000001")        # 1e-10: the smallest storable positive rate
# Any input amount must be below this and spell at most MAX_DECIMALS decimals.
AMOUNT_LIMIT = Decimal(10) ** 30
MAX_DECIMALS = 40


_NUMBER = re.compile(r"\+?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", re.ASCII)


class FxError(ValueError):
    """An input the rules refuse (not a number, negative, too precise)."""


def to_decimal(value: Any) -> Decimal:
    """float / int / str / Decimal -> exact Decimal. Never goes through the
    binary value of a float; refuses NaN, infinities and negatives."""
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, bool):
        raise FxError("boolean is not an amount")
    elif isinstance(value, float):
        d = Decimal(repr(value))
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, str):
        text = value.strip()
        # One grammar, shared with the Rust core: optional +, digits with an
        # optional fraction (or just a fraction), optional exponent. No sign
        # minus, no underscores, no "NaN" / "Infinity".
        if not _NUMBER.fullmatch(text):
            raise FxError(f"not a number: {value!r}")
        try:
            d = Decimal(text)
        except InvalidOperation as exc:
            raise FxError(f"not a number: {value!r}") from exc
    else:
        raise FxError(f"not a number: {value!r}")
    if not d.is_finite():
        raise FxError("amount must be finite")
    if d == 0:
        d = d.copy_abs()  # never "-0.00"
    if d < 0:
        raise FxError("amount must not be negative")
    # Bounds shared with the Rust core, so both refuse the same inputs.
    if d >= AMOUNT_LIMIT:
        raise FxError("amount too large")
    if -d.as_tuple().exponent > MAX_DECIMALS:
        raise FxError("amount has too many decimals")
    return d


def quantize_money(value: Decimal) -> Decimal:
    """The one rounding rule: 2 decimals, half up."""
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP, context=_CTX)


def parse_rate(value: Any) -> Decimal:
    """A user-entered rate: positive, at most 10 decimals (refused, never
    silently rounded), within [RATE_MIN, RATE_MAX]."""
    d = to_decimal(value)
    if d < RATE_MIN or d > RATE_MAX:
        raise FxError("rate out of range")
    if d != d.quantize(Decimal(1).scaleb(-RATE_DECIMALS), context=_CTX):
        raise FxError("rate has more than 10 decimals")
    return d


def line_value(qty: Any, unit_cost: Any) -> Decimal:
    """Exact qty x unit_cost in the line's own currency (not rounded)."""
    return _CTX.multiply(to_decimal(qty), to_decimal(unit_cost))


def convert(amount: Any, rate: Any) -> Decimal:
    """`amount` (in the foreign currency) x `rate`, rounded once (rule 2)."""
    return quantize_money(_CTX.multiply(to_decimal(amount), to_decimal(rate)))


def convert_line(qty: Any, unit_cost: Any, rate: Any) -> Decimal:
    """A purchase-order line's value in the base currency: qty x cost x rate,
    exact until the single final rounding."""
    return quantize_money(_CTX.multiply(line_value(qty, unit_cost), to_decimal(rate)))


def base_line_value(qty: Any, unit_cost: Any) -> Decimal:
    """A line that is already in the base currency, at the 2-decimal precision
    totals are summed at."""
    return quantize_money(line_value(qty, unit_cost))


def resolve_rate(rates: Iterable[Mapping[str, Any]], currency: str, base_currency: str,
                 as_of: date) -> Optional[Mapping[str, Any]]:
    """The rate row in force on `as_of` for currency -> base_currency: the latest
    `effective_date` that is not after `as_of`, or None (rule 3, rule 4).
    Rows: mappings with `currency`, `base_currency`, `effective_date`."""
    best = None
    for r in rates:
        if r["currency"] != currency or r["base_currency"] != base_currency:
            continue
        eff = r["effective_date"]
        if eff > as_of:
            continue
        if best is None or eff > best["effective_date"]:
            best = r
    return best


def total(values: Sequence[Decimal]) -> Decimal:
    """Exact sum of 2-decimal values."""
    acc = Decimal(0)
    for v in values:
        acc = _CTX.add(acc, v)
    return acc


def money_text(value: Decimal) -> str:
    """The canonical text of a 2-decimal amount ("1234.50"), what the Rust core
    prints too."""
    return format(quantize_money(value), "f")
