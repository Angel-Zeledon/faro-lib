"""
The semáforo's lead-time multipliers — the one place they live.

The signal (`service._calc_signal`) compares a SKU's days of cover against its
supplier's lead time:

    PEDIR_YA      coverage <  order_now_factor x lead time
    PEDIR_PRONTO  coverage <= reorder point (in days of cover)
    SOBRESTOCK    coverage >= max(overstock_factor x lead time,
                                  OVERSTOCK_REORDER_POINT_MULTIPLE x reorder point)
    OK            everything in between

Two of those boundaries are multiples of the lead time, and this module owns
them. The defaults reproduce the rule the product shipped with, byte for byte:

* `order_now_factor = 0.5` — "less than half a lead time of cover".
* `overstock_factor = 3.0` — "three lead times or more".

The middle boundary is deliberately NOT a multiple and NOT configurable here.
Since stability.md 17c it is the reorder point itself (lead-time demand plus
the safety cushion), and the recommended quantity is "top up to the reorder
point". A multiple that raised the PEDIR_PRONTO boundary above the reorder
point would flag products whose quantity is zero — "order soon: 0 units". Its
knob is the service level, which moves the cushion and the quantity together.
Making it a multiple means changing what the quantity is sized to — an owner
decision, reported rather than built.

The bounds keep both configurable boundaries on the right side of the reorder
point: `order_now_factor < 1` keeps PEDIR_YA strictly inside "at or below the
reorder point" (reorder_point_days >= lead time always), so an urgent row always
carries a quantity; and the overstock boundary is >= 2 x reorder point by
construction, so the OK band can never be empty.

Configuration rides on the existing planning-rule cascade
(`stock_defaults`, `stock_defaults_service.build_rule_index`):

    supplier rule  >  category rule  >  global (tenant) rule  >  defaults below

with one difference from the per-field cascade there: the factors resolve as
ONE unit. A supplier rule that set only one of them would combine with the
tenant's other into a pair nobody validated. So every write stores both,
validated together, and `resolve_signal_thresholds` takes the pair from the
narrowest scope that has one.

Every consumer reads `resolve_signal_thresholds`. Nothing else in the backend
may multiply a lead time by a literal (`test_signal_thresholds.py` greps for
it).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.db.connection import execute, query, query_one
from backend.errors import AppError

log = logging.getLogger(__name__)

# ── Defaults: the rule as it shipped ─────────────────────────────────────────
DEFAULT_ORDER_NOW_FACTOR: float = 0.5
DEFAULT_OVERSTOCK_FACTOR: float = 3.0

# Not configurable on purpose: it is the guard that keeps a volatile SKU's OK
# band non-empty when its own reorder point already sits past
# `overstock_factor` lead times (stability.md 17c). It is a property of the
# arithmetic, not a preference.
OVERSTOCK_REORDER_POINT_MULTIPLE: float = 2.0

FACTOR_FIELDS: tuple[str, ...] = ("order_now_factor", "overstock_factor")

DEFAULT_THRESHOLDS: dict[str, float] = {
    "order_now_factor": DEFAULT_ORDER_NOW_FACTOR,
    "overstock_factor": DEFAULT_OVERSTOCK_FACTOR,
}

# Bounds per factor. Below 0.1 lead times PEDIR_YA would only fire on an empty
# shelf. At 1.0 or more it could fire ABOVE a zero-cushion SKU's reorder point,
# where the quantity is 0 — an urgent row with nothing to order — so the
# ceiling is 0.95. Below 1.5 lead times "overstock" would be a normal cycle's
# stock; 12 lead times is already a year of cover on a monthly supplier.
FACTOR_BOUNDS: dict[str, tuple[float, float]] = {
    "order_now_factor": (0.1, 0.95),
    "overstock_factor": (1.5, 12.0),
}

SCOPE_TYPES: tuple[str, ...] = ("global", "supplier", "category")

# Where a resolved set came from. 'default' means nobody configured anything
# at any level — distinct from a tenant that saved the same numbers.
SOURCE_DEFAULT = "default"


def _normalize_scope_value(scope_type: str, scope_value: Optional[str]) -> str:
    # Same normalisation as stock_defaults_service, so the cascade lookup —
    # which lower-cases supplier and category names — finds what was written.
    if scope_type == "global":
        return ""
    return (scope_value or "").strip().lower()


def validate_thresholds(values: dict) -> dict[str, float]:
    """All factors, numeric, in bounds and strictly increasing.

    Raises AppError with a stable code; the frontend renders the sentence.
    """
    clean: dict[str, float] = {}
    for field in FACTOR_FIELDS:
        raw = values.get(field) if isinstance(values, dict) else None
        if raw is None:
            raise AppError(
                "signal_thresholds_missing_field",
                f"{field} is required: the factors are saved together",
                params={"field": field},
            )
        try:
            num = float(raw)
        except (TypeError, ValueError):
            raise AppError(
                "signal_thresholds_not_a_number",
                f"{field} must be a number",
                params={"field": field, "value": str(raw)},
            )
        if num != num or num in (float("inf"), float("-inf")):  # NaN / inf
            raise AppError(
                "signal_thresholds_not_a_number",
                f"{field} must be a finite number",
                params={"field": field, "value": str(raw)},
            )
        low, high = FACTOR_BOUNDS[field]
        if num < low or num > high:
            raise AppError(
                "signal_thresholds_out_of_range",
                f"{field} must be between {low} and {high}",
                params={"field": field, "value": num, "min": low, "max": high},
            )
        clean[field] = round(num, 2)

    # Implied by today's bounds (0.95 < 1.5), checked anyway so a future change
    # to FACTOR_BOUNDS cannot quietly allow an inverted scale.
    if not clean["order_now_factor"] < clean["overstock_factor"]:
        raise AppError(
            "signal_thresholds_not_increasing",
            "The 'order now' factor must be below the overstock factor",
            params={"lower": "order_now_factor", "upper": "overstock_factor",
                    "lower_value": clean["order_now_factor"],
                    "upper_value": clean["overstock_factor"]},
        )
    return clean


def _validate_scope(scope_type: str, scope_value: Optional[str]) -> str:
    if scope_type not in SCOPE_TYPES:
        raise AppError(
            "signal_thresholds_bad_scope",
            f"scope_type must be one of {', '.join(SCOPE_TYPES)}",
            params={"scope_type": scope_type, "allowed": list(SCOPE_TYPES)},
        )
    value = _normalize_scope_value(scope_type, scope_value)
    if scope_type != "global" and not value:
        raise AppError(
            "signal_thresholds_missing_scope_value",
            "A supplier or category override needs a name",
            params={"scope_type": scope_type},
        )
    return value


# ── The resolver ─────────────────────────────────────────────────────────────

def _factors_of(rule: Optional[dict]) -> Optional[dict[str, float]]:
    """The rule's factors, or None when the rule carries no complete
    set (a lead-time-only rule says nothing about the signal)."""
    if not rule:
        return None
    vals = [rule.get(f) for f in FACTOR_FIELDS]
    if any(v is None for v in vals):
        return None
    return {f: float(v) for f, v in zip(FACTOR_FIELDS, vals)}


def resolve_signal_thresholds(
    rule_index: Optional[dict],
    supplier: Optional[str] = None,
    category: Optional[str] = None,
) -> dict[str, Any]:
    """THE function every semáforo consumer reads the multipliers from.

    `rule_index` is `stock_defaults_service.build_rule_index(tenant_id)` —
    loaded once per status pass, never per SKU. Returns the factors plus
    `source` (default | global | supplier | category) and `scope_value`, so a
    screen can say WHICH rule a SKU was judged by.
    """
    idx = rule_index or {}
    candidates = (
        ("supplier", (supplier or "").strip().lower()),
        ("category", (category or "").strip().lower()),
        ("global", ""),
    )
    for scope, key in candidates:
        if scope != "global" and not key:
            continue
        found = _factors_of(idx.get(scope, {}).get(key))
        if found is not None:
            return {**found, "source": scope, "scope_value": key or None}
    return {**DEFAULT_THRESHOLDS, "source": SOURCE_DEFAULT, "scope_value": None}


def patch_rule_index(rule_index: dict, scope_type: str, scope_value: Optional[str],
                     values: Optional[dict]) -> dict:
    """A copy of `rule_index` with one scope's set replaced (or cleared when
    `values` is None). This is how the preview asks "what would the semáforo
    say under these numbers" through the SAME resolver, instead of a second
    implementation of the cascade."""
    patched = {k: dict(v) for k, v in (rule_index or {}).items()}
    for scope in ("supplier", "category", "global"):
        patched.setdefault(scope, {})
    key = _normalize_scope_value(scope_type, scope_value)
    rule = dict(patched[scope_type].get(key) or {})
    for f in FACTOR_FIELDS:
        rule[f] = None if values is None else values[f]
    patched[scope_type][key] = rule
    return patched


# ── Storage (stock_defaults rows) ────────────────────────────────────────────

def _row_public(r: dict) -> dict:
    return {
        "scope_type": r["scope_type"],
        "scope_value": r.get("scope_value") or None,
        **{f: float(r[f]) for f in FACTOR_FIELDS},
        "updated_at": r.get("updated_at").isoformat() if r.get("updated_at") else None,
    }


def get_signal_thresholds(tenant_id: str) -> dict:
    """What the settings screen shows: the defaults, the tenant-wide set
    (None when never configured — not the defaults dressed as a choice), every
    supplier/category override, and the bounds the form must respect."""
    rows = query(
        """SELECT scope_type, scope_value, order_now_factor,
                  overstock_factor, updated_at
           FROM stock_defaults
           WHERE tenant_id = %s
             AND order_now_factor IS NOT NULL
             AND overstock_factor IS NOT NULL
           ORDER BY scope_type, scope_value""",
        (tenant_id,),
    )
    tenant_row = next((r for r in rows if r["scope_type"] == "global"), None)
    overrides = [_row_public(r) for r in rows if r["scope_type"] != "global"]
    effective = (
        {f: float(tenant_row[f]) for f in FACTOR_FIELDS} if tenant_row
        else dict(DEFAULT_THRESHOLDS)
    )
    return {
        "defaults": dict(DEFAULT_THRESHOLDS),
        "tenant": _row_public(tenant_row) if tenant_row else None,
        "effective": effective,
        "source": "global" if tenant_row else SOURCE_DEFAULT,
        "overrides": overrides,
        "bounds": {f: {"min": lo, "max": hi} for f, (lo, hi) in FACTOR_BOUNDS.items()},
        "overstock_reorder_point_multiple": OVERSTOCK_REORDER_POINT_MULTIPLE,
    }


def set_signal_thresholds(
    tenant_id: str, scope_type: str, scope_value: Optional[str], values: dict,
) -> dict:
    """Validate and store one scope's set, every column at once."""
    key = _validate_scope(scope_type, scope_value)
    clean = validate_thresholds(values)
    execute(
        """INSERT INTO stock_defaults
               (tenant_id, scope_type, scope_value,
                order_now_factor, overstock_factor)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, scope_type, scope_value)
           DO UPDATE SET order_now_factor = EXCLUDED.order_now_factor,
                         overstock_factor = EXCLUDED.overstock_factor,
                         updated_at = NOW()""",
        (tenant_id, scope_type, key,
         clean["order_now_factor"], clean["overstock_factor"]),
    )
    row = query_one(
        """SELECT scope_type, scope_value, order_now_factor,
                  overstock_factor, updated_at
           FROM stock_defaults
           WHERE tenant_id = %s AND scope_type = %s AND scope_value = %s""",
        (tenant_id, scope_type, key),
    )
    if row is None or _factors_of(row) != clean:
        # Never report a save that did not land (silent-failures, question 1).
        raise AppError(
            "signal_thresholds_not_saved",
            "The thresholds could not be saved",
            status_code=500,
        )
    return _row_public(row)


