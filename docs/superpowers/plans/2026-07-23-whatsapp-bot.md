# WhatsApp Bot (Conversational, Tool-Calling) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an authenticated Faro user hold a WhatsApp conversation with the product — ask about inventory in natural language and execute two write actions (approve/send a PO, register a goods reception), each behind a system-controlled two-step confirmation.

**Architecture:** An inbound Twilio webhook (`backend/api/v1/whatsapp.py`) verifies the request, resolves the sender to a verified user, rate-limits, then hands the message to a thin tool-calling agent (`backend/whatsapp/agent.py`). The agent uses `get_local_llm_client()` only to route intent to a closed set of tools (`backend/whatsapp/tools.py`) that wrap the app's existing tenant-scoped services. Write tools never mutate when called — they store a `pending_action`; the mutation runs only on a following affirmative message. Conversation state (recent turns + one pending action) is persisted per user in `whatsapp_conversations` (`backend/whatsapp/conversation_store.py`). Identity/number linking lives in `backend/whatsapp/identity.py`.

**Tech Stack:** FastAPI, psycopg2 (Postgres), Twilio REST (outbound reuse), `get_local_llm_client()` (Anthropic/Ollama), pytest + FastAPI TestClient.

## Global Constraints

- **All code, comments, docstrings, identifiers, test names, commit messages in English.** The ONLY Spanish allowed is end-user copy — the text the bot sends back over WhatsApp (mirrors the existing `notifications/whatsapp.py` Spanish message builders).
- **No pandas/numpy** anywhere in `backend/whatsapp/` or `backend/api/v1/whatsapp.py`. `tests/test_no_pandas_in_backend.py` enforces this repo-wide.
- **Reuse, don't reimplement.** Outbound: `backend/notifications/whatsapp.py::send_whatsapp`. LLM: `backend/ai/local_llm.py::get_local_llm_client`. PO approve/send state change: `backend/inventory/reception_service.py::mark_po_sent`. Reception: `backend/inventory/reception_service.py::receive_po` (the atomic multi-warehouse flow with over-receipt guards). Semáforo/briefing: `backend/inventory/service.py`. PO list: `backend/inventory/roi_service.py::get_po_history`.
- **Role rule:** analyst-or-above = role in `("admin", "analyst")`; `viewer` is read-only. Write tools re-check this against the role resolved from the sender's number.
- **Every tool call is tenant-scoped** to the sender's tenant; no tool reads or writes another tenant's data.
- **Testing mandate:** assert DB state changes with direct queries (not just status codes/echoes); every write path has a permission pair (viewer denied + state unchanged, analyst success); no tests that can't fail. LLM is always mocked — never a real Anthropic call.
- **Migrations** are appended to the `_MIGRATIONS` list in `backend/db/migrations.py`; every statement is idempotent (`IF NOT EXISTS` / guarded `DO $$`), safe to re-run on every startup.

## Resolved Spec Ambiguities

- **"PO becomes approved":** the `inventory_po_log` schema has no `draft`/`approved` status column. The app's approve/send button (`POST /inventory/po/{id}/send`) records approval by stamping `sent_at` via `reception_service.mark_po_sent` (first-write-wins on `sent_at IS NULL`). The write tool therefore treats "approve/send a PO" as calling `mark_po_sent`, and the DB assertion for "approved" is `sent_at IS NOT NULL`. Rendering the full supplier PDF/email fan-out (`send_po_to_suppliers`) is out of scope for the bot — it needs supplier contact data and generates files; the observable, testable, tenant-scoped state change is `sent_at`.
- **Reception is against a PO line, not a raw stock write.** The spec says reuse the atomic multi-warehouse reception flow with its over-receipt guards — that is `receive_po`. The bot resolves the pending/partial PO whose ordered line carries the SKU (disambiguated by the named warehouse when given); on confirm it calls `receive_po(tenant, po_log_id, user_id, lines=[{sku, received_qty}])`, which credits that line's own warehouse in `inventory_stock (tenant_id, sku, warehouse)`.
- **Verification code storage reuses `pw_change_codes`** with `purpose='whatsapp'` (already has `code_hash`, `expires_at`, `attempts`, `used`, `purpose`) — no third migration. The pending (unverified) number is held in the existing `users.whatsapp_number` column; the new `users.whatsapp_verified_at` gates usability. This keeps exactly the two migrations the spec names (verified_at column + conversations table), plus a partial unique index folded into the users migration.
- **Idempotency** uses `whatsapp_conversations.last_message_sid`: an inbound whose `MessageSid` equals the conversation's stored `last_message_sid` is a Twilio retry → 200 no-op (this is what guards against double-approve / double-credit, since the same delivered message carries the same MessageSid).
- **Confirmation is system-controlled, not LLM-decided.** The confirming turn does NOT call the LLM at all: a deterministic affirmative classifier (`agent.is_affirmative`) decides whether the stored `pending_action` executes. Any non-affirmative message discards the pending action and is handled as a fresh intent.

---

## File Structure

- Create `backend/whatsapp/__init__.py` — package marker.
- Create `backend/whatsapp/identity.py` — phone normalization, sender resolution, number linking + 6-digit verification helpers.
- Create `backend/whatsapp/conversation_store.py` — load/save conversation state + pending action; 24h pruning; idempotency read.
- Create `backend/whatsapp/tools.py` — tool context, query tools, write-tool proposal builders, and the confirmed-action executor. All tenant-scoped and role-checked.
- Create `backend/whatsapp/agent.py` — the LLM tool-routing loop and the deterministic confirmation gate; the single `get_local_llm_client()` consumer.
- Create `backend/api/v1/whatsapp.py` — inbound webhook: Twilio signature check, idempotency, identity, rate-limit, orchestrate agent, reply.
- Modify `backend/db/migrations.py` — append the two migrations (+ unique index).
- Modify `backend/api/v1/users.py` — add WhatsApp link-request + confirm endpoints.
- Modify `backend/main.py` — register the whatsapp router.
- Create `backend/tests/test_whatsapp_identity.py`, `test_whatsapp_conversation_store.py`, `test_whatsapp_tools.py`, `test_whatsapp_agent.py`, `test_whatsapp_webhook.py`, `test_whatsapp_number_linking.py`.

---

## Task 1: Migrations — `whatsapp_verified_at`, unique number index, `whatsapp_conversations`

**Files:**
- Modify: `backend/db/migrations.py` (append to the `_MIGRATIONS` list, before the closing `]` at the end of the incremental section near line 889)
- Test: `backend/tests/test_whatsapp_migrations.py`

**Interfaces:**
- Produces: `users.whatsapp_verified_at TIMESTAMPTZ NULL`; partial unique index `users_whatsapp_number_uniq` on `users(whatsapp_number) WHERE whatsapp_number IS NOT NULL`; table `whatsapp_conversations(id TEXT PK, tenant_id TEXT, user_id TEXT, phone TEXT, history JSONB, pending_action JSONB, last_message_sid TEXT, updated_at TIMESTAMPTZ)` with unique index on `(tenant_id, user_id)`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_whatsapp_migrations.py`:

```python
"""The WhatsApp bot migrations must exist on the live schema after startup."""
from backend.db.connection import query_one


def test_users_has_whatsapp_verified_at():
    row = query_one(
        """SELECT 1 AS ok FROM information_schema.columns
           WHERE table_name = 'users' AND column_name = 'whatsapp_verified_at'"""
    )
    assert row is not None, "users.whatsapp_verified_at missing"


def test_whatsapp_number_partial_unique_index_exists():
    row = query_one(
        "SELECT 1 AS ok FROM pg_class WHERE relname = 'users_whatsapp_number_uniq'"
    )
    assert row is not None, "partial unique index on users.whatsapp_number missing"


def test_whatsapp_conversations_table_exists():
    row = query_one(
        """SELECT 1 AS ok FROM information_schema.tables
           WHERE table_name = 'whatsapp_conversations'"""
    )
    assert row is not None, "whatsapp_conversations table missing"


