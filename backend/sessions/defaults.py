"""Shared default session-config blobs for flows that auto-provision a
training session without a human walking the wizard (the demo quickstart and
the accounting-integrations sync service).

Extracted from `backend/api/v1/demo.py`'s former module-private
`_DEMO_CONFIGS` so `backend/integrations/sync_service.py` can seed the exact
same six `session_configs` JSONB blobs without importing an API router
module. Behavior is unchanged — this is a pure constant lift.
"""
import copy
from typing import Any

# Same defaults the quick-start wizard posts (Frontend quick-start page).
_DEFAULT_QUICKSTART_CONFIGS: dict[str, Any] = {
    "columns_cfg": {
        "schema_version": "canonical_v1",
        "canonical_mapping": {"sku": "sku", "date": "fecha", "demand": "cantidad"},
        "defaults_override": {},
    },
    "features_cfg": {"lags": [1, 7, 14, 28], "rolling": [7, 14, 28], "diffs": [1],
                     "calendar": True, "ewm_spans": [7, 14]},
    "models_cfg": {"selected_models": ["lightgbm", "prophet", "croston", "xgboost"]},
    "validation_cfg": {"train_ratio": 0.8, "walk_forward": True, "wfv_splits": 3,
                       "min_history": 20, "seasonal_period": 7},
    "forecast_cfg": {"horizon": 30},
    "business_cfg": {"service_level": 0.95, "lead_time_days": 15,
                     "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0},
}


def default_quickstart_configs() -> dict[str, Any]:
    """A fresh copy of the six default session_configs blobs.

    Returns a deep copy so callers (each iterating and writing per-field) can
    never mutate a shared module-level dict out from under one another.
    """
    return copy.deepcopy(_DEFAULT_QUICKSTART_CONFIGS)
