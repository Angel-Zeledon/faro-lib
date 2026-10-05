"""
Serialising a trained run into artifacts, and reading them back.

One artifact FILE per model family (``lightgbm``, ``xgboost``, ``global_lgbm``,
``arima``, ``ets``, ``croston``, ``prophet`` ...) holding every series that
family was trained for, plus one ``context`` file with everything that is not a
model: the config, the input schema, the metrics table the champions were chosen
from, the demand-risk bands, the training window. A family per file keeps the
count of rows the backend registers at a handful per session instead of
thousands, and a family is the natural unit of "which kind of model is this".

See ``codec`` for the format and the trust boundary: JSON + gzip, native model
formats for LightGBM / XGBoost, no pickle, SHA-256 verified before parsing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from forecasting_core.reforecast import codec
from forecasting_core.reforecast.prepare import (
    bucket_seconds, input_schema, prepare_history, schema_hash, series_anchors,
)

log = logging.getLogger(__name__)

CONTEXT_FAMILY = "context"

# How a family restores, which decides what `reforecast` can do for it.
KIND_ML_RECURSIVE = "ml_recursive"        # per-series tree model, recursive forecast
KIND_ML_GLOBAL = "ml_global_direct"       # one model over every series, direct horizons
KIND_STAT_STATE = "stat_state"            # fitted parameters, filter over new history
KIND_STAT_REFIT = "stat_refit_only"       # nothing to carry over: needs a refit

_LIBRARIES = ("lightgbm", "xgboost", "prophet", "statsmodels", "scikit-learn",
              "numpy", "pandas", "scipy")


def library_versions() -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for name in _LIBRARIES:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def _engine_version() -> str:
    from forecasting_core import __version__
    return __version__


@dataclass
class ArtifactFile:
    """One stored artifact. ``data`` is the exact byte string to write to disk;
    ``sha256`` is what the caller records and later passes to ``load``."""
    family: str
    kind: str
    data: bytes
    sha256: str
    size_bytes: int
    metadata: dict = field(default_factory=dict)


@dataclass
class ArtifactSet:
    files: List[ArtifactFile]

    def by_family(self, family: str) -> Optional[ArtifactFile]:
        return next((f for f in self.files if f.family == family), None)


@dataclass
class LoadedArtifacts:
    """Verified, parsed artifacts: ``context`` plus one payload per family."""
    context: dict
    families: Dict[str, dict]


# -- native model serialisation -------------------------------------------

def _native_model(model: Any) -> Optional[dict]:
    """The library's own serialisation of a fitted tree model, or None when it is
    not a model this module knows how to carry (the unit is then not restorable
    and `reforecast` reports that rather than guessing)."""
    try:
        import lightgbm
        if isinstance(model, lightgbm.LGBMModel):
            return {"format": "lightgbm_text", "data": model.booster_.model_to_string()}
    except Exception as exc:  # noqa: BLE001
        log.warning("could not serialise a LightGBM model: %s", exc)
        return None
    try:
        import xgboost
        if isinstance(model, xgboost.XGBModel):
            return {"format": "xgboost_ubj",
                    "data": bytes(model.get_booster().save_raw("ubj"))}
    except Exception as exc:  # noqa: BLE001
        log.warning("could not serialise an XGBoost model: %s", exc)
    return None


class _Booster:
    """`.predict(X)` over a restored LightGBM booster."""

    def __init__(self, booster):
        self._booster = booster

    def predict(self, X):
        return np.asarray(self._booster.predict(X), dtype=float)


def restore_native(native: Optional[dict]):
    """Rebuild a predicting model from `_native_model`'s output, or None."""
    if not native:
        return None
    fmt = native.get("format")
    if fmt == "lightgbm_text":
        import lightgbm
        return _Booster(lightgbm.Booster(model_str=native["data"]))
    if fmt == "xgboost_ubj":
        import xgboost
        model = xgboost.XGBRegressor()
        model.load_model(bytearray(native["data"]))
        return model
    return None


def _hyperparameters(model: Any) -> dict:
    try:
        params = model.get_params()
    except Exception:  # noqa: BLE001
        return {}
    return {k: v for k, v in params.items() if isinstance(v, (int, float, str, bool, type(None)))}


# -- building ---------------------------------------------------------------

def _champions(config, metrics_df: Optional[pd.DataFrame]) -> Dict[str, str]:
    if metrics_df is None or metrics_df.empty:
        return {}
    from forecasting_core.pipelines.pipeline import Pipeline
    try:
        return Pipeline(config)._select_champions(metrics_df, lambda x: str(x).strip())
    except Exception as exc:  # noqa: BLE001
        log.warning("could not derive champions for the artifact context: %s", exc)
        return {}


def _records(df: Optional[pd.DataFrame]) -> list:
    if df is None or df.empty:
        return []
    out = df.astype(object).where(df.notna(), None)
    return out.to_dict(orient="records")


