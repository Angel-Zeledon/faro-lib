import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel, field_validator

from backend.auth.guards import (
    CurrentUser, get_current_user, require_analyst_or_above,
)
from backend.db import session_store
from backend.db.connection import execute, query
from backend.datasets.service import get_dataset
from backend.errors import AppError
from backend.schemas.common import ok
from backend.schemas.forecast import PredictRequest
from backend.sessions import service as session_svc
from backend.workers.runner import build_engine_config

log = logging.getLogger(__name__)

router = APIRouter(tags=["forecasts"])


def _enrich_forecast_points(pts: list) -> list:
    """Add explicit p10/p50/p90 aliases to each forecast point if not already present."""
    out = []
    for pt in pts:
        p = dict(pt)
        if "p10" not in p:
            p["p10"] = p.get("q10", p.get("lower", p.get("value")))
        if "p50" not in p:
            p["p50"] = p.get("q50", p.get("value"))
        if "p90" not in p:
            p["p90"] = p.get("q90", p.get("upper", p.get("value")))
        out.append(p)
    return out


def _historical_for_sku(tenant_id: str, session_id: str, sku: str) -> list:
    """Historical series for one SKU: the inspection blob first, the raw dataset
    file second.

    Single source of truth on purpose. The forecast chart, the SKU-intelligence
    panel and the decomposition all draw the same history; a second way to build
    it is a second answer to "what did this SKU actually sell", and the two
    would drift.

    Returns [] (never raises) when the session has no usable history.
    """
    inspection = session_store.get_field(tenant_id, session_id, "inspection") or {}
    historical = list(inspection.get("historical_series", {}).get(sku, []))
    if historical:
        return historical

    s = session_svc.get_session(tenant_id, session_id)
    dataset_id = s.get("dataset_id") if s else None
    col_info = session_store.get_field(tenant_id, session_id, "columns_cfg") or {}
    if not dataset_id or not col_info:
        return []
    ds_meta = get_dataset(tenant_id, dataset_id)
    data_path = ds_meta.get("file_path") if ds_meta else None
    if not data_path:
        return []
    try:
        from backend.dataframes.series import historical_series
        dt_col     = col_info.get("date_column")   or col_info.get("date")
        target_col = col_info.get("target_column") or col_info.get("target")
        sku_col    = col_info.get("sku_column")    or col_info.get("sku") or col_info.get("group")
        if not (dt_col and target_col and sku_col):
            return []
        return [
            pt for pt in historical_series(data_path, dt_col, target_col, sku_col, sku=sku)
            if pt["value"] is not None
        ]
    except Exception as exc:
        log.warning("Could not build historical series for %s: %s", sku, exc)
        return []


def _require_completed(tenant_id: str, session_id: str) -> dict:
    s = session_svc.get_session(tenant_id, session_id)
    if not s:
        raise AppError("session_not_found", "Session not found", status_code=404)
    if s["status"] != "COMPLETED":
        # Opening any results screen while the run is still going trips this —
        # the single most-hit guard in this module, so it carries the status.
        raise AppError(
            "session_still_training",
            f"Session must be COMPLETED. Current: {s['status']}",
            status_code=409,
            params={"status": s["status"]},
        )
    return s


@router.get("/sessions/{session_id}/results")
def get_training_results(
    session_id: str,
    level: Optional[str] = Query(None, description="Filter by hierarchy level value"),
    user: CurrentUser = Depends(get_current_user),
):
    _require_completed(user.tenant_id, session_id)
    result = session_store.get_training_result(user.tenant_id, session_id)
    if not result:
        raise AppError(
            "training_results_not_found", "Training results not found", status_code=404,
        )
    if level:
        rows = result.get("metrics", {}).get("rows", [])
        result = dict(result)
        result["metrics"] = {
            **result.get("metrics", {}),
            "rows": [r for r in rows if str(r.get("sku", "")).startswith(f"{level}/") or r.get("level") == level],
        }
    return ok(result)


