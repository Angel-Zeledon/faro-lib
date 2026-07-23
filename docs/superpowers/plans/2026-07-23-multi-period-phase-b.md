# Multi-Period Planning — Phase B Implementation Plan (active period/horizon setting + resolver + top-bar UX)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the tenant an **active planning setting** — `{period, horizon}` — stored in `tenants.settings` JSONB, and a single **resolver** (`resolve_active_session`) that every screen and the daily alert loop read through to pick "the session the app should use now" = the newest family's COMPLETED session at the active period, with a clean fallback to the legacy latest-completed session for pre-feature tenants. Surface it as a compact **top-bar control** (period selector + horizon stepper), admin-editable and read-only for everyone else, hidden for mono-period tenants (spec: `docs/superpowers/specs/2026-07-23-multi-period-planning-design.md`, Phase B).

**Architecture:** Phase A already fanned each training launch into a **session family** (`sessions.family_id` + `sessions.granularity`, one member per available granularity, each pre-forecast to a generous reach). Phase B adds no table. It stores the active `planning = {"period","horizon"}` blob in the existing `tenants.settings` JSONB (today read/written nowhere in app code — this plan adds the first read/merge helpers in `backend/tenants/service.py`). A new `backend/sessions/planning_service.py` owns three pure-ish functions over that setting plus the `sessions` family rows: `get_planning`, `set_planning`, `resolve_active_session`. A new `backend/api/v1/planning.py` router exposes `GET /planning` (any user) and `PUT /planning` (admin-only, via the existing `require_admin` guard). The daily alert loop swaps its `get_latest_completed_session(tid)` call for `resolve_active_session(tid)`. The frontend gets `getPlanning`/`setPlanning` in `api.ts`, a `PlanningState` type, and a `PlanningControl` component mounted in `TopBar.tsx`, plus `es`+`en` i18n keys.

**Tech Stack:** FastAPI + psycopg2 raw SQL (`RealDictCursor` returns JSONB columns already parsed to dicts; JSONB writes go through `backend.db.connection._json`); no pandas in any Phase B backend code; pytest against local Postgres :5544 (docker `faro_db`); Next.js 14 front end typechecked with `node ./node_modules/typescript/bin/tsc --noEmit`.

## Global Constraints

- All code, comments, tests, commit messages in **English** (CLAUDE.md). The ONLY Spanish is `Frontend/src/i18n/translations.ts` `es` values and backend end-user copy.
- Reuse Phase A constants — do NOT redefine them. `GENEROUS_REACH = {"daily": 90, "weekly": 26, "monthly": 12}` lives in `backend/sessions/family_service.py`; import it.
- Supported planning periods in v1 are exactly `daily`, `weekly`, `monthly`, ordered finest→coarsest.
- Default planning (fresh tenant, no family, or missing/partial setting) is **exactly today's behavior**: `period="daily"`, `horizon=14`, `available_periods=["daily"]`, `max_horizon=90`.
- The resolver is the single seam: family exists → newest family's COMPLETED session at the active period; not found for any reason (family-less pre-feature tenant, or the active grain hasn't finished training yet) → fall back to `inventory/service.get_latest_completed_session`. Never raise from the resolver; return `None` only when the tenant has no completed session at all.
- `PUT /planning` is admin-only via `require_admin` (already defined in `backend/auth/guards.py:71` — do NOT re-add it). `GET /planning` uses `get_current_user` (any authenticated role).
- Invalid `period` (not in `available_periods`) or out-of-range `horizon` (not `1 <= horizon <= GENEROUS_REACH[period]`) → `set_planning` raises `ValueError`; the API layer maps it to HTTP 422.
- JSONB writes to `tenants.settings` must merge, never clobber — other keys may live there later.
- Run backend tests: `cd backend && python -m pytest tests/<file> -q` (needs Postgres on :5544). Run frontend typecheck: `cd Frontend && node ./node_modules/typescript/bin/tsc --noEmit`. Never `npm run build`.

---

### Task 1: Tenant settings read + merge helpers

**Files:**
- Modify: `backend/tenants/service.py` (add `get_settings`, `update_settings`)
- Test: `backend/tests/test_planning.py` (new)

**Interfaces:**
- Consumes: `backend.db.connection` (`query_one`, `execute`, `_json`).
- Produces:
  - `get_settings(tenant_id: str) -> dict` — the parsed `tenants.settings` JSONB (empty dict when the tenant is missing or the column is empty).
  - `update_settings(tenant_id: str, patch: dict) -> dict` — shallow-merges `patch` into the current settings, persists, and returns the merged dict.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_planning.py
