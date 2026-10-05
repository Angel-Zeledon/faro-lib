"""
One-click demo (quick-start feature 1.2 of the 2026-07-05 proposals; that doc
 was retired in the 2026-08-11 docs cleanup and lives in git history).

POST /demo/quickstart seeds everything a new user would otherwise have to
prepare by hand — bundled sales dataset, column mapping, model/validation
configs and per-SKU stock — and queues a real training job. The caller lands
on the inventory semáforo ~2 minutes later without touching a CSV.
"""

import logging
import shutil
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser, require_analyst_or_above
from backend.config import settings
from backend.db import session_store
from backend.db.connection import execute
from backend.inventory import service as inv_svc
from backend.schemas.common import ok
from backend.sessions import service as session_svc
from backend.sessions.defaults import default_quickstart_configs
from backend.storage import paths
from backend.training import job_service
from backend.utils.ids import generate_id

router = APIRouter(prefix="/demo", tags=["demo"])
log = logging.getLogger(__name__)

_DEMO_CSV = Path(__file__).resolve().parents[2] / "resources" / "demo_ventas.csv"

# Stock chosen so the first semáforo reads as a believable, mostly healthy
# business rather than an alarm: of 14 SKUs, one is urgent (PEDIR_YA), three
# are close to their reorder point (PEDIR_PRONTO), eight are covered (OK) and
# two carry more than they need (SOBRESTOCK). Each figure is a COVERAGE chosen
# against the SKU's recent average daily sales in demo_ventas.csv and its lead
# time, using the signal bands of inventory/service.py (PEDIR_YA below half a
# lead time of cover, PEDIR_PRONTO up to the reorder point, OK up to ~3 lead
# times, SOBRESTOCK beyond):
#   urgent ~0.3 x lead time   soon ~0.8 x   covered ~2 x   surplus 6-8 x
# test_demo_and_alerts.py::test_demo_seed_reads_mostly_healthy pins the mix.
_DEMO_STOCK = {
    # urgent
    "SKU-001": {"display_name": "Aceite de Oliva 1L", "current_stock": 100,  "lead_time_days": 10,
                "unit_cost": 8.5, "moq": 12,  "supplier": "Distribuidora Andina"},
    # order soon
    "SKU-002": {"display_name": "Arroz 5kg",          "current_stock": 570,  "lead_time_days": 7,
                "unit_cost": 5.2, "moq": 25,  "supplier": "Granos del Valle"},
    "SKU-009": {"display_name": "Atun en Lata 160g",  "current_stock": 620,  "lead_time_days": 9,
                "unit_cost": 1.3, "moq": 48,  "supplier": "Distribuidora Andina"},
    "SKU-011": {"display_name": "Papel Higienico 12 rollos", "current_stock": 390, "lead_time_days": 14,
                "unit_cost": 6.8, "moq": 12,  "supplier": "Comercial El Sol"},
    # covered
    "SKU-003": {"display_name": "Leche Entera 1L",    "current_stock": 1600, "lead_time_days": 5,
                "unit_cost": 1.1, "moq": 50,  "supplier": "Lácteos La Sabana"},
    "SKU-005": {"display_name": "Sal 1kg",            "current_stock": 790,  "lead_time_days": 10,
                "unit_cost": 0.9, "moq": 24,  "supplier": "Distribuidora Andina"},
    "SKU-006": {"display_name": "Frijoles Negros 1kg", "current_stock": 800, "lead_time_days": 8,
                "unit_cost": 1.6, "moq": 24,  "supplier": "Granos del Valle"},
    "SKU-007": {"display_name": "Pasta Espagueti 400g", "current_stock": 1030, "lead_time_days": 7,
                "unit_cost": 0.8, "moq": 40,  "supplier": "Comercial El Sol"},
    "SKU-008": {"display_name": "Cafe Molido 500g",   "current_stock": 650,  "lead_time_days": 12,
                "unit_cost": 4.1, "moq": 12,  "supplier": "Granos del Valle"},
    "SKU-010": {"display_name": "Jabon de Bano",      "current_stock": 860,  "lead_time_days": 10,
                "unit_cost": 0.7, "moq": 36,  "supplier": "Comercial El Sol"},
    "SKU-013": {"display_name": "Galletas Surtidas",  "current_stock": 670,  "lead_time_days": 6,
                "unit_cost": 1.2, "moq": 24,  "supplier": "Lácteos La Sabana"},
    "SKU-014": {"display_name": "Agua Mineral 600ml", "current_stock": 1800, "lead_time_days": 5,
                "unit_cost": 0.4, "moq": 48,  "supplier": "Lácteos La Sabana"},
    # surplus
    "SKU-004": {"display_name": "Azucar 2kg",         "current_stock": 7000, "lead_time_days": 15,
                "unit_cost": 2.4, "moq": 100, "supplier": "Granos del Valle"},
    "SKU-012": {"display_name": "Detergente 1kg",     "current_stock": 2100, "lead_time_days": 12,
                "unit_cost": 3.2, "moq": 12,  "supplier": "Comercial El Sol"},
}

# Same defaults the quick-start wizard posts (Frontend quick-start page).
# Lifted into backend/sessions/defaults.py so any future auto-provisioning
# path seeds the identical six config blobs rather than its own (pure constant
# extraction — this call returns an equal dict each time).
_DEMO_CONFIGS = default_quickstart_configs()


