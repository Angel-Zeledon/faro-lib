# ROI Monthly Evolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve `/inventory/roi` from a flat cumulative summary into a month-by-month view showing riesgos de quiebre atendidos, capital liberado de sobrestock, and % de adopción — per `docs/superpowers/specs/2026-07-11-roi-monthly-evolution-design.md`.

**Architecture:** A new monthly-cadence worker loop snapshots the tenant's total SOBRESTOCK value once a month (there is no historical cost/signal data to backfill, so this metric only exists going forward). A new aggregation function groups the existing `inventory_po_log` table by calendar month and joins it against these snapshots to compute month-over-month capital freed. One new read-only endpoint exposes it; the frontend replaces the existing "this month vs last month" tile with a 6-month table.

**Tech Stack:** FastAPI + psycopg2 (raw SQL, no ORM) on the backend; Next.js 14 / React / TypeScript on the frontend. Backend tests run against a real local Postgres (docker `faro_db`, port 5544) per `TESTING_GUIDELINES.md` — no mocking of DB state, only of upstream business logic that would otherwise require running a full forecasting session.

## Global Constraints

- No pandas/ML logic in `backend/`; no business logic in `Frontend/` (`CLAUDE.md`).
- Every mutating endpoint needs a permission pair (viewer denied / analyst success) — not applicable here, this feature adds only read endpoints, but per `TESTING_GUIDELINES.md` reads still get a viewer-success + unauthenticated-401 pair.
- Tests assert **state changes via direct DB queries**, never just HTTP status codes.
- Migrations are idempotent `CREATE TABLE IF NOT EXISTS` / `ALTER ... ADD COLUMN IF NOT EXISTS`, appended to `_MIGRATIONS` in `backend/db/migrations.py` — never edit `_BASE_SCHEMA`.
- Frontend has no unit test runner; its verification step is `cd Frontend && npx tsc --noEmit` (per `CLAUDE.md`). Do not run `npm run build` while `next dev` is running.
- "Capital liberado de sobrestock" only exists from the first snapshot forward — no backfill for months before this feature ships. Rows without two consecutive snapshots show `capital_liberado: null`.

---

### Task 1: Backend — overstock value snapshot (migration + service function)

**Files:**
- Modify: `backend/db/migrations.py:451` (insert new migration entries just before the closing `]` of `_MIGRATIONS`, i.e. before line `451	]`)
- Modify: `backend/inventory/service.py` (append at end of file, after line 1241 — after `run_daily_inventory_alerts`)
- Test: `backend/tests/test_roi_monthly.py` (new file)

**Interfaces:**
- Consumes: `get_tenants_with_active_sessions()` (`backend/inventory/service.py:1148`, returns `list[dict]` with key `tenant_id`), `get_latest_completed_session(tenant_id: str) -> Optional[dict]` (`backend/inventory/service.py:1160`, returns `{"session_id": str}` or `None`), `get_inventory_status(tenant_id, session_id) -> list[dict]` (`backend/inventory/service.py:308`, each item has `signal: str` and `valor_inventario: float | None`), `execute(sql, params)` / `query(sql, params)` / `query_one(sql, params)` from `backend.db.connection` (already imported at `backend/inventory/service.py:13`).
- Produces: `_sum_overstock_value(items: list[dict]) -> float` and `run_monthly_overstock_snapshot() -> None` in `backend.inventory.service` — consumed by Task 2 (worker loop) and indirectly by Task 3 (reads the table this writes).

- [ ] **Step 1: Add the migration**

In `backend/db/migrations.py`, insert immediately before the closing `]` of `_MIGRATIONS` (currently line 451, right after the `create_supplier_lead_time_obs_idx` entry):

```python
    # ── ROI monthly evolution (feature 1.5): capital freed from overstock ────
    # One row per tenant per month, taken by a scheduled job on the 1st.
    # No historical backfill — the metric only exists from here forward.
    ("create_inventory_overstock_snapshots",
     """CREATE TABLE IF NOT EXISTS inventory_overstock_snapshots (
         id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id       TEXT NOT NULL,
         session_id      TEXT NOT NULL,
         overstock_value FLOAT NOT NULL,
         recorded_at     TIMESTAMPTZ DEFAULT NOW()
     )"""),
    ("create_inventory_overstock_snapshots_idx",
     "CREATE INDEX IF NOT EXISTS overstock_snapshots_tenant_idx ON inventory_overstock_snapshots (tenant_id, recorded_at DESC)"),
```