"""Multi-period Phase B: tenant settings, planning service, resolver, API."""

from backend.tenants import service as tenant_svc


class TestTenantSettings:
    def test_settings_default_empty(self, client, test_tenant):
        assert tenant_svc.get_settings(test_tenant["id"]) == {}

    def test_update_settings_merges_and_persists(self, client, test_tenant):
        tid = test_tenant["id"]
        tenant_svc.update_settings(tid, {"planning": {"period": "weekly", "horizon": 6}})
        tenant_svc.update_settings(tid, {"other": 1})
        got = tenant_svc.get_settings(tid)
        assert got["planning"] == {"period": "weekly", "horizon": 6}  # not clobbered
        assert got["other"] == 1

    def test_get_settings_unknown_tenant_is_empty(self, client):
        assert tenant_svc.get_settings("ten_does_not_exist") == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_planning.py -q -k "TenantSettings"`
Expected: FAIL — `get_settings` / `update_settings` undefined.

- [ ] **Step 3: Implement in `backend/tenants/service.py`**

The module already imports `query_one, execute` from `backend.db.connection`; extend that import to add `_json`:

```python
from backend.db.connection import query_one, execute, _json
```

Append these functions:

```python
def get_settings(tenant_id: str) -> dict:
    """The tenant's `settings` JSONB, already parsed (RealDictCursor). Empty
    dict when the tenant is missing or has no settings yet."""
    row = query_one("SELECT settings FROM tenants WHERE id = %s", (tenant_id,))
    if not row or not row.get("settings"):
        return {}
    return dict(row["settings"])


def update_settings(tenant_id: str, patch: dict) -> dict:
    """Shallow-merge `patch` into the tenant's settings and persist. Returns the
    merged dict. Other top-level keys are preserved (never clobbered)."""
    merged = {**get_settings(tenant_id), **patch}
    execute("UPDATE tenants SET settings = %s WHERE id = %s", (_json(merged), tenant_id))
    return merged
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_planning.py -q -k "TenantSettings"`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/tenants/service.py backend/tests/test_planning.py
git commit -m "feat(tenants): settings read + shallow-merge helpers"
```

---

### Task 2: `planning_service` — get / set / resolve

**Files:**
- Create: `backend/sessions/planning_service.py`
- Test: `backend/tests/test_planning.py` (extend)

**Interfaces:**
- Consumes: `backend.db.connection` (`query`, `query_one`), Task 1 `tenants.service` (`get_settings`, `update_settings`), Phase A `sessions.family_service.GENEROUS_REACH`, `inventory.service.get_latest_completed_session`.
- Produces:
  - `DEFAULT_PLANNING = {"period": "daily", "horizon": 14}`.
  - `get_planning(tenant_id: str) -> dict` — `{"period", "horizon", "available_periods", "max_horizon"}`. Reads the stored setting, resolves the newest family's available periods, coerces `period` into `available_periods` and clamps `horizon` into `1..GENEROUS_REACH[period]`.
  - `set_planning(tenant_id: str, period: str, horizon: int) -> dict` — validates `period ∈ available_periods` and `1 <= horizon <= GENEROUS_REACH[period]` (else `ValueError`), persists under `settings.planning`, returns the fresh `get_planning`.
  - `resolve_active_session(tenant_id: str) -> str | None` — newest family's COMPLETED session id at the active period; falls back to `get_latest_completed_session` otherwise; `None` when no completed session exists.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_planning.py
from backend.db.connection import execute, query_one
from backend.sessions import service as session_svc
from backend.sessions import planning_service as plan


def _make_family(tid, uid, members, family_id="fam_test", completed=True):
    """Insert sibling sessions sharing family_id, each carrying a granularity.
    `members` is a list of granularity strings; created_at is staggered so the
    LAST-inserted family reads as the newest. Returns the family_id."""
    for i, grain in enumerate(members):
        s = session_svc.create_session(tid, uid, f"{grain} member")
        status = "COMPLETED" if completed else "MODELS_CONFIGURED"
        execute(
            "UPDATE sessions SET family_id=%s, granularity=%s, status=%s, "
            "created_at = NOW() + (%s || ' seconds')::interval, updated_at = NOW() "
            "WHERE id=%s AND tenant_id=%s",
            (family_id, grain, status, str(i), s["id"], tid))
    return family_id


