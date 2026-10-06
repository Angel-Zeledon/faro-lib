# Rewriting the HTTP API in Rust: the strangler-fig plan

Owner request, 2026-10-05: rewrite the API (everything that is not the
forecasting engine) in Rust, gradually. **The engine (`ForecastingCore`) and
the training worker stay in Python.** The Python FastAPI app keeps serving
every route that has not moved, and keeps the code of every route that has,
so any route can go back to Python in seconds.

Status: wave 0 (foundations) and the first wave-1 routes are implemented in
`backend-rs/`. **Nothing is deployed.** The compose and Caddy files under
`deploy/rust-api/` are disabled examples. Parity claims in this document are
limited to what `tests/contract/contract_test.py` showed (see "Results").

## 1. How requests reach which implementation

Today: browser -> public Caddy -> Next.js (`frontend:5000`) -> rewrite of
`/api/*` and `/api/v1/*` to `BACKEND_URL` -> Python (`api:8010`).
`BACKEND_URL` is baked into the frontend image **at build time**, and the
public Caddy never sees API paths as API paths. So the split cannot live in
the public Caddy. It lives in an **internal gateway**:

```
browser / integration
   -> Caddy (public, unchanged)
      -> Next.js (BACKEND_URL=http://api-gateway:8010, one rebuild, once)
         -> api-gateway (internal Caddy)
              routes.d/*.caddy matched   -> api-rs:8021 (Rust), failover to api:8010
              everything else            -> api:8010   (Python)
```

* **One file per migrated route group** in `routes.d/`, matched by method AND
  path (so one resource can be split: committed-demand writes go to Rust, its
  GET stays in Python).
* **Kill switch**: move the group's file to `routes.d/off/` and run
  `caddy reload` in the gateway container. Graceful, no restart of either API,
  no frontend rebuild. Everything back to Python = empty `routes.d/`.
* **Automatic failover**: each group lists `api-rs` first and `api` second
  with `lb_policy first` and an active health check on `/health`. If the Rust
  container is down, the same request is served by Python. Only dial failures
  are retried, so a write is never sent twice.
* **Rule that makes the above safe**: a route's Python code is deleted only
  after its group has run on Rust in production for a full release cycle with
  no kill-switch use. Until then Python is the fallback, so it must stay
  correct; the existing pytest suite keeps testing it.

Files: `deploy/rust-api/Caddyfile.gateway.example`,
`deploy/rust-api/routes.d/*.caddy.example`,
`deploy/rust-api/docker-compose.rust-api.example.yml`.

## 2. Inventory of the Python API (2026-10-05, `main`)

371 routes: 370 HTTP in 52 routers under `backend/api/v1/` plus one
websocket (`backend/api/ws/training_progress.py`). The largest routers are
inventory (87), configuration (30), forecasts (27), datasources (18) and
users (16).

Coupling legend: **C** imports `forecasting_core`, **P** pandas /
`backend.dataframes`, **W** `backend.workers`, **J** writes the `jobs` table,
**L** LLM, **N** email / WhatsApp / SMS, **S** files under `storage/`, **H**
outbound HTTP, **hub** = reaches `backend/inventory/service.py`. That module
pulls in notifications, the optimizer, planning, and through
`family_service` the job queue.

| Router (routes) | Auth | Key surface | Couplings |
|---|---|---|---|
| activity (2), preferences (2) | user | internal | DB only |
| alerts (4) | user | exposed | DB only (reads `activity_logs`) |
| currency (2), timezone (2) | user / admin | exposed | DB only |
| entitlements (2) | user, analyst | exposed (upgrade-request internal) | DB; upgrade-request is N |
| committed_demand (5) | user, analyst | internal | DB writes; the GET list is C + hub (risk verdict) |
| sessions (7), schedule (5) | user, analyst | exposed | DB only |
| webhooks (3) | user, analyst | exposed | DB, H (dispatch) |
| spike_edits (3) | user, analyst | internal | DB only |
| po_payments (2), po_cancellation (2) | analyst + PO warehouse guard | internal | DB only |
| api_keys (4) | user, analyst, admin | internal | DB only |
| audit (5) | admin, user | internal / exposed | DB (+ engine version string) |
| models (1) | public | internal | static list |
| tenant_data (2) | admin | internal | DB; ZIP export, whole-tenant erasure |
| auth (9), social_auth (7), sso (7) | mostly public | internal | DB, N, H (OAuth/OIDC) |
| users (16), messages (6) | user, admin, verified admin | internal | DB, N |
| po_approvals (10) | user, analyst, admin | internal | DB, N |
| trial (1), freshness (1) | public / user | internal / exposed | DB, N |
| inbound_email (4), whatsapp (1) | admin, signed webhooks | internal | P, J, L, N, H |
| service_config (11) | instance operator, tenant admin | internal | DB, H (probes), W |
| inventory (87), inventory_import (6), stock_counts (9), reception_reversals (2), inventory_recommendation_log (2), signal_thresholds (4), service_level_classes (2) | user, analyst, verified analyst | mostly exposed | hub, P, N, S, C (optimizer) |
| planning (3), scenarios (6), sku_analogies (3) | user, analyst, admin | exposed / internal | C, hub, J |
| training (7), reforecast (2), demo (1) | user, analyst | exposed / internal | **J**, C, P, S |
| configuration (30), datasources (18), datasets (3), forecasts (27), forecast_adjustments (3) | user, analyst | exposed / internal | **C, P, W, S, L** |
| artifacts (2), reports (3) | user, analyst | exposed | S, Excel/PDF builders |
| analyst (3), chats (9), ai_insights (4), documents (6) | user, analyst | mixed | **L**, H, S |
| mcp (2) | key | exposed | imports four routers |
| ws/training_progress (1) | JWT in query | none | jobs |

