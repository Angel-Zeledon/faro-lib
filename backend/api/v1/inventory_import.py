"""
Bulk import of suppliers and purchase orders (CSV / Excel).

  GET  /inventory/suppliers/import/template   header + examples, CSV or XLSX
  POST /inventory/suppliers/import/preview    dry run, writes nothing
  POST /inventory/suppliers/import            commit

  GET  /inventory/po/import/template
  POST /inventory/po/import/preview
  POST /inventory/po/import

Same contract as the stock importer (`POST /inventory/bulk`): headers matched by
alias, an explicit `mapping` from the wizard overriding the detection, every
rejected row reported by line number with a code + params, NUL rows refused by
name, and the writes done under the tenant lock. Parsing and resolution live in
`backend/inventory/bulk_import.py`.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile

from backend.activity.events import record_event
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.db.connection import query, query_one
from backend.errors import AppError
from backend.inventory import bulk_import as bi
from backend.inventory import supplier_service as sup_svc
from backend.schemas.common import ok

router = APIRouter(prefix="/inventory", tags=["inventory"])
log = logging.getLogger(__name__)

# Excel on a Spanish-locale Windows opens a .csv with the ANSI codepage unless
# it starts with a UTF-8 BOM — same reason as the stock template.
_CSV_BOM = "﻿"
_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_ALLOWED_SUFFIXES = (".csv", ".txt", ".xlsx", ".xls", ".xlsm")


# ── Shared plumbing ───────────────────────────────────────────────────────────

def _check_upload_name(filename: Optional[str], content: bytes) -> str:
    """The filename to read the upload by. Refuses a type we cannot read (a PDF
    would otherwise decode as latin-1 and 'import' as garbage rows), and treats
    a ZIP container named .csv as the spreadsheet it really is."""
    name = (filename or "").strip()
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix and suffix not in _ALLOWED_SUFFIXES:
        raise AppError(
            "import_unsupported_file_type",
            f"Files of type '{suffix}' cannot be imported; use .csv or .xlsx",
            status_code=422,
            params={"extension": suffix},
        )
    if content[:4] == b"PK\x03\x04" and not suffix.startswith(".xls"):
        return "upload.xlsx"
    return name


def _template_response(columns: list[str], rows: list[list[str]], stem: str, fmt: str):
    if fmt == "xlsx":
        from backend.dataframes.io import xlsx_bytes
        return Response(
            content=xlsx_bytes(columns, [dict(zip(columns, r)) for r in rows]),
            media_type=_XLSX_MEDIA,
            headers={"Content-Disposition": f'attachment; filename="{stem}_template.xlsx"'},
        )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return Response(
        content=_CSV_BOM + buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{stem}_template.csv"'},
    )


def _no_valid_rows(errors: list[dict], columns: list[str], used: dict,
                   missing: list[str], blank: int) -> AppError:
    return AppError(
        "bulk_import_no_valid_rows",
        "No valid rows found in the file.",
        status_code=422,
        params={
            "rejected": len(errors),
            "errors": errors[:bi.MAX_REPORTED_ROW_ERRORS],
            "columns": columns,
            "mapping": used,
            "missing_required": missing,
            "blank_rows": blank,
        },
    )


def _missing_columns(missing: list[str]) -> AppError:
    return AppError(
        "bulk_import_missing_columns",
        "The file has no column for: " + ", ".join(missing),
        status_code=422,
        params={"missing": ", ".join(missing), "fields": missing},
    )


# ── Suppliers ─────────────────────────────────────────────────────────────────

def _load_supplier_state(tenant_id: str) -> dict[str, dict]:
    """Every supplier row by lower-cased name, ACTIVE OR NOT — a deactivated
    supplier still holds its name in the unique index."""
    rows = query("SELECT id, name, active FROM suppliers WHERE tenant_id = %s", (tenant_id,))
    return {str(r["name"]).strip().casefold(): r for r in rows}


def _classify(suppliers: list[dict], state: dict[str, dict]) -> None:
    for s in suppliers:
        existing = state.get(s["name"].casefold())
        if existing is None:
            s["status"] = "new"
        elif existing.get("active") is False:
            s["status"] = "deactivated"
        else:
            s["status"] = "existing"


def _parse_suppliers(filename, content, mapping_json, tenant_id):
    bi.check_file_size(tenant_id, len(content))
    name = _check_upload_name(filename, content)
    fmt, columns, raw_rows, sep = bi.read_table(name, content, bi.MAX_SUPPLIER_ROWS)
    used, detected = bi.supplier_mapping(columns, mapping_json)
    rows, errors, blank = bi.parse_supplier_rows(raw_rows, used)
    suppliers, duplicates = bi.collapse_suppliers(rows)
    _classify(suppliers, _load_supplier_state(tenant_id))
    return fmt, sep, columns, raw_rows, used, detected, suppliers, errors, blank, duplicates


@router.get("/suppliers/import/template")
def suppliers_import_template(
    format: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    user: CurrentUser = Depends(get_current_user),
):
    """Suppliers import template: the header row plus two example rows."""
    return _template_response(
        bi.SUPPLIER_TEMPLATE_COLUMNS, bi.SUPPLIER_TEMPLATE_ROWS, "suppliers", format)


@router.post("/suppliers/import/preview")
async def suppliers_import_preview(
    file: UploadFile = File(...),
    mapping: Optional[str] = Form(default=None),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Dry run of POST /suppliers/import: what was detected and what would be
    written. Touches no row."""
    content = await file.read()
    (fmt, sep, columns, raw_rows, used, detected, suppliers, errors,
     blank, duplicates) = await asyncio.to_thread(
        _parse_suppliers, file.filename, content, mapping, user.tenant_id)
    counts = {s: sum(1 for x in suppliers if x["status"] == s)
              for s in ("new", "existing", "deactivated")}
    return ok({
        "format": fmt,
        "separator": sep,
        "columns": columns,
        "total_rows": len(raw_rows),
        "mapping": used,
        "detected_mapping": detected,
        "unmapped_columns": [c for c in columns if c not in used.values()],
        "missing_required": [] if "name" in used else ["name"],
        "fields": list(bi.SUPPLIER_FIELDS),
        "importable_rows": len(suppliers),
        "new_suppliers": counts["new"],
        "existing_suppliers": counts["existing"],
        "deactivated_suppliers": counts["deactivated"],
        "rejected_rows": len(errors),
        "blank_rows": blank,
        "duplicate_rows": duplicates,
        "sample_rows": [
            {k: v for k, v in s.items() if k != "row"}
            for s in suppliers[:bi.PREVIEW_SAMPLE_ROWS]
        ],
        "issues": bi.group_errors(errors),
        "errors": errors[:bi.MAX_REPORTED_ROW_ERRORS],
        "error_count": len(errors),
    })


