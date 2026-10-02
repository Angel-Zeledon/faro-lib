# StockAI's public API

> **2026-10-01 — the surface is no longer eight endpoints.** Every tenant-scoped
> action the app can take is callable with an API key, except auth/session,
> user and password management, instance configuration, tenant export/deletion,
> API-key management and person-scoped screens (inbox, assistant chats,
> preferences). The rule lives in `backend/api/public_surface.py`; the complete,
> generated reference is the landing page **/desarrolladores**
> (`Frontend/src/data/public-api.json`, regenerated with
> `python -m backend.scripts.export_public_api`). Keys are `read` (viewer) or
> `write` (analyst). Every key call that reaches an endpoint is metered per day
> in `api_usage_daily`; `GET /api/v1/api-keys/usage` (admin, JWT) reports it.
> The walkthrough below — the nightly ERP job — is still the shortest path.

## In short

StockAI does not want to be the system your inventory lives in. It wants to be the
layer that decides **what to buy** on top of the system you already have. This
API is that seam: your ERP pushes what it already knows and takes the decision
away, without anybody opening the application.

Six calls, in the order the work happens (the full list is eight — the other two
are further down; there is also an **MCP endpoint** for AI clients, at the end of
this page):

```
0. GET  /planning                      which session am I looking at  ← start here
1. POST /data-sources/{id}/file        last night's export
2. POST /sessions/{id}/train           retrain    (optional: see below)
3. GET  /inventory/status              the semáforo
4. GET  /inventory/morning-briefing    what to buy and why
5. POST /inventory/log-po              the order that went out
```

Do 0 first. `log-po` and the two training calls **require** a `session_id`, and
this is the only public endpoint that hands one out. On `status` and
`morning-briefing` it is optional: leave it out and they use the tenant's active
session — the same one `/planning` returns. Asking explicitly still has an
advantage: you know which run you are reading, instead of it changing under you
halfway through a cycle.

## The envelope

**Every** response is wrapped. What you want is always in `data`:

```json
{
  "success": true,
  "data": { "…yours…" },
  "meta": { "timestamp": "2026-08-11T16:45:10.783947+00:00" }
}
```

Errors carry `detail`, and **some** also carry `error_code` and `error_params`
at the top level, with no `data`. When `error_code` is there, branch on it and
never on the text of `detail`: that is written for people and gets rewritten.

It is worth knowing which ones do **not** carry it, because they are exactly the
three you will meet while integrating:

| Case | What actually arrives |
|---|---|
| `401` invalid or expired key | `detail` text only. No `error_code` |
| `429` over the limit | `detail` text only. No `error_code`; use the `Retry-After` header |
| `403` read-only key writing | `error_code = "role_not_permitted"` |
| `409` session not trainable | `error_code = "session_not_trainable"` |

For the first two, branch on the **HTTP status**, which is stable.

The fifth call is not optional even though it looks it: without it the order
does not exist as far as StockAI is concerned, and it is where the learning of each
supplier's real lead time comes from. It is the call that makes the next
forecast better than this one.

The second one usually **is** unnecessary. If the session has a schedule, StockAI
retrains by itself once the file has changed: uploading the export at night and
doing nothing else is the simplest integration that works.

**Base URL:** `https://<your-instance>/api/v1`
**Authentication:** `Authorization: Bearer sk_live_…`
**Limit:** 120 calls per minute per key, the same for everyone. Over it: `429`
with `Retry-After`.
**Included:** always. There is no tier that leaves it out.
**For an AI client:** the same key also works at `POST /api/v1/mcp` — see
"Connecting an AI client" below.

## Where to get your key

In the application: **Automatización → "API Keys" tab → "Generar key"**.

Two things are chosen at creation:

- **Name.** Use the name of the system that will use it ("nightly ERP"), not a
  person's. The key does not belong to whoever created it: it keeps working when
  that person leaves, and it does not gain permissions if they are promoted.
- **What it may do.** *Read only* is enough for the semáforo and the briefing.
  Uploading the export or logging orders needs **read and write** — which is
  what a real integration needs. It defaults to read-only, so you have to change
  it deliberately.

**The key is shown once.** No copy of the plaintext key is kept anywhere: we
store a hash and the last 4 characters, which are what let you recognise it in
the list. Not even we can show it to you again, so copy it then. If it is lost:
create another and revoke the previous one from that same screen, which also
shows when each one was last used.