## 3. Migration waves (ranked by risk and coupling)

**Wave 0: foundations (done).** Config from the same env vars, DB pool, JWT,
API keys with entitlement + exposure + scope + rate + metering, envelope and
error bodies, role guards, trial read-only, machine audit, request id and
access log, NUL-path guard, 404/405 envelopes. Plus `GET /health`.

**Wave 1: DB-only, self-contained (started).** No engine, no pandas, no
notification, no file storage, no hub.
* Done (foundation): `GET /health`, `GET /entitlements`; committed-demand
  `POST`, `POST /bulk`, `PATCH /{id}`, `POST /{id}/status`, resynced with
  main: the contract columns in every row, the warehouse-scope rules, the
  lock on contract-materialised fields, the withdrawn and inactive-contract
  guards, and the `commitment.fulfilled` webhook. `GET /committed-demand`
  stays Python (at-risk verdict = inventory hub).
* Done (R1, `routes/r1/`): preferences GET/PATCH; `GET /me/activity`,
  `GET /me/activity/action-types`; `GET /models`; alerts `GET /alerts`,
  `/alerts/activity`, `/alerts/kinds`, `POST /alerts/read`; currency GET/PATCH;
  timezone GET. `PATCH /tenant/timezone` stays Python for now (it re-anchors
  schedules; the croniter port from R2 makes it a candidate).
* Done (R2): sessions `GET /sessions`, `GET /sessions/summary`, `GET /{id}`,
  `DELETE /{id}` (archives), `POST /{id}/restore`; the five schedule routes
  (croniter 6.2.2 + zoneinfo ported in `routes/schedule/`; non-ASCII digits in
  a cron are a known gap); spike_edits, all three routes. `POST /sessions` and
  `PATCH /sessions/{id}` stay Python.
* Done (R3): webhooks `GET`, `GET /events`, `DELETE /{id}`,
  `POST /{id}/enable`, `POST /{id}/rotate-secret`, `GET /{id}/deliveries`;
  api-keys `POST`, `GET`, `GET /usage`, `DELETE /{id}`; audit `GET`,
  `GET /filters`, `GET /export`. `POST /webhooks` (SSRF host check),
  `POST /webhooks/{id}/test` (wakes the in-process worker) and delivery stay
  Python.
* Done (R4): `POST /inventory/po/{id}/mark-paid`, `/mark-unpaid`, `/cancel`
  (queues `purchase_order.cancelled` as main does), `/uncancel`; `GET`, `PUT`,
  `DELETE /inventory/signal-thresholds`. `POST /inventory/signal-thresholds/preview`
  stays Python (semaforo).