This table is created automatically the next time the app starts (`backend/main.py` runs `run_all()` on lifespan startup), including when the test `client` fixture boots the app — no manual migration run needed for tests.

- [ ] **Step 2: Write the failing tests for `_sum_overstock_value`**

Create `backend/tests/test_roi_monthly.py`:

```python
"""
Tests for ROI monthly evolution (feature 1.5): overstock capital-freed
snapshots and the monthly summary aggregation.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.db.connection import execute, query_one


class TestSumOverstockValue:
    def test_only_counts_sobrestock_items(self):
        from backend.inventory.service import _sum_overstock_value

        items = [
            {"signal": "SOBRESTOCK", "valor_inventario": 1000.0},
            {"signal": "OK", "valor_inventario": 500.0},
            {"signal": "SOBRESTOCK", "valor_inventario": 250.0},
            {"signal": "PEDIR_YA", "valor_inventario": None},
        ]
        assert _sum_overstock_value(items) == 1250.0

    def test_empty_items_returns_zero(self):
        from backend.inventory.service import _sum_overstock_value
        assert _sum_overstock_value([]) == 0.0

    def test_missing_valor_inventario_treated_as_zero(self):
        from backend.inventory.service import _sum_overstock_value
        assert _sum_overstock_value([{"signal": "SOBRESTOCK"}]) == 0.0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_roi_monthly.py -v`
Expected: FAIL with `ImportError` / `AttributeError: module 'backend.inventory.service' has no attribute '_sum_overstock_value'`

- [ ] **Step 4: Implement `_sum_overstock_value`**

Append to `backend/inventory/service.py` (after line 1241, end of file):

```python


def _sum_overstock_value(items: list[dict]) -> float:
    """Total valor_inventario of SKUs currently flagged SOBRESTOCK."""
    return sum(
        (i.get("valor_inventario") or 0)
        for i in items
        if i.get("signal") == "SOBRESTOCK"
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_roi_monthly.py -v`
Expected: 3 passed

- [ ] **Step 6: Write the failing tests for `run_monthly_overstock_snapshot`**

Append to `backend/tests/test_roi_monthly.py`:

```python


class TestRunMonthlyOverstockSnapshot:
    def test_inserts_snapshot_row_per_tenant(self, monkeypatch, test_tenant):
        from backend.inventory import service

        tid = test_tenant["id"]
        sess_id = f"sess_{tid[:8]}"

        monkeypatch.setattr(
            service, "get_tenants_with_active_sessions",
            lambda: [{"tenant_id": tid}],
        )
        monkeypatch.setattr(
            service, "get_latest_completed_session",
            lambda t: {"session_id": sess_id} if t == tid else None,
        )
        monkeypatch.setattr(
            service, "get_inventory_status",
            lambda t, s: [
                {"sku": "OS-1", "signal": "SOBRESTOCK", "valor_inventario": 3000.0},
                {"sku": "OS-2", "signal": "SOBRESTOCK", "valor_inventario": 1500.0},
                {"sku": "OK-1", "signal": "OK", "valor_inventario": 999.0},
            ],
        )

        service.run_monthly_overstock_snapshot()

        row = query_one(
            """SELECT overstock_value, session_id FROM inventory_overstock_snapshots
               WHERE tenant_id = %s ORDER BY recorded_at DESC LIMIT 1""",
            (tid,),
        )
        assert row is not None
        assert float(row["overstock_value"]) == 4500.0
        assert row["session_id"] == sess_id

    def test_skips_tenant_without_completed_session(self, monkeypatch, test_tenant):
        from backend.inventory import service

        tid = test_tenant["id"]
        monkeypatch.setattr(
            service, "get_tenants_with_active_sessions",
            lambda: [{"tenant_id": tid}],
        )
        monkeypatch.setattr(service, "get_latest_completed_session", lambda t: None)

        service.run_monthly_overstock_snapshot()

        row = query_one(
            "SELECT id FROM inventory_overstock_snapshots WHERE tenant_id = %s",
            (tid,),
        )
        assert row is None
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_roi_monthly.py -v`
Expected: the two new tests FAIL with `AttributeError: module 'backend.inventory.service' has no attribute 'run_monthly_overstock_snapshot'`

