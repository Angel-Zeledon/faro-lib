# Rewriting the HTTP API in Rust: the strangler-fig plan

Owner request, 2026-10-05: rewrite the API (everything that is not the
forecasting engine) in Rust, gradually. **The engine (`ForecastingCore`) and
the training worker stay in Python.** The Python FastAPI app keeps serving
every route that has not moved, and keeps the code of every route that has,
so any route can go back to Python in seconds.

Status: wave 0 (foundations), the wave-1 routes R1 to R4 and the outbox-free
part of wave 2 ("wave 2b", section 12) are implemented in `backend-rs/`.
**Nothing is deployed.** The compose and Caddy files under
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
  timezone GET. `PATCH /tenant/timezone` moved in wave 1b.
* Done (R2): sessions `GET /sessions`, `GET /sessions/summary`, `GET /{id}`,
  `DELETE /{id}` (archives), `POST /{id}/restore`; the five schedule routes
  (croniter 6.2.2 + zoneinfo ported in `routes/schedule/`; non-ASCII digits in
  a cron are a known gap); spike_edits, all three routes. `POST /sessions` and
  `PATCH /sessions/{id}` moved in wave 1b.
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

**Wave 2b (done in `routes/w2b/`, branch `feat/rust-wave2b`): the parts of
wave 2 that need no outbox.**
* `GET /data-freshness` (the read half; the daily reminder loop stays in the
  Python worker).
* `GET /tenant/export` (ZIP) and `DELETE /tenant` (whole-tenant erasure). The
  table lists are generated from `backend/tenants/data_export.py`.
* `GET /service-config/capabilities` only. The other ten service-config routes
  stay Python, see "Wave 2b: what stayed Python".
* `GET /auth/sso/availability`, the only `auth`-tag route moved. No
  authentication code moved; the per-feature plan for it is section 10.
* Results and divergences: "Results: wave 2b" below.

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
9. **Database session time zone (every migrated route).** Both services print
   timestamps in the session zone of their connection; Rust prints `+00:00`
   always. They agree only when the database is UTC, which holds for every
   StockAI deployment but NOT for the Postgres installed on this development
   machine (`America/Guatemala`). The wave 2b run set the disposable
   database to UTC. Against a non-UTC database every timestamp of every
   migrated route differs (R1 to R4 included).
10. Freshness: `datetime.fromisoformat` is ported for dates (all forms
    `date.fromisoformat` takes) and for `T` or space then `HH`, `HH:MM`,
    `HH:MM:SS` or compact `HHMMSS`, with a fraction and a `Z` / offset that
    `.replace(tzinfo=)` discards. ISO week dates followed by a time, other
    separators and 3.11-only exotic spellings are not matched: Rust then falls
    back to the upload date where Python would have parsed them. The two
    services read their own clock, so an age exactly on a day boundary can
    differ by one day between two calls a moment apart.
11. Export: row order inside a member is whatever the query returns (no
    `ORDER BY`, same as Python; none of the runs saw a difference). ZIP
    container bytes differ (compressor, timestamps), member contents do not.
    A column type outside text / int / float8 / bool / jsonb / timestamptz /
    date / text[] is selected `::text`, which equals Python's `str()` for
    numeric, uuid and time, and differs for interval, bytea and the extreme
    float4 cases (the schema has none today). JSON text from Postgres is
    re-parsed with Python's integer rule (big integers stay integers).
    Feedback screenshot paths are checked lexically (one plain file name);
    Python also resolves symlinks.
12. Erase: `removed_storage_dirs` prints paths with the platform separator
    (the harness normalises it); storage removal is `remove_dir_all` per
    category, best effort, like `shutil.rmtree`.
13. `GET /service-config/capabilities` and `GET /auth/sso/availability`: Python
    caches the override table for 10 s per process, Rust reads it per request,
    so after a panel write the two can disagree for up to 10 s. A stored float
    is parsed with Rust's `f64` rules: Python's `float()` also accepts
    underscores (`1_0`), which Rust refuses (the value is then ignored).
14. Invalid JSON bodies on `DELETE /tenant`: same status, type and `loc`
    shape as every other route; `ctx.error` is serde's wording (divergence 2).

## 9. Results

### Wave 1b and the start of wave 2 (2026-10-06, branch feat/rust-wave1b)

Harness: Python from this worktree on `:8012`, Rust debug build on `:8040`,
both on a DISPOSABLE database (`rust_wave1b`, local Postgres 18, timezone
UTC), `TESTING_MODE=true`, `SCHEDULER_ENABLED=false` (no worker loops).
Final full-filter run (`--only "pa "`, which also pulls in the committed-demand
cases): **286/286 pass**. The groups below were each run on their own earlier.
A complete unfiltered run of the whole harness was NOT repeated after the last
commit; run it before enabling any proxy file.

| Group | Routes | Cases |
|---|---|---|
| Timezone write | `PATCH /tenant/timezone` (re-anchors armed schedules with the croniter port) | 15 |
| Sessions write | `POST /sessions` (ceiling under the tenant lock, `session_configs`, audit), `PATCH /sessions/{id}` | 49 |
| Lineage | `GET /sessions/{id}/manifest`, `GET /training/run-durations` | 57 |
| Reception reversals | `POST /inventory/po/{id}/unreceive`, `/unsend` | 34 |
| PO approvals | settings, rules POST/PATCH/DELETE, approvers PUT, pending, `GET /po/{id}/approval`, approve, reject (9 of 10) | 130+ |
| Outbox | writer parity, drain outcomes | 6 |

New gateway examples: `33-sessions` (renamed from the duplicated `30-`),
`34-lineage`, `42-reception-reversals`, `43-po-approvals`; `30-settings-reads`
gains `PATCH /tenant/timezone`.

**The outbox (wave 2 foundation).** Table `outbound_messages` (additive
migration `backend/notifications/outbox_migrations.py`), Python drain loop
`outbox-drain` in `backend/workers/worker.py` (5 s poll, `FOR UPDATE SKIP
LOCKED` claim with a lease, 5 attempts on 30 s / 2 min / 10 min / 40 min, then
`failed`), rendering through the existing `send_*` functions. A row names an
English `kind` plus data (`params`), never the text; the Spanish stays in the
Python catalog. `params` is scrubbed when a row ends and a row nobody
delivered by `expires_at` is abandoned and scrubbed. Trial addresses are
`abandoned` with `trial_address`; no transport is `failed` with the reason
code. Rust writer: `backend-rs/src/outbox.rs` (registry parity with
`outbox.KINDS` checked by a unit test that reads the Python source). Erasure
and export know the table (export without `params`). Python tests:
`backend/tests/test_outbox.py` (22). Contract: `run_outbox` writes the same
scenarios with Rust and Python and compares the rows, then drains both.
Only the first consumer is wired: approve/reject queue the requester's mail.
Nothing else moved onto it. Delivery is at-least-once.