* Shared modules, one implementation each: `audit/` (the whole
  `backend/audit/catalog.py` as data, and `audit::record`, the
  `AuditMiddleware` writer every catalogued Rust route calls with its route
  template); `auth/warehouse_scope.rs`; `limits.rs`; `activity.rs` (the
  `Event` specs every Rust route records through); `webhook_events.rs` (the
  emit half of `backend/webhooks/service.py`: it only inserts
  `webhook_deliveries` rows, which Python's delivery loop sends).
* Next: the rest of the Wave 1 list in the table above.

**Wave 2: DB plus side effects and security.** auth / social / sso, users,
messages, po_approvals, entitlements upgrade-request, trial, freshness,
service_config, tenant_data. These send email / WhatsApp / SMS. Proposal: the
Rust side never talks to Resend / SMTP / Twilio directly. It writes a row to
an **outbox table** that a Python worker loop drains, so there is exactly one
sender implementation and one place where "the transport refused
`@stockai.demo`" is enforced. Auth is security-critical: bcrypt cost, OTP
throttling, refresh-token rotation and the `sessions_invalid_before` cut
each need a contract case before they move.

**Wave 3: the inventory hub.** inventory, stock_counts, inventory_import,
reception_reversals, recommendation log, signal_thresholds,
service_level_classes, planning reads, the committed-demand list. This is
pure business logic, but it is deep: the semaphore, the lead-time cascade and
learned lead times, transfers, receptions and snapshots. Port it module by
module behind the contract harness. Anything that calls the optimizer (HiGHS
via `forecasting_core`) stays Python and is called over HTTP from Rust, or
stays routed to Python.

**Wave 4: stays in Python, probably for good.** training, reforecast, demo,
configuration, datasources, datasets, forecasts, forecast_adjustments,
scenarios, sku_analogies, artifacts, reports, analyst, chats, ai_insights,
documents, mcp, and the websocket. They import `forecasting_core`, pandas or
the LLM stack, or they read and write `storage/`. The routes that only
*enqueue* work (`POST /sessions/{id}/train`, reforecast, demo seeding) can
later get a **thin Rust shim** that validates the request and inserts the
`jobs` row in the same transaction `family_service._enqueue` uses. That is
only worth doing once the session wizard itself is in Rust. The worker that
claims jobs (`FOR UPDATE SKIP LOCKED`) stays Python and never notices which
service enqueued.

## 4. The compatibility contract (what every Rust route must keep)

**Envelope.** Success is `{"success": true, "data": ..., "meta":
{"timestamp": <datetime.isoformat() UTC>}}`
(`backend/schemas/common.py::ok`). `/health` is outside the envelope. Error
bodies come in three shapes, from the handlers in `backend/main.py`:
* `AppError` gives `{"detail": <English>, "error_code": <code>,
  "error_params": {...}}`, with the same status.
* `HTTPException(detail="sentence")` gives `{"detail": ...}`, plus
  `error_code` / `error_params` when the sentence is in
  `backend/error_codes.py`. The rules are ported one for one to
  `backend-rs/src/error.rs`.
* `HTTPException(detail={"code": ...})` keeps `detail` and lifts `code` to
  `error_code` and the rest to `error_params` (`PLAN_LIMIT_REACHED`,
  `TRIAL_EXPIRED`).

Validation failures are `422 {"detail": [pydantic errors], "error_code":
"validation_error"}` with no `error_params`, built with pydantic's types,
messages and lax coercion (`validation.rs`). Unhandled errors are
`500 internal_error`, and an exhausted pool is `503 server_busy` with
`Retry-After: 2`. Unknown paths and wrong methods return the 404 / 405
envelopes. A NUL in the path is `400 malformed_path`. The English `detail`
stays part of the contract: the frontend renders `error_code`, but API
clients read the English.

**JWT** (`backend/auth/jwt_handler.py`, `guards.py`). HS256 with
`SECRET_KEY`. Claims are `sub`, `tenant_id`, `role`, `email_verified`, `jti`,
`type="access"`, and `iat` / `exp` as **floats** (15 min). Verification
replays PyJWT 2.13 step by step (`backend-rs/src/auth/jwt.rs`): segment
parsing, `alg` allow-list, HMAC, `int(iat)` / `int(exp)` against a float
`now` with zero leeway, `sub` / `jti` must be strings, and any `aud` is
refused. Error texts are PyJWT's ("Token expired",
"Invalid token: Signature verification failed", ...). After decoding, the
checks run in Python's order:
1. `type == "access"`.
2. `jti` is in `revoked_tokens` with `expires_at > NOW()`.
3. `users.sessions_invalid_before` is compared with `iat` at sub-second
   precision.
4. The person actor is recorded.

A missing or non-Bearer header is `401 Not authenticated` with
`WWW-Authenticate: Bearer` (FastAPI 0.136 `HTTPBearer`). Refresh tokens are
opaque, hashed and served by `/auth/refresh`, which stays Python until wave 2.

**API keys** (`api_key_auth.py`, `guards._authenticate_api_key`). The
`sk_live_` prefix is dispatched before any JWT work. The key is looked up as
unsalted SHA-256 hex, and expiry is checked in SQL. Then, in this order:
1. Plan entitlement: `ensure_feature(api)`, or `mcp` on `/mcp`. The values
   come from the `PlanDef` booleans plus `tenants.quota` overrides. The check
   is skipped in `TESTING_MODE`.
2. Route exposure: `api_key_route_not_exposed` with the same `reason` string.
3. Scope: `analyst` means write and anything else means read
   (`api_key_scope_insufficient`).
