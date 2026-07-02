"""
Narrator service — arbitrary JSON + business context → Claude verbal insights.
No embeddings, no Pinecone — direct LLM call with optional conversation history.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

_MODEL       = "claude-sonnet-4-6"
_MAX_TOKENS  = 1500
_MAX_HISTORY = 6
_DATA_LIMIT  = 12_000  # chars — truncate very large payloads

_SYSTEM = """\
You are a senior business analyst embedded in an enterprise demand-forecasting platform.
A user will provide structured data (JSON) and business context. Your job is to generate
clear, concise, actionable insights in plain language.

Rules:
- Respond in the same language as the user's question or context.
- Anchor every claim to a specific number or field from the data.
- Use bullet points for lists; use prose for narrative explanations.
- Highlight risks (stockouts, anomalies, model degradation) and opportunities
  (cost savings, top performers, reorder candidates).
- Never invent data not present in the JSON.
- Keep your answer under ~400 words unless the user explicitly asks for more detail.
"""


def narrate(
    data: Any,
    context: str,
    question: Optional[str] = None,
    history: Optional[list] = None,
) -> dict:
    """
    Generate a verbal narrative for `data` given a business context.

    Args:
        data:     Any JSON-serializable object (list, dict, …).
        context:  Free-text business context ("We are a medical supplies distributor…").
        question: Optional specific question; if None, a full analysis is requested.
        history:  Optional prior turns [{"role": "user"|"assistant", "content": "…"}].

    Returns:
        {
            "narrative":   str,
            "source":      "llm" | "error",
            "tokens_used": int | None,
            "error":       str | None,
        }
    """
    try:
        from backend.ai.local_llm import get_local_llm_client
        client = get_local_llm_client(timeout=60.0)
    except Exception as exc:
        log.error("Narrator: client init failed: %s", exc)
        return {"narrative": "Could not connect to AI service.", "source": "error",
                "tokens_used": None, "error": str(exc)}

    # Serialize + truncate
    try:
        data_str = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except Exception:
        data_str = str(data)
    if len(data_str) > _DATA_LIMIT:
        data_str = data_str[:_DATA_LIMIT] + "\n\n… [data truncated — payload too large]"

    parts = [
        f"**Business context:** {context.strip()}",
        f"\n**Data:**\n```json\n{data_str}\n```",
    ]
    if question:
        parts.append(f"\n**Specific question:** {question.strip()}")
    else:
        parts.append(
            "\nPlease provide a comprehensive verbal analysis with actionable insights."
        )

    messages: list[dict] = []
    for turn in (history or [])[-_MAX_HISTORY:]:
        role    = turn.get("role")
        content = turn.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": "\n".join(parts)})

    try:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM,
            messages=messages,
        )
        narrative = resp.content[0].text if resp.content else ""
        tokens    = (resp.usage.input_tokens + resp.usage.output_tokens) if resp.usage else None
        return {"narrative": narrative, "source": "llm", "tokens_used": tokens, "error": None}
    except Exception as exc:
        log.error("Narrator: LLM call failed: %s", exc)
        return {"narrative": f"AI generation failed: {exc}", "source": "error",
                "tokens_used": None, "error": str(exc)}