- [ ] **Step 8: Implement `run_monthly_overstock_snapshot`**

Append to `backend/inventory/service.py` (after `_sum_overstock_value`):

```python


def run_monthly_overstock_snapshot() -> None:
    """
    Called once a month by the scheduler (day 1). For each tenant with a
    completed session, records the current total SOBRESTOCK value so the
    ROI monthly view can compute capital freed month over month.
    """
    tenants = get_tenants_with_active_sessions()
    log.info("overstock_snapshot: checking %d tenants", len(tenants))

    for tenant in tenants:
        tid = tenant["tenant_id"]
        try:
            session = get_latest_completed_session(tid)
            if not session:
                continue

            items = get_inventory_status(tid, session["session_id"])
            overstock_value = _sum_overstock_value(items)

            execute(
                """INSERT INTO inventory_overstock_snapshots
                       (tenant_id, session_id, overstock_value)
                   VALUES (%s, %s, %s)""",
                (tid, session["session_id"], overstock_value),
            )
        except Exception as e:
            log.error("overstock_snapshot: tenant=%s error=%s", tid, e)
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_roi_monthly.py -v`
Expected: 5 passed

- [ ] **Step 10: Commit**

```bash
git add backend/db/migrations.py backend/inventory/service.py backend/tests/test_roi_monthly.py
git commit -m "feat(inventory): monthly overstock value snapshot for ROI capital-freed metric"
```

---

### Task 2: Backend — monthly scheduler loop

**Files:**
- Modify: `backend/workers/worker.py`
- Test: `backend/tests/test_worker_scheduling.py` (new file)

**Interfaces:**
- Consumes: `run_monthly_overstock_snapshot()` from Task 1 (`backend.inventory.service`).
- Produces: `_next_month_start(now: datetime) -> datetime` (pure, testable) and `_monthly_overstock_snapshot_loop()` registered in `start()` — nothing downstream depends on these directly; this task is self-contained scheduling infra.

- [ ] **Step 1: Write the failing test for `_next_month_start`**

Create `backend/tests/test_worker_scheduling.py`:

```python
"""
Pure-logic tests for worker scheduling helpers (feature 1.5: monthly
overstock snapshot). No DB access — offline.
"""

from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.offline


def test_next_month_start_mid_month_rolls_to_first_of_next_month():
    from backend.workers.worker import _next_month_start

    now = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)
    result = _next_month_start(now)
    assert result == datetime(2026, 8, 1, 0, 5, tzinfo=timezone.utc)


def test_next_month_start_before_trigger_time_on_the_1st_stays_same_day():
    from backend.workers.worker import _next_month_start

    now = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    result = _next_month_start(now)
    assert result == datetime(2026, 7, 1, 0, 5, tzinfo=timezone.utc)


def test_next_month_start_after_trigger_time_on_the_1st_rolls_forward():
    from backend.workers.worker import _next_month_start

    now = datetime(2026, 7, 1, 0, 5, tzinfo=timezone.utc)
    result = _next_month_start(now)
    assert result == datetime(2026, 8, 1, 0, 5, tzinfo=timezone.utc)


def test_next_month_start_rolls_over_year_boundary():
    from backend.workers.worker import _next_month_start

    now = datetime(2026, 12, 20, 9, 0, tzinfo=timezone.utc)
    result = _next_month_start(now)
    assert result == datetime(2027, 1, 1, 0, 5, tzinfo=timezone.utc)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_worker_scheduling.py -v`
Expected: FAIL with `ImportError: cannot import name '_next_month_start'`

- [ ] **Step 3: Implement `_next_month_start` and the loop**

In `backend/workers/worker.py`, add after `_inventory_alert_loop` (after line 119, before `def start()`):

