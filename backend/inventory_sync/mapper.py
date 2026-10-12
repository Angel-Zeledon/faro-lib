"""One source row -> one canonical stock row, or a rejection with a reason.

Pure functions (no DB): the dry run, the run and the tests all call the same
code, so what the preview says is what the run does.

Rejections reuse the bulk importer's guards and add the ones a database
source needs:

    sku_blank               the SKU cell is empty or whitespace
    row_has_nul             a NUL byte anywhere in the row (corrupt export)
    not_a_number            a numeric field holds text that is not a number
    number_format_unclear   "1.250" with nothing saying if the dot is decimal
    negative_quantity       a negative stock (policy `reject`, the default)
    out_of_range            outside the bounds the stock screen enforces
    text_too_long           a text value longer than the column accepts
    unknown_warehouse       a warehouse that does not exist (unless allowed)
    duplicate_key           the same (sku, warehouse) twice (policy `reject`)

Each rejection is ``{"code", "params", "sku", "row"}`` — a stable code the UI
renders through i18n (`inventorySync.reject.<code>`), never prose.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from backend.inventory_sync import fields as F
from backend.utils import stock_import

DEFAULT_WAREHOUSE = "principal"


class Rejected(Exception):
    def __init__(self, code: str, **params: Any):
        super().__init__(code)
        self.code = code
        self.params = params


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _as_text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, Decimal):
        try:
            if value == value.to_integral_value():
                return str(int(value))
        except InvalidOperation:
            pass
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    if isinstance(value, list):
        return ", ".join(_as_text(v) for v in value)
    return str(value)


def _to_number(value: Any, decimal: str, field: str) -> float:
    """A cell as a float, or `Rejected`. Native numbers (what a DB driver
    returns for NUMERIC/INT columns) are taken as they are; only TEXT goes
    through the LatAm-tolerant parser, and an ambiguous dot is refused rather
    than guessed — guessing it wrong divides a catalogue by a thousand."""
    if isinstance(value, bool):
        raise Rejected("not_a_number", field=field, value=str(value))
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            raise Rejected("not_a_number", field=field, value=str(value))
        return number
    text = _as_text(value).strip()
    if decimal == "dot":
        parsed = stock_import.parse_number(text, decimal_comma=False, thousands_dot=False)
    elif decimal == "comma":
        parsed = stock_import.parse_number(text, decimal_comma=True)
    else:
        if stock_import.dot_is_ambiguous([text]):
            raise Rejected("number_format_unclear", field=field, value=text[:40])
        parsed = stock_import.parse_number(text)
    if parsed is None:
        raise Rejected("not_a_number", field=field, value=text[:40])
    return parsed


def _lookup(row: dict, column: str) -> Any:
    if column in row:
        return row[column]
    # Case-blind fallback: Oracle upper-cases unquoted names, PostgreSQL
    # lower-cases them; a mapping typed either way still finds its column.
    low = column.lower()
    for key, value in row.items():
        if str(key).lower() == low:
            return value
    return None


def _field_value(row: dict, field: str, spec: dict) -> Any:
    raw = _lookup(row, spec["column"]) if spec.get("column") else None
    if _is_empty(raw):
        raw = spec.get("default")
    if _is_empty(raw):
        return None
    if spec.get("value_map"):
        key = _as_text(raw).strip()
        if key in spec["value_map"]:
            raw = spec["value_map"][key]
            if _is_empty(raw):
                return None
    cast = spec.get("cast") or "auto"
    numeric = field in F.NUMBER_FIELDS or field in F.INTEGER_FIELDS
    if (numeric and cast != "text") or cast in ("number", "integer"):
        number = _to_number(raw, spec.get("decimal") or "auto", field)
        if spec.get("multiplier"):
            number = number * float(spec["multiplier"])
        if field in F.INTEGER_FIELDS or cast == "integer":
            number = float(round(number))
        return number
    text = _as_text(raw)
    if spec.get("trim", True):
        text = text.strip()
    if spec.get("case") == "upper":
        text = text.upper()
    elif spec.get("case") == "lower":
        text = text.lower()
    return text or None


def passes_filters(row: dict, filters: list[dict]) -> bool:
    for f in filters:
        value = _lookup(row, f["column"])
        op, target = f["op"], f.get("value")
        if op == "is_null":
            if not _is_empty(value):
                return False
            continue
        if op == "not_null":
            if _is_empty(value):
                return False
            continue
        text = None if value is None else _as_text(value).strip()
        if op in ("in", "not_in"):
            hit = text in (target or [])
            if (op == "in") != hit:
                return False
            continue
        if op in ("eq", "ne"):
            equal = text == (target if target is None else str(target).strip())
            if not equal and text is not None and target is not None:
                try:
                    equal = float(text) == float(target)
                except ValueError:
                    pass
            if (op == "eq") != equal:
                return False
            continue
        # gt / gte / lt / lte: numeric when both sides are numbers, text otherwise.
        if text is None or target is None:
            return False
        try:
            a, b = float(text), float(target)
        except ValueError:
            a, b = text, str(target)
        if op == "gt" and not a > b:
            return False
        if op == "gte" and not a >= b:
            return False
        if op == "lt" and not a < b:
            return False
        if op == "lte" and not a <= b:
            return False
    return True


def map_row(row: dict, mapping: dict, *, default_warehouse: Optional[str] = None,
            negative_policy: str = "reject") -> dict:
    """The canonical row for one source row, or raises `Rejected`.

    The returned dict always has `sku` and `warehouse`; value fields appear
    only when the source gave them (an empty optional cell is "no value", not
    zero — a zero the source never said is how a full warehouse ends up in
    PEDIR_YA)."""
    if any(isinstance(v, str) and "\x00" in v for v in row.values()):
        raise Rejected("row_has_nul")

    out: dict[str, Any] = {}
    for field in F.CANONICAL_FIELDS:
        spec = mapping.get(field)
        if not spec:
            continue
        value = _field_value(row, field, spec)
        if value is None:
            continue
        out[field] = value

    sku = out.get("sku")
    if sku is None or not str(sku).strip():
        raise Rejected("sku_blank")
    if len(str(sku)) > F.MAX_SKU:
        raise Rejected("text_too_long", field="sku", max=F.MAX_SKU)
    warehouse = out.get("warehouse") or (default_warehouse or "").strip() or DEFAULT_WAREHOUSE
    out["warehouse"] = str(warehouse).strip()

    if "current_stock" not in out:
        # The quantity is required: a row with no stock figure is not a
        # stock row. Rejected by name, never written as zero.
        raise Rejected("quantity_missing", field="current_stock")

    for field in F.TEXT_FIELDS:
        if field in out and len(str(out[field])) > F.MAX_TEXT:
            raise Rejected("text_too_long", field=field, max=F.MAX_TEXT)

    qty = out["current_stock"]
    if qty < 0:
        if negative_policy == "zero":
            out["current_stock"] = 0.0
        else:
            raise Rejected("negative_quantity", field="current_stock", value=qty)

    for field, (lo, hi) in F.BOUNDS.items():
        if field in out and not (lo <= out[field] <= hi):
            raise Rejected("out_of_range", field=field, value=out[field], min=lo, max=hi)
    if "lead_time_days" in out:
        out["lead_time_days"] = int(out["lead_time_days"])
    return out


def rejection(code: str, params: dict, *, sku: Any = None, row_number: Optional[int] = None) -> dict:
    return {"code": code, "params": {k: (v if isinstance(v, (int, float, str)) or v is None else str(v))
                                     for k, v in params.items()},
            "sku": None if sku is None else str(sku).replace("\x00", "")[:F.MAX_SKU],
            "row": row_number}


def sku_hint(row: dict, mapping: dict) -> Optional[str]:
    """Best-effort SKU of a rejected row, for the report."""
    spec = mapping.get("sku") or {}
    if not spec.get("column"):
        return None
    value = _lookup(row, spec["column"])
    return None if _is_empty(value) else _as_text(value)[:F.MAX_SKU]


def same_value(a: Any, b: Any) -> bool:
    """Equality for change detection: numbers within float noise, text exact."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)), abs(float(b)))
    return str(a) == str(b)


def map_key(row: dict, mapping: dict, *, default_warehouse: Optional[str] = None) -> tuple[str, str]:
    """Only the (sku, warehouse) of a row, with the mapping's transforms —
    what a ledger movement contributes to. Raises `Rejected` like map_row."""
    if any(isinstance(v, str) and "\x00" in v for v in row.values()):
        raise Rejected("row_has_nul")
    sku = _field_value(row, "sku", mapping["sku"]) if mapping.get("sku") else None
    if sku is None or not str(sku).strip():
        raise Rejected("sku_blank")
    if len(str(sku)) > F.MAX_SKU:
        raise Rejected("text_too_long", field="sku", max=F.MAX_SKU)
    wh = _field_value(row, "warehouse", mapping["warehouse"]) if mapping.get("warehouse") else None
    wh = wh or (default_warehouse or "").strip() or DEFAULT_WAREHOUSE
    return str(sku), str(wh).strip()


def number(value: Any, decimal: str = "auto", field: str = "current_stock") -> float:
    """A cell as a number with the importer's rules (raises `Rejected`)."""
    return _to_number(value, decimal, field)