def test_whatsapp_conversations_has_expected_columns():
    rows = query_one(
        """SELECT array_agg(column_name::text) AS cols
           FROM information_schema.columns
           WHERE table_name = 'whatsapp_conversations'"""
    )
    cols = set(rows["cols"])
    assert {"id", "tenant_id", "user_id", "phone", "history",
            "pending_action", "last_message_sid", "updated_at"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_whatsapp_migrations.py -q`
Expected: FAIL (column/index/table not found) — the migrations run at app startup via the `client` fixture in other tests, but these tests query the schema directly; they fail until migrations are added and applied. If they error on "DB pool not initialized", add `client` fixture usage: they do not need it because the session-wide app import triggers `run_all()`. If pool isn't initialized, prepend a `client` fixture param to each test. Expected first run: FAIL on missing objects.

- [ ] **Step 3: Add the migrations**

In `backend/db/migrations.py`, append these entries to the end of the `_MIGRATIONS` list (just before the final `]` that closes the list, after the `add_sessions_granularity` / `create_sessions_family_idx` block):

```python
    # ── Conversational WhatsApp bot (spec 2026-07-23) ────────────────────────
    # A pre-registered number identifies the sender; it is only usable once
    # verified. whatsapp_number itself already exists (add_users_whatsapp_number).
    ("add_users_whatsapp_verified_at",
     "ALTER TABLE users ADD COLUMN IF NOT EXISTS whatsapp_verified_at TIMESTAMPTZ"),
    # One number links to at most one user. Partial so many users may keep NULL.
    ("create_users_whatsapp_number_uniq",
     """CREATE UNIQUE INDEX IF NOT EXISTS users_whatsapp_number_uniq
        ON users (whatsapp_number) WHERE whatsapp_number IS NOT NULL"""),
    # Recent turns + one pending write action, one row per (tenant, user).
    # last_message_sid is the idempotency anchor (Twilio retries resend the
    # same MessageSid). Rows older than the 24h window are pruned on read.
    ("create_whatsapp_conversations",
     """CREATE TABLE IF NOT EXISTS whatsapp_conversations (
         id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id        TEXT NOT NULL,
         user_id          TEXT NOT NULL,
         phone            TEXT NOT NULL,
         history          JSONB NOT NULL DEFAULT '[]',
         pending_action   JSONB,
         last_message_sid TEXT,
         updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_whatsapp_conversations_uniq",
     """CREATE UNIQUE INDEX IF NOT EXISTS whatsapp_conversations_tenant_user_uniq
        ON whatsapp_conversations (tenant_id, user_id)"""),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_whatsapp_migrations.py -q`
Expected: PASS (the `client`/app import in conftest applies migrations at session start; if a test runs standalone without the app import, add `def test_...(client):` param — the `client` session fixture guarantees `run_all()` ran).

- [ ] **Step 5: Commit**

```bash
git add backend/db/migrations.py backend/tests/test_whatsapp_migrations.py
git commit -m "feat(whatsapp): migrations for verified number + conversations table"
```

---

## Task 2: `identity.py` — phone normalization, sender resolution, verification

**Files:**
- Create: `backend/whatsapp/__init__.py` (empty)
- Create: `backend/whatsapp/identity.py`
- Test: `backend/tests/test_whatsapp_identity.py`

**Interfaces:**
- Consumes: `backend.db.connection.query_one/execute`; `backend.config.settings.secret_key`.
- Produces:
  - `normalize_phone(raw: str) -> str` — strips a leading `whatsapp:` and surrounding whitespace, returns E.164-ish string.
  - `is_e164(num: str) -> bool` — matches `^\+[1-9]\d{7,14}$`.
  - `resolve_sender(phone: str) -> dict | None` — returns `{"user_id", "tenant_id", "role", "phone"}` for a user whose `whatsapp_number == phone` AND `whatsapp_verified_at IS NOT NULL`, else `None`.
  - `start_verification(tenant_id: str, user_id: str, phone: str) -> str` — validates E.164; rejects (raises `ValueError`) if the number is already verified on ANOTHER user; sets `users.whatsapp_number = phone`, `whatsapp_verified_at = NULL`; issues a 6-digit code stored in `pw_change_codes` with `purpose='whatsapp'`; returns the plaintext code.
  - `confirm_verification(tenant_id: str, user_id: str, code: str) -> bool` — validates the newest unused `purpose='whatsapp'` code (15-min expiry, ≤5 attempts); on success sets `whatsapp_verified_at = NOW()` and marks the code used, returns `True`; else `False`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_whatsapp_identity.py`:

```python
"""Identity resolution + number linking/verification for the WhatsApp bot."""
import pytest

from backend.db.connection import query_one, execute
from backend.whatsapp import identity


def test_normalize_phone_strips_whatsapp_prefix():
    assert identity.normalize_phone("whatsapp:+573001234567") == "+573001234567"
    assert identity.normalize_phone("  +573001234567 ") == "+573001234567"


def test_is_e164():
    assert identity.is_e164("+573001234567")
    assert not identity.is_e164("3001234567")
    assert not identity.is_e164("+0123")


def test_resolve_sender_unknown_returns_none():
    assert identity.resolve_sender("+59990000000") is None


def test_resolve_sender_unverified_returns_none(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    execute(
        "UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = NULL WHERE id = %s",
        ("+573001112222", uid),
    )
    assert identity.resolve_sender("+573001112222") is None


def test_resolve_sender_verified_returns_context(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    execute(
        "UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = NOW() WHERE id = %s",
        ("+573002223333", uid),
    )
    ctx = identity.resolve_sender("+573002223333")
    assert ctx is not None
    assert ctx["user_id"] == uid
    assert ctx["tenant_id"] == tid
    assert ctx["role"] == "admin"


def test_start_and_confirm_verification_sets_verified_at(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    code = identity.start_verification(tid, uid, "+573004445555")
    # Before confirmation the number is present but NOT usable.
    assert identity.resolve_sender("+573004445555") is None
    assert identity.confirm_verification(tid, uid, code) is True
    row = query_one("SELECT whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_verified_at"] is not None
    assert identity.resolve_sender("+573004445555") is not None


def test_confirm_verification_wrong_code_fails(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    identity.start_verification(tid, uid, "+573005556666")
    assert identity.confirm_verification(tid, uid, "000000") is False
    row = query_one("SELECT whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_verified_at"] is None


def test_start_verification_rejects_number_verified_on_another_user(registered_user, analyst_user):
    tid = registered_user["tenant"]["id"]
    execute(
        "UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = NOW() WHERE id = %s",
        ("+573007778888", registered_user["user"]["id"]),
    )
    with pytest.raises(ValueError):
        identity.start_verification(tid, analyst_user["user"]["id"], "+573007778888")


def test_start_verification_rejects_bad_format(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    with pytest.raises(ValueError):
        identity.start_verification(tid, uid, "3001234567")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_whatsapp_identity.py -q`
Expected: FAIL with `ModuleNotFoundError: backend.whatsapp.identity`.

- [ ] **Step 3: Write the implementation**

Create `backend/whatsapp/__init__.py` (empty file).

Create `backend/whatsapp/identity.py`:

```python
"""
Identity for the WhatsApp bot: a pre-registered, verified phone number on a
user profile is the sole credential. A number is only usable once
`whatsapp_verified_at` is set; the pending (unverified) number lives in the
existing `users.whatsapp_number` column and the 6-digit code reuses the
`pw_change_codes` table (purpose='whatsapp').
"""

from __future__ import annotations

import hashlib
import hmac
import random
import re
from datetime import datetime, timedelta, timezone

from backend.config import settings
from backend.db.connection import execute, query_one

_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
_CODE_EXPIRE_MINUTES = 15
_CODE_MAX_ATTEMPTS = 5
_PURPOSE = "whatsapp"


def normalize_phone(raw: str) -> str:
    """Strip Twilio's `whatsapp:` channel prefix and whitespace."""
    s = (raw or "").strip()
    if s.lower().startswith("whatsapp:"):
        s = s[len("whatsapp:"):].strip()
    return s


def is_e164(num: str) -> bool:
    return bool(_E164_RE.fullmatch(num or ""))


def _hash_code(code: str) -> str:
    return hmac.new(settings.secret_key.encode(), code.encode(), hashlib.sha256).hexdigest()


def resolve_sender(phone: str) -> dict | None:
    """
    Map an inbound E.164 number to its verified user. Returns None for unknown
    or unverified numbers — the webhook turns that into a polite reject and
    never leaks tenant data.
    """
    row = query_one(
        """SELECT id AS user_id, tenant_id, role
           FROM users
           WHERE whatsapp_number = %s AND whatsapp_verified_at IS NOT NULL""",
        (phone,),
    )
    if not row:
        return None
    return {"user_id": row["user_id"], "tenant_id": row["tenant_id"],
            "role": row["role"], "phone": phone}


def start_verification(tenant_id: str, user_id: str, phone: str) -> str:
    """
    Begin linking `phone` to this user. Stores it unverified and returns a
    fresh 6-digit code. Raises ValueError on bad format or if the number is
    already verified by a different user.
    """
    phone = normalize_phone(phone)
    if not is_e164(phone):
        raise ValueError("whatsapp_number must be E.164 format with country code, e.g. +573001234567")

    clash = query_one(
        """SELECT id FROM users
           WHERE whatsapp_number = %s AND whatsapp_verified_at IS NOT NULL
             AND id <> %s""",
        (phone, user_id),
    )
    if clash:
        raise ValueError("This WhatsApp number is already linked to another account")

    execute(
        """UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = NULL,
               updated_at = NOW()
           WHERE id = %s AND tenant_id = %s""",
        (phone, user_id, tenant_id),
    )

    code = f"{random.SystemRandom().randint(0, 999999):06d}"
    execute(
        "DELETE FROM pw_change_codes WHERE user_id = %s AND purpose = %s AND used = FALSE",
        (user_id, _PURPOSE),
    )
    execute(
        """INSERT INTO pw_change_codes (id, user_id, tenant_id, code_hash, expires_at, purpose)
           VALUES (gen_random_uuid()::text, %s, %s, %s, %s, %s)""",
        (user_id, tenant_id, _hash_code(code),
         datetime.now(timezone.utc) + timedelta(minutes=_CODE_EXPIRE_MINUTES), _PURPOSE),
    )
    return code


def confirm_verification(tenant_id: str, user_id: str, code: str) -> bool:
    """
    Validate the pending code and, on success, mark the number verified.
    Enforces expiry and an attempt cap; a wrong code burns one attempt.
    """
    row = query_one(
        """SELECT id, code_hash, expires_at, attempts
           FROM pw_change_codes
           WHERE user_id = %s AND tenant_id = %s AND purpose = %s AND used = FALSE
           ORDER BY expires_at DESC LIMIT 1""",
        (user_id, tenant_id, _PURPOSE),
    )
    if not row:
        return False
    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return False
    if (row["attempts"] or 0) >= _CODE_MAX_ATTEMPTS:
        return False

    if not hmac.compare_digest(row["code_hash"], _hash_code(code)):
        execute("UPDATE pw_change_codes SET attempts = attempts + 1 WHERE id = %s", (row["id"],))
        return False

    execute("UPDATE pw_change_codes SET used = TRUE WHERE id = %s", (row["id"],))
    execute(
        "UPDATE users SET whatsapp_verified_at = NOW(), updated_at = NOW() WHERE id = %s AND tenant_id = %s",
        (user_id, tenant_id),
    )
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_whatsapp_identity.py -q`
Expected: PASS (all 9 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/whatsapp/__init__.py backend/whatsapp/identity.py backend/tests/test_whatsapp_identity.py
git commit -m "feat(whatsapp): identity resolution + number verification helpers"
```

---

## Task 3: WhatsApp number linking endpoints on the users router

**Files:**
- Modify: `backend/api/v1/users.py` (add two endpoints after `update_me`, near line 91)
- Test: `backend/tests/test_whatsapp_number_linking.py`

**Interfaces:**
- Consumes: `backend.whatsapp.identity.start_verification/confirm_verification`; `CurrentUser`, `get_current_user`.
- Produces HTTP:
  - `POST /api/v1/users/me/whatsapp/link` body `{"whatsapp_number": "+57..."}` → `ok({"sent": true})`. In non-production, also returns the code under `debug_code` so the app/tests can complete the flow without a live SMS/WhatsApp round-trip (mirrors the code-return pattern used only when not production).
  - `POST /api/v1/users/me/whatsapp/confirm` body `{"code": "123456"}` → `ok({"verified": true})` or 400 on bad code.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_whatsapp_number_linking.py`:

```python
"""Profile endpoints to link and verify a WhatsApp number (in-app path)."""
from backend.db.connection import query_one


def _link(client, headers, number):
    return client.post("/api/v1/users/me/whatsapp/link",
                       json={"whatsapp_number": number}, headers=headers)


def test_link_then_confirm_sets_verified_at(client, auth_headers, registered_user):
    uid = registered_user["user"]["id"]
    resp = _link(client, auth_headers, "+573009990001")
    assert resp.status_code == 200, resp.text
    code = resp.json()["data"]["debug_code"]

    # Not yet verified in the DB.
    row = query_one("SELECT whatsapp_number, whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_number"] == "+573009990001"
    assert row["whatsapp_verified_at"] is None

    resp = client.post("/api/v1/users/me/whatsapp/confirm",
                       json={"code": code}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    row = query_one("SELECT whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_verified_at"] is not None


def test_link_rejects_bad_format(client, auth_headers):
    resp = _link(client, auth_headers, "3009990002")
    assert resp.status_code == 422


def test_confirm_wrong_code_400_and_unverified(client, auth_headers, registered_user):
    uid = registered_user["user"]["id"]
    _link(client, auth_headers, "+573009990003")
    resp = client.post("/api/v1/users/me/whatsapp/confirm",
                       json={"code": "000000"}, headers=auth_headers)
    assert resp.status_code == 400
    row = query_one("SELECT whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_verified_at"] is None


def test_link_requires_auth(client):
    resp = client.post("/api/v1/users/me/whatsapp/link",
                       json={"whatsapp_number": "+573009990004"})
    assert resp.status_code in (401, 403)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_whatsapp_number_linking.py -q`
Expected: FAIL (404 — endpoints not defined).

- [ ] **Step 3: Write the implementation**

In `backend/api/v1/users.py`, add after the `update_me` function (after line 91). Add near the other `class ...(BaseModel)` blocks:

```python
class WhatsAppLinkRequest(BaseModel):
    whatsapp_number: str


class WhatsAppConfirmRequest(BaseModel):
    code: str


@router.post("/me/whatsapp/link")
def link_whatsapp(body: WhatsAppLinkRequest, user: CurrentUser = Depends(get_current_user)):
    """Start linking a WhatsApp number: stores it unverified and issues a code."""
    from backend.whatsapp import identity
    try:
        code = identity.start_verification(user.tenant_id, user.user_id, body.whatsapp_number)
    except ValueError as e:
        # Bad format -> 422; already-linked -> 409.
        status = 409 if "already linked" in str(e) else 422
        raise HTTPException(status_code=status, detail=str(e))

    payload = {"sent": True}
    # Outside production we surface the code so the in-app "type the code" flow
    # (and tests) can complete without a live WhatsApp round-trip. The daily
    # alert transport is a logged no-op without TWILIO_* anyway.
    if settings.environment.strip().lower() not in ("production", "prod"):
        payload["debug_code"] = code
    else:
        from backend.notifications.whatsapp import send_whatsapp
        send_whatsapp(body.whatsapp_number.strip(),
                      f"Tu código de verificación de Faro es: {code}")
    return ok(payload)


@router.post("/me/whatsapp/confirm")
def confirm_whatsapp(body: WhatsAppConfirmRequest, user: CurrentUser = Depends(get_current_user)):
    """Confirm the code and mark the number verified."""
    from backend.whatsapp import identity
    if not identity.confirm_verification(user.tenant_id, user.user_id, body.code.strip()):
        raise HTTPException(status_code=400, detail="Código inválido o expirado")
    return ok({"verified": True})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_whatsapp_number_linking.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/api/v1/users.py backend/tests/test_whatsapp_number_linking.py
git commit -m "feat(whatsapp): in-app link + verify WhatsApp number endpoints"
```

---

## Task 4: `conversation_store.py` — state, pending action, idempotency, pruning

**Files:**
- Create: `backend/whatsapp/conversation_store.py`
- Test: `backend/tests/test_whatsapp_conversation_store.py`

**Interfaces:**
- Consumes: `backend.db.connection.query_one/execute`.
- Produces:
  - `SESSION_WINDOW_HOURS = 24`, `MAX_TURNS = 12`.
  - `load(tenant_id, user_id) -> dict` — returns `{"history": list[dict], "pending_action": dict | None, "last_message_sid": str | None, "exists": bool}`. If the stored row's `updated_at` is older than the 24h window, it is treated as empty (`history=[]`, `pending_action=None`) — i.e. pruned on read.
  - `is_duplicate(tenant_id, user_id, message_sid) -> bool` — True iff a non-pruned row's `last_message_sid == message_sid`.
  - `save(tenant_id, user_id, phone, history, pending_action, last_message_sid) -> None` — upsert one row per `(tenant_id, user_id)`, trimming history to the last `MAX_TURNS`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_whatsapp_conversation_store.py`:

```python
"""Conversation state persistence + idempotency + 24h pruning."""
from backend.db.connection import execute, query_one
from backend.whatsapp import conversation_store as cs


def test_load_empty_when_no_row(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    state = cs.load(tid, uid)
    assert state["history"] == []
    assert state["pending_action"] is None
    assert state["exists"] is False


def test_save_then_load_roundtrip(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    cs.save(tid, uid, "+573001234567",
            history=[{"role": "user", "content": "hola"}],
            pending_action={"type": "approve_po", "po_log_id": "po1"},
            last_message_sid="SM1")
    state = cs.load(tid, uid)
    assert state["history"] == [{"role": "user", "content": "hola"}]
    assert state["pending_action"]["type"] == "approve_po"
    assert state["last_message_sid"] == "SM1"
    assert state["exists"] is True


def test_save_upserts_single_row(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    cs.save(tid, uid, "+57300", history=[], pending_action=None, last_message_sid="SM1")
    cs.save(tid, uid, "+57300", history=[], pending_action=None, last_message_sid="SM2")
    row = query_one(
        "SELECT COUNT(*) AS n FROM whatsapp_conversations WHERE tenant_id = %s AND user_id = %s",
        (tid, uid),
    )
    assert row["n"] == 1


def test_history_trimmed_to_max_turns(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    long_history = [{"role": "user", "content": str(i)} for i in range(50)]
    cs.save(tid, uid, "+57300", history=long_history, pending_action=None, last_message_sid="SMx")
    state = cs.load(tid, uid)
    assert len(state["history"]) == cs.MAX_TURNS
    assert state["history"][-1]["content"] == "49"


def test_is_duplicate_matches_last_sid(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    cs.save(tid, uid, "+57300", history=[], pending_action=None, last_message_sid="SM-DUP")
    assert cs.is_duplicate(tid, uid, "SM-DUP") is True
    assert cs.is_duplicate(tid, uid, "SM-OTHER") is False


def test_stale_row_pruned_on_read(registered_user):
    tid = registered_user["tenant"]["id"]
    uid = registered_user["user"]["id"]
    cs.save(tid, uid, "+57300",
            history=[{"role": "user", "content": "old"}],
            pending_action={"type": "approve_po"}, last_message_sid="SM-OLD")
    # Age the row past the 24h window.
    execute(
        "UPDATE whatsapp_conversations SET updated_at = NOW() - INTERVAL '25 hours' "
        "WHERE tenant_id = %s AND user_id = %s",
        (tid, uid),
    )
    state = cs.load(tid, uid)
    assert state["history"] == []
    assert state["pending_action"] is None
    # A stale row must not make an old MessageSid look already-processed.
    assert cs.is_duplicate(tid, uid, "SM-OLD") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_whatsapp_conversation_store.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `backend/whatsapp/conversation_store.py`:

```python
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
```

Note: `execute` uses psycopg2 with `%s`; passing `json.dumps(...)` as text into a JSONB column works (Postgres casts a JSON string literal). If a cast error surfaces, wrap with `psycopg2.extras.Json` via `backend.db.connection._json`. Prefer `json.dumps` first; only switch if the test fails on a cast.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_whatsapp_conversation_store.py -q`
Expected: PASS (6 tests). If a JSONB cast error appears, change the two dumped params to `from backend.db.connection import _json` and pass `_json(trimmed)` / `_json(pending_action)`; rerun.

- [ ] **Step 5: Commit**

```bash
git add backend/whatsapp/conversation_store.py backend/tests/test_whatsapp_conversation_store.py
git commit -m "feat(whatsapp): conversation store with idempotency and 24h pruning"
```

---

## Task 5: `tools.py` — query tools, write proposals, confirmed executor

**Files:**
- Create: `backend/whatsapp/tools.py`
- Test: `backend/tests/test_whatsapp_tools.py`

**Interfaces:**
- Consumes: `backend.inventory.service` (`get_latest_completed_session`, `get_morning_briefing`), `backend.inventory.roi_service.get_po_history`, `backend.inventory.reception_service` (`get_po`, `get_po_items`, `mark_po_sent`, `receive_po`), `backend.db.connection.query`.
- Produces:
  - `class ToolContext` with attributes `tenant_id`, `user_id`, `role`; property `is_analyst_or_above -> bool` (role in `("admin","analyst")`).
  - `QUERY_TOOLS: dict[str, callable]` and `WRITE_TOOLS: dict[str, callable]` mapping tool name → handler.
  - `TOOL_SPECS: list[dict]` — name/description/args for the agent's routing prompt.
  - Query handlers `(ctx, args) -> str` (Spanish end-user text):
    - `semaphore_status(ctx, args)` — resolves the tenant's latest completed session, calls `get_morning_briefing`, renders a short summary (counts of PEDIR_YA / PEDIR_PRONTO / SOBRESTOCK + top risks).
    - `list_pending_pos(ctx, args)` — `get_po_history` filtered to `reception_status in ('pending','partial')`, rendered as a short list (po_number, supplier count, total).
    - `forecast_summary(ctx, args)` with `args["sku"]` — reads `session_results.forecasts` for the latest session and renders the SKU's near-term trend; if no SKU or not found, a helpful message.
  - Write proposal builders `(ctx, args) -> dict` returning a `pending_action` `{"type", ..., "summary"}` WITHOUT mutating:
    - `propose_approve_po(ctx, args)` with `args["po_log_id"]` — resolves the PO (tenant-scoped); raises `ToolError` if missing or already sent; returns `{"type":"approve_po","po_log_id","summary"}`. `summary` = `"Aprobar OC #<n> — <k> proveedor(es), total <money>. ¿Confirmas?"`.
    - `propose_reception(ctx, args)` with `args["sku"]`, optional `args["warehouse"]`, `args["quantity"]` — finds the most recent pending/partial PO with an ordered line for the SKU (matching warehouse if given); raises `ToolError` if none; returns `{"type":"register_reception","po_log_id","sku","warehouse","quantity","summary"}`. `summary` = `"Registrar recepción de <qty> uds de <sku> en <warehouse> (OC #<n>). ¿Confirmas?"`.
  - `execute_pending_action(ctx, action) -> str` — re-checks `ctx.is_analyst_or_above` (raises `ToolError` if viewer); dispatches on `action["type"]`: `approve_po` → `mark_po_sent`; `register_reception` → `receive_po(..., lines=[{"sku","received_qty"}])`. Returns a Spanish success message. Never called except from a confirmed turn.
  - `class ToolError(Exception)` — carries an end-user Spanish message.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_whatsapp_tools.py`. These tests seed DB rows directly and call tools with a `ToolContext`; they do NOT touch the LLM.

```python
"""Query tools read tenant-scoped data and mutate nothing; write tools propose
without mutating; the executor mutates only when invoked and enforces role."""
import pytest

from backend.db.connection import execute, query_one
from backend.whatsapp import tools
from backend.whatsapp.tools import ToolContext, ToolError


def _ctx(reg, role="admin"):
    return ToolContext(tenant_id=reg["tenant"]["id"], user_id=reg["user"]["id"], role=role)


def _seed_po(tid, *, sku="SKU1", warehouse="bodega norte", qty=200, sent=False):
    po = query_one(
        """INSERT INTO inventory_po_log
               (tenant_id, session_id, sku_count, total_units, total_value, reception_status, sent_at)
           VALUES (%s, %s, 1, %s, %s, 'pending', %s)
           RETURNING id""",
        (tid, "sess-x", qty, qty * 10, ("NOW()" if sent else None)),
    ) if False else None
    # Explicit two-step insert so sent_at is a real timestamp or NULL.
    row = query_one(
        """INSERT INTO inventory_po_log
               (tenant_id, session_id, sku_count, total_units, total_value, reception_status)
           VALUES (%s, 'sess-x', 1, %s, %s, 'pending')
           RETURNING id, po_number""",
        (tid, qty, qty * 10),
    )
    po_id = row["id"]
    if sent:
        execute("UPDATE inventory_po_log SET sent_at = NOW() WHERE id = %s", (po_id,))
    execute(
        """INSERT INTO inventory_po_items
               (po_log_id, tenant_id, sku, display_name, supplier, status,
                recommended_qty, final_qty, unit_cost, warehouse)
           VALUES (%s, %s, %s, %s, 'Proveedor A', 'approved', %s, %s, 10, %s)""",
        (po_id, tid, sku, sku, qty, qty, warehouse),
    )
    return po_id


# ── Query tools ──────────────────────────────────────────────────────────────

def test_semaphore_status_returns_text_and_no_mutation(registered_user, completed_session):
    ctx = _ctx(registered_user)
    before = query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s",
                       (ctx.tenant_id,))["n"]
    out = tools.QUERY_TOOLS["semaphore_status"](ctx, {})
    assert isinstance(out, str) and len(out) > 0
    after = query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s",
                      (ctx.tenant_id,))["n"]
    assert before == after


