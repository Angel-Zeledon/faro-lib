"""Re-forecast a finished session with newer sales, without retraining it.

A re-forecast is a NEW session derived from a COMPLETED one: it copies the
parent's configuration, points at the (newer) data, and runs on the ordinary job
queue. The runner sees `is_reforecast` and, instead of fitting models, loads the
parent's verified artifacts and asks the engine to forecast from them. The parent
is never modified, and a failed re-forecast leaves it serving, exactly as a
failed scheduled retrain does.

The new session is a session like any other: it counts against the plan's
`max_sessions` ceiling, joins the parent's family at the parent's granularity (so
`resolve_active_session` picks the newest COMPLETED one), and writes its own
lineage manifest, which names the parent, the trigger, the dataset hash and the
artifacts used.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from backend.config import settings
from backend.db import session_store
from backend.db.connection import execute, query, query_one
from backend.errors import AppError
from backend.lineage.hashing import dataset_content_hash
from backend.model_registry import service as registry
from backend.sessions import service as session_svc

log = logging.getLogger(__name__)

# Configs copied from the parent into the re-forecast. Same list a scheduled
# retrain copies: `dataset_ref` and `inspection` travel too, because the engine
# reads them and re-deriving them could change the shape the models were fitted on.
_COPIED_CONFIG_FIELDS = (
    "dataset_ref", "inspection", "columns_cfg", "features_cfg", "models_cfg",
    "validation_cfg", "business_cfg", "forecast_cfg", "granularity_cfg",
)

# Families whose models cannot carry state and are refitted per series. Shown to
# the user so "what will happen" is true before they press the button.
_REFIT_ONLY_KINDS = ("stat_refit_only",)

_IN_FLIGHT = ("QUEUED", "RUNNING")


# -- what a session was trained on ------------------------------------------

def _trained_dataset_hash(tenant_id: str, session_id: str) -> Optional[str]:
    row = query_one(
        """SELECT manifest->'dataset'->>'content_hash' AS h FROM session_manifests
            WHERE tenant_id = %s AND session_id = %s AND outcome = 'COMPLETED'
            ORDER BY created_at DESC LIMIT 1""",
        (tenant_id, session_id),
    )
    return (row or {}).get("h")


def _newer_dataset_id(tenant_id: str, dataset_id: str) -> Optional[str]:
    """A later snapshot of the same SQL source, if one has been materialized."""
    row = query_one(
        """SELECT n.id FROM datasets d
             JOIN datasets n ON n.tenant_id = d.tenant_id AND n.parent_id = d.parent_id
                            AND n.id <> d.id AND n.uploaded_at > d.uploaded_at
            WHERE d.id = %s AND d.tenant_id = %s AND d.parent_id IS NOT NULL
            ORDER BY n.uploaded_at DESC LIMIT 1""",
        (dataset_id, tenant_id),
    )
    return row["id"] if row else None


def _last_full_refit(session: dict) -> Optional[datetime]:
    value = session.get("last_full_refit_at")
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def refit_age_days(session: dict) -> Optional[float]:
    when = _last_full_refit(session)
    if when is None:
        return None
    return round((datetime.now(timezone.utc) - when).total_seconds() / 86400.0, 2)


def refit_due(session: dict) -> bool:
    """True when the models behind this session are old enough that a scheduled
    retrain in 'reforecast' mode should refit them instead. An unknown age counts
    as due: models of unknown age are not models to keep trusting."""
    age = refit_age_days(session)
    return age is None or age >= float(settings.reforecast_full_refit_days)


def _refit_only_families(tenant_id: str, session_id: str) -> list[str]:
    return [r["family"] for r in registry.list_artifacts(tenant_id, session_id)
            if r["kind"] in _REFIT_ONLY_KINDS]


def _reforecast_in_flight(tenant_id: str, parent_session_id: str) -> Optional[str]:
    row = query_one(
        "SELECT id FROM sessions WHERE tenant_id = %s AND parent_session_id = %s "
        "AND is_reforecast AND status IN %s LIMIT 1",
        (tenant_id, parent_session_id, _IN_FLIGHT),
    )
    return row["id"] if row else None


# -- status ------------------------------------------------------------------

def status(tenant_id: str, session_id: str) -> dict:
    """Whether a re-forecast is on offer for this session and what it would do.
    Reasons are stable codes the frontend renders; nothing here is prose."""
    session = session_svc.get_session(tenant_id, session_id)
    if not session:
        raise AppError("session_not_found", "Session not found", status_code=404)

    out = {
        "session_id": session_id, "eligible": False, "reason": None,
        "has_artifacts": False, "new_data": False, "newer_dataset_id": None,
        "last_full_refit_at": None, "refit_age_days": refit_age_days(session),
        "refit_due": refit_due(session), "refit_families": [],
        "is_reforecast": bool(session.get("is_reforecast")),
        "parent_session_id": session.get("parent_session_id"),
    }
    when = _last_full_refit(session)
    out["last_full_refit_at"] = when.isoformat() if when else None

    if session["status"] != "COMPLETED":
        out["reason"] = "session_not_completed"
        return out
    if session.get("is_backtest"):
        out["reason"] = "backtest_session"
        return out
    out["has_artifacts"] = registry.has_artifacts(tenant_id, session_id)
    if not out["has_artifacts"]:
        out["reason"] = "no_artifacts"
        return out
    out["refit_families"] = _refit_only_families(tenant_id, session_id)
    dataset_id = session.get("dataset_id")
    if not dataset_id:
        out["reason"] = "dataset_missing"
        return out

    newer = _newer_dataset_id(tenant_id, dataset_id)
    out["newer_dataset_id"] = newer
    current = dataset_content_hash(tenant_id, dataset_id)
    trained = _trained_dataset_hash(tenant_id, session_id)
    out["new_data"] = bool(newer) or (
        current is not None and trained is not None and current != trained)
    if not out["new_data"]:
        out["reason"] = "no_new_data"
        return out
    if _reforecast_in_flight(tenant_id, session_id):
        out["reason"] = "already_running"
        return out
    out["eligible"] = True
    return out


# -- launch -------------------------------------------------------------------

def launch_reforecast(
    tenant_id: str, user_id: str, parent_session_id: str, *,
    schedule_id: Optional[str] = None, dataset_id: Optional[str] = None,
    require_new_data: bool = True, enforce_job_cap: bool = True,
) -> dict:
    """Create and enqueue a re-forecast of `parent_session_id`.

    Returns `{"session_id", "job_id", "parent_session_id", "dataset_id"}`.
    Raises `AppError` with a stable code for every refusal; nothing is created
    when it does (a session made by a launch that then fails is archived)."""
    from backend.entitlements.service import enforce_limit, limit_guard
    from backend.sessions import data_gate
    from backend.sessions.family_service import _enqueue
    from backend.training import job_service

    parent = session_svc.get_session(tenant_id, parent_session_id)
    if not parent:
        raise AppError("session_not_found", "Session not found", status_code=404)
    if parent["status"] != "COMPLETED":
        raise AppError(
            "reforecast_parent_not_completed",
            "Only a completed session can be re-forecast.",
            status_code=409, params={"status": parent["status"]})
    if parent.get("is_backtest"):
        raise AppError(
            "reforecast_parent_is_backtest",
            "A back-test is a measurement, not a forecast to keep updating.",
            status_code=409)
    if not registry.has_artifacts(tenant_id, parent_session_id):
        raise AppError(
            "reforecast_no_artifacts",
            "This session was trained before models were stored; train it again "
            "once to enable re-forecasting.",
            status_code=409, params={"session_id": parent_session_id})
    if _reforecast_in_flight(tenant_id, parent_session_id):
        raise AppError(
            "reforecast_already_running",
            "A re-forecast of this session is already running.",
            status_code=409, params={"session_id": parent_session_id})

    target_dataset = dataset_id or parent.get("dataset_id")
    if not target_dataset:
        raise AppError("reforecast_dataset_missing",
                       "The session has no dataset to read new sales from.",
                       status_code=409)
    if dataset_id and not query_one(
            "SELECT 1 AS ok FROM datasets WHERE id = %s AND tenant_id = %s",
            (dataset_id, tenant_id)):
        raise AppError("dataset_not_found", "Dataset not found", status_code=404)

    if require_new_data:
        trained = _trained_dataset_hash(tenant_id, parent_session_id)
        current = dataset_content_hash(tenant_id, target_dataset)
        same_dataset = target_dataset == parent.get("dataset_id")
        if same_dataset and current is not None and trained is not None and current == trained:
            raise AppError(
                "reforecast_no_new_data",
                "The data has not changed since this forecast was made.",
                status_code=409)

    if enforce_job_cap and not settings.testing_mode:
        active = job_service.count_active_jobs_for_tenant(tenant_id)
        if active >= 3:
            raise AppError(
                "too_many_active_jobs",
                f"Too many active training jobs ({active}). "
                "Wait for a job to finish before queuing another.",
                status_code=429, params={"active": active, "max": 3})

    with limit_guard(tenant_id) as conn:
        enforce_limit(tenant_id, "max_sessions",
                      session_svc.count_sessions(tenant_id, conn=conn), conn=conn)
        child = session_svc.create_session(
            tenant_id, user_id, parent["name"],
            description=parent.get("description"), tags=parent.get("tags") or [])
    child_id = child["id"]

    for field in _COPIED_CONFIG_FIELDS:
        value = session_store.get_field(tenant_id, parent_session_id, field)
        if value is not None:
            session_store.set_field(tenant_id, child_id, field, value)
    session_svc.attach_dataset(tenant_id, child_id, target_dataset)

    # The models behind the child are the parent's, so their age is the
    # parent's. A parent whose own stamp is missing (trained before this
    # existed, or inherited from nobody) falls back to when it finished.
    refit_at = parent.get("last_full_refit_at") or parent.get("updated_at")
    execute(
        """UPDATE sessions
              SET is_reforecast = TRUE, parent_session_id = %s,
                  last_full_refit_at = %s, family_id = %s, granularity = %s,
                  scheduled_job_id = %s, status = 'MODELS_CONFIGURED',
                  pipeline_step = 'train', updated_at = NOW()
            WHERE id = %s AND tenant_id = %s""",
        (parent_session_id, refit_at, parent.get("family_id"), parent.get("granularity"),
         schedule_id, child_id, tenant_id),
    )

    try:
        # The same gate every launch goes through: new rows can bring a problem
        # the old file did not have.
        data_gate.enforce(tenant_id, child_id)
        job_id = _enqueue(tenant_id, child_id, user_id)
    except Exception:
        try:
            session_svc.archive_session(tenant_id, child_id, user_id)
        except Exception:  # noqa: BLE001
            log.warning("reforecast: could not archive the failed launch %s", child_id)
        raise

    log.info("reforecast launched: parent=%s child=%s job=%s", parent_session_id, child_id, job_id)
    return {"session_id": child_id, "job_id": job_id,
            "parent_session_id": parent_session_id, "dataset_id": target_dataset}


def reforecast_parents_for_schedule(
    tenant_id: str, schedule_id: str, template_session_id: str,
) -> list[dict]:
    """The sessions a scheduled re-forecast would derive from: the COMPLETED,
    unarchived members of the family this schedule last produced (or, on its
    first run, of the template's family), each with stored models. Empty when
    there is nothing to derive from (sessions trained before models were stored)
    and the caller refits."""
    serving = query_one(
        "SELECT id, family_id FROM sessions WHERE tenant_id = %s AND scheduled_job_id = %s "
        "AND status = 'COMPLETED' AND archived_at IS NULL ORDER BY updated_at DESC LIMIT 1",
        (tenant_id, schedule_id),
    ) or query_one(
        "SELECT id, family_id FROM sessions WHERE tenant_id = %s AND id = %s "
        "AND status = 'COMPLETED'",
        (tenant_id, template_session_id),
    )
    if not serving:
        return []
    rows = query(
        """SELECT id FROM sessions WHERE tenant_id = %s AND status = 'COMPLETED'
             AND archived_at IS NULL AND NOT is_backtest
             AND (id = %s OR (family_id IS NOT NULL AND family_id = %s))
           ORDER BY updated_at DESC""",
        (tenant_id, serving["id"], serving["family_id"]),
    ) or []
    parents, seen = [], set()
    for r in rows:
        s = session_svc.get_session(tenant_id, r["id"])
        grain = s.get("granularity") or ""
        if grain in seen or not registry.has_artifacts(tenant_id, s["id"]):
            continue
        seen.add(grain)          # newest per grain: one parent per period
        parents.append(s)
    return parents
