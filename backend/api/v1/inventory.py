"""
Inventory management API.
GET/POST/PATCH/DELETE /inventory/stock     — per-SKU stock CRUD
GET                   /inventory/status    — traffic-light signal + recommendations
POST                  /inventory/bulk      — bulk CSV import
"""

import asyncio
import csv
import io
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError, model_validator

from backend.auth.guards import CurrentUser, get_current_user
from backend.inventory import service as svc
from backend.inventory import supplier_service as sup_svc
from backend.inventory import bom_service as bom_svc
from backend.schemas.common import ok

router = APIRouter(prefix="/inventory", tags=["inventory"])
log = logging.getLogger(__name__)


# ── Request models ─────────────────────────────────────────────────────────────

class StockUpsert(BaseModel):
    display_name:   Optional[str]   = None
    stock_actual:   float           = Field(ge=0)
    stock_minimo:   float           = Field(default=0, ge=0)
    lead_time_dias: int             = Field(default=15, ge=1, le=365)
    costo_unitario: Optional[float] = Field(default=None, ge=0)
    moq:            float           = Field(default=1, ge=1)
    proveedor:      Optional[str]   = None
    notas:          Optional[str]   = None


class StockPatch(BaseModel):
    display_name:   Optional[str]   = None
    stock_actual:   Optional[float] = Field(default=None, ge=0)
    stock_minimo:   Optional[float] = Field(default=None, ge=0)
    lead_time_dias: Optional[int]   = Field(default=None, ge=1, le=365)
    costo_unitario: Optional[float] = Field(default=None, ge=0)
    moq:            Optional[float] = Field(default=None, ge=1)
    proveedor:      Optional[str]   = None
    notas:          Optional[str]   = None


# ── Stock CRUD ─────────────────────────────────────────────────────────────────

@router.get("/stock")
def list_stock(user: CurrentUser = Depends(get_current_user)):
    return ok(svc.list_stock(user.tenant_id))


@router.get("/stock/{sku}")
def get_stock(sku: str, user: CurrentUser = Depends(get_current_user)):
    row = svc.get_stock(user.tenant_id, sku)
    if not row:
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found in inventory")
    return ok(row)


@router.put("/stock/{sku}", status_code=200)
def upsert_stock(
    sku: str,
    body: StockUpsert,
    user: CurrentUser = Depends(get_current_user),
):
    row = svc.upsert_stock(user.tenant_id, sku, body.model_dump(exclude_none=True))
    return ok(row)


@router.patch("/stock/{sku}")
def patch_stock(
    sku: str,
    body: StockPatch,
    user: CurrentUser = Depends(get_current_user),
):
    existing = svc.get_stock(user.tenant_id, sku)
    if not existing:
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found in inventory")
    data = body.model_dump(exclude_none=True)
    if not data:
        return ok(existing)
    row = svc.upsert_stock(user.tenant_id, sku, data)
    return ok(row)


@router.delete("/stock/{sku}", status_code=204)
def delete_stock(sku: str, user: CurrentUser = Depends(get_current_user)):
    existing = svc.get_stock(user.tenant_id, sku)
    if not existing:
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found in inventory")
    svc.delete_stock(user.tenant_id, sku)


# ── Bulk import from CSV ───────────────────────────────────────────────────────

