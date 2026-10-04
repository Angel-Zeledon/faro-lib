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


def list_sessions(tenant_id: str, skip: int = 0, limit: int = 50,
                  archived: str = "active") -> list[dict]:
    rows = query(
        f"SELECT * FROM sessions WHERE tenant_id = %s{_archived_clause(archived)} "
        "ORDER BY updated_at DESC LIMIT %s OFFSET %s",
        (tenant_id, limit, skip),
    )
    return [_fmt(r) for r in rows]


def _archived_clause(archived: str, alias: str = "") -> str:
    """SQL fragment for the library scope: active (default), archived, or all."""
    col = f"{alias}archived_at"
    if archived == "archived":
        return f" AND {col} IS NOT NULL"
    if archived == "all":
        return ""
    return f" AND {col} IS NULL"


# Headline accuracy of a run, from the metric rows stored with its result:
# 1 - the mean over SKUs of the best non-baseline model's WAPE. Naive baselines
# are excluded for the same reason `compute_session_accuracy` excludes them.
_ACCURACY_SQL = """
    (SELECT 1 - AVG(best) FROM (
         SELECT MIN((e->>'wape')::numeric) AS best
         FROM jsonb_array_elements(r.training_result->'metrics'->'rows') AS e
         WHERE COALESCE(e->>'type', '') <> 'baseline'
           AND (e->>'wape') ~ '^-?[0-9]+([.][0-9]+)?([eE][-+]?[0-9]+)?$'
         GROUP BY e->>'sku') per_sku)
"""
_MODELS_SQL = """
    (SELECT ARRAY_AGG(DISTINCT e->>'model')
     FROM jsonb_array_elements(r.training_result->'metrics'->'rows') AS e
     WHERE COALESCE(e->>'type', '') <> 'baseline' AND e->>'model' IS NOT NULL)
"""
_HAS_ROWS = "jsonb_typeof(r.training_result->'metrics'->'rows') = 'array'"

SORT_COLUMNS = {
    "created_at": "s.created_at",
    "updated_at": "s.updated_at",
    "name": "LOWER(s.name)",
    "status": "s.status",
    "horizon": "horizon_value",
    "accuracy": "accuracy_value",
}


def _like_pattern(text: str) -> str:
    """A %text% pattern with the user's own %, _ and backslash taken literally."""
    escaped = text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _library_filters(
    tenant_id: str, *, q: Optional[str], status: Optional[list[str]],
    dataset_id: Optional[str], archived: str,
    created_from: Optional[str], created_to: Optional[str],
) -> tuple[str, list]:
    where = "s.tenant_id = %s" + _archived_clause(archived, "s.")
    params: list = [tenant_id]
    if status:
        where += " AND s.status = ANY(%s)"
        params.append(list(status))
    if dataset_id:
        where += " AND s.dataset_id = %s"
        params.append(dataset_id)
    if created_from:
        where += " AND s.created_at >= %s::date"
        params.append(created_from)
    if created_to:
        where += " AND s.created_at < (%s::date + 1)"
        params.append(created_to)
    if q and q.strip():
        like = _like_pattern(q)
        where += (" AND (s.name ILIKE %s OR COALESCE(s.description, '') ILIKE %s"
                  " OR COALESCE(d.name, '') ILIKE %s OR COALESCE(d.original_filename, '') ILIKE %s)")
        params += [like, like, like, like]
    return where, params


