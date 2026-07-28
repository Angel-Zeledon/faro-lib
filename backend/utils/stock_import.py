"""
Tolerant header/value reading for the stock importer (onboarding-friction
plan #1, phase 4).

No LatAm ERP exports the exact headers `POST /inventory/bulk` used to demand
(`sku,current_stock,lead_time_days,...`). Real files say `Codigo`,
`Existencia`, `Costo Unitario`, are separated by `;`, and write money as
`1.234,56`. This module turns those files into canonical rows, mirroring what
the sales importer already does for the sales file.

Pure functions: no DB, no pandas, no I/O. The API layer owns the reading of
the upload and the DB writes.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Canonical stock fields an imported file can fill. Mirrors the columns of
# StockPatch in backend/api/v1/inventory.py — the import can never write a
# field the direct PATCH endpoint would not accept.
TEXT_FIELDS = (
    "display_name", "supplier", "notes", "category", "brand",
    "unit_of_measure", "barcode", "warehouse",
)
FLOAT_FIELDS = ("current_stock", "min_stock", "unit_cost", "moq", "sale_price")
INT_FIELDS = ("lead_time_days",)
NUMERIC_FIELDS = FLOAT_FIELDS + INT_FIELDS
# Order matters: this is the order the mapping wizard lists the fields in, so
# it runs from what every file has (a code, a name, a quantity) down to the
# rarely-filled ones, instead of the arbitrary text-then-numbers grouping.
CANONICAL_FIELDS = (
    "sku", "display_name", "current_stock", "unit_cost", "sale_price",
    "lead_time_days", "supplier", "min_stock", "moq", "category", "brand",
    "unit_of_measure", "barcode", "warehouse", "notes",
)

# Ordered alias table. Written WITHOUT accents and in lowercase: normalize()
# strips accents and punctuation from the file's headers before matching, so
# "Código", "CODIGO" and "codigo" all land on the same key.
#
# Order matters twice over: the fields are tried in this order, and within a
# field the aliases are tried in order — so a file carrying both "codigo" and
# "producto" maps codigo -> sku and leaves producto for display_name, which is
# what a LatAm ERP export means by those two columns.
_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sku", (
        "sku", "codigo", "codigo producto", "codigo articulo", "codigo item",
        "cod", "cod producto", "clave", "clave producto", "referencia", "ref",
        "item", "id producto", "id", "part number", "no parte", "articulo",
        "code", "product code", "item code",
    )),
    ("barcode", (
        "barcode", "codigo de barras", "codigo barras", "barras", "ean",
        "ean13", "upc",
    )),
    ("display_name", (
        "display name", "nombre", "nombre producto", "descripcion",
        "descripcion producto", "detalle", "producto", "product", "name",
        "description", "articulo descripcion",
    )),
    ("current_stock", (
        "current stock", "existencia", "existencias", "stock", "stock actual",
        "inventario", "inventario actual", "cantidad", "cantidad actual",
        "saldo", "disponible", "on hand", "qty on hand", "quantity",
        "unidades", "exist",
    )),
    ("min_stock", (
        "min stock", "stock minimo", "minimo", "existencia minima",
        "cantidad minima", "min", "minimum stock",
    )),
    ("lead_time_days", (
        "lead time days", "lead time", "leadtime", "dias entrega",
        "dias de entrega", "tiempo entrega", "tiempo de entrega", "plazo",
        "plazo entrega", "dias reposicion",
    )),
    ("unit_cost", (
        "unit cost", "costo", "costo unitario", "costo unit", "ultimo costo",
        "costo promedio", "precio compra", "precio de compra", "precio costo",
        "cost", "purchase price",
    )),
    ("sale_price", (
        "sale price", "precio", "precio venta", "precio de venta",
        "precio publico", "pvp", "price", "selling price",
    )),
    ("moq", (
        "moq", "pedido minimo", "compra minima", "cantidad minima compra",
        "lote minimo", "minimum order quantity",
    )),
    ("supplier", (
        "supplier", "proveedor", "suplidor", "distribuidor", "vendor",
        "fabricante", "nombre proveedor",
    )),
    ("category", (
        "category", "categoria", "rubro", "linea", "departamento", "grupo",
    )),
    ("brand", ("brand", "marca")),
    ("unit_of_measure", (
        "unit of measure", "unidad", "unidad de medida", "um", "uom",
        "presentacion", "medida", "empaque",
    )),
    ("warehouse", (
        "warehouse", "bodega", "almacen", "deposito", "sucursal", "tienda",
        "ubicacion", "local", "centro",
    )),
    ("notes", ("notes", "notas", "observaciones", "comentarios", "nota")),
)

# Columns that become the SKU only when the file carries no code column at
# all — the product name is then the only identifier the rows have.
_SKU_LAST_RESORT = (
    "producto", "product", "descripcion", "description", "nombre", "name",
    "detalle", "material",
)

# Symbols glued to a figure ("₡1.234", "1.234$", "12%"). Letters are NOT in
# this class on purpose: stripping them would turn the garbage cell "12abc"
# into a silent 12, and reporting garbage as garbage is the point of the
# per-row errors.
_GLUED_SYMBOLS = re.compile(r"^[^\w.,-]+|[^\w.,-]+$", re.UNICODE)
_SPACES = re.compile(r"[\s  ]+")


def normalize(header: str) -> str:
    """
    'Costo Unitario ($)' -> 'costo unitario'. Accent- and punctuation-blind so
    the alias table can be written once, unaccented.
    """
    if header is None:
        return ""
    text = unicodedata.normalize("NFKD", str(header))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    # Drop anything that is not a letter, digit or space (parentheses, units,
    # underscores, dots), then collapse the whitespace it leaves behind.
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return _SPACES.sub(" ", text).strip()


def sniff_separator(sample: str) -> str:
    """
    Guess a CSV's column separator from its header line. Spanish-locale Excel
    exports with ';' and pasted exports with tabs; assuming ',' collapses them
    into one unusable column named "codigo;existencia;costo".

    Same rule as backend/dataframes/io.sniff_separator — repeated here so a
    plain CSV import never has to import pandas.
    """
    header = (sample or "").lstrip("﻿").splitlines()[0] if (sample or "").strip() else ""
    best, best_count = ",", 1
    for candidate in (",", ";", "\t", "|"):
        count = len(header.split(candidate))
        if count > best_count:
            best, best_count = candidate, count
    return best


def detect_mapping(columns: list[str]) -> dict[str, str]:
    """
    Best-effort {canonical_field: source_column} for a file's header row.

    Two passes: exact alias matches first (so "codigo" wins `sku` over a
    column merely containing the word), then a containment pass for the fields
    still unmapped ("costo unitario final" -> unit_cost). A source column is
    never assigned to two fields.
    """
    normalized = [(col, normalize(col)) for col in columns if str(col or "").strip()]
    mapping: dict[str, str] = {}
    taken: set[str] = set()

    for field, aliases in _ALIASES:
        for alias in aliases:
            hit = next(
                (col for col, norm in normalized if norm == alias and col not in taken),
                None,
            )
            if hit is not None:
                mapping[field] = hit
                taken.add(hit)
                break

    # A file with no code column at all: the product NAME is then the only
    # identifier there is, so it becomes the SKU (the same thing the sales
    # importer does with its "producto" hint). The column is taken away from
    # display_name — one column cannot be both.
    if "sku" not in mapping:
        for alias in _SKU_LAST_RESORT:
            hit = next((col for col, norm in normalized if norm == alias), None)
            if hit is None:
                continue
            for other, owner_col in list(mapping.items()):
                if owner_col == hit:
                    mapping.pop(other)
            mapping["sku"] = hit
            taken.add(hit)
            break

    for field, aliases in _ALIASES:
        if field in mapping:
            continue
        for alias in aliases:
            # Word-boundary containment: "costo unitario final" matches the
            # alias "costo unitario"; "descuento" must not match "cuento".
            hit = next(
                (col for col, norm in normalized
                 if col not in taken and re.search(rf"(^|\s){re.escape(alias)}(\s|$)", norm)),
                None,
            )
            if hit is not None:
                mapping[field] = hit
                taken.add(hit)
                break

    return mapping


def has_decimal_comma(samples: list[str]) -> bool:
    """
    Does this file write decimals with a comma? True as soon as one cell has a
    comma followed by anything other than a 3-digit group ("12,5", "1.234,56"),
    which no thousands separator ever produces.

    Deciding this once per FILE instead of per cell is what keeps "1,250" from
    being read as 1.25 in a file that means 1250 — the ambiguous cell inherits
    the answer its unambiguous neighbours already gave.
    """
    for raw in samples:
        if raw and re.search(r",\d{1,2}(?!\d)", str(raw)):
            return True
        if raw and re.search(r",\d{4,}", str(raw)):
            return True
    return False


def parse_number(raw, decimal_comma: Optional[bool] = None) -> Optional[float]:
    """
    Read a spreadsheet cell as a number, or None when it is not one.

    Handles: plain "120", money "₡ 1.234,56" / "$1,234.56", spaced thousands
    "1 234", accounting negatives "(50)", and the trailing "-" some ERPs
    append. `decimal_comma` overrides the per-cell guess with the file-level
    verdict from has_decimal_comma().
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)

    text = str(raw).strip()
    if not text:
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip()
    if text.endswith("-"):
        negative, text = True, text[:-1].strip()

    # Drop whitespace-separated non-numeric tokens (a currency code: "12,50
    # USD", "S/ 45"), keeping the token that actually carries digits. Several
    # digit tokens are only accepted as space-separated thousands ("1 234 567").
    tokens = [t for t in text.split() if any(ch.isdigit() for ch in t)]
    if not tokens:
        return None
    if len(tokens) == 1:
        text = tokens[0]
    elif re.fullmatch(r"\d{1,3}", tokens[0]) and all(re.fullmatch(r"\d{3}", t) for t in tokens[1:]):
        text = "".join(tokens)
    else:
        return None

    text = _GLUED_SYMBOLS.sub("", text)
    if text.startswith("-"):
        negative, text = True, text[1:]
    # A letter still glued to the figure means this was never a number.
    if not text or re.search(r"[^\d.,]", text):
        return None

    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        # Whichever separator comes last is the decimal one.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_comma:
        groups = text.split(",")
        looks_like_thousands = len(groups) > 1 and all(len(g) == 3 for g in groups[1:])
        use_decimal = not looks_like_thousands if decimal_comma is None else decimal_comma
        text = text.replace(",", "." if use_decimal else "")
    elif has_dot:
        groups = text.split(".")
        # "1.234.567" is a thousands-formatted integer, never a decimal.
        if len(groups) > 2 and all(len(g) == 3 for g in groups[1:]):
            text = text.replace(".", "")
        elif len(groups) == 2 and len(groups[1]) == 3 and decimal_comma:
            # In a comma-decimal file a lone dot before 3 digits is thousands.
            text = text.replace(".", "")

    if not re.fullmatch(r"\d*\.?\d+(?:[eE][+-]?\d+)?", text):
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return -value if negative else value


def apply_mapping(row: dict, mapping: dict[str, str]) -> dict:
    """
    Rewrite one source row into canonical field names, dropping blank cells.
    Values are returned as read — parsing/validation stays with the caller.
    """
    out: dict = {}
    for field, source_col in mapping.items():
        if field not in CANONICAL_FIELDS:
            continue
        value = row.get(source_col)
        if value is None:
            continue
        # Excel hands us numbers, and a code column with one blank cell comes
        # back as float — str() would then turn the product code 12345 into
        # "12345.0" and create a second, wrong SKU.
        if isinstance(value, float) and not isinstance(value, bool) and value.is_integer():
            text = str(int(value))
        else:
            text = str(value).strip()
        if not text:
            continue
        out[field] = text
    return out
