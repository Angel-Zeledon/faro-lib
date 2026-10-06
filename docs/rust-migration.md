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
* Rust-only (new feature, no Python route, **no failover**): the organization
  hierarchy, `/org/*`; see "Organization hierarchy" at the end.
* Done (contract renewals, new routes with NO Python implementation): `GET /supply-contracts/renewals`,
  `GET /supply-contracts/{root_id}/comparison`, `POST /supply-contracts/{root_id}/renew`. See "Contract
  renewals" below: **these have no Python failover** (Python would read `renewals` as a contract id and
  answer "not found"), so their proxy file lists `api-rs` only. Python owns the additive schema and the daily
  alert pass.
* Shared modules, one implementation each: `audit/` (the whole
  `backend/audit/catalog.py` as data, and `audit::record`, the
  `AuditMiddleware` writer every catalogued Rust route calls with its route
  template); `auth/warehouse_scope.rs`; `limits.rs`; `activity.rs` (the
  `Event` specs every Rust route records through); `webhook_events.rs` (the
  emit half of `backend/webhooks/service.py`: it only inserts
  `webhook_deliveries` rows, which Python's delivery loop sends).
* Done (new feature, Rust-first): per-tenant session and password policy,
  `GET`, `PUT`, `DELETE /session-policy` and `POST /session-policy/unlock/{user_id}`
  (`routes/session_policy.rs`). **No Python twin and no Python failover for
  these four routes**; see the section "Session and password policy" below.
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
## 10. Two-step sign-in (MFA): the first NEW routes, and the first without a Python failover

Owner-approved feature, 2026-10-06: TOTP (RFC 6238, authenticator apps) plus
single-use recovery codes, optionally REQUIRED per tenant by an admin.

**Split.** Auth stays in Python this pass, so the sign-in half is Python:
`POST /auth/login` answers `mfa_required` (an opaque challenge token, 5 minutes)
instead of the token pair when the user has an active enrollment, and
`mfa_enrollment_required` (an enrollment token, 15 minutes, no session) when the
tenant requires MFA and the user has none. `POST /auth/mfa/verify` takes the
challenge plus a code and mints the same token pair a password-only login
always minted (`backend/auth/mfa.py`, `backend/api/v1/auth.py`). The management
half is Rust, under `/api/v1/mfa/` (`backend-rs/src/routes/mfa/`): status,
`enroll/begin`, `enroll/confirm`, `disable`, `recovery-codes/regenerate`,
`GET|PUT policy`, `users/{id}/reset`.

**No Python failover.** These routes have no Python twin, so
`deploy/rust-api/routes.d/45-mfa.caddy.example` lists `api-rs` as the only
upstream: with the Rust service down they answer 502, not a Python 404. The
login challenge keeps working without them. With no rows in `user_mfa` and
`tenants.mfa_required` false, login is exactly what it was, which is the
rollback: delete the rows and clear the flag.

**Bit-for-bit agreement.** `backend-rs/test-vectors/mfa.json` is read by the
Rust unit tests and by `backend/tests/test_mfa.py`: the RFC 6238 appendix B
SHA-1 vectors (and RFC 4226 appendix D), the acceptance window (one step each
side), the recovery-code HMAC for fixed codes, and a Fernet token written by
Python's `cryptography` that Rust must decrypt.

**What is stored.** `user_mfa` (secret Fernet-encrypted under the key every
stored secret uses, status `pending|active`, `last_used_step`),
`user_mfa_recovery_codes` (HMAC-SHA256 of the code keyed with `SECRET_KEY`,
`used_at`), `mfa_challenges` (SHA-256 of the opaque token, purpose, attempts),
`tenants.mfa_required`. All additive; erased with the tenant and the user, and
left out of the data export. The Rust service reads the Fernet key from
`INTEGRATIONS_SECRET_KEY` or the file Python generates, and never creates it:
with neither, enrollment answers `503 mfa_unavailable` out loud.

**Properties, each pinned by a test.** A time-step is accepted once
(compare-and-set on `last_used_step`; a replay across the two services is
refused); a recovery code is burned by compare-and-set; five guesses per
challenge counted by one atomic UPDATE, and ten per user per ten minutes in
`auth_rate_events` under the key `mfa:<user_id>`, shared by both services;
requiring MFA is refused (`mfa_admin_not_enrolled`) unless the acting admin is
enrolled, and enabling it ends the sessions of password users who are not
enrolled yet; an admin reset also ends that user's sessions; disabling is
refused while the tenant requires MFA. `refresh_tokens` rotation and
`sessions_invalid_before` are untouched (they are only written, the same way a
password change writes them).

**Exempt on purpose, and tested** (`test_mfa.py::TestExemptions`): social and
enterprise (OIDC) sign-ins follow the identity provider's own MFA and policy,
so neither an enrollment nor a tenant requiring MFA stops
`/auth/oauth/exchange`; API keys (`sk_live_*`) are machine credentials and are
unaffected (the `/mfa` routes themselves are `Exposure::Internal`, so a key is
refused there).

**Events and audit.** `account.mfa_enrolled`, `_disabled`,
`_recovery_codes_regenerated`, `_recovery_code_used`, `_reset`,
`_policy_changed` in `backend/activity/events.py`, mirrored in `activity.rs` and
`routes/r1/alerts.rs` (the parity test re-reads the Python source). Two
catalogued routes, `PUT /mfa/policy` (`config.changed`) and
`POST /mfa/users/{user_id}/reset` (`user.mfa_reset`), live in
`RUST_ONLY_ROUTES` of `backend/audit/catalog.py` so the Python "every
catalogued route is real" test skips exactly them. Size asserts moved to ROUTES
58, audit actions 109, stored actions 113.

**Results (2026-10-06, this worktree, throwaway Postgres 18 on a private port,
Python dev API on :8071, Rust debug build on :8062, plus a second Rust with
`TESTING_MODE=false` on :8063).** `tests/contract/mfa_cases.py`: 15 cases, 15
PASS (permission pairs, keys refused, encrypted secret decrypted by Python's
Fernet, HMACs only, replay across services, recovery code once across
services, policy and session cuts, audit and event rows, the enrollment-token
flow end to end, five-guess burn, cross-tenant 404, the Rust throttle).
`backend/tests/test_mfa.py`: 35 pass. `cargo test`: 129 pass, 2 ignored.
The QR code is drawn in the browser by `Frontend/src/lib/qr.ts` (no
dependency, no external service); `npm run check:qr` decodes its output with a
separately written reader (format word, both copies, every Reed-Solomon block,
payload round trip) for versions 1 to 10. A real authenticator app scanning the
code was NOT tested (none was available).
## 10. Multi-currency (new Rust routes, no Python failover)

Owner-approved feature, built the strangler-fig way but with one difference
from every other group: these routes are NEW, so Python has no copy of them.
**There is no Python failover.** With `api-rs` down the exchange-rate screen
shows nothing (502 from the gateway); with the Caddy file removed it is a 404.
Nothing else stops: Python purchase orders, budgets, the cash calendar and
approvals read the `exchange_rates` table themselves, with the same rules.
Gateway example: `deploy/rust-api/routes.d/50-multicurrency.caddy.example`
(no `api:8010` upstream on purpose).

### What it does

* The tenant's **base (reporting) currency** is the existing currency setting
  (`tenants.settings.currency`, default CRC). Its PATCH still only relabels.
* A money amount that needs a currency carries an explicit ISO 4217 code from
  `SUPPORTED` (13 codes). Supplier price (`sku_suppliers.currency`) and a
  purchase-order **line** (`inventory_po_items.currency`) may differ from base.
  `NULL` means "the tenant's own currency", which is what every row written
  before this feature means. The currency is per LINE, not per order, because one
  order already spans suppliers (it is split per supplier on send).
* A **dated exchange-rate table** (`exchange_rates`): 1 unit of `currency` =
  `rate` units of `base_currency`, effective from `effective_date`, an optional
  source note, who entered it. Tenant-entered only; no external feed. The base
  currency is stored on the row, so relabelling the base makes the old rates
  stop applying (a missing rate, said out loud) instead of silently meaning
  something else.
* The **rate used is recorded on the document when it is written**:
  `inventory_po_items.fx_rate / fx_rate_date / fx_rate_id / fx_base_currency /
  value_base`. Later rates, edits and deletions never move an order. A costed
  foreign line with no rate has `value_base` NULL ("unconverted"); the order's
  `inventory_po_log.fx_unconverted_lines` counts them and its `total_value`
  leaves them out, exactly like a line with no cost. **Never converted with a
  made-up 1.0.**
* `inventory_po_log.total_value` keeps its meaning (the order's value in the
  tenant's currency) and so ROI, recaps and the assistant need no change. With no
  foreign line anywhere the old float arithmetic runs byte for byte.

### The rules (`backend/fx/reference.py` is the spec, `backend-rs/src/fx.rs` the twin)

1. Exact decimals; a float is first turned into the decimal its shortest
   round-trip text spells (Python `repr`, Rust `{}`), never its binary value.
2. One rounding, at the end: `qty x unit_cost x rate` rounded ONCE to 2
   decimals, **half up** (ties away from zero; all amounts are non-negative).
   Two decimals are the storage precision in every currency; the display
   precision of a currency (0 for CRC) is a rendering concern.
3. As-of lookup: the latest `effective_date` not after the document's date (the
   UTC date the order is written). A future-dated rate is not used until its day.
4. No rate means no conversion (`None`, never 1.0).
5. A line in the base currency is not converted; a total is the exact sum of the
   2-decimal line values.
6. A stored rate is positive, within [1e-10, 1e9] and has at most 10 decimals
   (refused, never rounded). Inputs must be below 1e30 with at most 40 decimals.

The number type in Rust is a digit string (schoolbook arithmetic), not `i128`:
qty x cost x rate can have 70+ digits, and a wrapping overflow would be a silent
wrong total.

### Routes (`backend-rs/src/routes/fx_rates.rs`)

| Route | Who | Notes |
|---|---|---|
| `GET /tenant/currency/rates` | every role, read key | current base only, `in_force` per row, `other_base_count` |
| `POST /tenant/currency/rates` | admin | 201; 409 `fx_rate_exists` on the same currency + date |
| `PATCH /tenant/currency/rates/{id}` | admin | rate and/or note; the currency and date are not editable |
| `DELETE /tenant/currency/rates/{id}` | admin | written orders keep their recorded rate |
| `GET /tenant/currency/rates/resolve?currency=&on=` | every role, read key | 404 `fx_rate_missing` when none |
| `POST /tenant/currency/convert` | every role, read key | a read-only preview, says which rate it used |

Writes are admin only (like the base currency itself: a rate moves every
converted total) and internal (no API key can hold the role). Events
`currency_rate.created / changed / deleted` are in `activity/events.py`, the
audit catalogue (LEGACY, target type `currency_rate`) and their Rust mirrors.

### Where Python honours it

`backend/fx/` (`reference.py`, `service.py`, `migrations.py`, `vectors.py`):
PO creation (`roi_service.log_po_generation`, `create_manual_po`; `currency` on
`POLineItem` / `ManualPOLine`), purchase budgets (`check_order`, usage; the hard
cap refuses an order it cannot fully value with `purchase_budget_fx_rate_missing`,
an administrator can override with a reason), the cash calendar, PO approval
amounts, reception fill value, the BI datasets (`currency` column, converted
`line_value`), the supplier PDF (each line in its own currency; several
currencies print as one amount per currency, never one summed figure), supplier
price currency on the SKU-supplier link, and the price-history comparisons
(`cost_alerts`) which skip foreign lines.

### Tests

* `cargo test`: `fx::tests` (rules, parsing grammar, rounding, overflow, rate
  validation, as-of lookup), `fx_rates::tests`, and the golden vectors
  (`backend-rs/tests/fx_vectors.json`, 3,011 cases generated by the Python
  reference, replayed exactly by Rust). Audit and event mirrors updated (LEGACY 76,
  target types 29, audit actions 111, stored actions 115).
* Differential: `tests/contract/fx_differential.py` runs fresh seeds through the
  Python reference and `stockai-api fx-eval` and demands identical answers
  (40,088 cases over 8 seeds when written; the contract run does 20,000 fresh).
* pytest: `test_fx_reference.py` (hand cases, an independent `Fraction` oracle
  over 4,000 seeded cases, golden file is current), `test_multicurrency.py`
  (orders, recorded rates, history, budgets, hard cap, cash, approvals, PDF,
  supplier price currency, permission pairs, state asserted in the database).
* Contract: `tests/contract/fx_cases.py` (hooked by `run_fx_section`): rate CRUD
  with permission pairs and refusals asserting state, events in `activity_logs`,
  another tenant's rate untouched, and the cross-service promise (a rate entered
  through Rust is the one a Python order converts with and records; editing or
  deleting it afterwards moves nothing already written).

