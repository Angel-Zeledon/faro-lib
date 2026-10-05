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
from datetime import date, datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request, Response, UploadFile, File

from backend import audit
from fastapi.responses import StreamingResponse, FileResponse
from psycopg2.pool import PoolError
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from backend.activity.events import record_event
from backend.api.v1.currency import currency_of
from backend.auth.guards import (
    CurrentUser, get_current_user, require_analyst_or_above,
    require_verified_analyst_or_above,
)
from backend.auth import warehouse_scope as wscope
from backend.config import settings
from backend.errors import AppError
from backend.sessions import planning_service
from backend.inventory import service as svc
from backend.inventory import status_snapshot
from backend.inventory import supplier_service as sup_svc
from backend.inventory import bom_service as bom_svc
from backend.inventory import warehouse_service as wh_svc
from backend.inventory import optimizer_service as opt_svc
from backend.inventory import po_pdf
from backend.inventory import transfer_service as tr_svc
from backend.inventory import transfer_lane_service as lane_svc
from backend.inventory import price_break_service as pb_svc
from backend.inventory import cash_service
from backend.inventory import dead_capital as dead_capital_svc
from backend.inventory import cost_alerts as cost_alerts_svc
from backend.inventory import forecast_money as forecast_money_svc
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
    return ok(wscope.filter_rows(user, svc.list_stock(user.tenant_id)))


@router.get("/stock/page")
def list_stock_page(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    q: Optional[str] = Query(default=None, max_length=100,
                             description="Matches SKU, category or supplier"),
    warehouse: Optional[str] = Query(default=None, max_length=100),
    user: CurrentUser = Depends(get_current_user),
):
    """Stock rows searched, filtered by warehouse and paged on the server. The
    plain `/stock` list stays whole for the screens that need every row."""
    if wscope.is_scoped(user):
        # Paged over the caller's warehouses only: `total` must not count rows
        # they cannot see.
        if warehouse:
            wscope.require_in_scope(user, wh_svc.resolve_canonical_name(user.tenant_id, warehouse))
        everything = svc.list_stock_page(user.tenant_id, limit=10**9, offset=0,
                                         q=q, warehouse=warehouse)["items"]
        rows = wscope.filter_rows(user, everything)
        return ok({"items": rows[offset:offset + limit], "total": len(rows),
                   "limit": limit, "offset": offset})
    return ok(svc.list_stock_page(user.tenant_id, limit=limit, offset=offset,
                                  q=q, warehouse=warehouse))


@router.get("/stock/lookup")
def lookup_stock(
    code: str = Query(min_length=1, max_length=200,
                      description="A scanned barcode or a SKU"),
    warehouse: Optional[str] = Query(default=None, max_length=100),
    user: CurrentUser = Depends(get_current_user),
):
    """Resolve a scanned code to a SKU (barcode first, then SKU). Declared before
    `/stock/{sku}`, which would otherwise take 'lookup' for a SKU."""
    from backend.inventory import stock_count_service as count_svc
    if not wscope.is_scoped(user):
        return ok(count_svc.lookup(user.tenant_id, code, warehouse))
    # A scoped caller. A named warehouse must be theirs; the quantity returned
    # is then that warehouse's alone, and the product is still identified from
    # the catalogue (scanning an item that has never been stocked here is how
    # it gets counted in). Without a warehouse the quantity would be a sum, so
    # it is summed over THEIR warehouses only, and a code held only elsewhere
    # is not found - as on GET /stock/{sku}.
    if (warehouse or "").strip():
        wscope.require_in_scope(user, wh_svc.resolve_canonical_name(user.tenant_id, warehouse))
        return ok(count_svc.lookup(user.tenant_id, code, warehouse))
    return ok(count_svc.lookup(user.tenant_id, code, None,
                               visible=lambda w: wscope.in_scope(user, w)))


@router.get("/stock/{sku}")
def get_stock(sku: str, user: CurrentUser = Depends(get_current_user)):
    row = svc.get_stock(user.tenant_id, sku)
    if wscope.is_scoped(user):
        # The row returned must be one of the caller's, not whichever the
        # database happens to find first.
        mine = wscope.filter_rows(user, [
            svc.get_stock(user.tenant_id, sku, warehouse=w)
            for w in svc.list_stock_warehouses(user.tenant_id, sku)])
        row = mine[0] if mine else None
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
    from backend.entitlements.service import enforce_limit, limit_guard

    # Resolve to the canonical spelling FIRST so the pre-checks below judge
    # the same (sku, warehouse) row svc.upsert_stock will actually write —
    # 'norte' with an existing 'Norte' is an update, not a new location.
    warehouse = wh_svc.resolve_canonical_name(user.tenant_id, body.warehouse)
    wscope.require_in_scope(user, warehouse)

    data = body.model_dump(exclude_none=True)
    # Only what the caller ACTUALLY SENT is written — on a new row as much as
    # on an existing one.
    #
    # `exclude_none` alone cannot tell "omitted" from "sent": the three fields
    # that are not Optional (`min_stock`, `lead_time_days`, `moq`) arrive
    # already materialised to their model defaults, so a body naming only the
    # stock count silently wrote 0 / 15 / 1 over them. Measured on the daily
    # "update stock" screen, which posts exactly {current_stock,
    # lead_time_days, supplier}: a supplier minimum of 100 became 1 and the
    # recommendation went from 100 units to 81 — below a minimum the supplier
    # will not ship. The stock count and the supplier's minimum have nothing to
    # do with each other; counting stock must not rewrite the purchasing rules.
    #
    # This filter used to apply ONLY to existing rows, on the reasoning that "a
    # new row has to start somewhere". It does — but the place it starts is the
    # SCHEMA default (`lead_time_days INT NOT NULL DEFAULT 15`), which is
    # already exactly what omitting the column produces. What the old branch
    # actually added was the provenance stamp: upsert_stock stamps
    # `<field>_set_by = 'user'` for every tracked field present in `data`, so a
    # materialised default arrived labelled as a value a human had chosen.
    #
    # That lie has teeth. `stock_defaults_service.resolve_field` lets the SKU
    # row win over a rule only when its `_set_by` says somebody set it — so a
    # tenant who configures "Acme delivers in 45 days" as a supplier rule had it
    # silently overridden, on every SKU created through this endpoint, by a 15
    # nobody ever typed. It also defeated the whole point of the provenance
    # columns: "the user chose 15" and "nobody ever touched this" became
    # indistinguishable again, which is the exact bug they were migrated in to
    # kill (see defaults.py, SOURCE_DEFAULT).
    #
    # Omitting them leaves `<field>_set_by` NULL, which is what "we assumed
    # this" is spelled as, and the value on the row is unchanged either way.
    data = {k: v for k, v in data.items() if k in body.model_fields_set}
    # The ceiling and the write, inside one transaction holding one per-tenant
    # lock. Split apart — the way this endpoint used to do it — twelve
    # simultaneous requests against a ceiling of five left ten rows: every one
    # of them counted before any of them had committed. See
    # entitlements.service.limit_guard.
    with limit_guard(user.tenant_id) as conn:
        if not svc.get_stock(user.tenant_id, sku, warehouse=warehouse, conn=conn):
            enforce_limit(user.tenant_id, "max_skus",
                          svc.count_stock(user.tenant_id, conn=conn), conn=conn)
        # A new warehouse name would otherwise be auto-created for free by
        # svc.upsert_stock -> _ensure_warehouse, bypassing max_locations
        # entirely. Enforced BEFORE the write so a blocked request never
        # creates the row.
        if not wh_svc.get_warehouse_by_name(user.tenant_id, warehouse):
            enforce_limit(user.tenant_id, "max_locations",
                          wh_svc.count_warehouses(user.tenant_id), conn=conn)
        row = svc.upsert_stock(user.tenant_id, sku, data, conn=conn)
    return ok(row)