No key can be an administrator. There is no way for an integration to delete the
tenant, create users or touch billing.

---

**Who it is for:** the system the customer already uses — their ERP, their POS,
their export script — so that data comes in and decisions go out without anybody
opening the application.

**What it promises:** the endpoints on this page do not change route, method or
response shape without notice. The list lives in
`backend/api/public_surface.py` and `backend/tests/test_public_api_surface.py`
fails if any of them stops existing, so this document cannot describe an API
that no longer runs.

**What it does NOT promise:** any other endpoint of the service. They are
reachable with a key, and they change when a screen changes — several changed in
the same week this was written. Building on them is building on something nobody
undertook to maintain.

## Authentication

```
Authorization: Bearer sk_live_xxxxxxxxxxxxxxxxxxxxxxxx
```

Three things worth knowing before you integrate:

- **A key is `viewer` or `analyst`, never administrator.** There is no way for a
  key to delete the tenant, create users or touch billing. Writing (uploading
  files, training, logging orders) needs `analyst`.
- **The key acts as itself**, not as the person who created it: internally the
  actor is `api_key:<id>`, so the integration keeps working when that person
  leaves the company and does not inherit permissions if they are promoted.

  **And it leaves a trail.** Every write by a key is recorded under the key's
  name — not the creator's — with the route, the outcome and the time. An
  attempt that reached execution and failed is recorded too, marked as an error.

  With one exception worth knowing: **what is refused at the door leaves no
  row.** A `401` (bad key), a `403` (read-only key trying to write) and a `429`
  are not recorded, because they are cut off before reaching the endpoint. So if
  your integration writes with a read-only key you will see nothing in the log —
  you will see the `403` on your side. Start there before suspecting the log.

  Reads are not recorded either: at 120 calls a minute they would bury what
  matters.

## Limits

The ceiling is **per key**, and there is one: **120 calls per minute**.

Over it: `429` with `Retry-After: 60`, and the message names the ceiling. The
120 are sized for an integration's real work — a nightly push and the polling
around it — not to be generous: if you need more, there is almost always a loop.
If your operation genuinely needs a different ceiling, talk to us; it is an
infrastructure number, not a feature we sell.

If the limiter cannot write, it **lets the call through**. A customer's sync
does not fall over because a counter is down.

## The jobs, in order

### 0. Know which session we are talking about

```http
GET /api/v1/planning
```

```json
{
  "period": "daily",
  "horizon": 14,
  "available_periods": ["daily", "weekly"],
  "max_horizon": 90,
  "period_source": "auto",
  "active_session_id": "sess_718a890426d4"
}
```

`active_session_id` is what goes in the calls that follow. **Do not hard-code it
in your configuration**: it changes when a new run trains, and it is precisely
the one the application is showing on screen. Asking for it every time is what
keeps your integration and the person looking at StockAI seeing the same thing.

`period` and `horizon` tell you at which granularity and over how many periods
what you read next was computed.

### 1. Get the data in

```http
POST /api/v1/data-sources/{source_id}/file
Content-Type: multipart/form-data
```

Replaces the file **in place**: the source keeps its id and its column mapping,
so the next training does not have to map anything again. The write is atomic —
a temporary file is written, its size checked, and only then swapped — so a full
disk does not leave half an export behind.

Accepts `.csv`, `.xlsx`, `.xls`, `.parquet` and `.json`. Reads `;` separators,
comma decimals, `dd/mm/yyyy` dates and `cp1252` files with accents.

To find out which `source_id` to replace:

```http
GET /api/v1/data-sources
```

### 2. Turn them into decisions

```http
POST /api/v1/sessions/{session_id}/train
GET  /api/v1/sessions/{session_id}/train/status
```

Training is asynchronous: the `POST` queues and the `GET` reports state.
`status` goes `QUEUED` → `RUNNING` → `COMPLETED` or `FAILED`; poll it every few
seconds until one of the last two.

```json
{
  "session_id": "sess_718a890426d4",
  "status": "COMPLETED",
  "job_id": "job_55989f9d2809",
  "job": { "created_by": "api_key:11c76a09-…", "started_at": "…" }
}
```

**Most of the time you do not need to call them.** If the session has a schedule
— Automatización → schedule — StockAI retrains by itself once the file has changed.
Uploading the export at night and letting the schedule do the rest is the
simplest integration that works, and it needs neither of these two calls.

