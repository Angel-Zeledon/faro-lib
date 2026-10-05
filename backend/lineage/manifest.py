"""The lineage manifest: an immutable record of how one forecast was produced.

A forecast a buyer acts on has to be answerable: who or what started it, on
which data (by content hash, not by name), under which configuration, with
which code, what each model did and how long each stage took, and a hash of the
forecast it produced — so "is this the same forecast I saw last week?" is a
comparison of two hashes, not a recollection.

One row per training JOB in `session_manifests`, written once when the run ends
(COMPLETED or FAILED). A database trigger refuses any UPDATE, so the record
cannot drift after the fact. No pandas here: the caller (the worker runner)
hands over plain dicts.
"""

from __future__ import annotations

import logging
import platform
from datetime import datetime, timezone
from importlib import metadata
from typing import Any, Optional

from backend.db import session_store
from backend.db.connection import _json, execute, query_one
from backend.lineage.hashing import dataset_content_hash, json_sha256
from backend.utils.ids import generate_id

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# The configuration blobs that define a run. `dataset_ref` and `inspection` are
# omitted: the first is a path and the second is a copy of the data's shape.
_CONFIG_FIELDS = (
    "columns_cfg", "features_cfg", "models_cfg", "validation_cfg",
    "forecast_cfg", "business_cfg", "granularity_cfg",
)

_LIBRARIES = (
    "lightgbm", "xgboost", "prophet", "statsmodels", "scikit-learn", "torch",
    "numpy", "pandas", "scipy", "pmdarima",
)


def _version_of(distribution: str) -> Optional[str]:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def runtime_versions() -> dict:
    """Engine and library versions, read from package metadata (no imports of
    the heavy libraries). Missing optional libraries are reported as absent."""
    from forecasting_core import __version__ as engine_version
    return {
        "engine": engine_version,
        "python": platform.python_version(),
        "libraries": {name: _version_of(name) for name in _LIBRARIES},
    }


def _trigger(tenant_id: str, session: dict, job: dict) -> dict:
    """Who or what started the run, from `jobs.created_by`."""
    created_by = (job.get("created_by") or "system")
    if created_by == "scheduler" or session.get("scheduled_job_id"):
        return {
            "kind": "schedule", "actor_id": created_by,
            "schedule_id": session.get("scheduled_job_id"),
            "label": None,
        }
    if created_by.startswith("api_key:"):
        key_id = created_by.split(":", 1)[1]
        key = query_one(
            "SELECT name FROM api_keys WHERE id = %s AND tenant_id = %s",
            (key_id, tenant_id),
        )
        return {"kind": "api_key", "actor_id": created_by, "schedule_id": None,
                "label": (key or {}).get("name")}
    if created_by == "system":
        return {"kind": "system", "actor_id": created_by, "schedule_id": None,
                "label": None}
    user = query_one(
        "SELECT email FROM users WHERE id = %s AND tenant_id = %s",
        (created_by, tenant_id),
    )
    return {"kind": "user", "actor_id": created_by, "schedule_id": None,
            "label": (user or {}).get("email")}


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _model_outcomes(metrics: dict) -> dict:
    """Per model: how many series it produced a score for, and its averages."""
    outcomes: dict[str, dict] = {}
    for row in (metrics or {}).get("rows") or []:
        model = str(row.get("model"))
        entry = outcomes.setdefault(model, {"series": 0})
        entry["series"] += 1
    for model, agg in ((metrics or {}).get("by_model") or {}).items():
        entry = outcomes.setdefault(str(model), {"series": 0})
        for key in ("avg_wape", "avg_mae", "avg_bias"):
            value = agg.get(key)
            entry[key] = round(float(value), 4) if value is not None else None
    return outcomes


def _public_effective_config(config: dict) -> dict:
    """The engine config the run really used, minus anything that is a path on
    the server's disk."""
    cleaned = dict(config or {})
    data = dict(cleaned.get("data") or {})
    data.pop("path", None)
    if data:
        cleaned["data"] = data
    else:
        cleaned.pop("data", None)
    return cleaned


def _guided_reading(tenant_id: str, session_id: str) -> Optional[dict]:
    record = (session_store.get_field(tenant_id, session_id, "dataset_ref") or {}).get(
        "guided_reading")
    if not record:
        return None
    return {k: record.get(k) for k in (
        "source_dataset_id", "derived_dataset_id", "fixes", "mapping",
        "rows_in", "rows_out", "applied_at", "applied_by")}


