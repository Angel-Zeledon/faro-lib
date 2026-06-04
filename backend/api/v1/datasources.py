import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator

from backend.auth.guards import CurrentUser, get_current_user
from backend.datasources import service as svc
from backend.datasources.service import SQL_ENGINES
from backend.schemas.common import ok

router = APIRouter(prefix="/data-sources", tags=["data-sources"])
log = logging.getLogger(__name__)


# ── Request models ─────────────────────────────────────────────────────────────

class CreateSqlSourceRequest(BaseModel):
    name:        str
    host:        str
    port:        int         = Field(ge=1, le=65535)
    database:    str
    username:    str
    password:    str
    engine:      str
    description: Optional[str] = None

    @field_validator("engine")
    @classmethod
    def _valid_engine(cls, v: str) -> str:
        if v not in SQL_ENGINES:
            raise ValueError(f"Unsupported engine '{v}'. Options: {sorted(SQL_ENGINES)}")
        return v


class UpdateSqlConfigRequest(BaseModel):
    host:     str
    port:     int = Field(ge=1, le=65535)
    database: str
    username: str
    engine:   str
    password: Optional[str] = None

    @field_validator("engine")
    @classmethod
    def _valid_engine(cls, v: str) -> str:
        if v not in SQL_ENGINES:
            raise ValueError(f"Unsupported engine '{v}'. Options: {sorted(SQL_ENGINES)}")
        return v


class ExecuteQueryRequest(BaseModel):
    sql:   str
    limit: int = Field(default=500, ge=1, le=5000)

    @field_validator("sql")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("sql cannot be empty")
        return v


class SaveQueryRequest(BaseModel):
    sql: str

    @field_validator("sql")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("sql cannot be empty")
        return v


class RenameSourceRequest(BaseModel):
    name:        str
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be empty")
        return v


def _ds_or_404(tenant_id: str, source_id: str) -> dict:
    src = svc.get_source(tenant_id, source_id)
    if not src:
        raise HTTPException(status_code=404, detail=f"Data source {source_id} not found")
    return src


# ── List / Get ─────────────────────────────────────────────────────────────────

@router.get("")
def list_sources(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
):
    sources = svc.list_sources(user.tenant_id, skip=skip, limit=limit)
    total = svc.count_sources(user.tenant_id)
    return ok({"items": sources, "total": total, "skip": skip, "limit": limit})


