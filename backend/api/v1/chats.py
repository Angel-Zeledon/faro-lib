"""
Persistent AI Analyst chat endpoints.

All chats are scoped to tenant_id + user_id from the JWT.

Routes:
  GET    /analyst/chats                       — list chats (favorites first)
  POST   /analyst/chats                       — create new chat
  GET    /analyst/chats/{chat_id}             — get single chat
  PATCH  /analyst/chats/{chat_id}             — update title / favorite / sources
  DELETE /analyst/chats/{chat_id}             — delete chat + all messages
  GET    /analyst/chats/{chat_id}/messages    — paginated messages
  POST   /analyst/chats/{chat_id}/messages    — send message, get the assistant's answer
  GET    /analyst/welcome                     — first name, today's counts, suggested questions

Answers come from the one assistant core, `backend/assistant/` (channel "web").
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.auth.guards import CurrentUser, get_current_user
from backend.config import settings
from backend.db import chat_store
from backend.schemas.common import ok
from backend.sessions import service as session_svc

router = APIRouter(
    tags=["chats"],
)
log = logging.getLogger(__name__)

# The frontend proxies /api through Next, which cuts a request at ~30s. Nothing
# in application code can raise that, so every LLM call on this path has to fit
# UNDER it — otherwise the browser gets a bare `500 Internal Server Error` with
# no code and no envelope, which is exactly what it used to get: measured against
# a local Ollama with a 60s budget, the proxy gave up at 30.0s while the model
# answered successfully at 63s, an answer nobody could ever see.
PROXY_CEILING_S = 30.0
# Serialising the error and returning it also takes time; leave room for it.
_CEILING_MARGIN_S = 2.0

# A chat title is decoration; the answer is the product. Title generation runs
# FIRST on a chat's first message, so it used to spend the whole answer budget
# before anything tried to answer — the one question that decides whether a buyer
# trusts this feature. It gets a small slice and falls back to the question.
_TITLE_BUDGET_S = 5.0

# Derived, not typed: the two budgets plus the margin have to fit the window, and
# a hand-picked pair drifted out of it the first time (5 + 25 = exactly 30).
LLM_BUDGET_S = PROXY_CEILING_S - _TITLE_BUDGET_S - _CEILING_MARGIN_S

MAX_QUESTION_LENGTH = 4000
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_MESSAGES = 20

# Available data source types (for frontend display)
DATA_SOURCE_TYPES = [
    {"id": "dataset_profile",  "label": "Data Overview"},
    {"id": "model_summary",    "label": "Model Performance"},
    {"id": "sku_metrics",      "label": "SKU Accuracy"},
    {"id": "inventory",        "label": "Inventory"},
    {"id": "data_quality",     "label": "Data Quality"},
    {"id": "routing",          "label": "Model Routing"},
    {"id": "config",           "label": "Configuration"},
    {"id": "forecast_summary", "label": "Forecast Trends"},
]

def _auth_chat(tenant_id: str, chat_id: str):
    chat = chat_store.get_chat(tenant_id, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


# ── List chats ─────────────────────────────────────────────────────────────────

@router.get("/analyst/chats")
def list_chats(
    search: Optional[str] = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    chats = chat_store.list_chats(user.tenant_id, user.user_id, search=search)
    return ok(chats)


# ── Create chat ────────────────────────────────────────────────────────────────

@router.post("/analyst/chats")
def create_chat(
    body: Optional[dict] = None,
    user: CurrentUser = Depends(get_current_user),
):
    b            = body or {}
    session_id   = b.get("session_id") or None
    title        = (b.get("title") or "New Chat").strip()
    data_sources = b.get("data_sources") or []

    # Validate session belongs to this tenant if provided
    if session_id:
        s = session_svc.get_session(user.tenant_id, session_id)
        if not s:
            raise HTTPException(status_code=404, detail="Session not found")

    chat = chat_store.create_chat(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        session_id=session_id,
        title=title,
        data_sources=data_sources,
    )
    return ok(chat)


# ── Get chat ───────────────────────────────────────────────────────────────────

@router.get("/analyst/chats/{chat_id}")
def get_chat(chat_id: str, user: CurrentUser = Depends(get_current_user)):
    return ok(_auth_chat(user.tenant_id, chat_id))


# ── Update chat ────────────────────────────────────────────────────────────────

@router.patch("/analyst/chats/{chat_id}")
def update_chat(
    chat_id: str,
    body: dict,
    user: CurrentUser = Depends(get_current_user),
):
    _auth_chat(user.tenant_id, chat_id)
    updated = chat_store.update_chat(
        user.tenant_id, chat_id,
        **{k: body[k] for k in ("title", "is_favorite", "session_id", "data_sources") if k in body},
    )
    return ok(updated)


# ── Delete chat ────────────────────────────────────────────────────────────────

@router.delete("/analyst/chats/{chat_id}")
def delete_chat(chat_id: str, user: CurrentUser = Depends(get_current_user)):
    _auth_chat(user.tenant_id, chat_id)
    chat_store.delete_chat(user.tenant_id, chat_id)
    return ok({"deleted": True})


# ── Get messages ───────────────────────────────────────────────────────────────

@router.get("/analyst/chats/{chat_id}/messages")
def get_messages(
    chat_id: str,
    limit: int = Query(30, ge=1, le=100),
    before: Optional[str] = Query(None, description="Message ID — return older messages before this"),
    user: CurrentUser = Depends(get_current_user),
):
    _auth_chat(user.tenant_id, chat_id)
    messages, has_more = chat_store.get_messages(
        chat_id, user.tenant_id, limit=limit, before_id=before
    )
    return ok({"messages": messages, "has_more": has_more})


# ── Favorite messages ──────────────────────────────────────────────────────────
# Any signed-in user may star a message of THEIR OWN chats (no role needed: it
# changes nothing but their own list). A message in someone else's chat — same
# tenant or not — answers 404, exactly like one that does not exist.

@router.patch("/analyst/messages/{message_id}/star")
def star_message(
    message_id: str,
    body: dict,
    user: CurrentUser = Depends(get_current_user),
):
    starred = body.get("starred")
    if not isinstance(starred, bool):
        raise HTTPException(status_code=400, detail="'starred' must be true or false")
    msg = chat_store.set_message_star(user.tenant_id, user.user_id, message_id, starred)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    return ok(msg)


@router.get("/analyst/favorites")
def list_favorites(user: CurrentUser = Depends(get_current_user)):
    return ok(chat_store.list_starred_messages(user.tenant_id, user.user_id))


# ── Send message ───────────────────────────────────────────────────────────────

@router.post("/analyst/chats/{chat_id}/messages")
def send_message(
    chat_id: str,
    body: dict,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Send a user message and get the assistant's answer.

    Body:
      question     str   — the user's message
      language     str?  — the UI language ("es" | "en"); the answer is written in it
      sku          str?  — a product the question is about (added to the question)

    Every message goes through the one assistant core (`backend/assistant/`),
    which answers from this account's live data. `session_id` and the chat's
    `data_sources` are still accepted and stored for old clients but no longer
    steer the answer: they selected RAG chunks of ONE session, while the
    assistant reads the account the way the screens do (the active session at
    the tenant's planning grain).

    A plain `def`, not `async def`: the LLM call and the account reads block,
    and inside an async endpoint they blocked the whole event loop — every
    other request to the API waited for this chat's answer.
    """
    chat = _auth_chat(user.tenant_id, chat_id)

    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="'question' is required")
    if len(question) > MAX_QUESTION_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"'question' exceeds maximum length of {MAX_QUESTION_LENGTH} characters",
        )

    if not settings.testing_mode:
        recent = chat_store.count_recent_user_messages(user.tenant_id, RATE_LIMIT_WINDOW_SECONDS)
        if recent >= RATE_LIMIT_MAX_MESSAGES:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: max {RATE_LIMIT_MAX_MESSAGES} messages per "
                       f"{RATE_LIMIT_WINDOW_SECONDS}s per organization. Please wait and try again.",
            )

    sku      = (body.get("sku") or "").strip() or None
    language = body.get("language") if body.get("language") in ("es", "en") else None

    # The last turns, read BEFORE this message is stored so it is not doubled.
    history = chat_store.get_history_for_context(chat_id, user.tenant_id, n_turns=8)

    # ── Save user message ──────────────────────────────────────────────
    user_msg = chat_store.add_message(
        chat_id=chat_id,
        tenant_id=user.tenant_id,
        role="user",
        content=question,
    )
    chat_store.touch_chat(chat_id, user.tenant_id)

    # ── Auto-generate title on first message ──────────────────────────
    if chat.get("message_count", 0) == 0 and chat.get("title") == "New Chat":
        try:
            title = _auto_title(question)
            chat_store.update_chat(user.tenant_id, chat_id, title=title)
        except Exception as exc:
            log.warning("Auto-title failed: %s", exc)

    # ── The assistant's answer ─────────────────────────────────────────
    # Never raises for a missing key, a slow model or a failed call: the core
    # answers from the data by rules instead, and says so in the reply.
    from backend.assistant import answer as assistant_answer
    message = f"{question}\n\n(About product: {sku})" if sku else question
    reply = assistant_answer(
        user.tenant_id, user.user_id, "web", message, history,
        role=user.role, language=language, budget_s=LLM_BUDGET_S,
    )
    # `source` is what the screen labels the bubble with: "assistant" for a
    # model answer whose every figure was verified, "assistant_unverified" when
    # the reply carries the guard's warning, "rules" when no model answered.
    source = "rules" if reply.source == "rules" else (
        "assistant" if reply.grounded else "assistant_unverified")

    # ── Save AI message ────────────────────────────────────────────────
    ai_msg = chat_store.add_message(
        chat_id=chat_id,
        tenant_id=user.tenant_id,
        role="assistant",
        content=reply.text,
        source=source,
        retrieved_count=len(reply.tools_used) or None,
    )
    chat_store.touch_chat(chat_id, user.tenant_id)

    return ok({"user_message": user_msg, "ai_message": ai_msg,
               "assistant": {"source": reply.source, "reason": reply.reason,
                             "grounded": reply.grounded, "unverified": reply.unverified,
                             "tools_used": reply.tools_used}})


# ── The assistant's opening ────────────────────────────────────────────────────

@router.get("/analyst/welcome")
def assistant_welcome(user: CurrentUser = Depends(get_current_user)):
    """First name, company, today's counts and suggested questions built from
    this account's own top risks (codes + params; the frontend writes them)."""
    from backend.assistant.welcome import build_welcome
    return ok(build_welcome(user))


# ── Data source types reference ────────────────────────────────────────────────

@router.get("/analyst/data-source-types")
def get_data_source_types(_: CurrentUser = Depends(get_current_user)):
    return ok(DATA_SOURCE_TYPES)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _auto_title(question: str) -> str:
    """Use the local LLM to generate a short 4-6 word chat title."""
    try:
        from backend.ai.local_llm import get_local_llm_client
        client = get_local_llm_client(timeout=_TITLE_BUDGET_S)
        msg = client.messages.create(
            max_tokens=30,
            messages=[{
                "role": "user",
                "content": (
                    f"Generate a 3-5 word title for a chat that starts with this question. "
                    f"Output ONLY the title, no quotes, no punctuation at the end.\n\n"
                    f"Question: {question[:300]}"
                ),
            }],
        )
        return msg.content[0].text.strip() or question[:40].strip()
    except Exception:
        return question[:40].strip()
