# Plan-Based Entitlements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split product functionality by subscription plan (Starter / Professional / Enterprise) — both feature gating and numeric limits — derived from a single `tenant.plan` attribute, with a 14-day Starter trial that drops to read-only on expiry.

**Architecture:** A central code catalog (`backend/entitlements/plans.py`) is the single source of truth mapping each plan to its numeric limits and its set of feature keys. A thin service layer answers `has_feature` / `tenant_limits` / `is_read_only`. FastAPI dependency guards enforce feature access and trial read-only at the API boundary; the frontend reads a `GET /entitlements` snapshot to gate UI (UX only — the backend is the real gate).

**Tech Stack:** Python 3, FastAPI, psycopg2, PostgreSQL, pytest; Next.js 14 / TypeScript frontend.

## Global Constraints

- All code (identifiers, comments, docstrings, test names, commit messages) is **English**. Only end-user copy may be Spanish. (CLAUDE.md)
- No ML/pandas in `backend/`; no business logic in `Frontend/`.
- `settings.testing_mode == True` must **bypass** every entitlement check (mirrors existing `check_session_quota`). The suite runs with `TESTING_MODE=true`; enforcement tests must `monkeypatch settings.testing_mode = False` themselves.
- Tests assert **state changes via direct DB queries**, not just status codes. Every mutating endpoint keeps its **permission pair** (viewer 403 + state unchanged AND analyst success).
- Migrations are **idempotent** (`ADD COLUMN IF NOT EXISTS`), appended to `_MIGRATIONS` in `backend/db/migrations.py`.
- Plan values are exactly `'starter'`, `'professional'`, `'enterprise'`. No `'free'` tier.
- Numeric limits: Starter 500 SKUs / 2 users / 1 location; Professional 5,000 / 10 / 5; Enterprise unlimited (`None`).

---

## File Structure

**Create:**
- `backend/entitlements/__init__.py` — package marker.
- `backend/entitlements/plans.py` — `Feature` enum, `PlanDef`, `PLAN_CATALOG`.
- `backend/entitlements/service.py` — `has_feature`, `tenant_limits`, `trial_state`, `is_read_only`, `required_plans_for`.
- `backend/entitlements/guards.py` — `require_feature`, `require_active_analyst`.
- `backend/api/v1/entitlements.py` — `GET /entitlements` router.
- `backend/tests/test_entitlements.py` — catalog + service + guard + endpoint tests.
- `Frontend/src/lib/entitlements.tsx` — `EntitlementsProvider` + `useEntitlements` hook.

**Modify:**
- `backend/db/migrations.py` — add `trial_ends_at`; convert `'free'` → `'enterprise'`.
- `backend/tenants/service.py` — `create_tenant` sets `starter` + trial; `get_quota`/`check_session_quota` read the catalog.
- `backend/auth/guards.py` — compose trial read-only into `require_analyst_or_above`.
- `backend/api/v1/sessions.py` — re-enable session-quota check.
- `backend/api/v1/users.py` — enforce user limit on create.
- `backend/api/v1/inventory.py` — endpoint-level feature guards + warehouse (location) limit + ABC-XYZ field omission.
- `backend/api/v1/{ai_insights,analyst,chats,documents,api_keys,webhooks,schedule}.py` — router-level feature guards.
- `backend/inventory/service.py` — gate the WhatsApp block in `run_daily_inventory_alerts`.
- `backend/main.py` — register `entitlements.router`.
- `Frontend/src/lib/api.ts` — `getEntitlements()` + `Entitlements` type.
- `Frontend/src/app/layout.tsx` (or the authed shell) — mount `EntitlementsProvider`; nav lock + read-only banner.

---

## Task 1: Plan catalog (source of truth)

**Files:**
- Create: `backend/entitlements/__init__.py`
- Create: `backend/entitlements/plans.py`
- Test: `backend/tests/test_entitlements.py`

**Interfaces:**
- Produces:
  - `class Feature(str, Enum)` with members: `SEMAPHORE, PO_GENERATION, RECEPTION, SUPPLIERS, REPORTS, EMAIL_ALERTS, ABC_XYZ, WHATSAPP_ALERTS, AI_ANALYST, DOCUMENTS_RAG, EVENT_SIMULATOR, MILP_OPTIMIZER, SCHEDULED_REPORTS, MULTI_LOCATION, BOM, API_ACCESS, WEBHOOKS`.
  - `@dataclass(frozen=True) class PlanDef` with fields `max_skus, max_users, max_locations, max_sessions, max_concurrent_jobs, max_dataset_size_mb` (each `int | None`) and `features: frozenset[Feature]`.
  - `PLAN_CATALOG: dict[str, PlanDef]` keyed by `'starter' | 'professional' | 'enterprise'`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_entitlements.py`:

```python
from backend.entitlements.plans import Feature, PLAN_CATALOG


def test_catalog_has_three_plans():
    assert set(PLAN_CATALOG) == {"starter", "professional", "enterprise"}


def test_each_tier_is_a_superset_of_the_lower():
    starter = PLAN_CATALOG["starter"].features
    pro = PLAN_CATALOG["professional"].features
    ent = PLAN_CATALOG["enterprise"].features
    assert starter <= pro <= ent


def test_starter_excludes_paid_features_but_includes_core():
    starter = PLAN_CATALOG["starter"].features
    assert Feature.SEMAPHORE in starter
    assert Feature.EMAIL_ALERTS in starter
    assert Feature.WHATSAPP_ALERTS not in starter
    assert Feature.API_ACCESS not in starter


