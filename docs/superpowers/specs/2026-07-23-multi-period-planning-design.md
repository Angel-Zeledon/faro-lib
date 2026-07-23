# Multi-Period Planning (admin-chosen view granularity) — Design

> Date: 2026-07-23
> Status: approved in brainstorm
> Related engine capability: `forecasting_core` already trains at a target frequency
> (`granularity.strategy="aggregate"`, `target_freq`), and `temporal_agg.py`
> (`detect_frequency`, `available_granularities`, `aggregate_*`) already powers the
> per-SKU chart granularity chip on `/skus`.

## Goal

Let the tenant administrator choose how the WHOLE app presents and plans over
time — **por día / por semana / por mes** — with a **horizon expressed in that
same unit** (daily + 7 = 7 days, monthly + 4 = 4 months). Today the only
granularity control is a per-chart chip buried on `/skus`; the inventory /
planning side (`/hoy`, `/inventory`, semáforo, recommendations) is daily-only,
and nothing is discoverable or global.

## Decisions (from brainstorm)

1. **Global, per-tenant view setting** — one active planning period governs the
   whole app, not a per-view toggle. Admin chooses; analyst/viewer see it read-only.
2. **Re-plan at that period** — each period is backed by a REAL session trained at
   that frequency (not daily math re-labeled). Demand/coverage/semáforo are honest
   per-period quantities.
3. **Pre-compute all supported periods** — one upload trains the whole family
   (daily/weekly/monthly, gated by data) up front, so switching period is INSTANT.
4. **Horizon is in the period's own unit**, and is a cheap knob: each period's
   session is pre-forecast to a generous reach; the admin's horizon is a WINDOW
   into that (no re-train). Granularity needs a pre-trained session; horizon does not.

## Core model — the session family

Today: one upload → one session at native (daily) frequency; the app uses
"latest completed session" (`inventory/service.py get_latest_completed_session`)
or a user-picked one via `useAutoSession`.

New: one upload → a **session family** — the same dataset trained once per
supported granularity, all sharing a `family_id`, each carrying its own
`granularity` (`daily` | `weekly` | `monthly`). The tenant has an active
`planning_period` + `planning_horizon` (in that period's units). A single
resolver returns "the session the app should use now" = the newest family's
session at `planning_period`.

### What "the data allows" means (the gate)

A granularity is offered ONLY if the history can actually train a model at it —
not merely resampled. Gate: with `base_freq = detect_frequency(dates)` and
`n_buckets(granularity)` = the number of distinct period-buckets the history
spans at that granularity, a granularity is **available** iff
`n_buckets >= MIN_BUCKETS_FOR_TRAINING` (reuse `validation_cfg.min_history`,
default 20, as the floor — a coarser grain must still yield ≥ that many training
points). Consequences:
- 6 weeks of daily data → daily + weekly, **not** monthly.
- `temporal_agg.available_granularities` currently returns all coarser grains
  ignoring `n_points`; this design makes it honor the bucket count (a real
  behavior fix, in scope because we depend on it).
- Base freq is the finest available (you can aggregate up, never down): a tenant
  whose data is natively weekly cannot get a "daily" period.

## Architecture — three phases

The work decomposes into three independently-testable phases behind one spec.

---

### Phase A — Session-family training + generous-reach forecasts

**What:** when a training run is launched (wizard finish, demo quickstart, or an
integrations sync), fan it out into one job per available granularity instead of
a single native-frequency job.

- **Schema (migration):** `sessions` gains `family_id TEXT` and `granularity TEXT`
  (nullable — existing rows keep NULL and behave as a lone "daily" family of one,
  so nothing pre-existing breaks). Index `(tenant_id, family_id)`.
- **Family orchestration** (`backend/sessions/family_service.py`, new):
  `plan_family(tenant_id, base_session_id) -> list[granularity]` computes the
  available granularities from the dataset's dates (via `detect_frequency` +
  the bucket gate). For each, it creates a sibling session sharing `family_id`
  with `granularity_cfg = {"strategy": "aggregate", "target_freq": FREQ_RULES[g]}`
  (native for the base freq) and a **generous** `forecast_cfg.horizon` per grain
  (`_GENEROUS_REACH = {"daily": 90, "weekly": 26, "monthly": 12}`), then enqueues
  a training job for each. The base/native session keeps `strategy="native"`.
- **Runner:** unchanged — it already consumes `granularity_cfg` + `forecast.horizon`.
  The fan-out only changes how many sessions/jobs are created, not how one trains.
- **Cost (stated tradeoff):** onboarding trains 1–3× instead of 1×. Acceptable for
  the <10-min goal (demo is ~90s × ≤3, still one user-visible "training…" wait since
  the sibling jobs run on the same in-process worker queue). The finest grain
  (daily) is enqueued FIRST so the semáforo is usable as early as today; coarser
  grains complete behind it.
- **Tests:** daily-only short data → family of 1; long daily data → family of 3
  (daily/weekly/monthly) sharing one `family_id`, each with the right
  `granularity` + `target_freq` + generous horizon; the bucket gate refuses
  monthly on <20 monthly buckets; native-weekly data never offers daily.