```python
def _next_month_start(now: datetime) -> datetime:
    """Returns the next day-1 00:05 UTC boundary strictly after `now`."""
    candidate = now.replace(day=1, hour=0, minute=5, second=0, microsecond=0)
    if candidate <= now:
        if candidate.month == 12:
            candidate = candidate.replace(year=candidate.year + 1, month=1)
        else:
            candidate = candidate.replace(month=candidate.month + 1)
    return candidate


def _monthly_overstock_snapshot_loop() -> None:
    """Snapshots each tenant's SOBRESTOCK value on the 1st of every month."""
    log.info("Monthly overstock snapshot scheduler started")
    while True:
        try:
            now = datetime.now(timezone.utc)
            next_run = _next_month_start(now)
            sleep_secs = (next_run - now).total_seconds()
            log.info("Overstock snapshot: next run at %s UTC (%.0f s)", next_run.isoformat(), sleep_secs)
            time.sleep(max(sleep_secs, 1))
        except Exception:
            time.sleep(3600)
            continue
        try:
            from backend.inventory.service import run_monthly_overstock_snapshot
            run_monthly_overstock_snapshot()
        except Exception as e:
            log.error("Overstock snapshot error: %s", e, exc_info=True)
```

Then modify `start()` (currently lines 122–141) to register the new thread — add before the `return worker_thread` line:

```python
    overstock_thread = threading.Thread(
        target=_monthly_overstock_snapshot_loop, daemon=True, name="overstock-snapshot",
    )
    overstock_thread.start()

```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_worker_scheduling.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/workers/worker.py backend/tests/test_worker_scheduling.py
git commit -m "feat(inventory): schedule monthly overstock snapshot job"
```

---

### Task 3: Backend — monthly summary aggregation

**Files:**
- Modify: `backend/inventory/roi_service.py`
- Test: `backend/tests/test_roi_monthly.py` (append)

**Interfaces:**
- Consumes: `query(sql, params) -> list[dict]` from `backend.db.connection` (already imported at `backend/inventory/roi_service.py:13`); reads `inventory_po_log` (existing columns: `generated_at`, `skus_pedir_ya`, `total_value`, `suggested_count`, `approved_count`) and `inventory_overstock_snapshots` (from Task 1: `tenant_id`, `overstock_value`, `recorded_at`).
- Produces: `get_monthly_summary(tenant_id: str, months: int = 6) -> list[dict]`, each row `{"month": str "YYYY-MM", "pos_count": int, "skus_pedir_ya": int, "total_value": float, "adoption_rate": float | None, "capital_liberado": float | None}`, most recent month first. Consumed by Task 4 (API endpoint).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_roi_monthly.py`:

```python


class TestGetMonthlySummary:
    def test_aggregates_by_calendar_month_and_computes_capital_liberado(self, test_tenant):
        from backend.inventory.roi_service import get_monthly_summary

        tid = test_tenant["id"]
        now = datetime.now(tz=timezone.utc)
        this_month = now.replace(day=1, hour=12, minute=0, second=0, microsecond=0)
        last_month = (this_month - timedelta(days=1)).replace(
            day=1, hour=12, minute=0, second=0, microsecond=0
        )

        # Last month: 2 orders. This month: 1 order.
        execute(
            """INSERT INTO inventory_po_log
                   (tenant_id, session_id, generated_at, sku_count, total_units, total_value,
                    skus_pedir_ya, skus_pedir_pronto, suggested_count, approved_count)
               VALUES (%s, 's1', %s, 2, 20, 500, 1, 0, 2, 2)""",
            (tid, last_month),
        )
        execute(
            """INSERT INTO inventory_po_log
                   (tenant_id, session_id, generated_at, sku_count, total_units, total_value,
                    skus_pedir_ya, skus_pedir_pronto, suggested_count, approved_count)
               VALUES (%s, 's1', %s, 1, 10, 300, 2, 0, 4, 3)""",
            (tid, last_month),
        )
        execute(
            """INSERT INTO inventory_po_log
                   (tenant_id, session_id, generated_at, sku_count, total_units, total_value,
                    skus_pedir_ya, skus_pedir_pronto, suggested_count, approved_count)
               VALUES (%s, 's1', %s, 1, 5, 150, 1, 1, 2, 1)""",
            (tid, this_month),
        )

        # Overstock snapshots: last month 10000, this month 6000 -> 4000 freed.
        execute(
            """INSERT INTO inventory_overstock_snapshots
                   (tenant_id, session_id, overstock_value, recorded_at)
               VALUES (%s, 's1', 10000, %s)""",
            (tid, last_month),
        )
        execute(
            """INSERT INTO inventory_overstock_snapshots
                   (tenant_id, session_id, overstock_value, recorded_at)
               VALUES (%s, 's1', 6000, %s)""",
            (tid, this_month),
        )

        rows = get_monthly_summary(tid, months=3)

        assert len(rows) == 3
        assert rows[0]["month"] == this_month.strftime("%Y-%m")  # most recent first

        this_row = rows[0]
        assert this_row["pos_count"] == 1
        assert this_row["skus_pedir_ya"] == 1
        assert this_row["total_value"] == 150.0
        assert this_row["adoption_rate"] == 0.5          # 1 approved / 2 suggested
        assert this_row["capital_liberado"] == 4000.0

        last_row = next(r for r in rows if r["month"] == last_month.strftime("%Y-%m"))
        assert last_row["pos_count"] == 2
        assert last_row["skus_pedir_ya"] == 3
        assert last_row["total_value"] == 800.0
        assert last_row["adoption_rate"] == pytest.approx(5 / 6)
        assert last_row["capital_liberado"] is None      # no snapshot before last_month

    def test_month_with_no_activity_returns_zeroed_row(self, test_tenant):
        from backend.inventory.roi_service import get_monthly_summary

        rows = get_monthly_summary(test_tenant["id"], months=2)

        assert len(rows) == 2
        for row in rows:
            assert row["pos_count"] == 0
            assert row["skus_pedir_ya"] == 0
            assert row["adoption_rate"] is None
            assert row["capital_liberado"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_roi_monthly.py::TestGetMonthlySummary -v`
