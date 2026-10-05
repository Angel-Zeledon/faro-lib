"""The installation's operations snapshot: one read-only answer to "is it healthy?".

Audience: the INSTANCE OPERATOR (see `access.py`), never a tenant admin. The
numbers here span every tenant (queue depth, failed jobs), so they are
deployment information, not tenant information.

Shape rule: every reading is a `check` of the form
`{"key", "state", "detail"}` where `state` is one of

  * `ok`        - inside its threshold;
  * `degraded`  - outside it (the SLO the registry's `operations` service names);
  * `unknown`   - the reading could not be taken or its source is not wired.
                  Never folded into `ok`: a backup nobody can see is not a
                  backup that happened.

`overall` is `degraded` when any check is, else `unknown` when any is, else
`ok`. The pure `evaluate_*` functions take plain values so the thresholds are
tested without a database or a disk.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

OK, DEGRADED, UNKNOWN = "ok", "degraded", "unknown"

# An archive this small is an empty Docker volume, not a small instance
# (deploy/RESTORE.md, "If you back up a Docker volume, check the name").
MIN_STORAGE_ARCHIVE_BYTES = 10_000


def _check(key: str, state: str, detail: dict | None = None) -> dict:
    return {"key": key, "state": state, "detail": detail or {}}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ── Pure evaluators ─────────────────────────────────────────────────────────

def evaluate_queue(
    queued: int, oldest_queued_age_s: Optional[float], max_wait_minutes: float,
) -> dict:
    degraded = (
        oldest_queued_age_s is not None
        and oldest_queued_age_s > max_wait_minutes * 60.0
    )
    return _check("queue", DEGRADED if degraded else OK, {
        "queued": queued,
        "oldest_queued_age_seconds": (
            round(oldest_queued_age_s) if oldest_queued_age_s is not None else None),
        "max_wait_minutes": max_wait_minutes,
    })


def evaluate_running(running: list[dict], max_minutes: float) -> dict:
    longest = max((j["elapsed_seconds"] for j in running), default=0)
    return _check(
        "running_jobs",
        DEGRADED if longest > max_minutes * 60.0 else OK,
        {"running": len(running), "longest_elapsed_seconds": longest,
         "max_minutes": max_minutes},
    )


def evaluate_heartbeat(
    age_s: Optional[float], queued: int, stale_after_s: float,
) -> dict:
    """No heartbeat ever is `unknown` when nothing waits (a fresh install, or a
    deployment whose worker predates the heartbeat) and `degraded` when jobs
    are waiting - then nobody is demonstrably claiming them."""
    if age_s is None:
        return _check("worker_heartbeat", DEGRADED if queued else UNKNOWN,
                      {"age_seconds": None, "queued": queued})
    return _check("worker_heartbeat", DEGRADED if age_s > stale_after_s else OK,
                  {"age_seconds": round(age_s), "stale_after_seconds": stale_after_s})


def evaluate_disk(key: str, total: int, free: int, min_free_pct: float) -> dict:
    pct = 100.0 * free / total if total else 0.0
    return _check(key, DEGRADED if pct < min_free_pct else OK, {
        "total_bytes": total, "free_bytes": free,
        "free_percent": round(pct, 1), "min_free_percent": min_free_pct,
    })


def evaluate_pool(stats: Optional[dict], max_pct: float) -> dict:
    if stats is None:
        return _check("db_pool", UNKNOWN, {})
    return _check("db_pool",
                  DEGRADED if stats["saturation_pct"] >= max_pct else OK,
                  {**stats, "max_saturation_percent": max_pct})


def evaluate_latency(rows: list[dict], max_p95_ms: float) -> dict:
    slow = [r["family"] for r in rows if r["count"] >= 5 and r["p95_ms"] > max_p95_ms]
    return _check("latency", DEGRADED if slow else OK,
                  {"slow_families": slow, "max_p95_ms": max_p95_ms})


def evaluate_backup(
    marker: Optional[dict], error: Optional[str], now: datetime, max_age_h: float,
) -> dict:
    """`marker` is the parsed success file; `error` says why there is none."""
    if marker is None:
        return _check("backup", UNKNOWN, {"reason": error or "no_marker"})
    finished = marker.get("finished_at")
    try:
        finished_dt = _aware(datetime.fromisoformat(str(finished).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return _check("backup", DEGRADED, {"reason": "marker_unreadable_timestamp"})
    age_h = (now - finished_dt).total_seconds() / 3600.0
    problems = []
    if marker.get("status") != "ok":
        problems.append("last_run_not_ok")
    if age_h > max_age_h:
        problems.append("too_old")
    storage_bytes = marker.get("storage_bytes")
    if isinstance(storage_bytes, int) and storage_bytes < MIN_STORAGE_ARCHIVE_BYTES:
        problems.append("storage_archive_suspiciously_small")
    return _check("backup", DEGRADED if problems else OK, {
        "finished_at": finished_dt.isoformat(),
        "age_hours": round(age_h, 1),
        "max_age_hours": max_age_h,
        "problems": problems,
        "db_bytes": marker.get("db_bytes"),
        "storage_bytes": storage_bytes,
    })


def overall(checks: list[dict]) -> str:
    states = {c["state"] for c in checks}
    if DEGRADED in states:
        return DEGRADED
    if UNKNOWN in states:
        return UNKNOWN
    return OK


def classify_error(message: Optional[str]) -> str:
    """The error CLASS of a failed job's stored message.

    `jobs.error` is free text. A Python exception name at the start
    (`ValueError: ...`, `psycopg2.errors.X: ...`) is the class; anything else
    is `other`, so the 24h table groups by cause and never by message."""
    if not message:
        return "unknown"
    match = re.match(r"\s*([A-Za-z_][\w.]*)\s*:", message)
    if not match:
        return "other"
    name = match.group(1).rsplit(".", 1)[-1]
    # A class name is CapWords; "Training failed: ..." style prose is not one.
    return name if name[:1].isupper() and " " not in name and name.isidentifier() \
        and any(c.islower() for c in name) and name == name.strip() else "other"


# ── Readers (impure) ────────────────────────────────────────────────────────

def read_backup_marker(path_str: str) -> tuple[Optional[dict], Optional[str]]:
    """Parse the backup script's success marker. Returns `(marker, reason)`."""
    if not path_str:
        return None, "backup_status_path_not_set"
    path = Path(path_str)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "marker_not_found"
    except OSError as exc:
        return None, f"marker_unreadable:{type(exc).__name__}"
    try:
        data = json.loads(raw)
    except ValueError:
        return None, "marker_not_json"
    return (data, None) if isinstance(data, dict) else (None, "marker_not_object")


