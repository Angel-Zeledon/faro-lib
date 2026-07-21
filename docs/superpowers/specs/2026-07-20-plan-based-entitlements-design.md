# Plan-Based Entitlements — Design

**Date:** 2026-07-20
**Status:** Approved (design), pending implementation plan
**Author:** brainstorming session

## Goal

Split product functionality by subscription plan. Both **feature gating**
(whole capabilities on/off per plan) and **numeric limits** (SKUs, users,
locations, sessions) are derived from a single plan attribute on the tenant.

Billing (Stripe) is not part of this work — it lands at the pre-launch milestone
(~month 3). This work builds the entitlement layer that Stripe will later drive
by simply setting `tenant.plan` and clearing `trial_ends_at`.

## Plan catalog

Three paid plans. No `free` tier.

| Limit       | Starter | Professional | Enterprise |
|-------------|:-------:|:------------:|:----------:|
| SKUs        | 500     | 5,000        | ∞          |
| Users       | 2       | 10           | ∞          |
| Locations   | 1       | 5            | ∞          |

Numeric limits come from the product strategy (Starter $99 / Professional $299 /
Enterprise $799). `∞` is represented as `None` in the catalog (no enforcement).

### Feature matrix

Composed by union: Professional = Starter + its extras; Enterprise =
Professional + its extras. A higher tier can never lose a lower tier's feature.

| Feature (key)                          | Starter | Pro | Enterprise |
|----------------------------------------|:-------:|:---:|:----------:|
| Semáforo / stock, coverage (`SEMAPHORE`)        | ✅ | ✅ | ✅ |
| PO generation Excel/PDF (`PO_GENERATION`)       | ✅ | ✅ | ✅ |
| Reception (`RECEPTION`)                          | ✅ | ✅ | ✅ |
| Suppliers + lead-time learning (`SUPPLIERS`)     | ✅ | ✅ | ✅ |
| Reports PDF/Excel (`REPORTS`)                     | ✅ | ✅ | ✅ |
| Email alerts (`EMAIL_ALERTS`)                     | ✅ | ✅ | ✅ |
| ABC-XYZ classification (`ABC_XYZ`)                | ❌ | ✅ | ✅ |
| WhatsApp daily alerts (`WHATSAPP_ALERTS`)         | ❌ | ✅ | ✅ |
| AI Analyst — RAG / chat / narrative (`AI_ANALYST`)| ❌ | ✅ | ✅ |
| Documents RAG (`DOCUMENTS_RAG`)                   | ❌ | ✅ | ✅ |
| Event simulator (`EVENT_SIMULATOR`)               | ❌ | ✅ | ✅ |
| MILP optimizer (`MILP_OPTIMIZER`)                 | ❌ | ✅ | ✅ |
| Scheduled reports (`SCHEDULED_REPORTS`)           | ❌ | ✅ | ✅ |
| Multi-location (`MULTI_LOCATION`)                 | ❌ | ✅ | ✅ |
| BOM / production (`BOM`)                          | ❌ | ❌ | ✅ |
| API access + keys (`API_ACCESS`)                  | ❌ | ❌ | ✅ |
| Webhooks / integrations (`WEBHOOKS`)              | ❌ | ❌ | ✅ |

**Rule:** any endpoint not explicitly gated is **core** — available on every
plan. Only the features above are gated. Niche endpoints not listed (shrinkage,
price-breaks, cash-calendar, ROI, morning-briefing) are core.

## Architecture — Option 1: central code catalog + declarative guards

Single source of truth in code. Rejected alternatives: DB-driven `plan_features`
table (runtime-editable but overkill pre-launch, drift risk, needs admin UI);
per-tenant feature booleans in the `quota` JSONB (no single source of truth,
duplicated matrix per tenant, guaranteed drift).

### 1. Data model (`tenants`)

- `plan` values become `'starter' | 'professional' | 'enterprise'`.
- New column `trial_ends_at TIMESTAMPTZ NULL`:
  - `NULL` → active paid plan, no expiry (what Stripe sets on payment).
  - `>= now` → **trialing**, full Starter access.
  - `< now` → **trial expired** → read-only.
- `create_tenant()` starts new tenants at `plan='starter'`,
  `trial_ends_at = now + 14 days`, `status='active'`.
- Migration (idempotent): add `trial_ends_at` column; convert existing `'free'`
  tenants → `'enterprise'`, `trial_ends_at = NULL` (grandfathered so dev/demo
  data is never blocked).
- `_DEFAULT_QUOTA` stops being the source of limits. The `quota` JSONB remains a
  per-tenant **override** only (bespoke Enterprise deals). Default `'{}'`.

### 2. Entitlements module (`backend/entitlements/`)