4. Rate: 120 per minute per key, plus the tenant's `max_api_calls_per_day`,
   decided under `pg_advisory_xact_lock(hashtext('ratelimit:<key>'))` in
   `auth_rate_events`. It fails open on database errors and is skipped in
   `TESTING_MODE`. A refusal is a 429 with `Retry-After: 60`.
5. Billing meter: an upsert into `api_usage_daily` that never fails the call.
6. The `last_used` throttle (one minute).
7. The machine actor is set: `user_id = api_key:<id>` and role = the key's
   role.

Each Rust route declares its exposure statically (`RouteAuth`). The contract
test checks it against Python with real keys.

**Roles.** `require_role` yields `role_not_permitted` with params
`{role, required: sorted}` and the English
`Role 'viewer' not permitted. Required: ['admin', 'analyst']`.
`require_analyst_or_above` adds the expired-trial read-only 403
(`TRIAL_EXPIRED`, skipped in testing mode).

**Tenant scoping.** Every query carries `tenant_id` from the authenticated
caller, never from the request. **Warehouse scope**
(`backend/auth/warehouse_scope.py`, fail-closed) must be ported with the
first route that uses it. No wave-1 route does. Note that the Python
committed-demand router does not apply it either, and the Rust port keeps
that behaviour rather than silently changing it.

**Audit and activity.** `record_event` rows are written to `activity_logs`
with the same action names, the same whitelisted detail keys, `severity` and
`kind`, and ids `act_<12 hex>`. A Rust route records exactly the events its
Python handler records. The machine-write audit (`api_write`, every
POST/PUT/PATCH/DELETE by a key except 401/403/429 and `/mcp`) is a Rust
middleware too. The `audit.*` trail of catalogued routes
(`backend/audit/catalog.py`) is not needed by any wave-1 route. It must be
ported, as data, before the first catalogued route moves.

**Database side effects** are the same rows and the same transactions. DB
triggers (`status_bump_*`) keep invalidating the inventory snapshot no matter
which service wrote the row. Schema ownership stays with Python
(`backend/db/migrations.py`). The Rust service never runs DDL and boots
without a database (its `/health` then says `degraded`).

**i18n.** The Rust side returns English plus codes, exactly like Python. The
Spanish lives in `Frontend/src/i18n/translations.ts`. A new code needs
`errors.<code>` in es and en, as before.

**Configuration.** The same variables, read from the same `.env` with real
environment variables winning, case-insensitively (`config.rs`).
`TESTING_MODE=true` with `ENVIRONMENT=production` refuses to boot. Panel
overrides (`service_config` table, Fernet-encrypted secrets) are read with the
same precedence. Python caches them for 10 s; Rust reads them per request.

## 5. Implementation choices (`backend-rs/`)