def clear_signal_thresholds(tenant_id: str, scope_type: str,
                            scope_value: Optional[str]) -> bool:
    """Reset one scope to "says nothing": the tenant row falls back to the
    defaults, an override falls back to the tenant's set.

    Only the factor columns are cleared — the same stock_defaults row may
    carry a lead time or an MOQ that has nothing to do with the signal. Returns
    whether there was anything to clear.
    """
    key = _validate_scope(scope_type, scope_value)
    existing = query_one(
        """SELECT id FROM stock_defaults
           WHERE tenant_id = %s AND scope_type = %s AND scope_value = %s
             AND order_now_factor IS NOT NULL""",
        (tenant_id, scope_type, key),
    )
    if not existing:
        return False
    execute(
        """UPDATE stock_defaults
           SET order_now_factor = NULL, overstock_factor = NULL,
               updated_at = NOW()
           WHERE id = %s AND tenant_id = %s""",
        (existing["id"], tenant_id),
    )
    # A row that only ever carried the factors now says nothing at all; drop
    # it rather than leave an empty rule behind.
    execute(
        """DELETE FROM stock_defaults
           WHERE id = %s AND tenant_id = %s
             AND lead_time_days IS NULL AND service_level IS NULL
             AND moq IS NULL AND holding_cost_pct IS NULL
             AND order_now_factor IS NULL AND overstock_factor IS NULL""",
        (existing["id"], tenant_id),
    )
    return True