def test_list_pending_pos_is_tenant_scoped(registered_user):
    ctx = _ctx(registered_user)
    _seed_po(ctx.tenant_id, sku="A")
    out = tools.QUERY_TOOLS["list_pending_pos"](ctx, {})
    assert isinstance(out, str)
    assert "OC" in out or "pendiente" in out.lower()


# ── Write proposals: NO mutation ─────────────────────────────────────────────

def test_propose_approve_po_does_not_mutate(registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id)
    action = tools.propose_approve_po(ctx, {"po_log_id": po_id})
    assert action["type"] == "approve_po"
    assert action["po_log_id"] == po_id
    assert "summary" in action
    # sent_at still NULL — proposing changed nothing.
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is None


def test_propose_reception_does_not_mutate(registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id, sku="SKU1", warehouse="bodega norte", qty=200)
    action = tools.propose_reception(ctx, {"sku": "SKU1", "warehouse": "bodega norte", "quantity": 200})
    assert action["type"] == "register_reception"
    assert action["po_log_id"] == po_id
    assert action["warehouse"] == "bodega norte"
    assert action["quantity"] == 200
    # No stock row created, no received_qty accumulated.
    stock = query_one(
        "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s AND warehouse = %s",
        (ctx.tenant_id, "SKU1", "bodega norte"),
    )
    assert stock is None
    item = query_one("SELECT received_qty FROM inventory_po_items WHERE po_log_id = %s", (po_id,))
    assert (item["received_qty"] or 0) == 0


