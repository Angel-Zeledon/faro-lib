"""
What-if scenario simulation (PENDIENTES #7).

A scenario is a named list of typed rules bound to one forecast session:

    demand_multiplier  {multiplier, sku?|category?, date_from?, date_to?}
    promo              {multiplier, sku?|category?, date_from, date_to}
    supplier_delay     {extra_days, supplier?}
    safety_stock       {service_level}

Running one is a pure read. Demand rules are translated into ForecastingCore
ScenarioEngine rules (backend/scenarios/bridge.py) and applied to the session's
stored forecast; supply-side rules become overrides on the inputs of the
EXISTING inventory math — the semáforo, reorder point and recommended quantity
are never recomputed here, they come from
`backend.inventory.service` fed with scenario-adjusted inputs.

The run result is a BASE vs SCENARIO comparison at portfolio level plus the
per-SKU rows that moved the most.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.db.connection import query, query_one, execute, _json
from backend.errors import AppError

log = logging.getLogger(__name__)

# ── Rule schema ───────────────────────────────────────────────────────────────

RULE_TYPES = ("demand_multiplier", "promo", "supplier_delay", "safety_stock")

# Rule types whose effect is a multiplier on forecast demand. Both go through
# ScenarioEngine; `promo` exists as its own type so the UI (and any later
# reporting) can tell a commercial promotion apart from a generic what-if.
DEMAND_RULE_TYPES = ("demand_multiplier", "promo")

MIN_MULTIPLIER, MAX_MULTIPLIER = 0.01, 10.0
MAX_EXTRA_DAYS = 365
MIN_SERVICE_LEVEL, MAX_SERVICE_LEVEL = 0.50, 0.999

# Fields kept per rule type — anything else the caller sends is dropped so a
# saved scenario never carries junk that the runner would silently ignore.
_RULE_FIELDS: dict[str, tuple[str, ...]] = {
    "demand_multiplier": ("multiplier", "sku", "category", "date_from", "date_to", "label"),
    "promo":             ("multiplier", "sku", "category", "date_from", "date_to", "label"),
    "supplier_delay":    ("extra_days", "supplier", "label"),
    "safety_stock":      ("service_level", "label"),
}


def _as_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise AppError(
            "scenario_rule_invalid_number",
            f"'{field}' must be a number",
            params={"field": field},
        )


def normalize_rule(raw: Any) -> dict:
    """
    Validate ONE rule and return it reduced to the fields its type uses.

    The API layer validates with Pydantic first; this runs again on rules read
    back from the DB, so a scenario saved by an older/looser client can never
    reach the runner in a shape it cannot interpret.
    """
    if not isinstance(raw, dict):
        raise AppError("scenario_rule_invalid", "Each rule must be an object")

    rule_type = str(raw.get("type") or "").strip()
    if rule_type not in RULE_TYPES:
        raise AppError(
            "scenario_rule_unknown_type",
            f"Unknown scenario rule type '{rule_type}'",
            params={"type": rule_type, "allowed": list(RULE_TYPES)},
        )

    out: dict = {"type": rule_type}
    for field in _RULE_FIELDS[rule_type]:
        if raw.get(field) is not None:
            out[field] = raw[field]

    if rule_type in DEMAND_RULE_TYPES:
        multiplier = _as_float(out.get("multiplier"), "multiplier")
        if not (MIN_MULTIPLIER <= multiplier <= MAX_MULTIPLIER):
            raise AppError(
                "scenario_multiplier_out_of_range",
                f"multiplier must be between {MIN_MULTIPLIER} and {MAX_MULTIPLIER}",
                params={"min": MIN_MULTIPLIER, "max": MAX_MULTIPLIER},
            )
        out["multiplier"] = multiplier
        if out.get("sku") and out.get("category"):
            raise AppError(
                "scenario_rule_sku_and_category",
                "A rule targets either a sku or a category, not both",
            )
        if rule_type == "promo" and not (out.get("date_from") and out.get("date_to")):
            raise AppError(
                "scenario_promo_requires_dates",
                "A promo rule requires date_from and date_to",
            )
        _check_date_order(out.get("date_from"), out.get("date_to"))

    elif rule_type == "supplier_delay":
        extra = _as_float(out.get("extra_days"), "extra_days")
        if not (0 <= extra <= MAX_EXTRA_DAYS):
            raise AppError(
                "scenario_extra_days_out_of_range",
                f"extra_days must be between 0 and {MAX_EXTRA_DAYS}",
                params={"max": MAX_EXTRA_DAYS},
            )
        out["extra_days"] = int(extra)

    elif rule_type == "safety_stock":
        level = _as_float(out.get("service_level"), "service_level")
        if not (MIN_SERVICE_LEVEL <= level <= MAX_SERVICE_LEVEL):
            raise AppError(
                "scenario_service_level_out_of_range",
                f"service_level must be between {MIN_SERVICE_LEVEL} and {MAX_SERVICE_LEVEL}",
                params={"min": MIN_SERVICE_LEVEL, "max": MAX_SERVICE_LEVEL},
            )
        out["service_level"] = level

    return out


def _check_date_order(date_from: Optional[str], date_to: Optional[str]) -> None:
    from datetime import date as _date

    parsed = {}
    for field, value in (("date_from", date_from), ("date_to", date_to)):
        if value is None:
            continue
        try:
            parsed[field] = _date.fromisoformat(str(value)[:10])
        except ValueError:
            raise AppError(
                "scenario_rule_invalid_date",
                f"'{field}' must be an ISO date (YYYY-MM-DD)",
                params={"field": field},
            )
    if "date_from" in parsed and "date_to" in parsed:
        if parsed["date_to"] < parsed["date_from"]:
            raise AppError(
                "scenario_rule_dates_reversed",
                "date_to must not be before date_from",
            )


def normalize_rules(raw_rules: Any) -> list[dict]:
    if raw_rules is None:
        return []
    if not isinstance(raw_rules, list):
        raise AppError("scenario_rules_invalid", "rules must be a list")
    return [normalize_rule(r) for r in raw_rules]


# ── Persistence ───────────────────────────────────────────────────────────────

def create_scenario(
    tenant_id: str,
    session_id: str,
    name: str,
    rules: list,
    created_by: Optional[str] = None,
) -> dict:
    clean_name = (name or "").strip()
    if not clean_name:
        raise AppError("scenario_name_required", "A scenario needs a name")
    clean_rules = normalize_rules(rules)
    row = query_one(
        """INSERT INTO scenarios (tenant_id, session_id, name, rules, created_by)
           VALUES (%s, %s, %s, %s, %s)
           RETURNING *""",
        (tenant_id, session_id, clean_name, _json(clean_rules), created_by),
    )
    return row


def list_scenarios(tenant_id: str, session_id: Optional[str] = None) -> list[dict]:
    if session_id:
        return query(
            """SELECT * FROM scenarios WHERE tenant_id = %s AND session_id = %s
               ORDER BY created_at DESC""",
            (tenant_id, session_id),
        )
    return query(
        "SELECT * FROM scenarios WHERE tenant_id = %s ORDER BY created_at DESC",
        (tenant_id,),
    )


def get_scenario(tenant_id: str, scenario_id: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM scenarios WHERE tenant_id = %s AND id = %s",
        (tenant_id, scenario_id),
    )


def delete_scenario(tenant_id: str, scenario_id: str) -> bool:
    if not get_scenario(tenant_id, scenario_id):
        return False
    execute(
        "DELETE FROM scenarios WHERE tenant_id = %s AND id = %s",
        (tenant_id, scenario_id),
    )
    return True


# ── Running a scenario ────────────────────────────────────────────────────────

def _series_keys_for(forecasts: dict, skus: set[str]) -> list[str]:
    """Forecast keys (possibly 'sku│store') whose base SKU is in `skus`."""
    from backend.inventory.series import split_key

    return [key for key in forecasts if split_key(str(key))[0] in skus]


def _skus_in_category(stock_rows: list[dict], category: str) -> set[str]:
    wanted = category.strip().lower()
    return {
        r["sku"] for r in stock_rows
        if (r.get("category") or "").strip().lower() == wanted
    }


def _build_demand_rules(rules: list[dict], forecasts: dict, stock_rows: list[dict]) -> list[dict]:
    """
    Translate the demand-side scenario rules into ForecastingCore rule dicts.

    A rule that names a sku/category is expanded into one core rule per matching
    forecast key, because a store-split session keys its series 'sku│store' and
    ScenarioEngine matches the key literally.
    """
    from backend.scenarios import bridge

    core_rules: list[dict] = []
    for rule in rules:
        if rule["type"] not in DEMAND_RULE_TYPES:
            continue
        label = rule.get("label") or rule["type"]
        date_from, date_to = rule.get("date_from"), rule.get("date_to")

        if rule.get("sku"):
            targets = _series_keys_for(forecasts, {str(rule["sku"])})
        elif rule.get("category"):
            targets = _series_keys_for(
                forecasts, _skus_in_category(stock_rows, str(rule["category"]))
            )
        else:
            targets = [None]  # every series

        for key in targets:
            core_rules.append(bridge.build_core_rule(
                series_key=key,
                multiplier=rule["multiplier"],
                date_start=date_from,
                date_end=date_to,
                label=label,
            ))
    return core_rules


def _series_touched(core_rules: list[dict], forecasts: dict) -> int:
    """
    How many forecast series the demand rules actually reach — an untargeted
    rule is ONE core rule that hits every series, so counting core rules would
    under-report it. This is what the UI shows as "N series adjusted".
    """
    touched: set[str] = set()
    for rule in core_rules:
        if rule.get("sku") is None:
            touched |= {str(k) for k in forecasts}
        else:
            touched.add(str(rule["sku"]))
    return len(touched)


def _apply_supply_rules(
    rules: list[dict],
    stock_rows: list[dict],
    learned_lead_times: dict,
    base_service_level: float,
) -> tuple[list[dict], dict, float]:
    """
    Supply-side overrides: returns (stock_rows, learned_lead_times,
    service_level) to feed the inventory math with. Inputs are never mutated.

    A supplier delay is added to BOTH the configured lead time on the SKU row
    and the learned-from-receptions average, because `resolve_lead_time` prefers
    whichever it has — the delay must land regardless of which one wins.
    """
    rows = [dict(r) for r in stock_rows]
    learned = dict(learned_lead_times or {})
    service_level = base_service_level

    for rule in rules:
        if rule["type"] == "supplier_delay":
            extra = int(rule["extra_days"])
            if extra <= 0:
                continue
            target = (rule.get("supplier") or "").strip().lower() or None
            for row in rows:
                supplier = (row.get("supplier") or "").strip().lower()
                if target is None or supplier == target:
                    current = int(row.get("lead_time_days") or 15)
                    row["lead_time_days"] = min(MAX_EXTRA_DAYS, current + extra)
            for supplier_key in list(learned):
                if target is None or supplier_key == target:
                    learned[supplier_key] = min(
                        float(MAX_EXTRA_DAYS), float(learned[supplier_key]) + extra
                    )
        elif rule["type"] == "safety_stock":
            service_level = float(rule["service_level"])
            # A per-SKU service_level on the stock row wins over the argument in
            # the inventory math, so the scenario level has to replace it too —
            # otherwise the rule would silently do nothing for those SKUs.
            for row in rows:
                row["service_level"] = service_level

    return rows, learned, service_level


def _summarize(items: list[dict]) -> dict:
    """Portfolio totals of one semáforo run."""
    total_units = 0.0
    purchase_value = 0.0
    skus_to_order = 0
    for item in items:
        qty = float(item.get("recommended_qty") or 0.0)
        if qty > 0:
            skus_to_order += 1
            total_units += qty
            cost = item.get("unit_cost")
            if cost is not None:
                purchase_value += qty * float(cost)
    return {
        "skus_evaluated":  len(items),
        "skus_to_order":    skus_to_order,
        "total_units_to_order": round(total_units, 2),
        "estimated_purchase_value": round(purchase_value, 2),
        "order_now":  sum(1 for i in items if i.get("signal") == "PEDIR_YA"),
        "order_soon": sum(1 for i in items if i.get("signal") == "PEDIR_PRONTO"),
        "ok":          sum(1 for i in items if i.get("signal") == "OK"),
        "overstock":   sum(1 for i in items if i.get("signal") == "SOBRESTOCK"),
        "no_data":     sum(1 for i in items if i.get("signal") == "SIN_DATOS"),
    }


_DELTA_KEYS = (
    "skus_to_order", "total_units_to_order", "estimated_purchase_value",
    "order_now", "order_soon", "ok", "overstock", "no_data",
)


def _deltas(base: dict, scenario: dict) -> dict:
    out: dict = {}
    for key in _DELTA_KEYS:
        diff = (scenario.get(key) or 0) - (base.get(key) or 0)
        out[key] = round(diff, 2) if isinstance(diff, float) else diff
    return out


def _changed_rows(base_items: list[dict], scenario_items: list[dict], limit: int) -> list[dict]:
    """Per-SKU rows where the scenario changed the quantity or the signal."""
    base_by_sku = {i["sku"]: i for i in base_items}
    rows: list[dict] = []
    for scenario_item in scenario_items:
        sku = scenario_item["sku"]
        base_item = base_by_sku.get(sku, {})
        base_qty = float(base_item.get("recommended_qty") or 0.0)
        scenario_qty = float(scenario_item.get("recommended_qty") or 0.0)
        base_signal = base_item.get("signal")
        scenario_signal = scenario_item.get("signal")
        if abs(scenario_qty - base_qty) < 1e-9 and base_signal == scenario_signal:
            continue
        cost = scenario_item.get("unit_cost")
        rows.append({
            "sku":            sku,
            "display_name":   scenario_item.get("display_name"),
            "supplier":       scenario_item.get("supplier"),
            "category":       scenario_item.get("category"),
            "current_stock":  scenario_item.get("current_stock"),
            "base_signal":     base_signal,
            "scenario_signal": scenario_signal,
            "base_qty":         round(base_qty, 2),
            "scenario_qty":     round(scenario_qty, 2),
            "delta_qty":        round(scenario_qty - base_qty, 2),
            "delta_value": round((scenario_qty - base_qty) * float(cost), 2) if cost is not None else None,
            "base_daily_demand":      base_item.get("daily_demand"),
            "scenario_daily_demand":  scenario_item.get("daily_demand"),
            "base_lead_time_days":      base_item.get("lead_time_days"),
            "scenario_lead_time_days":  scenario_item.get("lead_time_days"),
            "base_coverage_days":      base_item.get("coverage_days"),
            "scenario_coverage_days":  scenario_item.get("coverage_days"),
        })
    rows.sort(key=lambda r: -abs(r["delta_qty"]))
    return rows[:limit]


def run_scenario(
    tenant_id: str,
    session_id: str,
    rules: list,
    *,
    period: str = "daily",
    service_level: float = 0.95,
    top_changes: int = 50,
    scenario_id: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    """
    BASE vs SCENARIO comparison for one session. Nothing is persisted.

    The base run and the scenario run share the SAME loaded forecast/stock/
    lead-time inputs, so any difference in the output is caused by the rules
    and nothing else (no re-query in between, no ordering drift).
    """
    from backend.db import session_store
    from backend.inventory import service as inv_svc
    from backend.scenarios import bridge

    clean_rules = normalize_rules(rules)

    forecasts = session_store.get_forecasts(tenant_id, session_id) or {}
    stock_rows = inv_svc.list_stock(tenant_id)
    learned_lead_times = inv_svc.get_learned_lead_times(tenant_id)

    # `_compute_inventory_status` is the ONLY entry point of the semáforo that
    # accepts preloaded inputs, which is exactly what a what-if needs: same
    # math, different inputs. Reimplementing any of it here would fork the
    # business rules.
    base_items = inv_svc._compute_inventory_status(
        tenant_id, session_id, service_level,
        forecasts=forecasts,
        stock_rows=stock_rows,
        learned_lead_times=learned_lead_times,
        period=period,
    )

    core_rules = _build_demand_rules(clean_rules, forecasts, stock_rows)
    series_adjusted = _series_touched(core_rules, forecasts)
    scenario_forecasts = bridge.apply_rules_to_forecasts(forecasts, core_rules)
    scenario_rows, scenario_learned, scenario_service_level = _apply_supply_rules(
        clean_rules, stock_rows, learned_lead_times, service_level,
    )

    scenario_items = inv_svc._compute_inventory_status(
        tenant_id, session_id, scenario_service_level,
        forecasts=scenario_forecasts,
        stock_rows=scenario_rows,
        learned_lead_times=scenario_learned,
        period=period,
    )

    base_summary = _summarize(base_items)
    scenario_summary = _summarize(scenario_items)

    return {
        "scenario_id": scenario_id,
        "name":         name,
        "session_id":   session_id,
        "rules":        clean_rules,
        "base":         base_summary,
        "scenario":     scenario_summary,
        "delta":        _deltas(base_summary, scenario_summary),
        "changes":      _changed_rows(base_items, scenario_items, top_changes),
        "applied": {
            "demand_rules":   sum(1 for r in clean_rules if r["type"] in DEMAND_RULE_TYPES),
            "series_adjusted": series_adjusted,
            "service_level":  scenario_service_level,
        },
    }


def run_saved_scenario(tenant_id: str, session_id: str, scenario_id: str, **kwargs) -> dict:
    scenario = get_scenario(tenant_id, scenario_id)
    if not scenario:
        raise AppError("scenario_not_found", "Scenario not found", status_code=404)
    if scenario["session_id"] != session_id:
        raise AppError(
            "scenario_session_mismatch",
            "This scenario belongs to a different session",
            status_code=404,
            params={"session_id": scenario["session_id"]},
        )
    return run_scenario(
        tenant_id, session_id, scenario.get("rules") or [],
        scenario_id=scenario_id, name=scenario.get("name"), **kwargs,
    )
