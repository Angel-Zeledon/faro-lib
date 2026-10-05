"""
Bulk import of SUPPLIERS and PURCHASE ORDERS from a CSV / Excel upload.

Modelled on the stock importer (`backend/utils/stock_import.py` +
`POST /inventory/bulk`), with the same manners:

  * headers are matched by alias, accent- and case-blind, so an ERP export
    ("Proveedor", "Correo electronico", "Dias de credito") imports without
    hand-editing; the wizard can pin any column explicitly;
  * every rejected row is reported by line number with a stable `code` +
    `params` (rendered through i18n) and an English fallback;
  * a row carrying a NUL byte is refused by name, never stored as a different
    product than the file says;
  * the preview is a dry run that writes nothing.

This module is pure Python: no pandas (Excel goes through
`backend.dataframes.io.read_rows`), no HTTP, and the only DB access is the
read-only lookups the purchase-order resolver needs. The router owns the
transaction and the writes.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from typing import Optional

from backend.config import settings
from backend.errors import AppError
from backend.utils import stock_import

log = logging.getLogger(__name__)

# A suppliers file is a short list; a PO file is one row per order line. Both
# caps exist so a hostile or accidental multi-million-row upload is refused
# BEFORE it is parsed into memory row by row — the byte ceiling alone does not
# stop a 20 MB file of one-character rows.
MAX_SUPPLIER_ROWS = 5_000
MAX_PO_ROWS = 20_000
# Distinct purchase orders one file may create. Each is a transaction.
MAX_PO_GROUPS = 2_000

MAX_REPORTED_ROW_ERRORS = 50
PREVIEW_SAMPLE_ROWS = 8

_EXCEL_EXTENSIONS = (".xlsx", ".xls", ".xlsm")
_MAX_TEXT = 500
_MAX_NOTES = 2_000

# ── Fields and aliases ────────────────────────────────────────────────────────
# Written WITHOUT accents, lowercase: stock_import.normalize() strips them from
# the file's headers before matching.

SUPPLIER_FIELDS = (
    "name", "email", "phone", "whatsapp", "lead_time_days", "lead_time_std",
    "review_period_days", "payment_terms", "payment_terms_days", "notes",
)
SUPPLIER_INT_FIELDS = (
    "lead_time_days", "lead_time_std", "review_period_days", "payment_terms_days",
)
SUPPLIER_TEXT_FIELDS = ("name", "email", "phone", "whatsapp", "payment_terms", "notes")

_SUPPLIER_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("name", (
        "name", "supplier", "supplier name", "vendor", "nombre", "proveedor",
        "nombre proveedor", "nombre del proveedor", "razon social", "empresa",
        "suplidor",
    )),
    ("email", (
        "email", "e mail", "mail", "correo", "correo electronico", "email address",
    )),
    ("whatsapp", (
        "whatsapp", "whats app", "wa", "numero whatsapp", "whatsapp number",
    )),
    ("phone", (
        "phone", "telephone", "tel", "telefono", "celular", "movil", "contacto telefono",
    )),
    ("lead_time_days", (
        "lead time days", "lead time", "leadtime", "dias entrega", "dias de entrega",
        "tiempo entrega", "tiempo de entrega", "plazo entrega",
    )),
    ("lead_time_std", (
        "lead time std", "lead time variability", "desviacion entrega",
        "variabilidad entrega", "variacion entrega",
    )),
    ("review_period_days", (
        "review period days", "review period", "periodo revision", "cadencia pedido",
        "dias entre pedidos", "frecuencia pedido",
    )),
    ("payment_terms_days", (
        "payment terms days", "credit days", "dias credito", "dias de credito",
        "credito dias", "plazo pago dias",
    )),
    ("payment_terms", (
        "payment terms", "terms", "condiciones pago", "condiciones de pago",
        "terminos pago", "forma de pago", "plazo pago", "credito",
    )),
    ("notes", ("notes", "notas", "observaciones", "comentarios", "nota")),
)

PO_FIELDS = (
    "order_ref", "supplier", "sku", "qty", "unit_cost", "warehouse",
)
_PO_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("order_ref", (
        "order ref", "order reference", "order", "order number", "order no", "po",
        "po number", "po ref", "orden", "numero orden", "no orden", "orden de compra",
        "oc", "numero oc", "referencia orden", "pedido", "numero pedido",
    )),
    ("supplier", (
        "supplier", "supplier name", "vendor", "proveedor", "nombre proveedor",
        "suplidor", "razon social",
    )),
    ("sku", (
        "sku", "product code", "item code", "code", "codigo", "codigo producto",
        "codigo articulo", "cod", "clave", "referencia", "ref", "item", "articulo",
        "part number", "producto", "product", "descripcion", "description", "nombre",
    )),
    ("qty", (
        "qty", "quantity", "order qty", "units", "cantidad", "cantidad pedida",
        "cantidad ordenada", "unidades", "cant",
    )),
    ("unit_cost", (
        "unit cost", "cost", "unit price", "price", "costo", "costo unitario",
        "precio", "precio compra", "precio unitario", "precio de compra",
    )),
    ("warehouse", (
        "warehouse", "destination", "destination warehouse", "bodega", "almacen",
        "deposito", "sucursal", "destino", "bodega destino",
    )),
)

# What the template files carry. English headers on purpose: they are the
# canonical names the importer reads back verbatim (the Spanish aliases are for
# real ERP exports, not for the template).
SUPPLIER_TEMPLATE_COLUMNS = list(SUPPLIER_FIELDS)
SUPPLIER_TEMPLATE_ROWS = [
    ["Distribuidora Sur", "ventas@distribuidorasur.com", "2222 3333",
     "+50688887777", "7", "2", "14", "30 dias", "30", "Entrega los martes"],
    ["Importadora Andina", "pedidos@andina.example", "", "", "21", "5", "30",
     "contado", "", ""],
]
PO_TEMPLATE_COLUMNS = list(PO_FIELDS)
PO_TEMPLATE_ROWS = [
    ["PO-1001", "Distribuidora Sur", "SKU001", "120", "3.50", "principal"],
    ["PO-1001", "Distribuidora Sur", "SKU002", "48", "7.25", "principal"],
    ["PO-1002", "Importadora Andina", "SKU003", "300", "", "principal"],
]


# ── Reading the upload ────────────────────────────────────────────────────────

def decode_csv(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def check_file_size(tenant_id: str, size_bytes: int) -> None:
    """Refuse an upload over the tenant's per-file ceiling.

    The plan ceiling (`max_dataset_size_mb`: 25 MB free, 5 MB trial) goes
    through `enforce_limit` like every other plan limit, so the UI shows the
    same "write to us" dialog as for any ceiling. The infrastructure ceiling
    (`MAX_UPLOAD_SIZE_MB`) applies on top, and — like the dataset upload — is
    skipped in testing mode only.
    """
    import math

    from backend.entitlements.service import enforce_limit, tenant_limits
    from backend.tenants.service import get_tenant

    size_mb = size_bytes / (1024 * 1024)
    tenant = get_tenant(tenant_id)
    plan_max = tenant_limits(tenant)["max_dataset_size_mb"] if tenant else None
    if plan_max is not None:
        enforce_limit(tenant_id, "max_dataset_size_mb", current=math.ceil(size_mb), adding=0)
    if not settings.testing_mode and size_bytes > settings.max_upload_size_mb * 1024 * 1024:
        raise AppError(
            "import_file_too_large",
            f"The file is {size_mb:.1f} MB, over the {settings.max_upload_size_mb} MB limit",
            status_code=413,
            params={"size_mb": round(size_mb, 1), "max_mb": settings.max_upload_size_mb},
        )


def read_table(
    filename: Optional[str], content: bytes, max_rows: int,
) -> tuple[str, list[str], list[dict], str]:
    """(format, columns, raw rows, separator) of an uploaded table.

    Excel is read through backend/dataframes (the only layer allowed pandas);
    CSV through the stdlib reader with a sniffed separator, so a Spanish-locale
    ';' export is not read as one column. `max_rows` is enforced on the CSV
    side while streaming and on the Excel side right after the read.
    """
    if not content or not content.strip():
        raise AppError("import_empty_file", "The file is empty", status_code=422)

    name = (filename or "").lower()
    if name.endswith(_EXCEL_EXTENSIONS):
        from backend.dataframes.io import read_rows
        try:
            rows = read_rows(content, fmt="excel")
        except Exception as e:
            log.warning("bulk import: unreadable Excel file '%s': %s", filename, e)
            raise AppError(
                "inventory_import_unreadable_file",
                "The file could not be read as a spreadsheet",
                status_code=422,
                params={"filename": filename or ""},
            )
        if len(rows) > max_rows:
            raise AppError(
                "import_too_many_rows",
                f"The file has {len(rows)} rows; the limit is {max_rows}",
                status_code=422,
                params={"rows": len(rows), "max_rows": max_rows},
            )
        columns = list(rows[0].keys()) if rows else []
        return "excel", [str(c) for c in columns], rows, ""

    text = decode_csv(content)
    sep = stock_import.sniff_separator(text)
    # csv.Error is what Python raises on malformed input (a NUL on older
    # versions, an oversized field): report it as an unreadable file instead of
    # a 500.
    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=sep)
        rows = []
        for raw in reader:
            rows.append(dict(raw))
            if len(rows) > max_rows:
                raise AppError(
                    "import_too_many_rows",
                    f"The file has more than {max_rows} rows",
                    status_code=422,
                    params={"rows": len(rows), "max_rows": max_rows},
                )
        columns = [str(c) for c in (reader.fieldnames or []) if c is not None]
    except csv.Error as e:
        log.warning("bulk import: unreadable CSV '%s': %s", filename, e)
        raise AppError(
            "inventory_import_unreadable_file",
            "The file could not be read as a CSV",
            status_code=422,
            params={"filename": filename or ""},
        )
    return "csv", columns, rows, sep


# ── Mapping ───────────────────────────────────────────────────────────────────

def detect_mapping(
    columns: list[str], aliases: tuple[tuple[str, tuple[str, ...]], ...],
) -> dict[str, str]:
    """{canonical_field: source_column}: exact alias first, then word-boundary
    containment for the fields still unmapped. A column is never used twice."""
    normalized = [(c, stock_import.normalize(c)) for c in columns if str(c or "").strip()]
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for field, names in aliases:
        for alias in names:
            hit = next((c for c, n in normalized if n == alias and c not in taken), None)
            if hit is not None:
                mapping[field] = hit
                taken.add(hit)
                break
    for field, names in aliases:
        if field in mapping:
            continue
        for alias in names:
            hit = next(
                (c for c, n in normalized
                 if c not in taken and re.search(rf"(^|\s){re.escape(alias)}(\s|$)", n)),
                None,
            )
            if hit is not None:
                mapping[field] = hit
                taken.add(hit)
                break
    return mapping


def resolve_mapping(
    columns: list[str], mapping_json: Optional[str],
    fields: tuple[str, ...], aliases: tuple[tuple[str, tuple[str, ...]], ...],
) -> tuple[dict, dict]:
    """(mapping actually used, auto-detected). An explicit mapping from the
    wizard wins per field; every field it does not pin keeps the detection."""
    detected = detect_mapping(columns, aliases)
    if not mapping_json:
        return dict(detected), detected

    def _bad() -> AppError:
        return AppError(
            "inventory_import_bad_mapping",
            "mapping must be a JSON object of {canonical_field: source_column}",
            status_code=422,
        )

    try:
        explicit = json.loads(mapping_json)
    except (ValueError, TypeError):
        raise _bad()
    if not isinstance(explicit, dict):
        raise _bad()

    used = dict(detected)
    by_normalized = {stock_import.normalize(c): c for c in columns}
    for field, source in explicit.items():
        if field not in fields:
            continue
        if source in (None, ""):
            used.pop(field, None)
            continue
        source = str(source)
        actual = source if source in columns else by_normalized.get(stock_import.normalize(source))
        if actual is None:
            raise AppError(
                "inventory_import_unknown_column",
                f"Column '{source}' is not in the file",
                status_code=422,
                params={"column": source, "field": field},
            )
        for other, owner in list(used.items()):
            if owner == actual and other != field:
                used.pop(other)
        used[field] = actual
    return used, detected


def supplier_mapping(columns: list[str], mapping_json: Optional[str]) -> tuple[dict, dict]:
    return resolve_mapping(columns, mapping_json, SUPPLIER_FIELDS, _SUPPLIER_ALIASES)


def po_mapping(columns: list[str], mapping_json: Optional[str]) -> tuple[dict, dict]:
    return resolve_mapping(columns, mapping_json, PO_FIELDS, _PO_ALIASES)


# ── Row helpers ───────────────────────────────────────────────────────────────

def row_error(line_no: int, ref: str, code: str, params: dict, fallback: str) -> dict:
    """One rejected row: stable `code` + `params` for i18n, English `error`
    fallback for logs and older clients (same contract as AppError)."""
    log.warning("bulk import: rejected row %s ref=%s: %s", line_no, ref, fallback)
    return {"row": line_no, "ref": ref, "code": code, "params": params, "error": fallback}


def _cell(raw_row: dict, source: Optional[str]) -> str:
    """One cell as clean text. Excel hands integers as floats ("12345.0" would
    become a second, wrong code), so whole floats are written without the dot."""
    if not source:
        return ""
    value = raw_row.get(source)
    if value is None:
        return ""
    if isinstance(value, float) and not isinstance(value, bool):
        if value != value:                       # NaN
            return ""
        if value.is_integer():
            return str(int(value))
    return str(value).strip()


def _row_has_nul(raw_row: dict) -> bool:
    return any("\x00" in str(v) for v in raw_row.values() if v is not None)


def _row_is_blank(raw_row: dict) -> bool:
    return all(
        v is None or (isinstance(v, float) and v != v) or not str(v).strip()
        for v in raw_row.values()
    )


def _clean_ref(text: str) -> str:
    return text.replace("\x00", "")[:60]


def group_errors(errors: list[dict]) -> list[dict]:
    """The per-row errors grouped by (code, column), with a few samples each, so
    the preview shows "37 unknown suppliers" and not 37 lines to read."""
    grouped: dict[tuple, dict] = {}
    for err in errors:
        key = (err["code"], err["params"].get("column"))
        group = grouped.setdefault(key, {
            "code": err["code"], "column": err["params"].get("column"),
            "count": 0, "samples": [],
        })
        group["count"] += 1
        if len(group["samples"]) < 5:
            group["samples"].append({
                "row": err["row"], "ref": err["ref"],
                "value": err["params"].get("value"),
            })
    return list(grouped.values())


# ── Suppliers ─────────────────────────────────────────────────────────────────

def parse_supplier_rows(
    raw_rows: list[dict], mapping: dict,
) -> tuple[list[dict], list[dict], int]:
    """(valid supplier dicts with their `row`, per-row errors, blank rows).

    Validation is the SAME model the direct `POST /inventory/suppliers` uses
    (SupplierCreate, ranges and e-mail shape included): import is not a second,
    looser door into the table.
    """
    from pydantic import ValidationError

    from backend.api.v1.inventory import SupplierCreate

    numeric_sources = [mapping[f] for f in SUPPLIER_INT_FIELDS if f in mapping]
    samples = [
        str(r.get(col)) for r in raw_rows for col in numeric_sources
        if r.get(col) not in (None, "")
    ]
    decimal_comma = stock_import.has_decimal_comma(samples)

    rows: list[dict] = []
    errors: list[dict] = []
    blank = 0
    for line_no, raw_row in enumerate(raw_rows, start=2):
        if _row_is_blank(raw_row):
            blank += 1
            continue
        name = _cell(raw_row, mapping.get("name"))
        if _row_has_nul(raw_row):
            errors.append(row_error(
                line_no, _clean_ref(name), "bulk_import_row_has_nul", {},
                "the row contains a NUL byte and was not imported"))
            continue
        if not name:
            errors.append(row_error(
                line_no, "", "supplier_import_row_missing_name", {"column": "name"},
                "the row has no supplier name"))
            continue

        parsed: dict = {}
        problem: Optional[dict] = None
        for field in SUPPLIER_TEXT_FIELDS:
            text = _cell(raw_row, mapping.get(field))
            if not text:
                continue
            limit = _MAX_NOTES if field == "notes" else _MAX_TEXT
            if len(text) > limit:
                problem = row_error(
                    line_no, _clean_ref(name), "bulk_import_row_text_too_long",
                    {"column": field, "value": text[:20], "max": limit},
                    f"column '{field}' is longer than {limit} characters")
                break
            parsed[field] = text
        if problem is None:
            for field in SUPPLIER_INT_FIELDS:
                text = _cell(raw_row, mapping.get(field))
                if not text:
                    continue
                number = stock_import.parse_number(text, decimal_comma=decimal_comma)
                if number is None or number != int(number):
                    problem = row_error(
                        line_no, _clean_ref(name), "bulk_import_row_not_a_number",
                        {"column": field, "value": text[:40]},
                        f"column '{field}' is not a whole number: '{text[:40]}'")
                    break
                parsed[field] = int(number)
        if problem is not None:
            errors.append(problem)
            continue

        try:
            validated = SupplierCreate(**parsed)
        except ValidationError as e:
            first = e.errors()[0] if e.errors() else {}
            column = str(first.get("loc", ["value"])[0]) if first.get("loc") else "value"
            code = ("supplier_import_row_bad_email" if column == "email"
                    else "bulk_import_row_out_of_range")
            errors.append(row_error(
                line_no, _clean_ref(name), code,
                {"column": column, "value": str(parsed.get(column, ""))[:40],
                 "reason": first.get("type", "")},
                "; ".join(f"{(x['loc'][0] if x.get('loc') else 'value')}: {x['msg']}"
                          for x in e.errors())))
            continue
        data = validated.model_dump(exclude_none=True)
        data["row"] = line_no
        rows.append(data)
    return rows, errors, blank


def collapse_suppliers(rows: list[dict]) -> tuple[list[dict], int]:
    """One entry per supplier NAME (case-insensitive), field-wise, later cells
    winning — the same rule the stock importer applies to a repeated SKU. The
    count of repeated rows is returned so it is reported, never absorbed."""
    merged: dict[str, dict] = {}
    duplicates = 0
    for r in rows:
        key = r["name"].casefold()
        if key in merged:
            duplicates += 1
            first_row = merged[key]["row"]
            merged[key].update(r)
            merged[key]["row"] = first_row
        else:
            merged[key] = dict(r)
    return list(merged.values()), duplicates


# ── Purchase orders ───────────────────────────────────────────────────────────

def parse_po_rows(
    raw_rows: list[dict], mapping: dict,
) -> tuple[list[dict], list[dict], int]:
    """(lines, per-row errors, blank rows). Lines carry the REFERENCES as the
    file wrote them (supplier text, sku text); resolution against the tenant's
    data is `resolve_po_lines`, so the two failure families stay separate."""
    qty_source = mapping.get("qty")
    cost_source = mapping.get("unit_cost")
    samples = [
        str(r.get(c)) for r in raw_rows for c in (qty_source, cost_source)
        if c and r.get(c) not in (None, "")
    ]
    decimal_comma = stock_import.has_decimal_comma(samples)

    from backend.api.v1.inventory import _MAX_MONEY, _MAX_QTY

    lines: list[dict] = []
    errors: list[dict] = []
    blank = 0
    for line_no, raw_row in enumerate(raw_rows, start=2):
        if _row_is_blank(raw_row):
            blank += 1
            continue
        sku = _cell(raw_row, mapping.get("sku"))
        if _row_has_nul(raw_row):
            errors.append(row_error(
                line_no, _clean_ref(sku), "bulk_import_row_has_nul", {},
                "the row contains a NUL byte and was not imported"))
            continue
        if not sku:
            errors.append(row_error(
                line_no, "", "po_import_row_missing_sku", {"column": "sku"},
                "the row has no product code"))
            continue
        supplier = _cell(raw_row, mapping.get("supplier"))
        if not supplier:
            errors.append(row_error(
                line_no, _clean_ref(sku), "po_import_row_missing_supplier",
                {"column": "supplier"}, "the row has no supplier"))
            continue
        qty_text = _cell(raw_row, qty_source)
        qty = stock_import.parse_number(qty_text, decimal_comma=decimal_comma) if qty_text else None
        if qty is None:
            errors.append(row_error(
                line_no, _clean_ref(sku), "bulk_import_row_not_a_number",
                {"column": "qty", "value": qty_text[:40]},
                f"column 'qty' is not a number: '{qty_text[:40]}'"))
            continue
        if not (0 < qty <= _MAX_QTY):
            errors.append(row_error(
                line_no, _clean_ref(sku), "bulk_import_row_out_of_range",
                {"column": "qty", "value": qty_text[:40], "reason": "range"},
                f"column 'qty' must be greater than 0 and at most {_MAX_QTY}"))
            continue
        cost: Optional[float] = None
        cost_text = _cell(raw_row, cost_source)
        if cost_text:
            cost = stock_import.parse_number(cost_text, decimal_comma=decimal_comma)
            if cost is None:
                errors.append(row_error(
                    line_no, _clean_ref(sku), "bulk_import_row_not_a_number",
                    {"column": "unit_cost", "value": cost_text[:40]},
                    f"column 'unit_cost' is not a number: '{cost_text[:40]}'"))
                continue
            if not (0 <= cost <= _MAX_MONEY):
                errors.append(row_error(
                    line_no, _clean_ref(sku), "bulk_import_row_out_of_range",
                    {"column": "unit_cost", "value": cost_text[:40], "reason": "range"},
                    f"column 'unit_cost' must be between 0 and {_MAX_MONEY}"))
                continue
        for field, text in (("sku", sku), ("supplier", supplier)):
            if len(text) > _MAX_TEXT:
                errors.append(row_error(
                    line_no, _clean_ref(sku), "bulk_import_row_text_too_long",
                    {"column": field, "value": text[:20], "max": _MAX_TEXT},
                    f"column '{field}' is longer than {_MAX_TEXT} characters"))
                break
        else:
            lines.append({
                "row": line_no,
                "order_ref": _cell(raw_row, mapping.get("order_ref"))[:80],
                "supplier_ref": supplier,
                "sku_ref": sku,
                "qty": qty,
                "unit_cost": cost,
                "warehouse_ref": _cell(raw_row, mapping.get("warehouse")),
            })
    return lines, errors, blank


def build_resolvers(tenant_id: str) -> dict:
    """Read-only lookups the PO resolver needs, loaded once per request."""
    from backend.inventory import service as svc
    from backend.inventory import supplier_service as sup_svc
    from backend.inventory import warehouse_service as wh_svc

    suppliers = sup_svc.list_suppliers(tenant_id)
    by_supplier_name = {str(s["name"]).strip().casefold(): s for s in suppliers}
    by_supplier_id = {str(s["id"]): s for s in suppliers}

    stock = svc.list_stock(tenant_id)
    sku_exact: dict[str, dict] = {}
    sku_folded: dict[str, list[dict]] = {}
    name_folded: dict[str, list[dict]] = {}
    for row in stock:
        sku = str(row["sku"])
        if sku not in sku_exact:
            sku_exact[sku] = row
            sku_folded.setdefault(sku.casefold(), []).append(row)
        label = (row.get("display_name") or "").strip().casefold()
        if label:
            name_folded.setdefault(label, []).append(row)

    return {
        "suppliers_by_name": by_supplier_name,
        "suppliers_by_id": by_supplier_id,
        "sku_exact": sku_exact,
        "sku_folded": sku_folded,
        "name_folded": name_folded,
        # The default location always exists (stock rows land there when no
        # warehouse is named), even before anything has materialised its row.
        "warehouses": {w.casefold(): w for w in
                       (wh_svc.list_warehouse_names(tenant_id) | {wh_svc.DEFAULT_WAREHOUSE})},
    }


def _resolve_sku(ref: str, res: dict) -> tuple[Optional[dict], Optional[str]]:
    """(stock row, problem). The code wins; a product NAME is accepted only when
    it points at exactly one SKU — a guess between two products is an error."""
    if ref in res["sku_exact"]:
        return res["sku_exact"][ref], None
    folded = res["sku_folded"].get(ref.casefold(), [])
    skus = {str(r["sku"]) for r in folded}
    if len(skus) == 1:
        return folded[0], None
    if len(skus) > 1:
        return None, "ambiguous"
    named = res["name_folded"].get(ref.casefold(), [])
    skus = {str(r["sku"]) for r in named}
    if len(skus) == 1:
        return named[0], None
    if len(skus) > 1:
        return None, "ambiguous"
    return None, "unknown"


def resolve_po_lines(
    lines: list[dict], res: dict,
) -> tuple[list[dict], list[dict]]:
    """Attach the tenant's supplier / SKU / warehouse to each parsed line.

    A reference that does not resolve is reported on ITS row and the rest of the
    file carries on: one typo in a 400-line file must not cost the other 399.
    A missing unit cost falls back to the cost on the stock card (flagged as
    `cost_from_stock`, so the preview can say so) — and stays None when the card
    has none either, which is what a manual order does today.
    """
    resolved: list[dict] = []
    errors: list[dict] = []
    for ln in lines:
        ref = _clean_ref(ln["sku_ref"])
        supplier = (res["suppliers_by_name"].get(ln["supplier_ref"].strip().casefold())
                    or res["suppliers_by_id"].get(ln["supplier_ref"].strip()))
        if supplier is None:
            errors.append(row_error(
                ln["row"], ref, "po_import_row_unknown_supplier",
                {"column": "supplier", "value": ln["supplier_ref"][:60]},
                f"supplier '{ln['supplier_ref'][:60]}' is not registered"))
            continue
        stock_row, problem = _resolve_sku(ln["sku_ref"], res)
        if stock_row is None:
            code = ("po_import_row_ambiguous_sku" if problem == "ambiguous"
                    else "po_import_row_unknown_sku")
            errors.append(row_error(
                ln["row"], ref, code,
                {"column": "sku", "value": ln["sku_ref"][:60]},
                f"product '{ln['sku_ref'][:60]}' is not in the inventory"
                if problem != "ambiguous"
                else f"'{ln['sku_ref'][:60]}' matches more than one product"))
            continue
        warehouse: Optional[str] = None
        if ln["warehouse_ref"]:
            # Never creates a warehouse: that would be a way around the
            # max_locations ceiling, and a typo would mint a location.
            warehouse = res["warehouses"].get(ln["warehouse_ref"].strip().casefold())
            if warehouse is None:
                errors.append(row_error(
                    ln["row"], ref, "po_import_row_unknown_warehouse",
                    {"column": "warehouse", "value": ln["warehouse_ref"][:60]},
                    f"warehouse '{ln['warehouse_ref'][:60]}' does not exist"))
                continue
        cost = ln["unit_cost"]
        from_stock = False
        if cost is None and stock_row.get("unit_cost") is not None:
            cost, from_stock = float(stock_row["unit_cost"]), True
        resolved.append({
            "row": ln["row"],
            "order_ref": ln["order_ref"],
            "supplier": supplier,
            "sku": str(stock_row["sku"]),
            "display_name": stock_row.get("display_name"),
            "qty": ln["qty"],
            "unit_cost": cost,
            "cost_from_stock": from_stock,
            "warehouse": warehouse,
        })
    return resolved, errors


def group_po_lines(lines: list[dict]) -> tuple[list[dict], int]:
    """Group resolved lines into purchase orders.

    One order per (order reference, supplier, destination): an order belongs to
    ONE supplier (that is what `POST /inventory/po` creates and what the PDF and
    the send path assume), so a reference shared by two suppliers becomes two
    orders rather than one order that cannot be sent. Rows with no reference
    group by supplier + destination alone — one order per supplier.

    The same product twice in one order is ONE line with the quantities added
    (the file says "buy 10 and buy 5"); the count of folded rows is returned and
    reported. When the two rows disagree on cost the first stated cost is kept.
    """
    groups: dict[tuple, dict] = {}
    folded = 0
    for ln in lines:
        key = (ln["order_ref"].casefold(), str(ln["supplier"]["id"]),
               (ln["warehouse"] or "").casefold())
        group = groups.setdefault(key, {
            "order_ref": ln["order_ref"], "supplier": ln["supplier"],
            "warehouse": ln["warehouse"], "lines": {}, "rows": [],
        })
        group["rows"].append(ln["row"])
        existing = group["lines"].get(ln["sku"])
        if existing is None:
            group["lines"][ln["sku"]] = dict(ln)
        else:
            folded += 1
            existing["qty"] += ln["qty"]
            if existing["unit_cost"] is None and ln["unit_cost"] is not None:
                existing["unit_cost"] = ln["unit_cost"]
                existing["cost_from_stock"] = ln["cost_from_stock"]
    out = []
    for g in groups.values():
        g["lines"] = list(g["lines"].values())
        out.append(g)
    return out, folded