def test_enterprise_only_features():
    pro = PLAN_CATALOG["professional"].features
    assert Feature.API_ACCESS not in pro
    assert Feature.BOM not in pro
    assert Feature.WEBHOOKS not in pro
    ent = PLAN_CATALOG["enterprise"].features
    assert {Feature.API_ACCESS, Feature.BOM, Feature.WEBHOOKS} <= ent


def test_numeric_limits():
    assert PLAN_CATALOG["starter"].max_skus == 500
    assert PLAN_CATALOG["professional"].max_skus == 5000
    assert PLAN_CATALOG["enterprise"].max_skus is None
    assert PLAN_CATALOG["starter"].max_users == 2
    assert PLAN_CATALOG["starter"].max_locations == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_entitlements.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.entitlements'`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/entitlements/__init__.py` (empty file).

Create `backend/entitlements/plans.py`:

```python
"""Plan catalog — the single source of truth for entitlements.

Each plan maps to numeric limits (None means unlimited) and a set of feature
keys. Tiers are composed by union so a higher tier can never lose a lower
tier's feature.
"""

from dataclasses import dataclass
from enum import Enum


class Feature(str, Enum):
    # Core — every plan
    SEMAPHORE = "semaphore"
    PO_GENERATION = "po_generation"
    RECEPTION = "reception"
    SUPPLIERS = "suppliers"
    REPORTS = "reports"
    EMAIL_ALERTS = "email_alerts"
    # Professional
    ABC_XYZ = "abc_xyz"
    WHATSAPP_ALERTS = "whatsapp_alerts"
    AI_ANALYST = "ai_analyst"
    DOCUMENTS_RAG = "documents_rag"
    EVENT_SIMULATOR = "event_simulator"
    MILP_OPTIMIZER = "milp_optimizer"
    SCHEDULED_REPORTS = "scheduled_reports"
    MULTI_LOCATION = "multi_location"
    # Enterprise
    BOM = "bom"
    API_ACCESS = "api_access"
    WEBHOOKS = "webhooks"


@dataclass(frozen=True)
class PlanDef:
    max_skus: int | None
    max_users: int | None
    max_locations: int | None
    max_sessions: int | None
    max_concurrent_jobs: int | None
    max_dataset_size_mb: int | None
    features: frozenset[Feature]


_CORE = frozenset({
    Feature.SEMAPHORE, Feature.PO_GENERATION, Feature.RECEPTION,
    Feature.SUPPLIERS, Feature.REPORTS, Feature.EMAIL_ALERTS,
})

_PRO_EXTRA = frozenset({
    Feature.ABC_XYZ, Feature.WHATSAPP_ALERTS, Feature.AI_ANALYST,
    Feature.DOCUMENTS_RAG, Feature.EVENT_SIMULATOR, Feature.MILP_OPTIMIZER,
    Feature.SCHEDULED_REPORTS, Feature.MULTI_LOCATION,
})

_ENT_EXTRA = frozenset({Feature.BOM, Feature.API_ACCESS, Feature.WEBHOOKS})

PLAN_CATALOG: dict[str, PlanDef] = {
    "starter": PlanDef(
        max_skus=500, max_users=2, max_locations=1,
        max_sessions=20, max_concurrent_jobs=2, max_dataset_size_mb=200,
        features=_CORE,
    ),
    "professional": PlanDef(
        max_skus=5000, max_users=10, max_locations=5,
        max_sessions=100, max_concurrent_jobs=4, max_dataset_size_mb=500,
        features=_CORE | _PRO_EXTRA,
    ),
    "enterprise": PlanDef(
        max_skus=None, max_users=None, max_locations=None,
        max_sessions=None, max_concurrent_jobs=8, max_dataset_size_mb=2000,
        features=_CORE | _PRO_EXTRA | _ENT_EXTRA,
    ),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_entitlements.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/entitlements/__init__.py backend/entitlements/plans.py backend/tests/test_entitlements.py
git commit -m "feat(entitlements): plan catalog with feature keys and numeric limits"
```

---

## Task 2: Entitlements service

**Files:**
- Create: `backend/entitlements/service.py`
- Test: `backend/tests/test_entitlements.py` (append)

**Interfaces:**
- Consumes: `Feature`, `PlanDef`, `PLAN_CATALOG` from Task 1.
- Produces (all take a `tenant: dict` — the row from `tenants/service.get_tenant`):
  - `get_plan_def(plan: str) -> PlanDef` (unknown plan falls back to `'starter'`).
  - `has_feature(tenant: dict, feature: Feature) -> bool`
  - `tenant_limits(tenant: dict) -> dict` — catalog limits merged with `tenant["quota"]` overrides (override wins when present).
  - `trial_state(tenant: dict) -> str` — one of `'active'`, `'trialing'`, `'expired'`.
  - `is_read_only(tenant: dict) -> bool` — `True` iff `trial_state == 'expired'`.
  - `required_plans_for(feature: Feature) -> list[str]` — plans (in catalog order) whose features include `feature`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_entitlements.py`:

```python
from datetime import datetime, timedelta, timezone

from backend.entitlements import service as ent


def _tenant(plan="starter", trial_ends_at=None, quota=None):
    return {"id": "ten_x", "plan": plan, "trial_ends_at": trial_ends_at,
            "quota": quota or {}}


def test_has_feature_by_plan():
    assert ent.has_feature(_tenant("professional"), Feature.WHATSAPP_ALERTS)
    assert not ent.has_feature(_tenant("starter"), Feature.WHATSAPP_ALERTS)


def test_unknown_plan_falls_back_to_starter():
    assert ent.get_plan_def("garbage").max_skus == 500


def test_tenant_limits_merge_override():
    limits = ent.tenant_limits(_tenant("starter", quota={"max_skus": 999}))
    assert limits["max_skus"] == 999          # override wins
    assert limits["max_users"] == 2           # catalog default preserved


