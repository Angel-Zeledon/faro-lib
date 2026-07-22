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
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse
from psycopg2.pool import PoolError
from pydantic import BaseModel, Field, ValidationError, model_validator

from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.config import settings
from backend.entitlements.guards import require_feature
from backend.entitlements.plans import Feature
from backend.entitlements.service import has_feature
from backend.tenants.service import get_tenant
from backend.inventory import service as svc
from backend.inventory import supplier_service as sup_svc
from backend.inventory import bom_service as bom_svc
from backend.inventory import warehouse_service as wh_svc
from backend.inventory import optimizer_service as opt_svc
from backend.inventory import po_pdf
from backend.inventory import transfer_service as tr_svc
from backend.inventory import price_break_service as pb_svc
from backend.inventory import cash_service
from backend.schemas.common import ok
from backend.utils.csv_safe import csv_safe

router = APIRouter(prefix="/inventory", tags=["inventory"])
log = logging.getLogger(__name__)


# ── Request models ─────────────────────────────────────────────────────────────

class StockUpsert(BaseModel):
    display_name:   Optional[str]   = None
    current_stock:   float           = Field(ge=0)
    min_stock:   float           = Field(default=0, ge=0)
    lead_time_days: int             = Field(default=15, ge=1, le=365)
    unit_cost: Optional[float] = Field(default=None, ge=0)
    moq:            float           = Field(default=1, ge=1)
    supplier:      Optional[str]   = None
    notes:          Optional[str]   = None
    sale_price:   Optional[float] = Field(default=None, ge=0)
    category:      Optional[str]   = None
    brand:          Optional[str]   = None
    unit_of_measure:  Optional[str]   = None
    barcode:  Optional[str]   = None
    warehouse:         Optional[str]   = None


class StockPatch(BaseModel):
    display_name:   Optional[str]   = None
    current_stock:   Optional[float] = Field(default=None, ge=0)
    min_stock:   Optional[float] = Field(default=None, ge=0)
    lead_time_days: Optional[int]   = Field(default=None, ge=1, le=365)
    unit_cost: Optional[float] = Field(default=None, ge=0)
    moq:            Optional[float] = Field(default=None, ge=1)
    supplier:      Optional[str]   = None
    notes:          Optional[str]   = None
    sale_price:   Optional[float] = Field(default=None, ge=0)
    category:      Optional[str]   = None
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
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found in inventory")
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
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found in inventory")
    data = body.model_dump(exclude_none=True)
    if not data:
        return ok(existing)
    row = svc.upsert_stock(user.tenant_id, sku, data)
    return ok(row)


@router.delete("/stock/{sku}", status_code=204)
def delete_stock(sku: str, user: CurrentUser = Depends(require_analyst_or_above)):
    existing = svc.get_stock(user.tenant_id, sku)
    if not existing:
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found in inventory")
    svc.delete_stock(user.tenant_id, sku)


# ── Bulk import from CSV ───────────────────────────────────────────────────────