### Phase B — Active (period, horizon) setting + resolver + top-bar UX

**What:** the tenant-level setting and the single resolution point every screen
reads through.

- **Setting:** stored in `tenants.settings` JSONB under
  `planning = {"period": "daily", "horizon": 14}` (default preserves today's
  behavior exactly: daily, horizon 14). No new table.
- **Service** (`backend/sessions/planning_service.py`, new):
  - `get_planning(tenant_id) -> {period, horizon, available_periods, max_horizon}`
    — reads the setting, resolves the newest family, returns the family's available
    periods and the pre-computed reach cap for the current period.
  - `set_planning(tenant_id, period, horizon)` — validates `period ∈ available`
    and `1 <= horizon <= reach(period)`; admin-only.
  - `resolve_active_session(tenant_id) -> session_id | None` — newest family's
    session at `planning.period`; falls back to `get_latest_completed_session`
    when a tenant has no family (pre-feature data), so legacy tenants are unaffected.
- **API:** `GET /planning` (any user) and `PUT /planning` (admin-only —
  `require_admin`, a stricter guard than `require_analyst_or_above`; if none
  exists, add it in `auth/guards.py`). Wire `resolve_active_session` into the
  places that today call `get_latest_completed_session` (daily alert loop) and
  expose it so the frontend's `useAutoSession` can default to it.
- **UX:** a compact control in the top bar (`components/layout/`): **período**
  selector (gated to `available_periods`) + **horizonte** stepper (unit follows
  the period: "días" / "semanas" / "meses", capped at `max_horizon`). Admin sees
  it editable; others see the current value read-only (disabled). Switching period
  is instant (sessions pre-trained); changing horizon within the pre-computed reach
  is instant (windowing, Phase C). Mono-period tenants (family of 1) render it
  read-only or hidden — zero change for them.
- **Tests:** default planning = daily/14 for a fresh tenant; `set_planning`
  admin-success + analyst/viewer 403 with the setting unchanged (permission pair);
  invalid period / over-reach horizon rejected 422; resolver returns the
  period-matched session and falls back cleanly for a family-less tenant.

### Phase C — Per-period coverage/semáforo reinterpretation + horizon windowing

**What:** make the numbers honest in the active period's units.

- **Coverage & semáforo** (`inventory/service.py`): the active session is trained
  at the period frequency, so `daily_demand` from `_avg_daily_forecast` is really
  **per-period** demand for that session. Coverage = `current_stock / period_demand`
  is then in periods; the UI labels it in the period unit ("6 semanas"). The signal
  thresholds (`_calc_signal`) compare coverage against **lead time expressed in the
  same period** (`lead_time_days / days_per_period`, where
  `days_per_period = {daily:1, weekly:7, monthly:30}`), so PEDIR_YA/PRONTO/OK keep
  their meaning ("will I run out before a reorder arrives?") at any grain. Signal
  ENUM values are unchanged. Reorder qty / safety stock recompute at the period's
  demand and its variance — the existing formulas, fed period quantities.
- **Horizon windowing:** the forecast series and the optimizer are trimmed to the
  active `horizon` periods. Forecast chart: show the first `horizon` buckets of the
  pre-computed reach. Optimizer: `horizon_days` becomes `horizon × days_per_period`
  (capped at its existing `le=30` for daily-equivalent safety, or the cap is
  raised — decided in the plan). No re-forecast for horizons within the reach; a
  request beyond the reach is the single case that triggers a background
  re-forecast from cached models (`engine.predict(h)` — no re-train), out of scope
  for v1 (cap the stepper at the reach instead).
- **Presentation only where it must be:** `/skus` already aggregates per-chart; it
  now defaults its chip to the global period but keeps the local override.
- **Tests:** a weekly session's coverage reads in weeks and its semáforo matches a
  hand-computed period comparison; switching period flips the same SKU's coverage
  number and unit; horizon windowing returns exactly N buckets; daily/period=daily
  output is byte-identical to today (regression); optimizer horizon converts correctly.

## Cross-cutting

- **Tenant export/delete:** the family is just sessions — already covered by the
  cascade on `sessions`. The `planning` setting rides in `tenants.settings`,
  already exported. No new work.
- **Entitlements:** multi-period is not gated by plan in v1 (it's core UX, like the
  semáforo). If it later becomes a tier differentiator that's a one-line feature add.
- **i18n:** period/horizon labels and unit words (día/semana/mes, singular/plural)
  in both `es` and `en` blocks.

## Out of scope (v1)

- Re-forecasting beyond the pre-computed reach on demand (cap the stepper instead).
- Per-user (vs per-tenant) period preference.
- Mixing periods across screens simultaneously (one active period at a time).
- Changing the training models/validation per grain (same model routing at every grain).

## Phasing for the plan

Three implementation plans, in order, each shippable:
1. **A** — family schema + fan-out + generous reach (backend + engine wiring + tests).
2. **B** — planning setting + resolver + API + top-bar UX (backend + frontend + tests).
3. **C** — per-period coverage/semáforo + horizon windowing (backend + frontend + tests).
