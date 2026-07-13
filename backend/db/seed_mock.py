"""
Idempotent, coherent multi-warehouse mock-data seeder.

Populates a tenant's `warehouses` and `inventory_stock` tables (and,
best-effort, `suppliers`) with a deterministic, business-logic-consistent
demo dataset: several warehouses, a batch of SKUs each with a coherent
cost/price/lead-time, and stock spread unevenly across warehouses so a demo
tenant actually shows multi-warehouse variance instead of copy-pasted rows.

Scope note — no sales-history table exists in this schema (verified: no
`CREATE TABLE.*sales` in backend/db/migrations.py). Sales history only ever
lives inside uploaded dataset CSV/Excel files attached to a training
session, never as DB rows. Per the Multi-Warehouse Foundation plan's own
instruction, this seeder does NOT invent a sales-history schema — it is
scoped to `warehouses` + `inventory_stock` (+ `suppliers`, which is simple
and already exists) and reports `sales_history_seeded: False` explicitly
rather than silently skipping it.

Idempotency:
- `warehouses` rows go through `warehouse_service.create_warehouse`, which
  is itself `ON CONFLICT (tenant_id, name) DO NOTHING`.
- `inventory_stock` rows use deterministic SKU ids (`MOCK_001`, `MOCK_002`,
  ...) written through `inventory.service.upsert_stock`, whose
  `ON CONFLICT (tenant_id, sku, bodega) DO UPDATE` overwrites in place.
- `suppliers` rows are `ON CONFLICT (tenant_id, name) DO NOTHING`.
- The RNG is seeded deterministically from `tenant_id`, so a re-run
  generates the exact same values and overwrites rows with themselves —
  row counts (and content) never grow on re-run.

Run standalone:
    python -m backend.db.seed_mock <tenant_id> [warehouses] [skus]
"""

import logging
import random
import sys

from backend.db.connection import execute
from backend.inventory import service as inventory_service
from backend.inventory import warehouse_service

log = logging.getLogger(__name__)

DEFAULT_WAREHOUSES = ["principal", "Norte", "Sur"]

# Relative size of each named warehouse, used to keep stock levels
# realistic (a "Sur" satellite warehouse should not carry as much stock as
# "principal") rather than drawing every warehouse from the same range.
_WAREHOUSE_SIZE_FACTOR = {"principal": 1.0, "Norte": 0.55, "Sur": 0.3}

_SUPPLIERS = [
    {"name": "Distribuidora Andina S.A.", "lead_time_dias": 7, "lead_time_std": 2},
    {"name": "ImportMax LatAm", "lead_time_dias": 15, "lead_time_std": 4},
    {"name": "Suministros del Pacifico", "lead_time_dias": 21, "lead_time_std": 5},
]


def _ensure_suppliers(tenant_id: str) -> list[str]:
    """Best-effort: create a small fixed set of suppliers for this tenant.
    Never raises — a suppliers hiccup must not block stock seeding, which
    is this module's core responsibility. Idempotent via ON CONFLICT."""
    names: list[str] = []
    for s in _SUPPLIERS:
        try:
            execute(
                "INSERT INTO suppliers (tenant_id, name, lead_time_dias, lead_time_std) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (tenant_id, name) DO NOTHING",
                (tenant_id, s["name"], s["lead_time_dias"], s["lead_time_std"]),
            )
            names.append(s["name"])
        except Exception as e:
            log.warning(
                "seed_mock: failed to upsert supplier=%s tenant=%s err=%s",
                s["name"], tenant_id, e,
            )
    return names


def _warehouse_names(count: int) -> list[str]:
    names = list(DEFAULT_WAREHOUSES)
    extra_idx = 1
    while len(names) < count:
        names.append(f"Extra{extra_idx}")
        extra_idx += 1
    return names[: max(count, 1)]


def seed_mock_tenant(
    tenant_id: str, *, warehouses: int = 3, skus: int = 12, days: int = 120
) -> dict:
    """
    Populate `tenant_id` with a coherent, idempotent multi-warehouse demo
    dataset: `warehouses` warehouse rows and `skus` SKUs spread unevenly
    across them, with coherent cost/price/lead-time. Safe to call
    repeatedly for the same tenant — see module docstring for why.

    `days` is accepted for interface compatibility with the plan's declared
    signature but is currently unused: there is no sales-history table in
    this schema to backfill (see module docstring).
    """
    # Deterministic per tenant: re-running produces the exact same values,
    # so upserts overwrite rows with themselves rather than drifting.
    rng = random.Random(f"faro-mock-seed-{tenant_id}")

    wh_names = _warehouse_names(warehouses)
    for i, name in enumerate(wh_names):
        warehouse_service.create_warehouse(tenant_id, name, is_default=(i == 0))

    # Any warehouse name beyond the three known ones gets a deterministic
    # (but tenant-varying) relative size factor instead of a hardcoded one.
    wh_factors = {
        name: _WAREHOUSE_SIZE_FACTOR.get(name, round(rng.uniform(0.15, 0.85), 2))
        for name in wh_names
    }

    supplier_names = _ensure_suppliers(tenant_id)

    stock_rows_written = 0
    for i in range(1, skus + 1):
        sku = f"MOCK_{i:03d}"
        costo = round(rng.uniform(5.0, 500.0), 2)
        precio = round(costo * rng.uniform(1.2, 2.0), 2)  # always > costo
        lead_time = rng.randint(3, 30)
        moq = rng.choice([1, 5, 10, 20, 50])
        proveedor = supplier_names[i % len(supplier_names)] if supplier_names else None

        # One base stock level per SKU, scaled per warehouse by its size
        # factor plus a little jitter — realistic variance instead of
        # independently-random (and occasionally identical) draws per
        # warehouse.
        sku_base_stock = rng.randint(60, 400)

        for wh in wh_names:
            jitter = rng.randint(-10, 10)
            stock_actual = max(0, round(sku_base_stock * wh_factors[wh]) + jitter)
            data = {
                "display_name": f"Producto Demo {i:03d}",
                "stock_actual": stock_actual,
                "stock_minimo": rng.randint(5, 60),
                "lead_time_dias": lead_time,
                "costo_unitario": costo,
                "precio_venta": precio,
                "moq": moq,
                "bodega": wh,
            }
            if proveedor:
                data["proveedor"] = proveedor
            inventory_service.upsert_stock(tenant_id, sku, data)
            stock_rows_written += 1

    return {
        "tenant_id": tenant_id,
        "warehouses": len(wh_names),
        "skus": skus,
        "stock_rows": stock_rows_written,
        "suppliers": len(supplier_names),
        "sales_history_seeded": False,
        "note": (
            "No sales-history table exists in this schema — sales history "
            "only ever lives in uploaded dataset CSV/Excel files attached "
            "to a training session, never as DB rows. Intentionally not "
            "seeded; see module docstring."
        ),
    }


def _main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m backend.db.seed_mock <tenant_id> [warehouses] [skus]")
        sys.exit(1)

    # Standalone CLI use (not under pytest, where the `app`/`client` fixture
    # already initializes the pool): connect using the same settings the
    # app itself uses.
    from backend.config import settings
    from backend.db.connection import init_pool

    init_pool(settings.database_url)

    tenant_id = sys.argv[1]
    kwargs: dict = {}
    if len(sys.argv) > 2:
        kwargs["warehouses"] = int(sys.argv[2])
    if len(sys.argv) > 3:
        kwargs["skus"] = int(sys.argv[3])

    summary = seed_mock_tenant(tenant_id, **kwargs)
    print(summary)


if __name__ == "__main__":
    _main()