@router.get("/sessions/{session_id}/warnings")
def get_run_warnings(session_id: str, user: CurrentUser = Depends(get_current_user)):
    """Data problems the validation layers found while training this session.

    They never abort a run, so without this the only trace was the server log —
    and target leakage in particular yields a near-perfect score on a forecast
    that is worthless. Codes are English; the frontend renders the Spanish.
    """
    _require_completed(user.tenant_id, session_id)
    result = session_store.get_training_result(user.tenant_id, session_id)
    if not result:
        raise AppError(
            "training_results_not_found", "Training results not found", status_code=404,
        )
    warnings = result.get("warnings") or {}
    return ok({
        "validation":  warnings.get("validation") or [],
        "corrections": warnings.get("corrections") or [],
    })


@router.get("/sessions/{session_id}/metrics")
def get_metrics(session_id: str, user: CurrentUser = Depends(get_current_user)):
    _require_completed(user.tenant_id, session_id)
    result = session_store.get_training_result(user.tenant_id, session_id)
    if not result:
        raise AppError(
            "training_results_not_found", "Training results not found", status_code=404,
        )
    return ok(result.get("metrics", {}))


@router.get("/sessions/{session_id}/inventory")
def get_inventory(session_id: str, user: CurrentUser = Depends(get_current_user)):
    _require_completed(user.tenant_id, session_id)
    result = session_store.get_training_result(user.tenant_id, session_id)
    if not result:
        raise AppError(
            "training_results_not_found", "Training results not found", status_code=404,
        )
    return ok(result.get("inventory", {}))


@router.get("/sessions/{session_id}/routing")
def get_routing(session_id: str, user: CurrentUser = Depends(get_current_user)):
    _require_completed(user.tenant_id, session_id)
    result = session_store.get_training_result(user.tenant_id, session_id)
    return ok(result.get("routing", {}) if result else {})


def _normalize_quality(raw: dict) -> dict:
    """Normalize legacy quality dict keys to the current frontend contract."""
    out = {}
    for sku, q in raw.items():
        # data_quality may carry summary fields (n_series:int, issues:list,
        # rows:list) alongside any per-SKU dicts. Only normalize dict entries;
        # pass everything else through untouched instead of crashing on dict(q).
        if not isinstance(q, dict):
            out[sku] = q
            continue
        entry = dict(q)
        # outliers → n_outliers
        if "n_outliers" not in entry and "outliers" in entry:
            entry["n_outliers"] = entry.pop("outliers")
        elif "outliers" in entry:
            entry.pop("outliers")
        # has_min_history → is_valid
        if "is_valid" not in entry and "has_min_history" in entry:
            entry["is_valid"] = entry["has_min_history"]
        # quality_score: legacy stored as 0-100; normalize to 0-1
        qs = entry.get("quality_score", 0)
        if isinstance(qs, (int, float)) and qs > 1.0:
            entry["quality_score"] = round(qs / 100.0, 4)
        # missing_pct: if absent but missing_dates is present, compute it
        if "missing_pct" not in entry:
            n_rows = entry.get("n_rows", 0) or 0
            missing_dates = entry.get("missing_dates", 0) or 0
            total = n_rows + missing_dates
            entry["missing_pct"] = round(missing_dates / total, 4) if total > 0 else 0.0
        out[sku] = entry
    return out


@router.get("/sessions/{session_id}/quality")
def get_data_quality(session_id: str, user: CurrentUser = Depends(get_current_user)):
    _require_completed(user.tenant_id, session_id)
    result = session_store.get_training_result(user.tenant_id, session_id)
    raw = result.get("data_quality", {}) if result else {}
    return ok(_normalize_quality(raw))