def test_trial_state_and_read_only():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert ent.trial_state(_tenant(trial_ends_at=None)) == "active"
    assert ent.trial_state(_tenant(trial_ends_at=future)) == "trialing"
    assert ent.trial_state(_tenant(trial_ends_at=past)) == "expired"
    assert ent.is_read_only(_tenant(trial_ends_at=past)) is True
    assert ent.is_read_only(_tenant(trial_ends_at=future)) is False


def test_required_plans_for():
    assert ent.required_plans_for(Feature.WHATSAPP_ALERTS) == ["professional", "enterprise"]
    assert ent.required_plans_for(Feature.API_ACCESS) == ["enterprise"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_entitlements.py -k "service or trial or limits or required or has_feature or unknown" -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.entitlements.service'`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/entitlements/service.py`:

```python
"""Entitlement queries over a tenant row + the plan catalog."""

from datetime import datetime, timezone

from backend.entitlements.plans import Feature, PlanDef, PLAN_CATALOG

_LIMIT_FIELDS = (
    "max_skus", "max_users", "max_locations",
    "max_sessions", "max_concurrent_jobs", "max_dataset_size_mb",
)


def get_plan_def(plan: str) -> PlanDef:
    return PLAN_CATALOG.get(plan or "", PLAN_CATALOG["starter"])


def has_feature(tenant: dict, feature: Feature) -> bool:
    return feature in get_plan_def(tenant.get("plan", "")).features


def tenant_limits(tenant: dict) -> dict:
    plan = get_plan_def(tenant.get("plan", ""))
    override = tenant.get("quota") or {}
    limits = {}
    for field in _LIMIT_FIELDS:
        limits[field] = override[field] if field in override else getattr(plan, field)
    return limits


def trial_state(tenant: dict) -> str:
    ends = tenant.get("trial_ends_at")
    if ends is None:
        return "active"
    if isinstance(ends, str):
        ends = datetime.fromisoformat(ends)
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    return "trialing" if ends >= datetime.now(timezone.utc) else "expired"


def is_read_only(tenant: dict) -> bool:
    return trial_state(tenant) == "expired"


def required_plans_for(feature: Feature) -> list[str]:
    return [name for name, d in PLAN_CATALOG.items() if feature in d.features]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_entitlements.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add backend/entitlements/service.py backend/tests/test_entitlements.py
git commit -m "feat(entitlements): service for feature/limit/trial queries"
```

---

## Task 3: Migration + tenant creation

**Files:**
- Modify: `backend/db/migrations.py` (append to `_MIGRATIONS`, ends near line 251+)
- Modify: `backend/tenants/service.py:11-51` (quota defaults + `create_tenant`)
- Modify: `backend/tenants/service.py:58-69` (`get_quota`, `check_session_quota`)
- Test: `backend/tests/test_entitlements.py` (append)

**Interfaces:**
- Consumes: `tenant_limits`, `PLAN_CATALOG` from Tasks 1-2.
- Produces:
  - `tenants` rows now carry `trial_ends_at` and `plan in {'starter','professional','enterprise'}`.
  - `create_tenant(name)` returns a tenant with `plan='starter'` and `trial_ends_at ≈ now + 14 days`.
  - `check_session_quota(tenant_id) -> bool` uses `tenant_limits(...)["max_sessions"]`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_entitlements.py`:

```python
from backend.tenants import service as tenant_svc


def test_create_tenant_starts_on_starter_trial(db):  # db fixture ensures schema
    t = tenant_svc.create_tenant("Acme Trial Co")
    assert t["plan"] == "starter"
    assert t["trial_ends_at"] is not None
    # ~14 days out
    from datetime import datetime, timezone
    ends = t["trial_ends_at"]
    delta = ends - datetime.now(timezone.utc)
    assert timedelta(days=13) < delta < timedelta(days=15)
```

> If a `db` fixture does not already exist in `conftest.py`, use the existing DB-backed fixture pattern (e.g. `test_tenant`) — the point is that `migrations.run_all()` has executed so the `trial_ends_at` column exists. Adjust the fixture name to match `conftest.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_entitlements.py -k create_tenant_starts -q`
Expected: FAIL — `create_tenant` still inserts `plan='free'` and no `trial_ends_at` (KeyError or assertion on `plan == 'starter'`).

- [ ] **Step 3: Write minimal implementation**

In `backend/db/migrations.py`, append two entries to the `_MIGRATIONS` list (inside the trailing `[ ... ]`):

```python
    ("add_tenants_trial_ends_at",
     "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ"),
    ("migrate_free_plan_to_enterprise",
     "UPDATE tenants SET plan = 'enterprise', trial_ends_at = NULL "
     "WHERE plan = 'free'"),
```

In `backend/tenants/service.py`, replace the `_DEFAULT_QUOTA` block and `create_tenant` INSERTs. Replace lines 11-16 with:

```python
from datetime import datetime, timedelta, timezone

_TRIAL_DAYS = 14
```

Delete the old `_DEFAULT_QUOTA` dict. Then in `create_tenant`, change both INSERT statements so the tenant starts on a Starter trial with an **empty** quota override (`'{}'`), e.g.:

```python
def create_tenant(name: str) -> dict:
    tenant_id = generate_id("ten")
    base = _slugify(name)
    trial_ends = datetime.now(timezone.utc) + timedelta(days=_TRIAL_DAYS)
    candidates = [base] + [f"{base}-{secrets.token_hex(2)}" for _ in range(4)]
    last_exc: Optional[Exception] = None
    for slug in candidates:
        try:
            execute(
                """INSERT INTO tenants (id, name, slug, plan, status, quota, settings, trial_ends_at, created_at)
                   VALUES (%s, %s, %s, 'starter', 'active', '{}', '{}', %s, NOW())""",
                (tenant_id, name, slug, trial_ends),
            )
            return get_tenant(tenant_id)
        except psycopg2.errors.UniqueViolation as exc:
            last_exc = exc
    execute(
        """INSERT INTO tenants (id, name, slug, plan, status, quota, settings, trial_ends_at, created_at)
           VALUES (%s, %s, %s, 'starter', 'active', '{}', '{}', %s, NOW())""",
        (tenant_id, name, f"{base}-{tenant_id[-8:]}", trial_ends),
    )
    return get_tenant(tenant_id)
```

Rewrite `get_quota` and `check_session_quota` to use the catalog:

```python
def get_quota(tenant_id: str) -> dict:
    from backend.entitlements.service import tenant_limits
    tenant = get_tenant(tenant_id)
    return tenant_limits(tenant) if tenant else tenant_limits({"plan": "starter", "quota": {}})


def check_session_quota(tenant_id: str) -> bool:
    if settings.testing_mode:
        return True
    from backend.sessions import service as session_svc
    max_sessions = get_quota(tenant_id)["max_sessions"]
    if max_sessions is None:
        return True
    return session_svc.count_sessions(tenant_id) < max_sessions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_entitlements.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/db/migrations.py backend/tenants/service.py backend/tests/test_entitlements.py
git commit -m "feat(entitlements): starter-trial tenant creation + trial_ends_at migration"
```

---

## Task 4: Feature guard (`require_feature`)

**Files:**
- Create: `backend/entitlements/guards.py`
- Test: `backend/tests/test_entitlements.py` (append — uses a throwaway app)

**Interfaces:**
- Consumes: `Feature`, `has_feature`, `required_plans_for`, `get_tenant`, `CurrentUser`, `get_current_user`, `settings`.
- Produces:
  - `require_feature(feature: Feature)` → a FastAPI dependency callable that returns `CurrentUser` on success and raises `HTTPException(403, {code, feature, current_plan, required_plans})` otherwise. Bypasses when `settings.testing_mode`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_entitlements.py`:

```python
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


def _mini_app():
    from backend.auth.guards import CurrentUser
    from backend.entitlements.guards import require_feature
    app = FastAPI()

    @app.get("/whatsapp-thing")
    def thing(user: CurrentUser = Depends(require_feature(Feature.WHATSAPP_ALERTS))):
        return {"ok": True}

    return app


def test_require_feature_blocks_starter(monkeypatch, make_tenant_user_headers):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    headers = make_tenant_user_headers(plan="starter")
    r = TestClient(_mini_app()).get("/whatsapp-thing", headers=headers)
    assert r.status_code == 403
    body = r.json()["detail"]
    assert body["code"] == "PLAN_UPGRADE_REQUIRED"
    assert body["feature"] == "whatsapp_alerts"
    assert body["current_plan"] == "starter"
    assert body["required_plans"] == ["professional", "enterprise"]


def test_require_feature_allows_professional(monkeypatch, make_tenant_user_headers):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    headers = make_tenant_user_headers(plan="professional")
    r = TestClient(_mini_app()).get("/whatsapp-thing", headers=headers)
    assert r.status_code == 200
```

> **Fixture note:** add a `make_tenant_user_headers` fixture to `backend/tests/conftest.py` that (1) creates a tenant via `tenant_svc.create_tenant`, (2) `UPDATE tenants SET plan=%s, trial_ends_at=NULL WHERE id=%s`, (3) creates an analyst user, and (4) returns `{"Authorization": f"Bearer {access_token}"}`. Reuse the existing token-minting helper the other fixtures use. If a similar factory already exists, extend it with a `plan` argument instead of duplicating.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_entitlements.py -k require_feature -q`
Expected: FAIL — `backend.entitlements.guards` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `backend/entitlements/guards.py`:

```python
"""FastAPI dependency guards that enforce plan entitlements."""

from fastapi import Depends, HTTPException, status

from backend.auth.guards import CurrentUser, get_current_user
from backend.config import settings
from backend.entitlements.plans import Feature
from backend.entitlements.service import has_feature, required_plans_for
from backend.tenants.service import get_tenant


def require_feature(feature: Feature):
    def guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if settings.testing_mode:
            return user
        tenant = get_tenant(user.tenant_id) or {}
        if not has_feature(tenant, feature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PLAN_UPGRADE_REQUIRED",
                    "feature": feature.value,
                    "current_plan": tenant.get("plan", "starter"),
                    "required_plans": required_plans_for(feature),
                },
            )
        return user

    return guard
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_entitlements.py -k require_feature -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/entitlements/guards.py backend/tests/test_entitlements.py backend/tests/conftest.py
git commit -m "feat(entitlements): require_feature dependency guard"
```

---

## Task 5: Trial read-only guard composed into mutations

**Files:**
- Modify: `backend/entitlements/guards.py` (add `require_active_analyst`)
- Modify: `backend/auth/guards.py:72` (`require_analyst_or_above` delegates to it)
- Test: `backend/tests/test_entitlements.py` (append)

**Interfaces:**
- Consumes: `require_role`, `CurrentUser`, `is_read_only`, `get_tenant`, `settings`.
- Produces:
  - `require_active_analyst(user) -> CurrentUser` — role check (`admin`/`analyst`) **plus** trial read-only check. Raises `HTTPException(403, {code: "TRIAL_EXPIRED", ...})` when `is_read_only` and not testing.
  - `backend.auth.guards.require_analyst_or_above` now points at this composite so **all** existing mutating endpoints inherit the read-only block with no per-endpoint edits.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_entitlements.py`:

