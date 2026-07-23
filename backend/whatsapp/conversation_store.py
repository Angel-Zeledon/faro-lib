"""
Per-user WhatsApp conversation state: bounded recent turns plus at most one
pending write action. One row per (tenant, user). Rows older than the 24h
session window are treated as empty on read (pruned), matching WhatsApp's
24-hour in-session reply window.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from backend.db.connection import execute, query_one

SESSION_WINDOW_HOURS = 24
MAX_TURNS = 12


def _row(tenant_id: str, user_id: str) -> Optional[dict]:
    return query_one(
        """SELECT history, pending_action, last_message_sid,
                  (updated_at < NOW() - make_interval(hours => %s)) AS stale
           FROM whatsapp_conversations
           WHERE tenant_id = %s AND user_id = %s""",
        (SESSION_WINDOW_HOURS, tenant_id, user_id),
    )


def _as_obj(value: Any) -> Any:
    # psycopg2 returns JSONB as already-parsed Python; be defensive if a str slips through.
    if isinstance(value, str):
        return json.loads(value)
    return value


def load(tenant_id: str, user_id: str) -> dict:
    row = _row(tenant_id, user_id)
    if not row or row["stale"]:
        return {"history": [], "pending_action": None, "last_message_sid": None, "exists": False}
    return {
        "history": _as_obj(row["history"]) or [],
        "pending_action": _as_obj(row["pending_action"]),
        "last_message_sid": row["last_message_sid"],
        "exists": True,
    }


def is_duplicate(tenant_id: str, user_id: str, message_sid: str) -> bool:
    row = _row(tenant_id, user_id)
    if not row or row["stale"]:
        return False
    return bool(message_sid) and row["last_message_sid"] == message_sid


def save(
    tenant_id: str,
    user_id: str,
    phone: str,
    history: list[dict],
    pending_action: Optional[dict],
    last_message_sid: Optional[str],
) -> None:
    trimmed = (history or [])[-MAX_TURNS:]
    execute(
        """INSERT INTO whatsapp_conversations
               (tenant_id, user_id, phone, history, pending_action, last_message_sid, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, NOW())
           ON CONFLICT (tenant_id, user_id) DO UPDATE
           SET phone = EXCLUDED.phone,
               history = EXCLUDED.history,
               pending_action = EXCLUDED.pending_action,
               last_message_sid = EXCLUDED.last_message_sid,
               updated_at = NOW()""",
        (tenant_id, user_id, phone, json.dumps(trimmed),
         json.dumps(pending_action) if pending_action is not None else None,
         last_message_sid),
    )