@router.post("/bulk")
async def bulk_import(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Import stock data from CSV.
    Expected columns (case-insensitive): sku, warehouse, display_name, category, brand,
    unit_of_measure, barcode, current_stock, min_stock, lead_time_days,
    unit_cost, sale_price, moq, supplier, notes
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

        for fld in ("display_name", "supplier", "notes",
                    "category", "brand", "unit_of_measure", "barcode", "warehouse"):
            if fld in row:
                parsed[fld] = row[fld]
        for fld in ("current_stock", "min_stock", "unit_cost", "moq", "sale_price"):
            v = _float(fld)
            if v is not None:
                parsed[fld] = v
        v = _int("lead_time_days")
        if v is not None:
            parsed["lead_time_days"] = v

        # Validate against the same constraints as the direct PUT/PATCH endpoints
        # (e.g. current_stock/unit_cost/moq >= 0) — without this, CSV import
        # is the only write path that lets negative quantities into the DB.
        try:
            validated = StockPatch(**{k: v for k, v in parsed.items() if k != "sku"})
        except ValidationError as e:
            log.warning(f"bulk_import: skipped invalid row sku={parsed['sku']}: {e}")
            continue
        rows.append({"sku": parsed["sku"], **validated.model_dump(exclude_none=True)})

    if not rows:
        raise HTTPException(status_code=422, detail="No valid rows found in CSV. Ensure 'sku' column exists.")

    # Resolve every distinct warehouse spelling in the CSV to its canonical
    # form BEFORE the limit pre-checks and the writes: 'norte' rows must land
    # on an existing 'Norte' location instead of counting as (and creating) a
    # new one. One lookup per distinct name, not per row.
    resolved_wh = {
        raw: wh_svc.resolve_canonical_name(user.tenant_id, raw)
        for raw in {r.get("warehouse") for r in rows}
    }
    for r in rows:
        r["warehouse"] = resolved_wh[r.get("warehouse")]

    existing_keys = svc.list_stock_keys(user.tenant_id)
    new_keys = {(r["sku"], r["warehouse"]) for r in rows} - existing_keys
    from backend.entitlements.service import enforce_limit
    enforce_limit(user.tenant_id, "max_skus", svc.count_stock(user.tenant_id), adding=len(new_keys))

    # Same bypass risk as PUT /stock: a CSV with N distinct new warehouse
    # names would otherwise create all N for free via
    # svc.upsert_stock -> _ensure_warehouse inside bulk_upsert's loop.
    # Compute the DISTINCT new names up front and enforce max_locations
    # against (current count + new names) BEFORE bulk_upsert writes anything,
    # so a blocked import never partially creates stock/warehouse rows.
    existing_wh_names = wh_svc.list_warehouse_names(user.tenant_id)
    new_wh_names = {r["warehouse"] for r in rows} - existing_wh_names
    enforce_limit(user.tenant_id, "max_locations", wh_svc.count_warehouses(user.tenant_id), adding=len(new_wh_names))

    # bulk_upsert does one synchronous (blocking) DB round-trip per row. Without
    # offloading to a thread, a large CSV freezes the asyncio event loop — and
    # with it the entire backend, for every tenant — for the whole duration of
    # the import (confirmed: a 10k-row file blocked even the unrelated /health
    # endpoint for other tenants).
    count = await asyncio.to_thread(svc.bulk_upsert, user.tenant_id, rows)
    return ok({"imported": count, "total_rows": len(rows)})


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


@router.get("/status")
def inventory_status(
    session_id: str = Query(..., description="Completed forecast session to base recommendations on"),
    service_level: float = Query(default=0.95, ge=0.5, le=0.999),
    signal: Optional[str] = Query(default=None, description="Filter by signal: PEDIR_YA, PEDIR_PRONTO, OK, SOBRESTOCK, SIN_DATOS"),
    supplier: Optional[str] = Query(default=None),
    by_warehouse: bool = Query(default=False, description="Per-(sku, warehouse) rows with network transfer suggestions"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Returns per-SKU inventory status:
    - days of coverage
    - traffic-light signal (PEDIR_YA / PEDIR_PRONTO / OK / SOBRESTOCK / SIN_DATOS)
    - recommended order quantity
    - inventory value
    """
    # Both views share the source-then-filter shape; only the response
    # envelope differs. abc/xyz stripping applies to the aggregated view only
    # — per-warehouse rows never carry classification fields.
    if by_warehouse:
        items = svc.get_inventory_status_by_warehouse(user.tenant_id, session_id, service_level)
    else:
        items = svc.get_inventory_status(user.tenant_id, session_id, service_level)
        items = _strip_abc_xyz_unless_entitled(items, user.tenant_id)

    if signal:
        signal_up = signal.upper()
        items = [i for i in items if i["signal"] == signal_up]

    if supplier:
        items = [i for i in items if (i.get("supplier") or "").lower() == supplier.lower()]

    if by_warehouse:
        return ok({
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
            raise HTTPException(status_code=422, detail="occurred_at debe ser fecha ISO (YYYY-MM-DD)")

    try:
        row = shrinkage_svc.record_shrinkage(
            user.tenant_id, body.sku, body.quantity, body.reason,
            user_id=user.user_id, warehouse=body.warehouse, notes=body.notes,
            occurred_at=occurred_at,
        )
    except ValueError as e:
        status = 404 if "no encontrado" in str(e) else 422
        raise HTTPException(status_code=status, detail=str(e))
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
    active:     Optional[bool]  = None

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
    "¿Qué pasa si…?" — project a promo/season's impact per SKU: extra demand,
    stock survival, quantity to order and the latest order date. Read-only.
    """
    if body.event_id:
        ev = svc.get_event(user.tenant_id, body.event_id)
        if not ev:
            raise HTTPException(status_code=404, detail="Evento no encontrado")
        start, end = str(ev["start_date"]), str(ev["end_date"])
        mult = float(body.multiplier or ev.get("multiplier") or 1.0)
        name = body.name or ev.get("name")
    else:
        if not (body.start_date and body.end_date and body.multiplier):
            raise HTTPException(
                status_code=422,
                detail="Sin event_id se requieren start_date, end_date y multiplier",
            )
        start, end, mult, name = body.start_date, body.end_date, body.multiplier, body.name

    try:
        result = svc.simulate_event_impact(
            user.tenant_id, body.session_id, start, end, mult,
            event_name=name, event_id=body.event_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return ok(result)


# ── Per-product event multipliers ────────────────────────────────────────────

class EventMultiplierUpsert(BaseModel):
    scope:       str    # 'sku' | 'category'
    scope_value: str
    multiplier:  float = Field(ge=0.1, le=10.0)


@router.get("/events/{event_id}/multipliers", dependencies=[Depends(require_feature(Feature.EVENT_SIMULATOR))])
def list_event_multipliers(event_id: str, user: CurrentUser = Depends(get_current_user)):
    """Per-SKU or per-category multiplier overrides for this event."""
    if not svc.get_event(user.tenant_id, event_id):
        raise HTTPException(status_code=404, detail="Evento no encontrado")
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
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    try:
        row = svc.set_event_multiplier(
            user.tenant_id, event_id, body.scope, body.scope_value, body.multiplier,
        )
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
        raise HTTPException(status_code=404, detail="Override no encontrado")


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
            raise HTTPException(status_code=404, detail="Event not found")
        effective_start = data.get("start_date", str(existing["start_date"]))
        effective_end = data.get("end_date", str(existing["end_date"]))
        if date.fromisoformat(effective_end) < date.fromisoformat(effective_start):
            raise HTTPException(status_code=422, detail="end_date must not be before start_date")

    ev = svc.update_event(user.tenant_id, event_id, data)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
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
        raise HTTPException(
            status_code=422,
            detail=f"País '{country}' sin catálogo. Disponibles: {', '.join(cat.SUPPORTED_COUNTRIES)}",
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
        raise HTTPException(status_code=404, detail="Evento del catálogo no encontrado")
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
    signal:               Optional[str]   = None
    recommended_qty: float           = Field(default=0, ge=0)
    final_qty:       float           = Field(default=0, ge=0)
    unit_cost:       Optional[float] = Field(default=None, ge=0)
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
        raise HTTPException(status_code=404, detail="Orden de compra no encontrada")
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
            raise HTTPException(status_code=422, detail="received_at debe ser fecha ISO (YYYY-MM-DD)")

    lines = [l.model_dump() for l in body.lines] if (body and body.lines is not None) else None
    try:
        result = rec_svc.receive_po(
            user.tenant_id, po_log_id, user.user_id,
            lines=lines, received_at=received_at,
        )
    except ValueError as e:
        status = 404 if "no encontrada" in str(e) else 409 if "ya fue recibida" in str(e) else 422
        raise HTTPException(status_code=status, detail=str(e))
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
    WhatsApp on file, or a supplier name on PO lines with no ficha at all
    (feature 2.5).

    Returns BOTH relevant and dormant cases, each carrying
    `en_ordenes_pendientes` / `ordenes_pendientes`, rather than pre-filtering
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
    raise HTTPException(status_code=404, detail="PO PDF not found")


@router.post("/po/{po_log_id}/send", status_code=200)
def send_po_to_suppliers(
    po_log_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
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
        raise HTTPException(status_code=404, detail="Orden de compra no encontrada")

    items = rec_svc.get_po_items(user.tenant_id, po_log_id)
    ordered = [i for i in items if i["status"] in ("approved", "modified")]

    by_supplier: dict[str, list[dict]] = {}
    for i in ordered:
        name = (i.get("supplier") or "").strip()
        if not name:
            continue
        by_supplier.setdefault(name, []).append(i)

    sent: list[dict] = []
    skipped: list[dict] = []
    po_meta = {
        "generated_at": po["generated_at"].isoformat() if po.get("generated_at") else None,
        "po_log_id": po_log_id,
    }

    for supplier_name, supplier_items in by_supplier.items():
        supplier = sup_svc.get_supplier_by_name(user.tenant_id, supplier_name)
        if not supplier or not (supplier.get("email") or supplier.get("whatsapp")):
            skipped.append({"supplier": supplier_name, "reason": "Sin datos de contacto en la ficha del proveedor"})
            continue

        pdf_path = po_pdf.generate_po_pdf(user.tenant_id, po_log_id, supplier_name, supplier_items, po_meta)
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

        sent.append({"supplier": supplier_name, "email": email_ok, "whatsapp": whatsapp_ok})

    # Stamp the send time once anything actually left: the payment clock starts
    # here, so the cash calendar (3.6) has no due date to compute without it.
    # Only on a real delivery — a PO that reached nobody owes nobody. Kept
    # idempotent (sent_at IS NULL) so re-sending does not push the due dates of
    # invoices the supplier already issued.
    if any(s["email"] or s["whatsapp"] for s in sent):
        rec_svc.mark_po_sent(user.tenant_id, po_log_id)

    return ok({"sent": sent, "skipped": skipped})


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
        raise HTTPException(status_code=404, detail="Proveedor no encontrado")
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
        raise HTTPException(status_code=404, detail="Escala de precio no encontrada")
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
            result = optimize(inp)
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
        raise HTTPException(status_code=404, detail="Supplier not found")
    return ok(supplier)


@router.delete("/suppliers/{supplier_id}", status_code=204)
def delete_supplier(supplier_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    existing = sup_svc.get_supplier(user.tenant_id, supplier_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Supplier not found")
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
        raise HTTPException(status_code=422, detail="Warehouse name is required")
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
    notes: Optional[str] = None


class TransferReceive(BaseModel):
    lines: Optional[list[dict]] = None  # [{sku, received_qty}] | null = all


def _svc_error(e: ValueError) -> HTTPException:
    """Service-layer ValueError → HTTP: 'not found' wording means 404, the
    rest is a rejected request. Shared by the transfer and warehouse routes."""
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
        raise HTTPException(status_code=404, detail="Supplier not found")
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
    user: CurrentUser = Depends(require_analyst_or_above),
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
    emails = svc.get_tenant_admin_emails(user.tenant_id)
    for email in emails:
        try:
            send_inventory_alert_email(
                to=email, critical_items=critical[:10], warning_items=warning[:5],
                inventory_url=inventory_url,
            )
        except Exception as e:
            log.warning("alert test email failed to=%s: %s", email, e)

    wa_sent = 0
    tenant = get_tenant(user.tenant_id) or {}
    if has_feature(tenant, Feature.WHATSAPP_ALERTS):
        from backend.notifications.whatsapp import build_inventory_alert_text, send_whatsapp

        numbers = svc.get_tenant_admin_whatsapps(user.tenant_id)
        if numbers:
            text = build_inventory_alert_text(critical[:10], warning[:5], inventory_url)
            wa_sent = sum(1 for n in numbers if send_whatsapp(n, text))

    return ok({
        "sent": True,
        "critical": len(critical),
        "warning": len(warning),
        "emails_attempted": len(emails),
        "whatsapp_sent": wa_sent,
    })


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
    user: CurrentUser = Depends(require_analyst_or_above),
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
                'action_suggested': (
                    'Devolver al proveedor' if item.get('abc') == 'C' else
                    'Ofrecer descuento' if item.get('abc') == 'B' else
                    'Revisar con ventas'
                ),
            })

    dead_items.sort(key=lambda x: x['capital_trapped'], reverse=True)
    dead_items = _strip_abc_xyz_unless_entitled(
        dead_items, user.tenant_id, extra_keys=("action_suggested",)
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
    writer.writerow([
        "SKU", "Nombre", "Proveedor", "Señal",
        "Stock actual", "Días cobertura", "Demanda (lead time)",
        "Lead time (días)", "Origen lead time",
        "Cantidad recomendada", "MOQ", "Costo unitario", "Valor orden",
    ])
    for i in po_items:
        qty   = i.get("recommended_qty") or 0
        cost  = i.get("unit_cost")
        value = round(qty * cost, 2) if cost else ""
        # Label where the lead time came from so the buyer can trust (or
        # question) it — same distinction the /hoy and /inventory screens show.
        lead_origin = "Aprendido" if i.get("lead_time_source") == "learned" else "Configurado"
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
    session_id:   str = Query(...),
    # 30 (the max allowed) rather than a shorter default: the optimizer's own
    # lead-time gating (ForecastingCore business/optimizer.py build_problem)
    # blocks EVERY order bucket for a SKU whenever its lead_time_buckets >=
    # horizon_days, and optimizer_service's fallback lead time when a SKU has
    # no lead_time_days data is 15 days — a shorter default horizon would put
    # any under-configured SKU in a total lockout (real shortage, zero
    # possible recommendation) rather than just a smaller ordering window.
    horizon_days: int = Query(default=30, ge=1, le=30),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Runs the MILP purchasing/transfers optimizer for this session and
    returns suggested purchase quantities per SKU x warehouse, plus
    recommended inter-warehouse transfers, collapsed to one total per
    line over the full horizon.
    """
    from forecasting_core.business.optimizer import optimize

    # Read the inventory snapshot once and thread it through both build and
    # serialize — the endpoint used to call list_stock twice (once inside
    # build_optimization_input, once here), doubling this path's pooled-
    # connection checkouts for no benefit.
    try:
        stock_rows = svc.list_stock(user.tenant_id)
        inp = opt_svc.build_optimization_input(
            user.tenant_id, session_id, horizon_days, stock_rows=stock_rows,
        )
    except PoolError:
        # The DB pool (ThreadedConnectionPool, max=10) raises rather than
        # blocking once every connection is checked out, so a concurrent burst
        # can momentarily starve this request. That is transient and retryable,
        # not a server bug — surface 503 (retry) instead of a bare 500.
        raise HTTPException(
            status_code=503,
            detail="Optimizer temporarily unavailable (database busy); please retry.",
        )

    if inp is None:
        return ok({
            "status": "optimal", "total_cost": 0.0, "horizon_days": horizon_days,
            "orders": [], "transfers": [],
        })

    # optimize() never raises on structurally-valid-but-degenerate input
    # (infeasible/unbounded/oversized LP all degrade to a "fallback" result),
    # so a genuine 500 here would only come from an unexpected programming
    # error, which should stay a 500 rather than be masked.
    result = optimize(inp)
    return ok(opt_svc.serialize_optimization_result(inp, result, stock_rows))
