import math

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError
from typing import Annotated, Literal, Optional, Dict, Any, List

from backend.errors import AppError


class AttachDatasetRequest(BaseModel):
    dataset_id: str


class CovariateEntry(BaseModel):
    name: str
    type: str  # numeric | categorical | binary


# The strategies `workers/runner.py::_apply_outlier_treatment` implements. The
# set is closed and known at request time, and the chain there is a plain
# if/elif that ENDS: a name outside this list matches no branch, so the run does
# nothing, raises nothing and reports nothing — while the config screen keeps
# saying the treatment is active. Measured: `strategy: "no_existe"` was accepted
# with a 200 and silently became "leave".
_OUTLIER_STRATEGIES = frozenset({
    "leave", "winsorize_sigma", "winsorize_pct", "iqr_fence", "remove", "log1p",
})

# Bounds exist where a number outside them destroys the series rather than
# treating it. `n_sigma <= 0` winsorizes every point onto the mean — a flat line
# the models then learn perfectly and forecast uselessly. A percentile at or
# above 50 clips from both ends past the median and does the same. `iqr_k <= 0`
# collapses the fence onto the quartiles.
_Sigma      = Annotated[float, Field(gt=0, le=10)]
_Percentile = Annotated[float, Field(gt=0, lt=50)]
_IqrK       = Annotated[float, Field(gt=0, le=10)]


def _known_strategy(value: str) -> str:
    if value not in _OUTLIER_STRATEGIES:
        raise PydanticCustomError(
            "outlier_strategy_unknown",
            "'{strategy}' is not an outlier treatment this engine implements.",
            {"strategy": str(value)[:32],
             "allowed": ", ".join(sorted(_OUTLIER_STRATEGIES))},
        )
    return value


class OutlierConfig(BaseModel):
    """Outlier treatment configuration — applied per-SKU before training."""
    strategy: str = "leave"
    # winsorize_sigma params
    n_sigma: _Sigma = 3.0
    # winsorize_pct params: clip bottom p% and top p%
    percentile: _Percentile = 1.0
    # iqr_fence params
    iqr_k: _IqrK = 1.5
    # per-SKU overrides (map: sku_name → strategy). Capped so a runaway client
    # cannot park megabytes of JSONB on the session; 5000 SKUs is far past any
    # plan's `max_skus`.
    per_sku_overrides: Dict[str, str] = Field(default_factory=dict, max_length=5000)
    per_sku_n_sigma: Dict[str, _Sigma] = Field(default_factory=dict, max_length=5000)
    per_sku_percentile: Dict[str, _Percentile] = Field(default_factory=dict, max_length=5000)
    per_sku_iqr_k: Dict[str, _IqrK] = Field(default_factory=dict, max_length=5000)

    @field_validator("strategy")
    @classmethod
    def _check_strategy(cls, value: str) -> str:
        return _known_strategy(value)

    @field_validator("per_sku_overrides")
    @classmethod
    def _check_per_sku(cls, value: Dict[str, str]) -> Dict[str, str]:
        for strategy in value.values():
            _known_strategy(strategy)
        return value


class ColumnsConfigRequest(BaseModel):
    date_column: str
    target_column: str
    sku_column: Optional[str] = None
    exogenous: List[str] = []
    transforms: Dict[str, Dict[str, str]] = {}  # {col: {impute, encode, scale}}
    gap_fill: Optional[str] = "leave"  # zero | mean | forward | interpolate | leave
    outlier_config: OutlierConfig = OutlierConfig()


class RemediationsRequest(BaseModel):
    """The user's answers at the pre-training gate: {issue_type: option_code}.

    Both sides are stable English codes from `forecasting_core.data.gate`; the
    Spanish the user reads comes from the frontend catalogue. Validated against
    the LIVE gate in the handler rather than against an enum here — an option
    that is spelled correctly but answers a finding this file does not have is
    still a decision that changes nothing.
    """
    remediations: Dict[str, str] = Field(default_factory=dict, max_length=40)


