# WhatsApp Bot (Conversational, Tool-Calling) — Design

**Date:** 2026-07-23
**Status:** Approved for planning
**Feature ref:** supersedes/absorbs pending feature 3.7 ("approve PO by replying on WhatsApp")

## Goal

Let an authenticated Faro user hold a conversation with the product over
WhatsApp: ask about inventory (semáforo, coverage, forecast, pending POs) in
natural language, and execute a bounded set of write actions — approve/send a
purchase order, and register goods reception — each behind an explicit
two-step confirmation. The bot is a thin inbound layer over the AI assistant
(`get_local_llm_client()`) and the existing tenant-scoped services; it adds no
new business logic and no new ML.

## Scope

**In scope (v1):**
- Inbound Twilio WhatsApp webhook with signature verification and idempotency.
- Identity by pre-registered phone number on the user profile (E.164),
  including number capture + 6-digit verification from the app.
- Conversational agent over `get_local_llm_client()` using tool-calling.
- Query tools (read-only): semáforo/coverage/briefing, forecast, list pending POs.
- Action tools (write, confirmation-gated): approve+send a PO, register reception.
- Per-number rate limiting and role enforcement (viewer = read-only).
- Conversation state (recent turns + one pending action) persisted per user.

**Out of scope (v2+):**
- Creating a new PO from scratch through chat.
- Business-initiated template messages (only in-session, 24h-window replies).
- Multi-user group chats; the bot answers 1:1 with a known number.
- Any UI beyond the profile "link WhatsApp number" control.

## Architecture

The bot is **not** a new brain. Each inbound message is routed to a
conversational agent that may only call a closed set of *tools* — thin wrappers
over the services the app already uses, always scoped to the sender's tenant.
The LLM never touches the database directly and never emits SQL.

### Message flow

```
WhatsApp → Twilio → POST /api/v1/whatsapp/inbound  (form-encoded)
  1. Verify X-Twilio-Signature against TWILIO_AUTH_TOKEN. Invalid → 403.
  2. Idempotency: if MessageSid already processed → 200, no-op.
  3. Resolve identity: From (E.164) → users.whatsapp_number
       → user / tenant / role. Unknown or unverified → polite reject, stop.
  4. Rate-limit per phone number. Over limit → friendly "espera un momento", stop.
  5. Load conversation state (recent turns + pending_action) for this user.
  6. Run the agent (LLM + tools):
       · If a pending_action exists and the message confirms it → execute it.
       · Else the LLM may call query tools freely, or propose a write action.
  7. Persist updated conversation state.
  8. Reply via existing outbound send_whatsapp().
```

### Two-step confirmation (the safety core)

Write tools **never execute when the LLM calls them.** Calling a write tool
stores a structured `pending_action` and returns to the LLM a rendered summary
of exactly what was resolved (PO id + supplier + amount; or SKU + warehouse +
quantity). The bot replies with that summary and a confirmation question. The
real mutation runs only on the *next* inbound message, and only if it confirms
the pending action.

- Confirmation is **system-controlled**, not left to whether the LLM remembered
  to ask. Execution requires: `pending_action` present AND the new message
  classified as an affirmative confirmation of it.
- A non-confirming next message (a new question, "no", a correction) discards
  the pending action; nothing mutates.
- This makes a misread safe: if the bot resolved the wrong SKU/warehouse/PO,
  the user sees it in the summary and declines before anything changes.

## Components

Each file has one responsibility and is independently testable.

- `backend/api/v1/whatsapp.py` — **NEW.** The inbound webhook: signature check,
  idempotency, identity resolution, rate-limit, orchestrate agent, reply.
  HTTP + wiring only; no business logic.
- `backend/whatsapp/identity.py` — **NEW.** `resolve_sender(phone) -> {user,
  tenant, role} | None`; number linking + 6-digit verification helpers.
- `backend/whatsapp/agent.py` — **NEW.** The tool-calling loop over
  `get_local_llm_client()`. Owns the confirmation gate: detect confirm-of-
  pending vs. new intent; run query tools inline; store write proposals.
