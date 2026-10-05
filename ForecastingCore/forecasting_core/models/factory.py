"""
ModelFactory — instantiates models from a config dict.

Supported models: lightgbm, xgboost, arima, prophet, ets, croston, tsb, adida,
imapa, lstm.
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
# tsb / adida / imapa: intermittent-demand models (models/intermittent.py).
# Selectable, never in a default set, never added by the router.
INTERMITTENT_MODELS = {"croston", "tsb", "adida", "imapa"}
STAT_MODELS = {"arima", "sarimax", "prophet", "ets"} | INTERMITTENT_MODELS
# Trainable by the engine when a config names them (the benchmark does), but NOT
# offered to the product: `available_models()` is the list the backend validates
# a user's selection against and the list `mode: "all"` trains. On the synthetic
# benchmark (benchmarks/results/synthetic_pooled_42_7_intermittent_models.md)
# they tie with Croston, so they stay off the product until real evidence says
# otherwise. TSB is offered: its forecast fades on a SKU that stopped selling,
# which Croston's cannot.
ENGINE_ONLY_MODELS = {"adida", "imapa"}

# Reserved keys inside an ML model's params (`{"lightgbm": {...}}`). They are
# NOT estimator arguments — the Trainer reads them — so build_ml/create strip
# them before constructing the estimator. See `intermittent_objective_spec`.
INTERMITTENT_OBJECTIVE_KEY = "intermittent_objective"
TWEEDIE_POWER_KEY = "tweedie_variance_power"
_RESERVED_ML_KEYS = {INTERMITTENT_OBJECTIVE_KEY, TWEEDIE_POWER_KEY}
INTERMITTENT_OBJECTIVES = {"tweedie", "poisson"}
DEFAULT_TWEEDIE_POWER = 1.5


def _estimator_params(params: dict) -> dict:
    return {k: v for k, v in (params or {}).items() if k not in _RESERVED_ML_KEYS}


def intermittent_objective_spec(name: str, params: dict) -> "dict | None":
    """
    The OPTIONAL count-shaped objective an ML model uses on intermittent /
    lumpy series, or None (the default: L2 everywhere, exactly as before).

    Opt-in per model through its params:
        {"lightgbm": {"intermittent_objective": "tweedie",
                      "tweedie_variance_power": 1.5}}

    Returns the estimator kwargs that switch it on, in each library's own
    vocabulary. Raises ValueError on a value it does not know — a typo must not
    silently train the default and report the run as configured.
    """
    p = params or {}
    choice = p.get(INTERMITTENT_OBJECTIVE_KEY)
    if choice in (None, "", "none", False):
        return None
    choice = str(choice).lower()
    if choice not in INTERMITTENT_OBJECTIVES:
        raise ValueError(
            f"{name}: intermittent_objective must be one of "
            f"{sorted(INTERMITTENT_OBJECTIVES)} or null, got {p.get(INTERMITTENT_OBJECTIVE_KEY)!r}")
    if name not in ML_MODELS:
        raise ValueError(f"{name}: intermittent_objective applies to {sorted(ML_MODELS)} only")
    if choice == "poisson":
        return {"objective": "poisson" if name == "lightgbm" else "count:poisson"}
    power = float(p.get(TWEEDIE_POWER_KEY, DEFAULT_TWEEDIE_POWER))
    # 1 = Poisson, 2 = Gamma; the compound Poisson-Gamma (zero mass + positive
    # sizes) that intermittent demand looks like lives strictly between.
    if not (1.0 < power < 2.0):
        raise ValueError(f"{name}: tweedie_variance_power must be in (1, 2), got {power}")
    if name == "lightgbm":
        return {"objective": "tweedie", "tweedie_variance_power": power}
    return {"objective": "reg:tweedie", "tweedie_variance_power": power}


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
            params = _estimator_params(params)
            if name == "lightgbm":
                models[name] = LGBMRegressor(**{"n_jobs": 1, **params, "verbosity": -1})
            elif name == "xgboost":
                models[name] = XGBRegressor(**{"n_jobs": 1, **params, "verbosity": 0})
        return models

    # There is no build_quantile_ml(). Separately-fitted p10/p50/p90 regressors
    # were 58% of ML training time and produced a band no decision used; the
    # intervals come from the out-of-fold residual bank instead. The reasoning
    # and the measurement are in pipelines/pipeline.py, step 7b.

    def ml_names(self) -> list:
        return [n for n in self.config if n in ML_MODELS]

    def intermittent_objectives(self) -> dict:
        """{ml_model_name: estimator kwargs} for the models that opted in to a
        count objective on intermittent series. Empty by default."""
        out = {}
        for name in self.ml_names():
            spec = intermittent_objective_spec(name, self.config.get(name) or {})
            if spec:
                out[name] = spec
        return out

    def stat_names(self) -> list:
        return [n for n in self.config if n in STAT_MODELS]

    def dl_names(self) -> list:
        return [n for n in self.config if n in DL_MODELS]

    def global_names(self) -> list:
        return [n for n in self.config if n in GLOBAL_MODELS]

    @staticmethod
    def create(name: str, params: dict):
        """Instantiate a single ML model from a params dict (used by tuner)."""
        p = _estimator_params(params)
        if name == "lightgbm":
            from lightgbm import LGBMRegressor
            return LGBMRegressor(**{"n_jobs": 1, **p, "verbosity": -1})
        if name == "xgboost":
            from xgboost import XGBRegressor
            return XGBRegressor(**{"n_jobs": 1, **p, "verbosity": 0})
        raise ValueError(f"ModelFactory.create: unsupported model '{name}'")

    @staticmethod
    def available_models() -> list:
        return sorted((ML_MODELS | STAT_MODELS | DL_MODELS | GLOBAL_MODELS) - ENGINE_ONLY_MODELS)
