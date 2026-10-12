"""The canonical fields a sync profile can fill, and the transforms a field
mapping may apply. Pure: no DB, no I/O.

A mapping is ``{canonical_field: FieldMapping}`` where a field mapping is a
dict::

    {
      "column":     "LABST",          # source column (dotted path for MongoDB); optional
                                      #   when "default" is given
      "cast":       "auto",           # auto | number | integer | text
      "trim":       true,             # strip surrounding whitespace (text)
      "case":       "none",           # none | upper | lower (text)
      "multiplier": 12,               # number fields: cases -> units, grams -> kg...
      "decimal":    "auto",           # auto | dot | comma — how a TEXT number is written
      "default":    "principal",      # used when the cell is empty / the column is absent
      "value_map":  {"01": "Norte"},  # exact source value -> StockAI value
    }

Filters are a list of ``{"column", "op", "value"}`` predicates a row must ALL
pass to be synced (``active = 1``, ``warehouse in [...]``). A row a filter
drops is not a rejection: the profile said it does not belong.
"""

from __future__ import annotations

from typing import Any

from backend.errors import AppError

# Canonical fields, in the order the mapping editor lists them.
CANONICAL_FIELDS: tuple[str, ...] = (
    "sku", "warehouse", "current_stock", "min_stock", "unit_cost", "supplier",
    "lead_time_days", "category", "brand", "unit_of_measure", "barcode",
    "sale_price", "display_name",
)
REQUIRED_FIELDS: tuple[str, ...] = ("sku", "current_stock")
KEY_FIELDS: tuple[str, ...] = ("sku", "warehouse")

NUMBER_FIELDS: tuple[str, ...] = ("current_stock", "min_stock", "unit_cost", "sale_price")
INTEGER_FIELDS: tuple[str, ...] = ("lead_time_days",)
TEXT_FIELDS: tuple[str, ...] = (
    "sku", "warehouse", "supplier", "category", "brand", "unit_of_measure",
    "barcode", "display_name",
)

# Same bounds as the direct PUT/PATCH /inventory/stock endpoints
# (`api/v1/inventory.py` StockPatch): the sync can never write a value the
# screen would refuse.
_MAX_QTY = 1_000_000_000
_MAX_MONEY = 1_000_000_000
BOUNDS: dict[str, tuple[float, float]] = {
    "current_stock": (0, _MAX_QTY),
    "min_stock": (0, _MAX_QTY),
    "unit_cost": (0, _MAX_MONEY),
    "sale_price": (0, _MAX_MONEY),
    "lead_time_days": (1, 365),
}
MAX_TEXT = 500
MAX_SKU = 200

CASTS = ("auto", "number", "integer", "text")
CASES = ("none", "upper", "lower")
DECIMALS = ("auto", "dot", "comma")
FILTER_OPS = ("eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "is_null", "not_null")
MAX_VALUE_MAP = 2000
MAX_FILTERS = 20


def _bad(reason: str, **params: Any) -> AppError:
    return AppError("inventory_sync_mapping_invalid", f"The column mapping is not valid: {reason}.",
                    status_code=422, params={"reason": reason, **params})


def normalize_mapping(raw: Any) -> dict:
    """Validate a mapping from the editor and return it normalised. Refuses
    unknown fields, unknown options and a mapping without the required
    fields — a profile that cannot produce a SKU and a quantity cannot be
    saved."""
    if not isinstance(raw, dict):
        raise _bad("not_object")
    out: dict[str, dict] = {}
    for field, spec in raw.items():
        if field not in CANONICAL_FIELDS:
            raise _bad("unknown_field", field=str(field)[:60])
        if spec in (None, "", {}):
            continue
        if isinstance(spec, str):
            spec = {"column": spec}
        if not isinstance(spec, dict):
            raise _bad("field_not_object", field=field)
        column = spec.get("column")
        column = str(column).strip() if column not in (None, "") else None
        if column is not None and (len(column) > 256 or "\x00" in column):
            raise _bad("column_invalid", field=field)
        cast = spec.get("cast") or "auto"
        case = spec.get("case") or "none"
        decimal = spec.get("decimal") or "auto"
        if cast not in CASTS:
            raise _bad("cast_invalid", field=field, value=str(cast)[:20])
        if case not in CASES:
            raise _bad("case_invalid", field=field, value=str(case)[:20])
        if decimal not in DECIMALS:
            raise _bad("decimal_invalid", field=field, value=str(decimal)[:20])
        multiplier = spec.get("multiplier")
        if multiplier not in (None, ""):
            try:
                multiplier = float(multiplier)
            except (TypeError, ValueError):
                raise _bad("multiplier_invalid", field=field)
            if field not in NUMBER_FIELDS + INTEGER_FIELDS or not (0 < multiplier <= 1_000_000):
                raise _bad("multiplier_invalid", field=field)
        else:
            multiplier = None
        value_map = spec.get("value_map") or None
        if value_map is not None:
            if not isinstance(value_map, dict) or len(value_map) > MAX_VALUE_MAP:
                raise _bad("value_map_invalid", field=field)
            value_map = {str(k): ("" if v is None else str(v))[:MAX_TEXT] for k, v in value_map.items()}
        default = spec.get("default")
        default = None if default in (None, "") else str(default)[:MAX_TEXT]
        if column is None and default is None:
            continue
        out[field] = {
            "column": column, "cast": cast, "trim": bool(spec.get("trim", True)),
            "case": case, "decimal": decimal, "multiplier": multiplier,
            "default": default, "value_map": value_map,
        }
    missing = [f for f in REQUIRED_FIELDS if f not in out or not out[f].get("column")]
    if missing:
        raise _bad("required_missing", fields=", ".join(missing))
    return out


def normalize_filters(raw: Any) -> list[dict]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list) or len(raw) > MAX_FILTERS:
        raise _bad("filters_invalid")
    out = []
    for f in raw:
        if not isinstance(f, dict):
            raise _bad("filters_invalid")
        column = str(f.get("column") or "").strip()
        op = f.get("op") or "eq"
        if not column or len(column) > 256 or op not in FILTER_OPS:
            raise _bad("filter_invalid", column=column[:60], op=str(op)[:20])
        value = f.get("value")
        if op in ("in", "not_in"):
            if not isinstance(value, list) or len(value) > MAX_VALUE_MAP:
                raise _bad("filter_invalid", column=column[:60], op=op)
            value = [str(v) for v in value]
        elif op not in ("is_null", "not_null"):
            value = None if value is None else str(value)[:MAX_TEXT]
        out.append({"column": column, "op": op, "value": value})
    return out


def owned_fields(mapping: dict) -> list[str]:
    """The inventory fields this profile writes (the conflict rule's 'source
    wins' set). The key fields are identity, not values."""
    return [f for f in CANONICAL_FIELDS if f in mapping and f not in KEY_FIELDS]
