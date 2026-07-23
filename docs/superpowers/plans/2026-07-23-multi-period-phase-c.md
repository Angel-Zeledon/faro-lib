# Multi-Period Planning — Phase C Implementation Plan (per-period coverage/semáforo + horizon windowing)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the semáforo honest in the tenant's active planning period. Each active session is trained at its period frequency (Phase A), so its per-model forecast values are **per-period** demand (a weekly session forecasts units/week). Phase C reinterprets coverage as *periods of stock*, compares the signal against the **lead time expressed in that same period**, recomputes reorder/safety at the period's demand, windows the forecast + optimizer to the active `horizon`, and defaults every screen's session to the active period (spec: `docs/superpowers/specs/2026-07-23-multi-period-planning-design.md`, Phase C). The signal ENUM values (PEDIR_YA / PEDIR_PRONTO / OK / SOBRESTOCK) are unchanged — only the horizon they are judged against changes. **When the active period is `daily` the output is byte-identical to today** (`days_per_period=1` → every formula collapses to the current math); a regression test pins this.

**Architecture:** All per-period math is plain arithmetic in `backend/inventory/service.py` — no pandas, no engine change. A single constant `_DAYS_PER_PERIOD = {"daily": 1, "weekly": 7, "monthly": 30}` plus three tiny pure helpers (`_days_per_period`, `_lead_time_in_periods`, `_steps_for_lead_time`) drive the reinterpretation. `get_inventory_status` / `get_inventory_status_by_warehouse` gain a trailing `period="daily"` keyword (the frozen positional signature is untouched, so the alert loop / snapshot / PDF callers stay byte-identical). The `/status` and `/optimize` endpoints resolve the active `period` (and default the `session_id`) through Phase B's `backend/sessions/planning_service.py` — `get_planning(tenant_id)` and `resolve_active_session(tenant_id)` — so a screen that passes no `session_id` reflects the active period, while an explicit `session_id` (e.g. `/skus` picking a specific session) keeps working. The status envelope grows a `period` + `coverage_unit` so the frontend can label "6 semanas"; the item dicts are unchanged for `daily`. The optimizer's day-horizon cap is raised so a monthly horizon fits. Frontend reads the period from Phase B's planning context and labels coverage in día/semana/mes (singular/plural) via new `es`+`en` i18n keys.

**Tech Stack:** FastAPI + psycopg2 raw SQL; **no pandas anywhere in this phase** (per-period math is arithmetic); pytest against local Postgres :5544 (docker `faro_db`); Next.js 14 + TypeScript, verified with `tsc --noEmit`.

## Global Constraints