# ── Executor: mutates, enforces role ─────────────────────────────────────────

def test_execute_approve_po_stamps_sent_at(registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id)
    msg = tools.execute_pending_action(ctx, {"type": "approve_po", "po_log_id": po_id})
    assert isinstance(msg, str)
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is not None


def test_execute_reception_credits_named_warehouse(registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id, sku="SKU1", warehouse="bodega norte", qty=200)
    tools.execute_pending_action(ctx, {
        "type": "register_reception", "po_log_id": po_id,
        "sku": "SKU1", "warehouse": "bodega norte", "quantity": 200,
    })
    stock = query_one(
        "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s AND warehouse = %s",
        (ctx.tenant_id, "SKU1", "bodega norte"),
    )
    assert stock is not None
    assert float(stock["current_stock"]) == 200.0


def test_execute_denies_viewer_and_does_not_mutate(registered_user):
    ctx = _ctx(registered_user, role="viewer")
    po_id = _seed_po(ctx.tenant_id)
    with pytest.raises(ToolError):
        tools.execute_pending_action(ctx, {"type": "approve_po", "po_log_id": po_id})
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is None


def test_propose_approve_po_missing_raises(registered_user):
    ctx = _ctx(registered_user)
    with pytest.raises(ToolError):
        tools.propose_approve_po(ctx, {"po_log_id": "does-not-exist"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_whatsapp_tools.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `backend/whatsapp/tools.py`:

```python
"""
The closed set of tools the WhatsApp agent may call. Query tools are
read-only; write tools NEVER mutate — they return a pending_action that the
agent stores, and the real mutation happens later in execute_pending_action
on a confirming turn. Every tool is bound to the sender's tenant and, for
writes, re-checks analyst-or-above.

All returned strings are end-user copy (Spanish), like the existing
notifications/whatsapp.py message builders.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.db.connection import query, query_one

_ANALYST_ROLES = ("admin", "analyst")


class ToolError(Exception):
    """Carries an end-user (Spanish) message explaining why a tool could not run."""


@dataclass
class ToolContext:
    tenant_id: str
    user_id: str
    role: str

    @property
    def is_analyst_or_above(self) -> bool:
        return self.role in _ANALYST_ROLES


# ── Query tools (read-only) ──────────────────────────────────────────────────

def semaphore_status(ctx: ToolContext, args: dict) -> str:
    from backend.inventory import service as inv_svc
    sess = inv_svc.get_latest_completed_session(ctx.tenant_id)
    if not sess:
        return "Aún no hay un análisis de inventario listo. Sube tus ventas y entrena un modelo primero."
    briefing = inv_svc.get_morning_briefing(ctx.tenant_id, sess["session_id"])
    risks = briefing.get("risks", []) or []
    warnings = briefing.get("warnings", []) or []
    overstock = briefing.get("overstocked", []) or []
    lines = [
        f"🔴 {len(risks)} para pedir ya · 🟡 {len(warnings)} por reabastecer · 🟢 {len(overstock)} con sobrestock",
    ]
    for i in risks[:5]:
        cov = i.get("coverage_days")
        cov_s = f"{cov:.0f}d" if cov is not None else "—"
        qty = i.get("recommended_qty")
        qty_s = f" · pedir {qty:,.0f}" if qty else ""
        lines.append(f"  • {i.get('display_name') or i.get('sku')} ({cov_s}{qty_s})")
    return "\n".join(lines)


def list_pending_pos(ctx: ToolContext, args: dict) -> str:
    from backend.inventory.roi_service import get_po_history, format_po_number
    history = get_po_history(ctx.tenant_id, limit=50)
    pending = [p for p in history if p.get("reception_status") in ("pending", "partial")]
    if not pending:
        return "No tienes órdenes de compra pendientes de recibir."
    lines = ["Órdenes pendientes:"]
    for p in pending[:10]:
        ref = format_po_number(p.get("po_number"), p["id"])
        total = p.get("total_value")
        total_s = f" · ${total:,.0f}" if total else ""
        lines.append(f"  • {ref} — {p.get('sku_count', 0)} SKU{total_s} ({p.get('reception_status')})")
    return "\n".join(lines)


def forecast_summary(ctx: ToolContext, args: dict) -> str:
    from backend.inventory import service as inv_svc
    from backend.db import session_store
    sku = (args or {}).get("sku")
    if not sku:
        return "¿De qué SKU quieres el pronóstico? Indícame el código."
    sess = inv_svc.get_latest_completed_session(ctx.tenant_id)
    if not sess:
        return "Aún no hay pronósticos listos para esta cuenta."
    forecasts = session_store.get_forecasts(ctx.tenant_id, sess["session_id"]) or {}
    models = forecasts.get(str(sku))
    if not isinstance(models, dict) or not models:
        return f"No encontré pronóstico para el SKU {sku}."
    # Take the first model's near-term curve.
    series = next(iter(models.values()))
    pts = (series.get("forecast") or []) if isinstance(series, dict) else []
    values = [p["value"] for p in pts if isinstance(p, dict) and p.get("value") is not None]
    if len(values) < 2:
        return f"El pronóstico del SKU {sku} aún no tiene suficientes puntos."
    trend = "sube" if values[-1] > values[0] * 1.05 else ("baja" if values[-1] < values[0] * 0.95 else "estable")
    avg = sum(values) / len(values)
    return (f"Pronóstico SKU {sku}: {len(values)} periodos, promedio {avg:.1f} uds/periodo, "
            f"tendencia {trend} (de {values[0]:.1f} a {values[-1]:.1f}).")


# ── Write proposals (NO mutation) ────────────────────────────────────────────

def _money(v) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


def propose_approve_po(ctx: ToolContext, args: dict) -> dict:
    from backend.inventory import reception_service as rec_svc
    from backend.inventory.roi_service import format_po_number
    po_log_id = (args or {}).get("po_log_id")
    if not po_log_id:
        raise ToolError("Indícame el número de la orden de compra a aprobar.")
    po = rec_svc.get_po(ctx.tenant_id, po_log_id)
    if not po:
        raise ToolError("No encontré esa orden de compra.")
    if po.get("sent_at") is not None:
        raise ToolError("Esa orden ya fue enviada.")
    items = rec_svc.get_po_items(ctx.tenant_id, po_log_id)
    ordered = [i for i in items if i["status"] in ("approved", "modified")]
    suppliers = sorted({(i.get("supplier") or "").strip() for i in ordered if (i.get("supplier") or "").strip()})
    ref = format_po_number(po.get("po_number"), po_log_id)
    summary = (f"Aprobar y enviar la orden {ref} — {len(suppliers)} proveedor(es), "
               f"total {_money(po.get('total_value'))}. ¿Confirmas? (responde SÍ)")
    return {"type": "approve_po", "po_log_id": po_log_id, "summary": summary}


def propose_reception(ctx: ToolContext, args: dict) -> dict:
    from backend.inventory import reception_service as rec_svc
    from backend.inventory.roi_service import format_po_number
    args = args or {}
    sku = str(args.get("sku") or "").strip()
    warehouse = (args.get("warehouse") or "").strip() or None
    try:
        quantity = float(args.get("quantity"))
    except (TypeError, ValueError):
        raise ToolError("¿Cuántas unidades llegaron? Indícame la cantidad.")
    if not sku:
        raise ToolError("¿De qué SKU es la recepción?")
    if quantity <= 0:
        raise ToolError("La cantidad recibida debe ser mayor a cero.")

    # Find the most recent pending/partial PO whose ordered line carries this
    # SKU (and warehouse, when given).
    rows = query(
        """SELECT pol.id AS po_log_id, pol.po_number, pol.generated_at,
                  poi.warehouse
           FROM inventory_po_items poi
           JOIN inventory_po_log pol ON pol.id = poi.po_log_id
           WHERE poi.tenant_id = %s AND poi.sku = %s
             AND poi.status IN ('approved', 'modified')
             AND pol.reception_status IN ('pending', 'partial')
           ORDER BY pol.generated_at DESC""",
        (ctx.tenant_id, sku),
    )
    if warehouse:
        rows = [r for r in rows if (r.get("warehouse") or "principal") == warehouse]
    if not rows:
        raise ToolError(f"No encontré una orden pendiente con el SKU {sku}"
                        + (f" en {warehouse}." if warehouse else "."))
    chosen = rows[0]
    wh = warehouse or (chosen.get("warehouse") or "principal")
    ref = format_po_number(chosen.get("po_number"), chosen["po_log_id"])
    summary = (f"Registrar recepción de {quantity:g} uds de {sku} en {wh} "
               f"(orden {ref}). ¿Confirmas? (responde SÍ)")
    return {"type": "register_reception", "po_log_id": chosen["po_log_id"],
            "sku": sku, "warehouse": wh, "quantity": quantity, "summary": summary}


# ── Confirmed execution (mutates) ────────────────────────────────────────────

def execute_pending_action(ctx: ToolContext, action: dict) -> str:
    if not ctx.is_analyst_or_above:
        raise ToolError("Tu perfil es de solo lectura; no puedes ejecutar esta acción.")
    from backend.inventory import reception_service as rec_svc
    from backend.inventory.roi_service import format_po_number

    atype = (action or {}).get("type")
    if atype == "approve_po":
        po_log_id = action["po_log_id"]
        po = rec_svc.get_po(ctx.tenant_id, po_log_id)
        if not po:
            raise ToolError("No encontré esa orden de compra.")
        rec_svc.mark_po_sent(ctx.tenant_id, po_log_id)
        ref = format_po_number(po.get("po_number"), po_log_id)
        return f"Listo ✅ Orden {ref} aprobada y marcada como enviada."

    if atype == "register_reception":
        po_log_id = action["po_log_id"]
        sku = action["sku"]
        qty = float(action["quantity"])
        try:
            rec_svc.receive_po(
                ctx.tenant_id, po_log_id, ctx.user_id,
                lines=[{"sku": sku, "received_qty": qty}],
            )
        except ValueError as e:
            raise ToolError(str(e))
        return f"Listo ✅ Registré {qty:g} uds de {sku} en {action.get('warehouse')}."

    raise ToolError("Acción no reconocida.")


# ── Registries + specs for the agent's routing prompt ────────────────────────

QUERY_TOOLS = {
    "semaphore_status": semaphore_status,
    "list_pending_pos": list_pending_pos,
    "forecast_summary": forecast_summary,
}

WRITE_TOOLS = {
    "approve_po": propose_approve_po,
    "register_reception": propose_reception,
}

TOOL_SPECS = [
    {"name": "semaphore_status", "kind": "query",
     "description": "Estado del semáforo de inventario: qué pedir ya, qué reabastecer, sobrestock.",
     "args": {}},
    {"name": "list_pending_pos", "kind": "query",
     "description": "Lista las órdenes de compra pendientes de recibir.",
     "args": {}},
    {"name": "forecast_summary", "kind": "query",
     "description": "Resumen del pronóstico de demanda de un SKU.",
     "args": {"sku": "código del SKU"}},
    {"name": "approve_po", "kind": "write",
     "description": "Aprobar y enviar una orden de compra existente.",
     "args": {"po_log_id": "id o número de la orden"}},
    {"name": "register_reception", "kind": "write",
     "description": "Registrar la recepción de mercancía de una orden.",
     "args": {"sku": "código del SKU", "warehouse": "bodega (opcional)", "quantity": "unidades recibidas"}},
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_whatsapp_tools.py -q`
Expected: PASS (8 tests). If `completed_session` fixture's forecasts shape differs, `test_semaphore_status_...` only asserts a non-empty string + no PO mutation, so it holds regardless.

- [ ] **Step 5: Commit**

```bash
git add backend/whatsapp/tools.py backend/tests/test_whatsapp_tools.py
git commit -m "feat(whatsapp): query/write tools with confirmation-gated executor"
```

---

## Task 6: `agent.py` — LLM tool routing + deterministic confirmation gate

**Files:**
- Create: `backend/whatsapp/agent.py`
- Test: `backend/tests/test_whatsapp_agent.py`

**Interfaces:**
- Consumes: `backend.ai.local_llm.get_local_llm_client`; `backend.whatsapp.tools` (`ToolContext`, `QUERY_TOOLS`, `WRITE_TOOLS`, `TOOL_SPECS`, `execute_pending_action`, `ToolError`).
- Produces:
  - `is_affirmative(text: str) -> bool` — deterministic yes-detector for Spanish/English confirmations (`sí`, `si`, `confirmo`, `dale`, `ok`, `listo`, `aprobar`, `de acuerdo`, `yes`, `y`, `correcto`), case/accent-insensitive; treats a message that also contains a clear negation (`no`, `cancela`, `mejor no`) as NOT affirmative.
  - `run_turn(ctx, incoming_text, state) -> tuple[str, list[dict], dict | None]` — pure orchestration given the loaded `state` dict (`{"history", "pending_action", ...}`). Returns `(reply_text, new_history, new_pending_action)`. Behavior:
    1. If `state["pending_action"]` is set:
       - If `is_affirmative(incoming_text)` → call `execute_pending_action`; reply is its result (or `ToolError` message); clears pending action.
       - Else → discard the pending action (reply is produced by falling through to fresh-intent handling on this same message).
    2. Fresh-intent handling: call `_route(ctx, incoming_text, history)` → a decision `{"tool": name|None, "args": {...}, "reply": str|None}` parsed from ONE LLM completion.
       - `tool` in `QUERY_TOOLS` → dispatch inline; reply is the tool's string.
       - `tool` in `WRITE_TOOLS`:
         - if `not ctx.is_analyst_or_above` → reply a read-only explanation, no pending action.
         - else build the proposal (`WRITE_TOOLS[tool](ctx, args)`); reply is `proposal["summary"]`; new pending action = the proposal.
         - on `ToolError` → reply its message, no pending action.
       - else (no tool) → reply is `decision["reply"]` (LLM free-text) or a default help line.
    3. Appends `{"role":"user","content":incoming_text}` and `{"role":"assistant","content":reply}` to history.
    4. Any exception from the LLM/tool routing → reply an apology, no pending action executed/created (safety: an errored/ambiguous turn never mutates).
  - `_route(ctx, text, history) -> dict` — builds the system prompt from `TOOL_SPECS`, calls `get_local_llm_client().messages.create(...)`, parses the JSON object from `resp.content[0].text` (tolerates surrounding prose/code fences). On parse failure returns `{"tool": None, "args": {}, "reply": None}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_whatsapp_agent.py`:

```python
"""Agent: routing + the two-step confirmation gate. LLM is mocked."""
import json
from unittest import mock

import pytest

from backend.db.connection import query_one, execute
from backend.whatsapp import agent
from backend.whatsapp.tools import ToolContext


def _ctx(reg, role="admin"):
    return ToolContext(tenant_id=reg["tenant"]["id"], user_id=reg["user"]["id"], role=role)


def _seed_po(tid, *, sku="SKU1", warehouse="bodega norte", qty=200):
    row = query_one(
        """INSERT INTO inventory_po_log
               (tenant_id, session_id, sku_count, total_units, total_value, reception_status)
           VALUES (%s, 'sess-x', 1, %s, %s, 'pending') RETURNING id""",
        (tid, qty, qty * 10),
    )
    po_id = row["id"]
    execute(
        """INSERT INTO inventory_po_items
               (po_log_id, tenant_id, sku, display_name, supplier, status,
                recommended_qty, final_qty, unit_cost, warehouse)
           VALUES (%s, %s, %s, %s, 'Proveedor A', 'approved', %s, %s, 10, %s)""",
        (po_id, tid, sku, sku, qty, qty, warehouse),
    )
    return po_id


class _FakeLLM:
    """Returns a queued JSON string per messages.create call."""
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.messages = self

    def create(self, *a, **k):
        text = self._payloads.pop(0)
        block = mock.Mock()
        block.text = text
        resp = mock.Mock()
        resp.content = [block]
        resp.usage = mock.Mock(input_tokens=1, output_tokens=1)
        return resp


def test_is_affirmative():
    assert agent.is_affirmative("sí")
    assert agent.is_affirmative("Si, confirmo")
    assert agent.is_affirmative("dale")
    assert not agent.is_affirmative("no")
    assert not agent.is_affirmative("mejor no")
    assert not agent.is_affirmative("cuánto stock tengo?")


def test_query_turn_dispatches_tool(registered_user):
    ctx = _ctx(registered_user)
    _seed_po(ctx.tenant_id, sku="A")
    state = {"history": [], "pending_action": None}
    fake = _FakeLLM([json.dumps({"tool": "list_pending_pos", "args": {}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        reply, history, pending = agent.run_turn(ctx, "¿qué órdenes tengo pendientes?", state)
    assert "OC" in reply or "pendiente" in reply.lower()
    assert pending is None
    assert history[-1]["role"] == "assistant"


def test_write_proposal_turn_does_not_mutate(registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id)
    state = {"history": [], "pending_action": None}
    fake = _FakeLLM([json.dumps({"tool": "approve_po", "args": {"po_log_id": po_id}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        reply, history, pending = agent.run_turn(ctx, f"aprueba la orden {po_id}", state)
    assert pending is not None and pending["type"] == "approve_po"
    assert "confirm" in reply.lower()
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is None  # proposal turn mutated nothing


def test_confirmation_turn_executes_without_llm(registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id)
    state = {"history": [], "pending_action": {"type": "approve_po", "po_log_id": po_id}}
    # No LLM patch: confirmation must NOT call the LLM. If it does, this errors.
    reply, history, pending = agent.run_turn(ctx, "sí, confirmo", state)
    assert pending is None
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is not None


def test_non_confirming_message_discards_pending(registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id)
    state = {"history": [], "pending_action": {"type": "approve_po", "po_log_id": po_id}}
    fake = _FakeLLM([json.dumps({"tool": "semaphore_status", "args": {}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        reply, history, pending = agent.run_turn(ctx, "no, mejor muéstrame el semáforo", state)
    # Pending discarded, nothing approved.
    assert pending is None
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is None


def test_reception_full_cycle_credits_warehouse(registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id, sku="SKU1", warehouse="bodega norte", qty=200)
    # Turn 1: propose.
    state = {"history": [], "pending_action": None}
    fake = _FakeLLM([json.dumps({"tool": "register_reception",
                                 "args": {"sku": "SKU1", "warehouse": "bodega norte", "quantity": 200}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        reply, history, pending = agent.run_turn(ctx, "llegaron 200 de SKU1 a bodega norte", state)
    assert pending["type"] == "register_reception"
    stock = query_one("SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku='SKU1' AND warehouse='bodega norte'",
                      (ctx.tenant_id,))
    assert stock is None  # not yet
    # Turn 2: confirm (no LLM).
    state2 = {"history": history, "pending_action": pending}
    reply2, history2, pending2 = agent.run_turn(ctx, "sí", state2)
    assert pending2 is None
    stock = query_one("SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku='SKU1' AND warehouse='bodega norte'",
                      (ctx.tenant_id,))
    assert stock is not None and float(stock["current_stock"]) == 200.0


def test_viewer_write_intent_denied(registered_user):
    ctx = _ctx(registered_user, role="viewer")
    po_id = _seed_po(ctx.tenant_id)
    state = {"history": [], "pending_action": None}
    fake = _FakeLLM([json.dumps({"tool": "approve_po", "args": {"po_log_id": po_id}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        reply, history, pending = agent.run_turn(ctx, f"aprueba {po_id}", state)
    assert pending is None
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is None


def test_llm_failure_is_safe(registered_user):
    ctx = _ctx(registered_user)
    state = {"history": [], "pending_action": None}

    class _Boom:
        messages = None
        def create(self, *a, **k):
            raise RuntimeError("llm down")
    boom = _Boom(); boom.messages = boom
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=boom):
        reply, history, pending = agent.run_turn(ctx, "hola", state)
    assert isinstance(reply, str) and len(reply) > 0
    assert pending is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_whatsapp_agent.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `backend/whatsapp/agent.py`:

```python
"""
The WhatsApp tool-calling agent. One LLM completion per non-confirming turn
routes the message to a query tool, a write-tool proposal, or a free-text
reply. The confirmation gate is system-controlled: a turn that confirms a
stored pending_action executes it WITHOUT calling the LLM; any non-affirmative
message discards the pending action and is handled as a fresh intent.

The LLM is used only for intent routing / small talk; it never touches the DB
and never decides whether a write executes.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata

from backend.ai.local_llm import get_local_llm_client
from backend.whatsapp import tools as wt
from backend.whatsapp.tools import ToolContext, ToolError

log = logging.getLogger(__name__)

MAX_TOKENS = 400

_AFFIRMATIVE = {
    "si", "sisi", "s", "y", "yes", "ok", "oka", "okay", "dale", "listo",
    "confirmo", "confirmar", "confirmado", "aprobar", "apruebo", "correcto",
    "deacuerdo", "vale", "hazlo", "adelante", "sip",
}
_NEGATIVE = {"no", "cancela", "cancelar", "mejorno", "nop", "negativo"}

_HELP = ("Puedo ayudarte con tu inventario: pregúntame por el semáforo "
         "(qué pedir), tus órdenes pendientes o el pronóstico de un SKU. "
         "También puedo aprobar una orden o registrar una recepción.")

_APOLOGY = "Perdón, tuve un problema procesando tu mensaje. ¿Puedes intentarlo de nuevo?"


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _normalize(text: str) -> str:
    return _strip_accents((text or "").strip().lower())


def is_affirmative(text: str) -> bool:
    norm = _normalize(text)
    words = set(re.findall(r"[a-z]+", norm))
    if words & _NEGATIVE:
        return False
    if words & _AFFIRMATIVE:
        return True
    # A bare "si ..." start also counts (e.g. "si confirmo la orden").
    return norm.split(" ", 1)[0] in _AFFIRMATIVE if norm else False


def _system_prompt() -> str:
    lines = [
        "Eres el asistente de inventario de Faro por WhatsApp. Decide qué "
        "herramienta usar para responder al usuario. Responde SOLO con un "
        "objeto JSON, sin texto adicional.",
        'Formato: {"tool": <nombre|null>, "args": {...}, "reply": <texto|null>}.',
        "Si ninguna herramienta aplica, usa tool=null y escribe una respuesta breve en 'reply'.",
        "Herramientas disponibles:",
    ]
    for spec in wt.TOOL_SPECS:
        lines.append(f'- {spec["name"]} ({spec["kind"]}): {spec["description"]} args={spec["args"]}')
    return "\n".join(lines)


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _route(ctx: ToolContext, text: str, history: list[dict]) -> dict:
    client = get_local_llm_client()
    messages = [{"role": t["role"], "content": t["content"]} for t in (history or [])[-6:]]
    messages.append({"role": "user", "content": text})
    resp = client.messages.create(
        model="whatsapp-agent",
        max_tokens=MAX_TOKENS,
        system=_system_prompt(),
        messages=messages,
    )
    raw = resp.content[0].text if resp and resp.content else ""
    m = _JSON_RE.search(raw or "")
    if not m:
        return {"tool": None, "args": {}, "reply": None}
    try:
        obj = json.loads(m.group(0))
    except (ValueError, TypeError):
        return {"tool": None, "args": {}, "reply": None}
    return {
        "tool": obj.get("tool"),
        "args": obj.get("args") or {},
        "reply": obj.get("reply"),
    }


def run_turn(ctx: ToolContext, incoming_text: str, state: dict):
    """
    Returns (reply_text, new_history, new_pending_action). Pure orchestration;
    the caller loads `state` and persists the returned history/pending action.
    """
    history = list(state.get("history") or [])
    pending = state.get("pending_action")

    reply, new_pending = _handle(ctx, incoming_text, history, pending)

    history = history + [
        {"role": "user", "content": incoming_text},
        {"role": "assistant", "content": reply},
    ]
    return reply, history, new_pending


def _handle(ctx, incoming_text, history, pending):
    # 1. Confirmation gate — system-controlled, no LLM call.
    if pending:
        if is_affirmative(incoming_text):
            try:
                return wt.execute_pending_action(ctx, pending), None
            except ToolError as e:
                return str(e), None
            except Exception:  # noqa: BLE001 — never leave a half-applied write ambiguous
                log.exception("[whatsapp] execute_pending_action failed")
                return _APOLOGY, None
        # Non-confirming: discard and treat as a fresh intent below.
        pending = None

    # 2. Fresh intent routing (one LLM completion).
    try:
        decision = _route(ctx, incoming_text, history)
    except Exception:  # noqa: BLE001 — LLM/timeout: apologize, mutate nothing
        log.exception("[whatsapp] routing failed")
        return _APOLOGY, None

    tool = decision.get("tool")
    args = decision.get("args") or {}

    if tool in wt.QUERY_TOOLS:
        try:
            return wt.QUERY_TOOLS[tool](ctx, args), None
        except ToolError as e:
            return str(e), None
        except Exception:  # noqa: BLE001
            log.exception("[whatsapp] query tool failed: %s", tool)
            return _APOLOGY, None

    if tool in wt.WRITE_TOOLS:
        if not ctx.is_analyst_or_above:
            return ("Tu perfil es de solo lectura, así que no puedo ejecutar acciones. "
                    "Puedo darte información de inventario si quieres."), None
        try:
            proposal = wt.WRITE_TOOLS[tool](ctx, args)
        except ToolError as e:
            return str(e), None
        return proposal["summary"], proposal

    # 3. No tool — free-text reply from the LLM, or default help.
    reply = decision.get("reply")
    return (reply or _HELP), None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_whatsapp_agent.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/whatsapp/agent.py backend/tests/test_whatsapp_agent.py
git commit -m "feat(whatsapp): tool-routing agent with system-controlled confirmation gate"
```

---

## Task 7: `whatsapp.py` webhook — signature, idempotency, rate-limit, orchestration

**Files:**
- Create: `backend/api/v1/whatsapp.py`
- Modify: `backend/main.py` (import + register router)
- Test: `backend/tests/test_whatsapp_webhook.py`

**Interfaces:**
- Consumes: `backend.whatsapp.identity`, `backend.whatsapp.conversation_store`, `backend.whatsapp.agent`, `backend.notifications.whatsapp.send_whatsapp`, `backend.config.settings`, `backend.db.connection`.
- Produces HTTP:
  - `POST /api/v1/whatsapp/inbound` (form-encoded, no auth dependency — Twilio calls it). Flow: verify `X-Twilio-Signature` (invalid/missing → 403); normalize `From`; idempotency by `MessageSid`; `resolve_sender` (unknown/unverified → polite reject, 200); per-number rate limit (over → friendly wait, 200, no LLM); load state; `agent.run_turn`; save state; `send_whatsapp(reply)`; 200.
  - `compute_twilio_signature(url: str, params: dict, auth_token: str) -> str` — Twilio's algorithm (concat URL + sorted key+value, HMAC-SHA1, base64). Exposed at module level so tests can produce a valid signature.
  - `verify_twilio_signature(url, params, signature, auth_token) -> bool` — constant-time compare; returns False if no token configured.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_whatsapp_webhook.py`:

```python
"""Inbound webhook: signature, identity, idempotency, rate-limit, and the
end-to-end confirmation gate over HTTP. LLM is mocked; outbound send is a
no-op without TWILIO creds."""
import json
from unittest import mock

import pytest

from backend.config import settings
from backend.db.connection import query_one, execute
from backend.api.v1 import whatsapp as wh


AUTH_TOKEN = "test_twilio_token"
INBOUND_URL = "http://testserver/api/v1/whatsapp/inbound"


@pytest.fixture
def twilio_token(monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", AUTH_TOKEN)
    return AUTH_TOKEN


def _verified_number(reg, number, role=None):
    uid = reg["user"]["id"]
    if role:
        execute("UPDATE users SET role = %s WHERE id = %s", (role, uid))
    execute("UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = NOW() WHERE id = %s",
            (number, uid))
    return number


def _post(client, params, token=AUTH_TOKEN, sign=True):
    sig = wh.compute_twilio_signature(INBOUND_URL, params, token) if sign else "bad"
    return client.post("/api/v1/whatsapp/inbound", data=params,
                       headers={"X-Twilio-Signature": sig})


def _seed_po(tid, *, sku="SKU1", warehouse="bodega norte", qty=200):
    row = query_one(
        """INSERT INTO inventory_po_log
               (tenant_id, session_id, sku_count, total_units, total_value, reception_status)
           VALUES (%s, 'sess-x', 1, %s, %s, 'pending') RETURNING id""",
        (tid, qty, qty * 10),
    )
    po_id = row["id"]
    execute(
        """INSERT INTO inventory_po_items
               (po_log_id, tenant_id, sku, display_name, supplier, status,
                recommended_qty, final_qty, unit_cost, warehouse)
           VALUES (%s, %s, %s, %s, 'Proveedor A', 'approved', %s, %s, 10, %s)""",
        (po_id, tid, sku, sku, qty, qty, warehouse),
    )
    return po_id


class _FakeLLM:
    def __init__(self, payloads):
        self._payloads = list(payloads); self.messages = self
    def create(self, *a, **k):
        text = self._payloads.pop(0)
        blk = mock.Mock(); blk.text = text
        r = mock.Mock(); r.content = [blk]; r.usage = mock.Mock(input_tokens=1, output_tokens=1)
        return r


def test_invalid_signature_403(client, twilio_token, registered_user):
    _verified_number(registered_user, "+573001110000")
    resp = _post(client, {"From": "whatsapp:+573001110000", "Body": "hola", "MessageSid": "SM1"}, sign=False)
    assert resp.status_code == 403


def test_valid_signature_unknown_number_polite_200(client, twilio_token):
    resp = _post(client, {"From": "whatsapp:+59999999999", "Body": "hola", "MessageSid": "SMx"})
    assert resp.status_code == 200


def test_unverified_number_rejected_no_state(client, twilio_token, registered_user):
    uid = registered_user["user"]["id"]
    execute("UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = NULL WHERE id = %s",
            ("+573002220000", uid))
    resp = _post(client, {"From": "whatsapp:+573002220000", "Body": "hola", "MessageSid": "SMu"})
    assert resp.status_code == 200
    row = query_one("SELECT COUNT(*) AS n FROM whatsapp_conversations WHERE user_id = %s", (uid,))
    assert row["n"] == 0  # no conversation created for an unverified sender


def test_query_turn_over_http_no_mutation(client, twilio_token, registered_user):
    num = _verified_number(registered_user, "+573003330000")
    _seed_po(registered_user["tenant"]["id"], sku="A")
    fake = _FakeLLM([json.dumps({"tool": "list_pending_pos", "args": {}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        resp = _post(client, {"From": f"whatsapp:{num}", "Body": "órdenes?", "MessageSid": "SMq"})
    assert resp.status_code == 200


def test_confirmation_gate_over_http(client, twilio_token, registered_user):
    num = _verified_number(registered_user, "+573004440000", role="admin")
    po_id = _seed_po(registered_user["tenant"]["id"])
    fake = _FakeLLM([json.dumps({"tool": "approve_po", "args": {"po_log_id": po_id}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        r1 = _post(client, {"From": f"whatsapp:{num}", "Body": f"aprueba {po_id}", "MessageSid": "SM-p"})
    assert r1.status_code == 200
    # Proposal turn mutated nothing.
    assert query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))["sent_at"] is None
    # Confirm turn — no LLM needed.
    r2 = _post(client, {"From": f"whatsapp:{num}", "Body": "sí", "MessageSid": "SM-c"})
    assert r2.status_code == 200
    assert query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))["sent_at"] is not None


def test_idempotency_same_sid_single_execution(client, twilio_token, registered_user):
    num = _verified_number(registered_user, "+573005550000", role="admin")
    po_id = _seed_po(registered_user["tenant"]["id"])
    # Set up a pending approve action directly in the store.
    from backend.whatsapp import conversation_store as cs
    cs.save(registered_user["tenant"]["id"], registered_user["user"]["id"], num,
            history=[], pending_action={"type": "approve_po", "po_log_id": po_id},
            last_message_sid="SM-prev")
    # First confirm executes.
    r1 = _post(client, {"From": f"whatsapp:{num}", "Body": "sí", "MessageSid": "SM-confirm"})
    assert r1.status_code == 200
    assert query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))["sent_at"] is not None
    sent_first = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))["sent_at"]
    # Twilio retry with the SAME MessageSid must be a no-op (pending already cleared,
    # and the dedupe short-circuits before any processing).
    r2 = _post(client, {"From": f"whatsapp:{num}", "Body": "sí", "MessageSid": "SM-confirm"})
    assert r2.status_code == 200
    sent_second = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))["sent_at"]
    assert sent_first == sent_second  # not re-approved / no second write


def test_rate_limit_blocks_without_llm(client, twilio_token, registered_user, monkeypatch):
    # Rate limiting is bypassed in testing_mode; force it on for this test.
    monkeypatch.setattr(settings, "testing_mode", False)
    num = _verified_number(registered_user, "+573006660000")
    llm = mock.Mock(side_effect=AssertionError("LLM must not be called when rate-limited"))
    # Exhaust the limit; the last call must be blocked with a friendly 200 and no LLM.
    last = None
    with mock.patch("backend.whatsapp.agent.get_local_llm_client") as get_llm:
        get_llm.return_value = _FakeLLM([json.dumps({"tool": None, "args": {}, "reply": "hola"})] * 100)
        for i in range(wh.RATE_LIMIT_MAX + 3):
            last = _post(client, {"From": f"whatsapp:{num}", "Body": "hola", "MessageSid": f"SM{i}"})
    assert last.status_code == 200


def test_viewer_denied_over_http(client, twilio_token, registered_user):
    num = _verified_number(registered_user, "+573007770000", role="viewer")
    po_id = _seed_po(registered_user["tenant"]["id"])
    fake = _FakeLLM([json.dumps({"tool": "approve_po", "args": {"po_log_id": po_id}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        resp = _post(client, {"From": f"whatsapp:{num}", "Body": f"aprueba {po_id}", "MessageSid": "SM-v"})
    assert resp.status_code == 200
    assert query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))["sent_at"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_whatsapp_webhook.py -q`
Expected: FAIL — `backend.api.v1.whatsapp` has no attribute `compute_twilio_signature` / router not registered (404).

- [ ] **Step 3: Write the implementation**

Create `backend/api/v1/whatsapp.py`:

```python
"""
Inbound Twilio WhatsApp webhook. HTTP + wiring only — no business logic:
verify the Twilio signature, dedupe by MessageSid, resolve the sender to a
verified user, rate-limit per number, run the tool-calling agent, persist
state, and reply via the existing outbound send_whatsapp().
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from fastapi import APIRouter, Request, Response

from backend.config import settings
from backend.db.connection import execute, query_one
from backend.notifications.whatsapp import send_whatsapp
from backend.whatsapp import agent, conversation_store as cs, identity
from backend.whatsapp.tools import ToolContext

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
log = logging.getLogger(__name__)

# Per-number cap: N inbound messages per rolling window (seconds).
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW_SECS = 60

_REJECT_UNKNOWN = (
    "Hola 👋 No reconozco este número. Vincula tu WhatsApp desde tu perfil en "
    "Faro para poder ayudarte por aquí."
)
_RATE_LIMITED = "Vas muy rápido 🙏 Espera un momento y vuelve a escribirme."


def compute_twilio_signature(url: str, params: dict, auth_token: str) -> str:
    """Twilio's request-signature algorithm: URL + sorted(key+value), HMAC-SHA1, base64."""
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params.keys()))
    digest = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify_twilio_signature(url: str, params: dict, signature: str, auth_token: str) -> bool:
    if not auth_token or not signature:
        return False
    expected = compute_twilio_signature(url, params, auth_token)
    return hmac.compare_digest(expected, signature)


