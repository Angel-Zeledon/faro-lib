"""
DB operations for session configuration blobs, results, and training logs.
Replaces 8+ per-session JSON files with JSONB columns in two tables.
"""

from datetime import datetime
from typing import Any, Optional

from backend.db.connection import query, query_one, execute, _json

_CONFIG_FIELDS = frozenset({
    "dataset_ref",
    "inspection",
    "columns_cfg",
    "features_cfg",
    "models_cfg",
    "validation_cfg",
    "business_cfg",
    "forecast_cfg",
})


# ── Session config blobs ───────────────────────────────────────────────────

def get_field(tenant_id: str, session_id: str, field: str) -> Optional[Any]:
    if field not in _CONFIG_FIELDS:
        raise ValueError(f"Unknown config field: {field}")
    row = query_one(
        f"SELECT {field} FROM session_configs WHERE session_id = %s AND tenant_id = %s",
        (session_id, tenant_id),
    )
    return row[field] if row else None


def set_field(tenant_id: str, session_id: str, field: str, value: Any) -> None:
    if field not in _CONFIG_FIELDS:
        raise ValueError(f"Unknown config field: {field}")
    execute(
        f"""INSERT INTO session_configs (session_id, tenant_id, {field}, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (session_id) DO UPDATE
            SET {field} = EXCLUDED.{field}, updated_at = NOW()""",
        (session_id, tenant_id, _json(value)),
    )


def clear_field(tenant_id: str, session_id: str, field: str) -> None:
    if field not in _CONFIG_FIELDS:
        raise ValueError(f"Unknown config field: {field}")
    execute(
        f"""UPDATE session_configs SET {field} = NULL, updated_at = NOW()
            WHERE session_id = %s AND tenant_id = %s""",
        (session_id, tenant_id),
    )


def get_all_configs(tenant_id: str, session_id: str) -> dict:
    row = query_one(
        "SELECT * FROM session_configs WHERE session_id = %s AND tenant_id = %s",
        (session_id, tenant_id),
    )
    return dict(row) if row else {}


# ── Session results ────────────────────────────────────────────────────────

def get_training_result(tenant_id: str, session_id: str) -> Optional[dict]:
    row = query_one(
        "SELECT training_result FROM session_results WHERE session_id = %s AND tenant_id = %s",
        (session_id, tenant_id),
    )
    return row["training_result"] if row else None


def set_training_result(tenant_id: str, session_id: str, value: dict) -> None:
    execute(
        """INSERT INTO session_results (session_id, tenant_id, training_result, updated_at)
           VALUES (%s, %s, %s, NOW())
           ON CONFLICT (session_id) DO UPDATE
           SET training_result = EXCLUDED.training_result, updated_at = NOW()""",
        (session_id, tenant_id, _json(value)),
    )


def get_forecasts(tenant_id: str, session_id: str) -> Optional[dict]:
    row = query_one(
        "SELECT forecasts FROM session_results WHERE session_id = %s AND tenant_id = %s",
        (session_id, tenant_id),
    )
    return row["forecasts"] if row else None


def set_forecasts(tenant_id: str, session_id: str, value: dict) -> None:
    execute(
        """INSERT INTO session_results (session_id, tenant_id, forecasts, updated_at)
           VALUES (%s, %s, %s, NOW())
           ON CONFLICT (session_id) DO UPDATE
           SET forecasts = EXCLUDED.forecasts, updated_at = NOW()""",
        (session_id, tenant_id, _json(value)),
    )


# ── Training logs ──────────────────────────────────────────────────────────

def append_log(tenant_id: str, session_id: str, job_id: str, message: str) -> None:
    execute(
        """INSERT INTO training_logs (tenant_id, session_id, job_id, message, logged_at)
           VALUES (%s, %s, %s, %s, NOW())""",
        (tenant_id, session_id, job_id, message),
    )


def get_logs(tenant_id: str, session_id: str, job_id: str, tail: int = 100) -> list[str]:
    rows = query(
        """SELECT message FROM training_logs
           WHERE job_id = %s
           ORDER BY logged_at DESC LIMIT %s""",
        (job_id, tail),
    )
    return [r["message"] for r in reversed(rows)]
