"""
Session configuration routes — each endpoint corresponds to one wizard step.

Step flow:
  POST /sessions/{id}/dataset            → attach dataset
  GET  /sessions/{id}/inspect            → inspect + profile dataset
  GET  /sessions/{id}/health             → pre-training data health score + LLM diagnosis
  POST /sessions/{id}/configure/columns  → choose date/target/sku columns
  GET  /sessions/{id}/configure/columns
  POST /sessions/{id}/configure/features → configure feature engineering
  GET  /sessions/{id}/configure/features
  POST /sessions/{id}/configure/models   → select models + hyperparameters
  GET  /sessions/{id}/configure/models
  POST /sessions/{id}/configure/validation → training strategy
  GET  /sessions/{id}/configure/validation
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.datasets import service as ds_svc
from backend.datasets.service import get_dataset, update_stats
from backend.db import session_store
from backend.schemas.common import ok
from backend.schemas.configuration import (
    AttachDatasetRequest, BusinessConfigRequest, CanonicalColumnsRequest,
    ColumnsConfigRequest, FeaturesConfigRequest, ForecastConfigRequest,
    ModelsConfigRequest, ValidationConfigRequest,
)
from backend.sessions import service as session_svc

log = logging.getLogger(__name__)

router = APIRouter(tags=["configuration"])


def _get_session_or_404(tenant_id: str, session_id: str) -> dict:
    s = session_svc.get_session(tenant_id, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return s


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_granularity_detection(
    tenant_id: str, dataset_id: str, date_col: Optional[str], group_col: Optional[str],
) -> Optional[dict]:
    """
    Reload the dataset file and re-classify per-SKU granularity using the
    user's CONFIRMED columns. Used by configure_columns as a safety net over
    the profiler's auto-detection at /inspect time — if the user corrects the
    date/SKU mapping, a conflict that wasn't visible before (or one that was
    a false positive) must be caught before training. Non-fatal: returns None
    (and logs) if the file can't be reloaded, since re-validation is a safety
    net, not a hard requirement to save the column configuration.
    """
    try:
        import os
        ds_meta = get_dataset(tenant_id, dataset_id)
        if not ds_meta or not os.path.exists(ds_meta["file_path"]):
            return None
        from forecasting_core.engine import ForecastEngine
        from forecasting_core.data.profiler import DataProfiler
        engine = ForecastEngine()
        engine.load_data(ds_meta["file_path"])
        return DataProfiler().detect_granularity(engine._df, date_col, group_col)
    except Exception as e:
        log.warning("granularity re-validation failed dataset=%s: %s", dataset_id, e)
        return None


# ── Dataset attachment ─────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/dataset")
def attach_dataset(
    session_id: str,
    body: AttachDatasetRequest,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    _get_session_or_404(user.tenant_id, session_id)
    ds = get_dataset(user.tenant_id, body.dataset_id)
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")

    session = session_svc.attach_dataset(user.tenant_id, session_id, body.dataset_id)
    if session["status"] == "DRAFT":
        try:
            session = session_svc.transition(user.tenant_id, session_id, "DATASET_LOADED", "inspect")
        except ValueError:
            pass
    return ok(session)


# ── Inspection ─────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/inspect")
def inspect_dataset(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    s = _get_session_or_404(user.tenant_id, session_id)
    if not s.get("dataset_id"):
        raise HTTPException(status_code=400, detail="No dataset attached. POST /sessions/{id}/dataset first.")

    cached = session_store.get_field(user.tenant_id, session_id, "inspection")
    if cached:
        return ok(cached)

    ds_meta = get_dataset(user.tenant_id, s["dataset_id"])
    if not ds_meta:
        raise HTTPException(status_code=404, detail="Dataset file not found")

    import os
    if not os.path.exists(ds_meta["file_path"]):
        raise HTTPException(
            status_code=404,
            detail=f"Dataset file not found on disk. Please re-upload the file for session '{s['name']}'.",
        )

    try:
        from forecasting_core.engine import ForecastEngine
        from forecasting_core.data.profiler import DataProfiler
        engine = ForecastEngine()
        engine.load_data(ds_meta["file_path"])
        profile = engine.get_profile()
        col_options = engine.get_column_options()
        config_schema = engine.get_config_schema()

        profiler = DataProfiler()
        canonical_suggestions = profiler.get_canonical_mapping(engine._df)

        recommended = (profile or {}).get("recommended", {})
        granularity = profiler.detect_granularity(
            engine._df, recommended.get("date"), recommended.get("group"),
        )

        inspection = {
            "profile": profile,
            "column_options": col_options,
            "canonical_suggestions": canonical_suggestions,
            "config_schema": config_schema,
            "granularity": granularity,
            "inspected_at": _now(),
        }
        session_store.set_field(user.tenant_id, session_id, "inspection", inspection)

        if profile:
            update_stats(
                user.tenant_id, s["dataset_id"],
                row_count=profile.get("n_rows", 0),
                column_count=len(profile.get("columns", {})),
            )

        if s["status"] == "DATASET_LOADED":
            session_svc.transition(user.tenant_id, session_id, "INSPECTED", "columns")

        return ok(inspection)

    except ImportError:
        raise HTTPException(status_code=503, detail="ML library not available")
    except pd.errors.EmptyDataError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"The file for session '{s['name']}' is empty or has no columns. "
                "Upload a CSV file with at least a header row and data."
            ),
        )
    except pd.errors.ParserError as e:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Could not read the file for session '{s['name']}': invalid CSV format ({e}). "
                "Make sure the file is not corrupted and try again."
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Inspection failed: {e}")


# ── Columns ────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/configure/columns")
def configure_columns(
    session_id: str,
    body: dict,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    from pydantic import ValidationError

    # Determine request schema from body shape. Validate old-style schema eagerly
    # (before the session lookup) so that missing required fields return 422 rather
    # than 404 from the resource check below.
    is_canonical = "canonical_mapping" in body
    if not is_canonical:
        try:
            _req_pre = ColumnsConfigRequest(**body)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors())

    s = _get_session_or_404(user.tenant_id, session_id)
    inspection = session_store.get_field(user.tenant_id, session_id, "inspection") or {}
    real_columns = [c["name"] for c in inspection.get("profile", {}).get("columns", [])]

    if is_canonical:
        try:
            req = CanonicalColumnsRequest(**body)
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))
        try:
            req.validate_required(real_columns)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        config = {
            **req.model_dump(),
            "schema_version": "canonical_v1",
            "configured_at":  _now(),
            "configured_by":  user.user_id,
        }
    else:
        req_old = _req_pre  # reuse validated instance from early check

        def _require_column(value: Optional[str], field_label: str):
            if not value or not value.strip():
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"The '{field_label}' column is required and was not provided. "
                        f"Select one of the available columns: {', '.join(real_columns)}."
                    ),
                )
            if real_columns and value not in real_columns:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Column '{value}' does not exist in the uploaded file. "
                        f"Available columns: {', '.join(real_columns)}."
                    ),
                )

        _require_column(req_old.date_column, "Date")
        _require_column(req_old.target_column, "Target")
        if req_old.sku_column is not None and req_old.sku_column.strip():
            if real_columns and req_old.sku_column not in real_columns:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Column '{req_old.sku_column}' (SKU) does not exist in the uploaded file. "
                        f"Available columns: {', '.join(real_columns)}."
                    ),
                )
        config = {**req_old.model_dump(), "configured_at": _now(), "configured_by": user.user_id}

    session_store.set_field(user.tenant_id, session_id, "columns_cfg", config)
    if s["status"] in ("INSPECTED", "COLUMNS_CONFIGURED", "FEATURES_CONFIGURED",
                        "MODELS_CONFIGURED", "COMPLETED", "FAILED"):
        try:
            session_svc.transition(user.tenant_id, session_id, "COLUMNS_CONFIGURED", "features")
        except ValueError:
            pass

    confirmed_date  = config.get("date_column") or (config.get("canonical_mapping") or {}).get("date")
    confirmed_group = config.get("sku_column") or (config.get("canonical_mapping") or {}).get("sku")
    granularity = _run_granularity_detection(user.tenant_id, s["dataset_id"], confirmed_date, confirmed_group)

    response = dict(config)
    if granularity is not None:
        response["granularity"] = granularity

    return ok(response)


@router.get("/sessions/{session_id}/configure/columns")
def get_columns_config(session_id: str, user: CurrentUser = Depends(get_current_user)):
    _get_session_or_404(user.tenant_id, session_id)
    return ok(session_store.get_field(user.tenant_id, session_id, "columns_cfg"))


# ── Features ───────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/configure/features")
def configure_features(
    session_id: str,
    body: FeaturesConfigRequest,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    s = _get_session_or_404(user.tenant_id, session_id)
    config = {**body.model_dump(), "configured_at": _now()}
    session_store.set_field(user.tenant_id, session_id, "features_cfg", config)

    if s["status"] in ("COLUMNS_CONFIGURED", "FEATURES_CONFIGURED",
                        "MODELS_CONFIGURED", "COMPLETED", "FAILED"):
        try:
            session_svc.transition(user.tenant_id, session_id, "FEATURES_CONFIGURED", "models")
        except ValueError:
            pass
    return ok(config)


@router.get("/sessions/{session_id}/configure/features")
def get_features_config(session_id: str, user: CurrentUser = Depends(get_current_user)):
    _get_session_or_404(user.tenant_id, session_id)
    return ok(session_store.get_field(user.tenant_id, session_id, "features_cfg"))


# ── Models ─────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/configure/models")
def configure_models(
    session_id: str,
    body: ModelsConfigRequest,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    s = _get_session_or_404(user.tenant_id, session_id)
    config = {**body.model_dump(), "configured_at": _now()}
    session_store.set_field(user.tenant_id, session_id, "models_cfg", config)

    if s["status"] in ("FEATURES_CONFIGURED", "MODELS_CONFIGURED",
                        "COMPLETED", "FAILED", "CANCELLED"):
        try:
            session_svc.transition(user.tenant_id, session_id, "MODELS_CONFIGURED", "validation")
        except ValueError:
            pass
    return ok(config)


@router.get("/sessions/{session_id}/configure/models")
def get_models_config(session_id: str, user: CurrentUser = Depends(get_current_user)):
    _get_session_or_404(user.tenant_id, session_id)
    return ok(session_store.get_field(user.tenant_id, session_id, "models_cfg"))


# ── Validation ─────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/configure/validation")
def configure_validation(
    session_id: str,
    body: ValidationConfigRequest,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    _get_session_or_404(user.tenant_id, session_id)
    config = {**body.model_dump(), "configured_at": _now()}
    session_store.set_field(user.tenant_id, session_id, "validation_cfg", config)
    return ok(config)


@router.get("/sessions/{session_id}/configure/validation")
def get_validation_config(session_id: str, user: CurrentUser = Depends(get_current_user)):
    _get_session_or_404(user.tenant_id, session_id)
    return ok(session_store.get_field(user.tenant_id, session_id, "validation_cfg"))


# ── Full session config summary ────────────────────────────────────────────

@router.get("/sessions/{session_id}/config-summary")
def get_config_summary(session_id: str, user: CurrentUser = Depends(get_current_user)):
    _get_session_or_404(user.tenant_id, session_id)
    cfg = session_store.get_all_configs(user.tenant_id, session_id)
    return ok({
        "dataset": cfg.get("dataset_ref"),
        "columns": cfg.get("columns_cfg"),
        "features": cfg.get("features_cfg"),
        "models": cfg.get("models_cfg"),
        "validation": cfg.get("validation_cfg"),
    })


# ── Combined upload + attach (Frontend alias) ─────────────────────────────

@router.post("/sessions/{session_id}/upload", status_code=201)
async def upload_and_attach(
    session_id: str,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    s = _get_session_or_404(user.tenant_id, session_id)

    try:
        meta = await ds_svc.upload_dataset(user.tenant_id, user.user_id, file)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    session_svc.attach_dataset(user.tenant_id, session_id, meta["id"])
    # Invalidate cached inspection
    session_store.clear_field(user.tenant_id, session_id, "inspection")

    if s["status"] == "DRAFT":
        try:
            session_svc.transition(user.tenant_id, session_id, "DATASET_LOADED", "inspect")
        except ValueError:
            pass

    return ok(meta)


# ── Frontend alias routes ─────────────────────────────────────────────────

@router.get("/sessions/{session_id}/profile")
def get_profile_alias(session_id: str, user: CurrentUser = Depends(get_current_user)):
    return inspect_dataset(session_id, user)


@router.get("/sessions/{session_id}/columns")
def get_columns_alias(session_id: str, user: CurrentUser = Depends(get_current_user)):
    return get_columns_config(session_id, user)


@router.post("/sessions/{session_id}/columns")
def post_columns_alias(
    session_id: str,
    body: dict,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    return configure_columns(session_id, body, user)


@router.post("/sessions/{session_id}/config/features")
def config_features_alias(
    session_id: str,
    body: FeaturesConfigRequest,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    return configure_features(session_id, body, user)


@router.post("/sessions/{session_id}/config/training")
def config_training_alias(
    session_id: str,
    body: ValidationConfigRequest,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    return configure_validation(session_id, body, user)


@router.post("/sessions/{session_id}/config/models")
def config_models_alias(
    session_id: str,
    body: ModelsConfigRequest,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    return configure_models(session_id, body, user)


# ── New config endpoints ───────────────────────────────────────────────────

@router.post("/sessions/{session_id}/config/forecast")
def configure_forecast(
    session_id: str,
    body: ForecastConfigRequest,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    _get_session_or_404(user.tenant_id, session_id)
    config = {**body.model_dump(), "configured_at": _now()}
    session_store.set_field(user.tenant_id, session_id, "forecast_cfg", config)
    return ok(config)


@router.get("/sessions/{session_id}/config/forecast")
def get_forecast_config(session_id: str, user: CurrentUser = Depends(get_current_user)):
    _get_session_or_404(user.tenant_id, session_id)
    return ok(session_store.get_field(user.tenant_id, session_id, "forecast_cfg"))


@router.post("/sessions/{session_id}/config/business")
def configure_business(
    session_id: str,
    body: BusinessConfigRequest,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    _get_session_or_404(user.tenant_id, session_id)
    config = {**body.model_dump(), "configured_at": _now()}
    session_store.set_field(user.tenant_id, session_id, "business_cfg", config)
    return ok(config)


@router.get("/sessions/{session_id}/config/business")
def get_business_config(session_id: str, user: CurrentUser = Depends(get_current_user)):
    _get_session_or_404(user.tenant_id, session_id)
    return ok(session_store.get_field(user.tenant_id, session_id, "business_cfg"))


# ── Data Health Check ──────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/health")
def get_data_health(
    session_id: str,
    refresh: bool = Query(False, description="Force re-analysis even if cached"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Pre-training data health check.

    Runs DataQualityChecker on the uploaded dataset using auto-detected or
    user-configured columns. Returns a global health score (0–100), a per-SKU
    breakdown, and an optional LLM narrative diagnosis.

    Result is cached inside the 'inspection' blob. Re-uploading the dataset
    or passing ?refresh=true forces a fresh analysis.
    """
    s = _get_session_or_404(user.tenant_id, session_id)

    if not s.get("dataset_id"):
        raise HTTPException(400, "No dataset attached. POST /sessions/{id}/dataset first.")

    inspection = session_store.get_field(user.tenant_id, session_id, "inspection")
    if not inspection:
        raise HTTPException(
            400,
            "Dataset not inspected yet. Call GET /sessions/{id}/inspect first.",
        )

    if not refresh and inspection.get("health"):
        return ok(inspection["health"])

    # Resolve columns: prefer user-configured, fall back to profiler recommendations
    col_cfg    = session_store.get_field(user.tenant_id, session_id, "columns_cfg") or {}
    recommended = inspection.get("profile", {}).get("recommended", {})

    dt_col     = col_cfg.get("date_column")   or recommended.get("date")
    target_col = col_cfg.get("target_column") or recommended.get("target")
    group_col  = col_cfg.get("sku_column")    or recommended.get("group")

    if not dt_col or not target_col:
        raise HTTPException(
            400,
            "Cannot determine date/target columns. "
            "Call GET /sessions/{id}/inspect first, or configure columns via "
            "POST /sessions/{id}/configure/columns.",
        )

    ds_meta = get_dataset(user.tenant_id, s["dataset_id"])
    if not ds_meta:
        raise HTTPException(404, "Dataset file not found on disk")

    try:
        import pandas as pd
        fp = ds_meta["file_path"]
        df = pd.read_csv(fp) if str(fp).endswith(".csv") else pd.read_excel(fp)
    except Exception as exc:
        raise HTTPException(500, f"Could not load dataset: {exc}")

    try:
        from forecasting_core.data.quality import DataQualityChecker
        checker = DataQualityChecker(
            dt_col=dt_col,
            target_col=target_col,
            group_col=group_col,
            min_history=20,
            seasonal_period=7,
        )
        reports = checker.check(df)
    except ImportError:
        raise HTTPException(503, "forecasting_core not available")
    except Exception as exc:
        raise HTTPException(500, f"Quality check failed: {exc}")

    sku_reports = {sku: r.to_dict() for sku, r in reports.items()}

    # Weighted global score (by series length)
    total_rows = sum(r.n_rows for r in reports.values())
    if total_rows > 0:
        global_score = sum(
            r.quality_score * r.n_rows for r in reports.values()
        ) / total_rows
    else:
        global_score = 0.0
    global_score = round(global_score, 1)

    if global_score >= 75:
        status = "HEALTHY"
        can_train = True
    elif global_score >= 40:
        status = "NEEDS_ATTENTION"
        can_train = True
    else:
        status = "POOR"
        can_train = False

    # Diagnosis runs on the local LLM; it returns None gracefully if unavailable.
    diagnosis = _llm_health_narrative(
        global_score, status, sku_reports,
        inspection.get("profile", {}),
    )

    report = {
        "global_score":  global_score,
        "status":        status,
        "can_train":     can_train,
        "n_skus":        len(sku_reports),
        "sku_reports":   sku_reports,
        "diagnosis":     diagnosis,
        "columns_used":  {"date": dt_col, "target": target_col, "group": group_col},
        "analyzed_at":   _now(),
    }

    # Cache inside existing inspection blob (no DB migration needed)
    updated = {**inspection, "health": report}
    session_store.set_field(user.tenant_id, session_id, "inspection", updated)

    return ok(report)