@router.post("/bulk")
async def bulk_import(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Import stock data from CSV.
    Expected columns (case-insensitive): sku, stock_actual, lead_time_dias,
    costo_unitario, moq, proveedor, display_name, stock_minimo, notas
    """
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")  # handle BOM from Excel
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    # Normalize headers: lowercase + strip
    rows: list[dict] = []
    for raw_row in reader:
        row = {k.strip().lower(): v.strip() for k, v in raw_row.items() if v}

        def _float(k: str):
            try: return float(row[k]) if k in row else None
            except: return None

        def _int(k: str):
            try: return int(float(row[k])) if k in row else None
            except: return None

        parsed: dict = {"sku": row.get("sku", "").strip()}
        if not parsed["sku"]:
            continue

        for fld in ("display_name", "proveedor", "notas"):
            if fld in row:
                parsed[fld] = row[fld]
        for fld in ("stock_actual", "stock_minimo", "costo_unitario", "moq"):
            v = _float(fld)
            if v is not None:
                parsed[fld] = v
        v = _int("lead_time_dias")
        if v is not None:
            parsed["lead_time_dias"] = v

        # Validate against the same constraints as the direct PUT/PATCH endpoints
        # (e.g. stock_actual/costo_unitario/moq >= 0) — without this, CSV import
        # is the only write path that lets negative quantities into the DB.
        try:
            validated = StockPatch(**{k: v for k, v in parsed.items() if k != "sku"})
        except ValidationError as e:
            log.warning(f"bulk_import: skipped invalid row sku={parsed['sku']}: {e}")
            continue
        rows.append({"sku": parsed["sku"], **validated.model_dump(exclude_none=True)})

    if not rows:
        raise HTTPException(status_code=422, detail="No valid rows found in CSV. Ensure 'sku' column exists.")

    # bulk_upsert does one synchronous (blocking) DB round-trip per row. Without
    # offloading to a thread, a large CSV freezes the asyncio event loop — and
    # with it the entire backend, for every tenant — for the whole duration of
    # the import (confirmed: a 10k-row file blocked even the unrelated /health
    # endpoint for other tenants).
    count = await asyncio.to_thread(svc.bulk_upsert, user.tenant_id, rows)
    return ok({"imported": count, "total_rows": len(rows)})


# ── Status endpoint — the core of the product ─────────────────────────────────

@router.get("/status")
def inventory_status(
    session_id: str = Query(..., description="Completed forecast session to base recommendations on"),
    service_level: float = Query(default=0.95, ge=0.5, le=0.999),
    signal: Optional[str] = Query(default=None, description="Filter by signal: PEDIR_YA, PEDIR_PRONTO, OK, SOBRESTOCK, SIN_DATOS"),
    proveedor: Optional[str] = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Returns per-SKU inventory status:
    - days of coverage
    - traffic-light signal (PEDIR_YA / PEDIR_PRONTO / OK / SOBRESTOCK / SIN_DATOS)
    - recommended order quantity
    - inventory value
    """
    items = svc.get_inventory_status(user.tenant_id, session_id, service_level)

    if signal:
        signal_up = signal.upper()
        items = [i for i in items if i["signal"] == signal_up]

    if proveedor:
        items = [i for i in items if (i.get("proveedor") or "").lower() == proveedor.lower()]

    total_valor = sum(i["valor_inventario"] for i in items if i.get("valor_inventario"))
    critical    = sum(1 for i in items if i["signal"] == "PEDIR_YA")
    warning     = sum(1 for i in items if i["signal"] == "PEDIR_PRONTO")

    return ok({
        "items": items,
        "excluded_skus": svc.get_excluded_skus(user.tenant_id, session_id),
        "summary": {
            "total_skus":    len(items),
            "pedir_ya":      critical,
            "pedir_pronto":  warning,
            "ok":            sum(1 for i in items if i["signal"] == "OK"),
            "sobrestock":    sum(1 for i in items if i["signal"] == "SOBRESTOCK"),
            "sin_datos":     sum(1 for i in items if i["signal"] == "SIN_DATOS"),
            "valor_total_inventario": round(total_valor, 2),
        },
    })


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
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found in inventory")
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
    total_valor = sum(i["valor_inventario"] for i in items if i.get("valor_inventario"))
    return ok({
        "session_id":   session_id,
        "total_skus":   len(items),
        "pedir_ya":     sum(1 for i in items if i["signal"] == "PEDIR_YA"),
        "pedir_pronto": sum(1 for i in items if i["signal"] == "PEDIR_PRONTO"),
        "ok":           sum(1 for i in items if i["signal"] == "OK"),
        "sobrestock":   sum(1 for i in items if i["signal"] == "SOBRESTOCK"),
        "sin_datos":    sum(1 for i in items if i["signal"] == "SIN_DATOS"),
        "valor_total_inventario": round(total_valor, 2),
        "top_critical": [
            {"sku": i["sku"], "display_name": i["display_name"], "dias_cobertura": i["dias_cobertura"]}
            for i in items if i["signal"] == "PEDIR_YA"
        ][:5],
    })


# ── Events (temporadas / promociones) ────────────────────────────────────────

def _parse_event_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)")


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
            raise ValueError("end_date must not be before start_date")
        return self