- `plans.py` — source of truth:
  - `class Feature(str, Enum)` — the 17 keys above.
  - `@dataclass PlanDef`: `max_skus`, `max_users`, `max_locations`,
    `max_sessions`, `max_concurrent_jobs`, `max_dataset_size_mb` (each `int |
    None`), and `features: frozenset[Feature]`.
  - `PLAN_CATALOG: dict[str, PlanDef]` built by union so tier supersets hold.
- `service.py`:
  - `get_plan_def(plan) -> PlanDef`
  - `has_feature(tenant, feature) -> bool`
  - `tenant_limits(tenant) -> dict` — catalog limits merged with `quota` override.
  - `trial_state(tenant) -> 'active' | 'trialing' | 'expired'`
  - `is_read_only(tenant) -> bool`

### 3. Backend enforcement (`backend/entitlements/guards.py`)

- `require_feature(Feature.X)` — FastAPI dependency factory. If the tenant's plan
  lacks the feature → **HTTP 403** with structured body:
  `{code: "PLAN_UPGRADE_REQUIRED", feature, current_plan, required_plans}`.
- `require_active_plan` — if `is_read_only(tenant)` → **HTTP 403**
  `{code: "TRIAL_EXPIRED"}`. Layered **inside the existing mutation guard**
  (`require_analyst_or_above`) so every mutating endpoint is covered in one place.
  Read endpoints (`get_current_user`) are untouched.
- Limit helpers: `enforce_skus_limit`, `enforce_users_limit`,
  `enforce_locations_limit` at their mutation points, and re-enable the currently
  commented `check_session_quota` (`backend/api/v1/sessions.py:28`) using catalog
  limits. On breach → 403 `{code: "PLAN_LIMIT_REACHED", limit, current, max}`.
- Feature-guard wiring:
  - Router-level: `ai_insights` / `analyst` / `chats` → `AI_ANALYST`;
    `documents` → `DOCUMENTS_RAG`; `api_keys` → `API_ACCESS`;
    `webhooks` → `WEBHOOKS`; `schedule` → `SCHEDULED_REPORTS`.
  - Endpoint-level in `inventory.py`: `events/*` → `EVENT_SIMULATOR`;
    `bom/*` → `BOM`; `warehouses/*` → `MULTI_LOCATION`; MILP optimize endpoint →
    `MILP_OPTIMIZER`; `alerts/send-now` → `WHATSAPP_ALERTS`.
  - `ABC_XYZ`: the classification is surfaced as a field inside otherwise-core
    inventory responses (e.g. dashboard summary / scorecard), not a standalone
    endpoint. Gate it by **omitting the ABC-XYZ field** when the plan lacks the
    feature (graceful degradation), rather than returning 403 for the whole
    endpoint. The exact response shapes are pinned during planning.
  - Daily WhatsApp alert loop (worker) skips tenants without `WHATSAPP_ALERTS`.
- `settings.testing_mode = True` **bypasses** all enforcement (same pattern as
  today's `check_session_quota`), so the suite is unaffected unless a test opts in.

### 4. Frontend

- `GET /entitlements` → `{ plan, trial: {state, ends_at}, limits,
  features: {key: bool}, read_only }`.
- `src/lib/api.ts`: `getEntitlements()` + `Entitlements` type.
- `useEntitlements()` hook/context, loaded after login: `has(feature)`, `limits`,
  `readOnly`.
- Nav: items outside the plan render a 🔒 lock; click opens an upsell modal /
  `/planes` page.
- Read-only banner when the trial has expired; mutation buttons (create session,
  train, generate PO) disabled.
- The backend is the real gate; the frontend is UX only.

## Testing (per repo testing mandate)

- `test_entitlements.py`: catalog integrity (each tier is a superset of the
  lower), `has_feature` logic, `tenant_limits` merge with override.
- Feature gates: Starter tenant → 403 `PLAN_UPGRADE_REQUIRED` on
  WhatsApp / AI / API / etc., asserting **DB state unchanged**; Professional →
  success (assert the state change via direct DB query).
- Trial expiry: tenant with `trial_ends_at` in the past → mutation 403
  `TRIAL_EXPIRED` + state intact; a read still returns 200. Active tenant →
  success.
- Limits: creating a 3rd user on Starter (max 2) → 403 `PLAN_LIMIT_REACHED` +
  user count unchanged in DB.
- These tests `monkeypatch settings.testing_mode = False` themselves (the bypass
  would otherwise nullify them).
- Permission pairs preserved: every gated mutation keeps its viewer-denied /
  analyst-success pair in addition to the plan checks.

## Out of scope

- Stripe / billing / payment collection (month 3 pre-launch).
- Self-serve plan upgrade flow (the `/planes` page links to contact/upgrade;
  actual plan change is administrative until billing exists).
- Re-introducing a `free` tier.
- Per-tenant custom feature overrides beyond numeric `quota` overrides.