_SUPPLIER_WRITE_FIELDS = (
    "name", "email", "phone", "whatsapp", "lead_time_days", "lead_time_std",
    "review_period_days", "payment_terms", "payment_terms_days", "notes",
)


def _write_suppliers(tenant_id: str, suppliers: list[dict], on_existing: str):
    """Insert / update every supplier under the tenant lock, one savepoint per
    row so one refusal costs one row and not the whole file. Returns
    (created, updated, skipped_existing, row errors)."""
    from backend.db.connection import transaction
    from backend.entitlements.service import take_tenant_lock
    from backend.inventory.cash_service import parse_payment_terms_days

    created = updated = skipped = 0
    errors: list[dict] = []
    with transaction() as conn:
        # Held for the whole batch: the name check below and the INSERT must not
        # interleave with another request creating the same supplier.
        take_tenant_lock(tenant_id, conn)
        state = {
            str(r["name"]).strip().casefold(): r
            for r in query("SELECT id, name, active FROM suppliers WHERE tenant_id = %s",
                           (tenant_id,), conn=conn)
        }
        for s in suppliers:
            data = {k: s[k] for k in _SUPPLIER_WRITE_FIELDS if k in s}
            # Credit days derived from the free text, exactly as the direct
            # endpoint does; an explicit column always wins over the parser.
            if data.get("payment_terms_days") is None and data.get("payment_terms"):
                parsed = parse_payment_terms_days(data["payment_terms"])
                if parsed is not None:
                    data["payment_terms_days"] = parsed
            data = sup_svc._stamp_lead_time_provenance(data, data)

            existing = state.get(s["name"].casefold())
            if existing is not None and existing.get("active") is False:
                errors.append(bi.row_error(
                    s["row"], s["name"][:60], "supplier_import_row_deactivated",
                    {"column": "name", "value": s["name"][:60]},
                    "a deactivated supplier is registered under this name"))
                continue
            if existing is not None and on_existing == "skip":
                skipped += 1
                continue

            with conn.cursor() as cur:
                cur.execute("SAVEPOINT import_row")
            try:
                if existing is not None:
                    changes = {k: v for k, v in data.items() if k != "name"}
                    if changes:
                        sets = ", ".join(f"{k} = %s" for k in changes)
                        query_one(
                            f"UPDATE suppliers SET {sets} "
                            "WHERE tenant_id = %s AND id = %s RETURNING id",
                            (*changes.values(), tenant_id, existing["id"]), conn=conn)
                    updated += 1
                else:
                    cols = ", ".join(data)
                    phs = ", ".join(["%s"] * len(data))
                    row = query_one(
                        f"INSERT INTO suppliers (tenant_id, {cols}) VALUES (%s, {phs}) "
                        "RETURNING id, name, active",
                        (tenant_id, *data.values()), conn=conn)
                    state[s["name"].casefold()] = row or {"active": True}
                    created += 1
            except Exception as exc:                                  # noqa: BLE001
                with conn.cursor() as cur:
                    cur.execute("ROLLBACK TO SAVEPOINT import_row")
                log.warning("supplier import: row %s not written: %s", s["row"], exc)
                errors.append(bi.row_error(
                    s["row"], s["name"][:60], "bulk_import_row_write_failed",
                    {"column": "name", "value": s["name"][:60]},
                    "the row could not be saved"))
            else:
                with conn.cursor() as cur:
                    cur.execute("RELEASE SAVEPOINT import_row")
    return created, updated, skipped, errors


