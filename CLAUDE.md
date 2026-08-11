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
  `docs/estabilidad.md`** — the single live backlog and the order the work runs
  in. `docs/inventario-pantallas.md` is the table it draws from.
- Verify in a browser as a user, not only with tests. See the `running-faro`
  skill for the environment traps and `silent-failures` for the review lens.
- When robustness and scope conflict, robustness wins.
- Out of scope on the owner's instruction: CI/CD, Stripe.

## Project Overview

**Faro** — inventory purchasing decisions platform for LatAm SMB distributors.
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
# Backend (port 8010 for local dev; needs Postgres — see Database below)
backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8010

# Frontend (port 5000; proxies /api/* to BACKEND_URL, default 127.0.0.1:8010)
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
no end-to-end suite: every screen walk in `docs/inventario-pantallas.md` was done
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
- **AI features** (narrative, RAG analyst, chat, data-quality diagnosis): all go through the single factory `get_local_llm_client()` in `backend/ai/local_llm.py`. When `ANTHROPIC_API_KEY` is set, it returns a real Anthropic-backed client (model pinned to `settings.anthropic_model`, default the cheapest tier — Haiku); otherwise it falls back to a local Ollama server (`settings.local_llm_model`, default `deepseek-r1`). Every consumer (`rag_service.py`, `chats.py`, `narrator.py`, `narrative_service.py`, `configuration.py`'s data-quality blurb) calls this one factory and is agnostic to which backend actually serves the request — flipping the key alone switches all of them.
- **Storage**: Postgres for all metadata/results; binary files (datasets, artifacts, documents) on local disk under `storage/` (gitignored, never version it).

## Language (mandatory)

**All code is English. No exceptions.** Comments, docstrings, variable/function/class names, DB column names, API field names, test names, assertion messages and commit messages — English.

The only Spanish is **end-user copy**, and it lives in dedicated locale layers — **never hardcoded as a Spanish string literal in backend logic**. Frontend: the `es` values in `Frontend/src/i18n/translations.ts` (keys are English). Backend user-facing output — API error messages, explanation sentences, email/WhatsApp/PDF bodies, anything the user reads — must NOT be a Spanish string in code: the backend returns **English text or a structured error code + params**, and the **frontend renders the Spanish via i18n**. For backend-only channels the frontend never sees (email/WhatsApp/PDF), Spanish lives in a **backend locale catalog keyed by English identifiers** — the code references the key, not the Spanish. LLM prompts are written in English (they may instruct the model to *answer* in Spanish).

The dedicated sweep has run: identifiers, DB columns and API fields are English throughout. Four categories are deliberately still Spanish, and renaming them is **out of scope, not an oversight**:

- **App routes** — `/hoy`, `/pedidos`, `/skus`. Users may have shared these URLs.
- **Persisted signal values** — `PEDIR_YA`, `PEDIR_PRONTO`, `OK`, `SOBRESTOCK`, stored on stock rows and in PO history. Renaming needs a data migration, not a code change.
- **Spanish vocabulary that matches user data** — CSV header aliases (`fecha`, `ventas`, `categoria` in the canonical-column detectors), free-text payment terms (`contado`, `contra entrega`, `quincenal`), and calendar catalog keys (`co_quincena_15`). These are values the product reads from real user input.
- **Frontend end-user copy** — the `es` values in `translations.ts` and the Spanish marketing copy in the landing `page.tsx`. (Backend user-facing copy is **no longer** allowed as hardcoded Spanish — it moves to English + frontend i18n, or a backend locale catalog for email/WhatsApp/PDF. See above.)

## Testing Standards (mandatory)

- Assert **state changes with direct DB queries**, not just status codes or response echoes.
- Every mutating endpoint needs a **permission pair**: viewer denied (403 + state unchanged) AND analyst success.
- Never write tests that can't fail (either/or asserts, xfail on real bugs).
- Tests that depend on quotas/rate limits must `monkeypatch settings.testing_mode = False` themselves — the local `.env` runs with `TESTING_MODE=true`.
- conftest patches `backend.notifications.email._send` session-wide; email transport tests must target `_transport_send`.
- Fixtures: use `test_tenant` / `auth_headers` (admin) / `analyst_headers` / `viewer_headers` from `backend/tests/conftest.py`.

## Configuration

Backend env lives in `backend/.env` (see `backend/.env.example` for every variable). Notable:
- `TESTING_MODE=true` bypasses all quotas/rate limits — the server **refuses to boot** with it in `ENVIRONMENT=production`.
- `RESEND_API_KEY`, `TWILIO_*` activate email/WhatsApp; without them, sends are logged no-ops.
- `ANTHROPIC_API_KEY` switches AI features (chat/RAG/narrative) from the local Ollama fallback to the real Anthropic API — see AI features above.

Frontend env: `Frontend/.env.local` is gitignored and per-machine, and **it beats a shell `BACKEND_URL=...`** — setting the variable before `npm run dev` does nothing if that file names a different port. When the app 500s on every `/api/*` call with an empty body, check it first. Two symptoms worth recognising, because neither looks like what it is: a proxy pointing at a port nothing listens on gives `500` with no body (not a connection error), and a backend still running old code answers `404 {"detail":"Not Found"}` on new routes — FastAPI's own 404, not the endpoint's. `uvicorn` does not reload on edits, so restart it after backend changes.