@router.patch("/stock/{sku}")
def patch_stock(
    sku: str,
    body: StockPatch,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Partial update of ONE existing stock row. It never creates one.

    The row this patch lands on used to be decided by two halves that did not
    talk to each other: the 404 check looked the SKU up with no warehouse
    filter (so it found the row wherever it lived), and the write went through
    `upsert_stock`, which defaults a missing warehouse to 'principal'. A SKU
    that only existed in 'Norte' passed the check on the Norte row and got a
    BRAND NEW row inserted in 'principal' — phantom units that inflate
    coverage and talk a buyer out of a purchase they needed to make.

    Now the target warehouse is resolved first and the existence check is made
    against THAT row:

      · `warehouse` sent      → that row, 404 if the SKU is not in it;
      · SKU in exactly one    → that one, whatever it is called;
      · SKU in several, one
        of them 'principal'   → 'principal' (what this endpoint has always
                                done, and it is a real row, not a new one);
      · SKU in several, no
        'principal'           → 422 naming them. This is precisely the case
                                that used to fabricate the phantom row, and
                                there is no safe guess to make for the caller.
    """
    warehouses = svc.list_stock_warehouses(user.tenant_id, sku)
    if wscope.is_scoped(user):
        # Only the caller's rows exist as far as they are concerned; an SKU
        # held elsewhere reads as not found, and the "name one of them" help
        # never lists warehouses they may not see.
        warehouses = [w for w in warehouses if wscope.in_scope(user, w)]
    if not warehouses:
        raise AppError(
            "stock_sku_not_found", f"SKU '{sku}' not found in inventory",
            status_code=404, params={"sku": sku},
        )

    if body.warehouse is not None:
        target = wh_svc.resolve_canonical_name(user.tenant_id, body.warehouse)
        wscope.require_in_scope(user, target)
        if target not in warehouses:
            raise AppError(
                "stock_sku_not_found_in_warehouse",
                f"SKU '{sku}' has no stock in warehouse '{target}'",
                status_code=404, params={"sku": sku, "warehouse": target},
            )
    elif len(warehouses) == 1:
        target = warehouses[0]
    elif wh_svc.DEFAULT_WAREHOUSE in warehouses:
        target = wh_svc.DEFAULT_WAREHOUSE
    else:
        raise AppError(
            "stock_warehouse_required",
            f"SKU '{sku}' exists in more than one warehouse; name the one to update",
            status_code=422,
            params={"sku": sku, "warehouses": ", ".join(warehouses)},
        )

    wscope.require_in_scope(user, target)
    data = body.model_dump(exclude_none=True)
    data.pop("warehouse", None)
    if not data:
        return ok(svc.get_stock(user.tenant_id, sku, warehouse=target))
    row = svc.upsert_stock(user.tenant_id, sku, {**data, "warehouse": target})
    return ok(row)


@router.delete("/stock/{sku}", status_code=204)
def delete_stock(sku: str, user: CurrentUser = Depends(require_analyst_or_above)):
    # Deleting a SKU removes its rows in EVERY warehouse.
    wscope.require_company_wide(user)
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


def _parse_stock_rows(
    raw_rows: list[dict], mapping: dict, thousands_dot: Optional[bool] = None,
) -> tuple[list[dict], list[dict], int, list[str]]:
    """
    (valid canonical rows, per-row errors, rows skipped for having no SKU,
    cells whose dot nobody has disambiguated yet).

    Numbers are read with the LatAm-tolerant parser: '1.234,56' and '₡ 1 234'
    are values, 'N/D' is a reported error. The comma/dot verdict is taken once
    for the whole file so an ambiguous '1,250' inherits what its unambiguous
    neighbours already proved.

    The fourth return value is the half the file cannot settle on its own: a
    file of "1.250" and "980" with no comma anywhere means either 1250 or 1.25,
    and the parser used to pick 1.25 in silence — every quantity divided by a
    thousand, no row errors, and the whole catalogue in PEDIR_YA
    (stability 11.2). `thousands_dot` is the user's answer once they have
    been asked.
    """
    numeric_sources = [mapping[f] for f in stock_import.NUMERIC_FIELDS if f in mapping]
    samples = [
        str(r.get(col)) for r in raw_rows for col in numeric_sources
        if r.get(col) not in (None, "")
    ]
    decimal_comma = stock_import.has_decimal_comma(samples)
    ambiguous_cells = ([] if thousands_dot is not None
                       else stock_import.dot_is_ambiguous(samples))

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

        # A NUL anywhere in the row means the export is corrupt. This path
        # keeps the byte (the stdlib csv reader does not truncate the way
        # pandas does), so the row is rejected by name instead of being stored
        # as a different product code than the file says.
        if any("\x00" in str(v) for v in raw_row.values() if v is not None):
            errors.append(_row_error(
                line_no, sku.replace("\x00", ""), "inventory_import_row_has_nul",
                {}, "the row contains a NUL byte and was not imported",
            ))
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
            value = stock_import.parse_number(row[fld], decimal_comma=decimal_comma,
                                              thousands_dot=thousands_dot)
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

    return rows, errors, skipped_no_sku, ambiguous_cells


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
    rows, errors, skipped_no_sku, ambiguous_cells = _parse_stock_rows(raw_rows, used)

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
        # The one thing the file cannot answer about itself. When `ambiguous`
        # is true the wizard must ASK before importing: read as decimals (the
        # old silent guess) every quantity is divided by a thousand, the import
        # reports success, and the catalogue drops to PEDIR_YA.
        "number_format": {
            "ambiguous": bool(ambiguous_cells),
            "samples": ambiguous_cells,
            # What each reading would produce for the first sample, so the
            # question can be asked in numbers instead of in vocabulary.
            "as_decimal": (stock_import.parse_number(ambiguous_cells[0])
                           if ambiguous_cells else None),
            "as_thousands": (stock_import.parse_number(ambiguous_cells[0], thousands_dot=True)
                             if ambiguous_cells else None),
        },
    })


@router.post("/bulk")
async def bulk_import(
    file: UploadFile = File(...),
    mapping: Optional[str] = Form(default=None),
    warehouse: Optional[str] = Form(default=None),
    thousands_dot: Optional[bool] = Form(default=None),
    only_fill_missing: bool = Form(default=False),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Import stock from a CSV or Excel file.

    Columns are matched by alias, accent- and case-blind, so a real ERP export
    ('Código', 'Existencia', 'Costo Unitario', separated by ';') imports with
    no hand-editing. `mapping` — a JSON object of {canonical_field:
    source_column} sent by the wizard — overrides the detection per field.

    `warehouse` is the destination for rows that do not name one. Without it the
    only way to stock a second location was a `warehouse` COLUMN — supported
    here since 5.4, but never mentioned in the UI, so creating a warehouse led
    to "Sin datos en esta bodega" and no way forward. A row that DOES name a
    warehouse keeps its own: a multi-warehouse sheet still imports as written.

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
        rows_, errors_, skipped_, ambiguous_ = _parse_stock_rows(
            raw_rows_, used_, thousands_dot=thousands_dot)
        return fmt_, columns_, used_, detected_, rows_, errors_, skipped_, ambiguous_

    (fmt, columns, used, detected, rows, errors,
     skipped_no_sku, ambiguous_cells) = await asyncio.to_thread(_parse)

    # The file says "1.250" and nothing in it says whether that is 1250 or
    # 1.25. Refusing is the point: the old behaviour picked 1.25, reported
    # "1,200 products imported" and put the whole catalogue in PEDIR_YA
    # (stability 11.2). The preview asks the question; an import that arrives
    # without the answer is one that skipped it.
    if ambiguous_cells:
        raise AppError(
            "inventory_import_number_format_unclear",
            "The file uses a dot in numbers like "
            f"{ambiguous_cells[0]} and nothing in it says whether that is a "
            "thousands separator or a decimal point. Answer that first.",
            status_code=422,
            params={
                "samples": ", ".join(ambiguous_cells),
                "as_decimal": stock_import.parse_number(ambiguous_cells[0]),
                "as_thousands": stock_import.parse_number(ambiguous_cells[0],
                                                          thousands_dot=True),
            },
        )

    # Destination for rows that name no warehouse. Applied before the limit
    # pre-checks below, which count new (sku, warehouse) keys and new location
    # names — they must judge the rows that will actually be written.
    default_wh = (warehouse or "").strip()
    if default_wh:
        for row in rows:
            if not (row.get("warehouse") or "").strip():
                row["warehouse"] = default_wh

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
    from backend.entitlements.service import enforce_limit, limit_guard

    # Rows READ from the file. `rows` is collapsed in place below, so the
    # count the user is shown has to be taken before that.
    total_read = len(rows)
    stats = {"duplicates": 0}
    # Rows the writer could not persist. Filled inside the worker thread below
    # and merged into the same `errors` channel the parse stage already uses:
    # to the person holding the file, "row 41 never saved" and "row 41 was
    # unreadable" are the same question.
    write_failures: list[dict] = []

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
        # A sheet that writes to any warehouse outside the caller's scope is
        # refused whole: importing "the rows you may" would report success for a
        # file that was only partly applied.
        wscope.require_all_in_scope(user, sorted({r["warehouse"] for r in rows}))

        # Collapse duplicate (sku, warehouse) rows, and COUNT them.
        #
        # `new_keys` below was already de-duplicated for the ceiling check, but
        # the write loop was not: bulk_upsert does one upsert per row against
        # the (tenant, sku, warehouse) conflict target, so N rows for the same
        # pair meant the last one silently won — and `imported` counted the
        # CALLS, so the user was told "350 de 350" while 120 rows existed.
        #
        # Collapsed field-wise rather than last-row-wins wholesale: two rows
        # for one SKU often carry different columns (one the cost, one the
        # count), and dropping the earlier row's fields would lose data the
        # file did contain. Later values still win per field, which is the only
        # defensible reading of "the file says it twice".
        deduped: dict[tuple, dict] = {}
        for r in rows:
            key = (r["sku"], r["warehouse"])
            if key in deduped:
                stats["duplicates"] += 1
                deduped[key].update(r)
            else:
                deduped[key] = r
        rows[:] = list(deduped.values())

        # One lock for the whole import. Without it, two CSVs uploaded at the
        # same moment each counted the catalogue before either had written, and
        # a tenant capped at 100 SKUs ended up with 200.
        with limit_guard(user.tenant_id) as conn:
            return _check_and_write_locked(conn)

    def _check_and_write_locked(conn) -> int:
        existing_keys = svc.list_stock_keys(user.tenant_id)
        new_keys = {(r["sku"], r["warehouse"]) for r in rows} - existing_keys
        enforce_limit(user.tenant_id, "max_skus", svc.count_stock(user.tenant_id, conn=conn),
                      adding=len(new_keys), conn=conn)

        # Same bypass risk as PUT /stock: a CSV with N distinct new warehouse
        # names would otherwise create all N for free via
        # svc.upsert_stock -> _ensure_warehouse inside bulk_upsert's loop.
        # Compute the DISTINCT new names up front and enforce max_locations
        # against (current count + new names) BEFORE bulk_upsert writes
        # anything, so a blocked import never partially creates stock rows.
        existing_wh_names = wh_svc.list_warehouse_names(user.tenant_id)
        new_wh_names = {r["warehouse"] for r in rows} - existing_wh_names
        enforce_limit(user.tenant_id, "max_locations", wh_svc.count_warehouses(user.tenant_id),
                      adding=len(new_wh_names), conn=conn)

        # `only_fill_missing` is the buyer's answer to "does this re-import
        # overwrite what I corrected by hand?" — off by default, which is the
        # behaviour every existing caller had, and on when the wizard's toggle
        # says so. Without it a monthly ERP re-export silently reverted every
        # manual lead time, and re-stamped the provenance to 'file' so the UI
        # could not even badge the value as the tenant's own (stability 11.9).
        # bulk_upsert does one synchronous DB round-trip per row. `failures`
        # collects the rows that were read from the file and did not reach the
        # database, so the response can name them instead of leaving "83 of 120"
        # as the only signal (stability 11.34).
        return svc.bulk_upsert(user.tenant_id, rows, failures=write_failures,
                               only_fill_missing=only_fill_missing)

    count = await asyncio.to_thread(_check_and_write)
    result = {
        "imported": count,
        "total_rows": total_read,
        # Echoed so the screen can say which reading it used rather than
        # leaving the buyer to infer it from the numbers.
        "only_fill_missing": only_fill_missing,
        "format": fmt,
        # What we read the file as, so the UI can say "we took Existencia as
        # your stock" instead of leaving the user guessing.
        "mapping": used,
        "detected_mapping": detected,
        "unmapped_columns": [c for c in columns if c not in used.values()],
        "skipped_no_sku": skipped_no_sku,
    }
    # Rows the file repeated. Reported rather than absorbed: "350 read, 120
    # written" is a fact the user can act on (their export is per-branch and
    # the branch column is not mapped), and the old silence made it look like
    # every row had landed.
    if stats["duplicates"]:
        result["duplicate_rows"] = stats["duplicates"]
    # Surface rejected rows so the user learns their data was garbage instead
    # of it being silently dropped/coerced — and, since 11.34, the rows that
    # parsed cleanly and still did not land.
    reported_errors = errors + write_failures
    if reported_errors:
        result["errors"] = reported_errors[:_MAX_REPORTED_ROW_ERRORS]
        result["error_count"] = len(reported_errors)
    if write_failures:
        result["write_failed_rows"] = len(write_failures)

    # What the file had and what the database got, in the history — because
    # "83 products imported" after a 120-row preview was the only signal, and
    # whoever reads it a day later has no file in front of them.
    written_short = count < (total_read - stats["duplicates"])
    details = {
        "rows_read":      total_read + len(errors),
        "rows_written":   count,
        "duplicate_rows": stats["duplicates"],
        "rejected_rows":  len(errors) + len(write_failures),
    }
    # Offloaded like every other DB call on this endpoint: it is one INSERT,
    # but this handler is async and the module's rule is that blocking work
    # does not run on the event loop.
    if errors or stats["duplicates"] or written_short:
        await asyncio.to_thread(
            record_event,
            user.tenant_id, user.user_id, "data.stock_import_partial",
            resource=file.filename, details=details,
            # One reason, the one the user can act on first: a rejected row is
            # a file to fix, a collapsed duplicate is a column they did not
            # map. A short write with neither is the case nobody has explained
            # yet (see stability 11.34), and saying so is better than
            # inventing a cause.
            reason=("rows_rejected_by_validation" if errors or write_failures
                    else "duplicate_rows_collapsed" if stats["duplicates"]
                    else "unknown"),
        )
    else:
        await asyncio.to_thread(
            record_event, user.tenant_id, user.user_id, "data.stock_imported",
            resource=file.filename, details=details,
        )
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


# Excel on a Spanish-locale Windows opens a .csv with the system ANSI codepage
# unless the file starts with a UTF-8 BOM, so `Señal` arrives as `SeÃ±al` and a
# supplier called `Distribuidora Peña` is mangled in the document the buyer
# forwards to that supplier. The frontend's own template writer
# (Frontend/src/lib/csvCheck.ts) already prefixes it — the product knew, and
# applied it in one writer out of several.
_CSV_BOM = "﻿"


@router.get("/template.csv")
def download_template(user: CurrentUser = Depends(get_current_user)):
    """Canonical inventory import template: header row + one example row."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_TEMPLATE_COLUMNS)
    w.writerow(_TEMPLATE_EXAMPLE)
    return Response(
        content=_CSV_BOM + buf.getvalue(),
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
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
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

_COVERAGE_UNIT = {"daily": "day", "weekly": "week", "monthly": "month"}


@router.get("/status")
def inventory_status(
    session_id: Optional[str] = Query(
        default=None,
        description="Completed forecast session; defaults to the tenant's active-period session"),
    service_level: float = Query(default=0.95, ge=0.5, le=0.999),
    signal: Optional[str] = Query(default=None, description="Filter by signal: PEDIR_YA, PEDIR_PRONTO, OK, SOBRESTOCK, SIN_DATOS"),
    supplier: Optional[str] = Query(default=None),
    abc: Optional[str] = Query(
        default=None, pattern="^[AaBbCc]$",
        description="Filter by ABC class (A, B or C), the value ranking of the whole catalogue"),
    by_warehouse: bool = Query(default=False, description="Per-(sku, warehouse) rows with network transfer suggestions"),
    limit: Optional[int] = Query(
        default=None, ge=1, le=500,
        description="Page size. Omitted = every row (the original contract)."),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="urgency", pattern=svc.STATUS_SORT_PATTERN),
    order: Optional[str] = Query(
        default=None, pattern="^(asc|desc)$",
        description="Direction for the column sorts; omitted keeps each key's own default"),
    q: Optional[str] = Query(default=None, max_length=100,
                             description="Matches SKU, product name, category or supplier"),
    skus: Optional[str] = Query(
        default=None, max_length=4000,
        description="Comma-separated exact SKUs: look up the names/status of a known few"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Returns per-SKU inventory status in the tenant's ACTIVE planning period:
    - coverage (in the active period's units — see `coverage_unit`)
    - traffic-light signal (PEDIR_YA / PEDIR_PRONTO / OK / SOBRESTOCK / SIN_DATOS)
    - recommended order quantity
    - inventory value
    """
    # Called directly (MCP tool, assistant) the parameters it is not given keep
    # their FastAPI `Query(...)` default OBJECT. Treat those as 'not provided' so
    # the optional paging/sort/search parameters never break a direct caller.
    from fastapi.params import Query as _QueryDefault
    if isinstance(limit, _QueryDefault): limit = None
    if isinstance(offset, _QueryDefault): offset = 0
    if isinstance(sort, _QueryDefault): sort = "urgency"
    if isinstance(order, _QueryDefault): order = None
    if isinstance(q, _QueryDefault): q = None
    if isinstance(skus, _QueryDefault): skus = None
    if isinstance(abc, _QueryDefault): abc = None
    if not session_id:
        session_id = planning_service.resolve_active_session(user.tenant_id)
        if not session_id:
            raise AppError(
                "no_completed_session",
                "No completed session for this tenant yet",
                status_code=400,
            )

    period = planning_service.get_planning(user.tenant_id).get("period", "daily")

    # The aggregate view is served from the persisted snapshot (SQL filtering,
    # sorting and paging; recomputed only when an input changed — see
    # backend/inventory/status_snapshot.py). None means the snapshot could not
    # be trusted or built, and the live computation below answers instead.
    # A warehouse-scoped caller never reads it: the snapshot is the company-wide
    # aggregate (stock and demand summed over every warehouse), and the scoped
    # branch below is what restricts rows to the caller's warehouses.
    if not by_warehouse and not wscope.is_scoped(user):
        snap = status_snapshot.read_status(
            user.tenant_id, session_id, service_level, period,
            signal=signal, supplier=supplier, skus=skus, q=q,
            sort=sort, order=order, limit=limit, offset=offset, abc=abc,
        )
        if snap is not None:
            page = None
            if limit is not None:
                page = {"limit": limit, "offset": offset, "total": snap["total"], "sort": sort}
                if order:
                    page["order"] = order
            return ok({
                "period": period,
                "coverage_unit": _COVERAGE_UNIT.get(period, "day"),
                "items": snap["items"],
                "page": page,
                "excluded_skus": svc.get_excluded_skus(user.tenant_id, session_id),
                "summary": {
                    "total_skus":    snap["total"],
                    "order_now":     snap["counts"]["order_now"],
                    "order_soon":    snap["counts"]["order_soon"],
                    "ok":            snap["counts"]["ok"],
                    "without_stock": snap["counts"]["without_stock"],
                    "with_forecast": snap["counts"]["with_forecast"],
                    "overstock":     snap["counts"]["overstock"],
                    "sin_datos":     snap["counts"]["sin_datos"],
                    "total_inventory_value": round(snap["total_value"], 2),
                },
                "computed_at": snap["computed_at"],
            })

    # Both views share the source-then-filter shape; only the response
    # envelope differs.
    scoped = wscope.is_scoped(user)
    if by_warehouse or scoped:
        # A scoped caller always gets the per-warehouse computation, restricted
        # to their warehouses: the tenant-wide row blends in stock and demand of
        # warehouses they may not see. `scoped_status_rows` also removes any
        # transfer pointing at a warehouse outside the scope.
        items = wscope.scoped_status_rows(user, svc.get_inventory_status_by_warehouse(
            user.tenant_id, session_id, service_level, period))
    else:
        items = svc.get_inventory_status(user.tenant_id, session_id, service_level, period)

    if signal:
        signal_up = signal.upper()
        items = [i for i in items if i["signal"] == signal_up]

    if supplier:
        items = [i for i in items if (i.get("supplier") or "").lower() == supplier.lower()]

    if abc:
        items = [i for i in items if i.get("abc") == abc.upper()]

    if skus and skus.strip():
        wanted = {x.strip() for x in skus.split(",") if x.strip()}
        items = [i for i in items if i["sku"] in wanted]

    if q and q.strip():
        needle = q.strip().lower()
        items = [i for i in items if needle in " ".join(
            str(i.get(k) or "") for k in ("sku", "display_name", "category", "supplier")).lower()]

    # The summary describes the whole filtered set; paging only narrows what
    # is sent. Both responses below read `items` for the summary and `shown`
    # for the rows, so a page can never change a total.
    shown, page = items, None
    if limit is not None:
        ordered = svc.sort_status_items(items, sort, order)
        shown = ordered[offset:offset + limit]
        page = {"limit": limit, "offset": offset, "total": len(items), "sort": sort}
        if order:
            page["order"] = order

    if by_warehouse:
        return ok({
            "period": period,
            "coverage_unit": _COVERAGE_UNIT.get(period, "day"),
            "items": shown,
            "page": page,
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
        "items": shown,
        "page": page,
        # Present only for a scoped caller: the rows are per (sku, warehouse)
        # over these warehouses, not one row per SKU for the company.
        **({"scope": {"warehouses": sorted(wscope.scope_names(user) or []),
                      "rows_are_per_warehouse": True}} if scoped else {}),
        "excluded_skus": svc.get_excluded_skus(user.tenant_id, session_id),
        "summary": {
            "total_skus":    len(items),
            "order_now":      critical,
            "order_soon":  warning,
            "ok":            sum(1 for i in items if i["signal"] == "OK"),
            "without_stock": sum(1 for i in items if not i.get("has_stock")),
            "with_forecast": sum(1 for i in items if i.get("has_forecast")),
            "overstock":    sum(1 for i in items if i["signal"] == "SOBRESTOCK"),
            "sin_datos":     sum(1 for i in items if i["signal"] == "SIN_DATOS"),
            "total_inventory_value": round(total_value, 2),
        },
        "computed_at": datetime.now(timezone.utc).isoformat(),
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
    wscope.require_in_scope(user, wh_svc.resolve_canonical_name(user.tenant_id, body.warehouse))
    row = shrinkage_svc.record_shrinkage(
        user.tenant_id, body.sku, body.quantity, body.reason,
        user_id=user.user_id, warehouse=body.warehouse, notes=body.notes,
        occurred_at=occurred_at,
    )
    # Units left the building without a sale. The warehouse recorded is the one
    # the service RESOLVED, not the one the form sent: those differ (§11.8) and
    # the history has to say where the stock actually came off.
    record_event(
        user.tenant_id, user.user_id, "data.shrinkage_recorded",
        resource=body.sku,
        details={
            "sku":              body.sku,
            "quantity":         body.quantity,
            "warehouse":        row.get("warehouse"),
            "shrinkage_reason": body.reason,
        },
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
    if wscope.is_scoped(user):
        # Filter after the cut would return fewer than `limit` rows; read wider.
        rows = shrinkage_svc.list_shrinkage(user.tenant_id, sku=sku, limit=200)
        return ok(wscope.filter_rows(user, rows)[:limit])
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
    warehouse: Optional[str] = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
):
    """Point-in-time stock levels for trend visualisation.

    Without `warehouse` the series is the TENANT-WIDE level — per-location rows
    summed per day, not listed one after another. With it, that one location's
    own history, which begins when snapshots started carrying a warehouse
    (2026-09-16): rows older than that are tenant-wide totals and are not
    attributed to a location after the fact.
    """
    existing = svc.get_stock(user.tenant_id, sku)
    if not existing:
        raise AppError(
            "stock_sku_not_found", f"SKU '{sku}' not found in inventory",
            status_code=404, params={"sku": sku},
        )
    canonical = wh_svc.resolve_canonical_name(user.tenant_id, warehouse) if warehouse else None
    if canonical:
        wscope.require_in_scope(user, canonical)
    if canonical is None and wscope.is_scoped(user):
        # No warehouse named: the level over THEIR warehouses, never the
        # company's. An SKU they hold nowhere is simply not found.
        mine = [w for w in svc.list_stock_warehouses(user.tenant_id, sku)
                if wscope.in_scope(user, w)]
        if not mine:
            raise AppError(
                "stock_sku_not_found", f"SKU '{sku}' not found in inventory",
                status_code=404, params={"sku": sku},
            )
        from backend.inventory import scoped_views
        history = scoped_views.stock_history_over(user.tenant_id, sku, days, mine)
        return ok({"sku": sku, "days": days, "warehouse": None, "history": history})
    history = svc.get_stock_history(user.tenant_id, sku, days=days, warehouse=canonical)
    return ok({"sku": sku, "days": days, "warehouse": canonical, "history": history})


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
    period = planning_service.get_planning(user.tenant_id).get("period", "daily")
    if wscope.is_scoped(user):
        items = wscope.scoped_status_rows(user, svc.get_inventory_status_by_warehouse(
            user.tenant_id, session_id, 0.95, period))
    else:
        items = svc.get_inventory_status(user.tenant_id, session_id, period=period)
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


@router.post("/events/simulate")
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
            period=planning_service.get_planning(user.tenant_id).get("period", "daily"),
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


@router.get("/events/{event_id}/multipliers")
def list_event_multipliers(event_id: str, user: CurrentUser = Depends(get_current_user)):
    """Per-SKU, per-family or per-category multiplier overrides for this event."""
    if not svc.get_event(user.tenant_id, event_id):
        raise AppError("event_not_found", "Event not found", status_code=404)
    return ok(svc.get_event_multipliers(user.tenant_id, event_id))


@router.put("/events/{event_id}/multipliers")
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


@router.get("/events")
def list_events(user: CurrentUser = Depends(get_current_user)):
    return ok(svc.list_events(user.tenant_id))


@router.get("/events/upcoming")
def upcoming_events(
    days: int = Query(default=60, ge=1, le=365),
    user: CurrentUser = Depends(get_current_user),
):
    return ok(svc.get_upcoming_events(user.tenant_id, days_ahead=days))


@router.post(
    "/events", status_code=201,
)
def create_event(body: EventCreate, user: CurrentUser = Depends(require_analyst_or_above)):
    ev = svc.create_event(user.tenant_id, body.model_dump())
    return ok(ev)


@router.patch("/events/{event_id}")
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
)
def delete_event(event_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    svc.delete_event(user.tenant_id, event_id)


# ── LatAm commercial calendar (feature 3.4) ──────────────────────────────────

class CalendarSeedRequest(BaseModel):
    country: str = "CR"
    years:   Optional[list[int]] = Field(default=None, max_length=5)


class CatalogToggleRequest(BaseModel):
    active: bool


@router.get("/events/catalog")
def get_event_catalog(
    country: str = Query(default="CR", max_length=4),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Which commercial events StockAI knows for a country, and whether this tenant
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


@router.post("/events/catalog/seed")
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
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    period = planning_service.get_planning(user.tenant_id).get("period", "daily")
    try:
        pdf_bytes = svc.generate_inventory_pdf(user.tenant_id, session_id, service_level, period)
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


_IDEMPOTENCY_KEY_DOC = (
    "Optional. One unique value (a UUID) per order you mean to place. Repeating "
    "the request with the same key returns the order the first request created "
    "(200, replayed=true) instead of creating a second one; the same key with "
    "different lines is refused with 409 po_idempotency_key_reused."
)


def _po_response(record: dict, response: Response) -> dict:
    """A created order answers 201; a replay of an idempotency key answers 200
    with `replayed: true` and the FIRST order's body, so a client that retried
    a request it never saw answered gets the order it already placed."""
    out = {k: v for k, v in record.items() if k != "idempotency_fingerprint"}
    out["replayed"] = bool(record.get("replayed"))
    if out["replayed"]:
        response.status_code = 200
    return out


@router.post("/log-po", status_code=201)
def log_po(
    response: Response,
    session_id: str = Query(...),
    body: Optional[POLogRequest] = None,
    idempotency_key: Optional[str] = Header(
        default=None, alias="Idempotency-Key", description=_IDEMPOTENCY_KEY_DOC),
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

    `Idempotency-Key` (header, optional): one value per cart submission. A
    second request with the same key — a double tap, a client retry after a
    dropped connection, two tabs racing — returns the order the first one
    created instead of writing an identical second order (which then counted
    as stock on its way twice). A key reused for a DIFFERENT order is a 409.
    """
    from backend.inventory.roi_service import (
        find_by_idempotency_key, log_po_generation, validate_idempotency_key,
    )

    idempotency_key = validate_idempotency_key(idempotency_key)
    # Where the order arrives: the caller's own warehouse, never a default that
    # lands outside their scope (`scoped_destination` names one or refuses).
    po_destination = wscope.scoped_destination(
        user, body.destination_warehouse if body else None)
    decisions_recorded = bool(body and body.items)
    if idempotency_key and not decisions_recorded:
        # The no-body path re-derives the lines from the CURRENT semaforo,
        # which the first order has already changed (its units now count as
        # on their way). Re-deriving would produce a different list and a
        # false "key reused" conflict, so a replay is resolved before that.
        existing = find_by_idempotency_key(user.tenant_id, idempotency_key)
        if existing:
            return ok(_po_response({**existing, "replayed": True}, response))

    if decisions_recorded:
        po_items = [i.model_dump() for i in body.items]
    else:
        period = planning_service.get_planning(user.tenant_id).get("period", "daily")
        # Re-derived at the SAME level the file was built for. Without this the
        # export endpoint served Norte's rows while the order logged right
        # behind it was the tenant-wide list: the buyer downloaded a file with
        # nothing to order and /pedidos showed them an order for two SKUs they
        # never saw (stability 11.7, found walking the screen — the export
        # itself was already scoped).
        destination = po_destination
        if destination:
            canonical = wh_svc.resolve_canonical_name(user.tenant_id, destination)
            rows = svc.get_inventory_status_by_warehouse(
                user.tenant_id, session_id, period=period)
            items = [i for i in rows if i.get("warehouse") == canonical]
        else:
            items = svc.get_inventory_status(user.tenant_id, session_id, period=period)
        po_items = [
            i for i in items
            if i["signal"] in ("PEDIR_YA", "PEDIR_PRONTO") and (i.get("recommended_qty") or 0) > 0
        ]

    record = log_po_generation(
        user.tenant_id, session_id, po_items,
        destination_warehouse=po_destination,
        # The order is recorded either way; only the ADOPTION reading is
        # withheld. Nobody told us what the buyer decided here — the server
        # re-derived the list — so counting all of it as "followed" was the
        # product marking its own homework. See log_po_generation.
        decisions_recorded=decisions_recorded,
        idempotency_key=idempotency_key,
    )
    if record.get("replayed"):
        # Nothing was written, so nothing is recorded: the activity log must
        # not show the same order generated twice.
        return ok(_po_response(record, response))
    # The order exists from here on: the buyer will act on the file they just
    # downloaded, and /pedidos will show it. Recorded so the history answers
    # "who ordered what, and when" without anybody having to remember.
    from backend.inventory.roi_service import format_po_number
    record_event(
        user.tenant_id, user.user_id, "purchase.order_generated",
        resource=str(record.get("id") or ""),
        details={
            "reference": format_po_number(record.get("po_number"),
                                          str(record.get("id") or "")),
            "lines":     record.get("sku_count"),
            "value":     record.get("total_value"),
            "suppliers": len({(i.get("supplier") or "").strip()
                              for i in po_items if (i.get("supplier") or "").strip()}),
        },
    )
    return ok(_po_response(record, response))


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
    response: Response,
    idempotency_key: Optional[str] = Header(
        default=None, alias="Idempotency-Key", description=_IDEMPOTENCY_KEY_DOC),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    A purchase order the buyer writes from scratch — supplier chosen
    explicitly, lines typed in, no forecast session behind it. Persisted with
    source='manual' so adoption metrics stay clean. `Idempotency-Key`: same
    contract as /log-po.
    """
    from backend.inventory.roi_service import (
        create_manual_po as create_po_svc, validate_idempotency_key,
    )

    idempotency_key = validate_idempotency_key(idempotency_key)

    supplier = sup_svc.get_supplier(user.tenant_id, body.supplier_id)
    if not supplier:
        raise AppError("supplier_not_found", "Supplier not found", status_code=404)
    manual_destination = wscope.scoped_destination(user, body.destination_warehouse)

    record = create_po_svc(
        user.tenant_id, supplier,
        [l.model_dump() for l in body.lines],
        destination_warehouse=manual_destination,
        idempotency_key=idempotency_key,
    )
    return ok(_po_response(record, response))


@router.get("/roi")
def get_roi(user: CurrentUser = Depends(get_current_user)):
    """Returns accumulated ROI metrics across all time."""
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    from backend.inventory.roi_service import get_roi_summary
    return ok(get_roi_summary(user.tenant_id))


@router.get("/roi/monthly")
def get_roi_monthly(
    months: int = Query(default=6, ge=1, le=24),
    user: CurrentUser = Depends(get_current_user),
):
    """Last N months: orders, stockout risks handled, adoption, capital freed from overstock."""
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
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
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
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
    from backend.inventory import po_approval_service as approval_svc
    from backend.inventory.roi_service import get_po_history
    # `approval` is added only for a tenant with an approval rule.
    if wscope.is_scoped(user):
        rows = wscope.filter_po_rows(user, get_po_history(user.tenant_id, 10_000))[:limit]
    else:
        rows = get_po_history(user.tenant_id, limit)
    return ok(approval_svc.annotate_orders(user.tenant_id, rows))


@router.get("/po-history/page")
def po_history_page(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str = Query(default="all", pattern="^(all|unpaid|paid|cancelled)$"),
    q: Optional[str] = Query(default=None, max_length=100,
                             description="Matches the PO number"),
    user: CurrentUser = Depends(get_current_user),
):
    """The PO history, filtered and paged on the server. `total` counts the
    filtered set; `awaiting_reception` counts every open order of the tenant."""
    from backend.inventory import po_approval_service as approval_svc
    from backend.inventory.roi_service import get_po_history_page
    if wscope.is_scoped(user):
        from backend.inventory import scoped_views
        page = scoped_views.po_history_page(user, limit=limit, offset=offset,
                                            status=status, q=q)
    else:
        page = get_po_history_page(user.tenant_id, limit=limit, offset=offset,
                                   status=status, q=q)
    page["items"] = approval_svc.annotate_orders(user.tenant_id, page["items"])
    return ok(page)


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
    wscope.require_po_in_scope(user, po_log_id)
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
    Side effects: current_stock increases by the received units, and StockAI logs
    the supplier's REAL lead time (order date → reception date).
    """
    from datetime import datetime as _dt
    from backend.inventory import reception_service as rec_svc

    # Receiving adds stock to the order's destination warehouse.
    wscope.require_po_in_scope(user, po_log_id)
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
    # Stock moved and a lead time was learned. Both change what the semáforo
    # says tomorrow, and until now the only record was the PO's own row on a
    # screen the buyer has to go looking for.
    from backend.inventory.roi_service import format_po_number
    po_row = rec_svc.get_po(user.tenant_id, po_log_id) or {}
    received_items = [i for i in result.get("items", [])
                      if float(i.get("received_qty") or 0) > 0]
    record_event(
        user.tenant_id, user.user_id, "purchase.reception_recorded",
        resource=po_log_id,
        details={
            "reference": format_po_number(po_row.get("po_number"), po_log_id),
            "sku_count": len(received_items),
            "units":     sum(float(i.get("received_qty") or 0) for i in received_items),
            "warehouse": po_row.get("destination_warehouse"),
        },
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
    return ok(wscope.filter_po_rows(
        user, rec_svc.get_overdue_receptions(user.tenant_id), key="po_log_id"))


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
        # The directory name IS the tenant. A document generated before an
        # approval rule existed must not be served for an order that now needs
        # one and has not got it.
        from backend.inventory import po_approval_service as approval_svc
        approval_svc.assert_sendable(candidate.parent.name, po_log_id)
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

    wscope.require_po_in_scope(user, po_log_id)
    po = rec_svc.get_po(user.tenant_id, po_log_id)
    if not po:
        raise AppError("po_not_found", "Purchase order not found", status_code=404)
    # A cancelled order must not reach a supplier: they would ship it.
    if po.get("cancelled_at") is not None:
        raise AppError("po_cancelled",
                       "This order was cancelled; reopen it before sending it",
                       status_code=409)
    # An order that needs approval does not leave until it has it. Checked
    # before anything is built or mailed (and again, lower down, in
    # `generate_po_pdf` and `mark_po_sent`, so no caller can skip it).
    from backend.inventory import po_approval_service as approval_svc
    approval_svc.assert_sendable(user.tenant_id, po=po)

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
                tenant_id=user.tenant_id,
            )

        whatsapp_ok = False
        if supplier.get("whatsapp"):
            media_url = f"{settings.frontend_url}/api/v1/inventory/po/{po_log_id}/pdf/{slug}"
            text = wa_mod.build_po_supplier_text(supplier_name, po_log_id, supplier_items)
            whatsapp_ok = wa_mod.send_whatsapp(supplier["whatsapp"], text,
                                               media_url=media_url,
                                               tenant_id=user.tenant_id)

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

    # Same fact, in the history the whole tenant reads. `record_notification_
    # delivery` above is the delivery ledger the bell groups by fan-out; this is
    # the one-line answer to "did the order leave?", carrying WHY when it did
    # not. A buyer who closed the tab has no other way to find out.
    reference = format_po_number(po.get("po_number"), po_log_id)
    unreached = len(skipped) + len(unresolved)
    if sent:
        record_event(
            user.tenant_id, user.user_id, "purchase.order_sent",
            resource=po_log_id,
            details={"reference": reference, "sent": len(sent), "skipped": unreached},
        )
    elif ordered:
        # Nobody has the order. (An order with no orderable lines is not a
        # failure to deliver, so it records nothing.) The reason the buyer can
        # act on is the one that stopped the FIRST supplier — a missing contact is fixed on the
        # supplier's card, a dead transport by whoever owns the credential.
        no_contact = any(s.get("reason") == "no_contact_details" for s in skipped)
        if no_contact or unresolved:
            reason = "supplier_has_no_contact"
        else:
            reason = ("no_transport_configured"
                      if email_mod.failure_reason(user.tenant_id) == "not_configured"
                      else "transport_error")
        record_event(
            user.tenant_id, user.user_id, "purchase.order_not_sent",
            resource=po_log_id, reason=reason,
            details={"reference": reference, "skipped": unreached},
        )

    return ok({"sent": sent, "skipped": skipped, "unresolved": unresolved})


@router.post("/po/{po_log_id}/send-to-me", status_code=200)
def send_po_to_self(
    po_log_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Deliver the order to the BUYER's own WhatsApp so they forward it to their
    supplier (PENDIENTES #1) — no StockAI↔supplier integration required.

    Always returns the rendered text plus a wa.me deep link, so the flow works
    end to end even with no Twilio configured and no number on file: the UI can
    still offer "open in WhatsApp" and "copy message".
    """
    from urllib.parse import quote

    from backend.inventory import reception_service as rec_svc
    from backend.inventory.roi_service import format_po_number
    from backend.notifications import whatsapp as wa_mod
    from backend.users import service as user_svc

    wscope.require_po_in_scope(user, po_log_id)
    po = rec_svc.get_po(user.tenant_id, po_log_id)
    if not po:
        raise AppError("po_not_found", "Purchase order not found", status_code=404)
    # The text this returns is what the buyer forwards to the supplier: it is a
    # send path like any other, so it is held until the order is approved.
    from backend.inventory import po_approval_service as approval_svc
    approval_svc.assert_sendable(user.tenant_id, po=po)

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
    # The supplier the buyer has on this line right now. Optional, because the
    # briefing surface evaluates without a cart — but when the screen has one it
    # must travel, or a line whose supplier was switched keeps being quoted the
    # previous supplier's ladder (stability 11.14).
    supplier_id: Optional[str] = None


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
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    period = planning_service.get_planning(user.tenant_id).get("period", "daily")
    status_items = svc.get_inventory_status(user.tenant_id, session_id, period=period)

    if body and body.items:
        cart = [{"sku": i.sku, "quantity": i.quantity, "supplier_id": i.supplier_id}
                for i in body.items]
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
        user.tenant_id, cart, status_items, holding_cost_pct, period=period,
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
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
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
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    budget = body.budget if body else None
    # None on the cart path: nothing was solved, so there is no plan to qualify.
    plan_status: Optional[str] = None

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
        # The caller's own horizon and the tenant's planning period — not a
        # hardcoded 30 days at the default daily grain. This path answers "does
        # the recommended purchase fit in the cash I have?", so it has to price
        # the SAME plan /compras is showing; solving a different horizon at a
        # different grain answered a question nobody asked, and the answer was
        # then labelled with this endpoint's horizon_days.
        fit_period = planning_service.get_planning(user.tenant_id).get("period", "daily")
        fit_stock_rows = svc.list_stock(user.tenant_id)
        # Same resolution as /inventory/optimize, for the same reason: this
        # endpoint prices the plan /compras is showing, so it has to be built
        # on the lead times and MOQs that plan is built on.
        fit_planning = opt_svc.resolve_planning_inputs(user.tenant_id, fit_stock_rows)
        inp = opt_svc.build_optimization_input(
            user.tenant_id, session_id, horizon_days, stock_rows=fit_stock_rows,
            period=fit_period, planning=fit_planning)
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
                    result = opt_svc.solve(inp)
            except opt_svc.OptimizerBusy:
                raise AppError(
                    "optimizer_busy",
                    "Optimizer busy (too many concurrent requests); please retry.",
                    status_code=503,
                )
            serialized = opt_svc.serialize_optimization_result(
                inp, result, fit_stock_rows, horizon_days=horizon_days,
                planning=fit_planning)
            lines = [
                {
                    "sku": o["sku"],
                    "supplier_name": o.get("supplier"),
                    "quantity": o["qty"],
                    "unit_cost": o.get("unit_cost"),
                }
                for o in serialized["orders"]
            ]
            plan_status = result.status
    else:
        lines = []

    fit = cash_service.evaluate_purchase_fit(
        user.tenant_id, lines, budget, horizon_days,
    )
    # WHICH plan was priced, not just what it costs.
    #
    # `/inventory/optimize` degrades to a greedy shortcut when the solver cannot
    # finish in time, and says so — this endpoint threw that away, so a cash
    # answer built on the shortcut was presented with the same confidence as one
    # built on the optimum. The number is not wrong; the thing it describes is a
    # different plan, and the user had no way to tell.
    #
    # Only set on the path that actually solves. A caller who sent their own
    # cart is being told about THEIR cart, and there is no plan status to report.
    if plan_status is not None:
        fit["plan_status"] = plan_status
    return ok(fit)


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
            "never arrive. Use an address like name@company.com.",
            {"email": text[:64]},
        )
    return text


_PHONE_SEPARATORS = re.compile(r"[\s().\-]")


def _validated_phone(value: Optional[str]) -> Optional[str]:
    """Normalize a supplier phone / WhatsApp number; blank stays None.

    The UI sends E.164 (``+50688887777``), but API clients and numbers saved
    before the country picker existed arrive as ``+506 8888-7777``,
    ``00506 8888 7777`` or a bare local ``8888-7777``. Separators are stripped
    and a ``00`` international prefix becomes ``+``, so the stored value is what
    Twilio can actually deliver to.

    Rejected: letters and other junk, and anything that opens with ``+`` but is
    not valid E.164. A bare local number is kept as digits rather than rejected
    — editing an old supplier must not start failing on a field the user never
    touched — and the purchase-order send reports it per supplier if it cannot
    be delivered.
    """
    if value is None:
        return None
    text = _PHONE_SEPARATORS.sub("", value.strip())
    if not text:
        return None
    if text.startswith("00"):
        text = "+" + text[2:]
    if text.startswith("+"):
        valid = re.fullmatch(r"\+[1-9]\d{7,14}", text) is not None
    else:
        valid = re.fullmatch(r"\d{6,15}", text) is not None
    if not valid:
        raise PydanticCustomError(
            "supplier_phone_shape",
            "'{phone}' is not a phone number. Use the country code and digits, "
            "like +50688887777.",
            {"phone": value.strip()[:32]},
        )
    return text


class SupplierCreate(BaseModel):
    name:           str
    email:          Optional[str] = None
    phone:          Optional[str] = None
    whatsapp:       Optional[str] = None
    # Optional, and None by default, because this field is PROVENANCE.
    #
    # It used to be `int = Field(default=15)`, so the model handed the service a
    # 15 for every caller that sent none — and `_stamp_lead_time_provenance`
    # records SOURCE_USER for any call that supplies a lead time. StockAI's own
    # assumption was therefore filed as the supplier's declaration, and the
    # scorecard printed DECLARADO 15d for a supplier who declared nothing
    # (stability 11.32). `exclude_none=True` in the handler now drops it
    # entirely, so the column's own DEFAULT 15 applies without anybody claiming
    # to have chosen it.
    lead_time_days: Optional[int] = Field(default=None, ge=1, le=365)
    lead_time_std:  int   = Field(default=3, ge=0, le=60)
    # How often this buyer actually orders from this supplier, in days
    # (stability.md 17/19.3). 0 (the column's own default) means no cadence
    # has been declared, and the recommendation reproduces today's arithmetic
    # exactly — see `backend/inventory/service.py::_calc_recommended`.
    review_period_days: int = Field(default=0, ge=0, le=365)
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

    @field_validator("phone", "whatsapp")
    @classmethod
    def _check_phone(cls, value: Optional[str]) -> Optional[str]:
        return _validated_phone(value)


class SupplierPatch(BaseModel):
    name:           Optional[str]   = None
    email:          Optional[str]   = None
    phone:          Optional[str]   = None
    whatsapp:       Optional[str]   = None
    lead_time_days: Optional[int]   = Field(default=None, ge=1, le=365)
    lead_time_std:  Optional[int]   = Field(default=None, ge=0, le=60)
    review_period_days: Optional[int] = Field(default=None, ge=0, le=365)
    payment_terms:  Optional[str]   = None
    payment_terms_days: Optional[int] = Field(default=None, ge=0, le=365)
    notes:          Optional[str]   = None

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: Optional[str]) -> Optional[str]:
        return _validated_email(value)

    @field_validator("phone", "whatsapp")
    @classmethod
    def _check_phone(cls, value: Optional[str]) -> Optional[str]:
        return _validated_phone(value)


class SkuSupplierUpsert(BaseModel):
    is_primary:     bool  = True
    unit_cost:      Optional[float] = None
    moq:            float = Field(default=1, ge=1)
    lead_time_days: Optional[int]   = Field(default=None, ge=1, le=365)
    notes:          Optional[str]   = None


@router.get("/suppliers")
def list_suppliers(user: CurrentUser = Depends(get_current_user)):
    return ok(sup_svc.list_suppliers(user.tenant_id))


@router.get("/suppliers/page")
def list_suppliers_page(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: Optional[str] = Query(default=None, max_length=100,
                             description="Matches name, phone or email"),
    user: CurrentUser = Depends(get_current_user),
):
    """Active suppliers, searched and paged on the server (the plain list stays
    whole for the dropdowns that need every supplier)."""
    items = sup_svc.list_suppliers(user.tenant_id, q=q, limit=limit, offset=offset)
    return ok({"items": items, "total": sup_svc.count_suppliers(user.tenant_id, q),
               "limit": limit, "offset": offset})


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


@router.post("/suppliers/{supplier_id}/reactivate")
def reactivate_supplier(supplier_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    """Undo a deactivation.

    Deactivation was always logical (`active = FALSE`) and never had an undo, so
    dropping a supplier by mistake was a one-way door. It also left the create
    endpoint's `supplier_name_taken_by_deactivated` (409) naming a row the user
    could not reach — the error told them the answer and gave them no verb.

    Looked up WITHOUT the `active` filter on purpose: the row this acts on is
    precisely the one every other read hides.
    """
    row = sup_svc.reactivate_supplier(user.tenant_id, supplier_id)
    if row is None:
        raise AppError("supplier_not_found", "Supplier not found", status_code=404)
    return ok(row)


# ── Warehouses ────────────────────────────────────────────────────────────────

class WarehouseCreate(BaseModel):
    name:       str = Field(min_length=1)
    is_default: bool = False


@router.get("/warehouses")
def list_warehouses(user: CurrentUser = Depends(get_current_user)):
    return ok(wscope.filter_rows(user, wh_svc.list_warehouses(user.tenant_id), key="name"))


@router.post(
    "/warehouses", status_code=201,
)
def create_warehouse(body: WarehouseCreate, request: Request,
                     user: CurrentUser = Depends(require_analyst_or_above)):
    # A new warehouse changes the company's structure, which a user limited to
    # some warehouses does not own.
    wscope.require_company_wide(user)
    if not (body.name or "").strip():
        raise AppError(
            "warehouse_name_required", "Warehouse name is required", status_code=422,
        )
    # Resolve first so a case-variant of an existing warehouse ('norte' vs
    # 'Norte') is treated as the idempotent re-create it is, not a new
    # location for the max_locations pre-check.
    name = wh_svc.resolve_canonical_name(user.tenant_id, body.name)
    if not wh_svc.get_warehouse_by_name(user.tenant_id, name):
        from backend.entitlements.service import enforce_limit, limit_guard
        # The write stays INSIDE the block: the lock has to still be held when
        # the warehouse row commits, or the next caller counts a catalogue that
        # does not yet include it and the ceiling is back to being advisory.
        with limit_guard(user.tenant_id) as _conn:
            enforce_limit(user.tenant_id, "max_locations",
                          wh_svc.count_warehouses(user.tenant_id), conn=_conn)
            warehouse = wh_svc.create_warehouse(
                user.tenant_id, name, is_default=body.is_default,
            )
    else:
        # Already exists: an idempotent re-create that consumes no ceiling, so
        # it has no business waiting on another tenant's import for a lock.
        warehouse = wh_svc.create_warehouse(
            user.tenant_id, name, is_default=body.is_default,
        )
    audit.note(request, target_id=name, label=name,
               after={"name": name, "is_default": bool(body.is_default)})
    return ok(warehouse)


class WarehousePatch(BaseModel):
    demand_share: Optional[float] = Field(default=None, ge=0, le=100)


@router.patch(
    "/warehouses/{name}",
)
def patch_warehouse(
    name: str,
    body: WarehousePatch,
    request: Request,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Set or clear the manual demand share for one warehouse (feature 5.4)."""
    # A share redistributes demand between warehouses, so it moves the figures
    # of the ones outside a scope too.
    wscope.require_company_wide(user)
    previous = wh_svc.get_warehouse_by_name(user.tenant_id, name) or {}
    try:
        row = wh_svc.set_demand_share(user.tenant_id, name, body.demand_share)
    except ValueError as e:
        raise _svc_error(e)
    audit.note(request, label=name,
               before={"demand_share": previous.get("demand_share")},
               after={"demand_share": body.demand_share})
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
)
def list_transfer_lanes(user: CurrentUser = Depends(get_current_user)):
    """Configured lanes only. A pair with no row falls back to the documented
    default (lead_time_days=1, cost_per_unit=0, fixed_cost=0) everywhere it is
    consumed — see backend/inventory/transfer_lane_service.py."""
    lanes = lane_svc.list_lanes(user.tenant_id)
    if wscope.is_scoped(user):
        # A lane is visible from either end; the other end is shown by name only.
        lanes = [l for l in lanes if wscope.in_scope(user, l.get("from_warehouse"))
                 or wscope.in_scope(user, l.get("to_warehouse"))]
    return ok(lanes)


@router.put(
    "/warehouses/lanes",
)
def upsert_transfer_lane(
    body: TransferLaneUpsert,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    # A lane joins two warehouses and shapes the network's transfer advice.
    wscope.require_company_wide(user)
    try:
        row = lane_svc.upsert_lane(
            user.tenant_id, body.from_warehouse, body.to_warehouse,
            body.lead_time_days, body.cost_per_unit, body.fixed_cost)
    except ValueError as e:
        raise _svc_error(e)
    return ok(row)


@router.delete(
    "/warehouses/lanes", status_code=204,
)
def delete_transfer_lane(
    from_warehouse: str = Query(min_length=1),
    to_warehouse: str = Query(min_length=1),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Names travel as query params, not path segments: a warehouse name may
    contain a slash and would break path matching once encoded."""
    wscope.require_company_wide(user)
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


def _require_transfer_end_in_scope(user: CurrentUser, transfer_id: str, end: str) -> None:
    """403 unless the end of the transfer this action works on is the caller's.
    A transfer that does not exist is left for the service's own 404."""
    if not wscope.is_scoped(user):
        return
    t = tr_svc.get_transfer(user.tenant_id, transfer_id)
    if t:
        wscope.require_in_scope(user, t.get(end))


@router.post(
    "/transfers", status_code=201,
)
def create_transfer(
    body: TransferCreate,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    # Shipping takes stock OUT of the origin: that is the warehouse being acted on.
    wscope.require_in_scope(user, wh_svc.resolve_canonical_name(user.tenant_id, body.from_warehouse))
    try:
        t = tr_svc.create_transfer(
            user.tenant_id, user.user_id, body.from_warehouse, body.to_warehouse,
            [i.model_dump() for i in body.items], body.notes)
    except ValueError as e:
        raise _svc_error(e)
    record_event(
        user.tenant_id, user.user_id, "data.transfer_created",
        resource=str(t.get("id") or ""),
        details={
            "sku_count":      len(body.items),
            "units":          sum(i.qty for i in body.items),
            "from_warehouse": t.get("from_warehouse") or body.from_warehouse,
            "to_warehouse":   t.get("to_warehouse") or body.to_warehouse,
        },
    )
    return ok(t)


@router.get(
    "/transfers",
)
def list_transfers(
    status: Optional[str] = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
):
    rows = tr_svc.list_transfers(user.tenant_id, status)
    if wscope.is_scoped(user):
        # Visible from either end: the receiving side must see what is coming.
        rows = [t for t in rows if wscope.in_scope(user, t.get("from_warehouse"))
                or wscope.in_scope(user, t.get("to_warehouse"))]
    return ok(rows)


@router.post(
    "/transfers/{transfer_id}/receive",
)
def receive_transfer(
    transfer_id: str,
    body: TransferReceive,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    _require_transfer_end_in_scope(user, transfer_id, "to_warehouse")   # stock is added there
    try:
        t = tr_svc.receive_transfer(user.tenant_id, transfer_id, body.lines)
    except ValueError as e:
        raise _svc_error(e)
    return ok(t)


@router.post(
    "/transfers/{transfer_id}/cancel",
)
def cancel_transfer(
    transfer_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    _require_transfer_end_in_scope(user, transfer_id, "from_warehouse")  # stock goes back there
    try:
        t = tr_svc.cancel_transfer(user.tenant_id, transfer_id)
    except ValueError as e:
        raise _svc_error(e)
    return ok(t)


@router.post(
    "/transfers/{transfer_id}/close",
)
def close_transfer(
    transfer_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Close a partial transfer, writing the missing units off as shrinkage."""
    _require_transfer_end_in_scope(user, transfer_id, "to_warehouse")
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
    Fire the daily inventory alert immediately for this tenant — email to the
    admins, WhatsApp to the ones who opted in. Lets the user verify their
    channels without waiting for the 8:00 UTC scheduler run.

    Both channels mirror the daily loop in backend/inventory/service.py's
    run_daily_inventory_alerts(): a test fire that skipped one of them would
    prove less than it appears to.
    """
    # Same grain the 8:00 loop uses, so the test send previews the real thing
    # rather than a differently-computed one.
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    period = planning_service.get_planning(user.tenant_id).get("period", "daily")
    items = svc.get_inventory_status(user.tenant_id, session_id, period=period)
    critical = [i for i in items if i["signal"] == "PEDIR_YA"]
    warning  = [i for i in items if i["signal"] == "PEDIR_PRONTO"]
    if not critical and not warning:
        return ok({"sent": False, "reason": "No hay SKUs en riesgo — nada que alertar."})

    from backend.config import settings as _settings
    from backend.notifications.email import send_inventory_alert_email

    # `/hoy`, the same screen the 8:00 loop links to. This said `/inventory`,
    # which redirects to a DIFFERENT screen (`/inventario`) — so the preview and
    # the thing it previews did not even land the buyer in the same place.
    inventory_url = f"{_settings.frontend_url}/hoy"
    # Full lists: the email renderer trims the table itself and keeps the
    # counts real, so a test fire shows the same numbers the daily loop would.
    emails = svc.get_tenant_admin_emails(user.tenant_id)
    emails_sent = sum(
        1 for email in emails
        if send_inventory_alert_email(
            to=email, critical_items=critical, warning_items=warning,
            inventory_url=inventory_url, tenant_id=user.tenant_id,
            # The grain was resolved above and then not passed, so the renderer
            # fell back to its "daily" default: a weekly tenant's test email
            # said "4 días" where the real 8:00 email says "4 semanas". The
            # preview contradicted the thing it previews.
            period=period,
        )
    )

    from backend.notifications.whatsapp import build_inventory_alert_text, send_whatsapp

    wa_sent = 0
    numbers = svc.get_tenant_admin_whatsapps(user.tenant_id)
    if numbers:
        text = build_inventory_alert_text(critical, warning, inventory_url,
                                          period=period)
        wa_sent = sum(1 for n in numbers if send_whatsapp(n, text, tenant_id=user.tenant_id, plan_gated=True))

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
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
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
    """The valid product types, as the English keys the frontend translates.

    It used to return `{key: Spanish label}`, which put backend-authored copy
    on an English-mode screen. The caller renders `enum.product_type_<key>`.
    """
    return ok(list(bom_svc.PRODUCT_TYPES))


@router.patch("/stock/{sku}/product-type")
def set_product_type(
    sku: str,
    product_type: str = Query(..., description="finished_good | semi_finished | component | raw_material | packaging | service"),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
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


@router.get("/bom/{parent_sku}")
def get_bom(parent_sku: str, user: CurrentUser = Depends(get_current_user)):
    """Returns BOM (Bill of Materials) for a finished good."""
    return ok(bom_svc.list_bom(user.tenant_id, parent_sku))


@router.put(
    "/bom/{parent_sku}/{child_sku}", status_code=200,
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
)
def delete_bom_item(
    parent_sku: str,
    child_sku:  str,
    user:       CurrentUser = Depends(require_analyst_or_above),
):
    bom_svc.delete_bom_item(user.tenant_id, parent_sku, child_sku)


@router.get("/bom/{child_sku}/used-in")
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
    period = planning_service.get_planning(user.tenant_id).get("period", "daily")
    result = bom_svc.explode_requirements(user.tenant_id, session_id, horizon_days, period)
    return ok(result)


# ── Dead capital / Capital parado ─────────────────────────────────────────────
# "How long has the stock level itself gone without falling, and how much
# money is that" — from `inventory_snapshots` alone, no session or model. See
# `backend/inventory/dead_capital.py`'s module docstring. This is the ONE
# surface for money that is not moving: `/dead-stock` (depletion measured
# against the forecast, which priced an unknown unit cost at 0 and sorted by
# it) was retired on 2026-09-30 by the owner's decision, stability.md 19.2.

@router.get("/dead-capital")
def dead_capital(
    window_days: int = Query(
        default=dead_capital_svc.DEFAULT_WINDOW_DAYS, ge=7, le=365,
        description="Minimum days without a stock decrease to count as not moving"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Every SKU on hand whose stock level has not fallen in at least
    `window_days`, ranked worst first by money, with the tenant's total at the
    top. Needs no session — it reads real stock-level history, not a forecast.
    """
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    session_id = planning_service.resolve_active_session(user.tenant_id)
    result = dead_capital_svc.get_dead_capital(
        user.tenant_id, window_days=window_days, session_id=session_id)
    return ok(result)


# ── Supplier cost inflation / Margin erosion ─────────────────────────────────
# stability.md #20, items 5-6: both read `inventory_po_items.unit_cost` (a
# price history nobody realised the product already had) crossed either with
# itself (inflation) or with `inventory_stock.sale_price` (erosion). Neither
# needs a session or a new table — see `cost_alerts.py`'s module docstring
# for the honesty constraints this pair is built under.

@router.get("/supplier-cost-inflation")
def supplier_cost_inflation(
    window_days: int = Query(
        default=cost_alerts_svc.DEFAULT_WINDOW_DAYS, ge=30, le=1095,
        description="How far back to look for received-order cost observations"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Suppliers who raised a SKU's cost at least once in the window, worst
    first, with the products each one hit hardest. Built only from POs that
    were actually received — a quoted or rejected order proves nothing was
    paid.
    """
    result = cost_alerts_svc.get_supplier_cost_inflation(user.tenant_id, window_days=window_days)
    return ok(result)


@router.get("/margin-erosion")
def margin_erosion(
    window_days: int = Query(
        default=cost_alerts_svc.DEFAULT_WINDOW_DAYS, ge=30, le=1095,
        description="How far back to look for received-order cost observations"),
    min_erosion_pts: float = Query(
        default=cost_alerts_svc.DEFAULT_MIN_EROSION_PTS, ge=0, le=100,
        description="Minimum margin drop, in percentage points, to be listed"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    SKUs whose margin eroded because their cost rose while the product's
    sale_price is the only price StockAI has ever stored. `price_history_available:
    false` in the response is load-bearing: the "before" margin is today's
    price against a past cost, not a historical fact — see
    `cost_alerts.get_margin_erosion`'s docstring.
    """
    result = cost_alerts_svc.get_margin_erosion(
        user.tenant_id, window_days=window_days, min_erosion_pts=min_erosion_pts)
    return ok(result)


# ── The forecast in money ──────────────────────────────────────────────────
# stability.md #20 item 1: the engine predicts units, the product knows
# price and cost per SKU, nobody had multiplied them. See
# `forecast_money.py`'s module docstring for the honesty constraints.

@router.get("/forecast-money")
def forecast_money(
    session_id: Optional[str] = Query(
        default=None,
        description="Completed forecast session; defaults to the tenant's active-period session"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Projected revenue, cost and gross margin over the active session's
    forecast horizon, per SKU and in total, ranked so the top contributors
    are visible — "your next N days: X in sales, Y in margin, and these
    products carry it."
    """
    if not session_id:
        session_id = planning_service.resolve_active_session(user.tenant_id)
        if not session_id:
            raise AppError(
                "no_completed_session",
                "No completed session for this tenant yet",
                status_code=400,
            )

    result = forecast_money_svc.get_forecast_money(user.tenant_id, session_id)
    return ok(result)


# ── Export PO as CSV ───────────────────────────────────────────────────────────

@router.get("/status/export-po")
def export_po(
    session_id: str = Query(...),
    service_level: float = Query(default=0.95, ge=0.5, le=0.999),
    signals: str = Query(default="PEDIR_YA,PEDIR_PRONTO", description="Comma-separated signals to include"),
    warehouse: Optional[str] = Query(
        default=None,
        description="Export this warehouse's rows instead of the tenant-wide ones",
    ),
    user: CurrentUser = Depends(get_current_user),
):
    """Export purchase order as CSV, filtered to actionable SKUs.

    The period is resolved here for the same reason `GET /status` resolves it:
    this endpoint does NOT export what the buyer is looking at, it re-derives
    the list server-side. Without the period that re-derivation reads a weekly
    tenant's per-week demand as per-day, so the screen offered nothing to order
    and the CSV came back with a hundred units — plus a purchase order in
    /pedidos the buyer never saw on screen.

    `warehouse` exists for the same class of defect on the other axis
    (stability 11.7): the download menu sits above the warehouse selector and
    stayed enabled with a warehouse tab open, so a buyer reading "Norte needs
    40" downloaded a file saying 150 — the tenant-wide number — and `logPOGeneration`
    wrote that into /pedidos as an order they never saw. With it, the CSV is the
    rows of that warehouse, from the same per-warehouse computation the tab
    renders.
    """
    include_signals = {s.strip().upper() for s in signals.split(",")}
    period = planning_service.get_planning(user.tenant_id).get("period", "daily")
    if warehouse:
        canonical = wh_svc.resolve_canonical_name(user.tenant_id, warehouse)
        wscope.require_in_scope(user, canonical)
        rows = svc.get_inventory_status_by_warehouse(
            user.tenant_id, session_id, service_level, period)
        items = [i for i in rows if i.get("warehouse") == canonical]
    elif wscope.is_scoped(user):
        items = wscope.scoped_status_rows(user, svc.get_inventory_status_by_warehouse(
            user.tenant_id, session_id, service_level, period))
    else:
        items = svc.get_inventory_status(user.tenant_id, session_id, service_level, period)
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
        # `if cost` (and `cost or ""` below) treated a real unit cost of 0 as
        # "we do not know", printing both as an empty cell. They are different
        # facts: a free line and an unpriced line lead to different decisions,
        # and the PDF writer already makes this distinction
        # (inventory/po_pdf.py — "a line whose cost nobody recorded is priced
        # as UNKNOWN, not as zero"). Only None is unknown here too.
        value = round(qty * cost, 2) if cost is not None else ""
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
            cost if cost is not None else "",
            value,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([_CSV_BOM + output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=purchase_order.csv"},
    )


# ── MILP purchasing/transfers optimizer (MW-3) ───────────────────────────────

@router.get("/optimize")
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
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
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
        # The supplier inputs the semáforo plans on — lead time (rules +
        # learned receptions) and MOQ — resolved ONCE and handed to both the
        # build and the serialize, so the plan is solved and reported on the
        # same numbers /hoy shows.
        planning_inputs = opt_svc.resolve_planning_inputs(user.tenant_id, stock_rows)
        inp = opt_svc.build_optimization_input(
            user.tenant_id, session_id, horizon_days, stock_rows=stock_rows, period=period,
            planning=planning_inputs,
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
            result = opt_svc.solve(inp)
    except opt_svc.OptimizerBusy:
        raise AppError(
            "optimizer_busy",
            "Optimizer busy (too many concurrent requests); please retry.",
            status_code=503,
        )
    return ok({
        **opt_svc.serialize_optimization_result(
            inp, result, stock_rows, horizon_days=horizon_days,
            planning=planning_inputs),
        "needs_stock": needs_stock,
    })
