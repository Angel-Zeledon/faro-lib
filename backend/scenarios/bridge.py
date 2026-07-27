"""
ForecastingCore bridge for what-if scenarios.

The demand side of a scenario is NOT recomputed in the backend: this module
hands the session forecast over to `forecasting_core.scenarios.ScenarioEngine`
and merges the adjusted values back into the stored blob shape. All the
multiply/offset/floor/ceiling math (and its pandas) lives inside
ForecastingCore — the backend only reshapes plain Python dicts, which is why
this module keeps the pandas boundary intact
(see backend/tests/test_no_pandas_in_backend.py).

Stored session shape (backend):
    {series_key: {model: {"historical": [...], "forecast": [{date, value,
                                                             lower, upper}]}}}
ScenarioEngine shape:
    {series_key: {model: [{date, value, lower, upper}]}}
"""

from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


def _forecast_points(entry: Any) -> list[dict]:
    if isinstance(entry, dict):
        return list(entry.get("forecast") or [])
    if isinstance(entry, list):
        return list(entry)
    return []


def apply_rules_to_forecasts(forecasts: dict, rules: list[dict]) -> dict:
    """
    Return a NEW forecasts blob with every ScenarioEngine rule applied.

    `rules` are ForecastingCore rule dicts (sku / model / multiplier / offset /
    date_start / date_end / floor / ceiling), already resolved to concrete
    series keys by the caller. The input blob is never mutated.

    Only `value` (plus `lower`/`upper` when the original point carried them) is
    overwritten: a point stored without confidence bands must stay without
    them, otherwise the semáforo's std estimate would read a fabricated 0 band
    for the scenario and a real one for the base, and the comparison would be
    apples-to-oranges.
    """
    if not forecasts or not rules:
        return forecasts

    payload: dict[str, dict[str, list[dict]]] = {}
    for key, models in forecasts.items():
        if not isinstance(models, dict):
            continue
        for model, entry in models.items():
            pts = _forecast_points(entry)
            if not pts:
                continue
            payload.setdefault(str(key), {})[str(model)] = [
                {
                    "date": p.get("date"),
                    "value": float(p.get("value") or 0.0),
                    "lower": float(p["lower"]) if p.get("lower") is not None else 0.0,
                    "upper": float(p["upper"]) if p.get("upper") is not None else 0.0,
                }
                for p in pts
            ]

    if not payload:
        return forecasts

    from forecasting_core.scenarios import ScenarioEngine

    adjusted = ScenarioEngine.apply_to_dict(payload, rules) or {}

    out: dict = {}
    for key, models in forecasts.items():
        if not isinstance(models, dict):
            out[key] = models
            continue
        out[key] = {}
        for model, entry in models.items():
            src = _forecast_points(entry)
            adj = (adjusted.get(str(key)) or {}).get(str(model)) or []
            merged: list[dict] = []
            for i, point in enumerate(src):
                new_point = dict(point)
                if i < len(adj):
                    new_point["value"] = adj[i]["value"]
                    if point.get("lower") is not None:
                        new_point["lower"] = adj[i]["lower"]
                    if point.get("upper") is not None:
                        new_point["upper"] = adj[i]["upper"]
                merged.append(new_point)
            if isinstance(entry, dict):
                out[key][model] = {**entry, "forecast": merged}
            else:
                out[key][model] = merged
    return out


def build_core_rule(
    *,
    series_key: Optional[str],
    multiplier: float,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    label: str = "",
) -> dict:
    """One ForecastingCore rule dict. `series_key=None` targets every series."""
    return {
        "sku": series_key,
        "multiplier": float(multiplier),
        "offset": 0.0,
        "date_start": date_start,
        "date_end": date_end,
        # Demand can never go negative, whatever the user typed.
        "floor": 0.0,
        "label": label,
    }
