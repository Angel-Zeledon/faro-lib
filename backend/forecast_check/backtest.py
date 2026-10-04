"""Pure back-test: make "forecast vs. reality" possible when the future has not
happened yet.

A completed session's forecast starts after its data ends, so there is nothing
to grade it against. A back-test manufactures the missing overlap from history
the tenant already has: it copies the session's dataset WITHOUT its last N
periods, trains a new session on that copy with the same configuration, and the
new session's forecast then covers exactly the periods that were held out. The
comparison screen grades it against the ORIGINAL (full) dataset.

Orchestration only — the file is cut in `backend/dataframes/actuals.py`, the
training runs through the normal launch path, and the metrics stay in
ForecastingCore. Nothing is erased: the source session and its dataset are not
touched, and the copy is an ordinary dataset the tenant can see.

A back-test session carries ``is_backtest`` so planning (the semaforo, alerts,
purchase orders) never reads it: it was trained on deliberately incomplete data.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from backend.activity.service import log_action
from backend.dataframes.actuals import write_holdout_copy
from backend.datasets.service import get_dataset
from backend.db import session_store
from backend.db.connection import execute
from backend.errors import AppError
from backend.sessions import family_service as fam
from backend.sessions import service as session_svc
from backend.utils.ids import generate_id
from backend.workers.runner import build_engine_config

log = logging.getLogger(__name__)

# Configuration copied from the source session. The copy is trained exactly as
# the source was, so the only thing that differs is the data it can see.
_COPIED_FIELDS = (
    "columns_cfg", "features_cfg", "models_cfg", "validation_cfg",
    "business_cfg", "forecast_cfg",
)
_GRAIN_BY_FREQ = {"W-MON": "weekly", "MS": "monthly"}


def _grain(session: dict, target_freq: Optional[str]) -> str:
    g = (session.get("granularity") or "").lower()
    if g in fam.GENEROUS_REACH:
        return g
    return _GRAIN_BY_FREQ.get((target_freq or "").upper(), "daily")


def max_holdout(grain: str) -> int:
    """The longest hold-out a grain supports: the reach a launch pre-forecasts."""
    return fam.GENEROUS_REACH[grain]


def launch_backtest(
    tenant_id: str, user_id: str, source: dict, holdout_periods: int,
    name: Optional[str] = None,
) -> dict:
    """Train a back-test of ``source`` holding out its last ``holdout_periods``.

    Returns ``{session_id, dataset_id, compare_dataset_id, cutoff, grain,
    holdout_periods, family}``. Raises ``AppError`` with a structured code when
    the session cannot be back-tested; nothing is left half-made on those paths
    except, at worst, the held-out dataset copy (a normal dataset).
    """
    from backend.entitlements.service import enforce_limit, limit_guard
    from backend.storage import paths

    ds = get_dataset(tenant_id, source["dataset_id"]) if source.get("dataset_id") else None
    if not ds or not ds.get("file_path") or not Path(ds["file_path"]).exists():
        raise AppError(
            "backtest_dataset_missing",
            "The dataset this session was trained on is no longer available.",
            status_code=422, params={"session_id": source["id"]})
    missing = [f.replace("_cfg", "") for f in ("columns_cfg", "models_cfg")
               if not session_store.get_field(tenant_id, source["id"], f)]
    if missing:
        raise AppError(
            "training_config_incomplete",
            f"Missing required configuration: {missing}.",
            status_code=422, params={"missing": ", ".join(missing)})

    cfg = build_engine_config(tenant_id, source["id"])
    cols = cfg["columns"]
    target_freq = (cfg.get("granularity") or {}).get("target_freq")
    grain = _grain(source, target_freq)
    if holdout_periods > max_holdout(grain):
        raise AppError(
            "backtest_holdout_too_long",
            f"At most {max_holdout(grain)} {grain} periods can be held out.",
            status_code=422,
            params={"max": max_holdout(grain), "grain": grain})

    # 1. The held-out copy, stored as an ordinary dataset.
    new_ds_id = generate_id("ds")
    dst_dir = paths.dataset_dir(tenant_id, new_ds_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(ds["file_path"]).suffix
    dst = dst_dir / f"data{suffix}"
    cut = write_holdout_copy(ds["file_path"], str(dst), cols["date"], holdout_periods, target_freq)
    if "error" in cut:
        dst.unlink(missing_ok=True)
        try:
            dst_dir.rmdir()
        except OSError:
            pass
        raise AppError(
            "backtest_holdout_too_large" if cut["error"] == "holdout_too_large" else "backtest_dataset_unreadable",
            "That many periods cannot be held out of this dataset: too little history would remain."
            if cut["error"] == "holdout_too_large" else "The dataset could not be read for a back-test.",
            status_code=422, params={"holdout_periods": holdout_periods})
    execute(
        """INSERT INTO datasets
           (id, tenant_id, name, original_filename, file_type, file_path,
            size_bytes, uploaded_by, uploaded_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
        (new_ds_id, tenant_id, f"{ds['name']} (until {cut['cutoff']})",
         f"{Path(ds.get('original_filename') or ds['name']).stem}_until_{cut['cutoff']}{suffix}",
         suffix.lstrip("."), str(dst), dst.stat().st_size, user_id))

    # 2. The session — counted against the plan ceiling like any other, so at
    # the ceiling this is refused (nothing is deleted to make room).
    with limit_guard(tenant_id) as conn:
        enforce_limit(tenant_id, "max_sessions",
                      session_svc.count_sessions(tenant_id, conn=conn), conn=conn)
        title = name or f"Back-test: {source['name']} (last {holdout_periods} held out)"
        run = session_svc.create_session(
            tenant_id, user_id, title,
            description=f"Back-test of {source['id']}: trained until {cut['cutoff']}.",
            tags=["backtest"])
    run_id = run["id"]
    execute(
        "UPDATE sessions SET is_backtest = TRUE, backtest_source_dataset_id = %s, "
        "backtest_holdout_periods = %s WHERE id = %s AND tenant_id = %s",
        (ds["id"], holdout_periods, run_id, tenant_id))
    for field in _COPIED_FIELDS:
        value = session_store.get_field(tenant_id, source["id"], field)
        if value is not None:
            session_store.set_field(tenant_id, run_id, field, value)
    session_svc.attach_dataset(tenant_id, run_id, new_ds_id)
    execute(
        "UPDATE sessions SET status = 'MODELS_CONFIGURED', pipeline_step = 'train', "
        "updated_at = NOW() WHERE id = %s AND tenant_id = %s", (run_id, tenant_id))

    # 3. Launch through the normal path: one grain (the source's), a horizon of
    # exactly the held-out periods.
    try:
        family = fam.launch_training_family(
            tenant_id, run_id, user_id,
            user_horizon_days=holdout_periods * fam.DAYS_PER_PERIOD[grain],
            user_granularity=grain)
    except Exception:
        # Never delete: the session is archived so the ceiling slot is freed and
        # the attempt stays in the library for whoever needs to see why.
        session_svc.archive_session(tenant_id, run_id, user_id)
        raise
    execute(
        "UPDATE sessions SET is_backtest = TRUE, backtest_source_dataset_id = %s, "
        "backtest_holdout_periods = %s WHERE tenant_id = %s AND family_id = %s",
        (ds["id"], holdout_periods, tenant_id, family["family_id"]))
    log_action(tenant_id, user_id, "session.backtest", resource=run_id, context={
        "source_session_id": source["id"], "source_dataset_id": ds["id"],
        "holdout_periods": holdout_periods, "cutoff": cut["cutoff"], "grain": grain})
    return {
        "session_id": run_id, "dataset_id": new_ds_id, "compare_dataset_id": ds["id"],
        "cutoff": cut["cutoff"], "last_date": cut["last_date"], "grain": grain,
        "holdout_periods": holdout_periods, "family": family,
    }
