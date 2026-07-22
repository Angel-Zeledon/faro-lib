"""
Warehouse management service.

Thin CRUD over the `warehouses` table. Warehouses are also auto-created
implicitly by inventory_stock upserts (see `service._ensure_warehouse`) —
this module exposes the same table for direct listing/creation from the API.
"""

import logging

from backend.db.connection import execute, query, query_one

log = logging.getLogger(__name__)

# Name of the auto-created default warehouse. Every fallback that used to
# hardcode 'principal' (reception, PO lines, sync CSV, per-warehouse status)
# points here so the definition of "default warehouse" has a single owner.
# The DB-side DEFAULT on inventory_stock.warehouse must match (migrations.py).
DEFAULT_WAREHOUSE = "principal"


def name_precedence_key(name: str | None) -> tuple:
    """Sort key for default-warehouse precedence by NAME only:
    DEFAULT_WAREHOUSE first, then case-insensitive alphabetical order.

    Name-based on purpose: hot read paths (service._aggregate_stock_rows_by_sku
    runs once per status request over every stock row) only have stock rows in
    hand, and consulting warehouses.is_default there would add a DB round-trip
    to a pure in-memory aggregation. Paths that already load warehouse rows
    (get_demand_shares) check is_default explicitly BEFORE falling back to
    this key. The casefold matters: a case-sensitive sort lets any Capitalized
    name — 'Tienda Norte' < 'principal' in ASCII — beat lowercase names it
    should follow alphabetically.
    """
    return (name != DEFAULT_WAREHOUSE, (name or "").casefold())


def resolve_canonical_name(tenant_id: str, name: str | None) -> str:
    """Normalize a warehouse name at a WRITE boundary (normalize-at-write).

    Strips whitespace and, when the tenant already has a warehouse under a
    different casing, returns THAT existing spelling — so 'norte' lands on the
    existing 'Norte' warehouse/stock rows instead of creating a case-variant
    duplicate (every other query in the codebase matches names exactly).
    An unknown name is returned stripped and becomes the canonical spelling on
    first use. Empty/missing names fall back to DEFAULT_WAREHOUSE, matching
    upsert_stock's historical default. Read paths are NOT normalized.
    """
    stripped = (name or "").strip()
    if not stripped:
        return DEFAULT_WAREHOUSE
    row = query_one(
        "SELECT name FROM warehouses WHERE tenant_id = %s AND LOWER(name) = LOWER(%s) "
        "ORDER BY name LIMIT 1",
        (tenant_id, stripped),
    )
    return row["name"] if row else stripped


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


def set_demand_share(tenant_id: str, name: str, share: float | None) -> dict:
    """
    Store the manual demand share (0-100, or None to clear) for one warehouse.
    Used only by tenants whose sales history has no store column — see
    get_demand_shares() for how raw values become the actual split.
    """
    if share is not None and not (0 <= float(share) <= 100):
        raise ValueError("demand_share must be between 0 and 100")
    # Single round-trip: RETURNING doubles as the existence check.
    row = query_one(
        "UPDATE warehouses SET demand_share = %s WHERE tenant_id = %s AND name = %s RETURNING *",
        (share, tenant_id, name),
    )
    if not row:
        raise ValueError(f"Warehouse '{name}' not found")
    return row


def get_demand_shares(tenant_id: str) -> dict[str, float]:
    """
    Warehouse name -> demand fraction (sums to 1.0).

    Shares are normalized over the warehouses that have a non-NULL
    demand_share. With none set anywhere, the whole demand belongs to the
    default warehouse (falling back to the alphabetically-first one) — which
    is exactly the pre-multi-warehouse behavior for mono-warehouse tenants.
    """
    rows = list_warehouses(tenant_id)
    if not rows:
        return {}
    set_rows = [r for r in rows if r.get("demand_share") is not None]
    total = sum(float(r["demand_share"]) for r in set_rows)
    if set_rows and total > 0:
        return {r["name"]: float(r["demand_share"]) / total for r in set_rows}
    # Fallback precedence: explicit is_default flag, then name_precedence_key
    # — the shared name-based ordering (DEFAULT_WAREHOUSE first, then
    # casefolded alphabetical; see its docstring for the 'Tienda Norte' <
    # 'principal' ASCII trap) also used by _aggregate_stock_rows_by_sku, so
    # "which warehouse is the default" is answered identically everywhere.
    default = (
        next((r for r in rows if r.get("is_default")), None)
        or sorted(rows, key=lambda r: name_precedence_key(r["name"]))[0]
    )
    return {default["name"]: 1.0}


def create_warehouse(tenant_id: str, name: str, is_default: bool = False) -> dict:
    """Idempotent create: if a warehouse with this name already exists for the
    tenant, returns the existing row unchanged rather than 409ing (matches the
    ON CONFLICT ... DO NOTHING pattern already used for auto-created
    warehouses in `service._ensure_warehouse`).

    KNOWN LIMITATION: on a name collision, the requested `is_default` value is
    silently discarded — the existing row's `is_default` wins. No caller flips
    an existing warehouse's default flag yet; if one is added, this function
    needs an explicit UPDATE path for that case."""
    # Normalize-at-write: 'norte' must reuse an existing 'Norte' row, never
    # create a case-variant duplicate location.
    name = resolve_canonical_name(tenant_id, name)
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