```python
def test_expired_trial_blocks_mutation_but_allows_read(
    monkeypatch, make_tenant_user_headers, db
):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.main import app
    from backend.tenants import service as tenant_svc
    from backend.db.connection import execute, query_one

    headers, tenant_id = make_tenant_user_headers(
        plan="starter", expired_trial=True, return_tenant_id=True
    )
    client = TestClient(app)

    # read still works
    assert client.get("/api/v1/sessions", headers=headers).status_code == 200

    # mutation blocked with TRIAL_EXPIRED, and no session row created
    before = query_one(
        "SELECT COUNT(*) AS c FROM sessions WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    r = client.post("/api/v1/sessions", headers=headers, json={"name": "X"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "TRIAL_EXPIRED"
    after = query_one(
        "SELECT COUNT(*) AS c FROM sessions WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert after == before
```

> Extend `make_tenant_user_headers` to accept `expired_trial: bool` (sets `trial_ends_at = now - 1 day`) and `return_tenant_id: bool`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_entitlements.py -k expired_trial -q`
Expected: FAIL — mutation returns 201 (no read-only enforcement yet).

- [ ] **Step 3: Write minimal implementation**

Add to `backend/entitlements/guards.py`:

```python
from backend.auth.guards import require_role
from backend.entitlements.service import is_read_only


