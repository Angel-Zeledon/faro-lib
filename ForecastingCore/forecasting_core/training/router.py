"""
ModelRouter — assigns optimal models per SKU based on series type.

Routing table:
  short         → naive, seasonal_naive (insufficient data)
  intermittent  → croston, ets
  seasonal      → prophet, ets, lightgbm
  stable        → lightgbm, xgboost, arima
  volatile      → all declared models (ensemble later)

Override: set routing.enabled=False in config to run all models on all SKUs.

Example:
    router = ModelRouter(models_config={"lightgbm": {}, "prophet": {}}, enabled=True)
    routing = router.route(dq_reports)
    lgb_skus = router.skus_for_model(routing, "lightgbm")
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Set

from forecasting_core.data.quality import (
    SKUReport,
    SERIES_STABLE, SERIES_SEASONAL,
    SERIES_INTERMITTENT, SERIES_VOLATILE, SERIES_SHORT,
)

ROUTING_TABLE: Dict[str, Set[str]] = {
    SERIES_SHORT:        {"naive", "seasonal_naive"},
    SERIES_INTERMITTENT: {"croston", "ets"},
    SERIES_SEASONAL:     {"prophet", "ets", "lightgbm", "sarimax", "lstm"},
    SERIES_STABLE:       {"lightgbm", "xgboost", "arima", "sarimax", "lstm"},
    SERIES_VOLATILE:     {"lightgbm", "xgboost", "prophet", "arima", "ets", "sarimax"},
}

STAT_MODELS = {"arima", "sarimax", "prophet", "ets", "croston", "lstm", "naive", "seasonal_naive"}


class ModelRouter:
    """Routes SKUs to their optimal model set based on series classification."""

    def __init__(self, models_config: dict, enabled: bool = True):
        self._declared = set(models_config.keys())
        self.enabled = enabled

    def route(self, reports: Dict[str, SKUReport]) -> Dict[str, Set[str]]:
        """
        Compute per-SKU model assignment.

        Args:
            reports: {sku: SKUReport} from DataQualityChecker.

        Returns:
            {sku: {model_names_to_run}}
        """
        if not self.enabled:
            all_models = self._declared | STAT_MODELS
            return {sku: all_models for sku in reports}

        routing: Dict[str, Set[str]] = {}
        for sku, report in reports.items():
            # Multi-label: union model sets from all flags on this SKU
            flags = getattr(report, "series_flags", None) or {report.series_type}
            assigned: Set[str] = set()
            for flag in flags:
                assigned |= ROUTING_TABLE.get(flag, set())
            if not assigned:
                assigned = self._declared | STAT_MODELS
            ml_run   = assigned & self._declared
            stat_run = assigned & STAT_MODELS
            routing[sku] = ml_run | stat_run
        return routing

    def skus_for_model(self, routing: Dict[str, Set[str]], model: str) -> List[str]:
        """Return list of SKUs that should run a specific model."""
        return [sku for sku, models in routing.items() if model in models]

    def series_for_model(self, routing: Dict[str, Set[str]], model: str) -> List[str]:
        """Return list of series keys that should run a specific model."""
        return [key for key, models in routing.items() if model in models]

    def summary(self, routing: Dict[str, Set[str]]) -> str:
        counter: Counter = Counter()
        for models in routing.values():
            for m in models:
                counter[m] += 1
        lines = ["Model routing summary:"]
        for model, count in sorted(counter.items()):
            lines.append(f"  {model:20s}→ {count} SKUs")
        return "\n".join(lines)