class CanonicalColumnsRequest(BaseModel):
    """New canonical 14-field column mapping request."""
    canonical_mapping: Dict[str, Optional[str]] = {}
    defaults_override: Dict[str, Any] = {}

    def validate_required(self, available_columns: list[str]) -> None:
        """
        Raise an ``AppError`` (a ``ValueError``, so the API's existing
        ``except ValueError`` still catches it) when required fields are missing
        or mapped to columns that don't exist.

        This used to raise hand-written Spanish sentences straight out of backend
        logic, which CLAUDE.md forbids: the backend answers in English or with a
        code + params, and the frontend renders the Spanish. The dynamic values
        (which fields, which columns) now travel in ``params`` instead of being
        interpolated into a sentence, so a translator can reorder them.
        """
        from forecasting_core.data.canonical import REQUIRED_FIELDS

        missing = sorted(
            field for field in REQUIRED_FIELDS if not self.canonical_mapping.get(field)
        )
        if missing:
            raise AppError(
                "canonical_columns_missing",
                f"Required column mapping is missing for: {', '.join(missing)}.",
                status_code=422,
                params={"fields": ", ".join(missing)},
            )

        # Every mapped field, required or not — a typo'd optional column is just
        # as absent from the file as a typo'd required one.
        unmapped = sorted(
            (field, src) for field, src in self.canonical_mapping.items()
            if src and src not in available_columns
        )
        if unmapped:
            fields = ", ".join(field for field, _ in unmapped)
            chosen = ", ".join(src for _, src in unmapped)
            raise AppError(
                "canonical_columns_not_in_file",
                f"The columns chosen for {fields} are not in the file: {chosen}. "
                f"Available columns: {', '.join(available_columns)}.",
                status_code=422,
                params={
                    "fields": fields,
                    "chosen": chosen,
                    "columns": ", ".join(available_columns),
                },
            )


# A feature window is a count of periods BACKWARD. The engine builds each kind
# with a different pandas call (ForecastingCore .../features/engineer.py), and
# each breaks differently when the value is not a positive count:
#   lags     `shift(l)`            — a NEGATIVE lag shifts FORWARD, so `lag_-1`
#                                    hands the model tomorrow's demand. The
#                                    Trainer's leakage guard only drops features
#                                    IDENTICAL to the target, so a shifted-future
#                                    one sails through and the run reports an
#                                    accuracy it can never reproduce live.
#   diffs    `shift(1).diff(d)`    — negative d differences against a future row:
#                                    the same leakage, one step further removed.
#   rolling  `rolling(w)`          — w = 0 yields an all-NaN column that survives
#                                    `min_periods=1`; pandas raises for w < 0 and
#                                    the training job dies on a config the API
#                                    had already blessed.
#   ewm      `ewm(span=s)`         — pandas requires span >= 1 and raises below it.
# The ceiling is one year of periods. `validation/semantic.py` already WARNS once
# the largest lag passes 80% of the shortest series, so a window beyond a year of
# daily history is not a choice the product supports — it is a typo, and it costs
# one all-NaN column per SKU. The wizard's own longest default is 28.
_FeatureWindow = Annotated[int, Field(ge=1, le=365)]

# Fourier periods are DIVISORS: `fourier_frame` computes 2*pi*k*t/period, so 0 is
# a ZeroDivisionError inside the worker and 1 makes every term a constant. 366 is
# an annual cycle on daily data — the longest the granularity family fans out to.
_FourierPeriod = Annotated[int, Field(ge=2, le=366)]

# Each list entry is at least one generated column per SKU (rolling: five). 50 is
# ~12x the wizard's longest default list and still a feature matrix a worker can
# hold; 10 000 entries was accepted and would materialize 10 000 columns.
_MAX_FEATURE_TERMS = 50


def _supported_holiday_countries() -> Optional[frozenset[str]]:
    """Every country code the engine's holiday calendar can actually resolve.

    Enumerated from the `holidays` package rather than copied, so it cannot drift
    from what `holidays.country_holidays(...)` accepts.

    Returns None when the package is not installed. That is not a silent pass:
    the caller still enforces the SHAPE of an ISO code (2-3 ASCII letters), which
    is what rejects "", "ZZZZ"-length garbage, emoji and 200-letter strings. It
    only means we cannot tell a real code from a well-formed fake one — and in
    that install the engine degrades to Easter + Christmas for EVERY country
    anyway, so there is nothing left to protect.
    """
    try:
        import holidays
    except Exception:
        return None
    try:
        return frozenset(holidays.list_supported_countries().keys())
    except Exception:
        return None


