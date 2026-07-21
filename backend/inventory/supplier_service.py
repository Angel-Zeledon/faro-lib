"""
Supplier management service.

CRUD for suppliers and sku_suppliers join table.
"""

import logging
from typing import Optional

from backend.db.connection import query, query_one, execute

log = logging.getLogger(__name__)


# ── Suppliers ─────────────────────────────────────────────────────────────────

def list_suppliers(tenant_id: str) -> list[dict]:
    return query(
        "SELECT * FROM suppliers WHERE tenant_id = %s AND active = TRUE ORDER BY name",
        (tenant_id,),
    )


def get_supplier(tenant_id: str, supplier_id: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM suppliers WHERE tenant_id = %s AND id = %s AND active = TRUE",
        (tenant_id, supplier_id),
    )


def get_supplier_by_name(tenant_id: str, name: Optional[str]) -> Optional[dict]:
    """Case-insensitive lookup by name — PO line items store a free-text
    supplier name, not a supplier_id, so sending a PO to its supplier
    needs to resolve that name back to a supplier record. Excludes
    soft-deleted suppliers (active = TRUE), matching get_supplier/
    list_suppliers — a PO shouldn't get auto-sent to a supplier the
    business explicitly deactivated."""
    if not name:
        return None
    return query_one(
        "SELECT * FROM suppliers WHERE tenant_id = %s AND LOWER(name) = LOWER(%s) AND active = TRUE",
        (tenant_id, name),
    )


def create_supplier(tenant_id: str, data: dict) -> dict:
    allowed = {"name", "email", "phone", "whatsapp", "lead_time_days", "lead_time_std",
               "payment_terms", "payment_terms_days", "notes"}
    safe = {k: v for k, v in data.items() if k in allowed}

    cols = ", ".join(safe.keys())
    phs  = ", ".join(["%s"] * len(safe))
    values = list(safe.values())

    row = query_one(
        f"""INSERT INTO suppliers (tenant_id, {cols})
            VALUES (%s, {phs})
            RETURNING *""",
        (tenant_id, *values),
    )
    return row  # type: ignore[return-value]


def update_supplier(tenant_id: str, supplier_id: str, data: dict) -> Optional[dict]:
    allowed = {"name", "email", "phone", "whatsapp", "lead_time_days", "lead_time_std",
               "payment_terms", "payment_terms_days", "notes"}
    safe = {k: v for k, v in data.items() if k in allowed}
    if not safe:
        return get_supplier(tenant_id, supplier_id)

    sets   = ", ".join(f"{k} = %s" for k in safe)
    values = list(safe.values())

    return query_one(
        f"UPDATE suppliers SET {sets} WHERE tenant_id = %s AND id = %s AND active = TRUE RETURNING *",
        (*values, tenant_id, supplier_id),
    )


def delete_supplier(tenant_id: str, supplier_id: str) -> None:
    execute(
        "UPDATE suppliers SET active = FALSE WHERE tenant_id = %s AND id = %s",
        (tenant_id, supplier_id),
    )


# ── SKU–Supplier links ────────────────────────────────────────────────────────

def get_sku_suppliers(tenant_id: str, sku: str) -> list[dict]:
    return query(
        """SELECT
               ss.id,
               ss.sku,
               ss.supplier_id,
               ss.is_primary,
               ss.unit_cost,
               ss.moq,
               ss.lead_time_days,
               ss.notes,
               ss.created_at,
               s.name        AS supplier_name,
               s.email       AS supplier_email,
               s.phone       AS supplier_phone,
               COALESCE(ss.lead_time_days, s.lead_time_days) AS effective_lead_time
           FROM sku_suppliers ss
           JOIN suppliers s ON s.id = ss.supplier_id
           WHERE ss.tenant_id = %s AND ss.sku = %s AND s.active = TRUE
           ORDER BY ss.is_primary DESC, s.name""",
        (tenant_id, sku),
    )


def upsert_sku_supplier(tenant_id: str, sku: str, supplier_id: str, data: dict) -> dict:
    allowed = {"is_primary", "unit_cost", "moq", "lead_time_days", "notes"}
    safe = {k: v for k, v in data.items() if k in allowed}

    # Build the ON CONFLICT branch. When there is nothing to set, use DO
    # NOTHING rather than a placeholder "is_primary = EXCLUDED.is_primary":
    # since is_primary isn't in the INSERT column list on a conflict, EXCLUDED
    # would resolve to the column's schema default (TRUE), silently flipping
    # an existing row's is_primary back to TRUE even when it was explicitly
    # FALSE — an unconditional write where update_supplier's sibling behavior
    # (no-op on empty data) is what callers expect.
    if safe:
        upd_sets = ", ".join(f"{k} = EXCLUDED.{k}" for k in safe)
        conflict_action = f"DO UPDATE SET {upd_sets}"
    else:
        conflict_action = "DO NOTHING"

    cols   = ", ".join(["tenant_id", "sku", "supplier_id"] + list(safe.keys()))
    phs    = ", ".join(["%s"] * (3 + len(safe)))
    values = [tenant_id, sku, supplier_id] + list(safe.values())

    row = query_one(
        f"""INSERT INTO sku_suppliers ({cols})
            VALUES ({phs})
            ON CONFLICT (tenant_id, sku, supplier_id) {conflict_action}
            RETURNING *""",
        tuple(values),
    )
    # Enrich with supplier details
    rows = get_sku_suppliers(tenant_id, sku)
    for r in rows:
        if r["supplier_id"] == supplier_id:
            return r
    return row  # type: ignore[return-value]


def remove_sku_supplier(tenant_id: str, sku: str, supplier_id: str) -> None:
    execute(
        "DELETE FROM sku_suppliers WHERE tenant_id = %s AND sku = %s AND supplier_id = %s",
        (tenant_id, sku, supplier_id),
    )


def get_primary_supplier(tenant_id: str, sku: str) -> Optional[dict]:
    """Returns the primary supplier for a SKU with effective lead_time_days."""
    return query_one(
        """SELECT
               ss.id,
               ss.sku,
               ss.supplier_id,
               ss.is_primary,
               ss.unit_cost,
               ss.moq,
               ss.lead_time_days,
               ss.notes,
               s.name        AS supplier_name,
               s.email       AS supplier_email,
               s.phone       AS supplier_phone,
               COALESCE(ss.lead_time_days, s.lead_time_days) AS effective_lead_time
           FROM sku_suppliers ss
           JOIN suppliers s ON s.id = ss.supplier_id
           WHERE ss.tenant_id = %s AND ss.sku = %s AND ss.is_primary = TRUE AND s.active = TRUE
           LIMIT 1""",
        (tenant_id, sku),
    )