class TestGetPlanning:
    def test_default_is_daily_14_for_fresh_tenant(self, client, test_tenant):
        got = plan.get_planning(test_tenant["id"])
        assert got == {"period": "daily", "horizon": 14,
                       "available_periods": ["daily"], "max_horizon": 90}

    def test_available_periods_from_newest_family(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly", "monthly"], family_id="fam1")
        got = plan.get_planning(tid)
        assert got["available_periods"] == ["daily", "weekly", "monthly"]
        assert got["period"] == "daily" and got["max_horizon"] == 90

    def test_newest_family_wins(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily"], family_id="old")
        _make_family(tid, uid, ["daily", "weekly"], family_id="new")
        # `new` is inserted second → newer created_at → its periods win.
        assert plan.get_planning(tid)["available_periods"] == ["daily", "weekly"]


class TestSetPlanning:
    def test_set_valid_period_and_horizon(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        out = plan.set_planning(tid, "weekly", 6)
        assert out["period"] == "weekly" and out["horizon"] == 6
        # persisted
        assert plan.get_planning(tid)["period"] == "weekly"

    def test_invalid_period_rejected(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily"], family_id="fam1")
        try:
            plan.set_planning(tid, "weekly", 4)  # weekly not available
            assert False, "expected ValueError"
        except ValueError:
            pass
        # setting unchanged
        assert plan.get_planning(tid)["period"] == "daily"

    def test_over_reach_horizon_rejected(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        try:
            plan.set_planning(tid, "weekly", 27)  # weekly reach is 26
            assert False, "expected ValueError"
        except ValueError:
            pass


class TestResolveActiveSession:
    def test_resolves_period_matched_session(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        plan.set_planning(tid, "weekly", 4)
        sid = plan.resolve_active_session(tid)
        row = query_one("SELECT granularity FROM sessions WHERE id=%s", (sid,))
        assert row["granularity"] == "weekly"

    def test_falls_back_for_family_less_tenant(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        s = session_svc.create_session(tid, uid, "legacy")
        execute("UPDATE sessions SET status='COMPLETED' WHERE id=%s", (s["id"],))
        # No family_id set → resolver falls back to latest completed.
        assert plan.resolve_active_session(tid) == s["id"]

    def test_none_when_no_completed_session(self, client, test_tenant):
        assert plan.resolve_active_session(test_tenant["id"]) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_planning.py -q -k "GetPlanning or SetPlanning or ResolveActiveSession"`
Expected: FAIL — module/functions absent.

- [ ] **Step 3: Implement `backend/sessions/planning_service.py`**

```python
"""
Active planning setting + resolver (multi-period planning, Phase B).

The tenant has one active planning view: a `period` (daily/weekly/monthly) and a
`horizon` in that period's own unit, stored in `tenants.settings.planning`.
`resolve_active_session` is the single seam every screen and the daily alert loop
read through to pick the session the app should use now: the newest family's
COMPLETED session at the active period, falling back to the legacy latest-completed
session for pre-feature (family-less) tenants.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.db.connection import query, query_one
from backend.sessions.family_service import GENEROUS_REACH
from backend.tenants import service as tenant_svc

log = logging.getLogger(__name__)

# Preserves today's behavior exactly for a tenant with no family / no setting.
DEFAULT_PLANNING = {"period": "daily", "horizon": 14}
# Finest → coarsest; the order available_periods is returned in.
_PERIOD_ORDER = ["daily", "weekly", "monthly"]


def _newest_family_id(tenant_id: str) -> Optional[str]:
    """The family_id of the most recently created family session, or None when
    the tenant has no family (pre-feature data)."""
    row = query_one(
        """SELECT family_id FROM sessions
           WHERE tenant_id = %s AND family_id IS NOT NULL
           ORDER BY created_at DESC LIMIT 1""",
        (tenant_id,))
    return row["family_id"] if row else None


def _available_periods(tenant_id: str, family_id: Optional[str]) -> list[str]:
    """Distinct granularities of the given family, ordered finest→coarsest.
    Family-less tenants get the daily-only default (today's behavior)."""
    if not family_id:
        return ["daily"]
    rows = query(
        """SELECT DISTINCT granularity FROM sessions
           WHERE tenant_id = %s AND family_id = %s AND granularity IS NOT NULL""",
        (tenant_id, family_id))
    grains = {r["granularity"] for r in rows}
    out = [g for g in _PERIOD_ORDER if g in grains]
    return out or ["daily"]


def get_planning(tenant_id: str) -> dict:
    """Resolve the active setting against the newest family. period is coerced
    into available_periods and horizon clamped into 1..reach(period)."""
    stored = tenant_svc.get_settings(tenant_id).get("planning") or {}
    available = _available_periods(tenant_id, _newest_family_id(tenant_id))
    period = stored.get("period", DEFAULT_PLANNING["period"])
    if period not in available:
        period = available[0]
    max_horizon = GENEROUS_REACH.get(period, 90)
    try:
        horizon = int(stored.get("horizon", DEFAULT_PLANNING["horizon"]))
    except (TypeError, ValueError):
        horizon = DEFAULT_PLANNING["horizon"]
    horizon = max(1, min(horizon, max_horizon))
    return {"period": period, "horizon": horizon,
            "available_periods": available, "max_horizon": max_horizon}


def set_planning(tenant_id: str, period: str, horizon: int) -> dict:
    """Validate + persist the active planning setting. Raises ValueError on an
    unavailable period or an out-of-reach horizon (the API maps it to 422)."""
    available = _available_periods(tenant_id, _newest_family_id(tenant_id))
    if period not in available:
        raise ValueError(
            f"period '{period}' is not available; choose one of {available}")
    max_horizon = GENEROUS_REACH.get(period, 90)
    if not (1 <= int(horizon) <= max_horizon):
        raise ValueError(
            f"horizon must be between 1 and {max_horizon} for period '{period}'")
    tenant_svc.update_settings(
        tenant_id, {"planning": {"period": period, "horizon": int(horizon)}})
    return get_planning(tenant_id)


def resolve_active_session(tenant_id: str) -> Optional[str]:
    """The session the app should use now. Newest family's COMPLETED session at
    the active period; falls back to the legacy latest-completed session when
    that is not found (family-less tenant, or the active grain hasn't finished
    training). None when the tenant has no completed session at all."""
    from backend.inventory.service import get_latest_completed_session

    family_id = _newest_family_id(tenant_id)
    if family_id:
        stored = tenant_svc.get_settings(tenant_id).get("planning") or {}
        period = stored.get("period", DEFAULT_PLANNING["period"])
        row = query_one(
            """SELECT id AS session_id FROM sessions
               WHERE tenant_id = %s AND family_id = %s AND granularity = %s
                 AND status = 'COMPLETED'
               ORDER BY updated_at DESC LIMIT 1""",
            (tenant_id, family_id, period))
        if row:
            return row["session_id"]
    legacy = get_latest_completed_session(tenant_id)
    return legacy["session_id"] if legacy else None
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_planning.py -q -k "GetPlanning or SetPlanning or ResolveActiveSession"`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/sessions/planning_service.py backend/tests/test_planning.py
git commit -m "feat(sessions): planning_service get/set/resolve over active period setting"
```

---

### Task 3: API — `GET /planning` (any user) + `PUT /planning` (admin-only)

**Files:**
- Create: `backend/api/v1/planning.py`
- Modify: `backend/main.py` (import + `include_router`)
- Test: `backend/tests/test_planning.py` (extend)

**Interfaces:**
- Consumes: `backend.auth.guards` (`CurrentUser`, `get_current_user`, `require_admin`), Task 2 `planning_service` (`get_planning`, `set_planning`, `resolve_active_session`), `backend.schemas.common.ok`.
- Produces:
  - `GET /api/v1/planning` → `ok({...get_planning, "active_session_id": resolve_active_session()})` (any authenticated role).
  - `PUT /api/v1/planning` body `{"period": str, "horizon": int}` → `ok(get_planning)` on success (admin-only); `ValueError` → HTTP 422.

- [ ] **Step 1: Write the failing permission-pair + validation tests**

```python
# append to backend/tests/test_planning.py
class TestPlanningApi:
    def test_get_planning_default(self, client, auth_headers):
        r = client.get("/api/v1/planning", headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["period"] == "daily" and data["horizon"] == 14
        assert data["available_periods"] == ["daily"]
        assert "active_session_id" in data  # None for a fresh tenant

    def test_put_planning_admin_succeeds(self, client, auth_headers, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        r = client.put("/api/v1/planning", headers=auth_headers,
                       json={"period": "weekly", "horizon": 6})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["period"] == "weekly"
        # state changed in DB
        assert plan.get_planning(tid)["period"] == "weekly"

    def test_put_planning_analyst_forbidden(self, client, analyst_headers, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        r = client.put("/api/v1/planning", headers=analyst_headers,
                       json={"period": "weekly", "horizon": 6})
        assert r.status_code == 403, r.text
        assert plan.get_planning(tid)["period"] == "daily"  # unchanged

    def test_put_planning_viewer_forbidden(self, client, viewer_headers, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        r = client.put("/api/v1/planning", headers=viewer_headers,
                       json={"period": "weekly", "horizon": 6})
        assert r.status_code == 403, r.text
        assert plan.get_planning(tid)["period"] == "daily"  # unchanged

    def test_put_planning_invalid_period_422(self, client, auth_headers, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily"], family_id="fam1")
        r = client.put("/api/v1/planning", headers=auth_headers,
                       json={"period": "weekly", "horizon": 4})
        assert r.status_code == 422, r.text

    def test_put_planning_over_reach_422(self, client, auth_headers, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        r = client.put("/api/v1/planning", headers=auth_headers,
                       json={"period": "weekly", "horizon": 99})
        assert r.status_code == 422, r.text
```

> The `analyst_headers` / `viewer_headers` / `auth_headers` (admin) fixtures share the same `test_tenant`, so the family created inline is visible to every role — the pair asserts both the 403 AND the unchanged setting (per the Testing Standards mandate).

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_planning.py -q -k "PlanningApi"`
Expected: FAIL — route 404 (router not registered).

- [ ] **Step 3: Implement `backend/api/v1/planning.py`**

```python
"""
Active planning view API (multi-period planning, Phase B).

GET /planning  — the active {period, horizon}, the family's available periods,
                 the reach cap for the current period, and the resolved active
                 session id (for the frontend's default). Any authenticated role.
PUT /planning  — set {period, horizon}. Admin-only.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.auth.guards import CurrentUser, get_current_user, require_admin
from backend.schemas.common import ok
from backend.sessions import planning_service as plan

router = APIRouter(prefix="/planning", tags=["planning"])


class PlanningUpdate(BaseModel):
    period:  str = Field(..., description="daily | weekly | monthly")
    horizon: int = Field(..., ge=1, le=90)


@router.get("")
def get_planning(user: CurrentUser = Depends(get_current_user)):
    data = plan.get_planning(user.tenant_id)
    data["active_session_id"] = plan.resolve_active_session(user.tenant_id)
    return ok(data)


@router.put("")
def put_planning(
    body: PlanningUpdate,
    user: CurrentUser = Depends(require_admin),
):
    try:
        data = plan.set_planning(user.tenant_id, body.period, body.horizon)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return ok(data)
```

> `PlanningUpdate.horizon`'s `le=90` is the coarse structural bound (the largest reach any grain has, daily=90); the fine per-period cap (`weekly` → 26, `monthly` → 12) is enforced in `set_planning` and surfaces as 422 via the `ValueError` path — that is what `test_put_planning_over_reach_422` covers.

- [ ] **Step 4: Register the router in `backend/main.py`**

Add `planning` to the `from backend.api.v1 import ...` line (alongside `training`), and add after the `training.router` include (line ~166):

```python
app.include_router(planning.router,      prefix=_PREFIX)
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_planning.py -q -k "PlanningApi"`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/api/v1/planning.py backend/main.py backend/tests/test_planning.py
git commit -m "feat(api): GET/PUT /planning (admin-only writes) exposing the resolver"
```

---

### Task 4: Wire the resolver into the daily inventory alert loop

**Files:**
- Modify: `backend/inventory/service.py` (`run_daily_inventory_alerts`, both `get_latest_completed_session(tid)` call sites at ~2145 and ~2247)
- Test: `backend/tests/test_planning.py` (extend)

**Interfaces:**
- Consumes: Task 2 `resolve_active_session`.
- Behavior: the alert loop uses the same session the app shows — the newest family's active-period session — instead of always the raw latest-completed one. For a family-less tenant the resolver returns the identical session `get_latest_completed_session` would (regression-safe).

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_planning.py
class TestAlertLoopUsesResolver:
    def test_alert_loop_resolves_active_period_session(self, client, test_tenant, registered_user, monkeypatch):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        plan.set_planning(tid, "weekly", 4)

        seen = {}
        import backend.inventory.service as inv
        from backend.db import session_store as ss

        # Spy on which session the loop fetches forecasts for.
        def spy(tenant_id, session_id):
            if tenant_id == tid:
                seen["sid"] = session_id
            return {}
        monkeypatch.setattr(ss, "get_forecasts", spy)

        inv.run_daily_inventory_alerts()

        weekly = query_one(
            "SELECT id FROM sessions WHERE tenant_id=%s AND family_id='fam1' "
            "AND granularity='weekly'", (tid,))
        assert seen.get("sid") == weekly["id"]
```

> The loop iterates only tenants returned by `get_tenants_with_active_sessions()` (grouped by COMPLETED sessions); the inline family qualifies. If the spy is never hit, confirm the tenant appears in that query before debugging the resolver wiring. Note `run_daily_inventory_alerts` imports `session_store` locally as `from backend.db import session_store` — patch the module object (`backend.db.session_store.get_forecasts`), which is what the local import binds to.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_planning.py -q -k "AlertLoopUsesResolver"`
Expected: FAIL — the loop still fetches the daily (latest-completed) session, not the weekly one.

- [ ] **Step 3: Implement**

In `backend/inventory/service.py`, inside `run_daily_inventory_alerts`, add the import near the top of the function (beside the existing `from backend.db import session_store`):

```python
    from backend.sessions.planning_service import resolve_active_session
```

Then replace the first call site (~2145):

```python
            session = get_latest_completed_session(tid)
            if not session:
                continue
            sid = session["session_id"]
```

with:

```python
            sid = resolve_active_session(tid)
            if not sid:
                continue
```

Apply the identical swap at the second site (~2247, the per-warehouse pass) so both passes agree on one session id.

- [ ] **Step 4: Run new + regression**

Run: `cd backend && python -m pytest tests/test_planning.py tests/test_demo_and_alerts.py -q`
Expected: all pass — the family-less tenants in the existing alert tests resolve to the same session as before.

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/service.py backend/tests/test_planning.py
git commit -m "feat(inventory): daily alert loop reads through resolve_active_session"
```

---

### Task 5: Frontend API client + types

**Files:**
- Modify: `Frontend/src/lib/types.ts` (add `PlanningState`, `PlanningPeriod`)
- Modify: `Frontend/src/lib/api.ts` (add `getPlanning`, `setPlanning`)
- Verify: `cd Frontend && node ./node_modules/typescript/bin/tsc --noEmit`

**Interfaces:**
- Produces:
  - `type PlanningPeriod = 'daily' | 'weekly' | 'monthly'`.
  - `interface PlanningState { period: PlanningPeriod; horizon: number; available_periods: PlanningPeriod[]; max_horizon: number; active_session_id: string | null }`.
  - `getPlanning(): Promise<PlanningState>` → `GET /planning`.
  - `setPlanning(period, horizon): Promise<PlanningState>` → `PUT /planning`.

- [ ] **Step 1: Add the types**

In `Frontend/src/lib/types.ts`, append:

```typescript
export type PlanningPeriod = 'daily' | 'weekly' | 'monthly'

export interface PlanningState {
  period:            PlanningPeriod
  horizon:           number
  available_periods: PlanningPeriod[]
  max_horizon:       number
  active_session_id: string | null
}
```

- [ ] **Step 2: Add the API client functions**

In `Frontend/src/lib/api.ts`, add `PlanningState, PlanningPeriod` to the `import type { ... } from './types'` block, then append (mirroring the existing `request<T>('METHOD', path, body?)` style used by the other exports):

```typescript
export const getPlanning = () =>
  request<PlanningState>('GET', '/planning')

export const setPlanning = (period: PlanningPeriod, horizon: number) =>
  request<PlanningState>('PUT', '/planning', { period, horizon })
```

> `request` unwraps the `{ success, data }` envelope to `data` already (grep an existing `export const` such as `getSessions` to confirm the shape); these two follow the same pattern.

- [ ] **Step 3: Typecheck**

Run: `cd Frontend && node ./node_modules/typescript/bin/tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add Frontend/src/lib/types.ts Frontend/src/lib/api.ts
git commit -m "feat(web): planning api client + types"
```

---

### Task 6: Frontend — top-bar planning control + i18n + resolver default

**Files:**
- Create: `Frontend/src/components/layout/PlanningControl.tsx`
- Modify: `Frontend/src/components/layout/TopBar.tsx` (mount the control)
- Modify: `Frontend/src/i18n/translations.ts` (add `planning.*` keys to both `es` and `en`)
- Modify: `Frontend/src/hooks/useAutoSession.ts` (default to the resolver's `active_session_id`)
- Verify: `cd Frontend && node ./node_modules/typescript/bin/tsc --noEmit`

**Interfaces:**
- Consumes: Task 5 `getPlanning`, `setPlanning`, `PlanningState`, `PlanningPeriod`; `getUser` from `@/lib/auth` (role gate); `useLanguage` (`t`); `useToast` (`addToast`).
- Produces: `PlanningControl` — a compact period selector + horizon stepper. Admin: editable. Others: read-only (disabled). Mono-period family (`available_periods.length <= 1`): renders nothing. Unit word follows the period via i18n (`días`/`semanas`/`meses`).

- [ ] **Step 1: Add i18n keys (es + en)**

In `Frontend/src/i18n/translations.ts`, inside the `es` block (near the `'topbar.*'` keys) add:

```typescript
    'planning.period_label':   'Ver por',
    'planning.horizon_label':  'Horizonte',
    'planning.daily':          'Día',
    'planning.weekly':         'Semana',
    'planning.monthly':        'Mes',
    'planning.unit_daily':     'días',
    'planning.unit_weekly':    'semanas',
    'planning.unit_monthly':   'meses',
    'planning.saved':          'Vista de planificación actualizada',
    'planning.save_error':     'No se pudo actualizar la vista de planificación',
```

and the parallel keys in the `en` block:

```typescript
    'planning.period_label':   'View by',
    'planning.horizon_label':  'Horizon',
    'planning.daily':          'Day',
    'planning.weekly':         'Week',
    'planning.monthly':        'Month',
    'planning.unit_daily':     'days',
    'planning.unit_weekly':    'weeks',
    'planning.unit_monthly':   'months',
    'planning.saved':          'Planning view updated',
    'planning.save_error':     'Could not update the planning view',
```

- [ ] **Step 2: Create `Frontend/src/components/layout/PlanningControl.tsx`**

```tsx
'use client'
import { useState, useEffect } from 'react'
import { getPlanning, setPlanning, isApiError } from '@/lib/api'
import type { PlanningState, PlanningPeriod } from '@/lib/types'
import { getUser } from '@/lib/auth'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'

const PERIOD_KEY: Record<PlanningPeriod, string> = {
  daily: 'planning.daily', weekly: 'planning.weekly', monthly: 'planning.monthly',
}
const UNIT_KEY: Record<PlanningPeriod, string> = {
  daily: 'planning.unit_daily', weekly: 'planning.unit_weekly', monthly: 'planning.unit_monthly',
}

export default function PlanningControl() {
  const { t } = useLanguage()
  const { addToast } = useToast()
  const [state, setState] = useState<PlanningState | null>(null)
  const [busy,  setBusy]  = useState(false)
  const isAdmin = getUser()?.role === 'admin'

  useEffect(() => { getPlanning().then(setState).catch(() => setState(null)) }, [])

  // Mono-period (family of one) or not loaded → render nothing.
  if (!state || state.available_periods.length <= 1) return null

  const disabled = !isAdmin || busy

  async function apply(period: PlanningPeriod, horizon: number) {
    if (!state) return
    const capped = Math.max(1, Math.min(horizon, state.max_horizon))
    setBusy(true)
    try {
      const next = await setPlanning(period, capped)
      setState(next)
      addToast(t('planning.saved'), '', 'success')
    } catch (e) {
      addToast(t('planning.save_error'), isApiError(e) ? e.detail : '', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
      <span style={{ color: 'var(--dim)' }}>{t('planning.period_label')}</span>
      <select
        value={state.period}
        disabled={disabled}
        onChange={e => apply(e.target.value as PlanningPeriod, state.horizon)}
        style={{
          background: 'var(--surface-2)', color: 'var(--text)',
          border: '1px solid var(--border)', borderRadius: 7,
          padding: '4px 8px', fontSize: 12, cursor: disabled ? 'default' : 'pointer',
        }}
      >
        {state.available_periods.map(p => (
          <option key={p} value={p}>{t(PERIOD_KEY[p])}</option>
        ))}
      </select>

      <span style={{ color: 'var(--dim)', marginLeft: 4 }}>{t('planning.horizon_label')}</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <button
          disabled={disabled || state.horizon <= 1}
          onClick={() => apply(state.period, state.horizon - 1)}
          style={stepBtn(disabled || state.horizon <= 1)}
        >−</button>
        <span style={{ minWidth: 62, textAlign: 'center', fontVariantNumeric: 'tabular-nums' }}>
          {state.horizon} {t(UNIT_KEY[state.period])}
        </span>
        <button
          disabled={disabled || state.horizon >= state.max_horizon}
          onClick={() => apply(state.period, state.horizon + 1)}
          style={stepBtn(disabled || state.horizon >= state.max_horizon)}
        >+</button>
      </div>
    </div>
  )
}

function stepBtn(off: boolean): React.CSSProperties {
  return {
    width: 22, height: 22, borderRadius: 6,
    border: '1px solid var(--border)', background: 'var(--surface-2)',
    color: off ? 'var(--dim)' : 'var(--text)',
    cursor: off ? 'default' : 'pointer', lineHeight: 1,
  }
}
```

- [ ] **Step 3: Mount it in `TopBar.tsx`**

Import at the top: `import PlanningControl from './PlanningControl'`. Then render it inside the right-hand actions `div` (the `<div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>` block), as the first child before the SKU-search button:

```tsx
        <PlanningControl />
```

- [ ] **Step 4: Default `useAutoSession` to the resolved session**

In `Frontend/src/hooks/useAutoSession.ts`, import `getPlanning`, and in the `load` callback prefer the resolver's `active_session_id` over "latest completed" when the user has not manually picked a session. Replace the `.then(list => { ... })` handler body with:

```typescript
      .then(async list => {
        setSessions(list)
        if (sessionIdRef.current) return
        let preferred = ''
        try { preferred = (await getPlanning()).active_session_id ?? '' } catch { /* fall back */ }
        if (!preferred) {
          const completed = list
            .filter(s => s.status === 'COMPLETED')
            .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
          preferred = completed.length ? completed[0].session_id : ''
        }
        if (preferred) setSessionId(preferred)
      })
```

> This keeps the existing "latest completed" behavior as the fallback (identical for family-less tenants, whose resolver returns exactly that session), and only changes the default to honor the active period for family tenants. Manual picks (`sessionIdRef.current`) are still never overwritten. Add `getPlanning` to the existing `import { getSessions } from '@/lib/api'` line.

- [ ] **Step 5: Typecheck**

Run: `cd Frontend && node ./node_modules/typescript/bin/tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add Frontend/src/components/layout/PlanningControl.tsx Frontend/src/components/layout/TopBar.tsx Frontend/src/i18n/translations.ts Frontend/src/hooks/useAutoSession.ts
git commit -m "feat(web): top-bar planning control + resolver-defaulted session"
```

---

### Task 7: Full regression + plan doc note

**Files:**
- Modify: `docs/plan_general_faro_2026-07-18.md` (extend the multi-period note)

- [ ] **Step 1: Full backend suite**

Run: `cd backend && python -m pytest tests/ -q`
Expected: 0 failures beyond the known machine-load-flaky `test_stress.py::test_login_responds_under_2s` (re-run it alone to confirm). Watch specifically for alert-loop tests (`test_demo_and_alerts.py`) — the resolver swap must not change which session a family-less tenant alerts on.

- [ ] **Step 2: Full frontend typecheck**

Run: `cd Frontend && node ./node_modules/typescript/bin/tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Note it in the plan doc**

Extend the 2026-07-23 multi-period line in `docs/plan_general_faro_2026-07-18.md`:
`Multi-period planning Phase B: active {period, horizon} setting in tenants.settings.planning, planning_service (get/set/resolve_active_session), GET/PUT /planning (admin-only writes), daily alert loop reads through the resolver, and a top-bar period/horizon control (admin-editable, read-only otherwise, hidden for mono-period tenants). Phase C (per-period coverage/semáforo + horizon windowing) pending — spec 2026-07-23-multi-period-planning-design.md.`

- [ ] **Step 4: Commit**

```bash
git add docs/plan_general_faro_2026-07-18.md
git commit -m "docs: note multi-period planning Phase B landed"
```

## Out of scope (this phase)

- Per-period coverage/semáforo reinterpretation and horizon windowing (Phase C) — the resolver returns the right session, but reinterpreting its `daily_demand` as per-period demand and trimming the forecast series to `horizon` buckets is Phase C.
- Re-forecasting beyond the pre-computed reach (the stepper is capped at `max_horizon`).
- Per-user (vs per-tenant) period preference; mixing periods across screens simultaneously.
- Entitlement-gating multi-period by plan (it is core UX in v1).
