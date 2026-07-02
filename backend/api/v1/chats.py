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
  POST   /analyst/chats/{chat_id}/messages    — send message, get AI response
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.ai import rag
from backend.auth.guards import CurrentUser, get_current_user
from backend.config import settings
from backend.db import chat_store
from backend.schemas.common import ok
from backend.sessions import service as session_svc

router = APIRouter(tags=["chats"])
log = logging.getLogger(__name__)

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

_GENERAL_SYSTEM = """\
You are an AI analyst assistant for Forecasting CR, an enterprise SaaS platform \
for demand forecasting and supply chain intelligence built for logistics companies.

Help users with:
- Understanding forecasting concepts (MAE, RMSE, WAPE, bias, confidence intervals)
- Interpreting model performance results
- Supply chain and inventory strategy
- Platform workflow guidance (data upload → inspection → configuration → training → results)
- Time series analysis concepts

Be concise, professional, and actionable. Use bullet points for lists."""


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


# ── Send message ───────────────────────────────────────────────────────────────

@router.post("/analyst/chats/{chat_id}/messages")
async def send_message(
    chat_id: str,
    body: dict,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Send a user message and get an AI response.

    Body:
      question     str   — the user's message
      session_id   str?  — override the chat's session (optional)
      sku          str?  — focus on a specific SKU
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

    sku        = body.get("sku") or None
    session_id = body.get("session_id") or chat.get("session_id")
    sources    = chat.get("data_sources") or []

    # Build history from last 6 messages in DB
    history = chat_store.get_history_for_context(chat_id, user.tenant_id, n_turns=6)

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

    # ── Get AI response ────────────────────────────────────────────────
    ai_source       = "general"
    ai_answer       = ""
    retrieved_count = 0

    if session_id:
        # Session-linked chat: use RAG (or its fallback chain)
        s = session_svc.get_session(user.tenant_id, session_id)
        if s and s["status"] == "COMPLETED":
            result = rag.query(
                tenant_id=user.tenant_id,
                session_id=session_id,
                question=question,
                user_id=user.user_id,
                sku=sku,
                history=history,
                chunk_types=sources if sources else None,
            )
            ai_answer       = result["answer"]
            ai_source       = result.get("source", "rag")
            retrieved_count = len(result.get("retrieved", []))
        else:
            # Session not completed — use general fallback
            ai_answer = _general_answer(question, history)
            ai_source = "general"
    else:
        ai_answer = _general_answer(question, history)
        ai_source = "general"

    # ── Save AI message ────────────────────────────────────────────────
    ai_msg = chat_store.add_message(
        chat_id=chat_id,
        tenant_id=user.tenant_id,
        role="assistant",
        content=ai_answer,
        source=ai_source,
        retrieved_count=retrieved_count or None,
    )
    chat_store.touch_chat(chat_id, user.tenant_id)

    return ok({"user_message": user_msg, "ai_message": ai_msg})


# ── Data source types reference ────────────────────────────────────────────────

@router.get("/analyst/data-source-types")
def get_data_source_types(_: CurrentUser = Depends(get_current_user)):
    return ok(DATA_SOURCE_TYPES)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _auto_title(question: str) -> str:
    """Use the local LLM to generate a short 4-6 word chat title."""
    try:
        from backend.ai.local_llm import get_local_llm_client
        client = get_local_llm_client(timeout=60.0)
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


def _general_answer(question: str, history: list[dict]) -> str:
    """General LLM response for chats without a session context (local LLM)."""
    try:
        from backend.ai.local_llm import get_local_llm_client
        client = get_local_llm_client(timeout=60.0)
        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in (history or [])[-6:]
        ]
        messages.append({"role": "user", "content": question})
        response = client.messages.create(
            max_tokens=800,
            system=_GENERAL_SYSTEM,
            messages=messages,
        )
        return response.content[0].text
    except Exception as exc:
        log.warning("General answer failed: %s", exc)
        return (
            "The AI service is temporarily unavailable. "
            "Please try again in a moment or contact your administrator."
        )