# ── Preview ──────────────────────────────────────────────────────────────────

_SIGNAL_KEYS = ("PEDIR_YA", "PEDIR_PRONTO", "OK", "SOBRESTOCK", "SIN_DATOS")
_PREVIEW_SAMPLE = 8


def preview_signal_changes(
    tenant_id: str, session_id: str, period: str,
    scope_type: str, scope_value: Optional[str], values: Optional[dict],
) -> dict:
    """How many of the tenant's products would change signal if `values` were
    saved at this scope (`values=None` previews a reset).

    Runs the real semáforo twice — once as stored, once with the candidate
    patched into the rule index — so the preview cannot disagree with the
    screen it predicts. Nothing is written.
    """
    from backend.inventory import service as inv_svc

    key = _validate_scope(scope_type, scope_value)
    clean = validate_thresholds(values) if values is not None else None

    before = inv_svc._compute_inventory_status(tenant_id, session_id, period=period)
    after = inv_svc._compute_inventory_status(
        tenant_id, session_id, period=period,
        signal_threshold_patch=(scope_type, key, clean),
    )
    after_by_sku = {r["sku"]: r for r in after}

    counts_before = {s: 0 for s in _SIGNAL_KEYS}
    counts_after = {s: 0 for s in _SIGNAL_KEYS}
    transitions: dict[str, int] = {}
    changed: list[dict] = []
    for r in before:
        a = after_by_sku.get(r["sku"])
        s0 = r.get("signal")
        s1 = a.get("signal") if a else s0
        counts_before[s0] = counts_before.get(s0, 0) + 1
        counts_after[s1] = counts_after.get(s1, 0) + 1
        if s0 != s1:
            tkey = f"{s0}>{s1}"
            transitions[tkey] = transitions.get(tkey, 0) + 1
            changed.append({
                "sku": r["sku"], "name": r.get("name") or r.get("description"),
                "from": s0, "to": s1,
                "coverage_days": r.get("coverage_days"),
                "lead_time_days": r.get("lead_time_days"),
            })
    return {
        "available": True,
        "session_id": session_id,
        "total": len(before),
        "changed": len(changed),
        "counts_before": counts_before,
        "counts_after": counts_after,
        "transitions": transitions,
        "sample": changed[:_PREVIEW_SAMPLE],
        "values": clean,
    }