def require_active_analyst(
    user: CurrentUser = Depends(require_role("admin", "analyst")),
) -> CurrentUser:
    if settings.testing_mode:
        return user
    tenant = get_tenant(user.tenant_id) or {}
    if is_read_only(tenant):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "TRIAL_EXPIRED",
                "current_plan": tenant.get("plan", "starter"),
                "trial_ends_at": (
                    tenant["trial_ends_at"].isoformat()
                    if tenant.get("trial_ends_at") else None
                ),
            },
        )
    return user
```

In `backend/auth/guards.py`, replace line 72 so the public name resolves to the composite (keep `require_role` and `require_admin` as-is):

```python
require_admin = require_role("admin")

def require_analyst_or_above(
    user: "CurrentUser" = Depends(  # noqa: F821 (imported lazily below)
        require_role("admin", "analyst")
    ),
) -> "CurrentUser":
    # Delegates to the entitlements guard so trial read-only is enforced
    # on every mutating endpoint. Imported lazily to avoid a circular import
    # (entitlements.guards imports from this module).
    from backend.entitlements.guards import require_active_analyst as _active
    return _active(user)
```

> **Circular-import caution:** `entitlements/guards.py` imports `require_role` and `CurrentUser` from `auth/guards.py`; `auth/guards.py` imports `require_active_analyst` **inside the function body** (lazy) — never at module top level. If Python still complains, invert: keep `require_analyst_or_above = require_active_analyst` as a direct alias defined in `auth/guards.py` after a lazy import at the bottom of the module. Verify with `python -c "import backend.main"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_entitlements.py -k expired_trial -q && python -c "import backend.main"`
Expected: PASS and clean import (no circular-import error).

- [ ] **Step 5: Run the full suite to prove nothing regressed**

Run: `cd backend && python -m pytest tests/ -q`
Expected: PASS (existing tests unaffected — `testing_mode=true` bypasses the new check).

- [ ] **Step 6: Commit**

```bash
git add backend/entitlements/guards.py backend/auth/guards.py backend/tests/test_entitlements.py
git commit -m "feat(entitlements): trial read-only enforced on all mutations via require_analyst_or_above"
```

---

## Task 6: Numeric limit enforcement (sessions, users, SKUs, locations)

**Files:**
- Modify: `backend/api/v1/sessions.py:23-33` (re-enable session quota)
- Modify: `backend/api/v1/users.py` (user-count limit on create)
- Modify: `backend/api/v1/inventory.py` (SKU-count limit on `POST /bulk` and stock create; location limit on `POST /warehouses`)
- Modify: `backend/entitlements/service.py` (add `enforce_limit` helper)
- Test: `backend/tests/test_entitlements.py` (append)

**Interfaces:**
- Consumes: `tenant_limits`, `get_tenant`, `settings`.
- Produces:
  - `enforce_limit(tenant_id: str, limit_key: str, current: int, adding: int = 1) -> None` in `entitlements/service.py` — raises `HTTPException(403, {code:"PLAN_LIMIT_REACHED", limit, current, max})` when `current + adding > max` (and `max is not None`, and not testing). No-op otherwise.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_entitlements.py`:

```python
def test_user_limit_on_starter(monkeypatch, make_tenant_user_headers, db):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.main import app
    from backend.db.connection import query_one

    headers, tenant_id = make_tenant_user_headers(
        plan="starter", role="admin", return_tenant_id=True
    )
    client = TestClient(app)
    # tenant already has 1 admin user; Starter allows 2 → first create OK
    r1 = client.post("/api/v1/users", headers=headers,
                     json={"email": "u2@x.com", "full_name": "U2",
                           "password": "pw12345678", "role": "analyst"})
    assert r1.status_code in (200, 201)

    before = query_one("SELECT COUNT(*) AS c FROM users WHERE tenant_id=%s",
                       (tenant_id,))["c"]
    # 3rd user exceeds Starter's max_users=2 → blocked, count unchanged
    r2 = client.post("/api/v1/users", headers=headers,
                     json={"email": "u3@x.com", "full_name": "U3",
                           "password": "pw12345678", "role": "analyst"})
    assert r2.status_code == 403
    assert r2.json()["detail"]["code"] == "PLAN_LIMIT_REACHED"
    after = query_one("SELECT COUNT(*) AS c FROM users WHERE tenant_id=%s",
                      (tenant_id,))["c"]
    assert after == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_entitlements.py -k user_limit -q`