| Need | Crate | Why |
|---|---|---|
| HTTP | axum 0.8 + tokio | tower middleware model; `MatchedPath`; first-class 404 / 405 fallbacks |
| Postgres | sqlx 0.8, runtime queries | no compile-time DB needed in CI or Docker; rustls (no OpenSSL in the image) |
| JSON | serde_json with `preserve_order` | keys come out in the order Python's dicts have |
| JWT | hmac + sha2 + base64 (hand-rolled) | PyJWT's error texts and float-claim quirks are part of the contract; `jsonwebtoken`'s defaults (60 s leeway, required integer `exp`) are not |
| Secrets | fernet (RustCrypto backend) | decrypts panel-stored overrides written by Python's `cryptography.Fernet` |
| CORS | tower-http | same origins as `CORSMiddleware` |
| Logs | tracing | one access line per request (Python's format) plus `X-Request-Id` |

Modules: `config`, `pycompat` (`isoformat`, `date.fromisoformat`,
`str.strip`, truthiness), `error`, `validation`, `auth/{jwt,api_key,mod}`,
`entitlements`, `service_config`, `activity`, `middleware`,
`routes/{health,entitlements,committed_demand}`. There are 45 unit tests
(`cargo test --manifest-path backend-rs/Cargo.toml`).

## 6. Testing strategy

* **The Python suite is the spec.** `backend/tests` keeps running against
  Python, which stays the fallback for every migrated route.
* **Contract tests** (`tests/contract/contract_test.py`, stdlib only, plus
  psycopg2 for state checks). The harness signs up a throwaway tenant through
  the Python API, with a `@stockai.demo` login the mail transport refuses,
  verifies it, and invites an analyst and a viewer. Then it sends each case to
  both services and diffs:
  * status
  * `error_code` and `error_params`
  * the normalised body (ids and timestamps masked)
  * `WWW-Authenticate` and `Retry-After`

  For writes it also compares the database rows each side wrote, and the
  `activity_logs` row each recorded. At the end it erases the tenant with the
  typed confirmation; `--erase-orphans` cleans up after a crashed run. Cases
  cover the permission pair (viewer denied, analyst allowed), every auth
  failure mode, API keys on internal and exposed routes, and validation
  shapes.
* **Rule for every new route**: no proxy file for a group until its contract
  cases pass. New cases are added whenever a Python test reveals a branch the
  harness does not exercise.
* **DB tests on the server**: a disposable Postgres container started with
  the job (`postgres:16-alpine` on a random port, `tmpfs` data dir). The
  Python API boots against it once to run migrations, then both services
  start and the harness runs. The production database is never used.
  (CI/CD is out of scope by the owner's instruction, so this is a script to
  run on the box, `run_contract.sh` in a later change.)

## 7. Deployment

* Image: `backend-rs/Dockerfile`, three stages (cargo-chef planner, cook,
  build). The runtime is `gcr.io/distroless/cc-debian12:nonroot`. The
  healthcheck is the binary itself (`stockai-api healthcheck`), because the
  image has no shell.
* Size: the release binary is **4.6 MB** (Windows, stripped, LTO thin; Linux
  is the same order of size). distroless/cc is about 23 MB, so the image is
  **about 30 MB**, against several GB for the Python image (it ships
  TensorFlow). The Docker image itself has not been built: Docker is off on
  this machine.
* Build time, measured locally on 8 logical CPUs: **266 s** for a clean
  release build (all dependencies), and **72 s** for a release rebuild after
  a change to this crate only. Most of the 72 s is the thin-LTO link of the
  whole graph. On the 2-vCPU production box, expect **about 10 to 15 minutes
  for the first build** (dependency compilation scales almost linearly with
  cores), and **about 3 to 5 minutes for later builds**. cargo-chef keeps the
  dependency layer cached until `Cargo.toml` / `Cargo.lock` change, but the
  LTO link still runs on every build. These are extrapolations, not
  measurements on that box. Dropping `lto` cuts the rebuild time at a small
  runtime cost, and building elsewhere and pushing the image avoids both.
* Resources: 0.5 CPU and 128 MB are ample. Idle RSS is a few MB.
* Rollout: add `api-rs` and `api-gateway` with an **empty** `routes.d/`
  (behaviour identical to today), rebuild the frontend once with
  `BACKEND_URL=http://api-gateway:8010`, then enable one route group at a
  time.

## 8. Known divergences (Rust vs Python, today)

1. `/health` `services`: Python can report `degraded` from the last
   in-memory probe of its own process. Rust runs no probes, so it reports
   `ready` there.
2. A JSON syntax error: same type, message and status, but `loc[1]` is
   computed from serde's line and column, and `ctx.error` is serde's wording,
   not Python's `json` module's.
3. Non-standard JSON tokens (`NaN`, `Infinity`) are accepted by Python's
   `json` and then refused by field validation. Rust refuses them as
   `json_invalid`. The status is 422 either way.
4. Malformed-token `detail` texts that embed a JSON parser message
   (`Invalid header string: ...`) differ after the colon. The code is
   `token_invalid` either way.
5. Timestamps are rendered as `+00:00` because the Rust side assumes the
   database session time zone is UTC (it is, in every StockAI database).
6. A trailing slash (`/committed-demand/`) gets a 307 from Starlette and a
   404 from Rust. The proxy never routes it to Rust.
7. Bulk import with several unknown warehouses: Python names one picked from
   a set (arbitrary order), and Rust names the first in row order.
8. `api_keys.last_used` update failure: Python would raise (500), and Rust
   logs and continues.

## 9. Results

### Integrated branch: foundation + R1-R4, resynced with main (2026-10-06)

One full harness run, Python dev API on `:8011` running current main, Rust
release build on `:8040`, both on the dev database through the SSH tunnel,
`TESTING_MODE=true`. **477 cases: 474 PASS, 3 SKIP, 0 FAIL, 0 STALE**
(STALE is now only allowed with `--allow-stale`; against a Python running
main a difference is a failure). Three throwaway tenants, erased by id.

| Group | Routes | Cases |
|---|---|---|
| Foundation | `/health` 1/1, `/entitlements` 19/19, committed-demand `POST` 22/22, `/bulk` 6/6, `PATCH` 16/16, `/status` 11/11, 405 shape 1/1 | 76/76 |
| R1 | models 3/3, preferences 7/7 + 15/15, alerts 11/11, activity 15/15, kinds 3/3, read 6/6, me/activity 10/10 + 3/3, currency 11/11 + 13/13, timezone 7/7 | 104/104 |
| R2 | sessions 11/11, summary 13/13, `/{id}` 8/8, archive 10/10, restore 8/8, schedule 3/3 + 16/16 + 4/4, schedules 2/2, history 4/4, spike edits 8/8 + 15/15 + 5/5 | 107/107 |
| R3 | webhooks 7/7, events 3/3, deliveries 6/6, enable 4/4, rotate 3/3 + 1 skip, delete 8/8; api-keys 25/25 + 2 skip, list 4/4, usage 10/10, revoke 9/9; audit 15/15, filters 4/4, export 6/6 | 104/104 + 3 skip |
| R4 | mark-paid 19/19, mark-unpaid 6/6, cancel 21/21, uncancel 6/6, signal thresholds 5/5 + 18/18 + 7/7, tenant scope 1/1 | 83/83 |

New in this run: 19 committed-demand cases for main's rules (scoped caller
must name an own warehouse, company-wide rows are 404 to them, contract field
lock, withdrawn and inactive-contract guards, `commitment.fulfilled` queued
once per transition with the same envelope), and the `purchase_order.cancelled`
delivery each side queues on a real cancel (none on the idempotent repeat).
The first full run failed that last check: Rust read `po_number` (INT4) as
text, the emitter logged and swallowed it, and nothing was queued. Fixed.