@router.get("/{source_id}")
def get_source(
    source_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    src = _ds_or_404(user.tenant_id, source_id)
    return ok(src)


# ── Create file source ─────────────────────────────────────────────────────────

@router.post("/file")
async def create_file_source(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        src = await svc.create_file_source(
            user.tenant_id, user.user_id, file, name=name, description=description
        )
        return ok(src)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Create SQL source ──────────────────────────────────────────────────────────

@router.post("/sql")
def create_sql_source(
    body: CreateSqlSourceRequest,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        src = svc.create_sql_source(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            name=body.name,
            host=body.host,
            port=body.port,
            database=body.database,
            username=body.username,
            password=body.password,
            engine=body.engine,
            description=body.description,
        )
        return ok(src)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Replace file ───────────────────────────────────────────────────────────────

@router.post("/{source_id}/file")
async def replace_file(
    source_id: str,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    _ds_or_404(user.tenant_id, source_id)
    try:
        src = await svc.replace_file_source(user.tenant_id, user.user_id, source_id, file)
        return ok(src)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Update SQL config ──────────────────────────────────────────────────────────

@router.patch("/{source_id}/sql-config")
def update_sql_config(
    source_id: str,
    body: UpdateSqlConfigRequest,
    user: CurrentUser = Depends(get_current_user),
):
    _ds_or_404(user.tenant_id, source_id)
    try:
        src = svc.update_sql_config(
            tenant_id=user.tenant_id,
            source_id=source_id,
            host=body.host,
            port=body.port,
            database=body.database,
            username=body.username,
            password=body.password,
            engine=body.engine,
        )
        return ok(src)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Test SQL connection ────────────────────────────────────────────────────────

@router.post("/{source_id}/test-connection")
def test_connection(
    source_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    _ds_or_404(user.tenant_id, source_id)
    result = svc.test_sql_connection(user.tenant_id, source_id)
    return ok(result)


# ── Execute SQL query ──────────────────────────────────────────────────────────

@router.post("/{source_id}/execute-query")
def execute_query(
    source_id: str,
    body: ExecuteQueryRequest,
    user: CurrentUser = Depends(get_current_user),
):
    _ds_or_404(user.tenant_id, source_id)
    try:
        result = svc.execute_sql_query(user.tenant_id, source_id, body.sql, limit=body.limit)
        return ok(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Save SQL query ─────────────────────────────────────────────────────────────

@router.patch("/{source_id}/query")
def save_query(
    source_id: str,
    body: SaveQueryRequest,
    user: CurrentUser = Depends(get_current_user),
):
    _ds_or_404(user.tenant_id, source_id)
    src = svc.save_sql_query(user.tenant_id, source_id, body.sql)
    return ok(src)


# ── Preview ────────────────────────────────────────────────────────────────────

@router.get("/{source_id}/preview")
def get_preview(
    source_id: str,
    rows: int = Query(100, ge=1, le=1000),
    sheet: Optional[str] = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    _ds_or_404(user.tenant_id, source_id)
    try:
        result = svc.get_preview(user.tenant_id, source_id, rows=rows, sheet=sheet)
        return ok(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Rename / update metadata ───────────────────────────────────────────────────

@router.patch("/{source_id}")
def rename_source(
    source_id: str,
    body: RenameSourceRequest,
    user: CurrentUser = Depends(get_current_user),
):
    _ds_or_404(user.tenant_id, source_id)
    src = svc.rename_source(user.tenant_id, source_id, body.name, description=body.description)
    return ok(src)


# ── Delete ─────────────────────────────────────────────────────────────────────

@router.delete("/{source_id}")
def delete_source(
    source_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    import psycopg2
    _ds_or_404(user.tenant_id, source_id)
    try:
        svc.delete_source(user.tenant_id, source_id)
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete: this data source is still referenced by one or more sessions.",
        )
    return ok({"deleted": source_id})


# ── Statistical analysis ───────────────────────────────────────────────────────

@router.get("/{source_id}/analyze")
def analyze_source(
    source_id: str,
    date_col: Optional[str] = Query(None),
    target_col: Optional[str] = Query(None),
    sku_col: Optional[str] = Query(None),
    sheet: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    _ds_or_404(user.tenant_id, source_id)
    try:
        df = svc.load_dataframe(user.tenant_id, source_id, sheet=sheet)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    detected = svc.detect_columns(df)
    dc = date_col or detected["date_col"]
    tc = target_col or detected["target_col"]
    sc = sku_col if sku_col is not None else detected.get("sku_col")

    if not dc or dc not in df.columns:
        raise HTTPException(status_code=400, detail=f"Date column '{dc}' not found. Columns: {list(df.columns)}")
    if not tc or tc not in df.columns:
        raise HTTPException(status_code=400, detail=f"Target column '{tc}' not found. Columns: {list(df.columns)}")
    if sc and sc not in df.columns:
        sc = None

    import pandas as pd
    df[dc] = pd.to_datetime(df[dc], errors="coerce")
    if date_from:
        df = df[df[dc] >= pd.Timestamp(date_from)]
    if date_to:
        df = df[df[dc] <= pd.Timestamp(date_to)]

    try:
        from forecasting_core.analysis.analyzer import TimeSeriesAnalyzer
        analyzer = TimeSeriesAnalyzer(df, date_col=dc, target_col=tc, group_col=sc or None)
        summary_df = analyzer.summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    import math

    def _clean(v):
        if isinstance(v, float) and math.isnan(v):
            return None
        if hasattr(v, "item"):
            return v.item()
        return v

    rows = [{k: _clean(v) for k, v in row.items()} for _, row in summary_df.iterrows()]

    return ok({
        "date_col":  dc,
        "target_col": tc,
        "sku_col":   sc,
        "detected":  detected,
        "columns":   list(df.columns),
        "summary":   rows,
    })


@router.get("/{source_id}/analyze/{sku_id:path}")
def analyze_sku(
    source_id: str,
    sku_id: str,
    date_col: str = Query(...),
    target_col: str = Query(...),
    sku_col: Optional[str] = Query(None),
    sheet: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    _ds_or_404(user.tenant_id, source_id)
    try:
        df = svc.load_dataframe(user.tenant_id, source_id, sheet=sheet)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        import pandas as pd
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        if date_from:
            df = df[df[date_col] >= pd.Timestamp(date_from)]
        if date_to:
            df = df[df[date_col] <= pd.Timestamp(date_to)]
        from forecasting_core.analysis.analyzer import TimeSeriesAnalyzer
        analyzer = TimeSeriesAnalyzer(df, date_col=date_col, target_col=target_col, group_col=sku_col or None)
        sku_arg = sku_id if sku_col else None
        report = analyzer.analyze(sku_arg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    if sku_col and sku_col in df.columns:
        sub = df[df[sku_col].astype(str) == sku_id].sort_values(date_col)
    else:
        sub = df.sort_values(date_col)

    import pandas as pd
    series = [
        {"date": str(d)[:10], "value": float(v) if pd.notna(v) else None}
        for d, v in zip(sub[date_col], sub[target_col])
    ]

    # Detect outliers via Tukey IQR fences (Q1 − 1.5×IQR … Q3 + 1.5×IQR)
    import numpy as np
    _vals = [pt["value"] for pt in series if pt["value"] is not None]
    outliers: list[dict] = []
    if len(_vals) >= 4:
        _arr = np.array(_vals, dtype=float)
        _q1, _q3 = float(np.percentile(_arr, 25)), float(np.percentile(_arr, 75))
        _iqr = _q3 - _q1
        _lo = _q1 - 1.5 * _iqr
        _hi = _q3 + 1.5 * _iqr
        _mean = float(_arr.mean())
        _std = float(_arr.std()) if float(_arr.std()) > 0 else 1.0
        for pt in series:
            v = pt["value"]
            if v is None:
                continue
            fv = float(v)
            if fv < _lo or fv > _hi:
                outliers.append({
                    "date": pt["date"],
                    "value": fv,
                    "z_score": round((fv - _mean) / _std, 2),
                    "lower_bound": round(_lo, 4),
                    "upper_bound": round(_hi, 4),
                    "reason": (
                        f"Exceeds upper fence {_hi:.2f} (Q3 + 1.5×IQR)"
                        if fv > _hi
                        else f"Below lower fence {_lo:.2f} (Q1 − 1.5×IQR)"
                    ),
                })

    import math

    def _sanitize(obj):
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize(v) for v in obj]
        if isinstance(obj, float) and math.isnan(obj):
            return None
        if hasattr(obj, "item"):
            return obj.item()
        return obj

    return ok({"sku": sku_id, "report": _sanitize(report), "series": series, "outliers": outliers})
