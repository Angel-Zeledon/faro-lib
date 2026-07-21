"""
Warehouse management service.

Thin CRUD over the `warehouses` table. Warehouses are also auto-created
implicitly by inventory_stock upserts (see `service._ensure_warehouse`) —
this module exposes the same table for direct listing/creation from the API.
"""

import logging

from backend.db.connection import execute, query, query_one

log = logging.getLogger(__name__)


def list_warehouses(tenant_id: str) -> list[dict]:
    return query(
        "SELECT * FROM warehouses WHERE tenant_id = %s ORDER BY name",
        (tenant_id,),
    )


def count_warehouses(tenant_id: str) -> int:
    row = query_one("SELECT COUNT(*) AS c FROM warehouses WHERE tenant_id = %s", (tenant_id,))
    return row["c"] if row else 0


def list_warehouse_names(tenant_id: str) -> set[str]:
    """Names of warehouses already on file for this tenant — used by every
    stock-write path (PUT /stock, POST /bulk, PO reception) to figure out
    which warehouse names in an incoming payload are actually NEW, so
    max_locations can be enforced against (current count + new names) before
    any row is written."""
    rows = query("SELECT name FROM warehouses WHERE tenant_id = %s", (tenant_id,))
    return {r["name"] for r in rows}


def get_warehouse_by_name(tenant_id: str, name: str):
    return query_one(
        "SELECT * FROM warehouses WHERE tenant_id = %s AND name = %s",
        (tenant_id, name),
    )


def create_warehouse(tenant_id: str, name: str, is_default: bool = False) -> dict:
    """Idempotent create: if a warehouse with this name already exists for the
    tenant, returns the existing row unchanged rather than 409ing (matches the
    ON CONFLICT ... DO NOTHING pattern already used for auto-created
    warehouses in `service._ensure_warehouse`).

    KNOWN LIMITATION: on a name collision, the requested `is_default` value is
    silently discarded — the existing row's `is_default` wins. No caller flips
    an existing warehouse's default flag yet; if one is added, this function
    needs an explicit UPDATE path for that case."""
    execute(
        "INSERT INTO warehouses (tenant_id, name, is_default) VALUES (%s, %s, %s) "
        "ON CONFLICT (tenant_id, name) DO NOTHING",
        (tenant_id, name, is_default),
    )
    row = query_one(
        "SELECT * FROM warehouses WHERE tenant_id = %s AND name = %s",
        (tenant_id, name),
    )
    return row  # type: ignore[return-value]


def ensure_default_warehouse(tenant_id: str) -> dict:
    """Creates the 'principal' warehouse with is_default=True for this tenant
    if no warehouse exists yet. Returns the default warehouse row."""
    existing = query_one(
        "SELECT * FROM warehouses WHERE tenant_id = %s AND is_default = TRUE",
        (tenant_id,),
    )
    if existing:
        return existing
    any_row = query_one(
        "SELECT * FROM warehouses WHERE tenant_id = %s LIMIT 1",
        (tenant_id,),
    )
    if any_row:
        return any_row
    return create_warehouse(tenant_id, "principal", is_default=True)
