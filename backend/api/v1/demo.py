"""
One-click demo (quick-start feature 1.2, docs/features_propuestas_faro_2026-07-05.md).

POST /demo/quickstart seeds everything a new user would otherwise have to
prepare by hand — bundled sales dataset, column mapping, model/validation
configs and per-SKU stock — and queues a real training job. The caller lands
on the inventory semáforo ~2 minutes later without touching a CSV.
"""

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

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

# Stock chosen so the first semáforo shows every state at once:
# SKU-001/002 low vs their daily demand → PEDIR_YA / PEDIR_PRONTO,
# SKU-003/005 healthy → OK, SKU-004 absurdly high → SOBRESTOCK.
_DEMO_STOCK = {
    "SKU-001": {"display_name": "Aceite de Oliva 1L", "current_stock": 40,   "lead_time_days": 10,
                "unit_cost": 8.5, "moq": 12,  "supplier": "Distribuidora Andina"},
    "SKU-002": {"display_name": "Arroz 5kg",          "current_stock": 350,  "lead_time_days": 7,
                "unit_cost": 5.2, "moq": 25,  "supplier": "Granos del Valle"},
    "SKU-003": {"display_name": "Leche Entera 1L",    "current_stock": 2500, "lead_time_days": 5,
                "unit_cost": 1.1, "moq": 50,  "supplier": "Lácteos La Sabana"},
    "SKU-004": {"display_name": "Azucar 2kg",         "current_stock": 9000, "lead_time_days": 15,
                "unit_cost": 2.4, "moq": 100, "supplier": "Granos del Valle"},
    "SKU-005": {"display_name": "Sal 1kg",            "current_stock": 1200, "lead_time_days": 10,
                "unit_cost": 0.9, "moq": 24,  "supplier": "Distribuidora Andina"},
}

# Same defaults the quick-start wizard posts (Frontend quick-start page).
# Lifted into backend/sessions/defaults.py so the accounting-integrations
# sync service can seed the identical six config blobs (pure constant
# extraction — this call returns an equal dict each time).
_DEMO_CONFIGS = default_quickstart_configs()


@router.post("/quickstart", status_code=202)
def demo_quickstart(user: CurrentUser = Depends(require_analyst_or_above)):
    """Seed a complete demo session and start training. Returns {session_id, job_id}."""
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
    s = session_svc.create_session(user.tenant_id, user.user_id, "Demo Faro")
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

    # 4. Train — same path as POST /sessions/{id}/train
    job = job_service.create_job(user.tenant_id, session_id, user.user_id)
    session_svc.set_last_job(user.tenant_id, session_id, job["id"])
    try:
        session_svc.transition(user.tenant_id, session_id, "QUEUED", "training")
    except ValueError:
        pass

    log.info("[demo] tenant=%s session=%s job=%s stock_seeded=%s",
             user.tenant_id, session_id, job["id"], seeded)
    return ok({
        "session_id": session_id,
        "job_id": job["id"],
        "dataset_id": dataset_id,
        "stock_seeded": seeded,
    })