def _shap(fitted_models: Dict[str, dict]) -> dict:
    """{sku: {model: importance}}, the same shape `get_metrics()["shap"]` has."""
    out: Dict[str, dict] = {}
    for key, entry in fitted_models.items():
        imp = entry.get("shap_importance")
        if imp:
            out.setdefault(str(entry.get("sku", key)), {})[str(entry.get("model", key))] = imp
    return out


def _window(anchors: Dict[str, dict]) -> dict:
    if not anchors:
        return {"first": None, "last": None}
    return {"first": min(a["first"] for a in anchors.values()),
            "last": max(a["last"] for a in anchors.values())}


def _ml_units(entries: Dict[str, dict], anchors: Dict[str, dict]) -> Tuple[dict, dict]:
    units: Dict[str, dict] = {}
    hyper: dict = {}
    for key, entry in entries.items():
        sku = str(entry.get("sku", "__all__"))
        native = _native_model(entry.get("fitted_model"))
        a = anchors.get(sku, {})
        units[key] = {
            "sku": sku, "store": entry.get("store"),
            "native": native,
            "feature_names": list(entry.get("feature_names") or []),
            "residuals": np.asarray(entry.get("residuals", []), dtype=float),
            "trained_first_date": a.get("first"), "trained_last_date": a.get("last"),
            "n_rows": a.get("n"),
        }
        if not hyper and entry.get("fitted_model") is not None:
            hyper = _hyperparameters(entry["fitted_model"])
    return units, hyper


def _global_family(entries: Dict[str, dict], anchors: Dict[str, dict]) -> Tuple[dict, dict]:
    shared: dict = {}
    units: Dict[str, dict] = {}
    hyper: dict = {}
    for key, entry in entries.items():
        forecaster = entry.get("direct_forecaster")
        if forecaster is None:
            continue
        if not shared:
            shared = {
                "native": _native_model(forecaster.model),
                "model_features": list(forecaster.model_features),
                "scaled_features": list(forecaster.scaled_features),
                "residuals_by_horizon": [
                    [int(h), np.asarray(v, dtype=float)]
                    for h, v in forecaster.residuals_by_horizon.items()],
                "cumulative_residuals_by_horizon": [
                    [int(h), np.asarray(v, dtype=float)]
                    for h, v in forecaster.cumulative_residuals_by_horizon.items()],
            }
            hyper = _hyperparameters(forecaster.model)
        p = forecaster.profile
        sku = str(entry.get("sku", "__all__"))
        a = anchors.get(sku, {})
        units[key] = {
            "sku": sku, "store": entry.get("store"),
            "profile": {"key": p.key, "sku": p.sku, "store": p.store,
                        "codes": [int(x) for x in p.codes], "scale": float(p.scale),
                        "cv": float(p.cv), "n_rows": int(p.n_rows)},
            "feature_names": list(entry.get("feature_names") or []),
            "trained_first_date": a.get("first"), "trained_last_date": a.get("last"),
            "n_rows": a.get("n"),
        }
    return {"shared": shared, "units": units}, hyper


def _stat_units(model: str, by_sku: Dict[str, dict], anchors: Dict[str, dict]) -> Tuple[dict, str]:
    units: Dict[str, dict] = {}
    kind = KIND_STAT_REFIT
    for sku, res in by_sku.items():
        if not isinstance(res, dict):
            continue
        state = res.get("state")
        if state:
            kind = KIND_STAT_STATE
        a = anchors.get(str(sku), {})
        units[str(sku)] = {
            "sku": str(sku),
            "state": state,
            "residuals": np.asarray(res.get("residuals", []), dtype=float),
            "trained_first_date": a.get("first"), "trained_last_date": a.get("last"),
            "n_rows": a.get("n"),
        }
    return units, kind


