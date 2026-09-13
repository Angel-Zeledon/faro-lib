"""
Supplier management service.

CRUD for suppliers and sku_suppliers join table.
"""

import logging
from typing import Optional

from backend.db.connection import query, query_one, execute, transaction
from backend.errors import AppError
from backend.inventory.defaults import SOURCE_USER

log = logging.getLogger(__name__)

_UNIQUE_VIOLATION = "23505"

# Which of several primaries wins, when the data already holds more than one.
#
# `sku_suppliers.is_primary` is `DEFAULT TRUE` and nothing in the schema stops a
# SKU from having two, so linking a second supplier without naming the flag used
# to make it primary as well. From then on "the primary supplier of this SKU"
# had no answer: the map built a dict (last row of an unordered scan won), the
# single lookup was a `LIMIT 1` with no `ORDER BY`, and the two could disagree
# on the same request — the screen naming one supplier while the recommendation
# was built for another.
#
# OLDEST WINS, on purpose. `upsert_sku_supplier` now demotes the others when you
# explicitly set one, so new data has a single primary; this rule only decides
# rows that already violate the invariant. Picking the newest would let merely
# *listing* another supplier move the whole SKU over to it; picking the oldest
# keeps the answer stable as suppliers are added, which is the property a buyer
# depends on. `supplier_id` breaks the tie if two rows share a timestamp.
_PRIMARY_ORDER = "ss.is_primary DESC, ss.created_at ASC, ss.supplier_id ASC"


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
        #
        # Same reason for the `> 0` guard, and it is not hypothetical: three
        # counter pickups (ordered and collected the same day — routine in this
        # market) average to 0 days. `_effective_lead_time` refuses a
        # non-positive average, so the card was announcing "tardan 0 días en
        # promedio, y ese es el número con el que planifico" about a number the
        # planner had thrown away. Whatever this reports must be what plans.
        average = row.get("lead_time_learned_days")
        usable = average is not None and float(average) > 0
        row["lead_time_learned_days"] = (
            round(float(average), 1)
            if usable and observations >= MIN_LEAD_TIME_OBSERVATIONS
            else None
        )
        # Enough deliveries, none of them usable. Without this the UI reads
        # "3 of 3 recorded — 0 more and we adjust on our own", promising an
        # adjustment that will never come.
        row["lead_time_learned_unusable"] = bool(
            observations >= MIN_LEAD_TIME_OBSERVATIONS and not usable
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


def find_supplier_by_name_any_state(tenant_id: str, name: Optional[str]) -> Optional[dict]:
    """Case-insensitive lookup by name that does NOT filter on `active`.

    The deliberate opposite of `get_supplier_by_name`: that one answers "who
    should receive this PO?" and must skip a supplier the business dropped;
    this one answers "is this name already taken in the database?", and a
    deactivated row still occupies UNIQUE (tenant_id, name).
    """
    if not name:
        return None
    return query_one(
        "SELECT * FROM suppliers WHERE tenant_id = %s AND LOWER(name) = LOWER(%s) "
        "ORDER BY COALESCE(active, TRUE) DESC LIMIT 1",
        (tenant_id, name.strip()),
    )


def assert_name_available(
    tenant_id: str, name: Optional[str], *, exclude_id: Optional[str] = None,
) -> None:
    """Raise a named, translatable error instead of letting the UNIQUE index
    surface as a generic 500.

    Deactivating a supplier is logical (`active = FALSE`) and the unique index
    does not exclude inactive rows, so re-registering a dropped supplier under
    its own name hit a raw UniqueViolation: the buyer saw "error del servidor",
    with nothing naming the row that was in the way — which is invisible,
    because a deactivated supplier is not in any list on screen. The two cases
    get different codes because the user's next move differs: rename, versus
    "that supplier is deactivated, it has to be restored".

    The check is case-INSENSITIVE while the index is not, on purpose: every
    supplier-by-name resolution in the product (PO sending, the cash calendar,
    the lead-time rule index) is case-insensitive, so 'andina' next to 'Andina'
    is two cards competing to answer the same question — an ambiguity worth
    refusing at the door rather than a freedom worth keeping.
    """
    existing = find_supplier_by_name_any_state(tenant_id, name)
    if not existing or (exclude_id and existing["id"] == exclude_id):
        return
    if existing.get("active") is False:
        raise AppError(
            "supplier_name_taken_by_deactivated",
            "A deactivated supplier is already registered under this name",
            status_code=409,
            params={"name": existing["name"]},
        )
    raise AppError(
        "supplier_name_taken",
        "A supplier is already registered under this name",
        status_code=409,
        params={"name": existing["name"]},
    )


def create_supplier(tenant_id: str, data: dict) -> dict:
    allowed = {"name", "email", "phone", "whatsapp", "lead_time_days", "lead_time_std",
               "payment_terms", "payment_terms_days", "notes"}
    safe = _stamp_lead_time_provenance(
        {k: v for k, v in data.items() if k in allowed}, data)

    assert_name_available(tenant_id, safe.get("name"))

    cols = ", ".join(safe.keys())
    phs  = ", ".join(["%s"] * len(safe))
    values = list(safe.values())

    try:
        row = query_one(
            f"""INSERT INTO suppliers (tenant_id, {cols})
                VALUES (%s, {phs})
                RETURNING *""",
            (tenant_id, *values),
        )
    except Exception as exc:
        # The pre-check above loses to a concurrent insert of the same name.
        # Losing that race must read the same as arriving second, not as a 500.
        if getattr(exc, "pgcode", "") != _UNIQUE_VIOLATION:
            raise
        assert_name_available(tenant_id, safe.get("name"))
        raise
    return row  # type: ignore[return-value]


def update_supplier(tenant_id: str, supplier_id: str, data: dict) -> Optional[dict]:
    allowed = {"name", "email", "phone", "whatsapp", "lead_time_days", "lead_time_std",
               "payment_terms", "payment_terms_days", "notes"}
    safe = _stamp_lead_time_provenance(
        {k: v for k, v in data.items() if k in allowed}, data)
    if not safe:
        return get_supplier(tenant_id, supplier_id)

    # A rename hits the same unique index as a create — same 500, same fix.
    if safe.get("name"):
        assert_name_available(tenant_id, safe["name"], exclude_id=supplier_id)

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


def get_supplier_any_state(tenant_id: str, supplier_id: str) -> Optional[dict]:
    """A supplier by id, active or not.

    `get_supplier` filters on `active` because almost everything that reads a
    supplier is about to act towards them. Reactivation is the one operation
    whose whole subject is a row that filter hides.
    """
    return query_one(
        "SELECT * FROM suppliers WHERE tenant_id = %s AND id = %s",
        (tenant_id, supplier_id),
    )


def reactivate_supplier(tenant_id: str, supplier_id: str) -> Optional[dict]:
    """Bring a deactivated supplier back. Idempotent; returns the row.

    Deactivation here is logical and always was, so it has an undo — until this
    existed it did not, and the 409 the create endpoint now returns
    (`supplier_name_taken_by_deactivated`) told the user about a row they had no
    way to act on. That is a dead end the error message itself created.

    There is deliberately NO name re-check here. The obvious worry — that while
    the card sat deactivated somebody registered a live supplier under the same
    name, so bringing it back would put two cards on screen answering the same
    question — cannot happen: `UNIQUE (tenant_id, name)` does not exclude
    inactive rows, so the database itself refuses the second row. That index is
    also exactly why `create_supplier` needs its guard (it turns the resulting
    UniqueViolation into a 409 that says something).

    A check that cannot fire is worse than no check: it reads as protection and
    protects nothing, and the next person to touch this has to prove it is dead
    before they can simplify anything around it. If the index is ever made
    partial (`WHERE active`), this is the function that has to grow the check
    back — `tests/test_supplier_deactivation_is_reversible.py` pins the
    invariant so that day is loud.
    """
    row = get_supplier_any_state(tenant_id, supplier_id)
    if row is None:
        return None
    if row.get("active") is not False:
        return row                      # already live — nothing to undo
    return query_one(
        "UPDATE suppliers SET active = TRUE "
        "WHERE tenant_id = %s AND id = %s RETURNING *",
        (tenant_id, supplier_id),
    )


# ── SKU–Supplier links ────────────────────────────────────────────────────────

def get_sku_suppliers(tenant_id: str, sku: str) -> list[dict]:
    """Every supplier linked to this SKU, the effective primary FIRST.

    Ordered by the same rule `get_primary_suppliers_map` uses, so row `[0]` is
    the supplier the recommendation for this SKU was actually built for. They
    used to sort differently (this one alphabetically), which meant the list
    could name one supplier at the top while planning used another.
    """
    return query(
        f"""SELECT
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

    # A SKU has ONE primary supplier. Nothing in the schema said so — and both
    # `is_primary` defaults (the column's and the request model's) are TRUE — so
    # linking a second supplier used to leave the SKU with two, and "who supplies
    # this SKU" stopped having an answer. Setting one primary now demotes the
    # others, in the same transaction as the write: a reader either sees the old
    # primary or the new one, never two and never none.
    with transaction() as conn:
        if safe.get("is_primary") is True:
            execute(
                """UPDATE sku_suppliers SET is_primary = FALSE
                   WHERE tenant_id = %s AND sku = %s AND supplier_id <> %s
                     AND is_primary = TRUE""",
                (tenant_id, sku, supplier_id), conn=conn,
            )
        row = query_one(
            f"""INSERT INTO sku_suppliers ({cols})
                VALUES ({phs})
                ON CONFLICT (tenant_id, sku, supplier_id) {conflict_action}
                RETURNING *""",
            tuple(values), conn=conn,
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
        f"""SELECT ss.sku, ss.supplier_id, s.name AS supplier_name
           FROM sku_suppliers ss
           JOIN suppliers s ON s.id = ss.supplier_id
           WHERE ss.tenant_id = %s AND ss.is_primary = TRUE AND s.active = TRUE
           ORDER BY {_PRIMARY_ORDER}""",
        (tenant_id,),
    )
    # First row per SKU wins — the ORDER BY above puts the winner first. A dict
    # comprehension would have kept the LAST row instead, which is how this
    # returned a different supplier than `get_primary_supplier` for the same SKU.
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["sku"], {"supplier_id": r["supplier_id"],
                                  "supplier_name": r["supplier_name"]})
    return out


# `get_primary_supplier(tenant_id, sku)` was deleted on 2026-08-23. It had no
# caller anywhere in the repo, and it was a `LIMIT 1` with no `ORDER BY` against
# a table that can hold two primaries — so the day somebody wired it up it would
# have answered a different supplier than `get_primary_suppliers_map` for the
# same SKU, on the same request. Whoever needs a single SKU's primary should
# read it off `get_sku_suppliers(tenant_id, sku)[0]`, which orders by the same
# rule the planning map uses. Dead code that quietly disagrees with live code is
# worse than no code.
