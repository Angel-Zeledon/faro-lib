from backend.db.connection import query, query_one, execute, _json
from backend.utils.ids import generate_id


def ensure_table() -> None:
    execute("""
        CREATE TABLE IF NOT EXISTS activity_logs (
            id         TEXT PRIMARY KEY,
            tenant_id  TEXT NOT NULL,
            user_id    TEXT NOT NULL,
            action     TEXT NOT NULL,
            resource   TEXT,
            context    JSONB DEFAULT '{}',
            status     TEXT NOT NULL DEFAULT 'success',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    execute(
        "CREATE INDEX IF NOT EXISTS idx_activity_user_time ON activity_logs(user_id, created_at DESC)"
    )


def log_action(
    tenant_id: str,
    user_id:   str,
    action:    str,
    resource:  str  | None = None,
    context:   dict | None = None,
    status:    str         = "success",
) -> None:
    execute(
        """INSERT INTO activity_logs (id, tenant_id, user_id, action, resource, context, status, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())""",
        (generate_id("act"), tenant_id, user_id, action, resource, _json(context or {}), status),
    )


def get_logs(
    tenant_id:     str,
    user_id:       str,
    limit:         int         = 50,
    offset:        int         = 0,
    action_filter: str | None  = None,
) -> dict:
    base   = "FROM activity_logs WHERE tenant_id = %s AND user_id = %s"
    params = [tenant_id, user_id]

    if action_filter:
        base   += " AND action = %s"
        params.append(action_filter)

    rows = query(
        f"SELECT id, action, resource, context, status, created_at {base}"
        f" ORDER BY created_at DESC LIMIT %s OFFSET %s",
        tuple(params) + (limit, offset),
    )
    total_row = query_one(f"SELECT COUNT(*) AS n {base}", tuple(params))
    return {
        "items": [dict(r) for r in rows],
        "total": int(total_row["n"]) if total_row else 0,
    }


def get_action_types(tenant_id: str, user_id: str) -> list[str]:
    rows = query(
        "SELECT DISTINCT action FROM activity_logs WHERE tenant_id = %s AND user_id = %s ORDER BY action",
        (tenant_id, user_id),
    )
    return [r["action"] for r in rows]