SKIP (`TESTING_MODE=true` turns them off on both sides): `max_api_keys`
ceiling, `plan_feature_locked` on api-keys and on rotate/enable. Also not
contract-verified: rate limits, `TRIAL_EXPIRED`, a revoked `jti`, the
`sessions_invalid_before` cut, Python's in-memory `degraded` health state.

Build (8 logical CPUs, private target dir): first release build with
dependencies 3 min 30 s, release rebuild after a crate change about 45 s,
`cargo test` 120 passed, 2 ignored. Release binary 5.9 MB.

### Foundation alone (contract run, local, 2026-10-05)

The run used the Python dev API on `:8011` and the Rust debug build on
`:8021`. Both talked to the same dev database (through an SSH tunnel) with
`TESTING_MODE=true`, so each run used a throwaway tenant and erased it
afterwards.

| Route | Cases | Pass |
|---|---|---|
| `GET /health` | 1 | 1 |
| `GET /entitlements` (admin, viewer, read key, and 8 auth-failure modes) | 11 | 11 |
| `POST /committed-demand` (permission pair, write key refused, every `_clean` code, validation shapes) | 17 | 17 |
| `POST /committed-demand/bulk` | 5 | 5 |
| `PATCH /committed-demand/{id}` (explicit nulls, closed, not found, literal `bulk`) | 9 | 9 |
| `POST /committed-demand/{id}/status` | 5 | 5 |
| 405 envelope | 1 | 1 |
| **Total** | **49** | **49** |

The write cases also compared the stored `committed_demand` rows and the
`activity_logs` events each side wrote. They matched. In the invalid-JSON
cases, `loc` and `ctx` are masked (divergence 2).

The first run passed 48 of 49. pydantic prints the float bound `1e9` as
`1000000000`, not Python's `1000000000.0`, so the Rust message formatter was
wrong. It was fixed, and the rerun passed 49 of 49.

**Not covered by these cases**, because `TESTING_MODE=true` on the dev
backend skips them on both sides:
* `plan_feature_locked` on API keys
* the rate limiter and its 429
* `TRIAL_EXPIRED`

Also not covered: a revoked `jti`, and the `sessions_invalid_before` cut.
These paths are implemented and ported line for line. Unit tests cover the
pure parts. They are **not** contract-verified until a run with
`TESTING_MODE=false` on a disposable database.

During the run the SSH tunnel dropped once. Rust `/health` answered
`200 {"status":"degraded","database":false}` while it was down, and
recovered by itself. The Python dev API hung and returned `503 server_busy`
until its pooled connections recycled.

### R3: webhooks, API keys, audit trail (contract runs, local, 2026-10-06)

`run_r3()` in the harness is a sequence, not a flat list: keys minted by one
service are used on the other, revoked on one and tried on both. Each run
uses two throwaway tenants (the second for the wrong-tenant cases), erased
by id at the end.

Two runs, because the webhook API Rust implements (2592d73, scoped hooks and
a delivery log) is on the webhooks branch, not on main:

* **A. Dev database, Python dev API on `:8011` running main** (restarted
  2026-10-06). The dev database has no 2592d73 webhook columns, so the
  webhooks section skipped itself.
* **B. Throwaway database `rust_r3`**, Python from this worktree (main +
  the webhooks branch) on `:8012`, which self-migrated it, and Rust pointed
  at the same database. The database was dropped afterwards.