def _disk(path_str: str) -> Optional[tuple[int, int]]:
    try:
        usage = shutil.disk_usage(path_str)
    except OSError:
        return None
    return usage.total, usage.free


def _queue_state() -> tuple[int, Optional[float], list[dict], list[dict]]:
    from backend.db.connection import query, query_one

    row = query_one(
        "SELECT COUNT(*) AS n, "
        "       EXTRACT(EPOCH FROM (NOW() - MIN(created_at))) AS oldest "
        "FROM jobs WHERE status = 'QUEUED'") or {}
    queued = int(row.get("n") or 0)
    oldest = float(row["oldest"]) if row.get("oldest") is not None else None

    running = [{
        "job_id": r["id"],
        "tenant_id": r["tenant_id"],
        "worker_id": r.get("worker_id"),
        "elapsed_seconds": int(r["elapsed"] or 0),
    } for r in query(
        "SELECT id, tenant_id, worker_id, "
        "       EXTRACT(EPOCH FROM (NOW() - COALESCE(started_at, created_at))) AS elapsed "
        "FROM jobs WHERE status = 'RUNNING' ORDER BY started_at NULLS LAST LIMIT 50")]

    failed_rows = query(
        "SELECT error FROM jobs WHERE status = 'FAILED' "
        "AND completed_at >= NOW() - INTERVAL '24 hours' LIMIT 5000")
    classes: dict[str, int] = {}
    for r in failed_rows:
        key = classify_error(r.get("error"))
        classes[key] = classes.get(key, 0) + 1
    failed = [{"error_class": k, "count": v}
              for k, v in sorted(classes.items(), key=lambda kv: -kv[1])]
    return queued, oldest, running, failed


def snapshot(now: Optional[datetime] = None) -> dict:
    """Take every reading. A reading that raises becomes `unknown` with its
    cause; the snapshot itself never fails because one source is down."""
    from backend.config import settings
    from backend.db import connection as db
    from backend.middleware import latency_window
    from backend.workers import loop_state

    now = now or datetime.now(timezone.utc)
    checks: list[dict] = []
    out: dict[str, Any] = {"generated_at": now.isoformat()}

    queued = 0
    try:
        queued, oldest, running, failed = _queue_state()
        checks.append(evaluate_queue(queued, oldest, settings.ops_queue_wait_degraded_minutes))
        checks.append(evaluate_running(running, settings.ops_running_job_degraded_minutes))
        out["running_jobs"] = running
        out["failed_jobs_24h"] = {"total": sum(f["count"] for f in failed), "by_error_class": failed}
    except Exception as exc:  # noqa: BLE001 - this endpoint's job is to report
        log.error("ops snapshot: queue reading failed: %s", exc)
        for key in ("queue", "running_jobs"):
            checks.append(_check(key, UNKNOWN, {"reason": type(exc).__name__}))
        out["running_jobs"], out["failed_jobs_24h"] = [], {"total": 0, "by_error_class": []}

    try:
        beat = loop_state.heartbeat()
        age = (now - beat["at"]).total_seconds() if beat else None
        checks.append(evaluate_heartbeat(age, queued, settings.ops_worker_heartbeat_stale_seconds))
        out["worker"] = {"id": beat["worker"] if beat else None,
                         "last_heartbeat": beat["at"].isoformat() if beat else None}
    except Exception as exc:  # noqa: BLE001
        log.error("ops snapshot: heartbeat reading failed: %s", exc)
        checks.append(_check("worker_heartbeat", UNKNOWN, {"reason": type(exc).__name__}))
        out["worker"] = {"id": None, "last_heartbeat": None}

    checks.append(evaluate_pool(db.pool_stats(), settings.ops_pool_saturation_percent))
    out["slow_queries"] = db.slow_query_stats()

    for key, path in (("disk_storage", str(settings.storage_path)),
                      ("disk_backup", settings.backup_dir)):
        if not path:
            checks.append(_check(key, UNKNOWN, {"reason": "backup_dir_not_set"}))
            continue
        usage = _disk(path)
        checks.append(
            evaluate_disk(key, usage[0], usage[1], settings.ops_disk_free_min_percent)
            if usage else _check(key, UNKNOWN, {"reason": "path_not_readable"}))

    marker, reason = read_backup_marker(settings.backup_status_path)
    checks.append(evaluate_backup(marker, reason, now, settings.ops_backup_max_age_hours))

    latency = latency_window.snapshot()
    checks.append(evaluate_latency(latency, settings.ops_latency_slo_ms))
    out["latency"] = {
        "scope": "this_api_process_last_15_minutes",
        "families": latency,
    }

    out["queue"] = next(c["detail"] for c in checks if c["key"] == "queue")
    out["checks"] = checks
    out["overall"] = overall(checks)
    return out