def build_manifest(
    tenant_id: str, session_id: str, job_id: Optional[str], *,
    outcome: str, error: Optional[str] = None,
    result: Optional[dict] = None, forecasts: Optional[dict] = None,
    stage_timings: Optional[dict] = None,
    artifacts: Optional[list] = None, reforecast: Optional[dict] = None,
) -> dict:
    """Assemble the manifest from what the run left behind. Plain dicts only.

    `artifacts` lists the persisted model files the run produced (family, kind,
    SHA-256): the digests a later re-forecast verifies before loading anything.
    `reforecast` is present only for a re-forecast and records what it was
    derived from (see backend/model_registry/reforecast_service.py)."""
    from backend.sessions.service import get_session
    from backend.training.job_service import get_job

    session = get_session(tenant_id, session_id) or {}
    job = (get_job(tenant_id, job_id) if job_id else None) or {}
    result = result or {}
    forecasts = forecasts or {}

    dataset_id = session.get("dataset_id")
    dataset = None
    if dataset_id:
        dataset = query_one(
            "SELECT id, name, size_bytes, row_count, column_count, source_type, parent_id "
            "FROM datasets WHERE id = %s AND tenant_id = %s", (dataset_id, tenant_id))
    configs = {f: session_store.get_field(tenant_id, session_id, f)
               for f in _CONFIG_FIELDS}
    started, finished = job.get("started_at"), job.get("completed_at")
    duration = None
    if started and finished:
        duration = round((finished - started).total_seconds(), 2)

    models_cfg = configs.get("models_cfg") or {}
    effective = result.get("config") or {}
    # What the engine was actually given (mode "all" expands to a list the
    # session config does not contain), falling back on what was selected.
    selected_models = (list((effective.get("models") or {}).keys())
                       or (models_cfg.get("selected_models")
                           if isinstance(models_cfg, dict) else None))
    metrics = result.get("metrics") or {}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "outcome": outcome,
        "error": (error or None) and error[:500],
        "session": {
            "id": session_id, "name": session.get("name"),
            "family_id": session.get("family_id"),
            "granularity": session.get("granularity"),
        },
        "trigger": _trigger(tenant_id, session, job),
        "timing": {
            "queued_at": _iso(job.get("created_at")),
            "started_at": _iso(started), "finished_at": _iso(finished),
            "duration_seconds": duration,
        },
        "dataset": {
            "id": dataset_id,
            "name": (dataset or {}).get("name"),
            "content_hash": (dataset_content_hash(tenant_id, dataset_id)
                             if dataset_id else None),
            "size_bytes": (dataset or {}).get("size_bytes"),
            "source_type": (dataset or {}).get("source_type"),
            "parent_id": (dataset or {}).get("parent_id"),
            # What the guided upload did to the file before this run (None when
            # the file was used as uploaded). The fix list is the whole
            # transformation: replaying it over the parent dataset reproduces
            # this one.
            "guided_reading": _guided_reading(tenant_id, session_id),
        },
        "counts": {
            "rows": (dataset or {}).get("row_count"),
            "skus_forecast": len(forecasts),
            "skus_excluded": len(result.get("excluded_skus") or []),
        },
        "config": {
            **configs,
            "effective_engine_config": _public_effective_config(result.get("config") or {}),
        },
        "versions": runtime_versions(),
        "models": {
            "selected": selected_models,
            "outcomes": _model_outcomes(metrics),
        },
        "stage_timings_seconds": stage_timings or {},
        "forecast": {
            "hash": json_sha256(forecasts) if forecasts else None,
            "series_count": len(forecasts),
        },
    }
    if artifacts:
        manifest["model_artifacts"] = artifacts
    if reforecast is not None:
        manifest["reforecast"] = {
            "parent_session_id": session.get("parent_session_id"),
            "parent_dataset_hash": (reforecast or {}).get("parent_dataset_hash"),
            "last_full_refit_at": _iso(session.get("last_full_refit_at")),
            **{k: v for k, v in reforecast.items() if k != "parent_dataset_hash"},
        }
    return manifest


def save_manifest(
    tenant_id: str, session_id: str, job_id: Optional[str], **kwargs: Any,
) -> Optional[str]:
    """Build and store the manifest. Never raises: this runs at the very end of
    a training (or in its failure handler), and a lineage record is not worth
    failing a finished forecast over. A failure is logged at ERROR."""
    try:
        manifest = build_manifest(tenant_id, session_id, job_id, **kwargs)
        manifest_id = generate_id("mf")
        execute(
            """INSERT INTO session_manifests
               (id, session_id, tenant_id, job_id, outcome, manifest)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (manifest_id, session_id, tenant_id, job_id,
             manifest["outcome"], _json(manifest)),
        )
        return manifest_id
    except Exception:  # noqa: BLE001
        log.exception("could not write the lineage manifest for session=%s", session_id)
        return None


def latest_manifest(tenant_id: str, session_id: str) -> Optional[dict]:
    row = query_one(
        "SELECT id, session_id, job_id, outcome, manifest, created_at "
        "FROM session_manifests WHERE tenant_id = %s AND session_id = %s "
        "ORDER BY created_at DESC LIMIT 1",
        (tenant_id, session_id),
    )
    if not row:
        return None
    return {
        "id": row["id"], "session_id": row["session_id"], "job_id": row["job_id"],
        "outcome": row["outcome"], "created_at": _iso(row["created_at"]),
        "manifest": row["manifest"],
    }