@router.post("/sessions/{session_id}/predict")
def predict(
    session_id: str,
    body: PredictRequest,
    user: CurrentUser = Depends(get_current_user),
):
    _require_completed(user.tenant_id, session_id)
    config = build_engine_config(user.tenant_id, session_id)
    horizon = body.horizon or config.get("forecast", {}).get("horizon", 14)
    forecasts_data = session_store.get_forecasts(user.tenant_id, session_id) or {}
    sku_forecasts = forecasts_data.get(body.sku, {})
    chosen_model = next(iter(sku_forecasts.keys()), None)
    raw = sku_forecasts.get(chosen_model, {}) if chosen_model else {}
    # Handle both new {"historical"/"forecast"} format and legacy flat-list format
    if isinstance(raw, dict):
        forecast_series = raw.get("forecast", [])
    else:
        forecast_series = raw if isinstance(raw, list) else []
    return ok({
        "sku": body.sku,
        "horizon": horizon,
        "model": chosen_model,
        "result": forecast_series,
    })



# ── Forecast Overrides ─────────────────────────────────────────────────────────

class OverrideItem(BaseModel):
    sku:      str
    date:     str
    original: float
    override: float
    reason:   Optional[str] = None

    @field_validator("date")
    @classmethod
    def _valid_date(cls, v: str) -> str:
        try:
            import datetime; datetime.date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"Invalid date '{v}' — expected YYYY-MM-DD")
        return v


@router.patch("/sessions/{session_id}/overrides")
def save_overrides(
    session_id: str,
    body: list[OverrideItem],
    user: CurrentUser = Depends(require_analyst_or_above),
):
    _require_completed(user.tenant_id, session_id)
    for item in body:
        execute(
            """INSERT INTO forecast_overrides
                   (id, tenant_id, session_id, sku, date, original_value, override_value, reason, created_by)
               VALUES (gen_random_uuid()::text, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (session_id, sku, date)
               DO UPDATE SET override_value=%s, reason=%s, created_by=%s""",
            (
                user.tenant_id, session_id, item.sku, item.date,
                item.original, item.override, item.reason, user.user_id,
                item.override, item.reason, user.user_id,
            ),
        )
    log.info("[overrides] saved %d overrides session=%s", len(body), session_id)
    return ok({"saved": len(body)})


@router.get("/sessions/{session_id}/overrides")
def get_overrides(session_id: str, user: CurrentUser = Depends(get_current_user)):
    _require_completed(user.tenant_id, session_id)
    rows = query(
        """SELECT sku, date, original_value AS original, override_value AS override, reason, created_by, created_at
           FROM forecast_overrides
           WHERE session_id = %s AND tenant_id = %s
           ORDER BY date, sku""",
        (session_id, user.tenant_id),
    )
    return ok([dict(r) for r in rows])


# ── Accuracy Tracking ──────────────────────────────────────────────────────────

DEFAULT_WAPE_THRESHOLD = 0.20