def list_session_summaries(
    tenant_id: str, skip: int = 0, limit: int = 50, *,
    q: Optional[str] = None, status: Optional[list[str]] = None,
    dataset_id: Optional[str] = None, archived: str = "active",
    created_from: Optional[str] = None, created_to: Optional[str] = None,
    sort: str = "created_at", order: str = "desc",
) -> tuple[list[dict], int]:
    """
    The sessions library: every session of the tenant (nothing expires), with
    search, filters, sorting and pagination done in SQL so hundreds of runs stay
    cheap. Returns ``(page, total_matching)``.

    Two steps on purpose. Step 1 finds the page of ids (computing the accuracy
    only when the sort needs it); step 2 enriches just those ids with the
    dataset, config blobs and the training result. JSONB extraction happens in
    SQL so the (large) training_result blob never crosses the wire.

    Extracted fields:
      dataset_name / dataset_filename — from the datasets table
      horizon      — forecast_cfg.horizon (falls back to validation_cfg.horizon)
      granularity  — sessions.granularity (family runs), else granularity_cfg.target_freq
      sku_count    — training_result.metrics.n_skus, else distinct SKUs in metrics rows
      accuracy     — 1 - mean best-model WAPE; models — the non-baseline models trained
    """
    sort_col = SORT_COLUMNS.get(sort, SORT_COLUMNS["created_at"])
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    where, params = _library_filters(
        tenant_id, q=q, status=status, dataset_id=dataset_id, archived=archived,
        created_from=created_from, created_to=created_to)

    joins = ("FROM sessions s "
             "LEFT JOIN datasets d ON d.id = s.dataset_id AND d.tenant_id = s.tenant_id")
    total_row = query_one(f"SELECT COUNT(*) AS cnt {joins} WHERE {where}", tuple(params))
    total = total_row["cnt"] if total_row else 0

    extra_select, extra_join = "", ""
    if sort == "accuracy":
        extra_join = ("LEFT JOIN session_results r ON r.session_id = s.id "
                      f"LEFT JOIN LATERAL (SELECT CASE WHEN {_HAS_ROWS} THEN {_ACCURACY_SQL} END AS v) a ON TRUE ")
        extra_select = ", a.v AS accuracy_value"
    elif sort == "horizon":
        extra_join = "LEFT JOIN session_configs c ON c.session_id = s.id "
        extra_select = (", COALESCE((c.forecast_cfg->>'horizon')::numeric::int,"
                        " (c.validation_cfg->>'horizon')::numeric::int) AS horizon_value")
    id_rows = query(
        f"SELECT s.id{extra_select} {joins} {extra_join}WHERE {where} "
        f"ORDER BY {sort_col} {direction} NULLS LAST, s.created_at DESC, s.id "
        "LIMIT %s OFFSET %s",
        (*params, limit, skip),
    )
    ids = [r["id"] for r in id_rows]
    if not ids:
        return [], total

    rows = query(
        f"""SELECT s.id, s.name, s.description, s.status, s.pipeline_step,
                  s.created_at, s.updated_at, s.dataset_id, s.tags,
                  s.archived_at, s.is_backtest, s.backtest_source_dataset_id,
                  s.backtest_holdout_periods,
                  d.name AS dataset_name,
                  d.original_filename AS dataset_filename,
                  COALESCE((c.forecast_cfg->>'horizon')::numeric::int,
                           (c.validation_cfg->>'horizon')::numeric::int) AS horizon,
                  COALESCE(s.granularity, c.granularity_cfg->>'target_freq') AS granularity,
                  COALESCE(
                      (r.training_result->'metrics'->>'n_skus')::numeric::int,
                      CASE WHEN {_HAS_ROWS}
                           THEN (SELECT COUNT(DISTINCT elem->>'sku')
                                 FROM jsonb_array_elements(r.training_result->'metrics'->'rows') AS elem)::int
                      END
                  ) AS sku_count,
                  CASE WHEN {_HAS_ROWS} THEN {_ACCURACY_SQL} END AS accuracy,
                  CASE WHEN {_HAS_ROWS} THEN {_MODELS_SQL} END AS models,
                  j.error AS failure_reason
           FROM sessions s
           LEFT JOIN datasets d        ON d.id = s.dataset_id AND d.tenant_id = s.tenant_id
           LEFT JOIN session_configs c ON c.session_id = s.id
           LEFT JOIN session_results r ON r.session_id = s.id
           -- Why a run failed. It was already in jobs.error and the history
           -- screen showed a bare "Fallida", so the buyer whose 20-minute run
           -- died had nothing to act on: not the reason, not whether retrying
           -- would help. Latest job wins — a session can be re-queued.
           LEFT JOIN LATERAL (
               SELECT error FROM jobs
               WHERE session_id = s.id AND error IS NOT NULL
               ORDER BY created_at DESC LIMIT 1
           ) j ON TRUE
           WHERE s.tenant_id = %s AND s.id = ANY(%s)""",
        (tenant_id, ids),
    )
    by_id = {r["id"]: r for r in rows}
    out = []
    for sid in ids:
        r = by_id.get(sid)
        if r is None:
            continue
        if r.get("accuracy") is not None:
            r["accuracy"] = float(r["accuracy"])
        r["models"] = sorted(r["models"]) if r.get("models") else []
        out.append(_fmt(r))
    return out, total


def count_sessions(tenant_id: str, conn=None, archived: str = "active") -> int:
    """`conn` lets a caller count inside a `limit_guard` transaction, which is
    what makes a ceiling hold against two writers at once.

    By default this counts ACTIVE sessions only: it is what the `max_sessions`
    plan ceiling compares against. An archived session is kept forever but is
    out of the working list, exactly as a deleted one used to be — so archiving
    frees a slot, and the ceiling only ever blocks creating a new session; it
    never removes one."""
    row = query_one(
        f"SELECT COUNT(*) AS cnt FROM sessions WHERE tenant_id = %s{_archived_clause(archived)}",
        (tenant_id,), conn=conn,
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


def archive_session(tenant_id: str, session_id: str, user_id: Optional[str]) -> Optional[dict]:
    """Take a session out of the working list WITHOUT erasing anything.

    The session row, its configs, its results and its artifacts stay exactly as
    they were; ``restore_session`` puts it back. This replaced the hard delete:
    a forecast somebody trained is a record of what the product said on a given
    day, and "I deleted it by mistake" must never be unrecoverable.
    """
    execute(
        "UPDATE sessions SET archived_at = NOW(), archived_by = %s, updated_at = NOW() "
        "WHERE id = %s AND tenant_id = %s AND archived_at IS NULL",
        (user_id, session_id, tenant_id),
    )
    return get_session(tenant_id, session_id)


def restore_session(tenant_id: str, session_id: str) -> Optional[dict]:
    execute(
        "UPDATE sessions SET archived_at = NULL, archived_by = NULL, updated_at = NOW() "
        "WHERE id = %s AND tenant_id = %s",
        (session_id, tenant_id),
    )
    return get_session(tenant_id, session_id)