Expected: FAIL with `ImportError: cannot import name 'get_monthly_summary'`

- [ ] **Step 3: Implement `get_monthly_summary`**

Append to `backend/inventory/roi_service.py` (after `get_po_history`, end of file):

```python


def get_monthly_summary(tenant_id: str, months: int = 6) -> list[dict]:
    """
    Last `months` calendar months (most recent first): pedidos generados,
    riesgos de quiebre atendidos, valor gestionado, tasa de adopcion, y
    capital liberado de sobrestock (delta mes a mes de
    inventory_overstock_snapshots — None hasta tener 2 snapshots seguidos).
    """
    now = datetime.now(tz=timezone.utc)
    month_starts: list[datetime] = []
    y, m = now.year, now.month
    for _ in range(months):
        month_starts.append(datetime(y, m, 1, tzinfo=timezone.utc))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    month_starts.sort()  # oldest first

    po_rows = query(
        """SELECT date_trunc('month', generated_at) AS month,
                  COUNT(*)::int                          AS pos_count,
                  COALESCE(SUM(skus_pedir_ya), 0)::int    AS skus_pedir_ya,
                  COALESCE(SUM(total_value), 0)           AS total_value,
                  COALESCE(SUM(suggested_count), 0)::int  AS total_suggested,
                  COALESCE(SUM(approved_count), 0)::int   AS total_approved
           FROM inventory_po_log
           WHERE tenant_id = %s AND generated_at >= %s
           GROUP BY month""",
        (tenant_id, month_starts[0]),
    )
    po_by_month = {r["month"].strftime("%Y-%m"): r for r in po_rows}

    snap_rows = query(
        """SELECT date_trunc('month', recorded_at) AS month,
                  AVG(overstock_value) AS overstock_value
           FROM inventory_overstock_snapshots
           WHERE tenant_id = %s
           GROUP BY month""",
        (tenant_id,),
    )
    snap_by_month = {r["month"].strftime("%Y-%m"): float(r["overstock_value"]) for r in snap_rows}

    result: list[dict] = []
    prev_key: str | None = None
    for start in month_starts:
        key = start.strftime("%Y-%m")
        po = po_by_month.get(key)
        pos_count       = int(po["pos_count"]) if po else 0
        skus_pedir_ya   = int(po["skus_pedir_ya"]) if po else 0
        total_value     = float(po["total_value"]) if po else 0.0
        total_suggested = int(po["total_suggested"]) if po else 0
        total_approved  = int(po["total_approved"]) if po else 0
        adoption_rate = (total_approved / total_suggested) if total_suggested > 0 else None

        capital_liberado = None
        if prev_key is not None and key in snap_by_month and prev_key in snap_by_month:
            delta = snap_by_month[prev_key] - snap_by_month[key]
            if delta > 0:
                capital_liberado = round(delta, 2)

        result.append({
            "month":            key,
            "pos_count":        pos_count,
            "skus_pedir_ya":    skus_pedir_ya,
            "total_value":      round(total_value, 2),
            "adoption_rate":    adoption_rate,
            "capital_liberado": capital_liberado,
        })
        prev_key = key

    result.reverse()  # most recent first
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_roi_monthly.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/roi_service.py backend/tests/test_roi_monthly.py
git commit -m "feat(inventory): monthly ROI aggregation (quiebres, adopcion, capital liberado)"
```