@router.post("/suppliers/import")
async def suppliers_import(
    file: UploadFile = File(...),
    mapping: Optional[str] = Form(default=None),
    on_existing: str = Form(default="skip", pattern="^(skip|update)$"),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Create suppliers from a CSV / Excel file.

    `on_existing`: a supplier already registered under the same name (compared
    case-insensitively, like everywhere else) is left alone (`skip`, the
    default) or has the cells the file filled written over it (`update`). A
    DEACTIVATED supplier is never reactivated by an import — the row is
    reported, and the user restores it on purpose.
    """
    content = await file.read()
    (fmt, _sep, columns, raw_rows, used, detected, suppliers, errors,
     blank, duplicates) = await asyncio.to_thread(
        _parse_suppliers, file.filename, content, mapping, user.tenant_id)

    if "name" not in used:
        raise _missing_columns(["name"])
    if not suppliers:
        raise _no_valid_rows(errors, columns, used, [], blank)

    created, updated, skipped, write_errors = await asyncio.to_thread(
        _write_suppliers, user.tenant_id, suppliers, on_existing)

    # Same bookkeeping as the stock import: a clean import is history, one that
    # lost rows is a warning.
    all_errors = errors + write_errors
    result = {
        "created": created,
        "updated": updated,
        "skipped_existing": skipped,
        "total_rows": len(raw_rows),
        "format": fmt,
        "on_existing": on_existing,
        "mapping": used,
        "detected_mapping": detected,
        "unmapped_columns": [c for c in columns if c not in used.values()],
        "blank_rows": blank,
        "duplicate_rows": duplicates,
        "error_count": len(all_errors),
        "errors": all_errors[:bi.MAX_REPORTED_ROW_ERRORS],
    }
    details = {
        "rows_read": len(raw_rows) - blank,
        "rows_written": created + updated,
        "duplicate_rows": duplicates,
        "rejected_rows": len(all_errors),
    }
    if all_errors or duplicates:
        await asyncio.to_thread(
            record_event, user.tenant_id, user.user_id, "data.suppliers_import_partial",
            resource=file.filename, details=details,
            reason="rows_rejected_by_validation" if all_errors else "duplicate_rows_collapsed",
        )
    else:
        await asyncio.to_thread(
            record_event, user.tenant_id, user.user_id, "data.suppliers_imported",
            resource=file.filename, details=details,
        )
    return ok(result)


# ── Purchase orders ───────────────────────────────────────────────────────────

def _import_key(file_hash: str, group: dict) -> str:
    """Idempotency key of one order created by one file: the same file imported
    twice answers with the orders it already made instead of making them again."""
    ident = "|".join((group["order_ref"].casefold(), str(group["supplier"]["id"]),
                      (group["warehouse"] or "").casefold()))
    return f"import-{file_hash[:16]}-{hashlib.sha256(ident.encode()).hexdigest()[:16]}"


def _parse_orders(filename, content, mapping_json, tenant_id):
    bi.check_file_size(tenant_id, len(content))
    name = _check_upload_name(filename, content)
    fmt, columns, raw_rows, sep = bi.read_table(name, content, bi.MAX_PO_ROWS)
    used, detected = bi.po_mapping(columns, mapping_json)
    lines, errors, blank = bi.parse_po_rows(raw_rows, used)
    resolved, resolve_errors = bi.resolve_po_lines(lines, bi.build_resolvers(tenant_id))
    groups, folded = bi.group_po_lines(resolved)
    if len(groups) > bi.MAX_PO_GROUPS:
        raise AppError(
            "import_too_many_orders",
            f"The file would create {len(groups)} orders; the limit is {bi.MAX_PO_GROUPS}",
            status_code=422,
            params={"orders": len(groups), "max_orders": bi.MAX_PO_GROUPS},
        )
    file_hash = hashlib.sha256(content).hexdigest()
    for g in groups:
        g["key"] = _import_key(file_hash, g)
    already = {r["idempotency_key"] for r in query(
        "SELECT idempotency_key FROM inventory_po_log "
        "WHERE tenant_id = %s AND idempotency_key = ANY(%s)",
        (tenant_id, [g["key"] for g in groups] or [""]))} if groups else set()
    for g in groups:
        g["already_imported"] = g["key"] in already
    return (fmt, sep, columns, raw_rows, used, detected, groups, folded,
            errors + resolve_errors, blank, len(lines))


def _missing_po_fields(used: dict) -> list[str]:
    return [f for f in ("supplier", "sku", "qty") if f not in used]


def _group_summary(g: dict) -> dict:
    costed = [l for l in g["lines"] if l["unit_cost"] is not None]
    return {
        "order_ref": g["order_ref"],
        "supplier": g["supplier"]["name"],
        "warehouse": g["warehouse"],
        "line_count": len(g["lines"]),
        "total_units": sum(l["qty"] for l in g["lines"]),
        "total_value": (sum(l["qty"] * l["unit_cost"] for l in costed) if costed else None),
        "lines_without_cost": len(g["lines"]) - len(costed),
        "already_imported": g["already_imported"],
    }


@router.get("/po/import/template")
def po_import_template(
    format: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    user: CurrentUser = Depends(get_current_user),
):
    """Purchase-order import template: header row plus example lines (two
    orders, the first with two lines)."""
    return _template_response(
        bi.PO_TEMPLATE_COLUMNS, bi.PO_TEMPLATE_ROWS, "purchase_orders", format)


@router.post("/po/import/preview")
async def po_import_preview(
    file: UploadFile = File(...),
    mapping: Optional[str] = Form(default=None),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Dry run of POST /po/import: the orders the file would create, and every
    row whose supplier / product / warehouse did not resolve. Writes nothing."""
    content = await file.read()
    (fmt, sep, columns, raw_rows, used, detected, groups, folded, errors,
     blank, parsed_lines) = await asyncio.to_thread(
        _parse_orders, file.filename, content, mapping, user.tenant_id)
    summaries = [_group_summary(g) for g in groups]
    sample = [
        {"order_ref": g["order_ref"], "supplier": g["supplier"]["name"], "sku": l["sku"],
         "qty": l["qty"], "unit_cost": l["unit_cost"], "warehouse": g["warehouse"],
         "cost_from_stock": l["cost_from_stock"]}
        for g in groups for l in g["lines"]
    ][:bi.PREVIEW_SAMPLE_ROWS]
    return ok({
        "format": fmt,
        "separator": sep,
        "columns": columns,
        "total_rows": len(raw_rows),
        "mapping": used,
        "detected_mapping": detected,
        "unmapped_columns": [c for c in columns if c not in used.values()],
        "missing_required": _missing_po_fields(used),
        "fields": list(bi.PO_FIELDS),
        "importable_rows": sum(len(g["lines"]) for g in groups) + folded,
        "order_count": len(groups),
        "orders": summaries[:20],
        "already_imported_orders": sum(1 for s in summaries if s["already_imported"]),
        "folded_rows": folded,
        "rejected_rows": len(errors),
        "blank_rows": blank,
        "sample_rows": sample,
        "issues": bi.group_errors(errors),
        "errors": errors[:bi.MAX_REPORTED_ROW_ERRORS],
        "error_count": len(errors),
    })


def _write_orders(tenant_id: str, groups: list[dict]):
    """One purchase order per group, each through the same writer as
    `POST /inventory/po` (header + lines in one transaction, OC number MAX+1
    with its retry). Returns (created orders, replayed orders, group errors)."""
    from backend.inventory.roi_service import create_manual_po, format_po_number

    created: list[dict] = []
    replayed: list[dict] = []
    errors: list[dict] = []
    for g in groups:
        lines = [{"sku": l["sku"], "qty": l["qty"], "unit_cost": l["unit_cost"],
                  "display_name": l["display_name"]} for l in g["lines"]]
        try:
            record = create_manual_po(
                tenant_id, g["supplier"], lines,
                destination_warehouse=g["warehouse"], idempotency_key=g["key"])
        except AppError as exc:
            errors.append(bi.row_error(
                g["rows"][0], g["order_ref"][:60], "po_import_group_conflict",
                {"column": "order_ref", "value": g["order_ref"][:60], "reason": exc.code},
                f"the order could not be created: {exc.code}"))
            continue
        except Exception as exc:                                       # noqa: BLE001
            log.warning("po import: order '%s' not written: %s", g["order_ref"], exc)
            errors.append(bi.row_error(
                g["rows"][0], g["order_ref"][:60], "bulk_import_row_write_failed",
                {"column": "order_ref", "value": g["order_ref"][:60]},
                "the order could not be saved"))
            continue
        summary = {
            "id": record.get("id"),
            "po_number": format_po_number(record.get("po_number"), str(record.get("id") or "")),
            "order_ref": g["order_ref"],
            "supplier": g["supplier"]["name"],
            "line_count": len(lines),
            "rows": g["rows"][:50],
        }
        (replayed if record.get("replayed") else created).append(summary)
    return created, replayed, errors


@router.post("/po/import")
async def po_import(
    file: UploadFile = File(...),
    mapping: Optional[str] = Form(default=None),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Create purchase orders from a CSV / Excel file, one order per
    (order reference, supplier, destination warehouse).

    Supplier and product references are resolved against the tenant's own data
    (supplier by name; product by code, or by name when exactly one product
    carries it). A row that does not resolve is reported on its row and the rest
    of the file is still imported. Orders are numbered like every other manual
    order (the next OC-number), and importing the same file twice returns the
    orders it already made instead of duplicating them.
    """
    content = await file.read()
    (fmt, _sep, columns, raw_rows, used, detected, groups, folded, errors,
     blank, parsed_lines) = await asyncio.to_thread(
        _parse_orders, file.filename, content, mapping, user.tenant_id)

    missing = _missing_po_fields(used)
    if missing:
        raise _missing_columns(missing)
    if not groups:
        raise _no_valid_rows(errors, columns, used, [], blank)

    created, replayed, write_errors = await asyncio.to_thread(
        _write_orders, user.tenant_id, groups)

    all_errors = errors + write_errors
    result = {
        "created_orders": len(created),
        "already_imported_orders": len(replayed),
        "orders": created[:100],
        "replayed": replayed[:100],
        "total_rows": len(raw_rows),
        "format": fmt,
        "mapping": used,
        "detected_mapping": detected,
        "unmapped_columns": [c for c in columns if c not in used.values()],
        "blank_rows": blank,
        "folded_rows": folded,
        "error_count": len(all_errors),
        "errors": all_errors[:bi.MAX_REPORTED_ROW_ERRORS],
    }
    details = {
        "rows_read": len(raw_rows) - blank,
        "rows_written": sum(o["line_count"] for o in created),
        "duplicate_rows": folded,
        "rejected_rows": len(all_errors),
    }
    if all_errors or folded:
        await asyncio.to_thread(
            record_event, user.tenant_id, user.user_id, "data.orders_import_partial",
            resource=file.filename, details=details,
            reason="rows_rejected_by_validation" if all_errors else "duplicate_rows_collapsed",
        )
    else:
        await asyncio.to_thread(
            record_event, user.tenant_id, user.user_id, "data.orders_imported",
            resource=file.filename, details=details,
        )
    return ok(result)