class EventPatch(BaseModel):
    name:       Optional[str]   = None
    start_date: Optional[str]   = None
    end_date:   Optional[str]   = None
    multiplier: Optional[float] = Field(default=None, ge=0.1, le=10.0)
    notes:      Optional[str]   = None

    @model_validator(mode="after")
    def _check_date_order(self):
        if self.start_date is not None:
            _parse_event_date(self.start_date, "start_date")
        if self.end_date is not None:
            _parse_event_date(self.end_date, "end_date")
        if self.start_date is not None and self.end_date is not None:
            if date.fromisoformat(self.end_date) < date.fromisoformat(self.start_date):
                raise ValueError("end_date must not be before start_date")
        return self


@router.get("/events")
def list_events(user: CurrentUser = Depends(get_current_user)):
    return ok(svc.list_events(user.tenant_id))


@router.get("/events/upcoming")
def upcoming_events(
    days: int = Query(default=60, ge=1, le=365),
    user: CurrentUser = Depends(get_current_user),
):
    return ok(svc.get_upcoming_events(user.tenant_id, days_ahead=days))


@router.post("/events", status_code=201)
def create_event(body: EventCreate, user: CurrentUser = Depends(get_current_user)):
    ev = svc.create_event(user.tenant_id, body.model_dump())
    return ok(ev)


@router.patch("/events/{event_id}")
def patch_event(
    event_id: str,
    body: EventPatch,
    user: CurrentUser = Depends(get_current_user),
):
    data = body.model_dump(exclude_none=True)
    if "start_date" in data or "end_date" in data:
        existing = svc.get_event(user.tenant_id, event_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Event not found")
        effective_start = data.get("start_date", str(existing["start_date"]))
        effective_end = data.get("end_date", str(existing["end_date"]))
        if date.fromisoformat(effective_end) < date.fromisoformat(effective_start):
            raise HTTPException(status_code=422, detail="end_date must not be before start_date")

    ev = svc.update_event(user.tenant_id, event_id, data)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    return ok(ev)


@router.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: str, user: CurrentUser = Depends(get_current_user)):
    svc.delete_event(user.tenant_id, event_id)


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
    filename = f"inventario_{date.today().isoformat()}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── ROI tracking ─────────────────────────────────────────────────────────────

class POLineItem(BaseModel):
    sku:                  str
    display_name:         Optional[str]   = None
    proveedor:            Optional[str]   = None
    signal:               Optional[str]   = None
    cantidad_recomendada: float           = Field(default=0, ge=0)
    cantidad_final:       float           = Field(default=0, ge=0)
    costo_unitario:       Optional[float] = Field(default=None, ge=0)
    status:               str             = "approved"


class POLogRequest(BaseModel):
    items: Optional[list[POLineItem]] = None


@router.post("/log-po", status_code=201)
def log_po(
    session_id: str = Query(...),
    body: Optional[POLogRequest] = None,
    user: CurrentUser = Depends(get_current_user),
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
            if i["signal"] in ("PEDIR_YA", "PEDIR_PRONTO") and (i.get("cantidad_recomendada") or 0) > 0
        ]

    record = log_po_generation(user.tenant_id, session_id, po_items)
    return ok(record)