---

### Task 4: Backend — `GET /inventory/roi/monthly` endpoint

**Files:**
- Modify: `backend/api/v1/inventory.py:464-468` (right after the existing `get_roi` endpoint)
- Test: `backend/tests/test_roi_monthly.py` (append)

**Interfaces:**
- Consumes: `get_monthly_summary(tenant_id, months)` from Task 3; `CurrentUser`, `get_current_user` from `backend.auth.guards` (already imported at `backend/api/v1/inventory.py:19`); `ok()` from `backend.schemas.common` (already imported at line 23); `Query` from `fastapi` (already imported at line 15).
- Produces: `GET /api/v1/inventory/roi/monthly?months=N` → `{"data": [...]}` (list shape from Task 3). Consumed by Task 5 (frontend `getROIMonthly`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_roi_monthly.py`:

```python


class TestRoiMonthlyEndpoint:
    def test_viewer_can_read(self, client, viewer_headers):
        resp = client.get("/api/v1/inventory/roi/monthly", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) == 6  # default months

    def test_unauthenticated_rejected(self, client):
        resp = client.get("/api/v1/inventory/roi/monthly")
        assert resp.status_code == 401

    def test_months_param_respected(self, client, auth_headers):
        resp = client.get("/api/v1/inventory/roi/monthly?months=3", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_roi_monthly.py::TestRoiMonthlyEndpoint -v`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Implement the endpoint**

In `backend/api/v1/inventory.py`, insert right after the existing `get_roi` function (after line 468, before `@router.get("/po-history")`):

```python
@router.get("/roi/monthly")
def get_roi_monthly(
    months: int = Query(default=6, ge=1, le=24),
    user: CurrentUser = Depends(get_current_user),
):
    """Últimos N meses: pedidos, riesgos de quiebre atendidos, adopción, capital liberado de sobrestock."""
    from backend.inventory.roi_service import get_monthly_summary
    return ok(get_monthly_summary(user.tenant_id, months))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_roi_monthly.py -v`
Expected: 10 passed

- [ ] **Step 5: Run the full backend test suite to check for regressions**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all tests pass (no regressions)

- [ ] **Step 6: Commit**

```bash
git add backend/api/v1/inventory.py backend/tests/test_roi_monthly.py
git commit -m "feat(inventory): expose GET /inventory/roi/monthly"
```

---

### Task 5: Frontend — API client + types

**Files:**
- Modify: `Frontend/src/lib/types.ts:716` (insert new interface between `InventoryROISummary` and `POLogEntry`)
- Modify: `Frontend/src/lib/api.ts:630-631` (insert new function right after `getInventoryROI`)

**Interfaces:**
- Consumes: `request<T>(method, path)` helper (existing, used throughout `api.ts`, e.g. `Frontend/src/lib/api.ts:629-630`).
- Produces: `ROIMonthlyRow` type and `getROIMonthly(months = 6): Promise<ROIMonthlyRow[]>`. Consumed by Task 6 (page component).

- [ ] **Step 1: Add the type**

In `Frontend/src/lib/types.ts`, insert after line 716 (`}` closing `InventoryROISummary`), before `export interface POLogEntry {`:

```typescript
export interface ROIMonthlyRow {
  month:             string          // 'YYYY-MM'
  pos_count:         number
  skus_pedir_ya:     number
  total_value:       number
  adoption_rate:     number | null
  capital_liberado:  number | null
}

```

- [ ] **Step 2: Add the API client function**

In `Frontend/src/lib/api.ts`, insert after line 630 (`request<InventoryROISummary>('GET', '/inventory/roi')`), before `export const getPOHistory`:

```typescript
export const getROIMonthly = (months = 6) =>
  request<import('./types').ROIMonthlyRow[]>('GET', `/inventory/roi/monthly?months=${months}`)

```

- [ ] **Step 3: Typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add Frontend/src/lib/types.ts Frontend/src/lib/api.ts
git commit -m "feat(inventory): frontend client for monthly ROI endpoint"
```

---

### Task 6: Frontend — monthly evolution table on `/inventory/roi`

**Files:**
- Modify: `Frontend/src/app/inventory/roi/page.tsx`
- Modify: `Frontend/src/i18n/translations.ts` (add `roi.*` keys to both `es` and `en` blocks)

**Interfaces:**
- Consumes: `getROIMonthly` and `ROIMonthlyRow` from Task 5; `useLanguage()` → `{ t }` (existing, already imported at `Frontend/src/app/inventory/roi/page.tsx:8`); existing style constants `C` (`Frontend/src/app/inventory/roi/page.tsx:11-15`); `fmtCurrency`, `fmtUnits` helpers (`Frontend/src/app/inventory/roi/page.tsx:30-36`).
- Produces: nothing consumed elsewhere — this is the leaf UI change.

- [ ] **Step 1: Remove the `MonthKPIs` component**

In `Frontend/src/app/inventory/roi/page.tsx`, delete the entire `MonthKPIs` function (lines 164–192):

```typescript
function MonthKPIs({ roi }: { roi: InventoryROISummary }) {
  ...
}
```

- [ ] **Step 2: Add the `MonthlyEvolutionTable` component**

First, add `ROIMonthlyRow` to the existing type import at the top of the file (line 5):

```typescript
import type { InventoryROISummary, POLogEntry, POItemLine, ROIMonthlyRow } from '@/lib/types'
```

Then, in `Frontend/src/app/inventory/roi/page.tsx`, insert the new component where `MonthKPIs` used to be (same location, right before `// ── Reception badge + modal (feature 1.4) ────────────────────────────────────`):

```typescript
function fmtMonthLabel(month: string): string {
  const [y, m] = month.split('-').map(Number)
  return new Date(y, m - 1, 1).toLocaleDateString('es', { month: 'long', year: 'numeric' })
}

function MonthlyEvolutionTable({ rows }: { rows: ROIMonthlyRow[] }) {
  const { t } = useLanguage()

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>
      <div style={{
        padding: '14px 18px', borderBottom: `1px solid ${C.border}`,
        background: C.card, display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <TrendingUp size={14} color={C.indigo} />
        <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>
          {t('roi.monthly_evolution_title')}
        </span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ background: C.card }}>
              {[
                t('roi.col_month'), t('roi.col_orders'), t('roi.col_stockouts_handled'),
                t('roi.col_value_managed'), t('roi.col_adoption'), t('roi.col_capital_freed'),
              ].map(h => (
                <th key={h} style={{
                  padding: '9px 14px', textAlign: 'left', whiteSpace: 'nowrap',
                  color: C.dim, fontWeight: 600, fontSize: 10,
                  borderBottom: `1px solid ${C.border}`,
                  textTransform: 'uppercase', letterSpacing: '0.06em',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, idx) => (
              <tr key={row.month} style={{
                background: idx % 2 === 0 ? C.surface : C.card,
                borderBottom: `1px solid ${C.border}`,
              }}>
                <td style={{ padding: '11px 14px', color: C.text, fontWeight: 600, textTransform: 'capitalize' }}>
                  {fmtMonthLabel(row.month)}
                </td>
                <td style={{ padding: '11px 14px', color: C.text }}>{row.pos_count}</td>
                <td style={{ padding: '11px 14px', color: row.skus_pedir_ya > 0 ? C.red : C.dim, fontWeight: row.skus_pedir_ya > 0 ? 700 : 400 }}>
                  {row.skus_pedir_ya}
                </td>
                <td style={{ padding: '11px 14px', color: C.green, fontFamily: 'monospace' }}>
                  {fmtCurrency(row.total_value)}
                </td>
                <td style={{ padding: '11px 14px', color: C.text }}>
                  {row.adoption_rate != null ? `${Math.round(row.adoption_rate * 100)}%` : '—'}
                </td>
                <td style={{ padding: '11px 14px', color: row.capital_liberado != null ? C.green : C.dim, fontFamily: 'monospace', fontWeight: row.capital_liberado != null ? 600 : 400 }}>
                  {row.capital_liberado != null
                    ? fmtCurrency(row.capital_liberado)
                    : t('roi.capital_freed_pending')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Wire it into the page's data loading and render**

In `Frontend/src/app/inventory/roi/page.tsx`, in the `ROIPage` function:

Add state near the existing `history` state (around line 502):

```typescript
  const [monthly, setMonthly] = useState<ROIMonthlyRow[]>([])
```

In the `load` callback (around lines 507–522), change:

```typescript
      const [roiData, histData] = await Promise.all([
        getInventoryROI(),
        getPOHistory(20),
      ])
      setRoi(roiData)
      setHistory(histData)
```

to:

```typescript
      const [roiData, histData, monthlyData] = await Promise.all([
        getInventoryROI(),
        getPOHistory(20),
        getROIMonthly(),
      ])
      setRoi(roiData)
      setHistory(histData)
      setMonthly(monthlyData)
```

Add the import at the top of the file, alongside the existing `getInventoryROI, getPOHistory, getPOItems, receivePO` import (line 4):

```typescript
import { getInventoryROI, getPOHistory, getPOItems, receivePO, getROIMonthly } from '@/lib/api'
```

Replace the "Section 2 — Month comparison" block (currently lines 581–587):

```typescript
          {/* Section 2 — Month comparison */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: C.dim, marginBottom: 10 }}>
              {t('roi.monthly_activity')}
            </div>
            <MonthKPIs roi={roi} />
          </div>
```

with:

```typescript
          {/* Section 2 — Monthly evolution */}
          <MonthlyEvolutionTable rows={monthly} />
```

- [ ] **Step 4: Add translations**

In `Frontend/src/i18n/translations.ts`, add to the `es` block, right after line 304 (`'roi.last_order_registered_prefix': 'Ultima orden registrada el',`):

```typescript
    'roi.monthly_evolution_title': 'Evolución mensual',
    'roi.col_month': 'Mes',
    'roi.col_orders': 'Pedidos',
    'roi.col_stockouts_handled': 'Riesgos de quiebre atendidos',
    'roi.col_value_managed': 'Valor gestionado',
    'roi.col_adoption': '% Adopción',
    'roi.col_capital_freed': 'Capital liberado de sobrestock',
    'roi.capital_freed_pending': 'Aún no hay suficiente historial',
```

Add to the `en` block, right after line 1483 (`'roi.last_order_registered_prefix': 'Last order registered on',`):

```typescript
    'roi.monthly_evolution_title': 'Monthly evolution',
    'roi.col_month': 'Month',
    'roi.col_orders': 'Orders',
    'roi.col_stockouts_handled': 'Stockout risks handled',
    'roi.col_value_managed': 'Value managed',
    'roi.col_adoption': '% Adoption',
    'roi.col_capital_freed': 'Capital freed from overstock',
    'roi.capital_freed_pending': 'Not enough history yet',
```

- [ ] **Step 5: Typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 6: Manual verification**

Start the backend (`backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --port 8010`) and frontend (`cd Frontend && set BACKEND_URL=http://localhost:8010&& npm run dev`), log in, go to `/inventory/roi`, and confirm the new "Evolución mensual" table renders with 6 rows and no console errors. If no PO history exists yet, use `/quick-start` → "Probar con datos de ejemplo" first, then export a PO from `/inventory` so at least one row has real numbers.

- [ ] **Step 7: Commit**

```bash
git add Frontend/src/app/inventory/roi/page.tsx Frontend/src/i18n/translations.ts
git commit -m "feat(inventory): monthly evolution table replaces this-month/last-month tile on ROI page"
```

---

## Self-Review Notes

- **Spec coverage:** migration + snapshot job (spec §1–2) → Task 1; scheduler (spec §2) → Task 2; `get_monthly_summary` (spec §2) → Task 3; endpoint (spec §2) → Task 4; frontend client + types (spec §3) → Task 5; page UI + i18n (spec §3) → Task 6; testing plan (spec §4) → covered inline in Tasks 1, 3, 4.
- **Out of scope confirmed:** no proactive email/WhatsApp send, no backfill — matches spec.
- **Type consistency checked:** `ROIMonthlyRow` fields (`month`, `pos_count`, `skus_pedir_ya`, `total_value`, `adoption_rate`, `capital_liberado`) match exactly between Task 3's Python dict, Task 4's endpoint passthrough, and Task 5/6's TypeScript interface and usage.