| Route | A: pass | B: pass |
|---|---|---|
| `GET /webhooks` (per scope, X-API-Key) | skipped | 7/7 |
| `GET /webhooks/events` | skipped | 3/3 |
| `GET /webhooks/{id}/deliveries` (filters, 422, scoped 404, other tenant) | skipped | 6/6 |
| `POST /webhooks/{id}/enable` | skipped | 4/4 |
| `POST /webhooks/{id}/rotate-secret` | skipped | 3/3 + 1 skip |
| `DELETE /webhooks/{id}` (with its delivery log, other tenant, literal `/events`) | skipped | 8/8 |
| `POST /api-keys` (cross-service auth, warehouse scope, validation) | 24/24 + 2 skip | 25/25 + 2 skip |
| `GET /api-keys` (no reveal, no hash) | 4/4 | 4/4 |
| `GET /api-keys/usage` | 10/10 | 10/10 |
| `DELETE /api-keys/{id}` (revoked on one, refused by both) | 9/9 | 9/9 |
| `GET /audit` (incl. the scoped-admin refusal) | 15/15 | 15/15 |
| `GET /audit/filters` | 3/4 + 1 stale | 4/4 |
| `GET /audit/export` (CSV byte-identical; export rows checked) | 6/6 | 6/6 |

No R3 case failed in either run. The one STALE in A is the filter
vocabulary: main lacks the three `webhook.*` audit entries of the webhooks
branch, which Rust carries; against the webhooks-branch Python (B) it is a
plain PASS. (The other failures in both runs are foundation routes:
`/health`, `/entitlements` and committed demand have drifted from main since
the foundation was written; see the foundation section.)

What the state checks assert, straight from the database: the stored
`key_hash` is `sha256(key)` and nothing else in the row contains the raw key;
`last4` is the key's; no response carries a key hash; a rotated webhook
secret is `token_hex(32)`, is the stored one, and is in neither the list nor
its audit row; the `account.api_key_created` / `_revoked`,
`audit.webhook.*`, `audit.export.audit_log` and `api_write` rows each side
wrote are identical (actor, status, context); each unfiltered export lists
its own audit row first and its note's `rows` matches the file; refused
writes (viewer, out-of-scope warehouse, 500 on a huge expiry) wrote nothing;
the other tenant's webhook and key survive a delete aimed at them and the
key still authenticates.

**SKIP**: the `max_api_keys` ceiling (`PLAN_LIMIT_REACHED`, plus its
`limit.reached` event) and `plan_feature_locked` on `POST /api-keys`,
`/rotate-secret` and `/enable`, because `TESTING_MODE=true` turns both off
on both sides. Ported line for line (`limits.rs`: the same
`pg_advisory_xact_lock(hashtext(tenant))` as `limit_guard`, count and insert
in one transaction, so a Python and a Rust writer of one tenant also queue
behind each other) and unit-tested (`limits::tests`, `entitlements::tests`).

R3 divergences (none reachable through the gateway matchers, which name
methods and exact paths):

1. axum answers `HEAD` on the GET routes; FastAPI answers 405.
2. Audit date filters: the common speedate grammar is ported (dates,
   RFC 3339 datetimes at midnight or not, integer unix timestamps). Exotic
   inputs (fractional timestamps, odd offsets) can get a different
   `ctx.error` wording; the type and the 422 match.
3. Orders Python leaves to a `set` or to equal sort keys (two actors with
   the same label in `/audit/filters`, two keys with the same calls and name
   in `/api-keys/usage`) may come out in a different order.
4. `expires_in_days` or `offset` beyond 64 bits: Python sends it to Postgres
   and gets a 500; Rust answers the same 500 without the query.

Shared-file changes made by R3: `auth/mod.rs` reads `X-API-Key` (every
route gains it, as in Python), `Cargo.toml` adds `getrandom` (OS randomness
for keys and secrets) and `futures-util` (the streamed export). New modules:
`audit/` (catalogue, writer, reader), `auth/warehouse_scope.rs`, `limits.rs`,
`pyjson.rs` (Python's `json.dumps` / `repr` for the CSV and the 4000-byte
note clamp), `query.rs` (Starlette query parsing, pydantic query errors).

Build (8 logical CPUs, shared target dir): debug rebuild of the crate 30 s,
release rebuild after a change to the crate 59 s, release build including
dependencies for a fresh target triple 2 min 45 s. Release binary 5.2 MB.

## 10. SAML 2.0 single sign-on: the first group NEW in Rust (2026-10-06)

Branch `feat/saml`. Owner-approved feature, sibling of the OIDC sign-in
(`backend/auth/sso/`), off by default behind the same
`ENTERPRISE_SSO_ENABLED` switch. It is the first route group written in Rust
that **has no Python twin**, so it has no failover and no contract diff:
`deploy/rust-api/routes.d/50-saml-config.caddy.example` names `api-rs` alone.