class FeaturesConfigRequest(BaseModel):
    lags: List[_FeatureWindow] = Field(default=[1, 7, 14, 28], max_length=_MAX_FEATURE_TERMS)
    rolling: List[_FeatureWindow] = Field(default=[7, 14, 28], max_length=_MAX_FEATURE_TERMS)
    diffs: List[_FeatureWindow] = Field(default=[1], max_length=_MAX_FEATURE_TERMS)
    calendar: bool = True
    ewm_spans: List[_FeatureWindow] = Field(default=[], max_length=_MAX_FEATURE_TERMS)
    # e.g. [7, 30, 365]
    fourier_periods: List[_FourierPeriod] = Field(default=[], max_length=_MAX_FEATURE_TERMS)
    # Harmonics per period, two columns each. Below 1, `range(1, K + 1)` is empty:
    # every period the user asked for produces NO columns and nothing says so.
    # The ceiling is the Nyquist limit of the longest cycle the product uses — a
    # 52-period annual-on-weekly cycle carries no information above its 26th
    # harmonic, so beyond that the terms are aliases of lower ones. 1e6 harmonics
    # over three periods is six million columns.
    fourier_K: int = Field(default=2, ge=1, le=26)
    # ISO country code for the holiday calendar. Holidays are among the
    # strongest signals a daily retail series carries, and the product sells
    # across LatAm — a Colombian calendar is simply the wrong one for a
    # distributor in Mexico. "CO" preserves the historical behaviour.
    #
    # Bounded because the failure downstream is SILENT: `HolidayCalendar._load_year`
    # catches the `NotImplementedError` that `holidays.country_holidays("ZZZZ")`
    # raises and degrades to Easter + Christmas only, logging one server line the
    # user never sees. A typo'd country therefore produces a measurably worse
    # model that still reports success. min_length/max_length run first so a 5 MB
    # string is refused before the validator (and before FastAPI echoes it back
    # inside the 422 body).
    #
    # "" is REFUSED rather than quietly meaning "CO". `HolidayCalendar` reads it
    # as `country or DEFAULT_COUNTRY` and lands on Colombia, which is the exact
    # defect in a milder form: a Mexican distributor who cleared the field gets a
    # Colombian calendar and is never told. Omitting the field still gets "CO" —
    # that is the documented default. Sending "" is a user who erased their
    # answer, and the honest response is to ask again.
    holiday_country: str = Field(default="CO", min_length=2, max_length=64)

    @field_validator("holiday_country")
    @classmethod
    def _country_has_a_holiday_calendar(cls, value: str) -> str:
        code = value.strip().upper()
        supported = _supported_holiday_countries()
        known = code.isascii() and code.isalpha() and 2 <= len(code) <= 3
        if supported is not None:
            known = code in supported
        if not known:
            raise PydanticCustomError(
                "unknown_country",
                "'{country}' has no holiday calendar. Use an ISO country code "
                "the `holidays` package supports, such as CO, MX, PE or CL.",
                {"country": code[:8]},
            )
        return code


class ModelEntry(BaseModel):
    name: str
    params: Dict[str, Any] = {}


