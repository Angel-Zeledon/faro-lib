"""
CRUD operations for persistent AI Analyst chats and messages.

Schema:
  chats         — one row per conversation (title, favorite, session link, sources filter)
  chat_messages — individual messages within a chat, ordered by created_at ASC
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.db.connection import query, query_one, execute


# ── Chats ──────────────────────────────────────────────────────────────────────

def list_chats(
    tenant_id: str,
    user_id: str,
    search: Optional[str] = None,
    limit: int = 60,
) -> list[dict]:
    """
    Return chats for this user sorted favorites-first then by last_message_at DESC.
    Includes a `last_message_preview` snippet from the most recent message.
    """
    search_clause = ""
    params: list = [tenant_id, user_id]
    if search:
        search_clause = " AND c.title ILIKE %s"
        params.append(f"%{search}%")
    params.append(limit)

    rows = query(
        f"""
        SELECT
          c.id, c.title, c.is_favorite, c.session_id, c.data_sources,
          c.last_message_at, c.message_count, c.created_at,
          (
            SELECT cm.content
            FROM chat_messages cm
            WHERE cm.chat_id = c.id
            ORDER BY cm.created_at DESC
            LIMIT 1
          ) AS last_message_preview
        FROM chats c
        WHERE c.tenant_id = %s AND c.user_id = %s
          {search_clause}
        ORDER BY c.is_favorite DESC, c.last_message_at DESC
        LIMIT %s
        """,
        tuple(params),
    )
    return rows


def create_chat(
    tenant_id: str,
    user_id: str,
    session_id: Optional[str] = None,
    title: str = "New Chat",
    data_sources: Optional[list[str]] = None,
) -> dict:
    rows = query(
        """
        INSERT INTO chats (tenant_id, user_id, session_id, title, data_sources)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING *
        """,
        (tenant_id, user_id, session_id, title, data_sources or []),
    )
    return rows[0]


def get_chat(tenant_id: str, chat_id: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM chats WHERE id = %s AND tenant_id = %s",
        (chat_id, tenant_id),
    )


def update_chat(tenant_id: str, chat_id: str, **fields) -> Optional[dict]:
    """Update title, is_favorite, session_id, or data_sources."""
    allowed = {"title", "is_favorite", "session_id", "data_sources"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_chat(tenant_id, chat_id)

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [chat_id, tenant_id]
    rows = query(
        f"UPDATE chats SET {set_clause} WHERE id = %s AND tenant_id = %s RETURNING *",
        tuple(values),
    )
    return rows[0] if rows else None


def delete_chat(tenant_id: str, chat_id: str) -> None:
    execute(
        "DELETE FROM chats WHERE id = %s AND tenant_id = %s",
        (chat_id, tenant_id),
    )


def touch_chat(chat_id: str, tenant_id: str) -> None:
    """Update last_message_at and increment message_count."""
    execute(
        """
        UPDATE chats
        SET last_message_at = NOW(),
            message_count   = message_count + 1
        WHERE id = %s AND tenant_id = %s
        """,
        (chat_id, tenant_id),
    )


# ── Messages ───────────────────────────────────────────────────────────────────

def get_messages(
    chat_id: str,
    tenant_id: str,
    limit: int = 30,
    before_id: Optional[str] = None,
) -> tuple[list[dict], bool]:
    """
    Return up to `limit` messages for this chat, ordered oldest-first (ASC).
    If `before_id` is given, return messages older than that message (for
    infinite-scroll pagination from the top of the chat).

    Returns (messages, has_more) where has_more=True means older messages exist.
    """
    if before_id:
        anchor = query_one(
            "SELECT created_at FROM chat_messages WHERE id = %s AND chat_id = %s",
            (before_id, chat_id),
        )
        if anchor:
            rows = query(
                """
                SELECT * FROM chat_messages
                WHERE chat_id = %s AND tenant_id = %s
                  AND created_at < %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (chat_id, tenant_id, anchor["created_at"], limit + 1),
            )
            has_more = len(rows) > limit
            return list(reversed(rows[:limit])), has_more

    # Initial load: newest `limit` messages
    rows = query(
        """
        SELECT * FROM chat_messages
        WHERE chat_id = %s AND tenant_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (chat_id, tenant_id, limit + 1),
    )
    has_more = len(rows) > limit
    # Reverse so oldest appears first in the list
    return list(reversed(rows[:limit])), has_more


def add_message(
    chat_id: str,
    tenant_id: str,
    role: str,
    content: str,
    source: Optional[str] = None,
    retrieved_count: Optional[int] = None,
) -> dict:
    rows = query(
        """
        INSERT INTO chat_messages (chat_id, tenant_id, role, content, source, retrieved_count)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (chat_id, tenant_id, role, content, source, retrieved_count),
    )
    return rows[0]


def count_recent_user_messages(tenant_id: str, seconds: int = 60) -> int:
    """
    Count user messages sent by this tenant in the last `seconds` — used for rate limiting.

    The cutoff is computed in Python (not via `NOW()` in SQL) — under Supabase's
    transaction-mode pooler, a server-side `NOW()` expression can get bound to a
    stale value when the underlying connection is reused, causing the count to
    plateau instead of tracking real time.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    row = query_one(
        "SELECT COUNT(*) AS cnt FROM chat_messages WHERE tenant_id = %s AND role = 'user' AND created_at > %s",
        (tenant_id, cutoff),
    )
    return row["cnt"] if row else 0


def get_history_for_context(chat_id: str, tenant_id: str, n_turns: int = 6) -> list[dict]:
    """Return last n_turns messages for passing as history to the LLM."""
    rows = query(
        """
        SELECT role, content FROM chat_messages
        WHERE chat_id = %s AND tenant_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (chat_id, tenant_id, n_turns),
    )
    return list(reversed(rows))
