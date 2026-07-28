from __future__ import annotations

from typing import Any
import pandas as pd

REQUIRED_FIELDS: frozenset[str] = frozenset({"sku", "date", "demand"})

# The one lead-time default the whole product uses when nobody configured one.
#
# It is repeated here as a literal instead of imported because the layering
# forbids ForecastingCore from depending on `backend` — the authority is
# `backend/inventory/defaults.py:DEFAULT_LEAD_TIME_DAYS`, and
# `backend/tests/test_lead_time_provenance.py` asserts the two are equal so the
# duplication cannot silently drift apart again.
#
# It used to be 7 here (and 7 in the training runner) while the DB schema, the
# Quick Start wizard and /inventory all said 15. Three answers to one question,
# and no way to tell which one a given recommendation had used. 15 is the
# business call: the target user is a LatAm distributor whose replenishment
# usually involves an import leg, and when we have to guess, guessing long is
# the cheap mistake — a short guess shrinks the reorder point and the buyer
# discovers the error as a stockout.
DEFAULT_LEAD_TIME_DAYS = 15

# internal_name → default when not mapped (None = unknown, stays NaN)
FIELD_DEFAULTS: dict[str, Any] = {
    "sku":           None,   # required — no default
    "date":          None,   # required — no default
    "demand":        None,   # required — no default
    "store":         "Tienda única",
    "region":        "Sin región",
    "inventory":     0,
    "lead_time":     DEFAULT_LEAD_TIME_DAYS,
    "price":         None,
    "cost":          None,
    "regular_price": None,
    "promo_price":   None,
    "promo":         False,
    "promo_type":    "Sin promoción",
    "discount":      0.0,
}

CANONICAL_FIELDS: list[dict] = [
    {"name": "sku",           "label": "SKU / Producto",      "required": True,  "dtype": "str"},
    {"name": "date",          "label": "Fecha",               "required": True,  "dtype": "date"},
    {"name": "demand",        "label": "Demanda",             "required": True,  "dtype": "float"},
    {"name": "store",         "label": "Tienda",              "required": False, "dtype": "str"},
    {"name": "region",        "label": "Región",              "required": False, "dtype": "str"},
    {"name": "inventory",     "label": "Inventario",          "required": False, "dtype": "float"},
    {"name": "lead_time",     "label": "Lead Time (días)",    "required": False, "dtype": "int"},
    {"name": "price",         "label": "Precio",              "required": False, "dtype": "float"},
    {"name": "cost",          "label": "Costo",               "required": False, "dtype": "float"},
    {"name": "regular_price", "label": "Precio Regular",      "required": False, "dtype": "float"},
    {"name": "promo_price",   "label": "Precio Promocional",  "required": False, "dtype": "float"},
    {"name": "promo",         "label": "Promoción",           "required": False, "dtype": "bool"},
    {"name": "promo_type",    "label": "Tipo de Promoción",   "required": False, "dtype": "str"},
    {"name": "discount",      "label": "Descuento",           "required": False, "dtype": "float"},
]

_SEPARATOR = "│"   # U+2502 — chosen to avoid conflicts with CSV pipe characters


def series_key(sku: Any, store: Any) -> str:
    """Stable string key for a (sku, store) series."""
    return f"{sku}{_SEPARATOR}{store}"


def parse_series_key(key: str) -> tuple[str, str]:
    """Split a series key back into (sku, store)."""
    parts = key.split(_SEPARATOR, 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid series key (expected sku│store): {key!r}")
    return parts[0], parts[1]


def apply_canonical_defaults(
    df: pd.DataFrame,
    mapping: dict[str, str | None],
) -> pd.DataFrame:
    """
    Apply canonical mapping to df, filling missing optional fields with defaults.

    Args:
        df:      Source DataFrame with user column names.
        mapping: {canonical_field: source_column | None}
                 None means "not in file — use default".

    Returns:
        DataFrame with 14 internal canonical columns plus all original columns.

    Raises:
        ValueError: if a required field (sku, date, demand) has no mapping.
    """
    for field in REQUIRED_FIELDS:
        if not mapping.get(field):
            raise ValueError(
                f"Required canonical field '{field}' has no column mapping. "
                "Provide a source column name."
            )
        src = mapping[field]
        if src not in df.columns:
            raise ValueError(
                f"Column '{src}' mapped to '{field}' does not exist in the DataFrame. "
                f"Available columns: {list(df.columns)}"
            )

    result = df.copy()

    for field_def in CANONICAL_FIELDS:
        name = field_def["name"]
        src  = mapping.get(name)

        if src and src in df.columns:
            result[name] = df[src]
        else:
            default = FIELD_DEFAULTS[name]
            result[name] = default   # broadcasts scalar to full column

    return result