@router.post("/sessions/{session_id}/reconcile")
async def reconcile_actuals(
    session_id: str,
    file: UploadFile = File(...),
    date_col:   str = Query("date"),
    sku_col:    Optional[str] = Query(None),
    target_col: str = Query("actual"),
    user: CurrentUser = Depends(get_current_user),
):
    """Upload a CSV of actual values to compare against the stored forecast."""
    _require_completed(user.tenant_id, session_id)
    content = await file.read()
    from backend.dataframes.io import read_rows
    from backend.dataframes.series import normalize_date_column
    try:
        actual_rows = read_rows(content, fmt="csv")
    except Exception as e:
        raise AppError(
            "upload_parse_failed", f"Could not parse CSV: {e}",
            status_code=400, params={"reason": str(e)},
        )

    columns = list(actual_rows[0].keys()) if actual_rows else []
    for col in [date_col, target_col]:
        if actual_rows and col not in columns:
            raise AppError(
                "upload_column_missing",
                f"Column '{col}' not found in uploaded file",
                status_code=400,
                params={"column": col, "columns": ", ".join(columns)},
            )

    actual_rows = normalize_date_column(actual_rows, date_col)

    forecasts_data = session_store.get_forecasts(user.tenant_id, session_id) or {}
    inserted = 0
    for row in actual_rows:
        date = str(row[date_col])[:10]
        sku  = str(row[sku_col]) if sku_col and sku_col in columns else "__all__"
        actual_val = float(row[target_col]) if row[target_col] is not None else None
        if actual_val is None:
            continue

        # Look up stored forecast for this sku+date
        sku_forecasts = forecasts_data.get(sku, {})
        model_key = next(iter(sku_forecasts.keys()), None)
        raw = sku_forecasts.get(model_key, {}) if model_key else {}
        fc_series = raw.get("forecast", []) if isinstance(raw, dict) else []
        forecasted = next((p.get("value") for p in fc_series if str(p.get("date", ""))[:10] == date), None)
        if forecasted is None:
            continue

        mae  = abs(actual_val - forecasted)
        wape = mae / abs(actual_val) if actual_val != 0 else None

        execute(
            """INSERT INTO accuracy_snapshots
                   (id, tenant_id, session_id, sku, date, forecasted, actual, mae, wape)
               VALUES (gen_random_uuid()::text, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (session_id, sku, date)
               DO UPDATE SET actual=%s, mae=%s, wape=%s""",
            (
                user.tenant_id, session_id, sku, date, forecasted, actual_val, mae, wape,
                actual_val, mae, wape,
            ),
        )
        inserted += 1

    log.info("[reconcile] session=%s matched=%d rows", session_id, inserted)
    return ok({"matched_rows": inserted})


@router.get("/sessions/{session_id}/accuracy")
def get_accuracy(
    session_id: str,
    threshold:  float = Query(DEFAULT_WAPE_THRESHOLD, ge=0.0, le=1.0),
    user: CurrentUser = Depends(get_current_user),
):
    _require_completed(user.tenant_id, session_id)
    rows = query(
        """SELECT sku, date::text AS date, forecasted, actual, mae, wape
           FROM accuracy_snapshots
           WHERE session_id = %s AND tenant_id = %s
           ORDER BY sku, date""",
        (session_id, user.tenant_id),
    )
    snapshots = [dict(r) for r in rows]
    wapes = [s["wape"] for s in snapshots if s["wape"] is not None]
    overall_wape = sum(wapes) / len(wapes) if wapes else None
    return ok({"snapshots": snapshots, "overall_wape": overall_wape, "threshold": threshold})


@router.get("/sessions/{session_id}/config-schema")
def get_config_schema(session_id: str, user: CurrentUser = Depends(get_current_user)):
    session_svc.get_session(user.tenant_id, session_id)
    try:
        from forecasting_core.config.config import SessionConfig
        return ok(SessionConfig.schema())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/available-models")
def get_available_models(session_id: str, user: CurrentUser = Depends(get_current_user)):
    try:
        from forecasting_core.models.factory import ModelFactory
        return ok({"models": ModelFactory.available_models()})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/forecast-series/{sku}")
