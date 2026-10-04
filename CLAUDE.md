# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. All the code needs to be in english

## Priority: stability over scope (read this first)

The product has a lot of surface already built. **The work is making what exists
behave impeccably, not widening it.** In order:

1. Stability and correctness of what is already there.
2. Only the features the owner names explicitly as important.
3. Nothing else.

Concretely:

- **Fixing a bug is not the same as adding an option.** When a fix needs a new
  capability — an endpoint, a field, a screen, a toggle — say so and ask before
  building it.
- **Improvements found while testing go on a list, not into the code.** Report
  them; let the owner choose. A one-off "implement those" is permission for
  those, and does not lift this rule for the next idea. **That list is
  `docs/stability.md`** — the single live backlog and the order the work runs
  in. `docs/screen-inventory.md` is the table it draws from.
- Verify in a browser as a user, not only with tests. See the `running-stockai`
  skill for the environment traps and `silent-failures` for the review lens.
- When robustness and scope conflict, robustness wins.
- Out of scope on the owner's instruction: CI/CD.
- **Two usage tiers, no billing, no feature gates.** Both tiers ship every
  feature; `tenants.tier` only decides *how much* fits (`backend/entitlements/
  plans.py`). `free` is a permanent home with short ceilings — 100 SKUs, 2
  users, 1 warehouse, 3 saved forecasts, 1 API key, 25 MB per upload. `paid`
  lifts all of them. There is still **no Stripe and no checkout**: a tenant
  becomes `paid` because somebody talked to the owner and the column was set,
  and the app's only commercial surface is a "write to us" dialog
  (`Frontend/src/components/limits/`). Set 2026-08-22, replacing the
  one-plan-everything-free rule of 2026-08-16.
  **Never reintroduce**: a feature gate, a `require_feature`, a plan comparison
  screen, or a payment flow. A limit is a number, never a locked door.
  **Trial accounts** (2026-10-01, owner's request): the landing's "Probar sin
  registrarme" → `/prueba` → `POST /trial` (`backend/trial/`) mints a throwaway
  tenant on a third ceiling set, `demo` (30 SKUs, 1 user, 1 job at a time,
  5 MB), with a made-up `@stockai.demo` login that is never verified and that
  the email transport refuses — so it can reach nobody. It lives 24 h; the
  `trial-reaper` worker loop erases it, unless it filed an upgrade request
  still `new` (a lead is never lost to a cron job).

## Project Overview

**StockAI** — inventory purchasing decisions platform for LatAm SMB distributors.
Pipeline: sales history (CSV/Excel) → per-SKU forecasting (LightGBM, XGBoost, Prophet, ARIMA, ETS, Croston, LSTM) → stock semáforo (PEDIR_YA / PEDIR_PRONTO / OK / SOBRESTOCK) → purchase-order generation, reception tracking and supplier lead-time learning.

## Repository Structure

Three layers with a strict separation:

```
ForecastingCore/forecasting_core/   ML engine — ALL forecasting intelligence lives here
backend/                            FastAPI multi-tenant SaaS API — pure orchestration, no pandas/ML
Frontend/                           Next.js 14 (pages under src/app/, API client in src/lib/api.ts)
```

Do not put ML logic in `backend/` or business logic in `Frontend/`. pandas/numpy live only in `backend/dataframes/`, `backend/utils/temporal_agg.py`, and `backend/workers/runner.py`; no other backend module imports them (enforced by `backend/tests/test_no_pandas_in_backend.py`). Every tabular read/analysis in the API/service layer routes through the `backend/dataframes/` boundary, which returns plain Python (a DataFrame only ever crosses out via the explicit ForecastingCore-bridge helpers).

## Running

```bash
# Backend (port 8011 for local dev — 8010 is taken by another project's
# container on this machine; needs Postgres — see Database below)
backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8011

# Frontend (port 5000; proxies /api/* to BACKEND_URL — read Frontend/.env.local,
# it names the port and beats a shell variable)
cd Frontend && npm run dev

# Tests — one command, everything, in the right order
python scripts/run_tests.py        # backend, then engine, then typecheck
python scripts/run_tests.py --backend      # or one part at a time
```

`run_tests.py` exists because "the full suite" used to mean only
`backend/tests`, and the engine's 56 test files were a second command you had to
remember — which is how a change to `ForecastingCore` once shipped with just a
`-k` subset run against it. The script also **refuses to start while a dev
server is listening**: the job queue IS the `jobs` table, so a running backend
claims jobs the tests create and flips their sessions to FAILED, reporting
defects that are not there. And it never runs the two suites concurrently — the
timing-sensitive ones then fail for load.

The parts, if you need them individually:

```bash
cd backend && python -m pytest tests/ -q          # needs local Postgres on :5544
cd ForecastingCore && python -m pytest tests/ -q  # pure Python, no DB
cd Frontend && npx tsc --noEmit                   # typecheck — NOT tests
```

**The frontend has no tests.** `tsc` checks types, not behaviour, and there is
no end-to-end suite: every screen walk in `docs/screen-inventory.md` was done
by hand in a browser. A green run means the backend behaves — not that the
product works. On 2026-08-06 it was green while 27 real defects were live.

Local test Postgres: docker container **faro_db** (user/pass `postgres`/`postgres`, port 5544). An empty DB self-bootstraps: `backend/db/migrations.py run_all()` creates all tables at startup.

Do NOT run `npm run build` while `next dev` is running — it corrupts the dev server's `.next` cache.

## Key Architecture Facts

- **Config per session**: the training wizard stores 6 JSONB blobs in `session_configs` (`columns_cfg`, `features_cfg`, `models_cfg`, `validation_cfg`, `forecast_cfg`, `business_cfg`). `backend/workers/runner.py` assembles them into the engine config dict.
- **Canonical column schema**: uploads map user columns to canonical fields (sku/date/demand/…). `apply_canonical_defaults` adds alias columns — the Trainer drops any feature identical to the target (leakage guard) and the FeatureEngineer's dropna only applies to generated features.
- **Model routing narrows the user's selection** (`forecasting_core/training/router.py`): routing must never train a model the user didn't select.
- **Session state machine**: `backend/sessions/state_machine.py` (DRAFT → … → MODELS_CONFIGURED → QUEUED → RUNNING → COMPLETED/FAILED). Training jobs run on an in-process worker thread (`backend/workers/`), queued in the `jobs` table.
- **Auth**: own JWT (15-min access + refresh token), NOT Supabase Auth. Roles: admin / analyst / viewer — every mutating endpoint requires `require_analyst_or_above`; reads need `get_current_user`. Frontend renews expired tokens silently (`Frontend/src/lib/auth.ts tryRefresh`).
- **Notifications**: `backend/notifications/email.py` (Resend primary via RESEND_API_KEY, SMTP fallback) and `whatsapp.py` (Twilio). Daily inventory alert loop fires at 8:00 UTC from `backend/workers/worker.py`.
- **AI features** (narrative, RAG analyst, chat, data-quality diagnosis): all go through the single factory `get_local_llm_client()` in `backend/ai/local_llm.py`, and there is exactly **one** backend behind it: **DeepSeek** (`DEEPSEEK_API_KEY`, `settings.deepseek_model`, default `deepseek-chat`), spoken over plain httpx because the API is OpenAI-shaped and needs no SDK. Anthropic and the local Ollama fallback were removed 2026-08-23 — **do not reintroduce a second provider or a fallback chain**: "whichever key is set" meant a missing or mistyped `DEEPSEEK_API_KEY` silently answered from somewhere else, and the only symptom was a different bill. With no key the factory raises `LLMNotConfigured` at the call site; every consumer already degrades to its rule-based text on an exception. `conftest.py` patches the factory session-wide, so tests never bill a real key.
- **Storage**: Postgres for all metadata/results; binary files (datasets, artifacts, documents) on local disk under `storage/` (gitignored, never version it).
- **Sessions are permanent** (2026-10-04, owner's rule): nothing in the product
  erases a real tenant's session, its results/forecasts/artifacts or the dataset
  it trained on. `DELETE /sessions/{id}` ARCHIVES (`sessions.archived_at`;
  audited as `session.archive`, restorable with `POST /sessions/{id}/restore`);
  the scheduled-retrain prune archives too; a dataset any session reads (archived
  ones included) refuses deletion with `data_source_in_use`, and replacing a
  dataset's file moves the old one to `previous/` instead of unlinking it. The
  `max_sessions` ceiling counts ACTIVE sessions and only ever refuses to create or
  restore. The only code that removes session rows is whole-tenant erasure
  (`tenants/data_export.py`: typed confirmation, or the trial reaper on `demo`
  tenants); `test_sessions_permanent.py` greps the source for any other path.
  Back-test sessions (`is_backtest`) and archived ones never drive planning.
- **Public surface** (2026-10-02): an `sk_live_*` key can call every route
  whose router tag is in `EXPOSED_TAGS` of `backend/api/public_surface.py`
  (~211 operations), never auth/users/keys/config/admin routes; every tag must
  be in `EXPOSED_TAGS` or `INTERNAL_TAGS` or `test_public_api_surface.py`
  goes red. Keys carry a scope: `read` acts as viewer, `write` as analyst.
  Every key call is metered in `api_usage_daily`. The docs at /desarrolladores
  render `Frontend/src/data/public-api.json`, exported by
  `backend/scripts/export_public_api.py` (a test fails when it is stale).
  `PUBLIC_API_ONLY=true` prunes the app to the key-callable routes.
- **Social login** (Google/Apple/Facebook, `backend/auth/social/`): OFF by
  default — the source-code distribution shows only email+password. Enabled
  per installation by `SOCIAL_LOGIN_ENABLED` + each provider's credentials in
  /instalacion. Never make it on by default.
- **MCP**: `backend/mcp/` — a stateless Streamable-HTTP JSON-RPC server over
  **five read-only tools** (`catalog.py`), authenticated with the same
  `sk_live_*` key and the same rate limit. Hand-rolled rather than the `mcp`
  SDK, for the reason in `protocol.py`'s header. `mcp_server/stockai_mcp.py` is a
  stdlib-only stdio pipe to that endpoint for desktop clients — it holds no
  catalogue. **Never add a tool that writes**: an AI client cannot render a
  confirmation or hold an undo token (`docs/assistant-actions.md`), and
  `test_mcp_server.py::test_every_tool_actually_only_calls_GET_endpoints` is
  the wall: it resolves what each handler calls to its FastAPI route and
  demands `{GET}`.
- **Removed 2026-09-20**: the Alegra and Siigo accounting integrations, the
  `/integraciones` screen and `integration_connections`. Written, never run
  against a live account. Do not reintroduce an ERP connector without an account
  to verify it against first.

## Language (mandatory)

**All code is English. No exceptions.** Comments, docstrings, variable/function/class names, DB column names, API field names, test names, assertion messages and commit messages — English.

The only Spanish is **end-user copy**, and it lives in dedicated locale layers — **never hardcoded as a Spanish string literal in backend logic**. Frontend: the `es` values in `Frontend/src/i18n/translations.ts` (keys are English). Backend user-facing output — API error messages, explanation sentences, email/WhatsApp/PDF bodies, anything the user reads — must NOT be a Spanish string in code: the backend returns **English text or a structured error code + params**, and the **frontend renders the Spanish via i18n**. For backend-only channels the frontend never sees (email/WhatsApp/PDF), Spanish lives in a **backend locale catalog keyed by English identifiers** — the code references the key, not the Spanish. LLM prompts are written in English (they may instruct the model to *answer* in Spanish).

The dedicated sweep has run: identifiers, DB columns and API fields are English throughout. Four categories are deliberately still Spanish, and renaming them is **out of scope, not an oversight**:

- **App routes** — `/hoy`, `/pedidos`, `/skus`. Users may have shared these URLs.
- **Persisted signal values** — `PEDIR_YA`, `PEDIR_PRONTO`, `OK`, `SOBRESTOCK`, stored on stock rows and in PO history. Renaming needs a data migration, not a code change.
- **Spanish vocabulary that matches user data** — CSV header aliases (`fecha`, `ventas`, `categoria` in the canonical-column detectors), free-text payment terms (`contado`, `contra entrega`, `quincenal`), and calendar catalog keys (`co_quincena_15`). These are values the product reads from real user input.
- **Frontend end-user copy** — the `es` values in `translations.ts`. The landing is **bilingual** since 2026-08-23: its copy lives in `Frontend/src/i18n/landing.ts` as `LANDING: Record<Lang, LandingCopy>`, typed by one interface so a missing translation is a **compile error**, not a runtime fallback. Marketing prose stays out of `translations.ts` on purpose — that catalogue is short interface strings looked up by key; this is paragraphs owned by whoever owns the pitch. A new landing string goes in both languages or it does not build. (Backend user-facing copy is **no longer** allowed as hardcoded Spanish — it moves to English + frontend i18n, or a backend locale catalog for email/WhatsApp/PDF. See above.)

## Testing Standards (mandatory)

- Assert **state changes with direct DB queries**, not just status codes or response echoes.
- Every mutating endpoint needs a **permission pair**: viewer denied (403 + state unchanged) AND analyst success.
- Never write tests that can't fail (either/or asserts, xfail on real bugs).
- Tests that depend on quotas/rate limits must `monkeypatch settings.testing_mode = False` themselves — the local `.env` runs with `TESTING_MODE=true`.
- conftest patches `backend.notifications.email._send` session-wide; email transport tests must target `_transport_send`.
- Fixtures: use `test_tenant` / `auth_headers` (admin) / `analyst_headers` / `viewer_headers` from `backend/tests/conftest.py`.

## Configuration

**One registry owns every knob**: `backend/service_config/registry.py` declares
all 45 `Settings` fields with what each does, whether it is required, secret or
panel-editable, and **what stops working without it**. `backend/.env.example`
and `docs/configuration.md` are GENERATED from it
(`python -m backend.scripts.gen_config_docs`, `--check` in the suite) — edit the
registry, never those files. A new `Settings` field with no descriptor turns
`test_registry_covers_every_setting` red, which is the point.

**Two layers, and the app says which one won.** The environment is the floor;
`/instalacion` writes overrides that are stored encrypted (the Fernet key in
`backend/service_config/crypto.py`, still named `INTEGRATIONS_SECRET_KEY` for
compatibility) and take effect without a restart, and **the stored value beats
the file**. Consumers read `service_config.resolver.effective(tenant_id)`, never
`settings.x` — a consumer that reads the singleton directly cannot be
reconfigured without a redeploy. Environment-only on purpose: `SECRET_KEY`,
`DATABASE_URL`, `FRONTEND_URL`, `ALLOWED_ORIGINS`, `INSTANCE_ADMIN_EMAILS`,
`INTEGRATIONS_SECRET_KEY` and the deployment switches.

**Who may edit it is NOT the `admin` role.** `admin` lives inside a tenant and
every signup gets one; the deployment's credentials are not a tenant's to read.
`INSTANCE_ADMIN_EMAILS` (env only) names the instance operators, and empty means
nobody edits instance configuration from the app — the panel says so and names
the variable. A tenant admin still configures its OWN channels (email sender,
WhatsApp number), scoped by the token.

**Every optional service degrades out loud**: missing credential → the feature
reports itself off and names what is lost, never a 500 and never an answer from
somewhere else. `/health` carries the per-service state; `/service-config/
capabilities` carries booleans for any signed-in user, so a screen says "the
assistant is unavailable" before somebody types instead of after.

Backend env lives in `backend/.env` (see `backend/.env.example` for every variable). Notable:
- `TESTING_MODE=true` bypasses all quotas/rate limits — the server **refuses to boot** with it in `ENVIRONMENT=production`.
- `RESEND_API_KEY`, `TWILIO_*` activate email/WhatsApp; without them, sends are logged no-ops.
- `DEEPSEEK_API_KEY` is required for every AI feature (chat/RAG/narrative/data-quality). There is no fallback provider — see AI features above.
- `CONTACT_WHATSAPP` / `CONTACT_EMAIL` / `UPGRADE_NOTIFY_EMAIL` are the only commercial surface: with them empty the app hides its "write to us" buttons, and a free tenant that hits a ceiling has no way out.

Frontend env: `Frontend/.env.local` is gitignored and per-machine, and **it beats a shell `BACKEND_URL=...`** — setting the variable before `npm run dev` does nothing if that file names a different port. When the app 500s on every `/api/*` call with an empty body, check it first. Two symptoms worth recognising, because neither looks like what it is: a proxy pointing at a port nothing listens on gives `500` with no body (not a connection error), and a backend still running old code answers `404 {"detail":"Not Found"}` on new routes — FastAPI's own 404, not the endpoint's. `uvicorn` does not reload on edits, so restart it after backend changes.