**Stayed Python, and why.**
* `POST /webhooks`: the SSRF check relies on Python's `ipaddress` properties
  (`is_private`, `is_reserved`, `is_global`, version dependent), `urlsplit`
  quirks and live DNS. A port that is looser by one range is a security hole
  and cannot be proven equal here. Left alone.
* Committed-demand reads: the only GET is the list, which needs the at-risk
  verdict (inventory hub). No other read exists.
* `service_level_classes`: reads the inventory status snapshot (hub).
* `POST /po/{id}/approval/request`: its answer carries `notified`, the number
  of approver mails that actually left. An outbox can only say "queued".
  Changing that answer is a product decision, not a port.
* Users (16 routes): NOT started (owner paused the migration). Several send
  mail synchronously and answer `email_sent`, the same problem as above.
* Auth, social, sso, messages, trial, freshness, tenant_data: untouched.

**Not verified / hidden by TESTING_MODE=true.** The `max_sessions` ceiling
(`PLAN_LIMIT_REACHED`) on `POST /sessions`, rate limits, `TRIAL_EXPIRED`, a
revoked `jti`, the `sessions_invalid_before` cut. The `max_sessions` path is
ported line for line from `limit_guard` and unit-tested, not contract-tested.
The real mail path was never exercised (senders replaced by a recorder).
Manifest numbers: Python prints tiny floats as `1e-05`, Rust as `1e-5` (same
JSON value, different bytes). `run-durations` NaN / inf durations stored as
strings are not matched. Raw `created_at` bytes carry `+00:00` only because
the database timezone is UTC (known divergence 5). Docker was off, so the
compose files are untested. One shared-scratchpad mishap: another session's
helper script was briefly overwritten and restored; both sessions use their
own files now.

**Next** (when the owner resumes): run the full harness against a Python on
the same commit, then users (DB-only parts: list, status, permissions,
warehouse scope, delete), then auth with its own contract cases
(`TESTING_MODE=false` on a disposable database for throttling, rotation and
the `sessions_invalid_before` cut).

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

## 10. Authentication: per-feature contract-case plan

**Nothing in authentication moved in wave 2b** (except `GET /auth/sso/availability`,
a read of one configuration switch). This section is the plan the move must
follow, written from the Python source as of this branch
(`backend/api/v1/auth.py`, `auth/password.py`, `auth/guards.py`,
`users/service.py`). Each feature lists what the Python code does today, the
contract cases that must pass before it moves, and the traps. Auth is never
moved as one router: the order is at the end.

**Why the existing harness cannot verify any of it yet.** The harness runs both
services with `TESTING_MODE=true`, and Python's `_check_rate` returns on its
first line in testing mode. So every limiter below (login, OTP, trial, SSO) is
invisible to it. The auth runs need their own mode: a disposable database, both
services started with `TESTING_MODE=false` (never `ENVIRONMENT=production`; the
server refuses that pair), and a harness section that creates its own throwaway
tenants. Plan gates (`plan_feature_locked`, `PLAN_LIMIT_REACHED`),
`TRIAL_EXPIRED` and the API-key rate limit flip on in the same run, so that run
also closes the SKIPs listed in section 9.

Prerequisites, none built here: the email outbox (rust-wave1b) for
verification mail and OTP delivery, the users `create_user` / `create_tenant`
port (shared with `POST /trial`), the `bcrypt` crate.

**1. Password hashing (bcrypt).**
* Today: `bcrypt.gensalt()` (cost 12, `$2b$` prefix), `bcrypt.hashpw`;
  `checkpw` returns `False` on ANY exception, so a malformed stored hash is a
  401 `invalid_credentials`, not a 500. `validate_strength`: at least 8
  characters, at most 72 UTF-8 BYTES, at least one digit, at least one letter,
  each with its own English sentence. A `users.has_password = FALSE` account
  (provider-only) refuses every password.
* Cases: (a) a hash written by Rust verifies in Python's `POST /login` and the
  reverse, both directions, with the same password; (b) the stored hash starts
  with `$2b$12$`; (c) boundaries 7/8 characters, 72/73 bytes, a 72-byte
  password built from multibyte characters, digit-only, letter-only; (d) a
  garbage `hashed_password` gives 401, not 500; (e) `has_password = FALSE`
  with the right password gives 401; (f) unicode normalisation: bcrypt hashes
  bytes, so NFC and NFD spellings are different passwords on both sides.
* Traps: the unknown-address path does no dummy hash, so login timing already
  tells a valid address from an unknown one. Rust must not "fix" that silently
  (a behaviour change needs the owner's yes, and a timing test would have to
  tolerate it). Cost is a constant of the crate call, not a setting.

**2. Throttling and one-time codes.**
* Login: `_check_rate("login:<lowercased email>", 5, 300)` runs BEFORE the
  credentials are looked at, so every attempt counts, successful ones too. The
  limiter is `DELETE old rows; COUNT; INSERT` on `auth_rate_events`, three
  statements with no advisory lock (unlike the API-key limiter), so two
  concurrent requests can both read 4 and both pass. On a database error it
  falls back to a per-process in-memory window; Rust must choose between
  failing open and keeping its own window, and the choice is a decision.
  Both services share the table, so a Python attempt counts against Rust.
* Reset codes: `forgot-password` never reports delivery (same body for an
  unknown address) and has no limiter of its own. The code is
  `SystemRandom().randint(0, 999999)` zero-padded to 6 digits, stored as
  HMAC-SHA256(`SECRET_KEY`, code) hex, valid `OTP_EXPIRE_MINUTES`, one live code
  per user (older unused codes are deleted when a new one is issued).
  `forgot-password/verify` is limited by `otp:<email>` 10 per 600 s and by
  `attempts < 5` per code; every failure (unknown address, no live code,
  expired, wrong, attempts exhausted) is the same 400 `reset_code_invalid`; a
  correct code is burnt (`used = TRUE`) and buys a 10-minute signed token with
  `purpose = password_reset`.