### Who does what

| Piece | Language | Why |
|---|---|---|
| `GET/PUT/DELETE /auth/saml/config`, `GET /auth/saml/sp-metadata` | **Rust** (`routes/saml.rs`, `saml/`) | DB-only admin routes; metadata import and certificate checks are not trust decisions |
| `GET /auth/saml/start`, `POST /auth/saml/acs` | Python (`api/v1/saml.py`) | They end in session issuance, which stays Python this pass |
| Assertion validation (XML parsing, signature, conditions) | **Python** (`auth/saml/`) | See below: it could not be proven identical in two languages |
| Schema (`saml_providers`), "require SSO" on the password and social paths, discover | Python | Python owns the schema and still serves those paths |

**The validation core is Python, and this is a decision for the owner to
confirm.** The task allowed Rust only if differential tests could show the two
implementations identical. There is no XML-signature library in either
language's dependency set here (no lxml/xmlsec/signxml in the backend venv, no
xmldsig crate cached), so each side would be a hand-written exclusive
canonicalizer plus verifier; two of those agreeing on every hostile document is
a claim the tests cannot make, and a disagreement would be a security hole in
exactly one language. One implementation, attacked hard (`test_saml_xmlsig.py`,
87 cases written against hand-canonicalized documents, not against the code
itself), is the safer shape. Moving it to Rust later means writing the
differential harness first.

### Data and rules

* `saml_providers` (additive migration, no secret column: a SAML provider is a
  public signing certificate). Domains live in the existing `sso_domains`, so
  "a domain belongs to one tenant" has one table and one answer.
* One protocol per tenant: Rust refuses `sso_protocol_conflict` while an OIDC
  row exists, Python's OIDC save refuses it while a SAML row exists.
* SP-initiated only. The AuthnRequest id is the stored flow's nonce
  (`oauth_flows`, single use, ten minutes, bound to the browser by a cookie,
  `SameSite=None; Secure` over https because the ACS is a cross-site POST). An
  unsolicited (IdP-initiated) response has no request to answer and is refused.
* Per-tenant audience: `<FRONTEND_URL>/api/v1/auth/saml/sp/<tenant_id>`; ACS
  `<FRONTEND_URL>/api/v1/auth/saml/acs`. The two formulas exist in both
  languages and are compared by the contract run.
* Accepted: a signature (RSA-SHA256/384/512, exclusive c14n, enveloped) on the
  Response, the Assertion, or both, verified against the tenant's stored
  certificates only (KeyInfo is ignored); not accepted, and refused with a
  stable code rather than guessed: SHA-1, other transforms, encrypted
  assertions, more than one Assertion, a Signature anywhere else, DOCTYPE or
  non-UTF-8 documents, transient NameIDs, unknown Conditions.
* Tenant model as OIDC: just-in-time users, never administrators (also clamped
  on read, so a hand-edited row cannot mint one), role from an attribute
  mapping limited to analyst/viewer, "require SSO" only after an admin signed
  in through this very configuration. Extra lock-out guard (Rust): a request
  that changes which provider is trusted (entity ID, sign-on URL, or a
  certificate set sharing nothing with the stored one) cannot also require SSO;
  a rollover (old + new certificates together) is allowed.
* Events: `account.saml_config_changed` / `account.saml_config_removed`
  (activity feed, audit trail as `sso_config.changed/removed`), Rust mirrors in
  `activity.rs`, `audit/catalog.rs` (sizes now 75 / 29 / 110 / 114) and
  `routes/r1/alerts.rs`. Sign-ins reuse the OIDC `account.sso_*` events.

### Not done, on purpose

* No metadata URL import: the admin pastes XML (a URL would be a server-side
  request to an address a customer chose).
* No encrypted assertions, no ECDSA, no signed AuthnRequests, no single logout,
  no IdP-initiated flow.
* SCIM still requires an OIDC row (`scim_requires_sso`): a SAML tenant cannot
  enable SCIM yet.

### Verification (local, disposable Postgres on its own port)

* `cargo test`: all green, 33 new unit tests (`saml::cert`, `saml::metadata`,
  `saml::rules`, `routes::saml`), including a test that re-reads the Python
  free-mail list so the two cannot drift.
* `pytest`: `test_saml_xmlsig.py` (87), `test_saml_sso.py` (46), plus the
  existing SSO, audit, route-audit and tenant-erasure suites.
* `tests/contract/contract_test.py --only saml`: 78 cases against a real
  Python and a real Rust server on one database: every refusal checks the rows
  with SQL, a certificate stored by Rust verifies a signature in Python, and
  "require SSO" set through Rust closes Python's password door.