class ModelsConfigRequest(BaseModel):
    # `mode` has exactly two implementations in `workers/runner.py`: "all" swaps
    # the user's list for `ModelFactory.available_models()`, anything else keeps
    # the list. So "garbage" was silently a synonym for "selected" — a third
    # value the API accepted and nothing implements.
    mode: Literal["selected", "all"] = "selected"
    # The legal set is closed and known at request time. Unvalidated, the wizard
    # accepted "definitely_not_a_model", advanced the session to
    # MODELS_CONFIGURED and reported success; the runner then built `{name: {}}`
    # for it and the factory skipped what it does not know. The session trained
    # FEWER models than the user picked — or none at all — and said so nowhere.
    selected_models: List[str] = []
    hyperparameters: Dict[str, Dict[str, Any]] = {}
    auto_select_best: bool = True
    # Deliberately NOT an enum: nothing reads it. `build_engine_config` never
    # copies it into the engine config and no service queries it — it is written
    # to `models_cfg` and echoed back by GET /configure/models, and that is all.
    # An enum here would be a rule with no consumer to enforce, exactly the kind
    # of invented constraint that later rejects a value the product wants. The
    # length cap is only so a 5 MB string cannot be parked in JSONB.
    selection_metric: str = Field(default="wape", max_length=64)

    @field_validator("selected_models")
    @classmethod
    def _models_are_trainable(cls, values: List[str]) -> List[str]:
        # Imported here rather than at module scope so this schema module stays
        # free of the ML package, and NOT wrapped in try/except: if the engine
        # cannot be imported the backend cannot train at all, and quietly
        # accepting every name would restore the very bug this closes.
        from forecasting_core.models.factory import ModelFactory

        allowed = set(ModelFactory.available_models())
        # The cap IS the size of the legal set, so it cannot drift from it. A
        # 10 000-name list can only be duplicates, and they reach JSONB verbatim.
        if len(values) > len(allowed):
            raise PydanticCustomError(
                "too_many_models",
                "At most {max_models} models can be selected; got {count}.",
                {"max_models": len(allowed), "count": len(values)},
            )
        unknown = [m for m in values if m not in allowed]
        if unknown:
            raise PydanticCustomError(
                "unknown_model",
                "Unknown model '{model}'. Available: {allowed}.",
                {"model": str(unknown[0])[:64], "allowed": ", ".join(sorted(allowed))},
            )
        return values

    @field_validator("hyperparameters")
    @classmethod
    def _hyperparameters_reach_a_real_model(
        cls, values: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Keyed by model name, holding flat scalars, and bounded.

        `workers/runner.py` builds `{m: hyperparams.get(m, {})}` and hands it to
        the engine, so whatever lands here is passed to the library's
        constructor. Three things were possible and none of them was reported:

        * A block keyed by a model that does not exist — dead config that looks
          live. `selected_models` already refuses an unknown name; this is the
          same value arriving through the other field.
        * A nested dict or list where the library expects a scalar, which fails
          deep inside the fit with a library traceback and takes the whole run
          down. The file was fine; the config killed it.
        * Unbounded size, straight into JSONB.

        Deliberately NOT a per-library bound check. Whether `n_estimators` may
        be negative is LightGBM's rule to state, not ours to guess, and a table
        of invented limits here would reject values the product may later want.
        What is checked is the shape the runner and the factory require.
        """
        from forecasting_core.models.factory import ModelFactory

        allowed = set(ModelFactory.available_models())
        unknown = [m for m in values if m not in allowed]
        if unknown:
            raise PydanticCustomError(
                "unknown_model",
                "Hyperparameters given for unknown model '{model}'. Available: {allowed}.",
                {"model": str(unknown[0])[:64], "allowed": ", ".join(sorted(allowed))},
            )
        for model, params in values.items():
            if len(params) > 100:
                raise PydanticCustomError(
                    "too_many_hyperparameters",
                    "Model '{model}' was given {count} hyperparameters; at most 100.",
                    {"model": model, "count": len(params)},
                )
            for key, value in params.items():
                if not isinstance(value, (str, int, float, bool)) and value is not None:
                    raise PydanticCustomError(
                        "hyperparameter_not_scalar",
                        "Hyperparameter '{key}' for '{model}' must be a single "
                        "value, not a list or an object.",
                        {"key": str(key)[:64], "model": model},
                    )
                if isinstance(value, str) and len(value) > 256:
                    raise PydanticCustomError(
                        "hyperparameter_too_long",
                        "Hyperparameter '{key}' for '{model}' is too long.",
                        {"key": str(key)[:64], "model": model},
                    )
        return values

    @model_validator(mode="after")
    def _selected_mode_needs_at_least_one_model(self):
        # An empty list under mode "selected" is a session configured to train
        # zero models: `models_dict` comes out `{}`, the state machine still
        # advances to MODELS_CONFIGURED, and the run produces no forecast for
        # anything. Under mode "all" the list is legitimately ignored.
        if self.mode == "selected" and not self.selected_models:
            raise PydanticCustomError(
                "no_models_selected",
                "Select at least one model to train.",
                {},
            )
        return self


class ValidationConfigRequest(BaseModel):
    # Bounded here because the ENGINE bounds it: `SessionConfig.validate()` raises
    # ConfigError("training.train_ratio must be between 0 and 1"), and the runner
    # copies this value into the engine config verbatim. Unbounded, the wizard
    # accepted 1.5, stored it, reported success, and then the training job died on
    # a config the API had already blessed — a session sold that cannot train.
    train_ratio: float = Field(default=0.8, gt=0, lt=1)
    walk_forward: bool = True
    # The three below are NOT checked by `SessionConfig.validate()`, so the bounds
    # are the engine's own published ones — the ranges it declares as legal in
    # `validation/schema.py` `_RULES` and `SessionConfig.schema()`. Inventing a
    # tighter range here would reject configs the engine accepts, which is the same
    # class of lie as accepting ones it rejects.
    #
    # The engine publishes a `min` for each and no `max`, so the ceilings below are
    # chosen from what the value DOES, not invented — and each one only rules out
    # magnitudes at which the run is guaranteed to produce nothing:
    #   min_history      is compared against each series' row count. Above the
    #                    longest history any upload can carry, EVERY series is
    #                    skipped and the run completes with zero forecasts,
    #                    reporting success. 10 000 daily rows is ~27 years.
    #   seasonal_period  becomes `m` for ARIMA/ETS/SARIMAX. statsmodels allocates
    #                    per-season state, so 1e9 is an out-of-memory kill of the
    #                    worker, not a fit. 366 is an annual cycle on daily data —
    #                    the longest the granularity family produces.
    #   horizon          the runner reads THIS field as the fallback forecast
    #                    horizon (`forecast_cfg.get("horizon", validation_cfg
    #                    .get("horizon", 14))`), so leaving it open re-opens on
    #                    this endpoint exactly what `ForecastConfigRequest.horizon`
    #                    closes on the other. Same quantity, same 365.
    wfv_splits: int = Field(default=3, ge=1, le=10)
    min_history: int = Field(default=20, ge=5, le=10_000)
    seasonal_period: int = Field(default=7, ge=2, le=366)
    horizon: int = Field(default=14, ge=1, le=365)  # `SessionConfig.validate()`: horizon >= 1


_HORIZON_LIMITS = {"D": (1, 30), "W": (1, 12), "2W": (1, 6), "MS": (1, 12)}


class ForecastConfigRequest(BaseModel):
    # Bounded for the same reason `ValidationConfigRequest.train_ratio` is, and
    # the bound was missing here while the identical field on that model had it:
    # `SessionConfig.validate()` raises ConfigError("forecast.horizon must be
    # >= 1"), so a 0 or -1 accepted here is a session the API blessed and the
    # training job then dies on. The upper bound is the same 365 the engine can
    # meaningfully forecast; beyond it the request is a typo, not a plan.
    horizon: int = Field(default=14, ge=1, le=365)
    # Probabilities. Outside (0, 1) they are not quantiles, and NaN survives an
    # unbounded float field all the way into JSONB — where Postgres stores it as
    # null and every later read gets a None the engine never checks.
    quantiles: List[float] = Field(default=[0.1, 0.9])
    horizon_mode: str = "unified"                            # "unified" | "segmented"
    horizon_by_freq: Optional[Dict[str, int]] = None         # {"D": 10, "W": 4, ...}

    @field_validator("quantiles")
    @classmethod
    def _quantiles_are_probabilities(cls, values: List[float]) -> List[float]:
        for q in values:
            if not math.isfinite(q) or not (0.0 < q < 1.0):
                raise ValueError(
                    f"quantile {q!r} must be a finite number strictly between 0 and 1"
                )
        return values

    @model_validator(mode="after")
    def _validate_horizon_by_freq(self):
        if self.horizon_by_freq:
            for freq, value in self.horizon_by_freq.items():
                bounds = _HORIZON_LIMITS.get(freq)
                if bounds is None:
                    raise ValueError(f"Unknown frequency bucket '{freq}'")
                lo, hi = bounds
                if not (lo <= value <= hi):
                    raise ValueError(f"horizon_by_freq['{freq}']={value} must be between {lo} and {hi}")
        return self


class BusinessConfigRequest(BaseModel):
    # Every bound below already existed SOMEWHERE else for the same quantity —
    # `service_level` is `ge=0.5, le=0.999` as a query param on
    # /inventory/status, and `lead_time_days` is `ge=1, le=365` in StockUpsert.
    # Bounded on the read path and wide open on the write path is the worst of
    # both: the API blesses a value its own reader would reject.
    #
    # What each one costs when it gets through:
    #   service_level    -> z-score lookup; outside (0,1) there is no quantile
    #                       to look up, and NaN reaches JSONB, becomes null, and
    #                       then `business_cfg.get("service_level", 0.95)` does
    #                       NOT fall back, because the key exists with None.
    #   lead_time_days   -> multiplies demand into the reorder point; 0 removes
    #                       the protection interval, 1e9 orders a lifetime.
    #   holding_cost_pct -> read verbatim into the MILP objective
    #                       (inventory/optimizer_service.py). Negative makes it
    #                       PROFITABLE to hold stock, so the solver accumulates
    #                       inventory without bound.
    service_level: float = Field(default=0.95, gt=0.0, lt=1.0)
    lead_time_days: int = Field(default=7, ge=1, le=365)
    holding_cost_pct: float = Field(default=0.20, ge=0.0, le=10.0)
    # The MILP optimizer (backend/inventory/optimizer_service.py) derives
    # stockout_cost = order_cost * stockout_cost_multiplier. A value < 1
    # would make stockout_cost < order_cost, at which point the solver finds
    # it mathematically cheaper to leave demand permanently unmet than to
    # ever place an order — silently zeroing out every recommendation.
    stockout_cost_multiplier: float = Field(default=3.0, ge=1.0)