def get_forecast_series(
    session_id: str,
    sku: str,
    model: Optional[str] = Query(None, description="Filter by model name"),
    user: CurrentUser = Depends(get_current_user),
):
    _require_completed(user.tenant_id, session_id)

    result = session_store.get_training_result(user.tenant_id, session_id)
    if not result:
        raise AppError(
            "training_results_not_found", "Training results not found", status_code=404,
        )

    forecasts_data = session_store.get_forecasts(user.tenant_id, session_id) or {}

    # Historical series: inspection blob, then the raw dataset file.
    historical_raw = _historical_for_sku(user.tenant_id, session_id, sku)

    # Pull forecast
    sku_forecasts = forecasts_data.get(sku, {})
    if not sku_forecasts:
        metrics_rows = result.get("metrics", {}).get("rows", [])
        models_in_result = list({r["model"] for r in metrics_rows if r.get("sku") == sku})
        chosen_model = model or (models_in_result[0] if models_in_result else None)
    else:
        chosen_model = model or next(iter(sku_forecasts.keys()), None)

    forecast_series = []
    if chosen_model and isinstance(sku_forecasts, dict):
        raw = sku_forecasts.get(chosen_model, {})
        if isinstance(raw, dict):
            # New format: {"historical": [...], "forecast": [...]}
            forecast_series = raw.get("forecast", [])
            if not historical_raw:
                historical_raw = raw.get("historical", [])
        elif isinstance(raw, list):
            # Legacy flat format: [{date, value, lower, upper}]
            forecast_series = raw

    return ok({
        "sku": sku,
        "model": chosen_model,
        "historical": historical_raw,
        "forecast": _enrich_forecast_points(forecast_series),
        "available_models": list(sku_forecasts.keys()) if isinstance(sku_forecasts, dict) else [],
    })


@router.get("/sessions/{session_id}/sku-intelligence/{sku:path}")
def get_sku_intelligence(
    session_id: str,
    sku: str,
    model: Optional[str] = Query(None),
    granularity: Optional[str] = Query(None),
    agg: str = Query("sum"),
    user: CurrentUser = Depends(get_current_user),
):
    _require_completed(user.tenant_id, session_id)
    result = session_store.get_training_result(user.tenant_id, session_id)
    if not result:
        raise AppError(
            "training_results_not_found", "Training results not found", status_code=404,
        )

    forecasts_data = session_store.get_forecasts(user.tenant_id, session_id) or {}

    historical_raw = _historical_for_sku(user.tenant_id, session_id, sku)

    sku_forecasts = forecasts_data.get(sku, {})
    chosen_model  = model or next(iter(sku_forecasts.keys()), None)
    available_models = list(sku_forecasts.keys()) if isinstance(sku_forecasts, dict) else []

    forecast_raw: list = []
    if chosen_model and isinstance(sku_forecasts, dict):
        raw = sku_forecasts.get(chosen_model, {})
        if isinstance(raw, dict):
            forecast_raw = raw.get("forecast", [])
            if not historical_raw:
                historical_raw = list(raw.get("historical", []))
        elif isinstance(raw, list):
            forecast_raw = raw

    from backend.utils.temporal_agg import (
        _FREQ_ORDER,
        detect_frequency,
        available_granularities as _avail_gran,
        aggregate_historical,
        aggregate_forecast,
        compute_stats,
    )

    dates     = [p["date"] for p in historical_raw if "date" in p]
    hist_freq = detect_frequency(dates)

    # The floor is the FORECAST's own frequency, not the history's. A weekly
    # session predicts weekly totals; served next to daily history they land on
    # the same axis at ~7x the scale and read as an unexplained jump — the
    # single most confidence-destroying artifact on this screen. A weekly
    # forecast cannot be split back into days without inventing data, so the
    # finer grains are simply not offered.
    fc_dates  = [p["date"] for p in forecast_raw if isinstance(p, dict) and "date" in p]
    fc_freq   = detect_frequency(fc_dates) if len(fc_dates) >= 2 else hist_freq
    base_freq = fc_freq if _FREQ_ORDER.index(fc_freq) > _FREQ_ORDER.index(hist_freq) else hist_freq

    valid_gran = _avail_gran(base_freq, len(historical_raw))
    gran      = granularity if granularity in valid_gran else base_freq

    historical_out = aggregate_historical(historical_raw, gran, agg=agg) if gran != hist_freq else historical_raw
    forecast_out   = aggregate_forecast(forecast_raw, gran, agg=agg)     if gran != fc_freq else forecast_raw

    values  = [p["value"] for p in historical_out if isinstance(p.get("value"), (int, float))]
    stats   = compute_stats(values)

    metrics_rows = result.get("metrics", {}).get("rows", [])
    sku_metrics  = [r for r in metrics_rows if r.get("sku") == sku]
    data_quality = result.get("data_quality", {})
    sku_quality  = data_quality.get(sku) if isinstance(data_quality, dict) else None

    return ok({
        "sku":                     sku,
        "model":                   chosen_model,
        "available_models":        available_models,
        "original_freq":           base_freq,
        "applied_granularity":     gran,
        "available_granularities": valid_gran,
        "historical":              historical_out,
        "forecast":                _enrich_forecast_points(forecast_out),
        "metrics":                 sku_metrics,
        "quality":                 sku_quality,
        "stats":                   stats or None,
    })


