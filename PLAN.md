# Implementation Plan — PENDIENTES.md

Verified against the codebase on 2026-07-26. Ordered by phases: bugs first, then UX cleanup, then features. Each item lists what already exists so we don't rebuild it.

---

## STATUS 2026-07-26 — Phases 0, 1, 2 and 4 are DONE and browser-verified

Shipped: accuracy KPI fix, phone verification + PATCH security hole, per-plan
limits, chart axis labels, predictions toolbar cleanup, horizon/granularity/name
in Quick Start (navbar horizon removed), session history + rename, dataset reuse,
manual POs, per-line supplier selection, WhatsApp-to-self forwarding, phone at
signup, plan reconciliation, API/webhooks gating.

**Extra bug found during browser QA and fixed** — the real root cause of
PENDIENTES #3's "flat history, huge jump" complaint: `sku-intelligence` derived
the granularity floor from the HISTORY's frequency, so a weekly session served
weekly forecast totals (~164) next to daily history (~30) on one axis. The floor
now follows the FORECAST's frequency (`backend/api/v1/forecasts.py`), so finer
grains are no longer offered and the series is continuous. Guarded by
`backend/tests/test_forecast_chart_granularity.py`.

**Phase 3 is DONE too — every PENDIENTES item is now implemented.**

- **Transfers (#2)**: `transfer_lanes` table (per-lane lead time, cost per unit,
  fixed cost) with CRUD UI under the warehouse controls; transfers freeze their
  lead time and ETA on creation; `_network_transfer_pass` is time- and
  cost-aware and emits structured `{reason_code, params}` verdicts rendered as
  plain recommendation TEXT (never an alert, per the user's explicit ask); the
  MILP prices each lane and forbids transfers that cannot arrive in the bucket.
  Browser-verified: making a lane slow made the engine pick a different donor
  warehouse instead of that lane.
- **Events (#6)**: `family` scope (new `inventory_stock.family`), resolution
  order sku > family > category > event.
- **What-if (#7)**: `scenarios` table + preview/run endpoints wiring the
  previously dead `ScenarioEngine`; four rule types (demand multiplier, promo,
  supplier delay, safety stock); base-vs-scenario deltas computed through the
  existing semáforo, not a reimplementation. Browser-verified: x1.4 demand gave
  +3044 units / +$10,866 and dropped overstock SKUs from 8 to 1.
- **Clone a previous session (#11)**: the last deferred item — Quick Start now
  offers upload / reuse dataset / clone session, the clone carrying the previous
  column mapping so a repeat run needs no re-mapping.

### Adversarial browser QA (2026-07-26)

15 deliberately broken CSVs uploaded through the real UI (empty, headers-only,
invalid dates, text/negative/NaN quantities, duplicates, single row, CSV formula
injection + XSS + emoji SKUs, binary renamed .csv, ragged rows, unrecognizable
columns, all-zero demand, extreme outlier, time-travel dates, Spanish-Excel
`;`+BOM). Four real bugs found and fixed:

1. **`KeyError('model')` leaked to the user as "El entrenamiento falló: 'model'".**
   When every SKU-model pair fails, the metrics frame is empty and
   `get_metrics()` grouped it anyway. Now an empty run is a valid outcome and
   the worker raises `TrainingDataError("no_models_trained")`, a stable code the
   frontend renders as an actionable Spanish sentence.
   (`ForecastingCore/.../engine.py`, `backend/workers/runner.py`,
   `ForecastingCore/tests/test_metrics_empty_run.py`)
2. **Spanish-Excel CSVs (`;` separator) loaded as ONE column.** No `read_csv`
   passed a separator, so `sku;fecha;cantidad` became a single unusable column —
   while the client-side check understood `;` perfectly, making it look like a
   UI bug. Added separator sniffing + `utf-8-sig` across the dataframes boundary
   and the engine loader. (`backend/tests/test_csv_separator.py`)
3. **"Precisión: 100%" on a catalog that never sold.** WAPE divides by total
   real demand, so an all-zero series scores 0 error and reported perfect
   accuracy — exactly the meaningless metric PENDIENTES #3 complains about.
   Both the per-SKU figure and the session KPI now report nothing instead.
4. **Raw Pydantic English shown to users on 422s** (e.g. "qty: Input should be
   less than or equal to 1000000000"). Fixed twice over:
   - The manual-PO form now validates bounds client-side with Spanish copy, so
     the round trip never happens for the common mistakes.
   - **Systematically**: `ApiError` keeps the structured `fieldErrors`
     (Pydantic's stable `type` + `ctx`, not just its English `msg`) and
     `useErrorDetail` rebuilds the sentence from
     `errors.validation.<type>` + `errors.field.<name>`. Covers ~36 Pydantic
     rules and ~40 field names, so EVERY 422 in the app now reads in the user's
     language ("El multiplicador no puede ser mayor que 10."). Unknown rules or
     unnamed fields still degrade to the backend's English rather than to
     silence. (`Frontend/src/lib/api.ts`, `components/ui/States.tsx`)

Confirmed working under abuse: per-row date/number validation with business
explanations, ragged-row detection, binary-file rejection, required-field
gating, `<script>` payloads rendered inert as text, and the session history
faithfully showing failed/abandoned/completed runs.

### Test status

- After phases 0/1/2/4: **1403 passed, 19 skipped** (one pre-existing
  supplier-health test updated to the new skipped-vs-unresolved semantics).
- After phase 3: **1465 passed, 19 skipped, 3 failed**. All three failures had a
  single root cause — the historical `migrate_event_multiplier_scope_categoria`
  migration re-adds the scope CHECK on every startup and still listed only
  `('sku','category')`, so it blew up once family-scoped rows existed, taking
  `run_all()` (and the two PO-number backfill tests that call it) with it.
  Fixed by keeping that vocabulary in sync with the widening migration.
- After that fix and the QA fixes above, the full suite was run to completion:
  **1482 passed, 19 skipped, 0 failed** (12m08s).

---

## Phase 0 — Correctness bugs (do first: these destroy user trust)

### 0.1 Fix headline accuracy (PENDIENTES #3)
**Root cause found.** The KPI is `1 − mean(WAPE)` over **every model × SKU row, including naive/seasonal-naive/historical-avg baseline rows** (`backend/inventory/service.py:2084-2095`). The per-SKU card instead uses best-model WAPE (`Frontend/src/app/skus/page.tsx:1913-1915`), so the two numbers can't agree and the aggregate is meaningless.

- Per SKU: take the best non-baseline model (exclude `type: 'baseline'` rows).
- Aggregate demand-weighted (by SKU volume), not unweighted mean.
- Keep `1 − WAPE` as the definition; document it in the UI tooltip.
- Add a sanity flag: flat history + large forecast jump ⇒ mark forecast low-confidence, show WAPE/bias instead of a glossy %. Investigate the jump case itself as a separate debugging task (likely promo/price features or quantile model override in `predictor.py:228-238`).

### 0.2 Phone change flow (PENDIENTES #5)
**Root cause found.** Outside `ENVIRONMENT=production` the backend never sends the code — it returns `debug_code` in JSON (`backend/api/v1/users.py:117-122`), and a production-built frontend hides the debug code (`config/page.tsx:849, 986-990`) ⇒ user sees "code sent", nothing arrives.

- Send the code whenever Twilio creds exist, regardless of environment (keep `debug_code` only when Twilio is absent AND non-production).
- **Security hole:** `PATCH /users/me` updates `whatsapp_number` without clearing `whatsapp_verified_at` and without the linked-to-another-user check (`backend/users/service.py:102-106` vs `whatsapp/identity.py:69-77`). The WhatsApp bot authenticates on number+verified ⇒ inbound identity spoof. Fix: number change via PATCH clears verification and re-checks uniqueness.
- Add resend button with cooldown/rate limit on `/whatsapp/link`.
- Move Spanish literals to i18n/error codes (`users.py:122`, `:131` — violates CLAUDE.md language rule).

### 0.3 Plan enforcement bugs (PENDIENTES #8)
- `max_dataset_size_mb` declared per plan but uploads validate only global 200 MB (`backend/datasets/service.py:22-27` vs `entitlements/plans.py`). Enforce plan value.
- `max_concurrent_jobs` reads global settings, never the plan (`backend/workers/worker.py:24,46`). Enforce per-tenant.

### 0.4 Chart rendering (PENDIENTES #9)
- X-axis date labels overlap: `axisLabel.hideOverlap` + auto interval/rotation in `buildChartOption` (`Frontend/src/app/skus/page.tsx:421-710`).
- General spacing pass on the chart panel.

---

## Phase 1 — UX cleanup (existing screens)

### 1.1 Predictions screen (PENDIENTES #9)
Toolbar today (`skus/page.tsx:963-1130`): granularity chips, line/area/bar, Sum/Avg agg chips, model select (single), 4 CI band toggles, reset-zoom, export, legend toggle.

- **Remove:** Sum/Avg chips, reset-zoom + ECharts toolbox buttons, legend toggle, area type.
- **Keep:** line/bar toggle only, granularity chips, export, model selector.
- Zoom/pan by gestures only (ECharts `dataZoom` inside mode already does wheel/pinch/drag).
- Add fullscreen expand on the chart panel.
- **Delete** the "Seasonal demand / Predictability: High / 97% accuracy / You can confidently…" block (`skus/page.tsx:1904-1965`, i18n `skus.confidence_msg_*`, `skus.predictability_label`). Show per-SKU accuracy only when a SKU's prediction is selected.
- Uncertainty band: already computed end-to-end (walk-forward residual quantiles + real P10/P50/P90 quantile models) and rendered with toggles (`CI_BANDS`, `skus/page.tsx:79-84, 462-509`). Just default P10–P90 ON and collapse the 4 toggles into one on/off.
- Model comparison: today the selector swaps series; comparison = two stacked session charts. Add multi-model overlay on one axis (checkboxes over `available_models`, one series each).

### 1.2 Horizon → Quick Start; kill the navbar control (PENDIENTES #10/#11)
Today: navbar `PlanningControl` (`components/layout/PlanningControl.tsx`, mounted `TopBar.tsx:162`) is only a view window; the real training horizon is **hardcoded to 30** in quick-start (`quick-start/page.tsx:413`) and then overridden per grain by `GENEROUS_REACH` (`backend/sessions/family_service.py:21`).

- Remove `PlanningControl` from the TopBar.
- Quick Start: add horizon presets (4 weeks / 8 weeks / 6 months) + granularity choice; write `forecast_cfg.horizon` and make `family_service.plan_family` respect it (cap `GENEROUS_REACH` by user choice).
- Session-scoped only; nothing global.

### 1.3 Session naming (PENDIENTES #10)
Backend fully supports name/rename (`POST /sessions` name, `PATCH /sessions/{id}`); frontend never uses it (`createSession()` called with no arg, `patchSession` has zero call sites).

- Name input in Quick Start (e.g. "Forecast diario"); placeholder = current timestamp default.
- Rename control where sessions are listed.

### 1.4 Session history page (PENDIENTES #10)
No dedicated page exists; sessions appear only as pickers. `GET /sessions` (paginated) exists.

- New page listing sessions: name, date, dataset, horizon, SKU count, granularity, status.
- Click ⇒ open that session's predictions (`/skus` with session preselected).
- May need one enrichment of the list response (dataset name, SKU count).

### 1.5 Dataset reuse (PENDIENTES #10/#11)
Model already supports N sessions → 1 dataset; `GET /datasets` exists but the frontend never calls it. Quick-start retry after a mapping mistake forces full re-upload and burns a session against `max_sessions`.

- Quick Start step 1 becomes: **Upload new / Use existing dataset / Clone previous session**.
- Add `listDatasets` client fn + picker UI; retry reuses the already-uploaded dataset.
- Resolve the datasets-vs-datasources duplication (two parallel concepts: quick-start uses datasets, `/data` uses datasources with full CRUD). Proposal: keep `datasets` as the training-input concept, bridge "save as dataset" from datasources; do not merge tables now.

---

## Phase 2 — Purchase orders (PENDIENTES #1)

Today: single creation path `POST /inventory/log-po`, session-bound, lines derived from PEDIR_YA/PEDIR_PRONTO. Supplier = free-text inherited from the SKU stock row, resolved by name at send time, **silently skipped on mismatch** (`api/v1/inventory.py:1101-1103`). Quantities ARE editable pre-generation (`/hoy` cart, `/inventory`). WhatsApp send exists but only direct-to-supplier via Twilio.

### 2.1 Manual PO
- `POST /inventory/po` accepting `{supplier_id, destination_warehouse?, lines:[{sku, qty, unit_cost?}]}` with no session. Migration: `inventory_po_log.session_id` nullable + `source: 'forecast'|'manual'`.
- "New order" UI on `/pedidos`: pick supplier, add SKUs, set quantities.

### 2.2 Supplier selection on forecast-driven POs
- Supplier `<select>` per cart line on `/hoy`, persisted as `supplier_id` (not free text) on PO lines.
- Default from `sku_suppliers.is_primary` (table + CRUD exist, currently unused by the PO path).
- Name-mismatch silent skip becomes an explicit error.

### 2.3 WhatsApp to the user's own number
- New buyer-facing PO text builder (existing `build_po_supplier_text` is supplier-facing).
- On PO confirm: send the message to `users.whatsapp_number` + always offer `wa.me` deep link / copy-to-clipboard (works with zero Twilio config). User forwards it to the supplier.
- Make phone required at signup: add to `SignupRequest` (`backend/schemas/auth.py:5-9`) + signup form; E.164 validation exists. Verification stays post-signup.
- Keep the existing direct-to-supplier send as an option.

---

## Phase 3 — Features

### 3.1 Transfers: lead time + cost + recommendation (PENDIENTES #2)
Already exists: warehouses CRUD, full transfer lifecycle (send→receive→partial→close/cancel, shrinkage ledger), coverage-based transfer-vs-buy pass (`service.py:1072 _network_transfer_pass`), MILP optimizer. Missing: **time and money**.

- Migration: `inventory_transfer_log.lead_time_days` + `expected_arrival`; per-lane config table (`from_warehouse`, `to_warehouse`, `lead_time_days`, `cost_per_unit`, `fixed_cost`) editable in warehouse settings.
- Feed lane lead time + cost into `_network_transfer_pass` and the MILP (today: transfers instantaneous, hardcoded 0.5/unit — `optimizer_service.py:23`).
- Recommendation text (not an alert): "Transfer 80 units from Bodega Central — arrives in 1 day, cheaper than a new PO" vs "Buy — transfer would take too long". Emit structured `{action, reason_code, params}`; Spanish via i18n.
- Small fixes: warehouse rename/delete; `is_default` silently discarded on name collision.

### 3.2 Events: close the gaps (PENDIENTES #6 — mostly built already)
Already exists: `inventory_events` + `inventory_event_multipliers` (sku|category scopes), 21-entry CR/CO catalog with default multipliers, resolution order sku > category > event, simulate endpoint, full UI panels in `/inventory`.

- Add `family` scope to multipliers (needs a product-family field on SKUs — check what grouping exists beyond category first).
- Optional: promote events out of `/inventory` panels into a dedicated route for discoverability.
- Catalog: add more countries only on demand.

### 3.3 What-if scenarios (PENDIENTES #7)
Exists piecemeal: event simulator (endpoint + modal), per-SKU client-side sliders (lead time/demand/stock), and a **fully unwired** `ScenarioEngine` in ForecastingCore (`scenarios/scenario.py`, zero references from backend).

- Wire `ScenarioEngine` through the dataframes boundary: `POST /sessions/{id}/scenarios` applying named rule sets (event ±%, supplier delay, safety-stock change, promo) on the base forecast; return deltas (demand, POs triggered, cash).
- UI: scenario builder + side-by-side base-vs-scenario comparison; reuse the existing compare layout.
- Persist named scenarios per session.

---

## Phase 4 — Product/plan reconciliation

### 4.1 API / Webhooks page (PENDIENTES #4)
Findings: real endpoints, but **API keys are never accepted for auth anywhere** (JWT only — keys are decorative, "Last used" reads Never forever); `accuracy.degraded` webhook is offered but never fired; sidebar link has no feature gate so starter/professional admins get a raw 403.

**Recommendation (option "Próximamente"):** gate the sidebar entry on `Feature.API_ACCESS` with the standard upsell modal; inside the page mark API keys "Coming soon" (or hide the tab) until key auth is actually implemented; remove `accuracy.degraded` from the event list until it's emitted. Making keys real (auth middleware accepting `sk_live_*`) is Enterprise-tier work, later.

### 4.2 Plans (PENDIENTES #8)
Exists end-to-end (catalog `entitlements/plans.py`, enforcement, landing, `/planes`, upsell modal). Needed:
- Starter `max_skus` 500 → **1000** + update copy (3 hardcoded places — landing `page.tsx`, `planes/page.tsx`, `plans.py`; consolidate if cheap).
- Copy vs catalog contradictions: ABC-XYZ advertised in Starter but gated Professional; "ERP API integration" advertised in Professional but gated Enterprise. Decide per feature (default: fix the copy to match the catalog).
- Enterprise already modeled as custom/unlimited; copy only.
- Note "Interfaz simplificada" for Starter is a product design decision — out of scope until defined.

---

## Suggested execution order

| # | Item | Size |
|---|------|------|
| 1 | 0.1 accuracy fix + 0.4 chart rendering | S–M |
| 2 | 0.2 phone flow + security hole | S |
| 3 | 1.1 predictions cleanup (incl. delete confidence copy) | M |
| 4 | 1.2 horizon → Quick Start, remove navbar | M |
| 5 | 1.3 + 1.4 session naming + history | M |
| 6 | 1.5 dataset reuse in Quick Start | M |
| 7 | 2.1–2.3 purchase orders (manual, supplier, WhatsApp-to-me) | L |
| 8 | 0.3 + 4.2 plan limits + copy | S |
| 9 | 4.1 API/webhooks gating | S |
| 10 | 3.1 transfer lead time/cost | M–L |
| 11 | 3.2 events family scope | S–M |
| 12 | 3.3 what-if engine | L |

Every phase lands with the mandated tests: DB-state asserts + viewer/analyst permission pairs; UI changes verified by actually clicking through the app.
