"""
Planning defaults by supplier, category or globally — and the cascade that
resolves them.

A distributor does not have 2.000 lead times. It has 12 suppliers. Asking the
buyer to type a lead time, a service level and an MOQ on every SKU card is the
reason the setup never gets finished, and an unfinished setup is the reason the
semáforo runs on our assumptions instead of their data.

Resolution order — narrowest wins, exactly the precedence the event multipliers
already use (`service._resolve_multiplier`):

    SKU row  >  supplier rule  >  category rule  >  global rule  >  system default

and — this is the part that makes it worth building — `resolve_field` returns
WHICH LEVEL WON. That answer is the provenance vocabulary in
`backend/inventory/defaults.py`, so "configure by supplier" and "tell the user
where the number came from" are one mechanism, not two.

The lesson carried over from the multipliers work: state the precedence in the
copy. A user who sees 12 days on a SKU must be able to find out that it came
from their supplier rule and not from the SKU card, or the rule feels like the
app overwriting their input.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.db.connection import query, query_one, execute
from backend.errors import AppError
from backend.inventory.defaults import (
    SOURCE_DEFAULT,
    SOURCE_SUPPLIER_RULE,
    SYSTEM_DEFAULTS,
)

log = logging.getLogger(__name__)

# Narrowest to widest. The order of this tuple IS the cascade below the SKU row.
SCOPE_TYPES: tuple[str, ...] = ("supplier", "category", "global")

# The fields a rule may carry. `unit_cost` is deliberately not among them: a
# per-supplier "cost" is a price list, not a planning default, and it already
# has its own home (`sku_suppliers.unit_cost` / price breaks).
RULE_FIELDS: tuple[str, ...] = ("lead_time_days", "service_level", "moq", "holding_cost_pct")

# Lower bound per rule field, mirroring the floors inventory_stock writes
# enforce. A lead time of 0 collapses every semáforo threshold to 0 and reports
# a stocked-out SKU as SOBRESTOCK; a service level outside (0,1) has no z-score.
_RULE_MIN: dict[str, float] = {"lead_time_days": 1, "service_level": 0.0, "moq": 1.0, "holding_cost_pct": 0.0}
_RULE_MAX: dict[str, float] = {"service_level": 1.0}


def _normalize_scope_value(scope_type: str, scope_value: Optional[str]) -> str:
    """Global rules have no scope value; supplier and category names are stored
    lower-cased so 'Distribuidora Sur' and 'distribuidora sur' cannot become two
    rules that collide on read (the same normalisation `set_event_multiplier`
    does, for the same reason)."""
    if scope_type == "global":
        return ""
    return (scope_value or "").strip().lower()


def list_stock_defaults(tenant_id: str) -> list[dict]:
    return query(
        """SELECT * FROM stock_defaults
           WHERE tenant_id = %s
           ORDER BY scope_type, scope_value""",
        (tenant_id,),
    )


def get_stock_default(tenant_id: str, scope_type: str, scope_value: Optional[str]) -> Optional[dict]:
    return query_one(
        """SELECT * FROM stock_defaults
           WHERE tenant_id = %s AND scope_type = %s AND scope_value = %s""",
        (tenant_id, scope_type, _normalize_scope_value(scope_type, scope_value)),
    )


def set_stock_default(
    tenant_id: str, scope_type: str, scope_value: Optional[str], values: dict,
) -> dict:
    """
    Upsert one rule. Only the keys present in `values` are written, so setting a
    supplier's lead time does not wipe an MOQ the same rule already carried.
    An explicit None clears that field (back to "this rule says nothing").
    """
    if scope_type not in SCOPE_TYPES:
        raise AppError(
            "stock_default_bad_scope",
            f"scope_type must be one of {', '.join(SCOPE_TYPES)}",
            params={"scope_type": scope_type, "allowed": list(SCOPE_TYPES)},
        )
    value = _normalize_scope_value(scope_type, scope_value)
    if scope_type != "global" and not value:
        raise AppError(
            "stock_default_missing_scope_value",
            "A supplier or category rule needs a scope value",
            params={"scope_type": scope_type},
        )

    safe: dict[str, Any] = {}
    for field in RULE_FIELDS:
        if field not in values:
            continue
        raw = values[field]
        if raw is None:
            safe[field] = None
            continue
        try:
            num = float(raw)
        except (TypeError, ValueError):
            raise AppError(
                "stock_default_bad_value",
                f"{field} must be a number",
                params={"field": field, "value": raw},
            )
        low, high = _RULE_MIN.get(field), _RULE_MAX.get(field)
        if (low is not None and num < low) or (high is not None and num > high):
            raise AppError(
                "stock_default_out_of_range",
                f"{field} is out of range",
                params={"field": field, "value": num, "min": low, "max": high},
            )
        safe[field] = int(num) if field == "lead_time_days" else num

    if not safe:
        raise AppError(
            "stock_default_no_fields",
            "No planning fields supplied",
            params={"allowed": list(RULE_FIELDS)},
        )

    cols = ", ".join(safe.keys())
    phs = ", ".join(["%s"] * len(safe))
    upd = ", ".join(f"{k} = EXCLUDED.{k}" for k in safe)
    execute(
        f"""INSERT INTO stock_defaults (tenant_id, scope_type, scope_value, {cols})
            VALUES (%s, %s, %s, {phs})
            ON CONFLICT (tenant_id, scope_type, scope_value)
            DO UPDATE SET {upd}, updated_at = NOW()""",
        (tenant_id, scope_type, value, *safe.values()),
    )
    return get_stock_default(tenant_id, scope_type, value)  # type: ignore[return-value]


def delete_stock_default(tenant_id: str, rule_id: str) -> bool:
    existing = query_one(
        "SELECT id FROM stock_defaults WHERE id = %s AND tenant_id = %s",
        (rule_id, tenant_id),
    )
    if not existing:
        return False
    execute("DELETE FROM stock_defaults WHERE id = %s AND tenant_id = %s", (rule_id, tenant_id))
    return True


def count_affected_skus(tenant_id: str, scope_type: str, scope_value: Optional[str]) -> dict:
    """
    How many SKUs a rule would reach, and how many of those would actually
    CHANGE — i.e. those with no per-SKU value of their own.

    This is the preview the UI must show before confirming. "Applies to 340
    SKUs" is reassuring; "applies to 340, of which 12 already have their own
    lead time and keep it" is what stops the buyer from believing the rule
    silently overwrote their work.
    """
    value = _normalize_scope_value(scope_type, scope_value)
    if scope_type == "supplier":
        where, params = "LOWER(COALESCE(supplier, '')) = %s", (value,)
    elif scope_type == "category":
        where, params = "LOWER(COALESCE(category, '')) = %s", (value,)
    else:
        where, params = "TRUE", ()

    rows = query(
        f"""SELECT COUNT(*)::int AS matched,
                   COUNT(lead_time_set_by)::int AS lead_time_owned,
                   COUNT(service_level_set_by)::int AS service_level_owned,
                   COUNT(moq_set_by)::int AS moq_owned
            FROM inventory_stock
            WHERE tenant_id = %s AND {where}""",
        (tenant_id, *params),
    )
    row = rows[0] if rows else {}
    matched = int(row.get("matched") or 0)
    return {
        "scope_type": scope_type,
        "scope_value": value,
        "matched_skus": matched,
        # Per field: how many of the matched SKUs the rule would actually move.
        "would_change": {
            "lead_time_days": matched - int(row.get("lead_time_owned") or 0),
            "service_level": matched - int(row.get("service_level_owned") or 0),
            "moq": matched - int(row.get("moq_owned") or 0),
        },
    }


# ── Cascade ──────────────────────────────────────────────────────────────────

def build_rule_index(tenant_id: str) -> dict:
    """
    Every rule for the tenant, indexed for O(1) cascade lookups. Loaded ONCE per
    status pass — the recommendation loop walks every SKU and must never
    degenerate into a per-SKU query (the same discipline as
    `get_primary_suppliers_map` and `get_learned_lead_times`).
    """
    idx: dict = {s: {} for s in SCOPE_TYPES}

    # The supplier card's own lead time counts as a supplier rule. It always
    # did for the overdue-receptions screen (`reception_service._effective_lead_time`
    # answered 'declared'), but the semáforo ignored it completely: a buyer who
    # filled in "Distribuidora Sur: 12 días" on the supplier card still got
    # recommendations built on 15 for all 340 of that supplier's SKUs, and no
    # screen explained the contradiction. Loaded FIRST so an explicit
    # stock_defaults rule below overwrites it — the dedicated rule is the more
    # specific statement of intent.
    try:
        # `lead_time_set_by IS NOT NULL` is what keeps this honest:
        # `suppliers.lead_time_days` is NOT NULL DEFAULT 15, so without the
        # provenance flag every supplier row would inject a 15 nobody chose and
        # the SKU would proudly report 'supplier_rule' for our own assumption.
        for s in query(
            "SELECT name, lead_time_days FROM suppliers "
            "WHERE tenant_id = %s AND active = TRUE "
            "  AND lead_time_days IS NOT NULL AND lead_time_set_by IS NOT NULL",
            (tenant_id,),
        ):
            name = (s.get("name") or "").strip().lower()
            if name:
                idx["supplier"][name] = {"lead_time_days": s["lead_time_days"]}
    except Exception as e:
        log.warning("supplier lead-time index unavailable tenant=%s: %s", tenant_id, e)

    try:
        rows = list_stock_defaults(tenant_id)
    except Exception as e:
        # A missing/unmigrated table must degrade to "no rules", never break the
        # whole semáforo.
        log.warning("stock_defaults index unavailable tenant=%s: %s", tenant_id, e)
        return idx
    for r in rows:
        scope = r.get("scope_type")
        if scope not in idx:
            continue
        key = r.get("scope_value") or ""
        if scope == "supplier" and key in idx["supplier"]:
            # Merge rather than replace, so a stock_defaults rule that only sets
            # an MOQ does not discard the supplier card's lead time.
            merged = dict(idx["supplier"][key])
            merged.update({k: v for k, v in r.items() if v is not None})
            idx["supplier"][key] = merged
        else:
            idx[scope][key] = r
    return idx


def _rule_hit(field: str, rule_index: dict, supplier: Optional[str],
              category: Optional[str]) -> tuple[Any, Optional[str]]:
    """The narrowest rule that has something to say about `field`.

    Returns (value, scope) or (None, None). A rule that exists but leaves the
    field NULL says nothing, so the cascade keeps falling through instead of
    stopping at the first matching rule.
    """
    candidates = (
        ("supplier", (supplier or "").strip().lower()),
        ("category", (category or "").strip().lower()),
        ("global", ""),
    )
    for scope, key in candidates:
        if scope != "global" and not key:
            continue
        rule = rule_index.get(scope, {}).get(key)
        if rule is None:
            continue
        val = rule.get(field)
        if val is not None:
            return val, scope
    return None, None


def resolve_field(
    field: str,
    stock: Optional[dict],
    rule_index: dict,
    *,
    supplier: Optional[str] = None,
    category: Optional[str] = None,
) -> tuple[Any, str, Optional[str]]:
    """
    The value a recommendation is actually built on, plus where it came from.

    Returns `(value, source, rule_scope)`:
      - `source` ∈ user | file | supplier_rule | default  (see defaults.py)
      - `rule_scope` ∈ supplier | category | global, set only when a
        `stock_defaults` rule won — this is what lets the copy state the
        precedence explicitly instead of leaving the buyer to guess why their
        SKU shows 12 days.

    'learned' is NOT produced here: it is evidence from real receptions, resolved
    one level above by `service.resolve_lead_time`, which asks this function for
    the configured answer and then decides whether the evidence beats it.

    The per-SKU row wins ONLY when its `<field>_set_by` column says somebody
    actually set it. A row whose lead_time_days is 15 because that is the schema
    default is not a configuration — treating it as one is the exact bug this
    whole change exists to kill.
    """
    if stock is not None:
        set_by = stock.get(f"{_provenance_column(field)}")
        raw = stock.get(field)
        if set_by and raw is not None:
            return raw, set_by, None

    rule_value, rule_scope = _rule_hit(field, rule_index, supplier, category)
    if rule_value is not None:
        return rule_value, SOURCE_SUPPLIER_RULE, rule_scope

    return SYSTEM_DEFAULTS.get(field), SOURCE_DEFAULT, None


def _provenance_column(field: str) -> str:
    """inventory_stock's provenance sibling for a value column.

    `lead_time_days` is the one that does not follow the mechanical
    `<field>_set_by` rule: the column is `lead_time_set_by`, because
    `lead_time_days_set_by` reads like the provenance of a day count.
    """
    if field == "lead_time_days":
        return "lead_time_set_by"
    return f"{field}_set_by"