### 3. Read the semáforo

```http
GET /api/v1/inventory/status?session_id={id}
```

Returns **every** product: there is no pagination. With large catalogues, filter
on the server rather than fetching everything and discarding:

```http
GET /api/v1/inventory/status?signal=PEDIR_YA
GET /api/v1/inventory/status?supplier=Andina
```

`session_id` is optional here; without it the active session is used.

Per product, the fields an integration needs, with their real names (verified
against a live response, not from memory):

| Field | What it is |
|---|---|
| `signal` | `PEDIR_YA`, `PEDIR_PRONTO`, `OK`, `SOBRESTOCK` or `SIN_DATOS` |
| `recommended_qty` | How much to order. **Not** `order_qty` or `suggested_qty` |
| `coverage_days` | Days of stock left at the forecast rate |
| `current_stock` | What is there today |
| `reorder_point` | The level at which ordering is due |
| `has_stock`, `has_forecast` | If either is missing, the above may come back empty |
| `explanation_code` + `explanation_params` | The why, **structured** |
| `lead_time_source`, `unit_cost_source`, `moq_source` | Where each assumption came from. **Five** values, not three |

From most yours to most ours:

| Value | What it means |
|---|---|
| `user` | You typed it into StockAI |
| `file` | It came in your own file |
| `supplier_rule` | It comes from a supplier rule you configured |
| `learned` | We learned it from your receptions. **Only appears in `lead_time_source`** |
| `default` | We did not have it and assumed |

Handle all five. An integration written only for `user`/`learned`/`default`
breaks on exactly `file` and `supplier_rule`, the two most frequent non-default
cases.

The three `*_source` fields are the part most worth using and the most ignored:
they separate a number you gave us from one we made up. An integration that
treats both the same will trust our assumptions as if they were its own data.

`explanation_code` is a stable code with its parameters alongside — branch on
that, never on the text.

Products with no recorded stock come back as `SIN_DATOS`, not as "no risk". That
distinction is deliberate: not knowing and being fine are not the same thing,
and an integration that confuses them will buy late.

### 4. Read what to buy

```http
GET /api/v1/inventory/morning-briefing?session_id={id}
```

The same thing ordered as a day's work, with the reason behind each
recommendation, the suggested supplier and the estimated value.

### 5. Close the loop

```http
POST /api/v1/inventory/log-po?session_id={id}
```

Records that the order went out. **Without this call the order does not exist
for StockAI**: reception is checked against it, and each supplier's real lead time
is learned from it. It is what makes the next forecast better than this one.

Send what the buyer decided per line, including the rejected ones — that is the
adoption signal:

```json
{
  "items": [
    { "sku": "ABC-1", "recommended_qty": 120, "final_qty": 100,
      "status": "modified", "unit_cost": 12.5, "supplier": "Andina" },
    { "sku": "XYZ-9", "recommended_qty": 40, "final_qty": 0, "status": "rejected" }
  ]
}
```

## The whole integration, at once

This is the entire nightly cron. There is nothing else to do.

```bash
#!/usr/bin/env bash
set -euo pipefail
API=https://your-instance/api/v1
KEY=$STOCKAI_API_KEY          # from your secret manager, not from the repository

# 0. The session the app is looking at. Asked for every time, never hard-coded.
SESSION=$(curl -sf "$API/planning" -H "Authorization: Bearer $KEY" \
          | jq -r '.data.active_session_id')

# 1. The export your ERP left last night. Replaced in place: same id, same
#    column mapping, no wizard.
curl -sf -X POST "$API/data-sources/$SOURCE_ID/file" \
     -H "Authorization: Bearer $KEY" -F "file=@/exports/sales.csv"

# 2. If the session has a schedule, skip this: StockAI retrains by itself.
curl -sf -X POST "$API/sessions/$SESSION/train" \
     -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d '{}'
# Wait for QUEUED *and* RUNNING. Right after posting the state is QUEUED, so a
# loop that only watches RUNNING exits on the first pass and step 3 reads the
# semáforo of the PREVIOUS run.
while :; do
  ST=$(curl -sf "$API/sessions/$SESSION/train/status" \
       -H "Authorization: Bearer $KEY" | jq -r '.data.status')
  case "$ST" in QUEUED|RUNNING) sleep 5 ;; *) break ;; esac
done

# 3. What to buy. Mind `*_source`: it separates what you gave us from what we
#    assumed.
curl -sf "$API/inventory/status?session_id=$SESSION" \
     -H "Authorization: Bearer $KEY" \
  | jq '.data.items[] | select(.signal == "PEDIR_YA")
        | {sku, recommended_qty, lead_time_source, unit_cost_source}'

# 4. And when you issue the order, tell us. Without this the order does not
#    exist for StockAI and your suppliers' real lead times are never learned.
curl -sf -X POST "$API/inventory/log-po?session_id=$SESSION" \
     -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
     -d '{"items":[{"sku":"ABC-1","recommended_qty":120,"final_qty":100,
                    "status":"modified","unit_cost":12.5}]}'
```