class DemoQuickstartRequest(BaseModel):
    """Optional launch preferences from the Quick Start wizard (same knobs as
    POST /sessions/{id}/train, plus the session name)."""
    name: Optional[str] = Field(default=None, max_length=200)
    user_horizon_days: Optional[int] = Field(default=None, ge=1, le=365)
    user_granularity: Literal["auto", "daily", "weekly", "monthly"] = "auto"


@router.post("/quickstart", status_code=202)
def demo_quickstart(
    body: Optional[DemoQuickstartRequest] = None,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Seed a complete demo session and start training. Returns {session_id, job_id}."""
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    if not _DEMO_CSV.exists():
        raise HTTPException(status_code=503, detail="Demo dataset not bundled on this server")

    # Concurrency cap shared with the normal /train endpoint
    if not settings.testing_mode:
        active = job_service.count_active_jobs_for_tenant(user.tenant_id)
        if active >= 3:
            raise HTTPException(
                status_code=429,
                detail=f"Too many active training jobs ({active}). Wait for one to finish.",
            )

    # Plan limit: quickstart creates a real session same as POST /sessions,
    # so it must be gated by the same max_sessions cap — otherwise a tenant
    # could walk past its plan limit by repeatedly clicking "try the demo"
    # instead of creating sessions directly.
    from backend.entitlements.service import enforce_limit
    enforce_limit(user.tenant_id, "max_sessions", session_svc.count_sessions(user.tenant_id))

    # Plan limit: quickstart also creates real inventory_stock rows for the
    # fixed _DEMO_STOCK set (step 3 below), all in the "principal" warehouse.
    # Without this pre-check, a tenant near its max_skus/max_locations cap
    # would sail through the dataset/session/config writes above and only get
    # blocked MID-LOOP by upsert_stock's own per-row chokepoint — leaving an
    # orphaned session (MODELS_CONFIGURED) + demo dataset behind, plus 1..4
    # partially-created stock rows and no training job (the same "committed
    # prefix, aborted suffix" bug the receive_po fix addressed). Computed here,
    # before ANY write, mirrors the pre-loop pattern already used by
    # sync_stock_from_dataset / bulk_upsert / receive_po.
    from backend.inventory import warehouse_service as wh_svc
    existing_keys = inv_svc.list_stock_keys(user.tenant_id)
    new_pairs = {(sku, "principal") for sku in _DEMO_STOCK} - existing_keys
    if new_pairs:
        enforce_limit(
            user.tenant_id, "max_skus", inv_svc.count_stock(user.tenant_id),
            adding=len(new_pairs),
        )
        new_warehouses = {"principal"} - wh_svc.list_warehouse_names(user.tenant_id)
        if new_warehouses:
            enforce_limit(
                user.tenant_id, "max_locations", wh_svc.count_warehouses(user.tenant_id),
                adding=len(new_warehouses),
            )

    # 1. Dataset: copy the bundled CSV into the tenant's storage + DB row
    dataset_id = generate_id("ds")
    dst_dir = paths.dataset_dir(user.tenant_id, dataset_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    file_path = dst_dir / "data.csv"
    shutil.copyfile(_DEMO_CSV, file_path)
    execute(
        """INSERT INTO datasets
           (id, tenant_id, name, original_filename, file_type, file_path,
            size_bytes, uploaded_by, uploaded_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
        (dataset_id, user.tenant_id, "demo_ventas", "demo_ventas.csv", "csv",
         str(file_path), _DEMO_CSV.stat().st_size, user.user_id),
    )

    # 2. Session with dataset attached and the quick-start configs pre-seeded
    session_name = ((body.name if body else None) or "").strip() or "Demo StockAI"
    s = session_svc.create_session(user.tenant_id, user.user_id, session_name)
    session_id = s["session_id"] if "session_id" in s else s["id"]
    session_svc.attach_dataset(user.tenant_id, session_id, dataset_id)
    for field, cfg in _DEMO_CONFIGS.items():
        session_store.set_field(user.tenant_id, session_id, field, cfg)
    session_svc.force_status(user.tenant_id, session_id, "MODELS_CONFIGURED")

    # 3. Stock: only for SKUs the tenant doesn't already track, so a demo run
    # never overwrites real inventory data.
    seeded = []
    for sku, stock in _DEMO_STOCK.items():
        if inv_svc.get_stock(user.tenant_id, sku) is None:
            inv_svc.upsert_stock(user.tenant_id, sku, stock)
            seeded.append(sku)

    # 4. Train — fan out into the granularity family (the same path the
    # wizard uses).
    from backend.sessions import family_service as fam
    family = fam.launch_training_family(
        user.tenant_id, session_id, user.user_id,
        user_horizon_days=body.user_horizon_days if body else None,
        user_granularity=body.user_granularity if body else "auto",
    )
    job_id = family["base_job_id"]

    log.info("[demo] tenant=%s session=%s job=%s stock_seeded=%s",
             user.tenant_id, session_id, job_id, seeded)
    return ok({
        "session_id": session_id,
        "job_id": job_id,
        "dataset_id": dataset_id,
        "stock_seeded": seeded,
        # Full granularity family so the onboarding progress can reflect every
        # member (not just the base job) and redirect once the base is ready —
        # symmetric with POST /sessions/{id}/train.
        "family": family,
    })
