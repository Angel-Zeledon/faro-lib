"""Database side of multi-currency: rate lookups and pricing purchase-order lines.

The rules live in `reference.py`; this module only fetches rows and applies them.
Rate CRUD is Rust (`backend-rs/src/routes/fx_rates.rs`) and has no Python
twin: Python READS the same table, so a purchase order written by Python uses the
rates a person entered through the Rust routes.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from backend.db.connection import query
from backend.errors import AppError
from backend.fx import reference as ref

log = logging.getLogger(__name__)


def today_utc() -> date:
    """The date a document is converted at: the UTC date it is written on."""
    return datetime.now(timezone.utc).date()


def base_currency(tenant_id: str) -> str:
    from backend.api.v1.currency import currency_of
    return currency_of(tenant_id)["code"]


def supported_codes() -> set[str]:
    from backend.api.v1.currency import SUPPORTED
    return set(SUPPORTED)


def clean_line_currency(raw: Any, base: str) -> Optional[str]:
    """The currency a line is stored with: None when it is the tenant's own (no
    conversion is ever needed), the upper-cased ISO code when it is foreign.
    An unsupported code is refused: it could not be formatted or converted."""
    if raw is None:
        return None
    code = str(raw).strip().upper()
    if not code:
        return None
    if code not in supported_codes():
        raise AppError("currency_not_supported", f"Currency '{code}' is not supported.",
                       status_code=400,
                       params={"code": code, "supported": sorted(supported_codes())})
    return None if code == base else code


def rates_for(tenant_id: str, base: str, currencies: set[str], as_of: date,
              conn=None) -> dict[str, dict]:
    """{currency: the rate row in force on `as_of`} for the currencies that have
    one. A currency with no row is absent from the result (rule 4)."""
    if not currencies:
        return {}
    rows = query(
        """SELECT id, currency, base_currency, rate, effective_date
             FROM exchange_rates
            WHERE tenant_id = %s AND base_currency = %s AND currency = ANY(%s)
              AND effective_date <= %s
            ORDER BY currency, effective_date DESC""",
        (tenant_id, base, sorted(currencies), as_of), conn=conn)
    out: dict[str, dict] = {}
    for code in currencies:
        found = ref.resolve_rate(rows, code, base, as_of)
        if found is not None:
            out[code] = dict(found)
    return out


def _qty_of(line: dict) -> Any:
    qty = line.get("final_qty")
    if qty is None:
        qty = line.get("qty")
    if qty is None:
        qty = line.get("recommended_qty")
    return qty or 0


def price_lines(tenant_id: str, lines: list[dict], *, ordered: Optional[list[bool]] = None,
                as_of: Optional[date] = None, conn=None) -> dict:
    """Annotate purchase-order lines with their currency and the rate used.

    `lines` are dicts that may carry `currency` and `unit_cost`, and the ordered
    quantity as `final_qty` / `qty` / `recommended_qty`. Each line gets (in
    place) the columns the order stores: `currency` (None = tenant's own),
    `fx_base_currency`, `fx_rate`, `fx_rate_date`, `fx_rate_id`, `value_base`
    (Decimal or None). `ordered[i]` False marks a line that is not part of the
    order (a rejected recommendation): it still records its currency, but is
    never valued.

    Returns {"foreign": bool, "unconverted": int, "total": Decimal | None}:
    `total` is the order's value in the base currency (exact sum of the
    2-decimal line values) over the lines that could be valued, None when none
    could. It is only meaningful when `foreign` is True: with no foreign line
    anywhere the caller keeps the legacy float arithmetic, byte for byte.
    """
    base = base_currency(tenant_id)
    as_of = as_of or today_utc()
    for ln in lines:
        ln["currency"] = clean_line_currency(ln.get("currency"), base)
    foreign_codes = {ln["currency"] for ln in lines if ln["currency"]}
    rate_rows = rates_for(tenant_id, base, foreign_codes, as_of, conn=conn)

    values: list[Decimal] = []
    unconverted = 0
    for i, ln in enumerate(lines):
        in_order = True if ordered is None else bool(ordered[i])
        cost = ln.get("unit_cost")
        ln.update(fx_base_currency=None, fx_rate=None, fx_rate_date=None,
                  fx_rate_id=None, value_base=None)
        if ln["currency"] is None:
            if in_order and cost is not None:
                values.append(ref.base_line_value(_qty_of(ln), cost))
            continue
        ln["fx_base_currency"] = base
        row = rate_rows.get(ln["currency"])
        if row is not None:
            ln.update(fx_rate=Decimal(row["rate"]), fx_rate_date=row["effective_date"],
                      fx_rate_id=row["id"])
        if not in_order or cost is None:
            continue
        if row is None:
            unconverted += 1
            continue
        v = ref.convert_line(_qty_of(ln), cost, row["rate"])
        ln["value_base"] = v
        values.append(v)
    return {"foreign": bool(foreign_codes), "unconverted": unconverted,
            "total": ref.total(values) if values else None}


# The value of an order line in the tenant's base currency, as SQL, for the
# readers that sum money across lines. A tenant-currency line (currency IS NULL)
# is qty x cost exactly as before; a foreign line is the value recorded when the
# order was written, NULL when it could not be converted.
def line_value_sql(alias: str = "i", qty_column: str = "final_qty") -> str:
    a = alias
    return (f"(CASE WHEN {a}.currency IS NULL THEN {a}.{qty_column} * {a}.unit_cost "
            f"ELSE {a}.value_base::float8 END)")


def line_base_value(line: dict) -> Optional[float]:
    """Python twin of `line_value_sql` for a fetched line (`final_qty`,
    `unit_cost`, `currency`, `value_base`): its value in the tenant's own
    currency, or None when it has no cost or could not be converted."""
    cost = line.get("unit_cost")
    if cost is None:
        return None
    if line.get("currency"):
        vb = line.get("value_base")
        return None if vb is None else float(vb)
    return float(line.get("final_qty") or 0) * float(cost)


def currency_dict(code: Optional[str], default: dict) -> dict:
    """The formatting dict (`symbol`, `decimals`, ...) for a line's currency;
    `default` (the tenant's) when the line has none or the code is unknown."""
    if not code:
        return default
    from backend.api.v1.currency import SUPPORTED
    if code not in SUPPORTED:
        return default
    return {"code": code, **SUPPORTED[code]}
