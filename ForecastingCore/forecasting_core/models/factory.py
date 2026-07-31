"""
ModelFactory — instantiates models from a config dict.

Supported models: lightgbm, xgboost, arima, prophet, ets, croston, lstm.
ML models (lightgbm, xgboost) implement sklearn's fit/predict interface.
Statistical models are handled via their own run_* functions.

Example:
    factory = ModelFactory({"lightgbm": {"n_estimators": 300}, "xgboost": {}})
    ml_models = factory.build_ml()   # {name: fitted-ready model}
    stat_names = factory.stat_names() # ["arima", "prophet"]
"""

from __future__ import annotations

from typing import Dict, Any

ML_MODELS   = {"lightgbm", "xgboost"}
STAT_MODELS = {"arima", "sarimax", "prophet", "ets", "croston"}
DL_MODELS   = {"lstm"}
# Fitted ONCE over every series instead of once per series, so it is deliberately
# not in ML_MODELS: build_ml() must never hand it to the per-SKU Trainer, which
# would fit one "global" model per SKU — the exact opposite of the point.
GLOBAL_MODELS = {"global_lgbm"}


class ModelFactory:
    """Builds trainable model instances from a models config dict."""

    def __init__(self, models_config: Dict[str, Dict[str, Any]]):
        self.config = models_config or {}

    def build_ml(self) -> dict:
        """Return instantiated sklearn-compatible ML models."""
        from lightgbm import LGBMRegressor
        from xgboost import XGBRegressor

        models = {}
        for name, params in self.config.items():
            params = params or {}
            if name == "lightgbm":
                models[name] = LGBMRegressor(**{"n_jobs": 1, **params, "verbosity": -1})
            elif name == "xgboost":
                models[name] = XGBRegressor(**{"n_jobs": 1, **params, "verbosity": 0})
        return models

    def build_quantile_ml(self, quantile: float) -> dict:
        """Return quantile-regression ML models for a given quantile (0.1, 0.5, 0.9)."""
        from lightgbm import LGBMRegressor
        from xgboost import XGBRegressor

        models = {}
        for name, params in self.config.items():
            params = {k: v for k, v in (params or {}).items()
                      if k not in ("objective", "alpha", "quantile_alpha")}
            if name == "lightgbm":
                models[name] = LGBMRegressor(
                    **{"n_jobs": 1, **params}, objective="quantile", alpha=quantile, verbosity=-1
                )
            elif name == "xgboost":
                models[name] = XGBRegressor(
                    **{"n_jobs": 1, **params}, objective="reg:quantileerror",
                    quantile_alpha=quantile, verbosity=0
                )
        return models

    def ml_names(self) -> list:
        return [n for n in self.config if n in ML_MODELS]

    def stat_names(self) -> list:
        return [n for n in self.config if n in STAT_MODELS]

    def dl_names(self) -> list:
        return [n for n in self.config if n in DL_MODELS]

    def global_names(self) -> list:
        return [n for n in self.config if n in GLOBAL_MODELS]

    @staticmethod
    def create(name: str, params: dict):
        """Instantiate a single ML model from a params dict (used by tuner)."""
        p = params or {}
        if name == "lightgbm":
            from lightgbm import LGBMRegressor
            return LGBMRegressor(**{"n_jobs": 1, **p, "verbosity": -1})
        if name == "xgboost":
            from xgboost import XGBRegressor
            return XGBRegressor(**{"n_jobs": 1, **p, "verbosity": 0})
        raise ValueError(f"ModelFactory.create: unsupported model '{name}'")

    @staticmethod
    def available_models() -> list:
        return sorted(ML_MODELS | STAT_MODELS | DL_MODELS | GLOBAL_MODELS)