def _rate_limited(phone: str) -> bool:
    """True if `phone` exceeded the window; otherwise records this hit. Bypassed
    in testing_mode (matches the auth rate-limit convention)."""
    if settings.testing_mode:
        return False
    key = f"wa:{phone}"
    try:
        execute(
            "DELETE FROM auth_rate_events WHERE key = %s AND created_at < NOW() - make_interval(secs => %s)",
            (key, RATE_LIMIT_WINDOW_SECS),
        )
        row = query_one("SELECT COUNT(*) AS n FROM auth_rate_events WHERE key = %s", (key,))
        if row and row["n"] >= RATE_LIMIT_MAX:
            return True
        execute("INSERT INTO auth_rate_events (key) VALUES (%s)", (key,))
        return False
    except Exception:  # noqa: BLE001 — a rate-limit store hiccup must not break inbound
        log.exception("[whatsapp] rate-limit check failed; allowing")
        return False


@router.post("/inbound")
async def inbound(request: Request):
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)

    # 1. Signature — invalid/missing → 403, no processing.
    if not verify_twilio_signature(url, params, signature, settings.twilio_auth_token):
        return Response(status_code=403)

    from_raw = params.get("From", "")
    body = params.get("Body", "") or ""
    message_sid = params.get("MessageSid", "") or ""
    phone = identity.normalize_phone(from_raw)

    # 3. Identity (idempotency needs the resolved user, so resolve first).
    sender = identity.resolve_sender(phone)
    if not sender:
        send_whatsapp(phone, _REJECT_UNKNOWN)
        return Response(status_code=200)

    ctx = ToolContext(tenant_id=sender["tenant_id"], user_id=sender["user_id"], role=sender["role"])

    # 2. Idempotency — a repeated MessageSid (Twilio retry) is a no-op.
    if message_sid and cs.is_duplicate(ctx.tenant_id, ctx.user_id, message_sid):
        return Response(status_code=200)

    # 4. Rate limit — over cap → friendly wait, no LLM call.
    if _rate_limited(phone):
        send_whatsapp(phone, _RATE_LIMITED)
        return Response(status_code=200)

    # 5-7. Load state, run agent, persist.
    state = cs.load(ctx.tenant_id, ctx.user_id)
    reply, history, pending = agent.run_turn(ctx, body, state)
    cs.save(ctx.tenant_id, ctx.user_id, phone, history, pending, message_sid)

    # 8. Reply via the existing outbound path (logged no-op without TWILIO creds).
    send_whatsapp(phone, reply)
    return Response(status_code=200)
