"""
Inventory management API.
GET/POST/PATCH/DELETE /inventory/stock     — per-SKU stock CRUD
GET                   /inventory/status    — traffic-light signal + recommendations
POST                  /inventory/bulk      — bulk CSV import
GET                   /inventory/template.csv — downloadable import template
POST                  /inventory/shrinkage    — record shrinkage (non-sale stock-out)
GET                   /inventory/shrinkage    — shrinkage history
"""

import asyncio
import csv
import io
import json
import logging
import re
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Response, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse
from psycopg2.pool import PoolError
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from backend.api.v1.currency import currency_of
from backend.auth.guards import (
    CurrentUser, get_current_user, require_analyst_or_above,
    require_verified_analyst_or_above,
)
from backend.config import settings
from backend.errors import AppError
from backend.entitlements.guards import require_feature
from backend.entitlements.plans import Feature
from backend.entitlements.service import has_feature
from backend.tenants.service import get_tenant
from backend.sessions import planning_service
from backend.inventory import service as svc
from backend.inventory import supplier_service as sup_svc
from backend.inventory import bom_service as bom_svc
from backend.inventory import warehouse_service as wh_svc
from backend.inventory import optimizer_service as opt_svc
from backend.inventory import po_pdf
from backend.inventory import transfer_service as tr_svc
from backend.inventory import transfer_lane_service as lane_svc
from backend.inventory import price_break_service as pb_svc
from backend.inventory import cash_service
from backend.schemas.common import ok
from backend.utils import stock_import
from backend.utils.csv_safe import csv_safe

router = APIRouter(prefix="/inventory", tags=["inventory"])
log = logging.getLogger(__name__)


# ── Request models ─────────────────────────────────────────────────────────────

# Sane upper bounds shared by the direct PUT/PATCH endpoints and the CSV
# import. They sit comfortably above any realistic SMB value but reject the
# absurd inputs (e.g. current_stock=1e15) that an adversarial or garbage CSV
# would otherwise let straight into the DB. Lower bounds (ge=0 / ge=1) are
# preserved unchanged; only maximums are added.
_MAX_QTY = 1_000_000_000        # current_stock / min_stock levels (1e9)
_MAX_MONEY = 1_000_000_000      # unit_cost / sale_price (1e9)
_MAX_MOQ = 1_000_000            # minimum order quantity (1e6)


class StockUpsert(BaseModel):
    display_name:   Optional[str]   = None
    current_stock:   float           = Field(ge=0, le=_MAX_QTY)
    min_stock:   float           = Field(default=0, ge=0, le=_MAX_QTY)
    lead_time_days: int             = Field(default=15, ge=1, le=365)
    unit_cost: Optional[float] = Field(default=None, ge=0, le=_MAX_MONEY)
    moq:            float           = Field(default=1, ge=1, le=_MAX_MOQ)
    supplier:      Optional[str]   = None
    notes:          Optional[str]   = None
    sale_price:   Optional[float] = Field(default=None, ge=0, le=_MAX_MONEY)
    category:      Optional[str]   = None
    # Grouping between category and SKU ("Bebidas" > "Gaseosas" > "Coca 1L");
    # event multipliers can target it (PENDIENTES #6).
    family:         Optional[str]   = None
    brand:          Optional[str]   = None
    unit_of_measure:  Optional[str]   = None
    barcode:  Optional[str]   = None
    warehouse:         Optional[str]   = None


class StockPatch(BaseModel):
    display_name:   Optional[str]   = None
    current_stock:   Optional[float] = Field(default=None, ge=0, le=_MAX_QTY)
    min_stock:   Optional[float] = Field(default=None, ge=0, le=_MAX_QTY)
    lead_time_days: Optional[int]   = Field(default=None, ge=1, le=365)
    unit_cost: Optional[float] = Field(default=None, ge=0, le=_MAX_MONEY)
    moq:            Optional[float] = Field(default=None, ge=1, le=_MAX_MOQ)
    supplier:      Optional[str]   = None
    notes:          Optional[str]   = None
    sale_price:   Optional[float] = Field(default=None, ge=0, le=_MAX_MONEY)
    category:      Optional[str]   = None
    family:         Optional[str]   = None
    brand:          Optional[str]   = None
    unit_of_measure:  Optional[str]   = None
    barcode:  Optional[str]   = None
    warehouse:         Optional[str]   = None


# ── Stock CRUD ─────────────────────────────────────────────────────────────────

@router.get("/stock")
def list_stock(user: CurrentUser = Depends(get_current_user)):
    return ok(svc.list_stock(user.tenant_id))


@router.get("/stock/{sku}")
def get_stock(sku: str, user: CurrentUser = Depends(get_current_user)):
    row = svc.get_stock(user.tenant_id, sku)
    if not row:
        raise AppError(
            "stock_sku_not_found", f"SKU '{sku}' not found in inventory",
            status_code=404, params={"sku": sku},
        )
    return ok(row)