@router.get("/roi")
def get_roi(user: CurrentUser = Depends(get_current_user)):
    """Returns accumulated ROI metrics across all time."""
    from backend.inventory.roi_service import get_roi_summary
    return ok(get_roi_summary(user.tenant_id))


@router.get("/po-history")
def po_history(
    limit: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
):
    """Returns recent PO generation events for the history panel."""
    from backend.inventory.roi_service import get_po_history
    return ok(get_po_history(user.tenant_id, limit))


# ── Suppliers ─────────────────────────────────────────────────────────────────

class SupplierCreate(BaseModel):
    name:           str
    email:          Optional[str] = None
    phone:          Optional[str] = None
    whatsapp:       Optional[str] = None
    lead_time_dias: int   = Field(default=15, ge=1, le=365)
    lead_time_std:  int   = Field(default=3, ge=0, le=60)
    payment_terms:  Optional[str] = None
    notes:          Optional[str] = None


class SupplierPatch(BaseModel):
    name:           Optional[str]   = None
    email:          Optional[str]   = None
    phone:          Optional[str]   = None
    whatsapp:       Optional[str]   = None
    lead_time_dias: Optional[int]   = Field(default=None, ge=1, le=365)
    lead_time_std:  Optional[int]   = Field(default=None, ge=0, le=60)
    payment_terms:  Optional[str]   = None
    notes:          Optional[str]   = None


class SkuSupplierUpsert(BaseModel):
    is_primary:     bool  = True
    unit_cost:      Optional[float] = None
    moq:            float = Field(default=1, ge=1)
    lead_time_dias: Optional[int]   = Field(default=None, ge=1, le=365)
    notes:          Optional[str]   = None


@router.get("/suppliers")
def list_suppliers(user: CurrentUser = Depends(get_current_user)):
    return ok(sup_svc.list_suppliers(user.tenant_id))


@router.post("/suppliers", status_code=201)
def create_supplier(body: SupplierCreate, user: CurrentUser = Depends(get_current_user)):
    supplier = sup_svc.create_supplier(user.tenant_id, body.model_dump(exclude_none=True))
    return ok(supplier)


@router.patch("/suppliers/{supplier_id}")
def update_supplier(
    supplier_id: str,
    body: SupplierPatch,
    user: CurrentUser = Depends(get_current_user),
):
    data = body.model_dump(exclude_none=True)
    supplier = sup_svc.update_supplier(user.tenant_id, supplier_id, data)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return ok(supplier)