Two things this script does on purpose and worth copying: it asks for the
`session_id` on every run instead of fixing it, and it sends a `final_qty`
different from `recommended_qty` when the buyer adjusts — that is what measures
whether StockAI is helping.

## Errors

| Code | What happened |
|---|---|
| `401` | Unknown, revoked or expired key. Which one is deliberately not disclosed. |
| `403` | The key is `viewer` and the operation writes. |
| `429` | Over 120 per minute. Retry after `Retry-After`. |
| `409` | The session is not in a state that allows this (training one that is already running). |

Errors carry a stable `error_code` alongside the message. Branch on the code,
not the text: the text is written for people and gets rewritten.

## Connecting an AI client (MCP)

Everything above is for a system that runs on a schedule. If what you want is to
**ask** — "what should I buy today?" — the same key opens an MCP endpoint:

```
URL      https://<your-instance>/api/v1/mcp
Header   Authorization: Bearer sk_live_…
```

Five tools, all of them reads: `get_planning_context`, `get_morning_briefing`,
`get_inventory_status`, `list_data_sources`, `get_training_status`. A
**read-only** key is enough for all five.

**Nothing there writes, and that is deliberate.** An AI client cannot render
StockAI's confirmation card and cannot hold an undo token, so it is given nothing
that would need one — `log-po` in particular is irreversible (there is no "void
an order" anywhere in the product). Uploading, training and logging orders stay
on the REST calls above, where a person triggers them. A key with `analyst` role
changes nothing about this: the catalogue is the ceiling, not the role.

Two behaviours worth knowing, because they differ from the REST endpoints on
purpose:

- **`get_inventory_status` is capped and ordered by urgency.** The REST
  `/inventory/status` returns every product; an answer a model has to read
  cannot. It returns the most urgent rows first, sets `truncated`, reports
  `total_matching_items` — and the `summary` counts **all** of them, not the
  page. Filter with `signal` or `supplier` rather than raising `limit`.
- **A refusal comes back as a tool error the model can read**, not as a
  transport failure it never sees. "No completed session for this tenant yet"
  arrives as text with its `error_code`, so the assistant can say what to do.
- **An empty answer says which kind of empty it is.** If a filter matched
  nothing the response carries `empty_reason`; an unknown `signal` is refused
  with the valid ones named. Over REST an empty array is fine — a script author
  reads it and checks their spelling. A model reads it and tells somebody their
  stock is fine.

The server is stateless: no session to establish, nothing to keep alive. It
speaks the Streamable HTTP transport, answers `GET` with `405` (there is nothing
to stream), and accepts protocol revisions `2024-11-05` through `2025-11-25`.

For Claude Desktop and other clients that speak stdio instead of HTTP, copy
[`mcp_server/stockai_mcp.py`](../mcp_server/stockai_mcp.py) — standard library only,
no install — and set `STOCKAI_URL` and `STOCKAI_API_KEY`.
[`mcp_server/README.md`](../mcp_server/README.md) has the configuration block
and the troubleshooting table.

The same 120-per-minute ceiling applies; it is the same limiter, not a second
one to forget about.

## What is not there yet

Honesty up front, so nobody designs against something that does not exist:

- **Webhooks fire once, with no retry.** If your endpoint was down at that
  moment, the event is gone: for anything that cannot be lost, poll.
- **There is no sandbox.** You test against the real tenant.
- **There is no real versioning yet.** The `/api/v1` prefix exists, but the
  stability promise comes from this list, not from the number. If something ever
  has to break, there will be a `/v2` and notice.

## Running it on separate infrastructure

No second project and no second codebase: it is **the same image with different
configuration**.

```bash
PUBLIC_API_ONLY=true      # only the 10 public routes + /health
WORKER_ENABLED=false      # does not claim training jobs
SCHEDULER_ENABLED=false   # runs no crons — exactly ONE instance may have this
                          # true, or the daily emails go out twice
```

Started that way, the instance says so in its own log:

```
PUBLIC_API_ONLY: serving 15 of 277 routes (10 public endpoints + health)
Worker components: none (API-only instance)
```

**What it buys.** The promise stops being a list somebody has to respect and
becomes a wall: on that host the internal routes answer **404, not 403** — they
do not exist. The MCP endpoint is on the list, so an instance started this way
still serves AI clients. An integrator cannot reach an internal endpoint even by guessing,
and an endpoint written for a screen cannot receive machine traffic by accident.
The customer's integration also stops competing for CPU with the application,
and a UI deploy does not restart their connection.

**What it does NOT buy, said before somebody assumes it:**

- **It does not isolate the database.** Both instances share one Postgres. If
  the database goes down, the customer's integration and the application go down
  together. Splitting that is a much bigger decision and probably the wrong one
  for this product.
- **Migrations run at every boot, in every instance.** With two services coming
  up at once there is a race. They are idempotent (`IF NOT EXISTS`), so in
  practice it holds, but the correct shape is one instance running them while
  the others wait. **Unresolved.**
- **It changes neither authentication nor permissions.** Narrower reach, not a
  second security model: what was reachable there still is, with the same
  credentials and the same guards.

## How this was verified

Not written from memory. On 2026-08-11 the five jobs were walked with a real key
against the server, in this order: create the key → list sources → replace the
file with a new 360-row export (the source kept its id) → retrain and wait for
`COMPLETED` → read the semáforo → read the briefing → log the order, which
landed in the database as OC-000005 with 250 units and `modified_count = 1`.

The unhappy paths too: a `viewer` key reads (200) and does not write (403 on
both file upload and order logging), and an invented key gives 401.

What was **not** exercised against the server: the `429` rate limit — it is
covered by tests, including its mutation gate, but spending 120 calls a minute
against the development environment added nothing.

**The screen was walked** the same day, and found what was needed to make any of
this usable: the API Keys tab was **switched off** (`ENABLED['api-keys'] =
false`), so there was no way to obtain a key from the product — the API existed
for nobody. It also announced "coming soon, keys cannot authenticate yet", which
was true when written and false by then.

And a third, the one that really mattered: the screen **was not sending the
role**, so every key created there came out `viewer` in silence. A customer
would have generated their key, put it in their ERP and received a 403 on the
first attempt to upload the export, with nothing telling them why. It is chosen
now, and verified: a "read and write" key created from the screen → `201` on
logging an order, and `role = analyst` in the database.

**The rate limit, live.** An instance was brought up with `TESTING_MODE=false` —
the limiter is skipped in development — and the quota was spent with a real key:
**429 on call 121**, with `Retry-After: 60`. Exactly the design (120 pass, the
next does not).

**`PUBLIC_API_ONLY` mode, live.** Instance brought up with the flag: the public
routes answer 200 and the internal ones — `/auth/login`, `/users`, `/tenant`,
`/api-keys`, `/messages` — answer **404**. They are not mounted.

**The MCP endpoint, 2026-09-20.** 53 tests cover the handshake, version
negotiation, the read-only wall, truncation honesty, tenant scoping with a key
from another tenant, the shared rate limiter and malformed frames. Beyond the
tests — 42 on the endpoint and 11 driving the stdio adapter as a subprocess
against a real socket — the adapter was also driven against a running server the
way Claude Desktop drives it — a real signup, a real read-only key, `initialize` →
`notifications/initialized` → `tools/list` → two `tools/call`s — and then the
two failure paths a customer meets first: a wrong key and an unreachable
instance, both of which must produce a readable frame rather than a client that
hangs. `PUBLIC_API_ONLY` was brought up again to confirm MCP survives the
pruning: **15 of 277 routes**, which is where the figure above comes from.

What was **not** exercised against a real client: no commercial MCP client was
pointed at the endpoint. The protocol shape is checked against the 2025-11-25
specification and by the frame-level tests, not by Claude Desktop connecting.
