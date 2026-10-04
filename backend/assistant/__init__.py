"""StockAI's assistant: one core, every channel.

The owner's ask: answers grounded in the user's own account that feel personal,
from one bot that is then used in different places. So there is exactly one
place that decides what the assistant knows and says, and every surface calls it:

    from backend.assistant import answer

    reply = answer(tenant_id, user_id, channel, message, history,
                   role=..., language=..., budget_s=...)
    reply.text        # formatted for `channel`, ready to show/send
    reply.source      # "assistant" (model) | "rules" (no model; reply.reason says why)
    reply.grounded    # every figure was found in the account's data
    reply.unverified  # the figures that were not (already flagged in reply.text)

## What happens in a turn

1. `account.AccountData` — lazy, cached reads of THIS tenant through the same
   GET endpoint functions the screens use (tenant from the caller's identity,
   never from the message).
2. `context.build_account_context` — a compact, ranked brief: who the user is
   (first name, role, company), forecast freshness, today's red/amber products
   with quantities, cover, supplier and lead time, overstock and capital tied
   up, open and overdue purchase orders, supplier real lead times and
   reliability, demand trend and peaks, and what the user did lately. Sections
   the question is about rank first; a char budget keeps it ~1.8k tokens.
3. `core` — the persona prompt (English, answers in the user's UI language)
   plus that brief, sent to DeepSeek through `get_local_llm_client()` with the
   READ-ONLY tools of `tools.py` (product, forecast, product lists, supplier,
   purchase orders, activity). Up to 3 tool rounds inside the time budget.
4. `grounding` — every number in the reply must appear in the brief, a tool
   result or the user's message. One corrective rewrite; whatever still does
   not match is listed to the user as unverified, never silently shipped.
5. `channels` — the same answer shaped for the surface (markdown on web, short
   plain text with absolute links on WhatsApp).

No key, a failing model or no time left -> `core.rules_answer`: the same data,
written by rules from the backend locale catalog, opening with WHY it is not an
AI answer.

## Rules this package keeps

* **Read-only.** No tool writes (`docs/assistant-actions.md`: a model cannot
  hold a confirmation or an undo). For an action, the model names the screen
  that does it (`core.DEEP_LINKS`). `tests/test_assistant.py` resolves every
  read to a `{GET}` route.
* **One provider.** DeepSeek via `get_local_llm_client()`, nothing else.
* **Prompts in English**; the user-facing fallback copy lives in
  `backend/notifications/locale.py` (es + en).

## Adding a channel (email, MCP, a future integration)

1. Add a `Channel` in `channels.py`: a `style` sentence for the prompt and a
   deterministic `format()` (length cap, link base, markup).
2. Authenticate the caller in your adapter and call `answer(...)` with the
   tenant/user from that identity, your channel name and a budget that fits
   your transport (web: under the 30 s proxy; WhatsApp: Twilio's webhook).
3. Persist the conversation the way your surface needs (web: `chat_store`;
   WhatsApp: `whatsapp/conversation_store`) and pass the last turns as
   `history` ({"role": "user"|"assistant", "content": str}).

Wired today: the web chat (`api/v1/chats.py`, channel "web") and the WhatsApp
bot (`whatsapp/agent.py`, channel "whatsapp"). The public MCP server
(`backend/mcp/`) keeps its own narrower read-only catalogue for API-key
callers; it does not route through this core.
"""
from backend.assistant.core import AssistantReply, answer, rules_answer

__all__ = ["answer", "AssistantReply", "rules_answer"]