- `backend/whatsapp/tools.py` — **NEW.** Tool definitions + dispatch. Query
  tools wrap `inventory/service.py`, forecast and PO read services. Write tools
  wrap the *same* PO-approve service and the *same* atomic multi-warehouse
  reception flow the app uses. Every tool re-checks tenant scope and role.
- `backend/whatsapp/conversation_store.py` — **NEW.** Load/save conversation
  state and pending action; prune past the 24h window.

### Reuse (no reimplementation)

- Outbound: existing `backend/notifications/whatsapp.py send_whatsapp()`.
- LLM: existing `get_local_llm_client()` factory (Anthropic when keyed, Ollama
  fallback) — agnostic like every other AI consumer.
- Approve PO → the same service the app's approve button calls.
- Reception → the same atomic multi-warehouse reception flow (per-warehouse
  stock credit), so races/over-receipt guards already in place still apply.
- Role gate → the role resolved in `identity.py` maps to the same
  analyst-or-above rule mutating endpoints already require.

## Data model

Two migrations (idempotent, via `backend/db/migrations.py`):

1. **`users`**: add `whatsapp_number` (TEXT, E.164, unique when non-null,
   nullable) and `whatsapp_verified_at` (TIMESTAMPTZ, nullable). A number is
   only usable once verified. Verification: user requests a link from the app,
   backend stores a short-lived 6-digit code, user replies with it (or types it
   in the app) to set `whatsapp_verified_at`.
2. **`whatsapp_conversations`** (new table): `id`, `tenant_id`, `user_id`,
   `phone`, `history` (JSONB — bounded recent turns), `pending_action` (JSONB or
   null), `last_message_sid` (TEXT, for idempotency), `updated_at`. One row per
   `(tenant_id, user_id)`; pruned when older than the 24h session window.

## Error handling & safety

- **Signature invalid / missing** → 403, no processing.
- **Idempotency** → duplicate `MessageSid` returns 200 without re-executing;
  guards against Twilio retries double-approving or double-crediting stock.
- **Rate limit per phone** → cap messages/window; over limit → friendly wait
  message, no LLM call (protects LLM cost and DB).
- **Unknown / unverified number** → polite reject; never leaks tenant data.
- **Role** → write tools revalidate analyst-or-above; viewer gets a read-only
  explanation instead of an action.
- **Tenant scope** → every tool call is bound to the sender's tenant; no tool
  can read or write another tenant's data.
- **LLM failure / timeout** → the bot apologizes and takes no action; a pending
  action is never executed on an ambiguous or errored turn.
- **No creds** → without `TWILIO_*`, outbound is a logged no-op (existing
  behavior); the webhook still verifies signature and can be exercised in tests.

## Testing

Following the repo mandate — assert DB state changes directly, permission pairs,
no tests that can't fail. The LLM is mocked (as with every AI feature); no real
Anthropic call.

- **Signature:** request without a valid signature → 403; with a valid
  signature → 200.
- **Identity:** known verified number resolves the right user/tenant/role;
  unknown → polite reject (no data leak); unverified number → reject.
- **Query tools:** a semáforo/coverage question returns tenant-scoped data and
  mutates nothing.
- **Confirmation gate (write not executed early):** the turn where the LLM
  proposes approve/reception makes **no** DB change (assert PO still draft /
  stock unchanged). Only after an affirmative confirmation does state change:
  PO row becomes approved; `inventory_stock` for the named warehouse rises by
  the exact quantity.
- **Permission pair:** viewer number → write tool denied, state unchanged;
  analyst number → success.
- **Idempotency:** same `MessageSid` delivered twice → exactly one execution
  (assert single state change).
- **Rate limit:** over-limit number → no LLM invocation, friendly reply.
- **Reception resolution:** "llegaron 200 a bodega norte" resolves to the
  intended SKU + warehouse and, on confirm, credits that warehouse by 200
  (assert per-warehouse).

## Open questions

None blocking. Number-verification UX (reply-with-code vs. type-in-app) is an
implementation detail settled in the plan; both set `whatsapp_verified_at`.
