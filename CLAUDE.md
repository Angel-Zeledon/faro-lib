# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

Do not put ML logic in `backend/` or business logic in `Frontend/`.

## Running

```bash
# Backend (port 8010 for local dev; needs Postgres — see Database below)
backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --port 8010

# Frontend (port 5000; proxies /api/* to BACKEND_URL, default localhost:8000)
cd Frontend && set BACKEND_URL=http://localhost:8010&& npm run dev

# Tests
cd backend && python -m pytest tests/ -q          # needs local Postgres on :5544
cd ForecastingCore && python -m pytest tests/ -q  # pure Python, no DB
cd Frontend && npx tsc --noEmit                   # typecheck
```

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