```

In `backend/main.py`: add `whatsapp as whatsapp_router` to the `from backend.api.v1 import ...` line (line 21), and register it alongside the others (after the `inventory_router` include, around line 180):

```python
app.include_router(whatsapp_router.router, prefix=_PREFIX)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_whatsapp_webhook.py -q`
Expected: PASS (8 tests). Note on the idempotency test: the confirming turn clears `pending_action` and stores `last_message_sid="SM-confirm"`; the retry with the same SID short-circuits at `is_duplicate`, so no second `mark_po_sent`. Since `mark_po_sent` is itself first-write-wins on `sent_at IS NULL`, `sent_at` is unchanged either way — the assertion holds.

- [ ] **Step 5: Commit**

```bash
git add backend/api/v1/whatsapp.py backend/main.py backend/tests/test_whatsapp_webhook.py
git commit -m "feat(whatsapp): inbound webhook with signature, idempotency, rate-limit, agent"
```

---

## Task 8: Full-suite verification + pandas-boundary check

**Files:** none (verification only).

- [ ] **Step 1: Run the pandas-boundary guard**

Run: `cd backend && python -m pytest tests/test_no_pandas_in_backend.py -q`
Expected: PASS — `backend/whatsapp/` and `backend/api/v1/whatsapp.py` import no pandas/numpy.

- [ ] **Step 2: Run every new WhatsApp test together**

Run: `cd backend && python -m pytest tests/test_whatsapp_migrations.py tests/test_whatsapp_identity.py tests/test_whatsapp_number_linking.py tests/test_whatsapp_conversation_store.py tests/test_whatsapp_tools.py tests/test_whatsapp_agent.py tests/test_whatsapp_webhook.py -q`
Expected: PASS (all).

- [ ] **Step 3: Run the full backend suite**

Run: `cd backend && python -m pytest tests/ -q`
Expected: baseline `1232 passed, 19 skipped` PLUS the new tests, all passing. The only tolerated flake is `test_stress.py::test_login_responds_under_2s` — rerun once if it trips. Any real failure must be fixed before completion.

- [ ] **Step 4: Commit any final fixes**

```bash
git add -A
git commit -m "test(whatsapp): full-suite green for conversational bot"
```

---

## Self-Review Notes (traceability to spec)

- Two migrations (users.whatsapp_verified_at + whatsapp_conversations) → Task 1. `whatsapp_number` already existed; the spec's "unique when non-null" is the partial unique index in Task 1.
- Identity resolution + number linking/verification → Tasks 2 & 3.
- conversation_store (turns + one pending action + 24h prune + idempotency) → Task 4.
- Tool-calling agent with two-step confirmation gate → Task 6; the gate's "write stores pending_action and does NOT mutate; mutation only on confirming next message" is tested in Tasks 5 (`test_propose_*_does_not_mutate`, `test_execute_*`) and 6 (`test_write_proposal_turn_does_not_mutate`, `test_confirmation_turn_executes_without_llm`) and 7 (`test_confirmation_gate_over_http`).
- Query tools (semáforo/coverage/briefing, forecast, pending POs) + two write tools (approve PO, register reception) → Task 5.
- Inbound webhook: Twilio signature (403 on invalid), idempotency by MessageSid, per-number rate limit → Task 7.
- Testing checklist from the spec: signature (Task 7), identity (Tasks 2 & 7), query tools mutate nothing (Tasks 5 & 7), confirmation-gate write-not-executed-early (Tasks 5/6/7), permission pair viewer/analyst (Tasks 5/6/7), idempotency single execution (Task 7), rate limit no-LLM (Task 7), reception per-warehouse credit (Tasks 5/6).
