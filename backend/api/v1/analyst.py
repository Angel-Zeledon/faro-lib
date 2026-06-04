"""
AI Analyst routes — RAG-powered Q&A over completed session data.

Security model enforced by rag.query():
  - namespace = tenant_id   → physical company isolation in Pinecone
  - filter    = session_id + user_id → row-level within company
  - MIN_SCORE threshold     → off-topic queries never reach the LLM
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from backend.ai import rag
from backend.ai.narrator import narrate
from backend.auth.guards import CurrentUser, get_current_user
from backend.schemas.common import ok
from backend.sessions import service as session_svc

router = APIRouter(tags=["analyst"])
log = logging.getLogger(__name__)


@router.post("/sessions/{session_id}/analyst/query")
async def analyst_query(
    session_id: str,
    body: dict,
    user: CurrentUser = Depends(get_current_user),
):
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if s["status"] != "COMPLETED":
        raise HTTPException(status_code=409, detail="Session must be COMPLETED to query the analyst")

    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="'question' field is required")

    sku     = body.get("sku") or None
    history = body.get("history") or []
    top_k   = int(body.get("top_k") or 8)

    result = rag.query(
        tenant_id=user.tenant_id,
        session_id=session_id,
        question=question,
        user_id=user.user_id,
        sku=sku,
        history=history,
        top_k=top_k,
    )

    return ok({
        "question":        question,
        "answer":          result["answer"],
        "sku":             sku,
        "source":          result.get("source", "unknown"),
        "retrieved_count": len(result.get("retrieved", [])),
    })


@router.get("/sessions/{session_id}/analyst/rag-status")
async def rag_status(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    status = rag.is_indexed(user.tenant_id, session_id, user.user_id)
    return ok(status)


@router.post("/sessions/{session_id}/analyst/narrate")
async def analyst_narrate(
    session_id: str,
    body: dict,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Verbalize arbitrary JSON data with business context.
    The session only provides auth/tenant isolation — it does not need to be COMPLETED.
    """
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    data    = body.get("data")
    context = (body.get("context") or "").strip()
    question = (body.get("question") or "").strip() or None
    history  = body.get("history") or []

    if data is None:
        raise HTTPException(status_code=400, detail="'data' field is required")
    if not context:
        raise HTTPException(status_code=400, detail="'context' field is required")

    result = narrate(data=data, context=context, question=question, history=history)

    if result["source"] == "error":
        raise HTTPException(status_code=502, detail=result.get("error") or "AI generation failed")

    return ok({
        "narrative":   result["narrative"],
        "source":      result["source"],
        "tokens_used": result.get("tokens_used"),
        "question":    question,
    })