### Deferred (not in this release)

Price-break tables in a foreign currency (they read in the supplier link's
currency); a screen for the supplier price currency (API only); stock unit costs,
the optimizer and the "does it fit the cash" check stay in the base currency;
a "convert now" action for an order written before its rate existed (it stays
unconverted until someone acts); inverse and cross rates (only foreign -> base);
budgets in a currency other than the base (they only count lines converted INTO
their currency); an external rate feed; a maximum age for a rate; the approval
rule amount still leaves an unconverted line out (same as an uncosted line).
## 10. Custom roles with enforced permissions (new Rust routes, 2026-10-06)

The defect: the product stored 12 per-user permissions (`user_permissions`,
`PATCH /users/{id}/permissions`) that no code ever read. The owner already
removed their checkboxes on 2026-10-02 for that reason. They are still stored
and still **not enforced**, on purpose (enforcing rows nobody meant would lock
people out); the enforced mechanism is the **custom role**.

* **Schema (Python owns it, additive):** `custom_roles` (tenant, name unique
  per tenant ignoring case, `permissions TEXT[]`) and `users.custom_role_id`.
  No foreign keys: a role id that points at nothing reads as "no permissions".
  Whole-tenant erasure and the data export carry the table.
* **One catalogue, two languages:** `backend/auth/permissions.json` (17
  permissions, the path rules and a `probes` table) and its byte-identical copy
  `backend-rs/src/auth/permissions.json`. Python (`backend/auth/permissions.py`)
  and Rust (`auth/permissions.rs`) run the same longest-prefix matcher over
  route templates, with every `{param}` normalised, and the same probes.
  `test_permission_catalogue_parity.py` fails on any difference, on a
  permission no rule uses, and on any mutating route (Python app, and Rust
  sources scanned) with no rule.