# ── Series decomposition ───────────────────────────────────────────────────────

def _session_granularity(tenant_id: str, session_id: str, session: dict) -> Optional[str]:
    """The grain this session is expressed at, or None if it trains natively.

    Family runs stamp `sessions.granularity` ('daily'/'weekly'/'monthly');
    older/aggregating sessions only carry `granularity_cfg.target_freq`.
    """
    from backend.utils.seasonality import granularity_from_target_freq

    grain = (session or {}).get("granularity")
    if grain:
        return str(grain)
    gcfg = session_store.get_field(tenant_id, session_id, "granularity_cfg") or {}
    return granularity_from_target_freq(gcfg.get("target_freq"))


@router.get("/sessions/{session_id}/decomposition/{sku:path}")
def get_series_decomposition(
    session_id: str,
    sku: str,
    granularity: Optional[str] = Query(
        None, description="Grain to decompose at; floored at the data's native grain",
    ),
    user: CurrentUser = Depends(get_current_user),
):
    """STL trend / seasonal / residual split of one SKU's history.

    This is the chart that answers "is this product really growing, or is it
    just December again?" — the trend panel is the growth, the seasonal panel
    is December. The engine owns the maths (`forecasting_core.analysis.
    decomposition`); this endpoint only picks the grain and the period, and
    refuses when the history cannot support either. A plausible-looking wrong
    decomposition is worse than an empty state, so every failure is an error
    code, never a degraded split.
    """
    s = _require_completed(user.tenant_id, session_id)

    from backend.utils.seasonality import (
        SEASONAL_CYCLE, MIN_CYCLES, min_points, seasonal_period,
    )
    from backend.utils.temporal_agg import (
        _FREQ_ORDER, aggregate_historical, bucket_count, detect_frequency,
    )

    # Same history as the forecast chart — never a second derivation of it.
    historical = _historical_for_sku(user.tenant_id, session_id, sku)
    if not historical:
        # Last resort, mirroring get_forecast_series: the forecasts blob keeps
        # its own copy of the history the model was fitted on.
        forecasts_data = session_store.get_forecasts(user.tenant_id, session_id) or {}
        sku_forecasts = forecasts_data.get(sku) or {}
        if isinstance(sku_forecasts, dict):
            for raw in sku_forecasts.values():
                if isinstance(raw, dict) and raw.get("historical"):
                    historical = list(raw["historical"])
                    break

    historical = [
        p for p in historical
        if isinstance(p, dict) and p.get("date") and isinstance(p.get("value"), (int, float))
    ]
    if not historical:
        raise AppError(
            "decomposition_no_history",
            f"No historical series for SKU '{sku}' in this session",
            status_code=404,
            params={"sku": sku},
        )

    dates = [p["date"] for p in historical]
    base_freq = detect_frequency(dates)

    # Grain: the caller's chip, else the session's own grain, else what the
    # dates say. Never finer than the data — resampling upward would invent
    # observations, and the seasonal panel would be drawing noise.
    requested = granularity or _session_granularity(user.tenant_id, session_id, s) or base_freq
    if requested in _FREQ_ORDER and _FREQ_ORDER.index(requested) >= _FREQ_ORDER.index(base_freq):
        grain = requested
    else:
        grain = base_freq

    period = seasonal_period(grain)
    if period is None:
        raise AppError(
            "decomposition_unsupported_granularity",
            f"A '{grain}' series has no seasonal cycle left to separate",
            status_code=422,
            params={"sku": sku, "granularity": grain},
        )

    # Resample onto a regular grid: STL assumes evenly spaced observations, and
    # a raw upload can skip days. Gap buckets sum to 0, which for demand is the
    # truthful reading (nothing sold), not an imputation.
    points = aggregate_historical(historical, grain, agg="sum")
    points = [p for p in points if p.get("value") is not None]

    required = min_points(grain)
    if len(points) < required:
        raise AppError(
            "decomposition_history_too_short",
            f"Decomposition needs at least {required} {grain} points "
            f"({MIN_CYCLES} full cycles of {period}); this SKU has {len(points)}.",
            status_code=422,
            params={
                "sku": sku,
                "granularity": grain,
                "period": period,
                "cycles": MIN_CYCLES,
                "required": required,
                "available": len(points),
            },
        )

    values = [float(p["value"]) for p in points]
    try:
        from forecasting_core.analysis.decomposition import decompose
    except ImportError:
        raise HTTPException(status_code=503, detail="ML library not available")
    try:
        parts = decompose(values, period)
    except Exception as exc:
        log.exception("Decomposition failed session=%s sku=%s", session_id, sku)
        raise AppError(
            "decomposition_failed",
            f"Could not decompose the series for SKU '{sku}'",
            status_code=422,
            params={"sku": sku, "reason": str(exc)},
        )

    # One row per bucket: aligned by construction, so a three-panel chart can
    # bind trend/seasonal/residual straight off the same array.
    series = [
        {
            "date":     p["date"],
            "observed": round(v, 4),
            "trend":    t,
            "seasonal": se,
            "residual": r,
        }
        for p, v, t, se, r in zip(
            points, values, parts["trend"], parts["seasonal"], parts["residual"],
        )
    ]

    # Only offer grains a decomposition would actually succeed at, so the chip
    # can never lead the user into an error state.
    available = [
        g for g in _FREQ_ORDER[_FREQ_ORDER.index(base_freq):]
        if min_points(g) is not None
        and (bucket_count(dates, g) >= min_points(g) or g == grain)
    ]

    return ok({
        "sku":                     sku,
        "granularity":             grain,
        "original_granularity":    base_freq,
        "available_granularities": available,
        "period":                  period,
        "seasonal_cycle":          SEASONAL_CYCLE[grain],
        "cycles_covered":          round(len(series) / period, 2),
        "n_points":                len(series),
        "series":                  series,
        "trend_strength":          parts["trend_strength"],
        "seasonal_strength":       parts["seasonal_strength"],
    })


