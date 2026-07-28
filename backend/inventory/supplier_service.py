"""
Supplier management service.

CRUD for suppliers and sku_suppliers join table.
"""

import logging
from typing import Optional

from backend.db.connection import query, query_one, execute
from backend.inventory.defaults import SOURCE_USER

log = logging.getLogger(__name__)


def _stamp_lead_time_provenance(safe: dict, data: dict) -> dict:
    """Mark `suppliers.lead_time_days` as user-set when this call supplies one.

    `suppliers.lead_time_days` is `INT NOT NULL DEFAULT 15`, so every supplier
    row carries a lead time whether or not anybody chose it. That column now
    feeds the planning cascade (one supplier rule covering all of that
    supplier's SKUs — a distributor has 12 suppliers, not 2.000 lead times), and
    without this stamp merely creating a supplier would impose a 15 that the SKU
    card would then report as 'supplier_rule'. That is the same false claim of
    authorship the inventory_stock provenance migration exists to kill, one
    table over. `stock_defaults_service.build_rule_index` only reads the column
    when this flag is set.
    """
    if data.get("lead_time_days") is not None:
        return {**safe, "lead_time_set_by": SOURCE_USER}
    return safe


# ── Suppliers ─────────────────────────────────────────────────────────────────

def list_suppliers(tenant_id: str) -> list[dict]:
    """Active suppliers, each carrying the state of the lead-time learning.

    `service.resolve_lead_time` already replaces the configured lead time with
    the one learned from a supplier's real receptions, but only once there are
    MIN_LEAD_TIME_OBSERVATIONS of them. A new tenant never clears that bar, so
    every SKU fell back to our assumption and no screen said the learning
    existed at all — a silent default the buyer had no reason to expect would
    ever improve.

    The three fields below turn it into a promise they can wait for: how many
    of this supplier's deliveries we have recorded, how many we need, and the
    average once we have enough. The threshold travels with the data instead of
    being duplicated in the frontend, so it can never disagree with the number
    the planner actually uses.
    """
    # Imported inside the function: service.py is the heavier module and imports
    # this one, so a module-level import would close the cycle.
    from backend.inventory.service import MIN_LEAD_TIME_OBSERVATIONS

    rows = query(
        """SELECT s.*,
                  COALESCE(o.n, 0) AS lead_time_observations,
                  o.avg_days       AS lead_time_learned_days
           FROM suppliers s
           LEFT JOIN (
               SELECT LOWER(supplier)     AS supplier,
                      COUNT(*)::int       AS n,
                      AVG(lead_time_days) AS avg_days
               FROM supplier_lead_time_obs
               WHERE tenant_id = %s
               GROUP BY LOWER(supplier)
           ) o ON o.supplier = LOWER(s.name)
           WHERE s.tenant_id = %s AND s.active = TRUE
           ORDER BY s.name""",
        (tenant_id, tenant_id),
    )
    for row in rows:
        observations = int(row.get("lead_time_observations") or 0)
        row["lead_time_observations"] = observations
        row["lead_time_observations_needed"] = MIN_LEAD_TIME_OBSERVATIONS
        # Below the threshold the average describes one delivery, not the
        # supplier, and the planner ignores it. Reporting it anyway would show
        # the buyer a number nothing is actually using.
        average = row.get("lead_time_learned_days")
        row["lead_time_learned_days"] = (
            round(float(average), 1)
            if average is not None and observations >= MIN_LEAD_TIME_OBSERVATIONS
            else None
        )
    return rows


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
    safe = _stamp_lead_time_provenance(
        {k: v for k, v in data.items() if k in allowed}, data)

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
    safe = _stamp_lead_time_provenance(
        {k: v for k, v in data.items() if k in allowed}, data)
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


def get_primary_suppliers_map(tenant_id: str) -> dict[str, dict]:
    """{sku: {supplier_id, supplier_name}} for every SKU with a primary
    supplier. Loaded in one query so the recommendation pass — which walks
    every SKU — never degenerates into a per-SKU lookup."""
    rows = query(
        """SELECT ss.sku, ss.supplier_id, s.name AS supplier_name
           FROM sku_suppliers ss
           JOIN suppliers s ON s.id = ss.supplier_id
           WHERE ss.tenant_id = %s AND ss.is_primary = TRUE AND s.active = TRUE""",
        (tenant_id,),
    )
    return {r["sku"]: {"supplier_id": r["supplier_id"],
                       "supplier_name": r["supplier_name"]} for r in rows}


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
