"""Team messaging — 1-to-1 direct messages between users of the same tenant.

Professional-plan feature (Feature.TEAM_MESSAGING gates the whole router).
Viewers can chat too: messaging is communication, not a business-data
mutation, so the security boundary here is the tenant, not the role.
"""

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.auth.guards import CurrentUser, get_current_user
from backend.config import settings
from backend.db.connection import execute, query, query_one
from backend.entitlements.guards import require_feature
from backend.entitlements.plans import Feature
from backend.notifications import sms, whatsapp
from backend.notifications.locale import render_es
from backend.preferences import service as pref_svc
from backend.schemas.common import ok

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/messages",
    tags=["messages"],
    dependencies=[Depends(require_feature(Feature.TEAM_MESSAGING))],
)

_MAX_BODY_LEN = 4000
_THREAD_PAGE_SIZE = 50

# A new message triggers an SMS only when the recipient is "away" from the
# conversation: no older unread message from this sender (the first unread
# already earned its SMS) and no message from this sender read within the
# last few minutes (recently-read = actively chatting). Both in one query.
_RECENTLY_ACTIVE_MINUTES = 10


class MessageSend(BaseModel):
    recipient_id: str
    body: str = Field(min_length=1, max_length=_MAX_BODY_LEN)


class MarkRead(BaseModel):
    with_user: str


def _get_counterpart(tenant_id: str, user_id: str) -> Optional[dict]:
    return query_one(
        "SELECT id, full_name, email, status, whatsapp_number FROM users "
        "WHERE id = %s AND tenant_id = %s",
        (user_id, tenant_id),
    )


@router.get("/contacts")
def list_contacts(user: CurrentUser = Depends(get_current_user)):
    """Active users of the tenant a conversation can be started with."""
    rows = query(
        "SELECT id, full_name, email, role FROM users "
        "WHERE tenant_id = %s AND status = 'active' AND id <> %s "
        "ORDER BY full_name NULLS LAST, email",
        (user.tenant_id, user.user_id),
    )
    return ok(rows)


@router.get("/conversations")
def list_conversations(user: CurrentUser = Depends(get_current_user)):
    rows = query(
        """
        WITH mine AS (
            SELECT id, sender_id, body, created_at,
                   CASE WHEN sender_id = %s THEN recipient_id ELSE sender_id END
                       AS counterpart_id
            FROM direct_messages
            WHERE tenant_id = %s AND (sender_id = %s OR recipient_id = %s)
        ), last AS (
            SELECT DISTINCT ON (counterpart_id)
                   counterpart_id, id, sender_id, body, created_at
            FROM mine ORDER BY counterpart_id, id DESC
        ), unread AS (
            SELECT sender_id AS counterpart_id, COUNT(*) AS unread_count
            FROM direct_messages
            WHERE tenant_id = %s AND recipient_id = %s AND read_at IS NULL
            GROUP BY sender_id
        )
        SELECT l.counterpart_id, u.full_name, u.email,
               l.body AS last_body, l.created_at AS last_at,
               (l.sender_id = %s) AS last_is_mine,
               COALESCE(un.unread_count, 0) AS unread_count
        FROM last l
        JOIN users u ON u.id = l.counterpart_id
        LEFT JOIN unread un ON un.counterpart_id = l.counterpart_id
        ORDER BY l.id DESC
        """,
        (user.user_id, user.tenant_id, user.user_id, user.user_id,
         user.tenant_id, user.user_id, user.user_id),
    )
    return ok(rows)


@router.get("/unread-count")
def unread_count(user: CurrentUser = Depends(get_current_user)):
    row = query_one(
        "SELECT COUNT(*) AS n FROM direct_messages "
        "WHERE tenant_id = %s AND recipient_id = %s AND read_at IS NULL",
        (user.tenant_id, user.user_id),
    )
    return ok({"unread": int(row["n"]) if row else 0})