@router.get("/sessions/{session_id}/shap/{sku:path}")
def get_shap(
    session_id: str,
    sku: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Return SHAP feature importances for the best model for the given SKU."""
    _require_completed(user.tenant_id, session_id)
    result = session_store.get_training_result(user.tenant_id, session_id)
    if not result:
        raise AppError(
            "training_results_not_found", "Training results not found", status_code=404,
        )

    shap_data = result.get("metrics", {}).get("shap", {})
    sku_shap = shap_data.get(sku)
    if not sku_shap:
        raise AppError(
            "shap_not_available", f"No SHAP data for SKU '{sku}'",
            status_code=404, params={"sku": sku},
        )

    # Find best model by lowest MAE
    metrics_rows = result.get("metrics", {}).get("rows", [])
    sku_rows = [r for r in metrics_rows if str(r.get("sku")) == str(sku) and r.get("mae") is not None]
    best_model = None
    if sku_rows:
        best_row = min(sku_rows, key=lambda r: r["mae"])
        best_model = best_row.get("model")

    if best_model and best_model in sku_shap:
        importance = sku_shap[best_model]
    else:
        # Fall back to any available model
        importance = next(iter(sku_shap.values()), [])
        best_model = next(iter(sku_shap.keys()), None)

    return ok({
        "sku":   sku,
        "model": best_model,
        "shap_importance": importance,
    })


@router.get("/sessions/{session_id}/routing-plan")
def get_routing_plan_alias(session_id: str, user: CurrentUser = Depends(get_current_user)):
    return get_routing(session_id, user)


@router.get("/sessions/{session_id}/report")
def get_report_alias(session_id: str, user: CurrentUser = Depends(get_current_user)):
    # Pass user by keyword — get_training_results has a `level` param between
    # session_id and user, so a positional call bound user to level and left the
    # real user param as the unresolved Depends default (AttributeError).
    return get_training_results(session_id, user=user)


@router.get("/sessions/{session_id}/export-config")
def get_export_config_alias(session_id: str, user: CurrentUser = Depends(get_current_user)):
    session_svc.get_session(user.tenant_id, session_id)
    cfg = session_store.get_all_configs(user.tenant_id, session_id)
    return ok({
        "dataset": cfg.get("dataset_ref"),
        "columns": cfg.get("columns_cfg"),
        "features": cfg.get("features_cfg"),
        "models": cfg.get("models_cfg"),
        "validation": cfg.get("validation_cfg"),
        "business": cfg.get("business_cfg"),
        "forecast": cfg.get("forecast_cfg"),
    })


@router.get("/config/schema")
def get_global_config_schema(user: CurrentUser = Depends(get_current_user)):
    try:
        from forecasting_core.config.config import SessionConfig
        return ok(SessionConfig.schema())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models/available")
def get_global_available_models(user: CurrentUser = Depends(get_current_user)):
    try:
        from forecasting_core.models.factory import ModelFactory
        return ok({"models": ModelFactory.available_models()})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/drift")
async def detect_drift(
    session_id: str,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    _require_completed(user.tenant_id, session_id)

    col_info = session_store.get_field(user.tenant_id, session_id, "columns_cfg") or {}
    s = session_svc.get_session(user.tenant_id, session_id)
    dataset_id = s.get("dataset_id") if s else None
    ref_path = None
    if dataset_id:
        ds_meta = get_dataset(user.tenant_id, dataset_id)
        ref_path = ds_meta.get("file_path") if ds_meta else None

    if not ref_path or not col_info:
        raise AppError(
            "drift_no_reference_dataset",
            "Session has no reference dataset configured",
            status_code=409,
        )

    target_col = col_info.get("target_column") or col_info.get("target", "")
    group_col = col_info.get("sku_column") or col_info.get("sku") or col_info.get("group", "")

    from backend.dataframes.io import read_dataframe
    try:
        ref_df = read_dataframe(ref_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not load reference dataset: {e}")

    try:
        content = await file.read()
        cur_fmt = "excel" if (file.filename and file.filename.endswith((".xlsx", ".xls"))) else "csv"
        cur_df = read_dataframe(content, fmt=cur_fmt)
    except Exception as e:
        raise AppError(
            "upload_parse_failed", f"Could not parse uploaded file: {e}",
            status_code=422, params={"reason": str(e)},
        )

    try:
        from forecasting_core.monitoring.drift import DriftDetector
        detector = DriftDetector(ref_df, target_col, group_col)
        feature_drift = detector.detect_feature_drift(cur_df)
        alerts = detector.alerts(feature_drift)
        has_drift = any(v.get("drift") for v in feature_drift.values())
    except Exception as e:
        log.exception("Drift detection failed")
        raise HTTPException(status_code=500, detail=f"Drift detection error: {e}")

    return ok({
        "session_id": session_id,
        "target_col": target_col,
        "feature_drift": feature_drift,
        "alerts": alerts,
        "has_drift": has_drift,
        "ref_rows": len(ref_df),
        "cur_rows": len(cur_df),
    })
