"""Shared default session-config blobs for flows that auto-provision a
training session without a human walking the wizard.

Extracted from `backend/api/v1/demo.py`'s former module-private
`_DEMO_CONFIGS` so a non-router caller can seed the exact same six
`session_configs` JSONB blobs without importing an API router module. Today
`POST /demo/quickstart` is the only caller; it is kept out of the router so the
next auto-provisioning path cannot quietly seed a DIFFERENT set of defaults.
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
    # `global_lgbm` is one model fitted across the whole catalogue at once. It is
    # in the default set because the catalogue it is best on — short histories,
    # newly-launched SKUs, intermittent movers — is most of a real tenant's
    # catalogue, and those are exactly the series the per-SKU models cannot
    # serve. It competes on the same metrics table as the rest.
    "models_cfg": {"selected_models": ["global_lgbm", "lightgbm", "prophet",
                                       "croston", "xgboost"]},
    # `wfv_splits` is 8, not the 3 it was until 2026-09-21. Those folds are the
    # rolling origins the cushion's calibration is measured from, and three of
    # them starve it: measured end to end at a 15-day lead time, the band the
    # product publishes delivered 83.3% against a promised 95% at three folds
    # and 96.7% at eight, with nothing else changed (docs/stability.md 17b).
    # The extra fits are affordable because removing the separate p10/p50/p90
    # quantile models freed 58% of ML training time. Short series are not hurt:
    # the splitter still funds eight non-empty folds down to 20 rows, and the
    # out-of-fold residual count comes out the same or better.
    "validation_cfg": {"train_ratio": 0.8, "walk_forward": True, "wfv_splits": 8,
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