* Cases (all with `TESTING_MODE=false`): 5 logins pass and the 6th is 429
  `too_many_attempts`; the window slides (rows aged by SQL); a wrong-password
  attempt and a right one both consume the budget; case-insensitive key;
  a mixed sequence (3 attempts on Python, 3 on Rust) hits the limit on the 6th
  whichever side it lands on; 10 OTP verifies then 429; 5 wrong codes kill the
  code and the right one is then refused; a code issued by Python is accepted
  by Rust and the reverse (this proves the HMAC and the 6-digit format); a new
  code invalidates the previous one; all five failure modes return identical
  bodies; unknown address on `forgot-password` returns the identical body and
  writes no row; a concurrent burst of 20 logins against the limit (Python
  admits more than 5; the case records how many and Rust must not admit
  more than Python's worst case).
* Traps: email delivery is the outbox; the OTP row is Rust's job, the send is
  not. `trial:<address>` and `sso-*` limiters share `_check_rate` and move
  with it.

**3. Refresh tokens.**
* Today there is NO rotation. `POST /login` stores sha256(raw) in
  `refresh_tokens` with a 7-day expiry (raw = `token_urlsafe(64)`, returned
  once). `POST /refresh` hashes the presented token, joins the user row, and
  returns only a NEW access token (and the current `email_verified`); the
  refresh token stays valid and reusable until it expires or is deleted. It
  deletes nothing and does not look at `users.status`; deactivation and
  password change delete the user's rows instead. An expired trial tenant
  gets 401 `trial_account_expired` here (403 on login).
* Cases: a token issued by Python refreshes on Rust and the reverse; two
  refreshes with the same token both succeed and neither returns a refresh
  token (this pins "no rotation"); unknown, expired (row aged by SQL) and
  deleted tokens give 401 `refresh_token_invalid`; `email_verified` is
  re-read (verify the user between two refreshes and the claim flips);
  expired trial; deactivating the user (`PATCH /users/{id}`) kills the token.
* Decision for the owner, not a defect: rotation with reuse detection is the
  standard hardening and is NOT current behaviour. Adding it in Rust alone
  would break the failover (a Rust-rotated token presented to Python) and
  every Python-issued session. If wanted, it goes into Python first.

**4. The `sessions_invalid_before` cut and the revoked-`jti` list.**
* Today: `users.sessions_invalid_before` is stamped `NOW()` by
  `update_password` (reset, change), social password drop, SSO, and SCIM, which
  also delete the user's refresh tokens. `guards.py` refuses an access token
  whose `iat` (float, sub-second) is below the cut, with 401 "Session ended by
  a password change"; a token without `iat` is refused only for an account that
  has a cut. `/logout` revokes the presented `jti` in `revoked_tokens` and
  deletes ALL the user's refresh tokens but does NOT set the cut, so other
  access tokens of that user live up to 15 minutes. A reset token is one-use:
  its `jti` is revoked after the password change (replay: 400
  `reset_token_invalid`, "This reset link was already used").
* The read side already exists in `backend-rs/src/auth/mod.rs` (the order of
  checks is in section 4) but was never contract-verified. Cases: a token
  minted just before a reset is refused by both services and one minted just
  after is accepted (microsecond boundary: `iat` equal to the cut passes, one
  microsecond lower fails); the cut set by Rust is honoured by Python and the
  reverse; a token with no `iat` for an account with and without a cut; a
  revoked `jti` is 401 on both; a revoked row past `expires_at` no longer
  blocks; logout then reuse of the same access token (401), reuse of another
  access token of the same user (still 200: pins the 15-minute window),
  refresh after logout (401); reset-token replay; a weak new password leaves
  the reset token usable ("deliberately BEFORE the burn").

**5. Signup, verification, social and SSO.**
* `signup` creates tenant, admin user, quota, terms acceptance and a signed
  verification token; `verify-email` and `resend-verification` consume or
  re-issue it. They depend on `create_tenant` / `create_user` (the same
  functions `POST /trial` uses) and on the outbox. Cases: slug generation and
  collisions, terms version, whatsapp normalisation, the `verify_url` the
  harness itself depends on, the `@stockai.demo` refusal at the transport.
* Social (Google, Microsoft, Apple) and SSO (OIDC) call out over HTTP, keep
  binding cookies, PKCE and nonce state, and the provider list depends on
  parsing an ES256 private key (Apple) to decide whether a button shows. They
  stay Python the longest. `GET /auth/providers` and `GET /auth/identities`
  were NOT moved in wave 2b because both embed that list; `GET /auth/sso/config`
  was not moved because it needs the secret-storage probe and the
  `sso_providers` row view. Only `GET /auth/sso/availability` moved.

**Order.** (1) Run the existing read side (guards, `sessions_invalid_before`,
`jti` list) under `TESTING_MODE=false` and close the section 9 SKIPs. (2)
`login`, `refresh`, `logout` (needs bcrypt; no mail). (3) The reset family
(needs the outbox). (4) `signup` and verification (needs the users port). (5)
Social and SSO last, or never. Each step is a gateway file per route, and
`POST /login` is the first one worth a canary: it has the loudest failure mode.

## 11. Wave 2b: what stayed Python, and why

| Router | Routes | Why it stayed |
|---|---|---|
| service_config | 10 of 11 | The panel shows `last_check`, the result of the last probe, kept in the memory of the Python process (`status._last_probe`); every write calls `forget_probe` there. A Rust `GET /services` would show a stale or empty check next to a Python probe, and a Rust write would not clear a stale `degraded`. The probes themselves make outbound HTTP (Twilio, Resend, SMTP, DeepSeek, Pinecone). `/ops` reads queue, pool, disk and backup state of the Python process. Writes encrypt secrets and enforce the operator rules. Moving any of it needs the probe state in the database first. Only `/capabilities` moved: it reads no probe state. |
| trial | 1 | `POST /trial` needs `create_tenant` and `create_user` (bcrypt, terms, quota, owned by the users port that rust-wave1b is building), the `auth_rate_events` limiter with its in-memory fallback, and the capacity ceilings that `TESTING_MODE` hides. The harness signs its own tenants up through the Python API, so a Rust trial would be matched against a path no case exercises. The trial reaper is a Python worker loop. |
| auth, social_auth, sso | 23 of 23 minus `/auth/sso/availability` | Section 10. |
| documents, artifacts, reports | 11 | Files under `storage/`, Excel and PDF builders, and for documents the RAG pipeline (Voyage, Pinecone). Python-only libraries. |
| analyst, chats, ai_insights | 16 | DeepSeek calls, prompt assembly, RAG retrieval. The product rule is one LLM backend behind one factory. |
| mcp | 2 | Imports four Python routers to resolve what each tool calls; the test that pins "only GET endpoints" reads the FastAPI route table. |
| whatsapp, inbound_email | 5 | Signed provider webhooks, outbound Twilio, dataset ingestion with pandas, the job queue. |
| users, messages, po_approvals, entitlements upgrade-request | | Not in this pass: they send email or WhatsApp and are rust-wave1b's. |

Everything above keeps its Python route code untouched. Nothing was deleted.

## 12. Results: wave 2b (disposable database, 2026-10-06)

**Setup.** Disposable database `rust_w2b` on this machine's Postgres 5432,
created for the run and dropped at the end. The Python API is this worktree's
code on `:8072` (it self-migrated the database); the Rust debug build on
`:8073`. `TESTING_MODE=true`, `WORKER_ENABLED=false`, a Fernet key in
`INTEGRATIONS_SECRET_KEY` shared by both. The database was set to
`timezone = UTC` after creation: this machine's Postgres defaults to
`America/Guatemala`, and both services print timestamps in the session zone
(divergence 9). No credentials of any provider were present, so no mail or
message could leave. Production was never touched.

| Route | Cases | Pass |
|---|---|---|
| `GET /data-freshness` | 41 | 41 |
| `GET /tenant/export` | 11 | 11 |
| `DELETE /tenant` | 33 | 33 |
| `GET /service-config/capabilities` | 26 | 26 |
| `GET /auth/sso/availability` | 15 | 15 |
| **Wave 2b section** | **126** | **126** |

What the cases are:
* **Freshness (41):** 25 data scenarios (empty tenant, fresh, stale, blind,
  sales and stock blind separately, each way the file date can be wrong or
  missing, archived / backtest / running sessions, one warehouse, one that
  lags, all late, registered without stock, case folding and sort of
  warehouse names, junk store dates), roles, keys, 5 auth failures, the
  `X-API-Key` header, 5 warehouse-scoped callers, the thresholds compared with
  the Python constants, and a second tenant's rows that must not leak. Every
  body is compared EXACTLY (timestamps included), and for 7 scenarios the
  Python answer itself is asserted, so two empty answers cannot pass.
* **Export (11):** a tenant with one synthetic row in all 86 tenant tables
  (generated from the schema: foreign keys, CHECK constraints) plus NaN,
  Infinity, control characters, emoji, a 10^20 integer inside jsonb, text
  arrays, microsecond timestamps and feedback screenshots (one valid, five
  hostile paths). The two ZIPs have the same member list and byte-identical
  member contents (only `generated_at` is masked, the zip container bytes
  differ), the Python archive is asserted to contain each scenario, no stored
  credential hash appears in either, and the `audit.export.tenant_data` row is
  identical.
* **Erase (33):** 16 refusal cases (viewer, analyst, no token, bad signature,
  expired, read key, write key, wrong / empty / foreign-slug confirmation,
  missing / numeric / null / list / absent / invalid JSON body), a
  warehouse-scoped admin, 5 blocking subscription states, 8 successful erasures
  (typed `DELETE`, lowercase with whitespace, the slug, and the subscription
  states that do not block), a second call on an erased tenant, a bystander
  tenant proven untouched, and the table-list check. Every erasure runs on two
  identically seeded tenants (86 tables, 9 storage directories, a plain file
  where a directory is expected), one erased by each service, and compares:
  status and body, the row-count delta of EVERY table in the database, the
  rows left in every table that carries a `tenant_id`, the storage
  directories removed, and that a refused request changed nothing anywhere.
* **Capabilities (26) and SSO availability (15):** instance rows and tenant
  rows written straight to `service_config`, secrets Fernet-encrypted by the
  harness (so Rust's decryption is exercised), 7 phases for capabilities
  including a tenant row for an instance-only field (ignored), unparseable
  stored values (ignored), a padded mixed-case PayPal mode, a zero price. Each
  case asserts the Python answer is the scenario named.

Unit tests: `cargo test` 135 passed, 2 ignored (120 before wave 2b: +15 for freshness dates and ages, the JSON dump, the screenshot-path rule, `grants_access`, and the capability resolver).

**What the erase run did NOT prove.**
* In a freshly migrated database every one of the 87 tenant tables carries
  `REFERENCES tenants(id) ON DELETE CASCADE` (the `data_export.py` header says
  most do not; that is out of date). So a table missing from Rust's explicit
  list would still be emptied by the final `DELETE FROM tenants`, and the
  row-for-row comparison cannot see it. Parity of the list therefore rests on
  generation: `scripts/gen_rust_tenant_tables.py` reads the Python lists with
  `ast`, the harness fails when the Rust file is stale or when a table with a
  `tenant_id` column or an FK to `tenants` is missing from the Python list, and
  a mutation check showed both fire (a deleted storage category and a stale
  file were caught; a deleted table was caught only by the stale check, for the
  reason above). On an older database without those FKs the explicit list is
  what erases, and that case was not reproduced.
* The Python and Rust erasures were never run concurrently against one tenant,
  nor with a job running for it.
* Billing subscriptions were seeded by SQL; no provider was involved.
  `grants_access` was ported (`billing_access.rs`) with unit tests and the
  contract cases above (5 blocked, 3 allowed states); `decide_tier` was not
  ported (the webhooks stay Python).

**What `TESTING_MODE=true` hides from this run** (on both sides): the login,
OTP and trial limiters, trial capacity, `plan_feature_locked`, `TRIAL_EXPIRED`,
the API-key rate limit. None of the wave 2b routes depends on them (the admin
guard has no trial read-only check; a trial tenant can erase itself), but the
auth plan in section 10 does.

**Not verified at all.** The `routes.d` examples have not been loaded by Caddy
(no Caddy here); the failover of a `DELETE` through `lb_try_duration`; export
memory and time on a large tenant (both services build the archive in memory;
neither was measured); a Postgres whose session zone is not UTC; production
data volumes; any release build (all runs used the debug binary).

**Whole harness, same run setup (Python `:8072`, Rust `:8073`, disposable
`rust_w2b`): 603 of 603 cases pass, 0 fail**, i.e. the earlier 477-case
foundation + R1-R4 suite plus the 126 wave 2b cases. `cargo test` is green
(135 passed, 2 ignored).

**Status at hand-over (owner slowed the migration on 2026-10-06).** Done and
contract-verified: everything in section 12. Registered in `routes/mod.rs`
through one line (`w2b::router()`): only those five routes, each complete.
Gateway examples exist for each (`50` to `53` in `deploy/rust-api/routes.d/`);
none is enabled, and the tenant erasure example (`51`) carries a warning.
Nothing is half-ported. Next, in order: the section 10 `TESTING_MODE=false`
run of the existing guards, then the rest of wave 2 once the outbox and the
users port land. Run the harness for this section alone with
`python tests/contract/w2b_cases.py --db ... --sections freshness,export,erase,capabilities`.
### Wave 3 (inventory hub), partial (2026-10-06)

Stopped on the owner's instruction to slow the migration; what exists is below.
Nothing here is deployed and **no Caddy example exists for any wave-3 group**.

**Map of `backend/api/v1/inventory*.py` (87 + 6 + 9 + 2 + 2 + 4 + 2 routes), by risk.**
Highest (money decided by the numbers): `GET /inventory/status`, `/optimize`,
`/status/export-po`, `/morning-briefing`, `/dashboard-summary`, `POST /alerts/send-now`,
`/events/simulate`, service-level-classes (all need the semaforo or the optimizer or
notifications: **stay Python**). High (writes stock): stock rows, counts, transfers,
reception and reversals, shrinkage, bulk import (pandas/file: stays Python). Medium:
suppliers, warehouses, lanes, price breaks, BOM, events, cash calendar, PO log/send/PDF
(files, email: stay Python for now). Low: reads of history, lists.

**Registered in `routes/w3/mod.rs` and contract-verified: 20 routes, 271/271 cases.**
`GET /inventory/stock`, `/stock/page`, `/stock/lookup`, `/stock/{sku}`; `PUT`, `PATCH`,
`DELETE /stock/{sku}`; the nine `/stock-counts` routes; `POST /po/{id}/unreceive` and
`/unsend`. Run: `python tests/contract/run_w3.py` (two identically seeded tenants, one per
service, a third as the foreign tenant; compares status, error code and params, masked
bodies, and the rows each side added or removed in 12 tables). Python ran on the worktree
code (`:8052`), Rust release on `:8051`, a disposable local Postgres (`rust_w3`, timezone
UTC), `TESTING_MODE=true`. First run found 22 divergences, all fixed: the Rust side must
answer 404 for `%2F` inside a path parameter (Starlette routes on the decoded path;
`inventory::scope::reject_slash`), and the database must be UTC (divergence 5).
Per route: stock 7+21+23+9+38+19+8, counts 20+9+8+33+6+10+9+16+10, reversals 13+11.

**Written, differential-tested where numeric, but NOT registered (no contract result yet):**
`routes/w3/receive.rs` (`GET /po/{id}/items`, `POST /po/{id}/receive`), `transfers.rs`
(create, list, receive, cancel, close), `shrinkage.rs` (create, list, reasons). The cases
exist (`tests/contract/w3_cases_3b.py`, about 150) but the run did not complete before the
stop. Next step: build, start both services, run `run_w3.py`, fix, then register.

**Numerics, bit-exact against Python (`backend-rs/src/inventory/calc.rs`, `pydt.rs`).**
`cargo test differential` replays `backend-rs/tests/fixtures/inventory_calc.json`, written by
the Python implementation (`tests/contract/gen_inventory_fixtures.py`, seeded, `--check`
detects staleness): z quantile (Acklam), semaforo signal, measured and modelled safety stock,
recommended quantity with MOQ, lead-time cascade and learned lead time, ABC/XYZ and class
summary, fill rate, unit margin, forecast averages, `format(x,"g")`, and CPython 3.12
`datetime.fromisoformat` (5,049 strings, one measured quirk reproduced). About 25,700 cases,
every f64 compared by bits. Reproduced exactly: `round()` ties-to-even, compensated `sum()`
of CPython 3.12, Python `max`/`min` on NaN, `pow(x, 2.0)` through libm (not `x*x`).
Mutating `py_sum` to a plain sum turns the suite red, so the comparison can fail.
Caveat: `log` and `pow` come from the platform libm; verified on Windows only. Production
Python and the distroless Rust image are both Debian 12 glibc, which is the intended match,
unverified here.

**Stays Python (and why).** Semaforo status, optimizer, `service_level_classes` (read the
status; the numerics are ported but the route needs `_compute_inventory_status`, 700 lines
over session results), planning, PDF, email/WhatsApp sends, bulk and PO imports (pandas,
files), `receive_po` side effects beyond the DB are none, so it is a candidate once verified.

**Divergences and gaps.** Ours stronger: stock writes, shrinkage and the reversals run in one
transaction where Python uses several autocommits. `SELECT *` without ORDER BY (a SKU in two
warehouses on `GET /stock/{sku}`) relies on the same heap order. Not covered: ceilings
(`max_skus`, `max_locations`) and `plan_feature_locked`, because `TESTING_MODE=true` turns
them off on both sides; rate limits; `TRIAL_EXPIRED`. Python `float()` of non-ASCII digits
and ordinal ISO dates are not reproduced. Rust handlers of waves 1-2 probably need the same
`%2F` 404 guard.
## 10. New routes written in Rust only: approval delegation

Everything above moves routes that already exist in Python. Purchase-order
**approval delegation** (a corporate feature: an approver names a substitute
for a date range) is the first feature whose routes exist **only in Rust**:

| Route | Who | What |
|---|---|---|
| `GET /inventory/po-approval/delegations` | any signed-in user (`?all=true`: admin) | the delegations the caller gave or received, plus, for a current approver, the colleagues they could name |
| `POST /inventory/po-approval/delegations` | analyst or above, and a current approver | name a substitute for `starts_on`..`ends_on` (UTC days, inclusive) |
| `POST /inventory/po-approval/delegations/{id}/revoke` | the giver or an admin | end it now (idempotent) |

Code: `backend-rs/src/routes/po_delegations.rs`. Gateway file:
`deploy/rust-api/routes.d/42-po-approval-delegations.caddy.example`.

**These routes have no Python failover.** Unlike every group above, the
gateway file lists `api-rs` as the only upstream. With the Rust service down
the three paths answer 502; with the file in `routes.d/off/` they answer
Python's own 404 and the delegation card on `/pedidos` stays hidden. The kill
switch for this feature is therefore "delegation off", not "delegation on
Python". Rolling back Rust does not remove data: the table and the Python
decision path stay in place.

**What Python still does.** `POST /inventory/po/{id}/approval/approve|reject`
and `GET .../pending` are still served by Python, so
`backend/inventory/po_delegation_service.py` implements, in Python, the same
rules the Rust routes enforce at creation, applied at decision time. Both
sides must be changed together:

* The schema is Python's (`backend/inventory/po_delegation_migrations.py`):
  table `po_approval_delegations`, and two columns on `po_approvals`
  (`decided_on_behalf_of`, `delegation_id`) so "approved by X on behalf of Y"
  is a fact in the row. Additive: with no delegation nothing changes, and the
  pending response keeps its old shape.
* Creation rules (Rust): never to yourself (422), never to a viewer or an
  inactive user (409), only a current approver may delegate (403), dates not
  backwards, not already over, at most 366 days, no overlap with a live
  delegation to the same person (409).
* Decision rules (Python): the dates are compared with the UTC date at the
  moment of the decision (no cleanup job, an expired or not-yet-started
  delegation does nothing); revoked ones do nothing; the delegator must still
  be an approver; the order's destination warehouse must be inside the
  delegator's warehouse scope (an empty, unreadable or stale scope is "none":
  fail closed) on top of the delegate's own scope, which the route guard
  already applies; if the delegator asked for the order, the same
  self-approval limit applies to the substitute (approving is refused above
  it; rejecting is not a self-approval); a delegation is never transitive.
  A refusal that comes from a delegation answers
  `po_approval_delegation_not_permitted` (403), otherwise
  `po_approval_not_approver`.
* The decision events (`purchase.approval_approved|rejected`) gain an
  `on_behalf_of` detail; two new events (`approval_delegation.created|revoked`)
  and the audit target `approval_delegation` exist in
  `backend/activity/events.py`, `backend/audit/catalog.py` and their Rust
  mirrors (`routes/r1/alerts.rs`, `audit/catalog.rs`, whose tests pin the new
  sizes: LEGACY 75, target types 29, audit actions 110, stored actions 114).
  The `purchase_order.approved|rejected` webhook payload is unchanged.

**Tests.** `cargo test` (the pure window, delegate and body rules);
`backend/tests/test_po_approval_delegation.py` (the Python decision path with
rows written the way Rust writes them: delegate decides, expired, not yet
started, revoked, delegator no longer an approver, viewer, transitive, own
request, delegator scope and a scope that cannot be read, the delegate's own
scope, the delegate's inbox); and `run_delegation` in
`tests/contract/contract_test.py`, a sequence rather than a diff because there
is nothing to diff against: Rust creates, lists and revokes, Python decides,
and every refusal is checked against the database.
## 10. Commitment fulfillment outlook (new Rust routes, no Python twin)

Owner-approved feature (2026-10-06, corporate committed demand is the first
priority). For each open committed-demand row: will it be met on its delivery
date, given stock, the open purchase orders with their expected arrival dates
(a supplier promise a person accepted, or generated + the supplier's lead
time), the supplier lead-time history and the other commitments competing for
the same stock? Per commitment: a verdict (`on_track`, `at_risk`, `will_miss`,
`insufficient_data`), the projected shortfall in units, the date stock would
cover it, and a reason code with the figures behind it (the frontend renders
the es/en sentence). Plus a tenant summary with a per-customer and a
per-contract roll-up and the list of data gaps.

**These are NEW routes. There is no Python implementation to fail over to**
(`deploy/rust-api/routes.d/42-commitment-outlook.caddy.example` has no `api`
upstream). With the Rust container down they answer 502, the panel says "the
outlook is not available" and the rest of the screen, served by Python, keeps
working. Kill switch: move the file to `routes.d/off/`; the routes then 404 at
Python and the panel degrades the same way.

| Route | What |
|---|---|
| `GET /api/v1/committed-demand/outlook` | list; filters `sku`, `customer`, `verdict`, `contract_root_id`, `warehouse_id`, `delivery_from`, `delivery_to`, `limit` (1..2000); misses first, then by date |
| `GET /api/v1/committed-demand/outlook/summary` | counts by verdict, shortfall, first problem date, `by_customer`, `by_contract`, `data_gaps` |
| `GET /api/v1/committed-demand/{id}/outlook` | one commitment: the commitments competing for its stock, stock by warehouse, every arrival with its date and where the date came from, and whether it counts |

Any signed-in role reads them (viewer included); an API key is refused like on
the rest of the internal `committed-demand` tag. They write nothing. A
warehouse-scoped caller sees only commitments naming their warehouses, judged
on THEIR warehouses' stock and arrivals (`scope: "warehouses"`), like
`GET /committed-demand`.

### How it stays consistent with the existing at-risk verdict

`GET /committed-demand` (Python) keeps its at-risk flag. The outlook is not a
second authority: it uses the same expected units (`quantity x probability`),
the same earliest-first allocation and the same supply-at-delivery rule as
`allocate_risk`, and `backend/tests/test_commitment_fulfillment_reference_pure.py`
demands the same shortfall for 1,500 seeded cases whose arrivals are all dated.
Where it knows more it says so: `allocate_risk` assumes an open purchase order
lands "within the lead time"; the outlook uses the order's real expected date
and answers `insufficient_data` when that date does not exist.

### Where the code is, and how it is tested

* `backend-rs/src/fulfillment/core.rs`: the arithmetic only (no DB, no clock),
  with unit tests.
* `tests/contract/fulfillment_reference.py`: the same rules in the plainest
  Python, the spec. `tests/contract/gen_fulfillment_fixtures.py` writes seeded
  cases and the reference's answers to
  `backend-rs/tests/fixtures/fulfillment_cases.json` (floats as strings, so no
  parser moves a last digit); the Rust test `differential_against_python`
  replays every case and demands exact equality, floats compared bit for bit.
  The Python test `test_the_differential_fixtures_are_current` fails when the
  fixture is stale. Mutating a rule (slack days, the `<=` on the lead time, the
  float tolerance) turns the Rust test red, which was checked by hand.
* `backend-rs/src/fulfillment/data.rs`: SELECTs only. Reads each number the way
  its Python owner does: commitments and scope as `list_for_tenant`; stock as
  `list_stock` summed over the caller's warehouses; arrivals as
  `get_incoming_detail` (per LINE, so a promised date stays with its line);
  the date of an order line as `get_overdue_receptions` /
  `_effective_lead_time`; the lead time of a new order as
  `resolve_planning_inputs` (learned > set on the SKU > supplier / category /
  global rule).
* `tests/contract/cf_outlook_cases.py` (called from `contract_test.py`): hand-
  computed scenarios seeded in the database, the permission pairs, scope,
  filters, "the reads wrote nothing", and a live comparison with Python's flag,
  shortfall and safe order date where no purchase order is involved.
* Frontend: `CommitmentOutlookPanel` under the committed-demand panel, copy in
  `translations.ts` (`outlook.*`, es and en).

### Decision rules to confirm (chosen here, owner to confirm)

1. **Insufficient data, never a guess.** No stock row; a shortfall with no known
   lead time (the shortfall is still shown); or a shortfall the undated order
   units could close. The 15-day system default is NOT a lead time here:
   nobody set it, so it is not a date.
2. **An order with no usable date does not count as supply**: an order whose
   expected date has already passed (overdue) or whose supplier has neither
   three real receptions nor a declared lead time. If it is the only thing that
   could close the gap the verdict is `insufficient_data`; if it cannot close
   it, the shortfall is reported as a minimum (`shortfall_is_minimum`).
3. **At risk although covered**: the cover depends on a purchase order landing
   fewer than **3 days** before delivery (`AT_RISK_SLACK_DAYS`). A chosen
   threshold, not a measured one.
4. **At risk vs will miss**: with a shortfall, `at_risk` if a new order placed
   today still arrives by the delivery date (`today + lead <= delivery`),
   `will_miss` if not, or if the delivery date has already passed.
5. A transfer in transit counts as available today (as `annotate_risk` does).
6. Every open commitment competes for stock, including rows marked "already in
   the history" (the customer still expects them), the same as the at-risk flag.
7. **Not modelled**: the statistical forecast of other customers' sales eating
   the stock before the delivery date, as in the existing at-risk flag. A
   commitment shown `on_track` is covered against the OTHER commitments, not
   against ordinary demand. Contract releases not yet materialised as
   commitments are not evaluated (the product does not plan on them either).
8. No `commitment.at_risk` event/alert: it would need a scheduled scan in
   Python (the Rust API has no worker) that re-implements this arithmetic, which
   is exactly the second authority this feature avoids.

### Results of the outlook (2026-10-06)

* `cargo test`: 144 passed, 2 ignored (the 24 new tests: 20 unit tests of the
  core, 3 of the route helpers, and `differential_against_python`). The
  differential replays 1,500 SKU cases (3,981 commitment verdicts, 900 random
  and 600 built on a rule's edge), 600 order-line arrivals, 400 supplier lead
  times, 400 SKU lead times and 239 roll-ups against the Python reference:
  exact equality, floats bit for bit.
* Python: `test_commitment_fulfillment_reference_pure.py` 3 passed (same
  shortfall as `allocate_risk` over 1,500 seeded cases, missing stock, fixture
  freshness).
* Contract (`cf_outlook_cases.py`), disposable Postgres, Python on `:8061` and
  the Rust debug build on `:8066`, `TESTING_MODE=true`: **53 of 53 pass**
  (list 36, summary 6, detail 11). In the same full harness run, 18 cases of
  OTHER routes failed on one cause that is not this feature: that machine's
  Python session reports timestamps as `-06:00` and Rust as `+00:00` (known
  divergence 5, which assumes a UTC database session).
* The frontend panel was type-checked (`tsc --noEmit`, clean) and the i18n
  parity and missing-key scripts pass. **It was not opened in a browser.**
* Not verified: the Caddy example against a real Caddy, and the routes behind
  the gateway; rate limits and `TRIAL_EXPIRED` (as for every Rust route).
## 10. Customer portal: the first route group written in Rust first (2026-10-06)

A read-only portal where a corporate customer opens a private link and sees
ONLY their own commitments (the mirror of the supplier confirmation link).
**It has no Python twin, so it has no failover**: the gateway file
`deploy/rust-api/routes.d/50-customer-portal.caddy.example` sends
`/api/v1/customer-portal/*` to `api-rs` only. With Rust down those paths answer
502 at the gateway (the customer page shows "try again later"); with the file in
`routes.d/off/` Python answers its own 404 (the page shows "invalid link").
Nothing else in the product depends on it.

Routes (`backend-rs/src/routes/customer_portal.rs`):

| Route | Who |
|---|---|
| `GET /customer-portal/customers`, `GET /links`, `GET /links/{id}` | signed-in user (viewer ok), company-wide scope only |
| `POST /links`, `PATCH /links/{id}`, `POST /links/{id}/revoke`, `/reopen`, `PUT /links/{id}/promised-dates` | analyst or admin; never an API key (Internal) |
| `GET /customer-portal/public/{token}`, `POST /public/{token}/respond` | nobody logged in: the token is the credential |

Security model, reused from the supplier link: 256-bit token, only its SHA-256
stored, constant-time compare; every bad link (malformed, unknown, expired,
revoked) is one identical 404 `customer_portal_unavailable`; rate limits per
address (120 reads / 20 writes per 10 min) and per link (60 / 10), counted for
every request valid or not, in `auth_rate_events` under a digest key; body capped
at 16 KB before parsing, strict fields (unknown keys refused), comments at most
500 characters with no control characters; `Cache-Control: no-store`,
`Referrer-Policy: no-referrer`, `X-Robots-Tag: noindex`. The page is a whitelist
(`PUBLIC_PAGE_KEYS`, `PUBLIC_COMMITMENT_KEYS`): SKU, display name, quantity,
requested date, status, the customer's own last answer, and a promised date only
when the link has `share_dates` AND the tenant set one. Never stock, cost,
probability, notes, warehouses, supplier names, other customers or verdicts.
The customer's answer ("received" / "the date does not work" + comment) is an
append-only event, recorded under the link's creator (the bell, severity warning
with reason `customer_date_objection` for an objection); it never edits a
commitment.

Schema (additive, `backend/inventory/customer_portal_migrations.py`):
`customer_portal_links`, `customer_portal_promised_dates`,
`customer_portal_events`; all cascade from `tenants`, listed in the erasure
order and the export (without hashes). Events, audit vocabulary and the Rust
mirrors: 7 `customer_portal.*` events; `LEGACY` 73 -> 80, target types 28 -> 29,
audit actions 108 -> 115, stored actions 112 -> 119 (asserts updated).

Not obvious: the stock table has one row per warehouse, so the display name is
picked with a `LATERAL ... LIMIT 1`; a plain join repeated commitments (found by
running against a real database). Python's route registries (`public_surface`,
`UNAUTHENTICATED`, `PUBLIC`) iterate FastAPI routes and fail on a stale entry, so
they are deliberately NOT edited; `backend/tests/test_customer_portal_registry.py`
reads the Rust source instead (only the two token routes are unauthenticated,
every other handler goes through `manager()`, Rust and Python vocabularies match,
Caddy has no Python failover, copy exists in both languages).

### Results (local, throwaway Postgres 16, TESTING_MODE=true)

Contract harness, Python from this branch on `:8031`, Rust debug build on `:8041`:
**490 cases, 486 PASS, 4 SKIP, 0 FAIL** (the 477 of the integrated run plus 13
Rust-only portal cases: permission pairs, validation, whitelist shape, leak test,
token probing with nine bad-token kinds, answers, revoke/reopen, cross-tenant
scope, whole-tenant erasure). `cargo test`: 136 passed, 2 ignored. Registry
pytest: 14 passed. A separate run of a Rust instance with `TESTING_MODE=false`
confirmed the limiter: 60 reads of one link pass, the 61st is 429 from any
address, and an unknown well-formed token is counted the same way.
## 10. IP allowlist (per tenant, 2026-10-06)

A corporate security feature, available on every tier (no plan gate): an admin
lists the IPv4 / IPv6 addresses and CIDR ranges that may reach the account, and
turns the list on. Tables `ip_allowlist_policies` (one row per tenant, `enabled`)
and `ip_allowlist_entries` (`cidr` stored normalised, `label`, `created_by`,
`created_at`, unique per tenant) are created by Python's additive migration. A
tenant with no policy row, or a disabled one, is not filtered: with nothing
configured nothing changes.

**The admin routes exist only in Rust, so there is no Python failover for
them** (`GET /ip-allowlist`, `POST /ip-allowlist/entries`,
`DELETE /ip-allowlist/entries/{id}`, `PUT /ip-allowlist/policy`; admin only,
never callable with a key, Caddy file `routes.d/50-ip-allowlist.caddy.example`).
With `api-rs` down the settings card shows an error and the policies already
stored keep being enforced, because **enforcement runs in both services**: after
authentication on every JWT and `sk_live_*` call (Rust `auth::current_user`,
Python `get_current_user` and `_authenticate_api_key`), on login and refresh
(Python), and on the training-progress websocket (Python). A refusal is
`403 ip_not_allowed` with `error_params: {"ip": ...}`; for a key it comes before
the rate window and the meter, so a refused call is neither counted nor billed;
at login it comes after the password matched, so a wrong password reveals
nothing about which tenants filter. Not covered: the SCIM bearer token, the
signed inbound webhooks (Twilio, inbound e-mail) and the public trial endpoint,
which are not the tenant's people or keys.

**Lockout guard** (same spirit as SSO "require"): enabling is refused with
`409 ip_allowlist_lockout` unless the caller's current address is covered by the
entries, and so is deleting the entry that leaves it uncovered. A caller whose
address cannot be read cannot enable (`ip_allowlist_address_unknown`).
`GET /ip-allowlist` returns `your_ip` and `your_ip_covered`, which is exactly
what the guard will decide, and the screen shows it. Disabling is never blocked
(from an allowed address; an outside caller is refused like everything else).

**Events.** `account.ip_allowlist_changed` (warning; reason says what was done:
entry added / removed, policy enabled / disabled) and `account.ip_access_refused`
(warning, one row per tenant and address per 10 minutes, written by whichever
service saw it first). Both are in `backend/activity/events.py`,
`backend/audit/catalog.py` (LEGACY, target `ip_allowlist`) and the Rust mirrors
(`activity.rs`, `routes/r1/alerts.rs`, `audit/catalog.rs`, size asserts updated).

### The client address (the trap)

`X-Forwarded-For` is written by the caller, so reading its first entry would let
anyone claim an allowed address (`api/v1/trial.py` does that for a speed bump
that nothing depends on; the allowlist must not). Proxies **append** the address
they saw, so the trustworthy entries are the ones the deployment's own proxies
wrote, counted **from the right**. The rule, identical in `ip_allowlist.rs` and
`backend/ip_allowlist/service.py`: build the chain as the header entries plus the
socket peer, and the client is the entry `TRUSTED_PROXY_HOPS` positions from the
right end (a shorter chain resolves to its first entry). Anything a client put
further left is ignored. With the default `0` the header is not read at all and
the socket peer is the client. The value is a new environment-only setting
(`TRUSTED_PROXY_HOPS`, in the registry, read by both services from the same
`.env`). An IPv4-mapped IPv6 address is the IPv4 address; an unreadable value is
outside every allowlist.

Hop counts per topology (derived from how each hop treats the header, **not
measured on the production box**):

| Topology | What the API sees | `TRUSTED_PROXY_HOPS` |
|---|---|---|
| Today: Caddy -> Next.js -> API | public Caddy sets `client`; Next's rewrite proxy appends its peer (`client, caddy`); the API's peer is the frontend | 2 |
| With the gateway: Caddy -> Next.js -> gateway -> API | the gateway (private ranges trusted, see `Caddyfile.gateway.example`) appends the frontend; the API's peer is the gateway | 3 |

The public Caddy must keep Caddy's default (no `trusted_proxies`), which
overwrites any incoming `X-Forwarded-For` with the real peer: that is the line
that stops a browser from injecting a chain. The API ports must be reachable only
through the proxies, or a direct caller can write the whole chain.

**Calibrate, do not trust the table**: with the allowlist still off, open the
card as an admin. "Your current address" is what the server derives; raise or
lower `TRUSTED_PROXY_HOPS` until it shows your real public address, and only then
add it and enable. A wrong count cannot lock anyone out by surprise: the guard
refuses to enable a list that does not cover the address the server sees. It can
still be wrong in a quieter way: a count too low makes the server see a proxy,
so the list would judge the proxy (shared by every visitor) instead of the
person. The card shows the address next to the entries so that is visible before
enabling.

### Tests and what was run

* `backend/tests/test_ip_allowlist.py` (22): derivation (spoofed prefix, hops,
  mapped IPv6, garbage), JWT and key enforcement, forged chain, IPv6 ranges,
  disabled / empty / other-tenant policy, refusal event throttle and actor, key
  not metered, login after password and no refresh token minted, refresh.
* `cargo test`: 10 new in `ip_allowlist.rs` (derivation, CIDR parse / normalise /
  match, what is refused).
* `tests/contract/contract_test.py` `run_ip_allowlist` (40 cases): the Rust-only
  routes have nothing to diff against, so each step asserts the Rust answer and
  the rows; the enforcement requests go to Python AND Rust and the two refusals
  must be identical. Needs both servers started with `TRUSTED_PROXY_HOPS=1`.
  Full harness on a throwaway database: **517 cases, 514 pass, 3 skip, 0 fail**.
