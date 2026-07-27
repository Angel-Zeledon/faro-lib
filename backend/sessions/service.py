from datetime import datetime, timezone
from typing import Optional

from backend.sessions.state_machine import assert_transition
from backend.db.connection import query_one, query, execute, _json
from backend.utils.ids import generate_id


def _fmt(row: Optional[dict]) -> Optional[dict]:
    """Add session_id alias for id — frontend uses session_id throughout."""
    if not row:
        return row
    return {**row, "session_id": row["id"]}


def create_session(
    tenant_id: str,
    user_id: str,
    name: str,
    description: Optional[str] = None,
    tags: Optional[list] = None,
) -> dict:
    session_id = generate_id("sess")
    execute(
        """INSERT INTO sessions
           (id, tenant_id, name, description, status, pipeline_step,
            created_by, created_at, updated_at, tags, version)
           VALUES (%s, %s, %s, %s, 'DRAFT', 'upload', %s, NOW(), NOW(), %s, 1)""",
        (session_id, tenant_id, name, description, user_id, _json(tags or [])),
    )
    execute(
        "INSERT INTO session_configs (session_id, tenant_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (session_id, tenant_id),
    )
    return get_session(tenant_id, session_id)


def get_session(tenant_id: str, session_id: str) -> Optional[dict]:
    return _fmt(query_one(
        "SELECT * FROM sessions WHERE id = %s AND tenant_id = %s",
        (session_id, tenant_id),
    ))


def list_sessions(tenant_id: str, skip: int = 0, limit: int = 50) -> list[dict]:
    rows = query(
        "SELECT * FROM sessions WHERE tenant_id = %s ORDER BY updated_at DESC LIMIT %s OFFSET %s",
        (tenant_id, limit, skip),
    )
    return [_fmt(r) for r in rows]


def list_session_summaries(tenant_id: str, skip: int = 0, limit: int = 50) -> list[dict]:
    """
    History view: one batched query per page — sessions joined with their
    dataset, config blobs and training result so the endpoint never does an
    N+1 per-session lookup. JSONB extraction happens in SQL so the (large)
    training_result blob never crosses the wire.

    Extracted fields:
      dataset_name / dataset_filename — from the datasets table
      horizon      — forecast_cfg.horizon (falls back to validation_cfg.horizon)
      granularity  — sessions.granularity (family runs), else granularity_cfg.target_freq
      sku_count    — training_result.metrics.n_skus, else distinct SKUs in metrics rows
    """
    rows = query(
        """SELECT s.id, s.name, s.description, s.status, s.pipeline_step,
                  s.created_at, s.updated_at, s.dataset_id, s.tags,
                  d.name AS dataset_name,
                  d.original_filename AS dataset_filename,
                  COALESCE((c.forecast_cfg->>'horizon')::numeric::int,
                           (c.validation_cfg->>'horizon')::numeric::int) AS horizon,
                  COALESCE(s.granularity, c.granularity_cfg->>'target_freq') AS granularity,
                  COALESCE(
                      (r.training_result->'metrics'->>'n_skus')::numeric::int,
                      CASE WHEN jsonb_typeof(r.training_result->'metrics'->'rows') = 'array'
                           THEN (SELECT COUNT(DISTINCT elem->>'sku')
                                 FROM jsonb_array_elements(r.training_result->'metrics'->'rows') AS elem)::int
                      END
                  ) AS sku_count
           FROM sessions s
           LEFT JOIN datasets d        ON d.id = s.dataset_id AND d.tenant_id = s.tenant_id
           LEFT JOIN session_configs c ON c.session_id = s.id
           LEFT JOIN session_results r ON r.session_id = s.id
           WHERE s.tenant_id = %s
           ORDER BY s.created_at DESC
           LIMIT %s OFFSET %s""",
        (tenant_id, limit, skip),
    )
    return [_fmt(r) for r in rows]


def count_sessions(tenant_id: str) -> int:
    row = query_one(
        "SELECT COUNT(*) AS cnt FROM sessions WHERE tenant_id = %s",
        (tenant_id,),
    )
    return row["cnt"] if row else 0


def transition(
    tenant_id: str,
    session_id: str,
    new_status: str,
    pipeline_step: Optional[str] = None,
) -> dict:
    s = get_session(tenant_id, session_id)
    if not s:
        raise ValueError(f"Session {session_id} not found")
    assert_transition(s["status"], new_status)
    if pipeline_step:
        execute(
            """UPDATE sessions SET status = %s, pipeline_step = %s,
               updated_at = NOW(), version = version + 1
               WHERE id = %s AND tenant_id = %s""",
            (new_status, pipeline_step, session_id, tenant_id),
        )
    else:
        execute(
            """UPDATE sessions SET status = %s, updated_at = NOW(), version = version + 1
               WHERE id = %s AND tenant_id = %s""",
            (new_status, session_id, tenant_id),
        )
    return get_session(tenant_id, session_id)


def force_status(
    tenant_id: str,
    session_id: str,
    new_status: str,
    pipeline_step: Optional[str] = None,
) -> dict:
    if pipeline_step:
        execute(
            """UPDATE sessions SET status = %s, pipeline_step = %s,
               updated_at = NOW(), version = version + 1
               WHERE id = %s AND tenant_id = %s""",
            (new_status, pipeline_step, session_id, tenant_id),
        )
    else:
        execute(
            """UPDATE sessions SET status = %s, updated_at = NOW(), version = version + 1
               WHERE id = %s AND tenant_id = %s""",
            (new_status, session_id, tenant_id),
        )
    return get_session(tenant_id, session_id)


def attach_dataset(tenant_id: str, session_id: str, dataset_id: str) -> dict:
    execute(
        "UPDATE sessions SET dataset_id = %s, updated_at = NOW() WHERE id = %s AND tenant_id = %s",
        (dataset_id, session_id, tenant_id),
    )
    from backend.db import session_store
    session_store.set_field(tenant_id, session_id, "dataset_ref", {
        "dataset_id": dataset_id,
        "attached_at": datetime.now(timezone.utc).isoformat(),
    })
    return get_session(tenant_id, session_id)


def set_last_job(tenant_id: str, session_id: str, job_id: str) -> None:
    execute(
        "UPDATE sessions SET last_job_id = %s, updated_at = NOW() WHERE id = %s AND tenant_id = %s",
        (job_id, session_id, tenant_id),
    )


def delete_session(tenant_id: str, session_id: str) -> None:
    # CASCADE deletes session_configs, session_results, jobs, training_logs
    execute(
        "DELETE FROM sessions WHERE id = %s AND tenant_id = %s",
        (session_id, tenant_id),
    )
    # Clean up binary artifact/log files
    import shutil
    from backend.storage import paths
    for d in [paths.artifacts_dir(tenant_id, session_id), paths.logs_dir(tenant_id, session_id)]:
        if d.exists():
            shutil.rmtree(d)