- All code, comments, tests, commit messages in **English** (CLAUDE.md). The ONLY Spanish is `Frontend/src/i18n/translations.ts` `es` values + backend end-user copy (explanation sentences).
- **No pandas in `backend/`** for this phase — the per-period conversion is arithmetic. (The `no-pandas-except-runner/temporal_agg` rule is not even approached here.)
- Signal ENUM values `PEDIR_YA` / `PEDIR_PRONTO` / `OK` / `SOBRESTOCK` / `SIN_DATOS` are **unchanged** — Phase C changes only the horizon they are compared against, never the vocabulary or the stored values.
- **Daily regression is first-class:** with `period="daily"`, `_days_per_period → 1`, `_lead_time_in_periods → lead_time_days`, `_steps_for_lead_time → lead_time_days`, so `get_inventory_status` returns a list byte-identical to the current code. Task 2 pins this with an explicit regression test; do not merge if it fails.
- `_DAYS_PER_PERIOD = {"daily": 1, "weekly": 7, "monthly": 30}` and the supported periods are exactly `daily` / `weekly` / `monthly` (matching Phase A's `GENEROUS_REACH`).
- Phase A + Phase B are assumed merged: `sessions.family_id` / `sessions.granularity` exist; `backend/sessions/planning_service.py` exposes `get_planning(tenant_id) -> {period, horizon, available_periods, max_horizon}` and `resolve_active_session(tenant_id) -> session_id | None`; Phase B's frontend exposes a planning context/hook (referred to below as `usePlanning()` returning `{period, horizon}`) and `GET /planning`. If Phase B named the hook differently, adapt the import — the data shape is what matters.
- `get_inventory_status` / `get_inventory_status_by_warehouse` add `period` as a **trailing keyword with default `"daily"`** — the frozen positional signature (API + alert/snapshot callers) is preserved; every existing caller keeps today's behavior.
- Run backend tests: `cd backend && python -m pytest tests/<file> -q` (needs Postgres on :5544). Run frontend typecheck: `cd Frontend && node ./node_modules/typescript/bin/tsc --noEmit`. Never run `npm run build` while `next dev` is running.

---

### Task 1: Period math helpers (`_DAYS_PER_PERIOD` + three pure functions)

**Files:**
- Modify: `backend/inventory/service.py` (add the constant + helpers next to `_calc_signal`)
- Test: `backend/tests/test_multi_period_coverage.py` (new)

**Interfaces:**
- Produces:
  - `_DAYS_PER_PERIOD = {"daily": 1, "weekly": 7, "monthly": 30}`.
  - `_days_per_period(period: str) -> int` — the map lookup, defaulting to `1` (daily) for any unknown value, so a bad/legacy period degrades to today's math instead of raising.
  - `_lead_time_in_periods(lead_time_days: float, period: str) -> float` — `lead_time_days / days_per_period`; the value `_calc_signal` and `_calc_recommended` are fed instead of `lead_time`. Float on purpose (a 15-day lead time is 2.14 weeks — the thresholds stay precise). For `daily` it equals `float(lead_time_days)`.
  - `_steps_for_lead_time(lead_time_days: float, period: str) -> int` — `max(1, ceil(lead_time_days / days_per_period))`; how many forecast buckets `_avg_daily_forecast` averages over (a lead time shorter than one period still averages ≥ 1 bucket). For `daily` it equals `int(lead_time_days)` for any positive integer lead time.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_multi_period_coverage.py
"""Per-period coverage/semáforo + horizon windowing (multi-period Phase C)."""

import math

from backend.inventory.service import (
    _DAYS_PER_PERIOD,
    _days_per_period,
    _lead_time_in_periods,
    _steps_for_lead_time,
)


class TestPeriodMathHelpers:
    def test_days_per_period_map(self):
        assert _DAYS_PER_PERIOD == {"daily": 1, "weekly": 7, "monthly": 30}
        assert _days_per_period("daily") == 1
        assert _days_per_period("weekly") == 7
        assert _days_per_period("monthly") == 30
        # Unknown/legacy period degrades to daily (1) — never raises.
        assert _days_per_period("fortnightly") == 1
        assert _days_per_period(None) == 1

    def test_lead_time_in_periods(self):
        # 14-day lead time: 2 weeks exactly, 0.4667 months.
        assert _lead_time_in_periods(14, "weekly") == 2.0
        assert _lead_time_in_periods(15, "weekly") == 15 / 7
        assert _lead_time_in_periods(30, "monthly") == 1.0
        # Daily is the identity: value equals the day count (as a float).
        assert _lead_time_in_periods(15, "daily") == 15.0

    def test_steps_for_lead_time_rounds_up_min_one(self):
        assert _steps_for_lead_time(15, "weekly") == 3     # ceil(15/7)
        assert _steps_for_lead_time(14, "weekly") == 2     # ceil(14/7)
        assert _steps_for_lead_time(5, "weekly") == 1      # ceil(5/7) -> 1 floor
        assert _steps_for_lead_time(45, "monthly") == 2    # ceil(45/30)
        # Daily identity: step count equals the integer day count.
        assert _steps_for_lead_time(15, "daily") == 15
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_multi_period_coverage.py -q -k "PeriodMathHelpers"`
Expected: FAIL — names not importable.

- [ ] **Step 3: Implement in `backend/inventory/service.py`**

Add directly above `_calc_signal` (around line 485):

```python
# ── Period-aware planning (multi-period Phase C) ──────────────────────────────
# A period-trained session (Phase A) forecasts PER-PERIOD demand: a weekly
# session's forecast values are units/week, a monthly session's are units/month.
# Coverage therefore comes out in periods, and the signal must be judged against
# the lead time expressed in that SAME period. This map is the only conversion
# factor; every helper below is plain arithmetic (no pandas).
_DAYS_PER_PERIOD = {"daily": 1, "weekly": 7, "monthly": 30}


def _days_per_period(period: Optional[str]) -> int:
    """Calendar days in one bucket of `period`. Unknown/legacy -> 1 (daily), so
    a bad value degrades to today's day-based math rather than raising."""
    return _DAYS_PER_PERIOD.get(period or "daily", 1)


def _lead_time_in_periods(lead_time_days: float, period: str) -> float:
    """Lead time expressed in the active period's units. Kept float so the
    signal thresholds stay precise (a 15-day lead time is 2.14 weeks). For
    `daily` this is exactly float(lead_time_days) — the identity that keeps the
    daily semáforo byte-identical to before Phase C."""
    return float(lead_time_days) / _days_per_period(period)


def _steps_for_lead_time(lead_time_days: float, period: str) -> int:
    """How many forecast buckets to average when estimating per-period demand:
    the lead time rounded UP to whole periods, at least one (a sub-period lead
    time still needs one bucket to average). For `daily` this equals
    int(lead_time_days) for any positive integer lead time."""
    return max(1, math.ceil(float(lead_time_days) / _days_per_period(period)))
```

`math` and `Optional` are already imported at the top of the module.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_multi_period_coverage.py -q -k "PeriodMathHelpers"`
Expected: 1 passed (the class, 3 methods).

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/service.py backend/tests/test_multi_period_coverage.py
git commit -m "feat(inventory): period math helpers for multi-period coverage"
```

---

### Task 2: Period-aware coverage/semáforo in `get_inventory_status`

**Files:**
- Modify: `backend/inventory/service.py` (`_calc_recommended`, `get_inventory_status`, `_compute_inventory_status`)
- Test: `backend/tests/test_multi_period_coverage.py` (extend)

**Interfaces:**
- Consumes: Task 1 helpers.
- Changes:
  - `_calc_recommended(current_stock, avg_daily, avg_std, lead_time, moq, service_level=0.95)` — the `lead_time` argument is now the **lead time in periods** (a float). The body is unchanged (`avg_daily * lead_time`, `math.sqrt(lead_time)`), which for `daily` (lead_time = `float(days)`) yields byte-identical output. Annotation widened `int` → `float`.
  - `get_inventory_status(tenant_id, session_id, service_level=0.95, period="daily")` and `_compute_inventory_status(..., period="daily")` — trailing keyword `period`. In the per-SKU loop `avg_daily`/`avg_std` are read over `_steps_for_lead_time(lead_time, period)` buckets (so they are per-period demand), `coverage` = `current_stock / avg_daily` (in periods), `signal = _calc_signal(coverage, _lead_time_in_periods(lead_time, period))`, and reorder/safety use the period lead time. The returned item dict's numeric fields keep their existing keys (`coverage_days`, `lead_time_demand`, `reorder_point`, …) — their VALUES are now in the active period's units; the key names are retained for backward-compat and daily byte-identity. Default `period="daily"` ⇒ every existing caller unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_multi_period_coverage.py
import pytest

from backend.db import session_store
from backend.inventory import service as svc
from backend.sessions.service import create_session


def _forecast(per_bucket_demand: float, spread: float, n: int = 30) -> dict:
    """One model, n buckets of constant demand. In a period-trained session each
    bucket is one PERIOD, so per_bucket_demand is units/period for that grain."""
    pts = [
        {
            "date": f"2026-01-{i + 1:02d}",
            "value": per_bucket_demand,
            "lower": max(0.0, per_bucket_demand - spread),
            "upper": per_bucket_demand + spread,
        }
        for i in range(n)
    ]
    return {"lightgbm": {"forecast": pts}}


def _put_stock(client, headers, sku, **fields):
    r = client.put(f"/api/v1/inventory/stock/{sku}", json=fields, headers=headers)
    assert r.status_code == 200, r.text


class TestWeeklyCoverage:
    def test_weekly_coverage_reads_in_weeks_and_signal_matches_hand_calc(
        self, client, auth_headers, test_tenant
    ):
        # Weekly session: 10 units/WEEK demand, 40 in stock, lead time 14 days.
        #   coverage = 40 / 10 = 4 weeks
        #   lead time in weeks = 14 / 7 = 2.0
        #   thresholds: PEDIR_YA < 1.0, PEDIR_PRONTO < 2.4, OK < 6.0
        #   4 weeks -> OK  (2.4 <= 4 < 6)
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "weekly-cov")["id"]
        sku = "WK_COV"
        _put_stock(client, auth_headers, sku, current_stock=40, lead_time_days=14, moq=1)
        session_store.set_forecasts(tid, sid, {sku: _forecast(10.0, 0.0)})

        items = svc.get_inventory_status(tid, sid, period="weekly")
        it = next(i for i in items if i["sku"] == sku)
        assert it["coverage_days"] == 4.0          # value is in WEEKS now
        assert it["daily_demand"] == 10.0          # per-period (weekly) demand
        assert it["signal"] == "OK"

    def test_switching_period_flips_the_same_sku_coverage(
        self, client, auth_headers, test_tenant
    ):
        # Same SKU/forecast, read at two periods -> different coverage numbers.
        # daily: 40 stock / 10 per-bucket = 4 (interpreted as 4 days)
        # weekly: 40 / 10 = 4 (interpreted as 4 weeks)  -> same NUMBER here,
        # so make demand differ per grain to prove the interpretation flips the
        # SIGNAL, which is the load-bearing behavior.
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "flip")["id"]
        sku = "FLIP"
        _put_stock(client, auth_headers, sku, current_stock=20, lead_time_days=14, moq=1)
        session_store.set_forecasts(tid, sid, {sku: _forecast(10.0, 0.0)})

        daily = next(i for i in svc.get_inventory_status(tid, sid, period="daily")
                     if i["sku"] == sku)
        weekly = next(i for i in svc.get_inventory_status(tid, sid, period="weekly")
                      if i["sku"] == sku)
        # daily: cov = 2 days, lead 14 days -> 2 < 7 (0.5*14) -> PEDIR_YA
        assert daily["signal"] == "PEDIR_YA"
        # weekly: cov = 2 weeks, lead 2 weeks -> 2.4>2>=1.0 -> PEDIR_PRONTO
        assert weekly["signal"] == "PEDIR_PRONTO"
        assert daily["coverage_days"] == 2.0 and weekly["coverage_days"] == 2.0


class TestDailyRegression:
    def test_period_daily_is_byte_identical_to_default(
        self, client, auth_headers, test_tenant
    ):
        """CRITICAL regression: period='daily' must reproduce today's output
        exactly. We assert the explicit call (period='daily') equals the frozen
        default call (no period) field-for-field for a mixed set of SKUs — if
        any Phase C conversion leaked into the daily path this diverges."""
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "daily-regression")["id"]
        _put_stock(client, auth_headers, "R_SHORT", current_stock=1, lead_time_days=10, moq=1)
        _put_stock(client, auth_headers, "R_OK", current_stock=130, lead_time_days=10, moq=1)
        _put_stock(client, auth_headers, "R_PILE", current_stock=9999, lead_time_days=10, moq=1)
        session_store.set_forecasts(tid, sid, {
            "R_SHORT": _forecast(100.0, 10.0, n=14),
            "R_OK":    _forecast(10.0, 6.0, n=14),
            "R_PILE":  _forecast(1.0, 0.1, n=14),
        })

        default_items = svc.get_inventory_status(tid, sid)               # frozen default
        daily_items = svc.get_inventory_status(tid, sid, period="daily")  # explicit
        assert default_items == daily_items

    def test_hand_computed_daily_values_unchanged(
        self, client, auth_headers, test_tenant
    ):
        """Pin the actual daily numbers so a future refactor can't silently
        change them: 10 units/day, 40 stock, lead 10 -> coverage 4 days ->
        4 < 5 (0.5*10) -> PEDIR_YA."""
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "daily-hand")["id"]
        _put_stock(client, auth_headers, "HAND", current_stock=40, lead_time_days=10, moq=1)
        session_store.set_forecasts(tid, sid, {"HAND": _forecast(10.0, 0.0, n=14)})
        it = next(i for i in svc.get_inventory_status(tid, sid) if i["sku"] == "HAND")
        assert it["coverage_days"] == 4.0
        assert it["daily_demand"] == 10.0
        assert it["signal"] == "PEDIR_YA"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_multi_period_coverage.py -q -k "WeeklyCoverage or DailyRegression"`
Expected: FAIL — `get_inventory_status` has no `period` kwarg (TypeError), and the weekly coverage is still computed in days.

- [ ] **Step 3: Implement — widen `_calc_recommended`**

In `backend/inventory/service.py`, change the signature annotation (body unchanged):

```python
def _calc_recommended(
    current_stock: float,
    avg_daily: float,
    avg_std: float,
    lead_time: float,   # lead time in PERIODS (Phase C); = day count when daily
    moq: float,
    service_level: float = 0.95,
) -> float:
```

- [ ] **Step 4: Implement — thread `period` through `get_inventory_status`**

Change the public wrapper and the compute signature:

```python
def get_inventory_status(
    tenant_id: str, session_id: str, service_level: float = 0.95, period: str = "daily",
) -> list[dict]:
    """... (existing docstring) ...

    `period` (multi-period Phase C): the active planning period the session was
    trained at ('daily'|'weekly'|'monthly'). Coverage and the signal are judged
    in that unit. Default 'daily' reproduces today's math byte-for-byte.
    """
    return _compute_inventory_status(tenant_id, session_id, service_level, period=period)
```

```python
def _compute_inventory_status(
    tenant_id: str, session_id: str, service_level: float = 0.95,
    *,
    period: str = "daily",
    forecasts: Optional[dict] = None,
    stock_rows: Optional[list] = None,
    learned_lead_times: Optional[dict] = None,
) -> list[dict]:
```

In the per-SKU `if has_forecast and has_stock:` block, replace the demand/coverage/recommend lines (current lines ~760-766 and ~771-773):

```python
            sku_service_level = float(stock.get("service_level") or service_level) if stock else service_level
            z = _Z.get(sku_service_level, 1.645)
            # Per-period demand: average over the lead time expressed in this
            # period's buckets (daily -> lead_time days, unchanged).
            steps = _steps_for_lead_time(lead_time, period)
            avg_daily, avg_std = _avg_daily_forecast(model_forecasts, steps)
            lead_time_periods = _lead_time_in_periods(lead_time, period)
            coverage_days = current_stock / avg_daily if avg_daily > 0 else 9999.0
            signal = _calc_signal(coverage_days, lead_time_periods)
            recommended = _calc_recommended(
                current_stock, avg_daily, avg_std, lead_time_periods, moq, sku_service_level
            )
            recommended = _gate_recommended_by_signal(signal, recommended)
            inventory_value = (
                round(current_stock * float(stock["unit_cost"]), 2)
                if stock.get("unit_cost") is not None else None
            )
            _demand_lt  = round(avg_daily * lead_time_periods, 2)
            _safety      = round(z * avg_std * math.sqrt(lead_time_periods), 2)
            _antes_moq   = round(max(0.0, _demand_lt + _safety - current_stock), 2)
```

Leave the `calc_explanation` dict, the `reorder_point = round(_demand_lt + _safety, 2)`, and `build_explanation(...)` call exactly as they are — they already read `_demand_lt` / `_safety` / `avg_daily` / `coverage_days`, which now carry period values. For `daily`: `steps = lead_time`, `lead_time_periods = float(lead_time)`, so every one of these lines produces the identical number it does today.

> Note: the `coverage_days` KEY name is retained deliberately (it is consumed by `/hoy`, the PDF, the alert loop, and the event simulator). Under Phase C its VALUE is "coverage in active-period units"; the endpoint (Task 4) publishes `period` + `coverage_unit` in the envelope so the UI labels it correctly. Renaming the key is out of scope (a breaking change across every consumer for no behavioral gain).

- [ ] **Step 5: Run new + regression**

Run: `cd backend && python -m pytest tests/test_multi_period_coverage.py -q`
Expected: all pass (helpers + weekly + daily regression).
Run: `cd backend && python -m pytest tests/test_inventory.py tests/test_calculation_audit.py tests/test_event_simulator.py -q`
Expected: all pass — these exercise the daily path and must be untouched (proof the default is byte-identical).

- [ ] **Step 6: Commit**

```bash
git add backend/inventory/service.py backend/tests/test_multi_period_coverage.py
git commit -m "feat(inventory): period-aware coverage and semaforo (daily unchanged)"
```

---

### Task 3: Period-aware per-warehouse status

**Files:**
- Modify: `backend/inventory/service.py` (`get_inventory_status_by_warehouse`)
- Test: `backend/tests/test_multi_period_coverage.py` (extend)

**Interfaces:**
- Consumes: Task 1 helpers.
- Changes: `get_inventory_status_by_warehouse(tenant_id, session_id, service_level=0.95, period="daily", *, forecasts=None, stock_rows=None, learned_lead_times=None)` — same trailing `period` keyword; the per-(sku, warehouse) row uses `_steps_for_lead_time` for demand, coverage-in-periods, `_calc_signal` against `_lead_time_in_periods`, and reorder at the period lead time. `_network_transfer_pass` is unchanged (it operates on already-computed `recommended_qty` / `coverage_days` / `daily_demand`, all now in the same period unit, so its relative comparisons stay correct — `TRANSFER_MIN_DONOR_COVERAGE_DAYS` is a coverage threshold that for `daily` is unchanged; see note). Default `period="daily"` ⇒ byte-identical.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_multi_period_coverage.py
from backend.inventory import warehouse_service as wh_svc


class TestByWarehousePeriod:
    def test_weekly_by_warehouse_coverage_in_weeks(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "wh-weekly")["id"]
        sku = "WHWK"
        # Single warehouse, share-mode demand: 10 units/week, 40 stock, lead 14d.
        _put_stock(client, auth_headers, sku, current_stock=40, lead_time_days=14,
                   moq=1, warehouse="principal")
        session_store.set_forecasts(tid, sid, {sku: _forecast(10.0, 0.0)})

        rows = svc.get_inventory_status_by_warehouse(tid, sid, period="weekly")
        row = next(r for r in rows if r["sku"] == sku)
        assert row["coverage_days"] == 4.0     # weeks
        assert row["signal"] == "OK"           # 4 weeks vs lead 2 weeks

    def test_by_warehouse_daily_default_unchanged(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "wh-daily")["id"]
        sku = "WHDL"
        _put_stock(client, auth_headers, sku, current_stock=40, lead_time_days=10,
                   moq=1, warehouse="principal")
        session_store.set_forecasts(tid, sid, {sku: _forecast(10.0, 0.0, n=14)})
        default = next(r for r in svc.get_inventory_status_by_warehouse(tid, sid)
                       if r["sku"] == sku)
        explicit = next(r for r in svc.get_inventory_status_by_warehouse(tid, sid, period="daily")
                        if r["sku"] == sku)
        assert default == explicit
        assert default["signal"] == "PEDIR_YA"   # 4 days vs lead 10 days
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_multi_period_coverage.py -q -k "ByWarehousePeriod"`
Expected: FAIL — no `period` kwarg / coverage still in days.

- [ ] **Step 3: Implement**

Add `period: str = "daily"` to the signature (as a keyword before the `*`, matching Task 2's shape):

```python
def get_inventory_status_by_warehouse(
    tenant_id: str, session_id: str, service_level: float = 0.95, period: str = "daily",
    *,
    forecasts: Optional[dict] = None,
    stock_rows: Optional[list] = None,
    learned_lead_times: Optional[dict] = None,
) -> list[dict]:
```

In the `if model_forecasts and share > 0.0:` block, replace the demand/coverage/recommend lines (current lines ~980-991):

```python
                z = _Z.get(sku_service_level, 1.645)
                steps = _steps_for_lead_time(lead_time, period)
                avg_daily, avg_std = _avg_daily_forecast(model_forecasts, steps)
                avg_daily *= share
                avg_std *= share
                lead_time_periods = _lead_time_in_periods(lead_time, period)
                coverage_days = current_stock / avg_daily if avg_daily > 0 else 9999.0
                signal = _calc_signal(coverage_days, lead_time_periods)
                recommended = _calc_recommended(
                    current_stock, avg_daily, avg_std, lead_time_periods, moq,
                    sku_service_level)
                recommended = _gate_recommended_by_signal(signal, recommended)
                reorder_point = round(
                    avg_daily * lead_time_periods
                    + z * avg_std * math.sqrt(lead_time_periods), 2)
```

> Note on `_network_transfer_pass`: it uses `TRANSFER_MIN_DONOR_COVERAGE_DAYS = 30.0` as a donor-coverage floor. Under a coarser period, `daily_demand` is per-period and `coverage_days` is in periods, so a 30-*day* floor compared against week/month coverage is looser than intended — but the transfer pass is an MW-5.4 concern and multi-warehouse + multi-period together are out of scope for Phase C v1 (spec "Out of scope": one active period at a time; the transfer pass is unchanged and correct for the daily path it ships on). Leave `_network_transfer_pass` untouched; note the interaction here so a future MW×period pass knows to periodize the donor floor.

- [ ] **Step 4: Run new + regression**

Run: `cd backend && python -m pytest tests/test_multi_period_coverage.py -q -k "ByWarehousePeriod"`
Expected: 2 passed.
Run: `cd backend && python -m pytest tests/test_status_by_warehouse.py tests/test_inventory_multi_bodega.py -q`
Expected: all pass (daily default unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/service.py backend/tests/test_multi_period_coverage.py
git commit -m "feat(inventory): period-aware per-warehouse status (daily unchanged)"
```

---

### Task 4: `/status` endpoint — resolve active period + default session + envelope fields

**Files:**
- Modify: `backend/api/v1/inventory.py` (`inventory_status`, the `/status` handler ~line 342)
- Test: `backend/tests/test_multi_period_coverage.py` (extend)

**Interfaces:**
- Consumes: `backend/sessions/planning_service.py` `get_planning`, `resolve_active_session` (Phase B); Task 2/3 `period` kwarg.
- Changes: `session_id` becomes optional (`Optional[str] = Query(default=None)`). When absent, resolve `planning_service.resolve_active_session(tenant_id)`; if that is `None` too, return `400` (no completed session yet). The active `period` comes from `planning_service.get_planning(tenant_id)["period"]` and is passed to `get_inventory_status` / `get_inventory_status_by_warehouse`. The response envelope gains `period` and `coverage_unit` (`"day"|"week"|"month"`) so the UI can label coverage; item dicts and `summary` are unchanged. An explicit `session_id` still works verbatim (`/skus` picking a specific session). Passing an explicit `period` query is NOT added — the period is a tenant-global setting, resolved server-side.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_multi_period_coverage.py
class TestStatusEndpointPeriod:
    def test_status_envelope_carries_active_period(
        self, client, auth_headers, test_tenant, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "ep-weekly")["id"]
        _put_stock(client, auth_headers, "EP", current_stock=40, lead_time_days=14, moq=1)
        session_store.set_forecasts(tid, sid, {"EP": _forecast(10.0, 0.0)})

        # Force the tenant's active planning to weekly (Phase B service).
        monkeypatch.setattr(
            inv_api.planning_service, "get_planning",
            lambda t: {"period": "weekly", "horizon": 4,
                       "available_periods": ["daily", "weekly"], "max_horizon": 26})

        r = client.get(f"/api/v1/inventory/status?session_id={sid}", headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["period"] == "weekly"
        assert data["coverage_unit"] == "week"
        it = next(i for i in data["items"] if i["sku"] == "EP")
        assert it["coverage_days"] == 4.0   # 4 weeks
        assert it["signal"] == "OK"

    def test_status_defaults_session_to_active_resolver(
        self, client, auth_headers, test_tenant, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "ep-default")["id"]
        _put_stock(client, auth_headers, "EPD", current_stock=1, lead_time_days=10, moq=1)
        session_store.set_forecasts(tid, sid, {"EPD": _forecast(100.0, 0.0, n=14)})
        monkeypatch.setattr(inv_api.planning_service, "resolve_active_session",
                            lambda t: sid)
        monkeypatch.setattr(inv_api.planning_service, "get_planning",
                            lambda t: {"period": "daily", "horizon": 14,
                                       "available_periods": ["daily"], "max_horizon": 90})
        # No session_id given -> resolver supplies it.
        r = client.get("/api/v1/inventory/status", headers=auth_headers)
        assert r.status_code == 200, r.text
        skus = {i["sku"] for i in r.json()["data"]["items"]}
        assert "EPD" in skus

    def test_status_no_session_and_no_active_returns_400(
        self, client, auth_headers, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        monkeypatch.setattr(inv_api.planning_service, "resolve_active_session",
                            lambda t: None)
        r = client.get("/api/v1/inventory/status", headers=auth_headers)
        assert r.status_code == 400
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_multi_period_coverage.py -q -k "StatusEndpointPeriod"`
Expected: FAIL — `session_id` required (422 not 200/400), envelope has no `period`, `planning_service` not imported into `inventory.py`.

- [ ] **Step 3: Implement**

At the top of `backend/api/v1/inventory.py`, add the import next to the other `backend.sessions` imports:

```python
from backend.sessions import planning_service
```

Replace the `/status` handler signature + head (current lines ~342-365):

```python
_COVERAGE_UNIT = {"daily": "day", "weekly": "week", "monthly": "month"}


@router.get("/status")
def inventory_status(
    session_id: Optional[str] = Query(
        default=None,
        description="Completed forecast session; defaults to the tenant's active-period session"),
    service_level: float = Query(default=0.95, ge=0.5, le=0.999),
    signal: Optional[str] = Query(default=None, description="Filter by signal: PEDIR_YA, PEDIR_PRONTO, OK, SOBRESTOCK, SIN_DATOS"),
    supplier: Optional[str] = Query(default=None),
    by_warehouse: bool = Query(default=False, description="Per-(sku, warehouse) rows with network transfer suggestions"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Returns per-SKU inventory status in the tenant's ACTIVE planning period:
    - coverage (in the active period's units — see `coverage_unit`)
    - traffic-light signal (PEDIR_YA / PEDIR_PRONTO / OK / SOBRESTOCK / SIN_DATOS)
    - recommended order quantity
    - inventory value
    """
    if not session_id:
        session_id = planning_service.resolve_active_session(user.tenant_id)
        if not session_id:
            raise HTTPException(status_code=400, detail="No completed session for this tenant yet")

    period = planning_service.get_planning(user.tenant_id).get("period", "daily")

    if by_warehouse:
        items = svc.get_inventory_status_by_warehouse(user.tenant_id, session_id, service_level, period)
    else:
        items = svc.get_inventory_status(user.tenant_id, session_id, service_level, period)
        items = _strip_abc_xyz_unless_entitled(items, user.tenant_id)
```

Then add `period` + `coverage_unit` to BOTH return envelopes (the `by_warehouse` branch's `ok({...})` and the final `ok({...})`):

```python
    if by_warehouse:
        return ok({
            "period": period,
            "coverage_unit": _COVERAGE_UNIT.get(period, "day"),
            "items": items,
            "summary": { ... unchanged ... },
        })
    ...
    return ok({
        "period": period,
        "coverage_unit": _COVERAGE_UNIT.get(period, "day"),
        "items": items,
        "excluded_skus": svc.get_excluded_skus(user.tenant_id, session_id),
        "summary": { ... unchanged ... },
    })
```

`HTTPException` and `Optional` are already imported in this module.

- [ ] **Step 4: Run new + regression**

Run: `cd backend && python -m pytest tests/test_multi_period_coverage.py -q -k "StatusEndpointPeriod"`
Expected: 3 passed.
Run: `cd backend && python -m pytest tests/test_inventory.py -q`
Expected: all pass. **Watch `test_status_missing_session_id_returns_422`** — it asserts a missing `session_id` returns 422. Phase C makes `session_id` optional, so a missing id now resolves the active session (or 400). Update that test to reflect the new contract: with no active session it is 400, not 422. Change the assertion to `== 400` and (if the fixture has no family) monkeypatch `planning_service.resolve_active_session` to return `None`, mirroring `test_status_no_session_and_no_active_returns_400`. This is a deliberate contract change, not a regression — note it in the commit body.

- [ ] **Step 5: Commit**

```bash
git add backend/api/v1/inventory.py backend/tests/test_multi_period_coverage.py backend/tests/test_inventory.py
git commit -m "feat(inventory): /status resolves active period and defaults the session"
```

---

### Task 5: Optimizer horizon windowing (raise the day cap + convert)

**Files:**
- Modify: `backend/api/v1/inventory.py` (`optimize_inventory`, ~line 1877)
- Modify: `backend/inventory/optimizer_service.py` (`build_optimization_input` — periodize lead time)
- Test: `backend/tests/test_multi_period_coverage.py` (extend), `backend/tests/test_optimizer_endpoint.py` (regression)

**Decision (spec left "raise the cap or clamp" to the plan):** **Raise the cap.** The `/optimize` endpoint keeps its `horizon_days` query but its ceiling goes from `le=30` to `le=360`, so a monthly horizon (12 × 30 = 360 days) fits. The endpoint resolves the active `(period, horizon)` from `planning_service` and, when the caller does not pin `horizon_days` explicitly, computes `horizon_days = horizon × _days_per_period(period)` — the literal spec conversion. `build_optimization_input` gains a `period` argument that converts each SKU's `lead_time_days` into buckets consistent with the horizon window. No re-forecast: `_avg_forecast_curve(max_steps=horizon_days)` naturally truncates to the pre-computed reach (any window beyond the reach is left as trailing-zero demand — the "beyond reach" case is out of scope for v1 per the spec, and the stepper is capped at the reach in Phase B so it is unreachable from the UI). Clamping to 30 was rejected: it would silently cut a legitimate monthly plan down to one day.

**Interfaces:**
- `optimize_inventory(session_id=None, horizon_days=Query(default=None, ge=1, le=360), ...)` — `session_id` optional (defaults to `resolve_active_session`), `horizon_days` optional (defaults to `horizon × days_per_period`).
- `build_optimization_input(tenant_id, session_id, horizon_days=14, stock_rows=None, period="daily")` — `period` converts `lead_time_buckets[sku] = max(1, ceil(lead_time_days / _days_per_period(period)))`. Default `period="daily"` ⇒ `lead_time_buckets = int(lead_time_days)`, byte-identical to today.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_multi_period_coverage.py
class TestOptimizerHorizonConversion:
    def test_endpoint_converts_active_horizon_to_days(
        self, client, auth_headers, test_tenant, test_session, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        captured = {}

        def _fake_build(tenant_id, session_id, horizon_days, stock_rows=None, period="daily"):
            captured["horizon_days"] = horizon_days
            captured["period"] = period
            return None  # short-circuits to the empty-optimal response

        monkeypatch.setattr(inv_api.opt_svc, "build_optimization_input", _fake_build)
        monkeypatch.setattr(inv_api.planning_service, "get_planning",
                            lambda t: {"period": "monthly", "horizon": 4,
                                       "available_periods": ["daily", "monthly"],
                                       "max_horizon": 12})
        # No horizon_days given -> derived from planning: 4 months * 30 = 120 days.
        r = client.get(f"/api/v1/inventory/optimize?session_id={test_session['id']}",
                       headers=auth_headers)
        assert r.status_code == 200, r.text
        assert captured["horizon_days"] == 120
        assert captured["period"] == "monthly"

    def test_endpoint_accepts_horizon_beyond_old_cap(
        self, client, auth_headers, test_session, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        monkeypatch.setattr(inv_api.planning_service, "get_planning",
                            lambda t: {"period": "monthly", "horizon": 12,
                                       "available_periods": ["monthly"], "max_horizon": 12})
        # 12*30 = 360 was a 422 under le=30; must be accepted now.
        r = client.get(f"/api/v1/inventory/optimize?session_id={test_session['id']}",
                       headers=auth_headers)
        assert r.status_code == 200, r.text


class TestOptimizerLeadTimeBuckets:
    def test_lead_time_periodized(self, client, auth_headers, test_tenant):
        from backend.inventory import optimizer_service as opt
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "opt-lead")["id"]
        _put_stock(client, auth_headers, "OPTL", current_stock=5, lead_time_days=30,
                   moq=1, warehouse="principal")
        session_store.set_forecasts(tid, sid, {"OPTL": _forecast(10.0, 0.0)})
        # monthly: lead_time_buckets = ceil(30/30) = 1
        inp = opt.build_optimization_input(tid, sid, horizon_days=4, period="monthly")
        assert inp is not None
        assert inp.lead_time_buckets["OPTL"] == 1
        # daily default: lead_time_buckets = 30 (unchanged)
        inp_d = opt.build_optimization_input(tid, sid, horizon_days=30)
        assert inp_d.lead_time_buckets["OPTL"] == 30
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_multi_period_coverage.py -q -k "OptimizerHorizonConversion or OptimizerLeadTimeBuckets"`
Expected: FAIL — `horizon_days` still capped at 30 (422 for 360), no `period` conversion, `planning_service` not consulted.

- [ ] **Step 3: Implement — `build_optimization_input`**

In `backend/inventory/optimizer_service.py`, add the `period` parameter and periodize the lead time. Add near the top:

```python
from backend.inventory.service import _days_per_period
import math as _math
```

Change the signature:

```python
def build_optimization_input(
    tenant_id: str,
    session_id: str,
    horizon_days: int = 14,
    stock_rows: Optional[list[dict]] = None,
    period: str = "daily",
) -> Optional[OptimizationInput]:
```

Replace the `lead_time_buckets[sku] = ...` line (current line ~116):

```python
        lead_times = [int(row["lead_time_days"]) for row in sku_rows.values() if row.get("lead_time_days") is not None]
        raw_lead = max(lead_times) if lead_times else _DEFAULT_LEAD_TIME_DAYS
        # Lead time in the horizon's own buckets: for daily this is the day
        # count (unchanged); for weekly/monthly it is the lead time rounded up
        # to whole periods, so it stays commensurable with horizon_days (which
        # is already in that period's buckets when a coarser period is active).
        lead_time_buckets[sku] = max(1, _math.ceil(raw_lead / _days_per_period(period)))
```

- [ ] **Step 4: Implement — `/optimize` endpoint**

In `backend/api/v1/inventory.py`, change the `optimize_inventory` signature and resolution:

```python
@router.get("/optimize", dependencies=[Depends(require_feature(Feature.MILP_OPTIMIZER))])
def optimize_inventory(
    session_id:   Optional[str] = Query(default=None),
    # Cap raised from 30 to 360 (multi-period Phase C): a monthly horizon of 12
    # buckets is 12*30 = 360 days. When omitted, horizon_days is derived from
    # the tenant's active (period, horizon): horizon * days_per_period.
    horizon_days: Optional[int] = Query(default=None, ge=1, le=360),
    user: CurrentUser = Depends(get_current_user),
):
    from forecasting_core.business.optimizer import optimize

    plan = planning_service.get_planning(user.tenant_id)
    period = plan.get("period", "daily")
    if not session_id:
        session_id = planning_service.resolve_active_session(user.tenant_id)
        if not session_id:
            raise HTTPException(status_code=400, detail="No completed session for this tenant yet")
    if horizon_days is None:
        horizon_days = int(plan.get("horizon", 14)) * svc._days_per_period(period)
        horizon_days = max(1, min(horizon_days, 360))
```

Thread `period` into the build call (current line ~1904):

```python
        inp = opt_svc.build_optimization_input(
            user.tenant_id, session_id, horizon_days, stock_rows=stock_rows, period=period,
        )
```

Leave the rest of the handler untouched.

- [ ] **Step 5: Run new + regression**

Run: `cd backend && python -m pytest tests/test_multi_period_coverage.py -q -k "Optimizer"`
Expected: 3 passed.
Run: `cd backend && python -m pytest tests/test_optimizer_endpoint.py tests/test_optimizer_service.py tests/test_permission_audit.py -q`
Expected: all pass. The existing endpoint tests pass an explicit `horizon_days=7` (still valid, ≤ 360) — the viewer-can-read and 503 paths are unchanged; the permission audit's viewer/analyst pair on `/optimize` is preserved.

- [ ] **Step 6: Commit**

```bash
git add backend/api/v1/inventory.py backend/inventory/optimizer_service.py backend/tests/test_multi_period_coverage.py
git commit -m "feat(optimizer): window horizon by active period; raise day cap to 360"
```

---

### Task 6: Frontend — coverage unit labels + planning-driven session/period

**Files:**
- Modify: `Frontend/src/i18n/translations.ts` (new `es` + `en` keys)
- Modify: `Frontend/src/app/inventory/page.tsx` (label coverage in the active period unit)
- Modify: `Frontend/src/app/hoy/page.tsx` (coverage reason unit follows the period)
- Modify: `Frontend/src/components/inventory/WarehouseStatusTable.tsx` (coverage column unit)
- Modify: `Frontend/src/app/skus/page.tsx` (chart granularity chip defaults to the global period)
- Modify (if needed): `Frontend/src/lib/types.ts` (`InventoryStatusResponse` gains `period`, `coverage_unit`)

**Interfaces:**
- Consumes: the `/status` envelope `period` + `coverage_unit` (Task 4); Phase B's `usePlanning()` (or equivalent context) returning `{period, horizon}`.
- Produces: a pure helper `coverageUnitLabel(unit: 'day'|'week'|'month', n: number, t): string` returning día/días, semana/semanas, mes/meses (via i18n, singular when `n === 1`). Coverage numbers across `/inventory`, `/hoy` and the warehouse table are rendered with this label instead of the hard-coded "días". The `/skus` chip's initial `granularity` seeds from the global period, keeping the existing local-override behavior.

- [ ] **Step 1: Add i18n keys (es + en)**

In `Frontend/src/i18n/translations.ts`, add to the `es` block:

```ts
    'period.day_singular':   'día',
    'period.day_plural':     'días',
    'period.week_singular':  'semana',
    'period.week_plural':    'semanas',
    'period.month_singular': 'mes',
    'period.month_plural':   'meses',
    'period.coverage_prefix': 'Cobertura',
```

and the matching `en` block:

```ts
    'period.day_singular':   'day',
    'period.day_plural':     'days',
    'period.week_singular':  'week',
    'period.week_plural':    'weeks',
    'period.month_singular': 'month',
    'period.month_plural':   'months',
    'period.coverage_prefix': 'Coverage',
```

- [ ] **Step 2: Add the pure label helper + type**

In `Frontend/src/lib/types.ts`, extend the inventory status response type (find the interface backing `/status`'s `data`) with:

```ts
  period?: 'daily' | 'weekly' | 'monthly'
  coverage_unit?: 'day' | 'week' | 'month'
```

Add a small helper (e.g. in `Frontend/src/lib/format.ts` if it exists, else co-locate in `inventory/page.tsx`):

```ts
export function coverageUnitLabel(
  unit: 'day' | 'week' | 'month',
  n: number,
  t: (k: string) => string,
): string {
  const key = n === 1 ? `period.${unit}_singular` : `period.${unit}_plural`
  return t(key)
}
```

- [ ] **Step 3: Use it where coverage is rendered**

Replace the hard-coded `t('hoy.reason_days_coverage')` / "días" coverage renders with `coverageUnitLabel(coverage_unit ?? 'day', n, t)`, reading `coverage_unit` from the `/status` response (default `'day'` when absent so pre-Phase-B/daily tenants read exactly as today). In `/skus`, seed the chip: `const [granularity, setGranularity] = useState<string | null>(planningPeriod ?? null)` where `planningPeriod` comes from `usePlanning()`, and keep the existing `setGranularity` override wiring untouched.

- [ ] **Step 4: Typecheck**

Run: `cd Frontend && node ./node_modules/typescript/bin/tsc --noEmit`
Expected: no errors. (There is no frontend unit-test runner in this repo; `tsc --noEmit` is the gate, per CLAUDE.md.) Manually confirm in the running dev app that a weekly tenant shows "4 semanas" where a daily tenant shows "4 días", and that `/skus` opens on the global period but still lets the user switch the chip locally.

- [ ] **Step 5: Commit**

```bash
git add Frontend/src/i18n/translations.ts Frontend/src/lib/types.ts Frontend/src/app/inventory/page.tsx Frontend/src/app/hoy/page.tsx Frontend/src/components/inventory/WarehouseStatusTable.tsx Frontend/src/app/skus/page.tsx
git commit -m "feat(frontend): label coverage in the active period unit"
```

---

### Task 7: Full regression + plan doc note

**Files:**
- Modify: `docs/plan_general_faro_2026-07-18.md` (extend the multi-period note)

- [ ] **Step 1: Full backend suite**

Run: `cd backend && python -m pytest tests/ -q`
Expected: 0 failures beyond the known machine-load-flaky `test_stress.py::test_login_responds_under_2s` (re-run it alone to confirm). Watch specifically for: any test that hit `/status` or `/optimize` with no `session_id` expecting 422 (now 400 / resolver) — update to the Phase C contract; and any test asserting the daily semáforo numbers, which must be unchanged (proof the default path is byte-identical).

- [ ] **Step 2: Frontend typecheck**

Run: `cd Frontend && node ./node_modules/typescript/bin/tsc --noEmit`
Expected: clean.

- [ ] **Step 3: Note it in the plan doc**

Append under the 2026-07-23 multi-period area of `docs/plan_general_faro_2026-07-18.md`:
`Multi-period planning Phase C: coverage and the semáforo are now computed in the tenant's active planning period (daily/weekly/monthly) — coverage reads in that unit, the signal is judged against the lead time in the same unit, and reorder/safety recompute at the period's demand; period=daily is byte-identical to before. The forecast/optimizer horizon is windowed to the active horizon (optimizer day cap raised to 360), and /status + /optimize default the session to the active-period session (planning_service.resolve_active_session). Feature complete across Phases A–C — spec 2026-07-23-multi-period-planning-design.md.`

- [ ] **Step 4: Commit**

```bash
git add docs/plan_general_faro_2026-07-18.md
git commit -m "docs: note multi-period planning Phase C landed"
```

## Out of scope (this phase)

- Re-forecasting beyond the pre-computed reach on demand (the Phase B stepper is capped at the reach; a window beyond it is left as trailing-zero demand, never a re-train).
- Per-grain optimizer bucketing math (holding/stockout cost per period): the optimizer runs its day-windowed solve with a periodized lead time; a full per-bucket cost re-derivation per grain is a later refinement.
- Multi-warehouse × multi-period together: `_network_transfer_pass`'s 30-day donor-coverage floor is not periodized (noted in Task 3). One active period at a time (spec).
- Renaming the `coverage_days` item key to something period-neutral — a breaking change across every consumer for no behavioral gain; the envelope's `period`/`coverage_unit` carry the unit instead.