@router.get("/thread")
def get_thread(
    with_user: str = Query(...),
    before: Optional[int] = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    counterpart = _get_counterpart(user.tenant_id, with_user)
    if not counterpart:
        raise HTTPException(404, "User not found")

    params: list = [user.tenant_id, user.user_id, with_user, with_user, user.user_id]
    before_sql = ""
    if before is not None:
        before_sql = "AND id < %s "
        params.append(before)
    params.append(_THREAD_PAGE_SIZE)

    rows = query(
        "SELECT id, sender_id, recipient_id, body, read_at, created_at "
        "FROM direct_messages "
        "WHERE tenant_id = %s "
        "AND ((sender_id = %s AND recipient_id = %s) "
        "  OR (sender_id = %s AND recipient_id = %s)) "
        f"{before_sql}"
        "ORDER BY id DESC LIMIT %s",
        tuple(params),
    )
    rows.reverse()  # oldest first, the order a chat renders in
    return ok({
        "counterpart": {
            "id": counterpart["id"],
            "full_name": counterpart["full_name"],
            "email": counterpart["email"],
        },
        "messages": rows,
        "has_more": len(rows) == _THREAD_PAGE_SIZE,
    })


@router.post("")
def send_message(
    body: MessageSend,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
):
    text = body.body.strip()
    if not text:
        raise HTTPException(400, "Message body is empty")
    if body.recipient_id == user.user_id:
        raise HTTPException(400, "Cannot send a message to yourself")

    recipient = _get_counterpart(user.tenant_id, body.recipient_id)
    if not recipient:
        raise HTTPException(404, "User not found")
    if recipient["status"] != "active":
        raise HTTPException(400, "Recipient is not active")

    row = query_one(
        "INSERT INTO direct_messages (tenant_id, sender_id, recipient_id, body) "
        "VALUES (%s, %s, %s, %s) RETURNING id, created_at",
        (user.tenant_id, user.user_id, body.recipient_id, text),
    )

    # Background: WhatsApp send + delivery confirmation waits ~8s for Twilio's
    # verdict, which must never sit inside the request.
    background_tasks.add_task(_maybe_send_heads_up, user, recipient, row["id"])

    return ok({
        "id": row["id"],
        "sender_id": user.user_id,
        "recipient_id": body.recipient_id,
        "body": text,
        "created_at": row["created_at"],
    })


@router.post("/read")
def mark_read(body: MarkRead, user: CurrentUser = Depends(get_current_user)):
    execute(
        "UPDATE direct_messages SET read_at = NOW() "
        "WHERE tenant_id = %s AND recipient_id = %s AND sender_id = %s "
        "AND read_at IS NULL",
        (user.tenant_id, user.user_id, body.with_user),
    )
    return ok({"read": True})


def _maybe_send_heads_up(sender: CurrentUser, recipient: dict, new_message_id: int) -> None:
    """Fire the opt-in new-message heads-up: WhatsApp first (the reliable,
    near-free channel in LatAm), SMS as fallback when WhatsApp is not
    configured or the send fails. Never raises — the message is already
    committed and a notification failure must not turn the send into a 500."""
    try:
        prefs = pref_svc.get_preferences(sender.tenant_id, recipient["id"])
        if not prefs.get("dm_sms_enabled") or not recipient.get("whatsapp_number"):
            return

        away_blocker = query_one(
            "SELECT 1 AS x FROM direct_messages "
            "WHERE tenant_id = %s AND sender_id = %s AND recipient_id = %s "
            "AND id < %s "
            "AND (read_at IS NULL OR read_at > NOW() - INTERVAL "
            f"'{_RECENTLY_ACTIVE_MINUTES} minutes') LIMIT 1",
            (sender.tenant_id, sender.user_id, recipient["id"], new_message_id),
        )
        if away_blocker:
            return

        sender_row = query_one(
            "SELECT full_name, email FROM users WHERE id = %s",
            (sender.user_id,),
        ) or {}
        sender_name = sender_row.get("full_name") or sender_row.get("email") or ""
        text = render_es(
            "dm_new_message_heads_up",
            sender=sender_name,
            url=f"{settings.frontend_url.rstrip('/')}/mensajes",
        )
        number = recipient["whatsapp_number"]
        if not whatsapp.send_whatsapp_and_confirm(number, text):
            sms.send_sms(number, text)
    except Exception as exc:
        log.error("DM heads-up notification failed (message %s): %s", new_message_id, exc)