def build_artifact_set(engine) -> ArtifactSet:
    """Serialise a trained ``ForecastEngine``. Raises ``ValueError`` when there is
    nothing trained to serialise."""
    config = engine._config
    if config is None or (not engine._fitted_models and not engine._stat_forecasts):
        raise ValueError("nothing trained to persist")

    prepared = prepare_history(config, engine._df)
    anchors = series_anchors(config, prepared)
    schema = input_schema(config)
    sch_hash = schema_hash(schema)
    window = _window(anchors)
    versions = library_versions()
    horizon = int(config.forecast.horizon)
    champions = _champions(config, engine._metrics_df)

    # ML entries grouped by family.
    ml_by_family: Dict[str, Dict[str, dict]] = {}
    for key, entry in engine._fitted_models.items():
        ml_by_family.setdefault(str(entry.get("model", "unknown")), {})[key] = entry

    created = datetime.now(timezone.utc).isoformat()
    files: List[ArtifactFile] = []

    def _add(family: str, kind: str, payload: dict, meta: dict) -> None:
        payload = {"schema": codec.SCHEMA_VERSION, "format": codec.FORMAT,
                   "family": family, "kind": kind, **payload}
        data = codec.pack(payload)
        meta = {
            "model_family": family, "kind": kind, "created_at": created,
            "training_window": window, "feature_spec": schema["features"],
            "input_schema_hash": sch_hash, "engine_version": _engine_version(),
            "library_versions": versions, "trained_horizon": horizon,
            **meta,
        }
        files.append(ArtifactFile(family=family, kind=kind, data=data,
                                  sha256=codec.sha256_hex(data),
                                  size_bytes=len(data), metadata=meta))

    for family, entries in ml_by_family.items():
        if any(e.get("forecast_strategy") == "direct" for e in entries.values()):
            body, hyper = _global_family(entries, anchors)
            kind = KIND_ML_GLOBAL
            # The model was fitted for `horizon + 1` buckets (see
            # GlobalTrainer.train_horizon) so that future step `horizon`, one
            # bucket behind the origin, stays inside what it learned. The
            # horizon recorded here is the one it can SERVE.
            payload = {"shared": body["shared"], "units": body["units"],
                       "trained_horizon": horizon}
            n_units = len(body["units"])
        else:
            units, hyper = _ml_units(entries, anchors)
            kind = KIND_ML_RECURSIVE
            payload = {"units": units}
            n_units = len(units)
        _add(family, kind, payload, {
            "series_count": n_units,
            "champion_series": sum(1 for u in (payload["units"]).values()
                                   if champions.get(u["sku"]) == family),
            "hyperparameters": {**hyper, **(config.models.get(family) or {})},
            "restorable": all(u.get("native") is not None
                              for u in payload["units"].values()) if kind == KIND_ML_RECURSIVE
            else payload["shared"].get("native") is not None,
        })

    for family, by_sku in engine._stat_forecasts.items():
        units, kind = _stat_units(family, by_sku, anchors)
        if not units:
            continue
        _add(family, kind, {"units": units}, {
            "series_count": len(units),
            "champion_series": sum(1 for u in units.values() if champions.get(u["sku"]) == family),
            "hyperparameters": dict(config.models.get(family) or {}),
            "restorable": kind == KIND_STAT_STATE,
        })

    context = {
        "run_id": engine._run_id,
        "engine_version": _engine_version(),
        "library_versions": versions,
        "created_at": created,
        "config": config.to_dict(),
        "input_schema": schema,
        "input_schema_hash": sch_hash,
        "trained_horizon": horizon,
        "bucket_seconds": bucket_seconds(prepared, config.columns.date),
        "data_window": window,
        "series_anchors": anchors,
        "champions": champions,
        "metrics_rows": _records(engine._metrics_df),
        "inventory_rows": _records(engine._inventory_df),
        "shap": _shap(engine._fitted_models),
        "demand_risk": getattr(engine, "_demand_risk", {}) or {},
        "policy_backtest": getattr(engine, "_policy_backtest", {}) or {},
        "run_metadata": {
            k: (engine._run_metadata or {}).get(k)
            for k in ("validation_findings", "corrections", "censoring",
                      "outperformed_by_baseline", "skipped_no_forecast", "n_skus")
        },
        "families": {f.family: {"kind": f.kind, "sha256": f.sha256,
                                "series_count": f.metadata.get("series_count")}
                     for f in files},
    }
    ctx_payload = {"schema": codec.SCHEMA_VERSION, "format": codec.FORMAT,
                   "family": CONTEXT_FAMILY, "kind": CONTEXT_FAMILY, **context}
    data = codec.pack(ctx_payload)
    files.insert(0, ArtifactFile(
        family=CONTEXT_FAMILY, kind=CONTEXT_FAMILY, data=data,
        sha256=codec.sha256_hex(data), size_bytes=len(data),
        metadata={"model_family": CONTEXT_FAMILY, "kind": CONTEXT_FAMILY,
                  "created_at": created, "training_window": window,
                  "feature_spec": schema["features"], "input_schema_hash": sch_hash,
                  "engine_version": _engine_version(), "library_versions": versions,
                  "trained_horizon": horizon, "series_count": len(anchors),
                  "hyperparameters": {}, "restorable": True}))
    return ArtifactSet(files=files)


# -- loading ----------------------------------------------------------------

def load_artifact_set(files: Iterable[Tuple[str, bytes, str]]) -> LoadedArtifacts:
    """Verify (hash first) and parse ``(family, bytes, expected_sha256)`` triples.

    Raises ``codec.ArtifactIntegrityError`` for any file that does not match its
    digest, and ``ValueError`` when there is no context among them."""
    context: Optional[dict] = None
    families: Dict[str, dict] = {}
    for family, data, expected in files:
        payload = codec.unpack(data, expected)
        if payload.get("family") != family:
            raise codec.ArtifactIntegrityError(
                f"artifact registered as {family!r} declares family {payload.get('family')!r}")
        if family == CONTEXT_FAMILY:
            context = payload
        else:
            families[family] = payload
    if context is None:
        raise ValueError("the artifact set has no context file")
    return LoadedArtifacts(context=context, families=families)