Expected: FAIL — 3rd user is created (201), no limit enforced.

- [ ] **Step 3: Write minimal implementation**

Add to `backend/entitlements/service.py`:

```python
def enforce_limit(tenant_id: str, limit_key: str, current: int, adding: int = 1) -> None:
    from fastapi import HTTPException, status
    from backend.config import settings
    from backend.tenants.service import get_tenant
    if settings.testing_mode:
        return
    tenant = get_tenant(tenant_id) or {"plan": "starter", "quota": {}}
    max_allowed = tenant_limits(tenant)[limit_key]
    if max_allowed is not None and current + adding > max_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "PLAN_LIMIT_REACHED", "limit": limit_key,
                    "current": current, "max": max_allowed},
        )
```

In `backend/api/v1/users.py`, inside the create-user handler, **before** inserting, add (using the existing user-count query — add `count_users(tenant_id)` to `users/service.py` if absent):

```python
from backend.users.service import count_users
from backend.entitlements.service import enforce_limit
enforce_limit(user.tenant_id, "max_users", count_users(user.tenant_id))
```

`count_users` in `backend/users/service.py`:

```python
def count_users(tenant_id: str) -> int:
    row = query_one("SELECT COUNT(*) AS c FROM users WHERE tenant_id = %s", (tenant_id,))
    return row["c"] if row else 0
```

In `backend/api/v1/sessions.py`, replace the commented block at lines 28-29 with a live check:

```python
    from backend.tenants.service import check_session_quota
    if not check_session_quota(user.tenant_id):
        raise HTTPException(
            status_code=403,
            detail={"code": "PLAN_LIMIT_REACHED", "limit": "max_sessions"},
        )
```

In `backend/api/v1/inventory.py`:
- On `POST /bulk` (line ~125): after parsing rows and **before** inserting, compute how many rows introduce **new** SKUs (`new_skus`), then
  `enforce_limit(user.tenant_id, "max_skus", current_sku_count, adding=new_skus)` where `current_sku_count = SELECT COUNT(*) FROM inventory_stock WHERE tenant_id=%s`.
- On `PUT /stock/{sku}` when it creates a not-yet-existing SKU: `enforce_limit(user.tenant_id, "max_skus", current_sku_count, adding=1)`.
- On `POST /warehouses` (line ~1235): `enforce_limit(user.tenant_id, "max_locations", current_warehouse_count, adding=1)`.