* **Enforcement:** inside `get_current_user` (Python) and `auth::current_user`
  (Rust, the matched template comes from a route layer), for people only. A
  user with no custom role is never touched. A user with one is checked on
  every request against a fresh database read: **there is no cache, so the
  staleness bound is zero** (the JWT's built-in role keeps its 15 minutes).
  Fail closed: dangling or foreign role id, a name outside the catalogue, an
  unreadable row (`permission_check_failed`), and a mutating route no rule
  covers (`permission_denied` with `unclassified_route`) all answer 403. An
  unclassified GET stays readable (the sensitive reads are listed).
* **A custom role narrows, never widens.** The route's built-in role guard
  still runs; a viewer holding a role with every permission is still a viewer.
  API keys are untouched (read / write scope as before).
* **New Rust routes, no Python failover:** `GET/POST /roles`,
  `GET /roles/permissions`, `GET /roles/me`, `GET/PATCH/DELETE /roles/{id}`,
  `PUT /users/{id}/custom-role`. Example `deploy/rust-api/routes.d/
  50-custom-roles.caddy.example` lists `api-rs` alone. Without it the role
  screens 404 and the users list shows no selector; enforcement still holds.
* **Escalation rules (all tested end to end):** only a built-in admin edits
  roles; nobody grants, changes, removes or assigns what is outside their own
  set (an unrestricted admin holds the whole catalogue, so a restricted editor
  cannot touch a user who has no role, remove anybody's role, or demote the
  unrestricted admin); nobody edits, deletes or assigns the role they hold, or
  assigns one to themselves; the last active admin without a role cannot be
  given one (`custom_role_last_admin`); a role in use cannot be deleted; every
  write runs under the tenant advisory lock.
* **Audit:** `audit.role.created|updated|deleted` and `audit.user.role_assigned`
  with before/after (catalogue entries in both languages, listed in
  `RUST_ONLY_ROUTES` for the "catalogued routes exist" test), plus the activity
  events `account.custom_role_created|updated|deleted|assigned` (warning, so
  they reach the bell).
* **Tests:** Rust unit tests (matcher, probes, decisions, validation), 20
  pytest cases of enforcement on the Python-served routes, 15 catalogue and
  coverage tests, and 63 cases in `tests/contract/custom_roles_cases.py`
  (hooked into `contract_test.py`), including identical 403s on both servers.

Known gaps: the mobile users screen (`UsersMobile`) has no role selector; the
users list has no "role" column; Python's own user delete / demote / suspend
still do not guard against removing the last admin (pre-existing, outside this
feature).
### Session and password policy (new feature, Rust-first, 2026-10-06)

Owner-approved enterprise control: per tenant, a maximum session length, an
idle timeout, a password minimum length / mixed case / symbol, a password
maximum age, a concurrent-session limit and a lockout after failed logins.
**No row, or a row with nothing set, behaves exactly as before** (tests:
`backend/tests/test_session_policy.py::TestNoPolicyChangesNothing`).

**Who does what (auth stays Python this pass).**

| Piece | Where |
|---|---|
| Schema: `tenant_session_policies`, `users.last_activity_at / password_changed_at / failed_login_count / locked_until` | Python (`backend/db/migrations.py`, additive; the table cascades from `tenants` and is in `data_export.py`'s lists) |
| Admin routes: GET / PUT / DELETE `/session-policy`, POST `/session-policy/unlock/{user_id}` | **Rust only** (`routes/session_policy.rs`), admin only, no API key may call them |
| Login: lockout check before the password, failure counting, password age | Python (`backend/auth/session_policy.py`, `api/v1/auth.py`) |
| Refresh: maximum session length and idle timeout (the refresh token is deleted on a refusal) | Python |
| Password rules at reset and change-password (`password_policy`) | Python (`_reject_weak_password(password, tenant_id)`) |
| Concurrent sessions: oldest refresh tokens dropped at login | Python (`users/service.add_refresh_token`) |
| Token validation: maximum session length and idle timeout on every access token | **Both guards**, check for check (`backend/auth/guards.py` and `auth/session_policy.rs`, called after the `sessions_invalid_before` cut) |

**Rules a reviewer should know.**

* The access token gains a `sat` claim (session auth time) on refresh only; a
  login token has none and its own `iat` is the session start. The maximum
  length is measured from `sat`, else `iat`; a token with neither is not
  refused (it predates `iat` and lives 15 minutes at most).
* Idle time is `NOW() - users.last_activity_at` computed in the database, per
  person (not per device). Written at most once per 30 s, and **only for
  tenants with an idle limit**. A request that sends `X-StockAI-Background: 1`
  (the page pollers do) is checked but not counted, otherwise an open tab would
  keep the session alive forever. API keys are never subject to either limit.
* The password-age clock is `GREATEST(password_changed_at or created_at,
  password_max_age_since)`, and `password_max_age_since` restarts whenever the
  limit is set or changed, so switching it on never locks the tenant out. It is
  enforced at password login only (a provider-only account has nothing to
  expire); the person resets with the normal forgot-password flow.
* Lockout: the lock is checked before the password, so a correct password
  proves nothing to a locked account. The failure counter is one atomic
  `UPDATE ... +1`. The lock ends by itself, by an admin unlock, or by a password
  reset. Removing the lockout setting clears every lock of the tenant, so none
  can come back to life later. `account.user_locked_out` is recorded once per
  lock.
* `PUT` is a replacement (null or absent = not set), refuses unknown fields (a
  typo must not silently store nothing), and rejects non-integers, booleans as
  numbers and values outside the bounds with a structured code. Bounds are in
  the route, in `session_policy.py` and in CHECK constraints.
* Events (`events.py` + the Rust mirrors in `activity.rs` and `r1/alerts.rs`):
  `account.session_policy_changed` (names of the settings that moved, never a
  value), `account.user_locked_out`, `account.user_unlocked`, reason
  `too_many_failed_logins`. They reach the audit trail through `LEGACY` in
  `backend/audit/catalog.py` and `audit/catalog.rs` (not `ROUTES`: that table
  must name real Python routes, and `test_every_catalogued_route_is_a_real_route`
  enforces it). Sizes now: 76 LEGACY, 29 target types, 111 audit actions, 115
  stored actions.

**No Python failover.** The four routes exist only in `api-rs`. With the Caddy
group moved to `routes.d/off/` or the container down, the policy screen cannot
load or save; stored policy keeps being enforced by Python on every path it
serves, and by Rust on every path Rust serves. Example gateway file:
`deploy/rust-api/routes.d/50-session-policy.caddy.example`.

**Contract results** (`run_session_policy`, Python on `:58731` from this
worktree, Rust debug build on `:58732`, throwaway database on a private
Postgres, `TESTING_MODE=true`): 66 session-policy cases, all pass; the whole
harness 543 cases, 540 pass, 3 skip (the same three as before). Covered: the
permission pairs on all four routes (viewer and analyst 403 with state
unchanged, no auth, read and write keys refused), 16 refused bodies that store
nothing, the stored row, the event, the age clock, the same token refused (or
accepted) with the same code by both guards, background polls and the idle
clock on both services, concurrent sessions, refresh past the limits,
lockout, unlock (including another tenant's user), password rules at the
Python endpoints, the audit trail on both services. Rust unit tests: 14 new
(134 pass in the crate). Not contract-verified: `TRIAL_EXPIRED` on the write
routes, and the run used `TESTING_MODE=true` (the auth-rate limiter is off).

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
92 cases written against hand-canonicalized documents, not against the code
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
* `pytest`: `test_saml_xmlsig.py` (92), `test_saml_sso.py` (46), plus the
  existing SSO, audit, route-audit and tenant-erasure suites.
* `tests/contract/contract_test.py --only saml`: 78 cases against a real
  Python and a real Rust server on one database: every refusal checks the rows
  with SQL, a certificate stored by Rust verifies a signature in Python, and
  "require SSO" set through Rust closes Python's password door.
## 10. Audit stream: continuous audit export to a SIEM (new routes)

Owner-approved feature, 2026-10-06. A tenant admin points the account at an
HTTPS endpoint; every new `activity_logs` row (the audit trail AND the activity
feed, one table) is delivered there at least once, in order per tenant, with a
cursor, a delivery log, replay, and an automatic switch-off with an event after
repeated failure. S3-compatible destinations are not built.

**These are NEW routes with no Python twin: there is no Python failover.** The
Caddy file is `deploy/rust-api/routes.d/38-audit-stream.caddy.example` and lists
only `api-rs`. With the Rust container down (or the file in `off/`) the paths
answer Python's `404 {"detail":"Not Found"}` and the Settings card says it could
not load. Delivery is not affected by that, see below.

| Piece | Where | Language |
|---|---|---|
| Schema (additive): `activity_logs.stream_xid/stream_seq` by column default, `audit_streams`, `audit_stream_deliveries` | `backend/audit_stream/migrations.py` | Python |
| Config, cursor, replay, test, delivery-log routes (9) | `backend-rs/src/routes/audit_stream.rs`, `ssrf.rs` | Rust |
| Delivery loop (worker component `audit-stream`) | `backend/audit_stream/service.py` | Python |
| Pure rules (cursor text, NDJSON, backoff, disable) | `backend/audit_stream/policy.py` | Python |

### Why the delivery loop is Python, not Rust

Delivery has to POST through the webhook SSRF guard
(`backend/datasources/network.py`: resolve, refuse private and metadata
addresses, connect to the CHECKED address, never follow redirects) and reuses
the webhook signing and failure-day code. Those exist once, in Python. Porting
them would create a second guard to keep identical, for no gain: the loop is
not on a request path. The no-duplicate / no-loss argument does not depend on
the language (next section). The Rust routes only write `audit_streams`; the
two meet through that table, so the loop keeps delivering when the Rust
container is down. The Rust side re-implements only the SAVE-time half of the
guard (`ssrf.rs`, same ranges, conservative: anything not clearly public is
refused unless `SQL_SOURCES_ALLOW_PRIVATE_HOSTS=true`); the authoritative check
is Python's, on every delivery.

### Position, ordering and why nothing is lost under concurrent writers

`created_at` is the time of the INSERT, not of the COMMIT: a cursor on it would
skip a row whose transaction commits after the reader moved past its timestamp.
Instead every row gets two values from COLUMN DEFAULTS (so Python writers, Rust
writers and any future writer are stamped without knowing): `stream_xid`, the
writing transaction id (`pg_current_xact_id()`), and `stream_seq`, a global
sequence. The stream position is `(stream_xid, stream_seq)`, and the reader
takes only rows with `stream_xid < xmin` of its own snapshot
(`pg_snapshot_xmin(pg_current_snapshot())`): every transaction below `xmin` has
finished, so no row can later appear behind the cursor. The cursor moves by a
compare-and-set in the same statement that checks the lease token, so a late
success after a replay is discarded (logged `superseded`), never applied.

* **At least once**: the cursor advances only after a 2xx, by the rows of that
  request. A timeout after the receiver processed the batch resends the same
  rows; the receiver dedupes on the record `id`.
* **In order, per tenant**: `ORDER BY stream_xid, stream_seq`, one batch in
  flight per tenant (lease, `FOR UPDATE SKIP LOCKED`, 120 s expiry for a dead
  worker). The order is by writing transaction: two overlapping writers may
  differ from wall-clock order; it is the same on every replay and each record
  carries `at`.
* **Cost**: one long-open transaction anywhere in the database holds every
  stream back until it ends. That shows as `lag_seconds` / `pending_records`
  in `GET /audit-stream`, never as a gap.
* Rows written before the migration have NULL and are not streamed (history is
  `GET /audit/export`). A new destination starts at "now"; `replay` goes back.
* Proven by `backend/tests/test_audit_stream.py::TestOrderUnderConcurrentWriters`
  (6 writers x 40 transactions with out-of-order commits against a draining
  reader: every row exactly once, in order). Removing the `xmin` condition makes
  the open-transaction cases fail.

### Batch format (NDJSON, `application/x-ndjson`)

One `POST` per batch (up to `batch_size`, default 500, max 1000 rows and
1,000,000 bytes), UTF-8, one compact JSON object per line, `\n` after every
line. Headers: `X-StockAI-Stream: audit`, `X-StockAI-Delivery` (per attempt),
`X-StockAI-Batch-Records`, `X-StockAI-Batch-First`, `X-StockAI-Batch-Last`
(cursors), `X-StockAI-Signature: t=<unix>,v1=<hex>` where
`v1 = HMAC-SHA256(secret, "<t>." + raw body)`. Verify exactly like webhooks
(`backend/webhooks/signing.py`: raw body, 300 s tolerance, constant-time
compare). Answer any 2xx to acknowledge; anything else is retried from the same
cursor (10 s, 30 s, 1 min, 5 min, 15 min, 40 min, then hourly).

```json
{"schema":"stockai.audit.v1","cursor":"3405:88121","id":"act_1f0c9a2b7e44",
 "at":"2026-10-06T15:31:01.115860+00:00","tenant_id":"ten_...","actor":"usr_...",
 "action":"audit.dataset.deleted","kind":"audit","event":"dataset.deleted",
 "target_type":"dataset","resource":"ds_...","status":"success","context":{}}
```

`kind` is `audit` for what the audit trail covers (`event` and `target_type`
are its normalised names) and `activity` for the rest of the feed (those two are
null). `action` is the stored name. `cursor` is `"<xid>:<seq>"`, opaque to the
receiver apart from being what `replay` accepts. A `kind:"test"` record
(`cursor: null`) is sent by the test button and is not part of the stream.

### Failure, switch-off and re-enable

A failing destination is retried forever with the schedule above; a batch is
never skipped. After 3 different days of failure with no success in between
(the webhook rule) the stream is switched off (`enabled=false`,
`disabled_reason=failing_for_days`) and the activity event
`audit_stream.auto_disabled` (warning, reaches the bell) is recorded; an
address that now resolves to a refused network is switched off at once
(`host_refused`). The cursor is kept: re-enabling resumes exactly there, and
the rows stay in `activity_logs` meanwhile. `replay` only goes BACK (a cursor
ahead of the current one is `422 audit_stream_cursor_ahead`): a stream never
skips. Deleting the destination also deletes its delivery log.

### Verification (2026-10-06, throwaway Postgres 18, own ports)

`cargo test` 131 passed; `backend/tests/test_audit_stream.py` 59 passed; the
end-to-end run (Python API + Rust + a local HTTPS receiver with a self-signed
certificate) covered connect, signed batches from Python- and Rust-written rows,
an outage with recovery and no loss, replay, rotation, test, disable / enable,
the 3-day auto-disable with its event, trail entries and delete. The contract
section `run_audit_stream` in `tests/contract/contract_test.py` checks the
Rust-only routes against the database (there is nothing to diff against).
Not verified: a run against the production-sized `activity_logs` (the index is
partial on `stream_xid IS NOT NULL`, empty on creation), the Caddy file against
a real gateway, the screen in a browser.
### Organization hierarchy (new feature, Rust only; 2026-10-06)

A holding with subsidiary tenants and four consolidated READ-ONLY views
(committed demand, stock by signal, purchase orders, budget vs spend). The routes
and queries are in `backend-rs/src/{org/,routes/org.rs,routes/org_consolidated.rs}`.
**There is no Python route behind them, so the gateway has no failover for
them** (`deploy/rust-api/routes.d/50-organization.caddy.example` lists `api-rs`
only; with Rust down the answer is a 502, not a Python 404 that reads as "feature
absent"). Python owns the schema (`backend/organizations/migrations.py`,
additive: `org_links`, `org_link_grants`) and honours the feature wherever it
still serves the path (below).

**Grant model (fail closed).**
1. Reach exists only through an `org_links` row with `status='active'`, made by
   a two-sided handshake: the holding's admin mints a one-time code (`orgl_` + 48
   hex; only its SHA-256 is stored; shown once; 7 days), the subsidiary's admin
   redeems it from its own session. The child tenant is the redeemer's own tenant,
   never a field.
2. A link alone shows nothing: each holding user needs an `org_link_grants` row
   (admin role is not a grant; the admin grants themself).
3. One level: a tenant is at most one live child, and a live parent cannot be a
   child (and vice versa). Trial (`demo`) and non-active tenants cannot join.
4. Read-only; routes are `Exposure::Internal`, so an `sk_live_*` key or MCP never
   reaches them (refused again inside `resolve`). Children never see the parent
   or siblings; the subsidiary sees only the holding's NAME (needed for consent),
   never its label for it, who holds a grant, or other subsidiaries.
5. Either side ends the link at any time; that deletes every grant. A new link
   needs a new handshake (nothing is reactivated).

**Where the tenant ids come from.** `org::scope::resolve` derives them on every
request from the database: the person is read live (status `active`, not
scoped to warehouses), then the grants join (`GRANTS_SQL`, only the caller's
user and tenant id are bound). Every consolidated statement binds exactly that
list (`tenant_id = ANY($1)`; a unit test pins it for each, and that none writes).
`?tenant_ids=a,b` may only NARROW: ids are looked up in the entitled list; any
foreign id is one flat 404 `org_tenant_not_found` (no existence oracle) and
poisons the whole request. Management routes check the admin role in the database
as well as in the token.

**Roll-up rules that keep numbers honest.** Money is never summed across
currencies (a list per currency). A stock snapshot counts only when fresh by
Python's `is_fresh` rule (inputs unchanged, computed today, under an hour; the
Python `code_hash` cannot be checked from Rust, so a deploy that changes the
numbers can be served for up to an hour) and its rows add up to its meta; others
are listed with a reason, never summed or dropped silently. A budget in a
currency other than its tenant's is shown, not totalled. Lines without a cost are
counted. Only company-wide budgets are valued (the count of other-scope budgets is
shown). A granted subsidiary that is suspended is named under `unavailable`.

**Whole-tenant erasure (`tenants/data_export.py`).** The link tables name a tenant
in `parent_tenant_id` / `child_tenant_id`, not `tenant_id`, so `_DELETE_ORDER`
cannot see them; `delete_tenant` calls `organizations.service.end_links_for_erasure`
first, inside the same transaction: for each LIVE link the surviving side gets an
`org.link_revoked` warning (reason `org_tenant_erased`; the subsidiary's row never
carries the holding's label), then every link and grant naming the tenant is
deleted (FKs also cascade). A guard test fails if another table gets such a
column. The export carries the holding's links and grants (never the code hash)
and gives a subsidiary only `{id, status, dates, side}` of its link.
Deactivating or suspending a person (`users.update_status`, the SCIM path) drops
their grants, so a reactivation cannot silently restore access; the Rust reads
also refuse a non-active person.

**Audit.** Events `org.link_created|accepted|revoked`, `org.grant_added|removed`
(kind `account`, in `events.py` and mirrored in `activity.rs` / `alerts.rs`),
trail entries as LEGACY rows with target type `organization` (the catalogue's
sizes are now 78 legacy, 29 types, 113 actions, 117 stored). Reads are not audited.

**Results (local, own throwaway Postgres 18, Rust debug build, Python dev API).**
`cargo test`: 141 passed, 2 ignored (21 new). `pytest
backend/tests/test_org_hierarchy_python_side.py`: 26 passed; related suites
(audit trail, system events, tenant data, public surface, MCP, SCIM, webhooks,
alerts, notifications): all green. Contract `--only org` (Rust-only, adversarial,
real HTTP + database assertions): **241/241**. Mutation check: replacing the
per-person grant condition with "any grant of the tenant" first passed all but 3
cases (a gap); after adding "an ungranted person gets nothing while a colleague
holds a grant" it fails 12, and the restored build passes 241/241.
Not verified: a browser walk of `/organizacion`, the rate limiting of code
guesses (codes are 192 bits), a production-sized snapshot.

**Decision rules for the owner to confirm.** (1) A grant is a company-wide read of
the subsidiary (a warehouse-scoped person cannot use the views at all). (2) The
subsidiary learns the holding's name at redemption and in its link list. (3)
Consolidated reads are not audited. (4) Stock counts only fresh snapshots, so a
subsidiary nobody opened today is listed "out of date". (5) At most 20 waiting
codes per holding and 200 subsidiaries per person.

## 10. New Rust-only routes: cost centers and chained approvals

Owner-approved feature (2026-10-06): cost-center budgets with chained
approvals, built on the purchase budgets (`a760918`) and the PO approval
workflow (`fecbc5a`). Written the way the strangler-fig plan asks for NEW
work: the routes and the evaluation core are Rust, the schema stays Python's,
and Python enforces the same rules on the paths it still serves.

**Routes (Rust only, no Python failover).** `backend-rs/src/routes/cost_centers.rs`:

| Route | Who | What |
|---|---|---|
| `GET /cost-centers` | any signed-in user | the tree, flat, with path, depth and whether it has an active chain |
| `POST /cost-centers` | admin (not a warehouse-scoped one) | code (unique ignoring case), name, optional active parent |
| `PATCH /cost-centers/{id}` | admin | code, name, parent (no cycles, depth <= 32), active. A no-op writes no event |
| `GET /cost-centers/spend` | any user without a warehouse scope | ordered value per center and period (own, rolled up to ancestors, lines with no cost counted apart, orders with no center apart) and the cost-center budgets running |
| `GET /approval-chains` | any signed-in user | chains, bands, levels with the names of the people, and who can be named |
| `POST /approval-chains` | admin | name, optional center (none = the DEFAULT chain), bands |
| `PATCH /approval-chains/{id}` | admin | name, active, bands (replaced as a set). The center cannot change |
| `POST /approval-chains/evaluate` | any signed-in user | what the chain says about (center, amount, escalate), the core's answer verbatim |
| `PUT /inventory/po/{id}/cost-center` | analyst + the order's warehouse guard | attribute an order to a center, or clear it |

Gateway file: `deploy/rust-api/routes.d/43-cost-centers-chains.caddy.example`.
**There is no Python failover**: with Rust down these paths answer 502, with the
file in `routes.d/off/` they answer Python's 404 and the cards on `/aprobaciones`
and the cart's picker show an error or disappear. Centers and chains are never
deleted (orders, budgets and approvals point at them); they are paused.
Writes are company-wide settings (a warehouse-scoped admin is refused), the tag
is INTERNAL (no API key reaches them), and every write is one transaction under
a per-tenant advisory lock (a cycle cannot be built by two concurrent
reparentings; a unique partial index allows one ACTIVE chain per center).

**Model.**
* A chain belongs to one center (and covers its descendants that have no chain
  of their own, nearest ancestor first) or to none: the DEFAULT chain, which
  covers every other order, orders with no center included.
* A chain has BANDS: from `min_amount` up, the order needs the band's LEVELS,
  in order. A level is a role (`analyst` accepts analysts and admins, `admin`
  only admins) or 1 to 20 named people (active admins or analysts). At most 10
  bands and 5 levels. Below the lowest band nothing is required. The `+0.005`
  tolerance is the one the rules already use.
* Spend is attributed through `inventory_po_log.cost_center_id`, chosen when the
  order is created (`POST /inventory/log-po` accepts `cost_center_id`) or set
  afterwards by the Rust `PUT`. A budget with `scope_type = cost_center` (the
  existing budget ledger, one new scope) covers its center and every center
  below it; the existing hard cap, administrator override and audit rows apply
  unchanged. The cart's budget preview takes the center too.
* An order that went past ANY budget when it was created is stamped
  `chain_escalate` and then needs the chain's TOP band whatever its value.

**Fail closed (the rule this feature must never break).** Once a tenant has an
active chain, an order that cannot be resolved is not auto-approved and cannot
leave by any send path: no center and no default chain (`no_cost_center`), a
center that is unknown, paused or in a cyclic tree (`cost_center_invalid`), a
center no chain covers and no default (`no_chain`), a chain with ANY malformed
band or level (`chain_invalid`), or an order whose value is unknown
(`amount_unknown`). The answer is `409 po_approval_chain_unresolved` with the
reason, and the order row says `chain_unresolved`. With no active chain the
whole feature is invisible: no new key in any response and no change in any
answer.

**What Python does** (`backend/inventory/po_chain_service.py`, with a one-line
hook each in `po_approval_service.py`: `requirement`, `assert_sendable`,
`describe`, `request_approval`, `decide`, `annotate_orders`; plus the inbox and
the decision event in `api/v1/po_approvals.py`):
* `requirement()` lays the chain over the rule-based answer.
* `request_approval` snapshots the chain (levels and a fingerprint) on the
  request and opens one `po_approval_steps` row per level. It refuses, before
  writing anything, a chain nobody else can staff: a different eligible person
  per level, never the requester, matched (not greedy).
* `decide` goes level by level. The requester never approves a level, nobody
  approves two levels of one request (a repeated click is a no-op, never a
  second signature), a level is decided by someone who fits it, a rejection at
  any level rejects the order (with a reason), and only the LAST level makes it
  approved (the `purchase_order.approved` webhook and the requester's mail go
  then). An earlier level records `purchase.approval_level_approved`, never
  "approved".
* The request keeps the levels it was asked under. If the order, its center or
  the chain changes so the fingerprint differs, the open request cannot be
  decided (`po_approval_chain_changed`) and asking again replaces it (the old
  row stays, `rejected` with `superseded:chain_changed`). An approval made under
  another fingerprint no longer counts, and an order approved the old way is
  asked again when a chain arrives.
* Schema (additive, `cost_center_migrations.py`): `cost_centers`,
  `approval_chains`, `approval_chain_bands`, `po_approval_steps`, four columns
  on `po_approvals` (the chain snapshot) and two on `inventory_po_log`, and the
  budget scope check now allows `cost_center`. All are in the tenant export and
  the erase order.

**Core in two languages.** `backend/inventory/cost_center_chain_core.py` and
`backend-rs/src/chain.rs` are one rule written twice (Python must apply it on
the decision path; Rust serves the preview and the management). They are held
together by (1) `tests/contract/chain_fixtures.json`, 900 seeded cases answered
by the Python core and replayed by `cargo test fixtures_agree_with_python`
(regenerate with `tests/contract/gen_chain_fixtures.py` whenever a rule changes
on either side), and (2) `tests/contract/chain_differential.py`, which writes
seeded configurations into a throwaway tenant's tables and compares Rust's
`POST /approval-chains/evaluate` with Python's loaders plus core on the same
database, so it covers the two row loaders as well.

**Composition with delegation (`feat/approval-delegation`, read, not edited).**
That branch lets an approver lend their authority for a date range and checks
it in `decide` through `po_delegation_service.authority_for`, which today asks
`is_approver(delegator)`. For a chained request the single predicate is
`cost_center_chain_core.level_eligible(level, person)`. Intended composition,
to implement when both are merged:
1. a substitute may decide the OPEN level iff the delegator would be eligible
   for it (role or named) and the delegation's own limits hold (dates, the
   delegator's warehouse scope, the delegator is not the requester);
2. a delegation never skips a level and never lets one person sign two levels:
   compare BOTH the substitute and the delegator with the earlier deciders;
3. the step records `decided_by` = the substitute and the delegation in the
   same columns that branch adds to `po_approvals` (`decided_on_behalf_of`,
   `delegation_id`): add them to `po_approval_steps` in that merge, and
   `decorate` / `merge_inbox` call the same predicate with the delegator row, so
   the substitute's inbox and `can_decide` agree with `decide`;
4. a delegation is never transitive: lending a level does not lend the
   delegator's own delegations.

Textual conflicts to expect: `po_approval_service.py` (`describe`, `decide`,
`list_pending`, which both branches touch), `po_approvals.py`,
`activity/events.py`, `audit/catalog.py`, `routes/mod.rs`, `activity.rs`,
`r1/alerts.rs`, the audit size asserts (together: LEGACY 81, target types 31,
audit actions 116, stored actions 120) and `contract_test.py` (each adds one
`results +=` line).

**Decision rules for the owner to confirm.**
1. Strictness: once any chain is active, orders with no center need a DEFAULT
   chain or they cannot be sent. The screen warns when none exists.
2. Editing a chain voids the approvals of orders still waiting to be sent (they
   are asked again) and freezes open requests until they are re-requested.
3. Cost-center budgets are checked when the order is PLACED, so an order cannot
   be re-attributed into or out of a center that has a budget (the Rust `PUT`
   refuses it); the cart's picker is the way in. Recommendations belong to no
   center, so the funding plan has no lines for a cost-center budget (a warning
   says so).
4. Over-budget escalation goes to the top band for ANY budget exceeded (soft
   ones too), and is permanent for that order.
5. A role level accepts that role or above; named people must be active admins
   or analysts when the chain is saved. A named person deactivated later leaves
   the level unfillable until the chain is edited.
6. The requester may reject their own order only if they fit the open level; they
   can never approve a level.

**Results (2026-10-06, disposable database, Python and Rust on private ports).**
`cargo test`: 138 passed, 2 ignored (including `fixtures_agree_with_python`,
900 cases). New pytest: 21 pure-core and 25 database tests
(`test_cost_center_chain_core.py`, `test_cost_center_chains.py`); with the
approval, budget, audit, event, error-code, public-surface and tenant-export
suites that the change touches, 413 passed. `run_cost_centers` in
`tests/contract/contract_test.py`: 59/59 (the rest of that run: 79/79).
`chain_differential.py`: 12,012 resolutions over 80 seeded configurations and
5,314 over 40 more, 0 mismatches. A mutation of two expectations in the harness
turned both red. The cost-center and chain cards were exercised in a browser at
phone width (create a center, a child, a default chain; the list renders with
paths, the chain text and the es copy); the cart picker, the budget scope option
and the "assign a cost center" control on an unresolved order were type-checked
and compiled but not clicked.

## 11. Money at risk on the outlook (additive fields, same Rust routes)

Owner-approved (2026-10-06). For each commitment the outlook calls `will_miss`
or `at_risk`: the money behind the shortfall, plus the margin when the unit cost
is known, with a per-customer and a per-contract roll-up and a tenant total.
**No new routes and no Python twin**: the figures are added to the three outlook
routes of section 10 (`money` on every item and on every `summary`, `currency`
at the top), so the same gateway file (`42-commitment-outlook.caddy.example`)
serves it and the same kill switch applies. No schema change, no new events
(nothing is written), so there is nothing for Python to honor: Python does not
serve these routes.

### What is computed

* Per row: `amount_at_risk = shortfall units x unit selling price`,
  `margin_at_risk = shortfall units x (price - unit cost)`. Amounts are exact
  decimal STRINGS (`"1000.00"`), never floats, in the tenant base currency
  (`currency` = `GET /tenant/currency`'s object).
* The unit price is the commitment's own price when it has one, else the SKU's:
  1. a commitment materialised from a contract takes the `unit_price` of its
     SKU's line on THAT contract revision (`committed_demand.contract_id`);
  2. a contract line with no price (or a manual commitment, which has no price
     column) falls back to `inventory_stock.sale_price` of the representative
     warehouse row (the same row the lead time reads: the tenant default
     warehouse first, then alphabetical) among the caller's warehouses;
  3. a contract price that exists but is unusable, or a SKU named twice on a
     contract with different prices, is NOT replaced by the SKU price: the row is
     "no price". `price_source` says `contract` or `sku`.
* The unit cost is `inventory_stock.unit_cost` of the same row.
* Totals are the exact sum of the rows' rounded cents (so the rows on screen add
  up to the total on screen). `eligible` counts at-risk and will-miss rows,
  `computed` those with an amount, `excluded_no_price` and
  `excluded_no_shortfall` the ones left out and why, `margin_rows` /
  `margin_excluded` the same for margin. A set with no at-risk rows totals a true
  `"0.00"` with `eligible: 0`.
* `is_minimum` / `has_minimum`: the shortfall is a lower bound (undated purchase
  order units were counted as arriving), so the amount is "at least".

### Arithmetic rules (defined rounding)

Integers only: units in hundredths, prices in ten-thousandths, amounts in cents.

* Units = the shortfall rounded to 2 decimals, half to even on the float's exact
  binary value (the same as `Decimal(x).quantize(..., ROUND_HALF_EVEN)`).
* Price and cost round to 4 decimals the same way. Missing, not finite, not
  above zero, rounding to zero, or above 1e9 is NOT AVAILABLE; units above 1e12
  are refused likewise.
* Amount and margin to cents HALF UP (ties away from zero). Margin is signed: a
  cost above the price shows a negative margin rather than hiding it.
* Only `at_risk` / `will_miss` rows are eligible. `insufficient_data` rows that
  carry a shortfall are NOT counted (their verdict is unknown), `on_track` rows
  have none. An `at_risk` row covered only by a tight purchase order has a
  shortfall of 0 and therefore `"0.00"` at risk: the money measures units short,
  not exposure of the whole order.

### Where the code is, and how it is tested

* `backend-rs/src/fulfillment/money.rs`: the arithmetic only, with unit tests.
  `tests/contract/money_reference.py` is the same rules in plain Python on
  `decimal`; `tests/contract/gen_money_fixtures.py` writes 6,000 seeded row cases
  (rounding edges, NaN, infinities, zero and absurd values) and 1,200 roll-ups
  to `backend-rs/tests/fixtures/money_cases.json`, and the Rust test
  `differential_against_python` demands exact equality. Python
  `test_money_at_risk_reference_pure.py` fails when the fixture is stale.
  Mutating the half-up rule turned the differential red (checked by hand).
* `fulfillment/data.rs`: adds `sale_price` / `unit_cost` to the stock read and
  the contract line price (`contract_line_price`, unit tested).
* `tests/contract/cf_money_cases.py`, hooked into `contract_test.py` with two
  lines: hand-computed scenarios in the database, the exclusion counts, roll-ups,
  viewer/analyst/anonymous reads, "the reads wrote nothing" and warehouse scope.
* Frontend: money line per row, totals tile, exclusion notices and the
  by-customer / by-contract lists in `CommitmentOutlookPanel` (`outlook.*`, es/en).

### Limitations and decision rules to confirm

1. **Single currency.** This base has no currency column on commitments, contract
   lines or SKU prices, so every amount is read as being in the tenant base
   currency (`tenants.settings.currency`), exactly like every other money figure
   in the product (changing it relabels, never converts). When multi-currency
   lands, a row priced in another currency must be excluded or converted there;
   until then, a tenant that mixes currencies in its price data would see wrong
   totals. Not detectable from this data.
2. A manual commitment has no price of its own (no column). Its price is the SKU
   price. Adding a per-commitment price is a Python schema and write-path change,
   deliberately not done here.
3. A zero price or zero cost is treated as not available, never as a real zero
   (a free sample would show as "no price"). Owner to confirm.
4. With several warehouses the SKU price is the representative warehouse's, not
   an average.
5. CRC and other 0-decimal currencies: amounts keep cents in the API; the
   frontend rounds for display only.

## 10. New feature written in Rust: stock allocation among committed customers

Owner-approved 2026-10-06. When the stock on hand plus the arrivals that can
be counted cannot cover every open commitment of a SKU, `/api/v1/allocation/*`
says who gets what and who is short by how much. **These routes are new: Python
has no twin, so there is NO failover** (`deploy/rust-api/routes.d/
50-stock-allocation.caddy.example` names `api-rs` as the only upstream; with the
Rust container down the answer is an honest 502, not a Python 404).

| Route | Role | What |
|---|---|---|
| `GET /allocation/priorities` | any signed-in | customer tiers, fair-share tiers, customers on open commitments with no tier yet |
| `PUT /allocation/priorities` | analyst+ | set or clear tiers (`tier: null`), set the fair-share tiers |
| `POST /allocation/preview` | any signed-in | one SKU's allocation; what-if tier overrides, fair-share tiers and hypothetical arrivals, nothing saved |
| `POST /allocation/apply` | analyst+ | records reservations; needs the preview's `result_hash` |
| `POST /allocation/release` | analyst+ | releases a SKU's active reservations |
| `GET /allocation/reservations` | any signed-in | active reservations, flagged stale when the commitment moved |
| `GET /allocation/overview` | any signed-in | every SKU where someone is short |

**Rules (the owner should confirm the starred ones).**
* Order of service: tier 1 first ... tier 9 last; `*` a customer with no tier is
  served at tier 5 (`DEFAULT_TIER`). Inside a tier: earliest delivery date first
  (ties by commitment id), or, for a tenant-chosen fair-share tier,
  proportionally to the units each member asks for.
* A claim is served only from supply that exists by its delivery date (stock +
  counted arrivals dated on or before it; an overdue claim counts as due today).
  Supply arriving later never serves it: that claim stays short and the later
  supply goes to the claims it can reach. `*` This is deliberately strict.
* Units are `quantity x probability`, exactly what the semaforo uses
  (`committed_demand_service.committed_units`). `on_top_of_base` is ignored here
  too: a customer already inside the baseline still wants the units.
* `*` Fair-share floors to a micro-unit (1e-6): the few micro-units a floor
  drops are never handed to someone else.
* Inputs are the SAME as the commitment fulfillment outlook
  (`feat/commitment-fulfillment`, `fulfillment/data.rs`): open commitments
  company-wide, stock summed over warehouses (a SKU with no stock row has no
  verdict, `stock_unknown`, never "all short"), open PO lines dated by an
  accepted supplier promise else generated + lead time (learned from >= 3
  receptions, else the supplier card; never the 15-day default), transfers in
  transit available today. A PO line with no usable date is listed under
  `incoming_not_counted`, not counted. `allocation/supply.rs` duplicates those
  reads until the two branches merge; then it should call the fulfillment
  loader (both return `(day, qty)`), and `allocation::core` does not change.
* `*` A caller limited to some warehouses is refused (`warehouse_scope_company_totals`):
  stock is a company-wide figure.
* Not exposed to API keys or MCP (internal route: the decision is recorded under
  a person's name). No plan gate: available on every tier.

**It is advisory, and a test says so.** Nothing writes `inventory_stock`; the four
tables (`allocation_customer_priorities`, `allocation_tier_policy`,
`allocation_runs`, `stock_reservations`, additive migration in
`backend/inventory/stock_allocation_migrations.py`) are NOT status inputs and no
Python module reads them, so the purchase recommendation (semaforo) is untouched:
one authority for how committed demand moves it.
`backend/tests/test_stock_allocation_schema.py` pins this (not in
`STATUS_INPUT_TABLES`, no status triggers, no Python reader, the Rust sources
write only those four tables). A reservation stores the commitment's quantity,
probability and date at apply time, so an edit or closing shows as stale at read
time instead of silently standing for a different order. Apply is serialised per
SKU by an advisory lock, refuses a preview whose inputs changed
(`allocation_stale`), and a re-apply releases the old rows (history kept; a
partial unique index allows one active reservation per commitment).

**Events:** `allocation.priorities_changed`, `allocation.applied`,
`allocation.released` (activity feed and audit trail, es/en copy), mirrored in
`activity.rs`, `r1/alerts.rs` and `audit/catalog.rs` (size asserts updated).

**Tests.**
* Rust: 142 unit tests pass (2 ignored), including the differential test
  `allocation::core::tests::matches_the_python_reference_exactly`: 2,600 seeded
  cases solved by `tests/contract/allocation_reference.py` (written
  independently with `fractions.Fraction`, re-evaluating every constraint from
  scratch), exact integer equality. `gen_allocation_fixtures.py --check` and
  `backend/tests/test_allocation_reference.py` fail on a stale fixture and check
  the reference's invariants on cases outside the fixture.
* Contract (`tests/contract/allocation_contract.py`, `--only allocation`, Rust
  only, asserts the database): 40 cases pass, covering a permission pair on every
  write, validation shapes, the advisory guarantee (stock row unchanged after
  preview, apply and release), stale hash, hypothetical-supply refusal, stale
  reservation on edit and on close, scoped caller, API key, other tenant.
* Python: `test_stock_allocation_schema.py` (constraints, one active reservation,
  cascade, whole-tenant erasure, advisory-only guards, events) and
  `test_allocation_reference.py`.
* Browser: the screen (committed-demand view of `/inventario`, below the
  commitments) was driven through a throwaway gateway that sends
  `/api/v1/allocation*` to Rust and everything else to Python: overview, preview,
  apply and the re-read of the reservations, stock unchanged. Phone width has no
  horizontal page scroll.
## 10. Recurring delivery schedules (a new feature written in Rust first)

Owner request, 2026-10-06: corporate contract lines of the form "N units every
month / fortnight / week from date A to date B", materialised ahead as
committed demand. Unlike everything above this is NOT a port: **there is no
Python implementation of these routes, so there is no failover.** The gateway
file `routes.d/50-recurring-deliveries.caddy.example` therefore lists only
`api-rs` as upstream. With the Rust service down the screen shows its load
error and no new commitments are generated; the rows already materialised keep
counting, because they are ordinary `committed_demand` rows read by Python.

What lives where:

* **Schema (Python, additive):** `recurring_delivery_schedules`
  (`backend/inventory/recurring_delivery_migrations.py`). Rows made by a
  schedule are `committed_demand` rows with `source = 'contract'`,
  `contract_id = contract_root_id = <schedule id>` and
  `contract_release_date = <nominal date>`, so they reuse the blanket-contract
  lock (sku, warehouse, customer, probability and the on-top flag cannot be
  edited on the row) and the partial unique index that makes materialisation
  idempotent.
* **Routes (Rust, `routes/recurring_deliveries.rs`):** `GET` list and one,
  `POST` create, `POST /preview`, `PATCH /{id}`, `POST /{id}/status`. No
  `DELETE`: cancel instead. Internal tag (no API key). Warehouse scope follows
  committed demand. Events `recurring_delivery.created / revised /
  status_changed` in both the Python and Rust event and audit catalogues.
* **Core (Rust, `recurring/`):** `dates.rs` (pure date rules), `materialise.rs`
  (rows), `materialiser.rs` (the loop). The date rules have an independent
  Python statement, `backend/inventory/recurring_delivery_dates.py`, and a
  differential test: `tests/contract/gen_recurring_fixtures.py` writes 4,000
  cases and `cargo test -- --ignored recurring_dates_match_python` replays them.
* **Python honours it where it still serves the path:** reopening a commitment
  a schedule made follows the schedule's status (`set_status` in
  `committed_demand_service.py`, mirrored in Rust); whole-tenant export and
  erasure list the table; `loop_state.LOOPS` and `/health` report the loop.

Rules (decisions the owner should confirm are marked *):

* Frequencies: `weekly`, `fortnightly` (every 14 days from the first chosen
  weekday on or after the start), `semimonthly` (the 15th and the last day),
  `monthly` (a day of the month, 31 = last day). *"Fortnight" is read both ways
  on purpose, as two options.
* Holidays: the existing calendar catalog holds commercial events, not public
  holidays, so each schedule carries its own list of customer holidays
  (at most 50) plus an optional "avoid weekends" for monthly/semimonthly. A
  delivery on such a day is skipped, or moved to the previous / next working
  day. The nominal date is the identity of a delivery; the moved date is where
  the commitment sits. *A moved date may fall after `end_date`.
* Horizon: the schedule's own `horizon_days` (default 180), raised to the
  SKU's longest lead time plus 60 days (same rule as blanket contracts), capped
  at 730. A delivery inside the protection interval that was not materialised
  would be under-bought silently, so `progress.missing` counts them and the
  card shows it.
* Nothing in the past is created: a schedule starting last year does not invent
  a year of overdue orders. *
* Editing a schedule rewrites or withdraws only rows that are `open` and dated
  today or later; fulfilled, cancelled, withdrawn and past rows are history and
  keep their slot. *A hand edit of quantity or date on a future open row is
  overwritten by a schedule edit (not by the periodic pass).* The product (sku)
  of a schedule cannot change.
* Pausing or cancelling withdraws future open rows (cancelled, stamped
  `contract_withdrawn_at`); resuming creates them again. Cancelled is final.
* The loop runs in the Rust process: every 6 h (and at start when the last
  pass is older), writes `system_loop_runs` row `recurring_deliveries`
  (`completed`, or `failed` with how many schedules failed; the failure is also
  stored on the schedule). Env: `RECURRING_MATERIALISER_ENABLED=false` turns it
  off, `RECURRING_MATERIALISER_INTERVAL_SECS` sets the period. These are
  Rust-only knobs and are not in the Python settings registry. A schedule
  created or edited through the API materialises immediately, in the request.

Verification (2026-10-06, disposable database, Python and Rust dev builds):
Rust unit tests 140 pass, 3 ignored (one is the differential) (4,000 fixtures against the
Python reference, all equal); `backend/tests/test_recurring_deliveries.py` 27
pass; `contract_test.py --only-rd` 36/36 pass (permission pairs, rows checked in
the database against the reference, history untouched by edits, pause/resume,
cancel finality, scope, the loop picking up a schedule written straight to the
table, `/health` on both services). Not verified in a browser (the browser
extension was not connected): the panel type-checks and its page compiles, and
the same calls were made through the Next proxy with curl.
### Contract renewals (new Rust-only routes, 2026-10-06)

Owner request: finish more corporate features, writing each new one in Rust.
This one tracks the end of a blanket supply contract: expiry and notice dates,
a renewals list, committed-vs-delivered over the term, alerts at 60/30/7 days,
and a Renew action.

**Who owns what.**

| Piece | Where | Why |
|---|---|---|
| Schema (`notice_days`, `auto_renew`, `renewal_lead_days`, `renewed_from_root_id` on `supply_contracts`, additive) | Python, `supply_contract_migrations.py` | Python owns the schema |
| Create / revise / status with the new fields; a revision that omits them keeps them | Python, `supply_contract_service.py` | still the writer of those paths |
| Renewals list, comparison, renew | **Rust**, `routes/contract_renewals.rs` + `contract_renewal.rs` | new routes, new logic |
| Daily alert pass | Python, `inventory/contract_renewal_alerts.py`, called from the 08:00 UTC loop after the contract materialisation | the loop and `record_event` are Python |
| The maths, as a reference | Python `inventory/contract_renewal.py`, replayed by Rust | differential test |

**No Python failover.** The three routes exist only in Rust. If they were
served by Python, `GET /supply-contracts/renewals` would match
`GET /supply-contracts/{root_id}` and answer "contract not found", so the
proxy example (`deploy/rust-api/routes.d/45-contract-renewals.caddy.example`)
names `api-rs` only, and the renewals panel turns that exact 404 into "not
available on this server yet". Without the routes file the product behaves as
before, plus the new optional fields on the contract form.

**Alerts through the existing registry.** `supply_contract.renewal_due`
(warning, kind `purchase`) reaches the bell and the history through the same
`EVENTS` registry; the Rust copy (`routes/r1/alerts.rs`) carries it, and the
unit test that re-reads `events.py` keeps the two lists equal. The reason codes
are `contract_expiring`, `contract_notice_deadline` and `contract_expired`.
`supply_contract.renewed` (info) is what the Renew action records, mirrored in
`activity.rs` and in the audit catalogue (`LEGACY`, 73 -> 74).

**Decision rules the owner should confirm.**

1. **A renewal is a new contract (a new draft lineage, revision 1), not a new
   revision of the old one.** The fulfilment of a lineage is every fulfilled
   commitment of that lineage, so moving the same lineage into the next term
   would show last term's deliveries as this term's. The new contract points at
   the old one (`renewed_from_root_id`); the old one is not touched. It is a
   DRAFT: nothing is materialised, so no purchase decision moves until a person
   activates it through the existing status route. Unit prices are carried and
   the UI says so.
2. The renewed term has the same length (whole calendar months when the old
   term was whole months, so 2027-01-01..12-31 renews to 2028-01-01..12-31
   whatever the leap year does; else the same day count). An explicit schedule
   is shifted by the same amount; a release that would fall outside the new term
   refuses the renewal (422) instead of being clamped.
3. Renew is refused when the new term has already ended (422): activating it
   would make every release an overdue commitment at once.
4. "Late" means fulfilled after the **contract's** release date, using the UTC
   date of the commitment's `status_changed_at`; a person may move
   `delivery_date`, which must not hide lateness. A fulfilled row with no
   fulfilment time is counted in `fulfilled_undated`, never as on time or late.
5. The fill rate is delivered / due to date and is `null` (not 100%) when
   nothing is due. It is not capped: an over-delivery reads above 100.
6. With a notice period the alert counts down to the notice deadline, not to
   the end date. Of the lead times already crossed only the nearest is raised
   (a contract entered 5 days before the end is one alert), and an active
   contract past its end date is raised once as expired. The event row itself
   is the "already sent" record, so a catch-up run or a second worker never
   repeats one, and extending the term restarts the countdown.
7. `auto_renew` is a label for the screens and the alert wording. Nothing
   creates a contract on its own.
8. `renewal_lead_days` is NULL until somebody sets it (the screen says "defaults
   60/30/7"); a default must not look like a choice.
9. "Today" in the new code is the UTC date. Python's existing contract progress
   uses the server's local date, which is the same on a UTC server.
10. The alert carries the customer's name to the tenant-wide bell, like
    `committed_demand.created` does in the activity history. A scoped user
    therefore sees the alert of a contract in a warehouse they cannot open.
    The renewals list and comparison do apply the warehouse scope.

**Tests.**

* `cargo test`: 137 pass, 2 ignored (the integrated branch had 120; this adds
  the maths, the scope and body-validation tests, the event registry rows and
  the 530-case differential replay).
* Differential: `tests/contract/renewal_diff_cases.json` (530 cases: 90
  `expand_releases`, 90 `renewal_view`, 120 `due_alert`, 90 `renewal_terms`,
  140 `commitment_comparison`) is generated from the Python reference by
  `backend/tests/test_contract_renewal_pure.py`, which fails when the file is
  stale; the Rust test `contract_renewal::tests::matches_the_python_reference`
  replays it, numbers compared within 1e-9. It passed on the first replay.
* pytest: `test_contract_renewal_pure.py` (24) and `test_contract_renewal.py`
  (20, against Postgres: fields persisted and carried through revisions and
  status changes, the 60/30/7 countdown exactly once each, late entry, notice
  deadline, custom lead times, term extension, draft/closed/renewed contracts
  not alerted, a cancelled renewal not silencing the alert, one failing
  contract not stopping the pass, the loop wiring). The existing supply-contract
  and event suites still pass (170 in the neighbouring files).
* Contract harness, `run_cr()` in `tests/contract/contract_test.py`, 53 cases
  that have no Python side to diff, so they check the database against
  expectations the harness computes itself, and Python where it has an opinion:
  auth failures, key refusal, viewer allowed to read and denied to renew, body
  and query validation, 404s, list buckets and order, comparison against rows
  seeded with SQL fulfilment times (late, short, undated) and against Python's
  own progress, renew (the stored row, the untouched old rows, the event row,
  stale revision, draft, elapsed term, twice, explicit schedule, two
  simultaneous renews give one 201 and one 409), Python reading and activating
  what Rust created and a cancelled renewal freeing the contract, warehouse
  scope, and `/alerts`, `/alerts/activity`, `/alerts/kinds` byte-identical
  between Python and Rust with a `supply_contract.renewal_due` row present.
  Full harness, Python dev API on a disposable Postgres (UTC), Rust debug
  build: **530/530 pass**.

**Not verified.** The browser walk of the new panel (the type check passes; no
screen was driven), the daily loop itself at 08:00 UTC (the pass is called
directly in the tests and the loop wiring is checked from the source), the
Docker image and the Caddy file (Docker is off on this machine), and the
plan-gate-free assumption that all roles may read the renewals.