@router.delete("/suppliers/{supplier_id}", status_code=204)
def delete_supplier(supplier_id: str, user: CurrentUser = Depends(get_current_user)):
    existing = sup_svc.get_supplier(user.tenant_id, supplier_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Supplier not found")
    sup_svc.delete_supplier(user.tenant_id, supplier_id)


@router.get("/stock/{sku}/suppliers")
def get_sku_suppliers(sku: str, user: CurrentUser = Depends(get_current_user)):
    return ok(sup_svc.get_sku_suppliers(user.tenant_id, sku))


@router.put("/stock/{sku}/suppliers/{supplier_id}")
def assign_sku_supplier(
    sku: str,
    supplier_id: str,
    body: SkuSupplierUpsert,
    user: CurrentUser = Depends(get_current_user),
):
    # Verify supplier exists for this tenant
    supplier = sup_svc.get_supplier(user.tenant_id, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    link = sup_svc.upsert_sku_supplier(user.tenant_id, sku, supplier_id, body.model_dump(exclude_none=True))
    return ok(link)


@router.delete("/stock/{sku}/suppliers/{supplier_id}", status_code=204)
def remove_sku_supplier(
    sku: str,
    supplier_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    sup_svc.remove_sku_supplier(user.tenant_id, sku, supplier_id)


# ── Morning Briefing ──────────────────────────────────────────────────────────

@router.get("/morning-briefing")
def morning_briefing(
    session_id: str = Query(...),
    service_level: float = Query(default=0.95, ge=0.5, le=0.999),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Daily operations briefing: risks, recommendations, demand changes, KPIs.
    Designed to be the first thing a manager opens every morning.
    """
    data = svc.get_morning_briefing(user.tenant_id, session_id, service_level)
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
    user: CurrentUser = Depends(get_current_user),
):
    existing = svc.get_stock(user.tenant_id, sku)
    if not existing:
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found in inventory")
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


@router.put("/bom/{parent_sku}/{child_sku}", status_code=200)
def upsert_bom_item(
    parent_sku: str,
    child_sku:  str,
    body:       BomItemUpsert,
    user:       CurrentUser = Depends(get_current_user),
):
    try:
        item = bom_svc.upsert_bom_item(
            user.tenant_id, parent_sku, child_sku, body.model_dump(exclude_none=True)
        )
        return ok(item)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/bom/{parent_sku}/{child_sku}", status_code=204)
def delete_bom_item(
    parent_sku: str,
    child_sku:  str,
    user:       CurrentUser = Depends(get_current_user),
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
    Capital trapped = stock_actual × costo_unitario.
    """
    from backend.inventory.service import get_inventory_status, get_stock_history

    items = get_inventory_status(user.tenant_id, session_id)
    dead_items = []

    for item in items:
        if not item.get('has_stock') or not item.get('stock_actual'):
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
        avg_daily = item.get('demanda_diaria') or 0
        expected  = avg_daily * len(history)

        # Classify as dead if actual depletion is < 20% of expected
        if expected > 0 and depletion < expected * 0.20:
            days_static = len(history)
            capital = round(float(item.get('stock_actual', 0)) * float(item.get('costo_unitario') or 0), 2)
            holding_cost_annual = capital * 0.25  # 25% annual holding cost estimate
            holding_cost_monthly = round(holding_cost_annual / 12, 2)

            dead_items.append({
                'sku':              item['sku'],
                'display_name':     item.get('display_name'),
                'proveedor':        item.get('proveedor'),
                'stock_actual':     item.get('stock_actual'),
                'costo_unitario':   item.get('costo_unitario'),
                'capital_trapped':  capital,
                'holding_cost_monthly': holding_cost_monthly,
                'days_without_movement': days_static,
                'depletion_pct':    round(depletion / first_stock * 100, 1) if first_stock > 0 else 0,
                'avg_daily_demand': round(avg_daily, 2),
                'signal':           item.get('signal'),
                'abc':              item.get('abc', '?'),
                'action_suggested': (
                    'Devolver al proveedor' if item.get('abc') == 'C' else
                    'Ofrecer descuento' if item.get('abc') == 'B' else
                    'Revisar con ventas'
                ),
            })

    dead_items.sort(key=lambda x: x['capital_trapped'], reverse=True)

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
    po_items = [i for i in items if i["signal"] in include_signals and (i.get("cantidad_recomendada") or 0) > 0]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "SKU", "Nombre", "Proveedor", "Señal",
        "Stock actual", "Días cobertura", "Demanda (lead time)",
        "Cantidad recomendada", "MOQ", "Costo unitario", "Valor orden",
    ])
    for i in po_items:
        qty   = i.get("cantidad_recomendada") or 0
        cost  = i.get("costo_unitario")
        valor = round(qty * cost, 2) if cost else ""
        writer.writerow([
            i["sku"],
            i.get("display_name") or "",
            i.get("proveedor") or "",
            i["signal"],
            i.get("stock_actual") if i.get("stock_actual") is not None else "",
            i.get("dias_cobertura") if i.get("dias_cobertura") is not None else "",
            i.get("demanda_lead_time") or "",
            qty,
            i.get("moq") or 1,
            cost or "",
            valor,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=orden_de_compra.csv"},
    )