> Use the existing count queries where present; add a small `count_*` helper in the relevant `service.py` if none exists, mirroring `count_users`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_entitlements.py -k user_limit -q`
Expected: PASS.

- [ ] **Step 5: Full suite**

Run: `cd backend && python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/entitlements/service.py backend/api/v1/users.py backend/api/v1/sessions.py backend/api/v1/inventory.py backend/users/service.py backend/tests/test_entitlements.py
git commit -m "feat(entitlements): enforce SKU/user/session/location numeric limits"
```

---

## Task 7: Wire feature guards onto routers + WhatsApp loop

**Files:**
- Modify: `backend/api/v1/ai_insights.py`, `analyst.py`, `chats.py` → `AI_ANALYST`
- Modify: `backend/api/v1/documents.py` → `DOCUMENTS_RAG`
- Modify: `backend/api/v1/api_keys.py` → `API_ACCESS`
- Modify: `backend/api/v1/webhooks.py` → `WEBHOOKS`
- Modify: `backend/api/v1/schedule.py` → `SCHEDULED_REPORTS`
- Modify: `backend/api/v1/inventory.py` → per-endpoint: `events/*`→`EVENT_SIMULATOR`, `bom/*`→`BOM`, `warehouses/*`→`MULTI_LOCATION`, MILP optimize→`MILP_OPTIMIZER`, `alerts/send-now`→`WHATSAPP_ALERTS`; ABC-XYZ field omission on dashboard/scorecard reads.
- Modify: `backend/inventory/service.py:1731-1738` (gate WhatsApp block in daily loop)
- Test: `backend/tests/test_entitlements.py` (append)

**Interfaces:**
- Consumes: `require_feature`, `has_feature`, `get_tenant` from Tasks 4/2.
- Produces: gated endpoints returning 403 `PLAN_UPGRADE_REQUIRED` for insufficient plans; the daily loop skips WhatsApp for tenants lacking the feature.

**Wiring pattern** — router-wide gate. Prefer the router-level `dependencies=` argument so every route inherits it (add alongside, not replacing, the existing auth dependency). Example for `documents.py`:

```python
from backend.entitlements.guards import require_feature
from backend.entitlements.plans import Feature

router = APIRouter(
    prefix="/documents", tags=["documents"],
    dependencies=[Depends(require_feature(Feature.DOCUMENTS_RAG))],
)
```

For **endpoint-level** gates in `inventory.py`, add the dependency to that route's signature, e.g.:

```python
@router.post("/events/simulate")
def simulate_events(
    body: EventSimRequest,
    user: CurrentUser = Depends(require_feature(Feature.EVENT_SIMULATOR)),
):
    ...
```

For the **WhatsApp daily loop** in `backend/inventory/service.py`, wrap the existing block (lines 1731-1738):

```python
            from backend.entitlements.service import has_feature
            from backend.entitlements.plans import Feature
            if has_feature(tenant, Feature.WHATSAPP_ALERTS):
                from backend.notifications.whatsapp import build_inventory_alert_text, send_whatsapp
                numbers = get_tenant_admin_whatsapps(tid)
                if numbers:
                    text = build_inventory_alert_text(critical[:10], warning[:5], inventory_url)
                    for number in numbers:
                        send_whatsapp(number, text)
```

> `tenant` here is the loop variable from `get_tenants_with_active_sessions()`. Confirm it carries `plan`/`trial_ends_at`; if it only has `tenant_id`, fetch `get_tenant(tid)` first.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_entitlements.py`:

```python
import pytest


@pytest.mark.parametrize("path,method", [
    ("/api/v1/documents", "get"),
    ("/api/v1/api-keys", "get"),
])
def test_router_feature_gate_blocks_starter(
    monkeypatch, make_tenant_user_headers, path, method
):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.main import app
    headers = make_tenant_user_headers(plan="starter")
    r = getattr(TestClient(app), method)(path, headers=headers)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "PLAN_UPGRADE_REQUIRED"


def test_router_feature_gate_allows_enterprise(
    monkeypatch, make_tenant_user_headers
):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.main import app
    headers = make_tenant_user_headers(plan="enterprise")
    r = TestClient(app).get("/api/v1/api-keys", headers=headers)
    assert r.status_code != 403
```

> Confirm the exact prefixes (`/api/v1/api-keys` vs `/api/v1/api_keys`) against `main.py` / each router's `prefix`; adjust the parametrized paths to match reality.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_entitlements.py -k router_feature_gate -q`
Expected: FAIL — endpoints return 200, not 403.

- [ ] **Step 3: Write minimal implementation**

Apply the wiring pattern above to each listed router and the inventory endpoints, and gate the WhatsApp loop block. For ABC-XYZ, in the dashboard/scorecard read handlers, omit the ABC/XYZ classification key from each item when `not has_feature(get_tenant(user.tenant_id), Feature.ABC_XYZ)` (skip the omission entirely in `testing_mode`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_entitlements.py -k router_feature_gate -q`
Expected: PASS.

- [ ] **Step 5: Full suite (guards must not break existing feature tests)**

Run: `cd backend && python -m pytest tests/ -q`
Expected: PASS — existing AI/documents/api-key tests run under `testing_mode=true`, which bypasses the gates.

- [ ] **Step 6: Commit**

```bash
git add backend/api/v1/ backend/inventory/service.py backend/tests/test_entitlements.py
git commit -m "feat(entitlements): gate AI/documents/api/webhooks/schedule + inventory features by plan"
```

---

## Task 8: `GET /entitlements` endpoint

**Files:**
- Create: `backend/api/v1/entitlements.py`
- Modify: `backend/main.py` (register router)
- Test: `backend/tests/test_entitlements.py` (append)

**Interfaces:**
- Consumes: `get_current_user`, `get_tenant`, `get_plan_def`, `tenant_limits`, `trial_state`, `is_read_only`, `Feature`.
- Produces: `GET /api/v1/entitlements` → `{plan, trial: {state, ends_at}, limits, features: {<feature_value>: bool}, read_only}` for the caller's tenant. Read endpoint (`get_current_user`, all roles).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_entitlements.py`:

```python
def test_entitlements_endpoint_reports_plan(monkeypatch, make_tenant_user_headers):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.main import app
    headers = make_tenant_user_headers(plan="professional")
    r = TestClient(app).get("/api/v1/entitlements", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"] if "data" in r.json() else r.json()
    assert data["plan"] == "professional"
    assert data["features"]["whatsapp_alerts"] is True
    assert data["features"]["api_access"] is False
    assert data["limits"]["max_skus"] == 5000
    assert data["read_only"] is False
```

> Match the response envelope to the repo convention (`ok(...)` wrapper used by other routers). Adjust the `data` unwrapping accordingly.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_entitlements.py -k entitlements_endpoint -q`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Write minimal implementation**

Create `backend/api/v1/entitlements.py`:

```python
from fastapi import APIRouter, Depends

from backend.auth.guards import CurrentUser, get_current_user
from backend.entitlements.plans import Feature, PLAN_CATALOG
from backend.entitlements.service import (
    get_plan_def, is_read_only, tenant_limits, trial_state,
)
from backend.schemas.common import ok
from backend.tenants.service import get_tenant

router = APIRouter(prefix="/entitlements", tags=["entitlements"])


@router.get("")
def get_entitlements(user: CurrentUser = Depends(get_current_user)):
    tenant = get_tenant(user.tenant_id) or {"plan": "starter", "quota": {}}
    plan_features = get_plan_def(tenant.get("plan", "starter")).features
    ends = tenant.get("trial_ends_at")
    return ok({
        "plan": tenant.get("plan", "starter"),
        "trial": {
            "state": trial_state(tenant),
            "ends_at": ends.isoformat() if ends else None,
        },
        "limits": tenant_limits(tenant),
        "features": {f.value: (f in plan_features) for f in Feature},
        "read_only": is_read_only(tenant),
    })
```

Register in `backend/main.py` alongside the other `include_router` calls:

```python
from backend.api.v1 import entitlements
app.include_router(entitlements.router, prefix="/api/v1")
```

> Match the exact `include_router` prefix style used by neighboring routers in `main.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_entitlements.py -k entitlements_endpoint -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api/v1/entitlements.py backend/main.py backend/tests/test_entitlements.py
git commit -m "feat(entitlements): GET /entitlements snapshot endpoint"
```

---

## Task 9: Frontend — API client + entitlements context

**Files:**
- Modify: `Frontend/src/lib/api.ts` (add `getEntitlements` + `Entitlements` type)
- Create: `Frontend/src/lib/entitlements.tsx` (`EntitlementsProvider`, `useEntitlements`)
- Test: `cd Frontend && npx tsc --noEmit`

**Interfaces:**
- Consumes: the `GET /entitlements` payload from Task 8.
- Produces:
  - `type Entitlements = { plan: string; trial: {state: string; ends_at: string | null}; limits: Record<string, number | null>; features: Record<string, boolean>; read_only: boolean }`.
  - `getEntitlements(): Promise<Entitlements>` in `api.ts`.
  - `useEntitlements()` returning `{ ent: Entitlements | null; has: (f: string) => boolean; readOnly: boolean; loading: boolean }`.

- [ ] **Step 1: Add the type and API call**

In `Frontend/src/lib/api.ts`, add the `Entitlements` type and:

```ts
export async function getEntitlements(): Promise<Entitlements> {
  const res = await apiFetch("/api/v1/entitlements");
  return (res.data ?? res) as Entitlements;
}
```

> Use the file's existing fetch/unwrap helper (`apiFetch`/`request`/etc.) and envelope convention — mirror a neighboring `get*` function exactly.

- [ ] **Step 2: Create the context/provider**

Create `Frontend/src/lib/entitlements.tsx`:

```tsx
"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { getEntitlements, type Entitlements } from "./api";

type Ctx = {
  ent: Entitlements | null;
  has: (f: string) => boolean;
  readOnly: boolean;
  loading: boolean;
};

const EntitlementsContext = createContext<Ctx>({
  ent: null, has: () => true, readOnly: false, loading: true,
});

export function EntitlementsProvider({ children }: { children: React.ReactNode }) {
  const [ent, setEnt] = useState<Entitlements | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    getEntitlements().then(setEnt).catch(() => setEnt(null)).finally(() => setLoading(false));
  }, []);
  const has = (f: string) => (ent ? !!ent.features[f] : true);
  return (
    <EntitlementsContext.Provider
      value={{ ent, has, readOnly: ent?.read_only ?? false, loading }}
    >
      {children}
    </EntitlementsContext.Provider>
  );
}

export const useEntitlements = () => useContext(EntitlementsContext);
```

- [ ] **Step 3: Typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
git add Frontend/src/lib/api.ts Frontend/src/lib/entitlements.tsx
git commit -m "feat(entitlements): frontend api client + entitlements context"
```

---

## Task 10: Frontend — nav gating, read-only banner, upsell

**Files:**
- Modify: the authed shell/layout that renders the sidebar nav and wraps pages (locate via `grep -rl "EntitlementsProvider\|nav" Frontend/src/app` — typically `Frontend/src/app/layout.tsx` or a `(app)` layout).
- Create: `Frontend/src/components/UpsellModal.tsx` and `Frontend/src/components/ReadOnlyBanner.tsx`
- Test: `cd Frontend && npx tsc --noEmit`

**Interfaces:**
- Consumes: `useEntitlements` from Task 9.
- Produces: nav items with a 🔒 for locked features (opening `UpsellModal`); a `ReadOnlyBanner` shown when `readOnly`; a `feature` prop on nav entries mapping to a `Feature` value.

- [ ] **Step 1: Mount the provider**

Wrap the authed shell's children with `<EntitlementsProvider>` (inside the authenticated layout, after auth is known).

- [ ] **Step 2: Create the banner**

Create `Frontend/src/components/ReadOnlyBanner.tsx`:

```tsx
"use client";
import { useEntitlements } from "@/lib/entitlements";

export function ReadOnlyBanner() {
  const { readOnly } = useEntitlements();
  if (!readOnly) return null;
  return (
    <div role="alert" className="bg-amber-100 text-amber-900 px-4 py-2 text-sm">
      Tu período de prueba terminó. Podés seguir viendo tus datos, pero para
      crear pedidos o entrenar modelos necesitás activar un plan.
    </div>
  );
}
```

> Match the project's styling system (Tailwind classes shown as a placeholder — mirror an existing banner/toast component's classes). Copy is Spanish end-user text, which is allowed.

- [ ] **Step 3: Create the upsell modal**

Create `Frontend/src/components/UpsellModal.tsx` — a simple modal that names the required plan and links to `/planes`. Reuse the shared dialog component the repo already has (`useConfirm`/`ConfirmDialog` referenced in recent commits) rather than hand-rolling one:

```tsx
"use client";
export function UpsellModal({ feature, onClose }: { feature: string; onClose: () => void }) {
  return (
    <div className="fixed inset-0 grid place-items-center bg-black/40" onClick={onClose}>
      <div className="bg-white rounded-lg p-6 max-w-sm" onClick={(e) => e.stopPropagation()}>
        <h2 className="font-semibold mb-2">Función no disponible en tu plan</h2>
        <p className="text-sm mb-4">
          Esta función requiere un plan superior. Actualizá para desbloquearla.
        </p>
        <a href="/planes" className="text-blue-600 underline text-sm">Ver planes</a>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Gate the nav**

In the sidebar, add an optional `feature?: string` to each nav item's definition. When rendering, if `!has(item.feature)`, render the label with a 🔒 and, on click, open `UpsellModal` for that feature instead of navigating. Render `<ReadOnlyBanner />` at the top of the authed content area.

- [ ] **Step 5: Typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
git add Frontend/src
git commit -m "feat(entitlements): nav lock, read-only banner, upsell modal"
```

---

## Final verification

- [ ] **Backend suite:** `cd backend && python -m pytest tests/ -q` → all pass.
- [ ] **Frontend typecheck:** `cd Frontend && npx tsc --noEmit` → clean.
- [ ] **Import smoke:** `cd backend && python -c "import backend.main"` → no circular-import error.
- [ ] **Manual:** boot backend, create a fresh tenant, confirm `GET /api/v1/entitlements` shows `plan=starter`, `trial.state=trialing`; force `trial_ends_at` into the past and confirm a session POST returns 403 `TRIAL_EXPIRED` while GET still works.