# ── Dataset Analysis ──────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/analysis")
def get_dataset_analysis(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Rich dataset analysis for the pre-column wizard step."""
    try:
        return _get_dataset_analysis_impl(session_id, user)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception(f"Unhandled error in analysis for session {session_id}")
        raise HTTPException(500, f"Analysis failed: {type(exc).__name__}: {exc}") from exc


def _get_dataset_analysis_impl(session_id: str, user):
    s = _get_session_or_404(user.tenant_id, session_id)

    inspection = session_store.get_field(user.tenant_id, session_id, "inspection")
    if not inspection:
        raise HTTPException(400, "Dataset not inspected yet. Call GET /sessions/{id}/inspect first.")

    cached = inspection.get("analysis")
    if cached:
        return ok(cached)

    if not s.get("dataset_id"):
        raise HTTPException(400, "No dataset attached.")

    ds_meta = get_dataset(user.tenant_id, s["dataset_id"])
    if not ds_meta:
        raise HTTPException(404, "Dataset not found")

    import os
    fp = ds_meta["file_path"]
    if not os.path.exists(fp):
        raise HTTPException(404, "Dataset file not found on disk")

    try:
        import numpy as np
        import pandas as pd
        df = pd.read_csv(fp) if str(fp).endswith(".csv") else pd.read_excel(fp)
    except Exception as exc:
        raise HTTPException(500, f"Could not load dataset: {exc}")

    # Column-level analysis
    col_analysis = []
    for col in df.columns:
        s_col = df[col]
        role = "numeric" if pd.api.types.is_numeric_dtype(s_col) else "categorical"
        col_analysis.append({
            "name":     col,
            "dtype":    str(s_col.dtype),
            "role":     role,
            "null_pct": round(float(s_col.isna().mean() * 100), 1),
            "n_unique": int(s_col.nunique()),
        })

    n_duplicates = int(df.duplicated().sum())
    memory_mb    = round(float(df.memory_usage(deep=True).sum() / 1024 / 1024), 2)
    
    col_cfg    = session_store.get_field(user.tenant_id, session_id, "columns_cfg") or {}
    recommended = inspection.get("profile", {}).get("recommended", {})

    dt_col     = col_cfg.get("date_column")   or recommended.get("date")
    target_col = col_cfg.get("target_column") or recommended.get("target")
    group_col  = col_cfg.get("sku_column")    or recommended.get("group")

    # Temporal analysis
    profile      = inspection.get("profile", {})
    recommended  = profile.get("recommended", {})
    

    temporal: dict = {}
    if dt_col and dt_col in df.columns:
        try:
            dates = pd.to_datetime(df[dt_col], errors="coerce").dropna().sort_values()
            diffs = dates.diff().dropna()
            if len(diffs) > 0:
                med_td     = diffs.median()
                freq_days  = int(round(med_td.total_seconds() / 86400))
                gap_thresh = med_td * 2.5
                gap_count  = int((diffs > gap_thresh).sum())
            else:
                freq_days = gap_count = 0
            temporal = {
                "date_min":   str(dates.min().date()),
                "date_max":   str(dates.max().date()),
                "n_periods":  int(dates.nunique()),
                "freq_days":  freq_days,
                "gap_count":  gap_count,
                "freq_label": recommended.get("freq", "unknown"),
            }
        except Exception:
            pass

    # Aggregate seasonality on target
    seasonality_info: Optional[dict] = None
    if target_col and target_col in df.columns and dt_col and dt_col in df.columns:
        try:
            import numpy as np
            ts   = df.groupby(dt_col)[target_col].sum().sort_index()
            vals = ts.values.astype(float)
            vals = vals[~np.isnan(vals)]
            if len(vals) >= 12:
                from forecasting_core.analysis.seasonality import detect_seasonality
                res = detect_seasonality(vals)
                dp = res.get("dominant_period")
                seasonality_info = {
                    "dominant_period":   int(dp) if dp is not None else None,
                    "top_periods":       [int(p) for p in res.get("top_periods", [])],
                    "seasonal_strength": round(float(res.get("seasonal_strength", 0)), 3),
                    "classification":    str(res.get("classification", "none")),
                }
        except Exception:
            pass

    # SKU-level stats
    sku_stats: dict = {}
    if group_col and group_col in df.columns and target_col and target_col in df.columns:
        try:
            import numpy as np
            grp            = df.groupby(group_col)
            sizes          = grp.size()
            zero_pcts      = grp[target_col].apply(lambda s: float((s == 0).mean()) * 100)
            sku_stats = {
                "n_skus":             int(df[group_col].nunique()),
                "intermittent_count": int((zero_pcts > 30).sum()),
                "short_series_count": int((sizes < 20).sum()),
                "avg_zero_pct":       round(float(zero_pcts.mean()), 1),
                "min_series_len":     int(sizes.min()),
                "max_series_len":     int(sizes.max()),
            }
        except Exception:
            pass
    
    result = {
        "columns":     col_analysis,
        "n_rows":      int(len(df)),
        "n_cols":      int(len(df.columns)),
        "n_duplicates": n_duplicates,
        "memory_mb":   memory_mb,
        "temporal":    temporal,
        "seasonality": seasonality_info,
        "sku_stats":   sku_stats,
        "analyzed_at": _now(),
    }

    try:
        session_store.set_field(user.tenant_id, session_id, "inspection", {**inspection, "analysis": result})
    except Exception as exc:
        log.warning(f"Could not cache analysis for session {session_id}: {exc}")
    return ok(result)


# ── Model Hyperparameter Schemas ───────────────────────────────────────────────

_MODEL_HYPERPARAMS: dict = {
    "lightgbm": [
        {"name": "n_estimators",     "type": "int",   "default": 300,  "min": 50,    "max": 2000, "desc": "Number of boosting iterations"},
        {"name": "learning_rate",    "type": "float", "default": 0.05, "min": 0.001, "max": 0.5,  "desc": "Step size shrinkage — lower = more robust, slower"},
        {"name": "num_leaves",       "type": "int",   "default": 31,   "min": 10,    "max": 300,  "desc": "Max leaves per tree — controls complexity"},
        {"name": "max_depth",        "type": "int",   "default": -1,   "min": -1,    "max": 20,   "desc": "Max tree depth (-1 = unlimited)"},
        {"name": "min_child_samples","type": "int",   "default": 20,   "min": 1,     "max": 200,  "desc": "Min observations per leaf — reduces overfit"},
        {"name": "feature_fraction", "type": "float", "default": 0.8,  "min": 0.1,   "max": 1.0,  "desc": "Fraction of features used per tree"},
        {"name": "bagging_fraction", "type": "float", "default": 0.8,  "min": 0.1,   "max": 1.0,  "desc": "Row sampling fraction per iteration"},
        {"name": "lambda_l1",        "type": "float", "default": 0.0,  "min": 0.0,   "max": 10.0, "desc": "L1 regularization (sparsity)"},
        {"name": "lambda_l2",        "type": "float", "default": 0.0,  "min": 0.0,   "max": 10.0, "desc": "L2 regularization (weight decay)"},
    ],
    "xgboost": [
        {"name": "n_estimators",      "type": "int",   "default": 300,  "min": 50,    "max": 2000, "desc": "Number of trees"},
        {"name": "learning_rate",     "type": "float", "default": 0.05, "min": 0.001, "max": 0.5,  "desc": "Step size shrinkage (eta)"},
        {"name": "max_depth",         "type": "int",   "default": 6,    "min": 1,     "max": 15,   "desc": "Maximum tree depth"},
        {"name": "subsample",         "type": "float", "default": 0.8,  "min": 0.1,   "max": 1.0,  "desc": "Row sampling fraction"},
        {"name": "colsample_bytree",  "type": "float", "default": 0.8,  "min": 0.1,   "max": 1.0,  "desc": "Column sampling per tree"},
        {"name": "min_child_weight",  "type": "int",   "default": 1,    "min": 1,     "max": 50,   "desc": "Min sum of weights in a leaf"},
        {"name": "gamma",             "type": "float", "default": 0.0,  "min": 0.0,   "max": 5.0,  "desc": "Min loss reduction for node split"},
        {"name": "reg_alpha",         "type": "float", "default": 0.0,  "min": 0.0,   "max": 10.0, "desc": "L1 regularization"},
        {"name": "reg_lambda",        "type": "float", "default": 1.0,  "min": 0.0,   "max": 10.0, "desc": "L2 regularization"},
    ],
    "prophet": [
        {"name": "changepoint_prior_scale",  "type": "float",  "default": 0.05,        "min": 0.001, "max": 0.5,  "desc": "Flexibility of trend change points"},
        {"name": "seasonality_prior_scale",  "type": "float",  "default": 10.0,        "min": 0.1,   "max": 50.0, "desc": "Strength of seasonal components"},
        {"name": "holidays_prior_scale",     "type": "float",  "default": 10.0,        "min": 0.1,   "max": 50.0, "desc": "Strength of holiday effects"},
        {"name": "seasonality_mode",         "type": "select", "default": "additive",  "options": ["additive", "multiplicative"], "desc": "Additive vs multiplicative seasonality"},
        {"name": "n_changepoints",           "type": "int",    "default": 25,          "min": 5,     "max": 100,  "desc": "Number of potential trend changepoints"},
    ],
    "arima": [
        {"name": "max_p",                    "type": "int",    "default": 5,    "min": 0, "max": 10,  "desc": "Max autoregressive order"},
        {"name": "max_q",                    "type": "int",    "default": 5,    "min": 0, "max": 10,  "desc": "Max moving average order"},
        {"name": "max_d",                    "type": "int",    "default": 2,    "min": 0, "max": 3,   "desc": "Max differencing order"},
        {"name": "seasonal",                 "type": "bool",   "default": True,                      "desc": "Include seasonal ARIMA terms"},
        {"name": "information_criterion",    "type": "select", "default": "aic", "options": ["aic", "bic", "hqic"], "desc": "Model selection criterion"},
    ],
    "ets": [
        {"name": "error",            "type": "select", "default": "add", "options": ["add", "mul"],          "desc": "Error component type"},
        {"name": "trend",            "type": "select", "default": "add", "options": ["add", "mul", "none"],  "desc": "Trend component type"},
        {"name": "seasonal",         "type": "select", "default": "add", "options": ["add", "mul", "none"],  "desc": "Seasonal component type"},
        {"name": "seasonal_periods", "type": "int",    "default": 7,    "min": 2, "max": 365,               "desc": "Number of periods per season"},
    ],
    "croston": [
        {"name": "alpha",   "type": "float",  "default": 0.1,          "min": 0.01, "max": 0.5,                   "desc": "Smoothing parameter"},
        {"name": "variant", "type": "select", "default": "optimized",  "options": ["classic", "optimized", "sba"], "desc": "SBA variant reduces forecast bias"},
    ],
    "lstm": [
        {"name": "hidden_size",  "type": "int",   "default": 64,    "min": 16,     "max": 512,  "desc": "LSTM hidden units per layer"},
        {"name": "num_layers",   "type": "int",   "default": 2,     "min": 1,      "max": 5,    "desc": "Number of stacked LSTM layers"},
        {"name": "dropout",      "type": "float", "default": 0.2,   "min": 0.0,    "max": 0.5,  "desc": "Dropout rate between layers"},
        {"name": "epochs",       "type": "int",   "default": 50,    "min": 10,     "max": 200,  "desc": "Training epochs"},
        {"name": "learning_rate","type": "float", "default": 0.001, "min": 0.0001, "max": 0.01, "desc": "Adam optimizer learning rate"},
        {"name": "batch_size",   "type": "int",   "default": 32,    "min": 8,      "max": 256,  "desc": "Mini-batch size"},
    ],
}


@router.get("/sessions/{session_id}/models/hyperparams")
def get_model_hyperparams(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Return hyperparameter schemas for all available models."""
    _get_session_or_404(user.tenant_id, session_id)
    return ok(_MODEL_HYPERPARAMS)


def _llm_health_narrative(
    global_score: float,
    status: str,
    sku_reports: dict,
    profile: dict,
) -> Optional[dict]:
    """Call the local LLM to produce a 3-field business-language diagnosis."""
    try:
        import json
        from backend.ai.local_llm import get_local_llm_client

        n_skus    = len(sku_reports)
        stats     = profile.get("stats", {})
        n_rows    = stats.get("n_rows", 0)
        date_min  = stats.get("date_min", "?")
        date_max  = stats.get("date_max", "?")

        low_score  = sum(1 for r in sku_reports.values() if r.get("quality_score", 100) < 50)
        intermit   = sum(1 for r in sku_reports.values() if r.get("is_intermittent", False))

        issues: list[str] = []
        for sku, r in list(sku_reports.items())[:10]:
            for w in r.get("warnings", []):
                issues.append(f"  - {sku}: {w}")
        issues_text = "\n".join(issues[:15]) if issues else "  - No critical issues found"

        prompt = (
            "You are a data quality auditor for a demand forecasting platform used by "
            "logistics and distribution companies.\n\n"
            f"Dataset summary:\n"
            f"- Global health score: {global_score}/100  (status: {status})\n"
            f"- Products/SKUs analyzed: {n_skus}\n"
            f"- Total rows: {n_rows:,}\n"
            f"- Date range: {date_min} to {date_max}\n"
            f"- SKUs with low data quality (score < 50): {low_score}\n"
            f"- Intermittent demand SKUs (high zero-ratio): {intermit}\n"
            f"- Issues detected:\n{issues_text}\n\n"
            "Write a concise business diagnosis. "
            "Respond ONLY with valid JSON — no extra text:\n"
            "{\n"
            '  "executive_summary": "2-3 sentence overview for management. Mention business impact.",\n'
            '  "risks": "1-2 sentences on what forecast accuracy risks these data issues create.",\n'
            '  "recommendation": "1-2 sentences on the single most important action the analyst should take now."\n'
            "}\n\n"
            "Rules: never mention algorithm names (IQR, PSI, ADF, KPSS, etc.). "
            "Use business language only (inventory risk, demand signal, data gaps). "
            "Each field under 120 words."
        )

        client = get_local_llm_client()
        msg = client.messages.create(
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )

        text = msg.content[0].text.strip()
        if "{" in text and "}" in text:
            start = text.index("{")
            end   = text.rindex("}") + 1
            return json.loads(text[start:end])
        return None
    except Exception as exc:
        log.warning("LLM health narrative failed: %s", exc)
        return None