@router.put("/stock/{sku}", status_code=200)
def upsert_stock(
    sku: str,
    body: StockUpsert,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    from backend.entitlements.service import enforce_limit

    # Resolve to the canonical spelling FIRST so the pre-checks below judge
    # the same (sku, warehouse) row svc.upsert_stock will actually write —
    # 'norte' with an existing 'Norte' is an update, not a new location.
    warehouse = wh_svc.resolve_canonical_name(user.tenant_id, body.warehouse)
    if not svc.get_stock(user.tenant_id, sku, warehouse=warehouse):
        enforce_limit(user.tenant_id, "max_skus", svc.count_stock(user.tenant_id))
    # A new warehouse name would otherwise be auto-created for free by
    # svc.upsert_stock -> _ensure_warehouse, bypassing max_locations entirely.
    # Enforce BEFORE the write so a blocked request never creates the row.
    if not wh_svc.get_warehouse_by_name(user.tenant_id, warehouse):
        enforce_limit(user.tenant_id, "max_locations", wh_svc.count_warehouses(user.tenant_id))
    row = svc.upsert_stock(user.tenant_id, sku, body.model_dump(exclude_none=True))
    return ok(row)


@router.patch("/stock/{sku}")
def patch_stock(
    sku: str,
    body: StockPatch,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    existing = svc.get_stock(user.tenant_id, sku)
    if not existing:
        raise AppError(
            "stock_sku_not_found", f"SKU '{sku}' not found in inventory",
            status_code=404, params={"sku": sku},
        )
    data = body.model_dump(exclude_none=True)
    if not data:
        return ok(existing)
    row = svc.upsert_stock(user.tenant_id, sku, data)
    return ok(row)


@router.delete("/stock/{sku}", status_code=204)
def delete_stock(sku: str, user: CurrentUser = Depends(require_analyst_or_above)):
    existing = svc.get_stock(user.tenant_id, sku)
    if not existing:
        raise AppError(
            "stock_sku_not_found", f"SKU '{sku}' not found in inventory",
            status_code=404, params={"sku": sku},
        )
    svc.delete_stock(user.tenant_id, sku)


# ── Bulk import from CSV / Excel ──────────────────────────────────────────────

# Upper bound on the per-row diagnostics echoed back when a whole import is
# rejected — the count is always exact, only the sample is capped.
_MAX_REPORTED_ROW_ERRORS = 50

# Rows echoed back by the preview so the user can see what the mapping will
# actually write before committing to it.
_PREVIEW_SAMPLE_ROWS = 8


def _decode_csv(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")  # handle BOM from Excel
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _read_upload(filename: Optional[str], content: bytes) -> tuple[str, list[str], list[dict], str]:
    """
    (format, columns, raw rows, separator) for a stock upload.

    Excel goes through backend/dataframes (the only layer allowed to touch
    pandas) and comes back as plain dicts; CSV stays on the stdlib reader with
    a sniffed separator, so a Spanish-locale ';' export is no longer read as a
    single column.
    """
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xls", ".xlsm")):
        from backend.dataframes.io import read_rows
        try:
            rows = read_rows(content, fmt="excel")
        except Exception as e:
            log.warning("stock import: unreadable Excel file '%s': %s", filename, e)
            raise AppError(
                "inventory_import_unreadable_file",
                "The file could not be read as a spreadsheet",
                status_code=422,
                params={"filename": filename or ""},
            )
        columns = list(rows[0].keys()) if rows else []
        return "excel", [str(c) for c in columns], rows, ""

    text = _decode_csv(content)
    sep = stock_import.sniff_separator(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=sep)
    rows = [dict(r) for r in reader]
    columns = [str(c) for c in (reader.fieldnames or []) if c is not None]
    return "csv", columns, rows, sep


def _resolve_mapping(columns: list[str], mapping_json: Optional[str]) -> tuple[dict, dict]:
    """
    (mapping actually used, mapping auto-detected). An explicit mapping from
    the wizard wins per field; every field the user did not pin keeps the
    detected column, so a partial mapping is a correction, not a reset.
    """
    detected = stock_import.detect_mapping(columns)
    if not mapping_json:
        return dict(detected), detected

    try:
        explicit = json.loads(mapping_json)
    except (ValueError, TypeError):
        raise AppError(
            "inventory_import_bad_mapping",
            "mapping must be a JSON object of {canonical_field: source_column}",
            status_code=422,
        )
    if not isinstance(explicit, dict):
        raise AppError(
            "inventory_import_bad_mapping",
            "mapping must be a JSON object of {canonical_field: source_column}",
            status_code=422,
        )

    used = dict(detected)
    by_normalized = {stock_import.normalize(c): c for c in columns}
    for field, source in explicit.items():
        if field not in stock_import.CANONICAL_FIELDS:
            continue
        if source in (None, ""):
            used.pop(field, None)          # explicit "do not import this field"
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
        # One source column per field: pinning it here releases it elsewhere.
        for other, taken in list(used.items()):
            if taken == actual and other != field:
                used.pop(other)
        used[field] = actual
    return used, detected


def _row_error(line_no: int, sku: str, code: str, params: dict, fallback: str) -> dict:
    """
    One rejected row. Carries a stable snake_case `code` + `params` (what the
    UI renders through i18n) and keeps `error` as the English fallback for
    logs and older clients — the same contract AppError uses.
    """
    log.warning("stock import: rejected row %s sku=%s: %s", line_no, sku, fallback)
    return {"row": line_no, "sku": sku, "code": code, "params": params, "error": fallback}


def _parse_stock_rows(raw_rows: list[dict], mapping: dict) -> tuple[list[dict], list[dict], int]:
    """
    (valid canonical rows, per-row errors, rows skipped for having no SKU).

    Numbers are read with the LatAm-tolerant parser: '1.234,56' and '₡ 1 234'
    are values, 'N/D' is a reported error. The comma/dot verdict is taken once
    for the whole file so an ambiguous '1,250' inherits what its unambiguous
    neighbours already proved.
    """
    numeric_sources = [mapping[f] for f in stock_import.NUMERIC_FIELDS if f in mapping]
    samples = [
        str(r.get(col)) for r in raw_rows for col in numeric_sources
        if r.get(col) not in (None, "")
    ]
    decimal_comma = stock_import.has_decimal_comma(samples)

    rows: list[dict] = []
    errors: list[dict] = []
    skipped_no_sku = 0

    # enumerate from 2: the header is line 1, so the first data row is line 2 —
    # the number the user sees in their spreadsheet.
    for line_no, raw_row in enumerate(raw_rows, start=2):
        row = stock_import.apply_mapping(raw_row, mapping)
        sku = row.get("sku", "").strip()
        if not sku:
            skipped_no_sku += 1
            continue

        parsed: dict = {"sku": sku}
        for fld in stock_import.TEXT_FIELDS:
            if fld in row:
                parsed[fld] = row[fld]

        # A field only reaches here when the cell was non-empty, so a parse
        # failure means genuine garbage ('N/D'), NOT a blank optional cell.
        # Report it instead of coercing to 0 and importing silently.
        row_error: Optional[dict] = None
        for fld in stock_import.NUMERIC_FIELDS:
            if fld not in row:
                continue
            value = stock_import.parse_number(row[fld], decimal_comma=decimal_comma)
            if value is None:
                row_error = _row_error(
                    line_no, sku, "inventory_import_row_not_a_number",
                    {"column": fld, "value": row[fld]},
                    f"column '{fld}' is not a number: '{row[fld]}'",
                )
                break
            parsed[fld] = int(value) if fld in stock_import.INT_FIELDS else value
        if row_error is not None:
            errors.append(row_error)
            continue

        # Same constraints as the direct PUT/PATCH endpoints (ge=0 lower
        # bounds and the sane upper bounds above) — without this, import would
        # be the only write path letting negative or absurd values into the DB.
        try:
            validated = StockPatch(**{k: v for k, v in parsed.items() if k != "sku"})
        except ValidationError as e:
            first = e.errors()[0] if e.errors() else {}
            column = str(first.get("loc", ["value"])[0]) if first.get("loc") else "value"
            detail = "; ".join(
                f"{(err['loc'][0] if err.get('loc') else 'value')}: {err['msg']}"
                for err in e.errors()
            )
            errors.append(_row_error(
                line_no, sku, "inventory_import_row_out_of_range",
                {"column": column, "value": parsed.get(column), "reason": first.get("type", "")},
                detail,
            ))
            continue
        rows.append({"sku": sku, **validated.model_dump(exclude_none=True)})

    return rows, errors, skipped_no_sku


@router.post("/bulk/preview")
async def bulk_import_preview(
    file: UploadFile = File(...),
    mapping: Optional[str] = Form(default=None),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Dry run of POST /bulk: what we detected in the file and what we would
    write, without touching a single row. This is what makes the mapping
    wizard possible — the user corrects our column guesses BEFORE importing,
    the same way the sales upload works.
    """
    content = await file.read()
    fmt, columns, raw_rows, sep = _read_upload(file.filename, content)
    used, detected = _resolve_mapping(columns, mapping)
    rows, errors, skipped_no_sku = _parse_stock_rows(raw_rows, used)

    # Group the per-row errors so the UI shows "37 non-numeric cells", not 37
    # separate lines the user has to read one by one.
    grouped: dict[tuple, dict] = {}
    for err in errors:
        key = (err["code"], err["params"].get("column"))
        group = grouped.setdefault(key, {
            "code": err["code"],
            "column": err["params"].get("column"),
            "count": 0,
            "samples": [],
        })
        group["count"] += 1
        if len(group["samples"]) < 5:
            group["samples"].append({"row": err["row"], "sku": err["sku"],
                                     "value": err["params"].get("value")})

    return ok({
        "format": fmt,
        "separator": sep,
        "columns": columns,
        "total_rows": len(raw_rows),
        # Field -> column we will read. `detected_mapping` is what the file
        # alone suggested, so the UI can show which picks are its own.
        "mapping": used,
        "detected_mapping": detected,
        "unmapped_columns": [c for c in columns if c not in used.values()],
        "missing_required": [] if "sku" in used else ["sku"],
        "importable_rows": len(rows),
        "rejected_rows": len(errors),
        "skipped_no_sku": skipped_no_sku,
        "sample_rows": rows[:_PREVIEW_SAMPLE_ROWS],
        "issues": list(grouped.values()),
        "fields": list(stock_import.CANONICAL_FIELDS),
    })


@router.post("/bulk")
async def bulk_import(
    file: UploadFile = File(...),
    mapping: Optional[str] = Form(default=None),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Import stock from a CSV or Excel file.

    Columns are matched by alias, accent- and case-blind, so a real ERP export
    ('Código', 'Existencia', 'Costo Unitario', separated by ';') imports with
    no hand-editing. `mapping` — a JSON object of {canonical_field:
    source_column} sent by the wizard — overrides the detection per field.

    Canonical fields: sku, warehouse, display_name, category, brand,
    unit_of_measure, barcode, current_stock, min_stock, lead_time_days,
    unit_cost, sale_price, moq, supplier, notes.
    """
    content = await file.read()

    # Parsing is CPU-bound over the whole file — a 3k-row sheet is thousands of
    # coercions — and it ran on the event loop, so every other request in the
    # process waited for it. 72a8ec4 offloaded the WRITES and stopped there;
    # this is the other half of the same defect.
    def _parse():
        fmt_, columns_, raw_rows_, _sep_ = _read_upload(file.filename, content)
        used_, detected_ = _resolve_mapping(columns_, mapping)
        rows_, errors_, skipped_ = _parse_stock_rows(raw_rows_, used_)
        return fmt_, columns_, used_, detected_, rows_, errors_, skipped_

    fmt, columns, used, detected, rows, errors, skipped_no_sku = await asyncio.to_thread(_parse)

    if not rows:
        # The whole file was rejected — a user event, not API misuse, so it
        # carries a code. The per-row diagnostics ride along in params, capped:
        # a 10k-row garbage CSV used to echo 10k error objects back.
        raise AppError(
            "inventory_import_no_valid_rows",
            "No valid rows found in the file. Ensure a product-code column exists.",
            status_code=422,
            params={
                "rejected": len(errors),
                "errors": errors[:_MAX_REPORTED_ROW_ERRORS],
                "columns": columns,
                "mapping": used,
                "missing_required": [] if "sku" in used else ["sku"],
            },
        )

    # Everything from here to the write is blocking DB work — five round-trips
    # plus the per-warehouse lookups — so it goes to the same thread as the
    # write rather than the event loop. Keeping the checks and the write
    # together also preserves the property the comments below depend on: no row
    # is written until every limit has been enforced.
    #
    # This is what the stress test caught. bulk_upsert was already offloaded,
    # but /health still stalled 5.4s against a 0.02s baseline during a 3k-row
    # import, because the loop was waiting on these.
    from backend.entitlements.service import enforce_limit

    def _check_and_write() -> int:
        # Resolve every distinct warehouse spelling in the CSV to its canonical
        # form BEFORE the limit pre-checks and the writes: 'norte' rows must
        # land on an existing 'Norte' location instead of counting as (and
        # creating) a new one. One lookup per distinct name, not per row.
        resolved_wh = {
            raw: wh_svc.resolve_canonical_name(user.tenant_id, raw)
            for raw in {r.get("warehouse") for r in rows}
        }
        for r in rows:
            r["warehouse"] = resolved_wh[r.get("warehouse")]

        existing_keys = svc.list_stock_keys(user.tenant_id)
        new_keys = {(r["sku"], r["warehouse"]) for r in rows} - existing_keys
        enforce_limit(user.tenant_id, "max_skus", svc.count_stock(user.tenant_id),
                      adding=len(new_keys))

        # Same bypass risk as PUT /stock: a CSV with N distinct new warehouse
        # names would otherwise create all N for free via
        # svc.upsert_stock -> _ensure_warehouse inside bulk_upsert's loop.
        # Compute the DISTINCT new names up front and enforce max_locations
        # against (current count + new names) BEFORE bulk_upsert writes
        # anything, so a blocked import never partially creates stock rows.
        existing_wh_names = wh_svc.list_warehouse_names(user.tenant_id)
        new_wh_names = {r["warehouse"] for r in rows} - existing_wh_names
        enforce_limit(user.tenant_id, "max_locations", wh_svc.count_warehouses(user.tenant_id),
                      adding=len(new_wh_names))

        # bulk_upsert does one synchronous DB round-trip per row.
        return svc.bulk_upsert(user.tenant_id, rows)

    count = await asyncio.to_thread(_check_and_write)
    result = {
        "imported": count,
        "total_rows": len(rows),
        "format": fmt,
        # What we read the file as, so the UI can say "we took Existencia as
        # your stock" instead of leaving the user guessing.
        "mapping": used,
        "detected_mapping": detected,
        "unmapped_columns": [c for c in columns if c not in used.values()],
        "skipped_no_sku": skipped_no_sku,
    }
    # Surface rejected rows so the user learns their data was garbage instead
    # of it being silently dropped/coerced.
    if errors:
        result["errors"] = errors
        result["error_count"] = len(errors)
    return ok(result)


_TEMPLATE_COLUMNS = [
    "sku", "warehouse", "display_name", "category", "brand", "unit_of_measure", "barcode",
    "current_stock", "min_stock", "lead_time_days", "unit_cost",
    "sale_price", "moq", "supplier", "notes",
]
_TEMPLATE_EXAMPLE = [
    "SKU001", "principal", "Agua 600ml", "Bebidas", "AguaPura", "caja", "7501234567890",
    "120", "20", "7", "3.50", "5.90", "12", "Distribuidora Sur", "producto de ejemplo",
]


@router.get("/template.csv")
def download_template(user: CurrentUser = Depends(get_current_user)):
    """Canonical inventory import template: header row + one example row."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_TEMPLATE_COLUMNS)
    w.writerow(_TEMPLATE_EXAMPLE)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="inventory_template.csv"'},
    )


# ── Setup gaps — ask only for what moves the needle ───────────────────────────

@router.get("/setup-gaps")
def setup_gaps(
    session_id: Optional[str] = Query(
        default=None,
        description="Completed forecast session; defaults to the tenant's active-period session"),
    horizon_days: int = Query(default=30, ge=1, le=365,
                              description="Window the projected spend is measured over"),
    limit: int = Query(default=50, ge=1, le=500),
    target_pct: float = Query(default=80.0, ge=1.0, le=100.0,
                              description="Share of projected spend the recommendation aims at"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Unconfigured SKUs ordered by the money they move, with the running
    cumulative share of projected spend.

    This is what turns "configure 2.000 rows" — which nobody finishes — into
    "configure these 40 and you cover 82% of your monthly purchase". The
    ordering is by SPEND (projected demand x unit price), never by row count;
    `basis` says so explicitly, and falls back to "units" when the tenant has
    given us no money figure at all rather than pretending otherwise.
    """
    from backend.inventory import setup_gaps_service as gaps_svc

    if not session_id:
        session_id = planning_service.resolve_active_session(user.tenant_id)
        if not session_id:
            raise AppError(
                "no_completed_session",
                "No completed session for this tenant yet",
                status_code=400,
            )

    return ok(gaps_svc.get_setup_gaps(
        user.tenant_id, session_id,
        horizon_days=horizon_days, limit=limit, target_pct=target_pct,
    ))


# ── Status endpoint — the core of the product ─────────────────────────────────

def _strip_abc_xyz_unless_entitled(
    items: list[dict], tenant_id: str, extra_keys: tuple[str, ...] = (),
) -> list[dict]:
    """
    ABC-XYZ classification is a Professional+ feature (Feature.ABC_XYZ), but
    it rides along as extra keys on otherwise-core inventory items rather than
    living behind its own endpoint. Starter tenants must still get the core
    signal/coverage/recommendation data — so we omit the classification keys
    (graceful degradation) instead of 403-ing the whole read. No-op in
    testing_mode, matching every other entitlement check.

    ``extra_keys`` lets a call site drop additional fields that are DERIVED
    from the classification (e.g. dead-stock's ``action_suggested``, which is
    picked from ``item['abc']``) — otherwise a non-entitled tenant could
    reverse-engineer the stripped classification from those derived fields.
    """
    if settings.testing_mode:
        return items
    tenant = get_tenant(tenant_id) or {}
    if has_feature(tenant, Feature.ABC_XYZ):
        return items
    for item in items:
        item.pop("abc", None)
        item.pop("xyz", None)
        item.pop("abc_xyz", None)
        for key in extra_keys:
            item.pop(key, None)
    return items


_COVERAGE_UNIT = {"daily": "day", "weekly": "week", "monthly": "month"}


@router.get("/status")
def inventory_status(
    session_id: Optional[str] = Query(
        default=None,
        description="Completed forecast session; defaults to the tenant's active-period session"),
    service_level: float = Query(default=0.95, ge=0.5, le=0.999),
    signal: Optional[str] = Query(default=None, description="Filter by signal: PEDIR_YA, PEDIR_PRONTO, OK, SOBRESTOCK, SIN_DATOS"),
    supplier: Optional[str] = Query(default=None),
    by_warehouse: bool = Query(default=False, description="Per-(sku, warehouse) rows with network transfer suggestions"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Returns per-SKU inventory status in the tenant's ACTIVE planning period:
    - coverage (in the active period's units — see `coverage_unit`)
    - traffic-light signal (PEDIR_YA / PEDIR_PRONTO / OK / SOBRESTOCK / SIN_DATOS)
    - recommended order quantity
    - inventory value
    """
    if not session_id:
        session_id = planning_service.resolve_active_session(user.tenant_id)
        if not session_id:
            raise AppError(
                "no_completed_session",
                "No completed session for this tenant yet",
                status_code=400,
            )

    period = planning_service.get_planning(user.tenant_id).get("period", "daily")

    # Both views share the source-then-filter shape; only the response
    # envelope differs. abc/xyz stripping applies to the aggregated view only
    # — per-warehouse rows never carry classification fields.
    if by_warehouse:
        items = svc.get_inventory_status_by_warehouse(user.tenant_id, session_id, service_level, period)
    else:
        items = svc.get_inventory_status(user.tenant_id, session_id, service_level, period)
        items = _strip_abc_xyz_unless_entitled(items, user.tenant_id)

    if signal:
        signal_up = signal.upper()
        items = [i for i in items if i["signal"] == signal_up]

    if supplier:
        items = [i for i in items if (i.get("supplier") or "").lower() == supplier.lower()]

    if by_warehouse:
        return ok({
            "period": period,
            "coverage_unit": _COVERAGE_UNIT.get(period, "day"),
            "items": items,
            "summary": {
                "total_rows": len(items),
                "order_now": sum(1 for i in items if i["signal"] == "PEDIR_YA"),
                "order_soon": sum(1 for i in items if i["signal"] == "PEDIR_PRONTO"),
                "transfers_suggested": sum(
                    1 for i in items if i.get("recommended_action") == "transfer"),
            },
        })

    total_value = sum(i["inventory_value"] for i in items if i.get("inventory_value"))
    critical    = sum(1 for i in items if i["signal"] == "PEDIR_YA")
    warning     = sum(1 for i in items if i["signal"] == "PEDIR_PRONTO")

    return ok({
        "period": period,
        "coverage_unit": _COVERAGE_UNIT.get(period, "day"),
        "items": items,
        "excluded_skus": svc.get_excluded_skus(user.tenant_id, session_id),
        "summary": {
            "total_skus":    len(items),
            "order_now":      critical,
            "order_soon":  warning,
            "ok":            sum(1 for i in items if i["signal"] == "OK"),
            "overstock":    sum(1 for i in items if i["signal"] == "SOBRESTOCK"),
            "sin_datos":     sum(1 for i in items if i["signal"] == "SIN_DATOS"),
            "total_inventory_value": round(total_value, 2),
        },
    })


# ── Mermas (shrinkage / non-sale stock-outs) ──────────────────────────────────

class ShrinkageCreate(BaseModel):
    sku:         str
    quantity:    float = Field(gt=0)
    reason:      str   # breakage | expiry | self_consumption | gift
    warehouse:      Optional[str] = None
    notes:       Optional[str] = None
    occurred_at: Optional[str] = None  # ISO date/datetime; default: ahora


@router.post("/shrinkage", status_code=201)
def create_shrinkage(
    body: ShrinkageCreate,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Record a stock-out that is NOT a sale — breakage, expiry,
    self-consumption or a gift/sample. Decrements the SKU's theoretical stock
    through the same path PO reception uses (so the signal stays accurate) and
    accumulates the cost (quantity x unit cost) for a future monthly
    shrinkage summary.
    """
    from backend.inventory import shrinkage_service as shrinkage_svc
    from datetime import datetime as _dt

    occurred_at = None
    if body.occurred_at:
        try:
            occurred_at = _dt.fromisoformat(body.occurred_at)
        except ValueError:
            raise AppError(
                "date_invalid_iso",
                "occurred_at must be an ISO date (YYYY-MM-DD)",
                params={"field": "occurred_at"},
            )

    # record_shrinkage raises AppError (own status_code + code + params) on bad
    # state/input; it propagates to the AppError handler in backend/main.py.
    row = shrinkage_svc.record_shrinkage(
        user.tenant_id, body.sku, body.quantity, body.reason,
        user_id=user.user_id, warehouse=body.warehouse, notes=body.notes,
        occurred_at=occurred_at,
    )
    return ok(row)


@router.get("/shrinkage")
def list_shrinkage(
    sku: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
):
    """Recent history of recorded shrinkage (input to the future monthly summary)."""
    from backend.inventory import shrinkage_service as shrinkage_svc
    return ok(shrinkage_svc.list_shrinkage(user.tenant_id, sku=sku, limit=limit))


@router.get("/shrinkage/reasons")
def list_shrinkage_reasons(user: CurrentUser = Depends(get_current_user)):
    """Returns the valid shrinkage reason codes (labels are handled client-side via i18n)."""
    from backend.inventory import shrinkage_service as shrinkage_svc
    return ok(list(shrinkage_svc.REASONS))


# ── Stock history ─────────────────────────────────────────────────────────────

@router.get("/stock/{sku}/history")
def get_stock_history(
    sku: str,
    days: int = Query(default=30, ge=1, le=365),
    user: CurrentUser = Depends(get_current_user),
):
    """Returns point-in-time stock snapshots for trend visualization."""
    existing = svc.get_stock(user.tenant_id, sku)
    if not existing:
        raise AppError(
            "stock_sku_not_found", f"SKU '{sku}' not found in inventory",
            status_code=404, params={"sku": sku},
        )
    history = svc.get_stock_history(user.tenant_id, sku, days=days)
    return ok({"sku": sku, "days": days, "history": history})


# ── Dashboard summary (lightweight — only summary block) ──────────────────────

@router.get("/dashboard-summary")
def dashboard_summary(
    session_id: str = Query(...),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Lightweight endpoint for the dashboard widget.
    Returns only the summary counts without the full item list.
    """
    items = svc.get_inventory_status(user.tenant_id, session_id)
    total_value = sum(i["inventory_value"] for i in items if i.get("inventory_value"))
    return ok({
        "session_id":   session_id,
        "total_skus":   len(items),
        "order_now":     sum(1 for i in items if i["signal"] == "PEDIR_YA"),
        "order_soon": sum(1 for i in items if i["signal"] == "PEDIR_PRONTO"),
        "ok":           sum(1 for i in items if i["signal"] == "OK"),
        "overstock":   sum(1 for i in items if i["signal"] == "SOBRESTOCK"),
        "sin_datos":    sum(1 for i in items if i["signal"] == "SIN_DATOS"),
        "total_inventory_value": round(total_value, 2),
        "top_critical": [
            {"sku": i["sku"], "display_name": i["display_name"], "coverage_days": i["coverage_days"]}
            for i in items if i["signal"] == "PEDIR_YA"
        ][:5],
    })


# ── Events (temporadas / promociones) ────────────────────────────────────────

def _parse_event_date(value: str, field: str) -> date:
    # A bare `ValueError` inside a validator reaches the browser as pydantic's
    # generic `value_error` on loc ["body"], which the frontend could only
    # render as "body: no es válido." — measured on screen when saving an event
    # whose end date preceded its start. A stable type plus params lets the
    # catalogue say which date and why.
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise PydanticCustomError(
            "event_date_invalid",
            "'{field}' must be a date written as YYYY-MM-DD.",
            {"field": field, "value": str(value)[:32]},
        )


class EventCreate(BaseModel):
    name:       str
    start_date: str   # ISO date YYYY-MM-DD
    end_date:   str
    multiplier: float = Field(default=1.0, ge=0.1, le=10.0)
    notes:      Optional[str] = None

    @model_validator(mode="after")
    def _check_date_order(self):
        start = _parse_event_date(self.start_date, "start_date")
        end = _parse_event_date(self.end_date, "end_date")
        if end < start:
            raise PydanticCustomError(
                "event_end_before_start",
                "The event ends before it starts.",
                {"start": self.start_date, "end": self.end_date},
            )
        return self


class EventPatch(BaseModel):
    name:       Optional[str]   = None
    start_date: Optional[str]   = None
    end_date:   Optional[str]   = None
    multiplier: Optional[float] = Field(default=None, ge=0.1, le=10.0)
    notes:      Optional[str]   = None
    active:     Optional[bool]  = None

    @model_validator(mode="after")
    def _check_date_order(self):
        if self.start_date is not None:
            _parse_event_date(self.start_date, "start_date")
        if self.end_date is not None:
            _parse_event_date(self.end_date, "end_date")
        if self.start_date is not None and self.end_date is not None:
            if date.fromisoformat(self.end_date) < date.fromisoformat(self.start_date):
                raise PydanticCustomError(
                    "event_end_before_start",
                    "The event ends before it starts.",
                    {"start": self.start_date, "end": self.end_date},
                )
        return self


class SimulateEventRequest(BaseModel):
    session_id: str
    # Either reference a saved event…
    event_id:   Optional[str] = None
    # …or simulate ad-hoc dates/multiplier (used when event_id is absent)
    start_date: Optional[str]   = None
    end_date:   Optional[str]   = None
    multiplier: Optional[float] = Field(default=None, gt=0, le=10)
    name:       Optional[str]   = None


@router.post("/events/simulate", dependencies=[Depends(require_feature(Feature.EVENT_SIMULATOR))])
def simulate_event(body: SimulateEventRequest, user: CurrentUser = Depends(get_current_user)):
    """
    What-if simulator — project a promo/season's impact per SKU: extra demand,
    stock survival, quantity to order and the latest order date. Read-only.
    """
    if body.event_id:
        ev = svc.get_event(user.tenant_id, body.event_id)
        if not ev:
            raise AppError("event_not_found", "Event not found", status_code=404)
        start, end = str(ev["start_date"]), str(ev["end_date"])
        mult = float(body.multiplier or ev.get("multiplier") or 1.0)
        name = body.name or ev.get("name")
    else:
        if not (body.start_date and body.end_date and body.multiplier):
            raise AppError(
                "event_simulate_missing_fields",
                "start_date, end_date and multiplier are required without an event_id",
            )
        start, end, mult, name = body.start_date, body.end_date, body.multiplier, body.name

    try:
        result = svc.simulate_event_impact(
            user.tenant_id, body.session_id, start, end, mult,
            event_name=name, event_id=body.event_id,
        )
    except AppError:
        # Already carries its own code/params — wrapping it would strip them.
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return ok(result)


# ── Per-product event multipliers ────────────────────────────────────────────

class EventMultiplierUpsert(BaseModel):
    # Narrowest match wins: sku > family > category > the event's own multiplier.
    scope:       Literal["sku", "family", "category"]
    scope_value: str
    multiplier:  float = Field(ge=0.1, le=10.0)


@router.get("/events/{event_id}/multipliers", dependencies=[Depends(require_feature(Feature.EVENT_SIMULATOR))])
def list_event_multipliers(event_id: str, user: CurrentUser = Depends(get_current_user)):
    """Per-SKU, per-family or per-category multiplier overrides for this event."""
    if not svc.get_event(user.tenant_id, event_id):
        raise AppError("event_not_found", "Event not found", status_code=404)
    return ok(svc.get_event_multipliers(user.tenant_id, event_id))


@router.put("/events/{event_id}/multipliers", dependencies=[Depends(require_feature(Feature.EVENT_SIMULATOR))])
def upsert_event_multiplier(
    event_id: str,
    body: EventMultiplierUpsert,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Pin the multiplier for a product or a category on this event.
    On Black Friday electronics do not behave like milk.
    """
    if not svc.get_event(user.tenant_id, event_id):
        raise AppError("event_not_found", "Event not found", status_code=404)
    try:
        row = svc.set_event_multiplier(
            user.tenant_id, event_id, body.scope, body.scope_value, body.multiplier,
        )
    except AppError:
        # Already carries its own code/params — wrapping it would strip them.
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return ok(row)


@router.delete(
    "/events/{event_id}/multipliers/{override_id}", status_code=204,
    dependencies=[Depends(require_feature(Feature.EVENT_SIMULATOR))],
)
def remove_event_multiplier(
    event_id: str,
    override_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Drop the override: the product falls back to the event multiplier."""
    if not svc.delete_event_multiplier(user.tenant_id, override_id):
        raise AppError(
            "event_multiplier_override_not_found", "Override not found", status_code=404,
        )


@router.get("/events", dependencies=[Depends(require_feature(Feature.EVENT_SIMULATOR))])
def list_events(user: CurrentUser = Depends(get_current_user)):
    return ok(svc.list_events(user.tenant_id))


@router.get("/events/upcoming", dependencies=[Depends(require_feature(Feature.EVENT_SIMULATOR))])
def upcoming_events(
    days: int = Query(default=60, ge=1, le=365),
    user: CurrentUser = Depends(get_current_user),
):
    return ok(svc.get_upcoming_events(user.tenant_id, days_ahead=days))


@router.post(
    "/events", status_code=201,
    dependencies=[Depends(require_feature(Feature.EVENT_SIMULATOR))],
)
def create_event(body: EventCreate, user: CurrentUser = Depends(require_analyst_or_above)):
    ev = svc.create_event(user.tenant_id, body.model_dump())
    return ok(ev)


@router.patch("/events/{event_id}", dependencies=[Depends(require_feature(Feature.EVENT_SIMULATOR))])
def patch_event(
    event_id: str,
    body: EventPatch,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    data = body.model_dump(exclude_none=True)
    if "start_date" in data or "end_date" in data:
        existing = svc.get_event(user.tenant_id, event_id)
        if not existing:
            raise AppError("event_not_found", "Event not found", status_code=404)
        effective_start = data.get("start_date", str(existing["start_date"]))
        effective_end = data.get("end_date", str(existing["end_date"]))
        if date.fromisoformat(effective_end) < date.fromisoformat(effective_start):
            raise AppError(
                "event_end_before_start",
                "end_date must not be before start_date",
                status_code=422,
            )

    ev = svc.update_event(user.tenant_id, event_id, data)
    if not ev:
        raise AppError("event_not_found", "Event not found", status_code=404)
    return ok(ev)


@router.delete(
    "/events/{event_id}", status_code=204,
    dependencies=[Depends(require_feature(Feature.EVENT_SIMULATOR))],
)
def delete_event(event_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    svc.delete_event(user.tenant_id, event_id)


# ── LatAm commercial calendar (feature 3.4) ──────────────────────────────────

class CalendarSeedRequest(BaseModel):
    country: str = "CR"
    years:   Optional[list[int]] = Field(default=None, max_length=5)


class CatalogToggleRequest(BaseModel):
    active: bool


@router.get("/events/catalog", dependencies=[Depends(require_feature(Feature.EVENT_SIMULATOR))])
def get_event_catalog(
    country: str = Query(default="CR", max_length=4),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Which commercial events Faro knows for a country, and whether this tenant
    has them seeded / switched on. Read-only.
    """
    from backend.inventory import calendar_catalog as cat

    country = country.upper()
    if country not in cat.SUPPORTED_COUNTRIES:
        available = ", ".join(cat.SUPPORTED_COUNTRIES)
        raise AppError(
            "calendar_country_unsupported",
            f"No catalog for country '{country}'. Available: {available}",
            params={"country": country, "available": available},
        )

    seeded = svc.get_catalog_state(user.tenant_id, country)
    entries = []
    for entry in cat.describe_catalog(country):
        state = seeded.get(entry["key"], {})
        entries.append({
            **entry,
            "seeded":       state.get("total", 0) > 0,
            "occurrences":  state.get("total", 0),
            "active":       state.get("active", 0) > 0,
            "next_start":   state.get("next_start"),
        })
    return ok({
        "country":   country,
        "countries": cat.SUPPORTED_COUNTRIES,
        "entries":   entries,
    })


@router.post("/events/catalog/seed", dependencies=[Depends(require_feature(Feature.EVENT_SIMULATOR))])
def seed_event_catalog(
    body: CalendarSeedRequest,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Preload the LatAm commercial calendar into this tenant's events."""
    try:
        result = svc.seed_calendar_events(user.tenant_id, body.country, body.years)
    except AppError:
        # Already carries its own code/params — wrapping it would strip them.
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return ok(result)


@router.patch(
    "/events/catalog/{catalog_key}",
    dependencies=[Depends(require_feature(Feature.EVENT_SIMULATOR))],
)
def toggle_catalog_entry(
    catalog_key: str,
    body: CatalogToggleRequest,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Switch every occurrence of one catalog entry on/off in a single call
    (e.g. all 24 seeded `co_quincena_15` rows).
    """
    updated = svc.set_catalog_group_active(user.tenant_id, catalog_key, body.active)
    if updated == 0:
        raise AppError(
            "calendar_event_not_found", "Catalog event not found", status_code=404,
        )
    return ok({"catalog_key": catalog_key, "active": body.active, "updated": updated})


# ── PDF executive summary ─────────────────────────────────────────────────────

@router.get("/report/pdf")
def download_pdf_report(
    session_id: str = Query(...),
    service_level: float = Query(default=0.95, ge=0.5, le=0.999),
    user: CurrentUser = Depends(get_current_user),
):
    """Generates and streams a one-page executive PDF inventory summary."""
    try:
        pdf_bytes = svc.generate_inventory_pdf(user.tenant_id, session_id, service_level)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    from datetime import date
    filename = f"inventory_{date.today().isoformat()}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── ROI tracking ─────────────────────────────────────────────────────────────

class POLineItem(BaseModel):
    sku:                  str
    display_name:         Optional[str]   = None
    supplier:            Optional[str]   = None
    # Set when the buyer picked the supplier explicitly in the cart; the name
    # above is kept for display and for historical rows that have no id.
    supplier_id:         Optional[str]   = None
    signal:               Optional[str]   = None
    recommended_qty: float           = Field(default=0, ge=0, le=_MAX_QTY)
    final_qty:       float           = Field(default=0, ge=0, le=_MAX_QTY)
    unit_cost:       Optional[float] = Field(default=None, ge=0, le=_MAX_MONEY)
    status:               str             = "approved"
    warehouse:               Optional[str]   = None


class POLogRequest(BaseModel):
    items: Optional[list[POLineItem]] = None
    # Where the goods should physically arrive. None = tenant default
    # warehouse ('principal'), which is the pre-5.4 behavior.
    destination_warehouse: Optional[str] = None


@router.post("/log-po", status_code=201)
def log_po(
    session_id: str = Query(...),
    body: Optional[POLogRequest] = None,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Called when a user downloads a PO.

    Preferred: the client sends the actual cart (`body.items`) with each line's
    buyer decision (approved / modified / rejected). This is what lets us track
    adoption ("you followed 8 of 10 recommendations").

    Fallback (no body): the server re-derives the actionable PEDIR_YA /
    PEDIR_PRONTO items — used by the legacy server-side CSV export, which has no
    per-line decisions to send.
    """
    from backend.inventory.roi_service import log_po_generation

    if body and body.items:
        po_items = [i.model_dump() for i in body.items]
    else:
        items = svc.get_inventory_status(user.tenant_id, session_id)
        po_items = [
            i for i in items
            if i["signal"] in ("PEDIR_YA", "PEDIR_PRONTO") and (i.get("recommended_qty") or 0) > 0
        ]

    record = log_po_generation(
        user.tenant_id, session_id, po_items,
        destination_warehouse=body.destination_warehouse if body else None,
    )
    return ok(record)


class ManualPOLine(BaseModel):
    sku:          str
    qty:          float = Field(gt=0, le=_MAX_QTY)
    unit_cost:    Optional[float] = Field(default=None, ge=0, le=_MAX_MONEY)
    display_name: Optional[str] = None


class ManualPORequest(BaseModel):
    supplier_id: str
    lines: list[ManualPOLine] = Field(min_length=1)
    destination_warehouse: Optional[str] = None


@router.post("/po", status_code=201)
def create_manual_po(
    body: ManualPORequest,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    A purchase order the buyer writes from scratch — supplier chosen
    explicitly, lines typed in, no forecast session behind it. Persisted with
    source='manual' so adoption metrics stay clean.
    """
    from backend.inventory.roi_service import create_manual_po as create_po_svc

    supplier = sup_svc.get_supplier(user.tenant_id, body.supplier_id)
    if not supplier:
        raise AppError("supplier_not_found", "Supplier not found", status_code=404)

    record = create_po_svc(
        user.tenant_id, supplier,
        [l.model_dump() for l in body.lines],
        destination_warehouse=body.destination_warehouse,
    )
    return ok(record)


@router.get("/roi")
def get_roi(user: CurrentUser = Depends(get_current_user)):
    """Returns accumulated ROI metrics across all time."""
    from backend.inventory.roi_service import get_roi_summary
    return ok(get_roi_summary(user.tenant_id))


@router.get("/roi/monthly")
def get_roi_monthly(
    months: int = Query(default=6, ge=1, le=24),
    user: CurrentUser = Depends(get_current_user),
):
    """Last N months: orders, stockout risks handled, adoption, capital freed from overstock."""
    from backend.inventory.roi_service import get_monthly_summary
    return ok(get_monthly_summary(user.tenant_id, months))


@router.get("/roi/month-report")
def get_roi_month_report(
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    user: CurrentUser = Depends(get_current_user),
):
    """Recap of a single calendar month (feature 3.2). Defaults to the month
    that just closed — the same period the monthly recap email covers."""
    from datetime import datetime, timezone

    from backend.inventory.roi_service import get_month_report, previous_month

    if year is None or month is None:
        year, month = previous_month(datetime.now(tz=timezone.utc))
    return ok(get_month_report(user.tenant_id, year, month))


@router.get("/po-history")
def po_history(
    limit: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
):
    """Returns recent PO generation events for the history panel."""
    from backend.inventory.roi_service import get_po_history
    return ok(get_po_history(user.tenant_id, limit))


# ── PO reception (cerrar el loop de purchase) ──────────────────────────────────

class ReceptionLine(BaseModel):
    sku: str
    received_qty: float = Field(ge=0)


class ReceptionRequest(BaseModel):
    # Omitting `lines` means "everything arrived" (each line receives its final_qty)
    lines:       Optional[list[ReceptionLine]] = None
    received_at: Optional[str] = None  # ISO date/datetime; default: ahora


@router.get("/po/{po_log_id}/items")
def po_items(po_log_id: str, user: CurrentUser = Depends(get_current_user)):
    """Lines of a PO with ordered vs received quantities (reception form)."""
    from backend.inventory import reception_service as rec_svc
    po = rec_svc.get_po(user.tenant_id, po_log_id)
    if not po:
        raise AppError("po_not_found", "Purchase order not found", status_code=404)
    return ok({
        "po_log_id": po_log_id,
        "reception_status": po.get("reception_status", "pending"),
        "generated_at": po["generated_at"].isoformat() if po.get("generated_at") else None,
        "received_at": po["received_at"].isoformat() if po.get("received_at") else None,
        "items": rec_svc.get_po_items(user.tenant_id, po_log_id),
    })


@router.post("/po/{po_log_id}/receive", status_code=200)
def receive_po(
    po_log_id: str,
    body: Optional[ReceptionRequest] = None,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Record that a PO arrived (fully, partially, or not at all).
    Side effects: current_stock increases by the received units, and Faro logs
    the supplier's REAL lead time (order date → reception date).
    """
    from datetime import datetime as _dt
    from backend.inventory import reception_service as rec_svc

    received_at = None
    if body and body.received_at:
        try:
            received_at = _dt.fromisoformat(body.received_at)
        except ValueError:
            raise AppError(
                "date_invalid_iso",
                "received_at must be an ISO date (YYYY-MM-DD)",
                params={"field": "received_at"},
            )

    lines = [l.model_dump() for l in body.lines] if (body and body.lines is not None) else None
    # receive_po raises AppError (own status_code: 404 not found / 409 already
    # received / 422 invalid input) which propagates to the AppError handler in
    # backend/main.py, carrying error_code + error_params to the client.
    result = rec_svc.receive_po(
        user.tenant_id, po_log_id, user.user_id,
        lines=lines, received_at=received_at,
    )
    return ok(result)


@router.get("/suppliers/scorecard")
def supplier_scorecard(user: CurrentUser = Depends(get_current_user)):
    """Per-supplier performance: real lead time range, on-time rate, fill rate."""
    from backend.inventory import reception_service as rec_svc
    return ok(rec_svc.get_supplier_scorecard(user.tenant_id))


@router.get("/suppliers/contact-health")
def supplier_contact_health(user: CurrentUser = Depends(get_current_user)):
    """
    Suppliers that POST /po/{id}/send would silently skip — no email and no
    WhatsApp on file, or a supplier name on PO lines with no record at all
    (feature 2.5).

    Returns BOTH relevant and dormant cases, each carrying
    `has_open_pos` / `open_pos`, rather than pre-filtering
    to "only those in open orders". Reason: the /hoy warning must also cover
    suppliers in the buyer's CURRENT CART, and the cart lives only in the
    browser until the PO is generated — the server cannot know it. Filtering
    server-side would make the cart case impossible to answer. The rule for
    what counts as incomplete stays here; each surface only chooses which of
    the flagged rows are on screen right now.
    """
    from backend.inventory import supplier_health_service as health_svc
    return ok(health_svc.get_contact_health(user.tenant_id))


@router.get("/suppliers/lead-time-alerts")
def supplier_lead_time_alerts(user: CurrentUser = Depends(get_current_user)):
    """
    Suppliers whose recent lead time is significantly slower than their own
    history — "Acme is taking 12 days, not 7" (feature 3.3).

    Significance is a robust one-sided 3-sigma SPC rule (median/MAD, with a
    practical-significance floor); see the threshold rationale in
    backend/inventory/supplier_health_service.py.
    """
    from backend.inventory import supplier_health_service as health_svc
    return ok(health_svc.get_lead_time_deviations(user.tenant_id))


@router.get("/po/overdue")
def po_overdue(user: CurrentUser = Depends(get_current_user)):
    """
    POs still pending/partial whose expected arrival — order date plus the
    supplier's already-learned lead time — has passed with no reception
    recorded. Powers the /hoy 'did it arrive?' nudge.
    """
    from backend.inventory import reception_service as rec_svc
    return ok(rec_svc.get_overdue_receptions(user.tenant_id))


# ── PO → supplier (feature 2.2) ──────────────────────────────────────────────

@router.get("/po/{po_log_id}/pdf/{supplier_slug}")
def download_po_pdf(po_log_id: str, supplier_slug: str):
    """
    Serves a generated PO PDF by (po_log_id, supplier_slug) — INTENTIONALLY
    unauthenticated. Twilio's WhatsApp MediaUrl fetch cannot carry this app's
    Bearer token, and this endpoint is the only way to deliver a PO PDF via
    WhatsApp. po_log_id is an unguessable id; this serves nothing more
    sensitive than what's already emailed to the same supplier.
    """
    from backend.storage import paths as storage_paths

    # po_log_id is not tenant-scoped here on purpose (see docstring) — we
    # don't have a tenant to scope by without auth, so we search every
    # tenant's directory for a matching file. In practice this is a single
    # glob since po_log_id is unique. po_pdf_dir("") == _base()/"pos" (an
    # empty tenant_id segment is a no-op in pathlib's `/` join), giving the
    # root directory one level ABOVE each per-tenant pos/ subdirectory —
    # do not call .parent on this, that would search one level too high.
    pos_root = storage_paths.po_pdf_dir("")
    for candidate in pos_root.glob(f"*/{po_log_id}_{supplier_slug}.pdf"):
        return FileResponse(candidate, media_type="application/pdf", filename=candidate.name)
    raise AppError("po_pdf_not_found", "Purchase order PDF not found", status_code=404)


@router.post("/po/{po_log_id}/send", status_code=200)
def send_po_to_suppliers(
    po_log_id: str,
    # Verified email required: this leaves the tenant, reaching third-party
    # suppliers by email and WhatsApp in the account's name.
    user: CurrentUser = Depends(require_verified_analyst_or_above),
):
    """
    Sends a PO's PDF to each of its suppliers by email and WhatsApp,
    grouping the PO's lines by supplier name (a PO can span more than one
    supplier). Lines with no supplier name, or whose supplier has no
    saved contact info, are skipped and reported back — never a 500.
    """
    from backend.inventory import reception_service as rec_svc
    from backend.inventory.roi_service import format_po_number
    from backend.notifications import email as email_mod
    from backend.notifications import whatsapp as wa_mod

    po = rec_svc.get_po(user.tenant_id, po_log_id)
    if not po:
        raise AppError("po_not_found", "Purchase order not found", status_code=404)

    items = rec_svc.get_po_items(user.tenant_id, po_log_id)
    ordered = [i for i in items if i["status"] in ("approved", "modified")]

    by_supplier: dict[str, list[dict]] = {}
    # Lines nobody can be reached for. These used to be dropped in silence, so
    # a typo on a SKU's supplier meant the order simply never went out with no
    # trace in the response.
    unresolved: list[dict] = []
    for i in ordered:
        name = (i.get("supplier") or "").strip()
        if not name:
            unresolved.append({"sku": i.get("sku"), "supplier": None})
            continue
        by_supplier.setdefault(name, []).append(i)

    sent: list[dict] = []
    skipped: list[dict] = []
    po_meta = {
        "generated_at": po["generated_at"].isoformat() if po.get("generated_at") else None,
        "po_log_id": po_log_id,
    }
    # One read for the whole send, not one per supplier PDF: this loop builds a
    # document per supplier and each document formats two amounts per line.
    po_currency = currency_of(user.tenant_id)

    for supplier_name, supplier_items in by_supplier.items():
        # The buyer's explicit pick wins over the free-text name on the line.
        picked_id = next(
            (i.get("supplier_id") for i in supplier_items if i.get("supplier_id")), None)
        supplier = (
            sup_svc.get_supplier(user.tenant_id, picked_id) if picked_id else None
        ) or sup_svc.get_supplier_by_name(user.tenant_id, supplier_name)

        if not supplier:
            unresolved.extend(
                {"sku": i.get("sku"), "supplier": supplier_name} for i in supplier_items)
            continue
        if not (supplier.get("email") or supplier.get("whatsapp")):
            skipped.append({"supplier": supplier_name, "reason": "no_contact_details"})
            continue

        pdf_path = po_pdf.generate_po_pdf(user.tenant_id, po_log_id, supplier_name,
                                          supplier_items, po_meta, po_currency)
        pdf_bytes = pdf_path.read_bytes()
        slug = po_pdf.slugify_supplier_name(supplier_name)

        email_ok = False
        if supplier.get("email"):
            email_ok = email_mod.send_po_to_supplier_email(
                to=supplier["email"], supplier_name=supplier_name, po_log_id=po_log_id,
                items=supplier_items, pdf_bytes=pdf_bytes, pdf_filename=pdf_path.name,
                po_ref=format_po_number(po.get("po_number"), po_log_id),
            )

        whatsapp_ok = False
        if supplier.get("whatsapp"):
            media_url = f"{settings.frontend_url}/api/v1/inventory/po/{po_log_id}/pdf/{slug}"
            text = wa_mod.build_po_supplier_text(supplier_name, po_log_id, supplier_items)
            whatsapp_ok = wa_mod.send_whatsapp(supplier["whatsapp"], text, media_url=media_url)

        if email_ok or whatsapp_ok:
            sent.append({"supplier": supplier_name, "email": email_ok, "whatsapp": whatsapp_ok})
        else:
            # We had contact details and still reached nobody (dead SMTP creds,
            # Twilio down, no transport configured at all). Reported as skipped
            # rather than sent: `sent` is what the buyer reads as "the supplier
            # has the order", and acting on that when nothing left is the
            # expensive mistake.
            skipped.append({"supplier": supplier_name, "reason": "delivery_failed"})

    # Stamp the send time once anything actually left: the payment clock starts
    # here, so the cash calendar (3.6) has no due date to compute without it.
    # Only on a real delivery — a PO that reached nobody owes nobody. Kept
    # idempotent (sent_at IS NULL) so re-sending does not push the due dates of
    # invoices the supplier already issued.
    if sent:
        rec_svc.mark_po_sent(user.tenant_id, po_log_id)

    # A PO that reached no supplier is the single most expensive silent failure
    # in the product, so the attempt is recorded against the buyer who made it.
    svc.record_notification_delivery(
        user.tenant_id, user.user_id, "po_sent_to_suppliers", bool(sent),
        context={
            "po_log_id": po_log_id,
            "delivered": [s["supplier"] for s in sent],
            "not_delivered": [s["supplier"] for s in skipped],
        },
    )

    return ok({"sent": sent, "skipped": skipped, "unresolved": unresolved})


@router.post("/po/{po_log_id}/send-to-me", status_code=200)
def send_po_to_self(
    po_log_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Deliver the order to the BUYER's own WhatsApp so they forward it to their
    supplier (PENDIENTES #1) — no Faro↔supplier integration required.

    Always returns the rendered text plus a wa.me deep link, so the flow works
    end to end even with no Twilio configured and no number on file: the UI can
    still offer "open in WhatsApp" and "copy message".
    """
    from urllib.parse import quote

    from backend.inventory import reception_service as rec_svc
    from backend.inventory.roi_service import format_po_number
    from backend.notifications import whatsapp as wa_mod
    from backend.users import service as user_svc

    po = rec_svc.get_po(user.tenant_id, po_log_id)
    if not po:
        raise AppError("po_not_found", "Purchase order not found", status_code=404)

    items = rec_svc.get_po_items(user.tenant_id, po_log_id)
    ordered = [i for i in items if i["status"] in ("approved", "modified")]

    by_supplier: dict[str, list[dict]] = {}
    for i in ordered:
        by_supplier.setdefault((i.get("supplier") or "").strip() or "—", []).append(i)
    groups = [{"supplier": name, "items": rows} for name, rows in by_supplier.items()]

    reference = format_po_number(po.get("po_number"), po_log_id)
    text = wa_mod.build_po_forward_text(reference, groups)

    # Outbound to the user's own number: an unverified number is still their
    # own contact detail, so verification is not required to receive it.
    me = user_svc.get_user(user.tenant_id, user.user_id) or {}
    number = (me.get("whatsapp_number") or "").strip()
    sent = wa_mod.send_whatsapp(number, text) if number else False

    return ok({
        "sent": sent,
        "has_number": bool(number),
        "message_text": text,
        "wa_me_url": f"https://wa.me/?text={quote(text)}",
    })


# ── Supplier price breaks (feature 3.5) ──────────────────────────────────────

class PriceBreakUpsert(BaseModel):
    sku:        str   = Field(min_length=1)
    min_qty:    float = Field(gt=0)
    unit_price: float = Field(ge=0)
    notes:      Optional[str] = None


class PriceBreakCartLine(BaseModel):
    sku:      str
    quantity: float = Field(ge=0)


class PriceBreakEvaluateRequest(BaseModel):
    items: list[PriceBreakCartLine] = Field(default_factory=list)


@router.get("/price-breaks")
def list_price_breaks(
    supplier_id: Optional[str] = Query(default=None),
    sku:         Optional[str] = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
):
    """Supplier quantity scales, optionally filtered by supplier and/or SKU."""
    return ok(pb_svc.list_price_breaks(user.tenant_id, supplier_id, sku))


@router.post("/suppliers/{supplier_id}/price-breaks", status_code=201)
def upsert_price_break(
    supplier_id: str,
    body: PriceBreakUpsert,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    supplier = sup_svc.get_supplier(user.tenant_id, supplier_id)
    if not supplier:
        raise AppError("supplier_not_found", "Supplier not found", status_code=404)
    row = pb_svc.upsert_price_break(
        user.tenant_id, supplier_id, body.sku,
        body.min_qty, body.unit_price, body.notes,
    )
    return ok(row)


@router.delete("/price-breaks/{price_break_id}", status_code=204)
def delete_price_break(
    price_break_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    existing = pb_svc.get_price_break(user.tenant_id, price_break_id)
    if not existing:
        raise AppError("price_break_not_found", "Price break not found", status_code=404)
    pb_svc.delete_price_break(user.tenant_id, price_break_id)


@router.post("/price-breaks/evaluate")
def evaluate_price_breaks(
    session_id: str = Query(...),
    body: Optional[PriceBreakEvaluateRequest] = None,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Given the cart the buyer currently has on screen, which lines are one step
    away from a better unit price AND would still be a good idea to step up to.

    POST with a body rather than GET because the cart lives only in the browser
    until a PO is generated — the same reason /suppliers/contact-health cannot
    filter server-side. Non-mutating, so viewers may call it.

    Falls back to the session's own recommended quantities when no cart is sent,
    which is what the daily briefing surface needs.
    """
    status_items = svc.get_inventory_status(user.tenant_id, session_id)

    if body and body.items:
        cart = [{"sku": i.sku, "quantity": i.quantity} for i in body.items]
    else:
        cart = [
            {"sku": i["sku"], "quantity": i.get("recommended_qty") or 0}
            for i in status_items
            if (i.get("recommended_qty") or 0) > 0
        ]

    from backend.db import session_store
    business_cfg = session_store.get_field(user.tenant_id, session_id, "business_cfg") or {}
    holding_cost_pct = float(business_cfg.get("holding_cost_pct", pb_svc.DEFAULT_HOLDING_COST_PCT))

    opportunities = pb_svc.evaluate_cart(
        user.tenant_id, cart, status_items, holding_cost_pct,
    )
    return ok({
        "opportunities": opportunities,
        "worth_it_count": sum(1 for o in opportunities if o["worth_it"]),
        "total_net_saving": round(
            sum(o["net_saving"] for o in opportunities if o["worth_it"]), 2,
        ),
        "holding_cost_pct": holding_cost_pct,
    })


# ── Cash calendar / accounts payable (feature 3.6) ───────────────────────────

class CashFitLine(BaseModel):
    sku:           Optional[str]   = None
    supplier_name: Optional[str]   = None
    quantity:      float           = Field(default=0, ge=0)
    unit_cost:     Optional[float] = Field(default=None, ge=0)


class CashFitRequest(BaseModel):
    items:  list[CashFitLine] = Field(default_factory=list)
    budget: Optional[float]   = Field(default=None, ge=0)


@router.get("/cash-calendar")
def cash_calendar(
    horizon_days: int = Query(default=30, ge=1, le=180),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Invoices falling due from POs already sent, dated by each supplier's credit
    terms. Read-only, so viewers may call it.
    """
    return ok(cash_service.get_payables(user.tenant_id, horizon_days))


@router.post("/cash-calendar/fit")
def cash_calendar_fit(
    horizon_days: int = Query(default=30, ge=1, le=180),
    session_id:   Optional[str] = Query(default=None),
    body: Optional[CashFitRequest] = None,
    user: CurrentUser = Depends(get_current_user),
):
    """
    "Does the recommended purchase fit in the cash I have?"

    The purchase under test comes from the cart the client sends. When no cart
    is sent and a `session_id` is given, it is taken from the MILP budget
    optimizer (/inventory/optimize) instead — that is the cross the plan asks
    for: the optimizer says what to buy at minimum cost, this says whether the
    business can pay for it in the window it lands in.
    """
    budget = body.budget if body else None

    if body and body.items:
        lines = [
            {
                "sku": i.sku,
                "supplier_name": i.supplier_name,
                "quantity": i.quantity,
                "unit_cost": i.unit_cost,
            }
            for i in body.items
        ]
    elif session_id:
        from forecasting_core.business.optimizer import optimize

        inp = opt_svc.build_optimization_input(user.tenant_id, session_id, 30)
        if inp is None:
            lines = []
        else:
            # The SAME gate `/inventory/optimize` uses. This solve was outside
            # it, which made the gate's cap a fiction: two purchasing panels
            # take both slots, this endpoint adds a third solve, and measured
            # locally three concurrent HiGHS solves stop making progress
            # altogether — the process wedges rather than erroring, so the whole
            # backend goes unresponsive instead of returning a 503. Two buyers
            # refreshing while a third opens the cash calendar is enough.
            try:
                with opt_svc.solve_slot():
                    result = optimize(inp)
            except opt_svc.OptimizerBusy:
                raise AppError(
                    "optimizer_busy",
                    "Optimizer busy (too many concurrent requests); please retry.",
                    status_code=503,
                )
            stock_rows = svc.list_stock(user.tenant_id)
            serialized = opt_svc.serialize_optimization_result(inp, result, stock_rows)
            lines = [
                {
                    "sku": o["sku"],
                    "supplier_name": o.get("supplier"),
                    "quantity": o["qty"],
                    "unit_cost": o.get("unit_cost"),
                }
                for o in serialized["orders"]
            ]
    else:
        lines = []

    return ok(cash_service.evaluate_purchase_fit(
        user.tenant_id, lines, budget, horizon_days,
    ))


# ── Suppliers ─────────────────────────────────────────────────────────────────

# A supplier's email is the address purchase orders are sent to. It was accepted
# as any string at all: `no-es-un-email` saved cleanly, showed in the EMAIL
# column of the suppliers table like a configured address, and stayed wrong
# until the day an order failed to arrive. The send path does report that
# (`skipped: delivery_failed`), so nothing is lost silently — but the user finds
# out after the order was supposed to have gone, which is the wrong moment.
#
# Deliberately a shape check, not a deliverability check: the only thing we can
# know at save time is whether an address could ever be routed. `pydantic`'s
# EmailStr would need the `email-validator` dependency and would also reject
# addresses that are unusual but legal; this refuses what is certainly
# unreachable and leaves the rest to the send, which already reports its result.
_EMAIL_SHAPE = re.compile(r"^[^@\s,;]+@[^@\s,;.]+(\.[^@\s,;.]+)+$")


def _validated_email(value: Optional[str]) -> Optional[str]:
    """None/blank stay None — not every supplier is contacted by email."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if not _EMAIL_SHAPE.match(text):
        raise PydanticCustomError(
            "supplier_email_shape",
            "'{email}' cannot receive mail — a purchase order sent there would "
            "never arrive. Use an address like nombre@empresa.com.",
            {"email": text[:64]},
        )
    return text


class SupplierCreate(BaseModel):
    name:           str
    email:          Optional[str] = None
    phone:          Optional[str] = None
    whatsapp:       Optional[str] = None
    lead_time_days: int   = Field(default=15, ge=1, le=365)
    lead_time_std:  int   = Field(default=3, ge=0, le=60)
    payment_terms:  Optional[str] = None
    # Structured credit days (feature 3.6). Optional: when omitted it is derived
    # from the free-text `payment_terms`, so existing clients keep working and
    # the user never has to type the same thing twice. An explicit value always
    # wins over the parser — the user correcting a bad parse must stick.
    payment_terms_days: Optional[int] = Field(default=None, ge=0, le=365)
    notes:          Optional[str] = None

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: Optional[str]) -> Optional[str]:
        return _validated_email(value)


class SupplierPatch(BaseModel):
    name:           Optional[str]   = None
    email:          Optional[str]   = None
    phone:          Optional[str]   = None
    whatsapp:       Optional[str]   = None
    lead_time_days: Optional[int]   = Field(default=None, ge=1, le=365)
    lead_time_std:  Optional[int]   = Field(default=None, ge=0, le=60)
    payment_terms:  Optional[str]   = None
    payment_terms_days: Optional[int] = Field(default=None, ge=0, le=365)
    notes:          Optional[str]   = None

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: Optional[str]) -> Optional[str]:
        return _validated_email(value)


class SkuSupplierUpsert(BaseModel):
    is_primary:     bool  = True
    unit_cost:      Optional[float] = None
    moq:            float = Field(default=1, ge=1)
    lead_time_days: Optional[int]   = Field(default=None, ge=1, le=365)
    notes:          Optional[str]   = None


@router.get("/suppliers")
def list_suppliers(user: CurrentUser = Depends(get_current_user)):
    return ok(sup_svc.list_suppliers(user.tenant_id))


def _with_derived_credit_days(data: dict) -> dict:
    """
    Fills `payment_terms_days` from the free-text `payment_terms` when the
    client did not send it explicitly (feature 3.6). Unparseable text leaves the
    field absent rather than guessing a number — see cash_service.
    """
    if data.get("payment_terms_days") is None and data.get("payment_terms"):
        parsed = cash_service.parse_payment_terms_days(data["payment_terms"])
        if parsed is not None:
            data["payment_terms_days"] = parsed
    return data


@router.post("/suppliers", status_code=201)
def create_supplier(body: SupplierCreate, user: CurrentUser = Depends(require_analyst_or_above)):
    data = _with_derived_credit_days(body.model_dump(exclude_none=True))
    supplier = sup_svc.create_supplier(user.tenant_id, data)
    return ok(supplier)


@router.patch("/suppliers/{supplier_id}")
def update_supplier(
    supplier_id: str,
    body: SupplierPatch,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    data = _with_derived_credit_days(body.model_dump(exclude_none=True))
    supplier = sup_svc.update_supplier(user.tenant_id, supplier_id, data)
    if not supplier:
        raise AppError("supplier_not_found", "Supplier not found", status_code=404)
    return ok(supplier)


@router.delete("/suppliers/{supplier_id}", status_code=204)
def delete_supplier(supplier_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    existing = sup_svc.get_supplier(user.tenant_id, supplier_id)
    if not existing:
        raise AppError("supplier_not_found", "Supplier not found", status_code=404)
    sup_svc.delete_supplier(user.tenant_id, supplier_id)


# ── Warehouses ────────────────────────────────────────────────────────────────

class WarehouseCreate(BaseModel):
    name:       str = Field(min_length=1)
    is_default: bool = False


@router.get("/warehouses", dependencies=[Depends(require_feature(Feature.MULTI_LOCATION))])
def list_warehouses(user: CurrentUser = Depends(get_current_user)):
    return ok(wh_svc.list_warehouses(user.tenant_id))


@router.post(
    "/warehouses", status_code=201,
    dependencies=[Depends(require_feature(Feature.MULTI_LOCATION))],
)
def create_warehouse(body: WarehouseCreate, user: CurrentUser = Depends(require_analyst_or_above)):
    if not (body.name or "").strip():
        raise AppError(
            "warehouse_name_required", "Warehouse name is required", status_code=422,
        )
    # Resolve first so a case-variant of an existing warehouse ('norte' vs
    # 'Norte') is treated as the idempotent re-create it is, not a new
    # location for the max_locations pre-check.
    name = wh_svc.resolve_canonical_name(user.tenant_id, body.name)
    if not wh_svc.get_warehouse_by_name(user.tenant_id, name):
        from backend.entitlements.service import enforce_limit
        enforce_limit(user.tenant_id, "max_locations", wh_svc.count_warehouses(user.tenant_id))
    warehouse = wh_svc.create_warehouse(user.tenant_id, name, is_default=body.is_default)
    return ok(warehouse)


class WarehousePatch(BaseModel):
    demand_share: Optional[float] = Field(default=None, ge=0, le=100)


@router.patch(
    "/warehouses/{name}",
    dependencies=[Depends(require_feature(Feature.MULTI_LOCATION))],
)
def patch_warehouse(
    name: str,
    body: WarehousePatch,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Set or clear the manual demand share for one warehouse (feature 5.4)."""
    try:
        row = wh_svc.set_demand_share(user.tenant_id, name, body.demand_share)
    except ValueError as e:
        raise _svc_error(e)
    return ok(row)


# ── Transfer lanes: time + money per (from, to) pair (PENDIENTES #2) ─────────

class TransferLaneUpsert(BaseModel):
    from_warehouse: str = Field(min_length=1)
    to_warehouse:   str = Field(min_length=1)
    lead_time_days: int = Field(ge=0, le=365)
    cost_per_unit:  float = Field(default=0.0, ge=0)
    fixed_cost:     float = Field(default=0.0, ge=0)


@router.get(
    "/warehouses/lanes",
    dependencies=[Depends(require_feature(Feature.MULTI_LOCATION))],
)
def list_transfer_lanes(user: CurrentUser = Depends(get_current_user)):
    """Configured lanes only. A pair with no row falls back to the documented
    default (lead_time_days=1, cost_per_unit=0, fixed_cost=0) everywhere it is
    consumed — see backend/inventory/transfer_lane_service.py."""
    return ok(lane_svc.list_lanes(user.tenant_id))


@router.put(
    "/warehouses/lanes",
    dependencies=[Depends(require_feature(Feature.MULTI_LOCATION))],
)
def upsert_transfer_lane(
    body: TransferLaneUpsert,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    try:
        row = lane_svc.upsert_lane(
            user.tenant_id, body.from_warehouse, body.to_warehouse,
            body.lead_time_days, body.cost_per_unit, body.fixed_cost)
    except ValueError as e:
        raise _svc_error(e)
    return ok(row)


@router.delete(
    "/warehouses/lanes", status_code=204,
    dependencies=[Depends(require_feature(Feature.MULTI_LOCATION))],
)
def delete_transfer_lane(
    from_warehouse: str = Query(min_length=1),
    to_warehouse: str = Query(min_length=1),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Names travel as query params, not path segments: a warehouse name may
    contain a slash and would break path matching once encoded."""
    if not lane_svc.delete_lane(user.tenant_id, from_warehouse, to_warehouse):
        raise AppError(
            "transfer_lane_not_found", "Transfer lane not found", status_code=404,
        )


@router.get("/stock/{sku}/suppliers")
def get_sku_suppliers(sku: str, user: CurrentUser = Depends(get_current_user)):
    return ok(sup_svc.get_sku_suppliers(user.tenant_id, sku))


# ── Inter-warehouse transfers (feature 5.4) ──────────────────────────────────

class TransferItemIn(BaseModel):
    sku: str
    qty: float = Field(gt=0)


class TransferCreate(BaseModel):
    from_warehouse: str
    to_warehouse: str
    items: list[TransferItemIn]
    notes: Optional[str] = Field(default=None, max_length=2000)


class TransferReceive(BaseModel):
    lines: Optional[list[dict]] = None  # [{sku, received_qty}] | null = all


def _svc_error(e: ValueError) -> Exception:
    """Service-layer ValueError → HTTP: 'not found' wording means 404, the
    rest is a rejected request. Shared by the transfer and warehouse routes.

    An ``AppError`` is already a ValueError that carries its own status, code
    and params — hand it straight back, otherwise wrapping it here would throw
    away the machine code and leave the user reading English prose.
    """
    if isinstance(e, AppError):
        return e
    msg = str(e)
    return HTTPException(status_code=404 if "not found" in msg.lower() else 422,
                         detail=msg)


@router.post(
    "/transfers", status_code=201,
    dependencies=[Depends(require_feature(Feature.MULTI_LOCATION))],
)
def create_transfer(
    body: TransferCreate,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    try:
        t = tr_svc.create_transfer(
            user.tenant_id, user.user_id, body.from_warehouse, body.to_warehouse,
            [i.model_dump() for i in body.items], body.notes)
    except ValueError as e:
        raise _svc_error(e)
    return ok(t)


@router.get(
    "/transfers",
    dependencies=[Depends(require_feature(Feature.MULTI_LOCATION))],
)
def list_transfers(
    status: Optional[str] = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
):
    return ok(tr_svc.list_transfers(user.tenant_id, status))


@router.post(
    "/transfers/{transfer_id}/receive",
    dependencies=[Depends(require_feature(Feature.MULTI_LOCATION))],
)
def receive_transfer(
    transfer_id: str,
    body: TransferReceive,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    try:
        t = tr_svc.receive_transfer(user.tenant_id, transfer_id, body.lines)
    except ValueError as e:
        raise _svc_error(e)
    return ok(t)


@router.post(
    "/transfers/{transfer_id}/cancel",
    dependencies=[Depends(require_feature(Feature.MULTI_LOCATION))],
)
def cancel_transfer(
    transfer_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    try:
        t = tr_svc.cancel_transfer(user.tenant_id, transfer_id)
    except ValueError as e:
        raise _svc_error(e)
    return ok(t)


@router.post(
    "/transfers/{transfer_id}/close",
    dependencies=[Depends(require_feature(Feature.MULTI_LOCATION))],
)
def close_transfer(
    transfer_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Close a partial transfer, writing the missing units off as shrinkage."""
    try:
        t = tr_svc.close_transfer(user.tenant_id, transfer_id, user.user_id)
    except ValueError as e:
        raise _svc_error(e)
    return ok(t)


@router.put("/stock/{sku}/suppliers/{supplier_id}")
def assign_sku_supplier(
    sku: str,
    supplier_id: str,
    body: SkuSupplierUpsert,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    # Verify supplier exists for this tenant
    supplier = sup_svc.get_supplier(user.tenant_id, supplier_id)
    if not supplier:
        raise AppError("supplier_not_found", "Supplier not found", status_code=404)
    link = sup_svc.upsert_sku_supplier(user.tenant_id, sku, supplier_id, body.model_dump(exclude_none=True))
    return ok(link)


@router.delete("/stock/{sku}/suppliers/{supplier_id}", status_code=204)
def remove_sku_supplier(
    sku: str,
    supplier_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    sup_svc.remove_sku_supplier(user.tenant_id, sku, supplier_id)


# ── Alert test-fire ───────────────────────────────────────────────────────────

@router.post("/alerts/send-now", status_code=202)
def send_alert_now(
    session_id: str = Query(...),
    # Verified email required: fires real email + WhatsApp to the tenant's
    # contacts.
    user: CurrentUser = Depends(require_verified_analyst_or_above),
):
    """
    Fire the daily inventory alert immediately for this tenant (email to all
    plans + WhatsApp to opted-in admins on Professional+). Lets the user
    verify their channels without waiting for the 8:00 UTC scheduler run.

    Email is core to every plan and always test-fired here. WhatsApp is a
    Professional+ feature (Feature.WHATSAPP_ALERTS) — only that slice is
    gated, mirroring the daily loop in backend/inventory/service.py's
    run_daily_inventory_alerts(). The endpoint itself must never 403 a
    Starter tenant out of its core email alert.
    """
    items = svc.get_inventory_status(user.tenant_id, session_id)
    critical = [i for i in items if i["signal"] == "PEDIR_YA"]
    warning  = [i for i in items if i["signal"] == "PEDIR_PRONTO"]
    if not critical and not warning:
        return ok({"sent": False, "reason": "No hay SKUs en riesgo — nada que alertar."})

    from backend.config import settings as _settings
    from backend.notifications.email import send_inventory_alert_email

    inventory_url = f"{_settings.frontend_url}/inventory"
    # Full lists: the email renderer trims the table itself and keeps the
    # counts real, so a test fire shows the same numbers the daily loop would.
    emails = svc.get_tenant_admin_emails(user.tenant_id)
    emails_sent = sum(
        1 for email in emails
        if send_inventory_alert_email(
            to=email, critical_items=critical, warning_items=warning,
            inventory_url=inventory_url,
        )
    )

    wa_sent = 0
    tenant = get_tenant(user.tenant_id) or {}
    if has_feature(tenant, Feature.WHATSAPP_ALERTS):
        from backend.notifications.whatsapp import build_inventory_alert_text, send_whatsapp

        numbers = svc.get_tenant_admin_whatsapps(user.tenant_id)
        if numbers:
            text = build_inventory_alert_text(critical, warning, inventory_url)
            wa_sent = sum(1 for n in numbers if send_whatsapp(n, text))

    # The point of a test fire is to prove the channel works, so its outcome is
    # recorded like a real send instead of only being echoed in the response.
    svc.record_notification_delivery(
        user.tenant_id, user.user_id, "inventory_alert_test_fire",
        bool(emails_sent or wa_sent),
        context={
            "critical": len(critical), "warning": len(warning),
            "emails_attempted": len(emails), "emails_sent": emails_sent,
            "whatsapp_sent": wa_sent,
        },
    )

    return ok({
        "sent": bool(emails_sent or wa_sent),
        "critical": len(critical),
        "warning": len(warning),
        "emails_attempted": len(emails),
        "emails_sent": emails_sent,
        "whatsapp_sent": wa_sent,
    })


# ── Morning Briefing ──────────────────────────────────────────────────────────

@router.get("/morning-briefing")
def morning_briefing(
    session_id: Optional[str] = Query(
        default=None,
        description="Completed forecast session; defaults to the tenant's active-period session"),
    service_level: float = Query(default=0.95, ge=0.5, le=0.999),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Daily operations briefing: risks, recommendations, demand changes, KPIs.
    Designed to be the first thing a manager opens every morning. Reflects the
    tenant's ACTIVE planning period — coverage and the signal in that unit —
    so /hoy agrees with /inventory (a weekly session must be read as weekly).
    """
    if not session_id:
        session_id = planning_service.resolve_active_session(user.tenant_id)
        if not session_id:
            raise AppError(
                "no_completed_session",
                "No completed session for this tenant yet",
                status_code=400,
            )
    period = planning_service.get_planning(user.tenant_id).get("period", "daily")
    data = svc.get_morning_briefing(user.tenant_id, session_id, service_level, period)
    return ok(data)


# ── Product Types ─────────────────────────────────────────────────────────────

@router.get("/product-types")
def list_product_types(user: CurrentUser = Depends(get_current_user)):
    """Returns the list of valid product types and their labels."""
    return ok(bom_svc.PRODUCT_TYPES)


@router.patch("/stock/{sku}/product-type")
def set_product_type(
    sku: str,
    product_type: str = Query(..., description="finished_good | semi_finished | component | raw_material | packaging | service"),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    existing = svc.get_stock(user.tenant_id, sku)
    if not existing:
        raise AppError(
            "stock_sku_not_found", f"SKU '{sku}' not found in inventory",
            status_code=404, params={"sku": sku},
        )
    if product_type not in bom_svc.PRODUCT_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid product_type. Options: {list(bom_svc.PRODUCT_TYPES)}")
    from backend.db.connection import execute as db_execute
    db_execute(
        "UPDATE inventory_stock SET product_type=%s, updated_at=NOW() WHERE tenant_id=%s AND sku=%s",
        (product_type, user.tenant_id, sku),
    )
    return ok(svc.get_stock(user.tenant_id, sku))


# ── BOM ───────────────────────────────────────────────────────────────────────

class BomItemUpsert(BaseModel):
    quantity: float = Field(gt=0)
    unit:     Optional[str] = None
    notes:    Optional[str] = None


@router.get("/bom/{parent_sku}", dependencies=[Depends(require_feature(Feature.BOM))])
def get_bom(parent_sku: str, user: CurrentUser = Depends(get_current_user)):
    """Returns BOM (Bill of Materials) for a finished good."""
    return ok(bom_svc.list_bom(user.tenant_id, parent_sku))


@router.put(
    "/bom/{parent_sku}/{child_sku}", status_code=200,
    dependencies=[Depends(require_feature(Feature.BOM))],
)
def upsert_bom_item(
    parent_sku: str,
    child_sku:  str,
    body:       BomItemUpsert,
    user:       CurrentUser = Depends(require_analyst_or_above),
):
    try:
        item = bom_svc.upsert_bom_item(
            user.tenant_id, parent_sku, child_sku, body.model_dump(exclude_none=True)
        )
        return ok(item)
    except AppError:
        # Already carries its own code/params — wrapping it would strip them.
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete(
    "/bom/{parent_sku}/{child_sku}", status_code=204,
    dependencies=[Depends(require_feature(Feature.BOM))],
)
def delete_bom_item(
    parent_sku: str,
    child_sku:  str,
    user:       CurrentUser = Depends(require_analyst_or_above),
):
    bom_svc.delete_bom_item(user.tenant_id, parent_sku, child_sku)


@router.get("/bom/{child_sku}/used-in", dependencies=[Depends(require_feature(Feature.BOM))])
def where_used(child_sku: str, user: CurrentUser = Depends(get_current_user)):
    """Returns all finished goods that use this component."""
    return ok(bom_svc.get_parents_using(user.tenant_id, child_sku))


# ── Production Requirements (MRP Level 1) ────────────────────────────────────

@router.get("/production-requirements")
def production_requirements(
    session_id:   str   = Query(...),
    horizon_days: int   = Query(default=30, ge=7, le=180),
    user:         CurrentUser = Depends(get_current_user),
):
    """
    MRP Level 1 explosion: given forecast demand + BOM,
    returns required quantities of each component and raw material,
    flagging shortages and purchase requirements.
    """
    result = bom_svc.explode_requirements(user.tenant_id, session_id, horizon_days)
    return ok(result)


# ── Dead stock / Inventario inmovilizado ─────────────────────────────────────

@router.get("/dead-stock")
def dead_stock(
    session_id: str = Query(...),
    min_days_static: int = Query(default=30, ge=7, le=365,
        description="Minimum days without significant stock depletion to flag as dead"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Returns inventory items that have had little or no stock movement
    for at least min_days_static days — 'dead' or 'slow-moving' inventory.
    Capital trapped = current_stock × unit_cost.
    """
    from backend.inventory.service import get_inventory_status, get_stock_history

    items = get_inventory_status(user.tenant_id, session_id)
    dead_items = []

    for item in items:
        if not item.get('has_stock') or not item.get('current_stock'):
            continue

        # Get stock history to detect if stock has barely moved
        history = get_stock_history(user.tenant_id, item['sku'], days=min_days_static)

        if len(history) < 2:
            # No history → can't determine movement, skip
            continue

        first_stock = history[0]['stock']
        last_stock  = history[-1]['stock']
        depletion   = first_stock - last_stock

        # If stock increased (restocking during period), skip — not dead stock
        if depletion < 0:
            continue

        # Expected depletion based on forecast
        avg_daily = item.get('daily_demand') or 0
        expected  = avg_daily * len(history)

        # Classify as dead if actual depletion is < 20% of expected
        if expected > 0 and depletion < expected * 0.20:
            days_static = len(history)
            capital = round(float(item.get('current_stock', 0)) * float(item.get('unit_cost') or 0), 2)
            holding_cost_annual = capital * 0.25  # 25% annual holding cost estimate
            holding_cost_monthly = round(holding_cost_annual / 12, 2)

            dead_items.append({
                'sku':              item['sku'],
                'display_name':     item.get('display_name'),
                'supplier':        item.get('supplier'),
                'current_stock':     item.get('current_stock'),
                'unit_cost':   item.get('unit_cost'),
                'capital_trapped':  capital,
                'holding_cost_monthly': holding_cost_monthly,
                'days_without_movement': days_static,
                'depletion_pct':    round(depletion / first_stock * 100, 1) if first_stock > 0 else 0,
                'avg_daily_demand': round(avg_daily, 2),
                'signal':           item.get('signal'),
                'abc':              item.get('abc', '?'),
                # Code + English fallback: the frontend renders
                # `inventory.dead_action_<code>`. This was Spanish prose in the
                # payload, printed verbatim, so the dead-stock table read
                # "Devolver al proveedor" with the rest of the page in English.
                'action_suggested_code': (
                    'return_to_supplier' if item.get('abc') == 'C' else
                    'offer_discount' if item.get('abc') == 'B' else
                    'review_with_sales'
                ),
                'action_suggested': (
                    'Return to the supplier' if item.get('abc') == 'C' else
                    'Offer a discount' if item.get('abc') == 'B' else
                    'Review with sales'
                ),
            })

    dead_items.sort(key=lambda x: x['capital_trapped'], reverse=True)
    dead_items = _strip_abc_xyz_unless_entitled(
        dead_items, user.tenant_id,
        extra_keys=("action_suggested", "action_suggested_code"),
    )

    total_capital = sum(d['capital_trapped'] for d in dead_items)
    total_holding = sum(d['holding_cost_monthly'] for d in dead_items)

    return ok({
        'items':                dead_items,
        'total_capital_trapped': round(total_capital, 2),
        'total_holding_cost_monthly': round(total_holding, 2),
        'sku_count':            len(dead_items),
        'min_days_static':      min_days_static,
    })


# ── Export PO as CSV ───────────────────────────────────────────────────────────

@router.get("/status/export-po")
def export_po(
    session_id: str = Query(...),
    service_level: float = Query(default=0.95, ge=0.5, le=0.999),
    signals: str = Query(default="PEDIR_YA,PEDIR_PRONTO", description="Comma-separated signals to include"),
    user: CurrentUser = Depends(get_current_user),
):
    """Export purchase order as CSV, filtered to actionable SKUs."""
    include_signals = {s.strip().upper() for s in signals.split(",")}
    items = svc.get_inventory_status(user.tenant_id, session_id, service_level)
    po_items = [i for i in items if i["signal"] in include_signals and (i.get("recommended_qty") or 0) > 0]

    output = io.StringIO()
    writer = csv.writer(output)
    # The file is opened in Excel, never rendered by the frontend, so its Spanish
    # headers come from the backend copy catalog keyed in English.
    from backend.notifications.locale import render_es
    writer.writerow([
        render_es("inventory_csv_col_sku"),
        render_es("inventory_csv_col_name"),
        render_es("inventory_csv_col_supplier"),
        render_es("inventory_csv_col_signal"),
        render_es("inventory_csv_col_stock"),
        render_es("inventory_csv_col_coverage"),
        render_es("inventory_csv_col_lead_demand"),
        render_es("inventory_csv_col_lead_time"),
        render_es("inventory_csv_col_lead_source"),
        render_es("inventory_csv_col_recommended"),
        render_es("inventory_csv_col_moq"),
        render_es("inventory_csv_col_unit_cost"),
        render_es("inventory_csv_col_order_value"),
    ])
    for i in po_items:
        qty   = i.get("recommended_qty") or 0
        cost  = i.get("unit_cost")
        value = round(qty * cost, 2) if cost else ""
        # Label where the lead time came from so the buyer can trust (or
        # question) it — same distinction the /hoy and /inventory screens show.
        lead_origin = render_es("inventory_csv_lead_source_learned"
                                if i.get("lead_time_source") == "learned"
                                else "inventory_csv_lead_source_declared")
        writer.writerow([
            # Neutralize the user-controlled text cells against CSV formula
            # injection — these can carry `=`/`+`/`@` from an imported catalog
            # or an accounting-integration supplier name.
            csv_safe(i["sku"]),
            csv_safe(i.get("display_name") or ""),
            csv_safe(i.get("supplier") or ""),
            i["signal"],
            i.get("current_stock") if i.get("current_stock") is not None else "",
            i.get("coverage_days") if i.get("coverage_days") is not None else "",
            i.get("lead_time_demand") or "",
            i.get("lead_time_days") if i.get("lead_time_days") is not None else "",
            lead_origin,
            qty,
            i.get("moq") or 1,
            cost or "",
            value,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=purchase_order.csv"},
    )


# ── MILP purchasing/transfers optimizer (MW-3) ───────────────────────────────

@router.get("/optimize", dependencies=[Depends(require_feature(Feature.MILP_OPTIMIZER))])
def optimize_inventory(
    session_id:   Optional[str] = Query(default=None),
    # Cap raised from 30 to 360 (multi-period Phase C): a monthly horizon of 12
    # buckets is 12*30 = 360 days. When omitted, horizon_days is derived from
    # the tenant's active (period, horizon): horizon * days_per_period.
    horizon_days: Optional[int] = Query(default=None, ge=1, le=360),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Runs the MILP purchasing/transfers optimizer for this session and
    returns suggested purchase quantities per SKU x warehouse, plus
    recommended inter-warehouse transfers, collapsed to one total per
    line over the full horizon.
    """
    from forecasting_core.business.optimizer import optimize

    plan = planning_service.get_planning(user.tenant_id)
    period = plan.get("period", "daily")
    if not session_id:
        session_id = planning_service.resolve_active_session(user.tenant_id)
        if not session_id:
            raise AppError(
                "no_completed_session",
                "No completed session for this tenant yet",
                status_code=400,
            )
    if horizon_days is None:
        horizon_days = int(plan.get("horizon", 14)) * svc._days_per_period(period)
        horizon_days = max(1, min(horizon_days, 360))

    # Read the inventory snapshot once and thread it through both build and
    # serialize — the endpoint used to call list_stock twice (once inside
    # build_optimization_input, once here), doubling this path's pooled-
    # connection checkouts for no benefit.
    try:
        stock_rows = svc.list_stock(user.tenant_id)
        inp = opt_svc.build_optimization_input(
            user.tenant_id, session_id, horizon_days, stock_rows=stock_rows, period=period,
        )
    except PoolError:
        # The DB pool (ThreadedConnectionPool, max=10) raises rather than
        # blocking once every connection is checked out, so a concurrent burst
        # can momentarily starve this request. That is transient and retryable,
        # not a server bug — surface 503 (retry) instead of a bare 500.
        raise AppError(
            "optimizer_unavailable",
            "Optimizer temporarily unavailable (database busy); please retry.",
            status_code=503,
        )

    # SKUs the optimizer refused to decide for: their stock is unknown, and how
    # much to buy is a function of how much is left. They travel with every
    # response — including the empty one — because "no suggestions" and "no
    # suggestions BECAUSE nobody has told us what is on the shelf" look
    # identical on screen, and only one of them is the user's to fix.
    from backend.db import session_store
    forecasts = session_store.get_forecasts(user.tenant_id, session_id) or {}
    needs_stock = opt_svc.skus_missing_stock(forecasts, stock_rows)

    if inp is None:
        return ok({
            "status": "optimal", "total_cost": 0.0, "horizon_days": horizon_days,
            "orders": [], "transfers": [], "needs_stock": needs_stock,
        })

    # optimize() never raises on structurally-valid-but-degenerate input
    # (infeasible/unbounded/oversized LP all degrade to a "fallback" result),
    # so a genuine 500 here would only come from an unexpected programming
    # error, which should stay a 500 rather than be masked. The solve runs
    # inside a bounded concurrency gate so a request burst can't occupy every
    # thread-pool worker and wedge the server — excess requests get a fast 503.
    try:
        with opt_svc.solve_slot():
            result = optimize(inp)
    except opt_svc.OptimizerBusy:
        raise AppError(
            "optimizer_busy",
            "Optimizer busy (too many concurrent requests); please retry.",
            status_code=503,
        )
    return ok({
        **opt_svc.serialize_optimization_result(inp, result, stock_rows),
        "needs_stock": needs_stock,
    })
