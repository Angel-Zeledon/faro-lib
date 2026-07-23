# Multi-Period Planning — Phase A Implementation Plan (session family + generous reach)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One training launch fans out into a **session family** — the same dataset trained once per supported granularity (daily/weekly/monthly, gated by how much history the data holds), each pre-forecast to a generous reach — instead of a single native-frequency session (spec: `docs/superpowers/specs/2026-07-23-multi-period-planning-design.md`, Phase A).

**Architecture:** A new `backend/sessions/family_service.py` owns the fan-out: given a base session already validated and ready to train, it computes the available granularities from the dataset's dates, tags the base session with a `family_id` + its `granularity`, and creates + configures + enqueues one sibling session per coarser granularity. The three existing training entry points (train endpoint, demo quickstart, integrations sync) call this one function instead of the inline `create_job → transition QUEUED` they each duplicate today. The engine (`runner.py`) is unchanged — it already consumes `granularity_cfg` + `forecast.horizon`.

**Tech Stack:** FastAPI + psycopg2 raw SQL; pandas only inside `backend/utils/temporal_agg.py` (already a pandas module) and to read the date column; pytest against local Postgres :5544 (docker `faro_db`).

## Global Constraints

- All code, comments, tests, commit messages in **English** (CLAUDE.md). User-facing chat is Spanish; the ONLY Spanish in the repo is `translations.ts` `es` values + backend end-user copy.
- No pandas in `backend/` **except** `workers/runner.py`, `utils/temporal_agg.py`, and the date-column read this plan adds (documented as a bounded exception, mirroring `forecasts.py`'s existing dataset read).
- Supported granularities in v1 are exactly `daily`, `weekly`, `monthly` (ignore quarterly/yearly even though `FREQ_RULES` lists them).
- Generous forecast reach per grain: `{"daily": 90, "weekly": 26, "monthly": 12}` (steps of that grain).
- A granularity is offered only if the history spans `>= _MIN_BUCKETS_FOR_GRANULARITY` (20) buckets at that grain. The base (finest detected) frequency is always included.
- Migrations are append-only idempotent tuples in `backend/db/migrations.py`'s `_MIGRATIONS` list.
- Existing single-session behavior must stay intact: a family of one (short data) is indistinguishable from today, and pre-feature sessions (NULL `family_id`) keep working.
- Run tests: `cd backend && python -m pytest tests/<file> -q` (needs Postgres on :5544).

---

### Task 1: Schema — `sessions.family_id` + `sessions.granularity`

**Files:**
- Modify: `backend/db/migrations.py` (append to `_MIGRATIONS`, after the `clamp_transfer_items_over_receipt` entry)
- Test: `backend/tests/test_session_family.py` (new)

**Interfaces:**
- Produces columns: `sessions.family_id TEXT` (nullable), `sessions.granularity TEXT` (nullable), index `sessions_family_idx (tenant_id, family_id)`.

- [ ] **Step 1: Write the failing schema test**

```python
# backend/tests/test_session_family.py
"""Session family: schema, planning, fan-out (multi-period Phase A)."""

from backend.db.connection import query


def _columns(table: str) -> set[str]:
    rows = query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,))
    return {r["column_name"] for r in rows}


class TestFamilySchema:
    def test_sessions_have_family_columns(self, client):
        cols = _columns("sessions")
        assert "family_id" in cols
        assert "granularity" in cols
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_session_family.py -q`
Expected: FAIL — columns absent.

- [ ] **Step 3: Append the migration**

In `backend/db/migrations.py`, append to `_MIGRATIONS`:

```python
    # Multi-period planning (Phase A): a training launch fans out into a
    # "family" of sessions, one per supported granularity, sharing a family_id.
    # Nullable — pre-feature sessions keep NULL and behave as a lone family.
    ("add_sessions_family_id",
     "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS family_id TEXT"),
    ("add_sessions_granularity",
     "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS granularity TEXT"),
    ("create_sessions_family_idx",
     "CREATE INDEX IF NOT EXISTS sessions_family_idx ON sessions (tenant_id, family_id)"),
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_session_family.py -q`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/db/migrations.py backend/tests/test_session_family.py
git commit -m "feat(db): session family_id and granularity columns"
```

---

### Task 2: `temporal_agg` honors real bucket counts

**Files:**
- Modify: `backend/utils/temporal_agg.py` (`available_granularities`; add `bucket_count`)
- Test: `backend/tests/test_temporal_agg.py` (extend)

**Interfaces:**
- Produces: `bucket_count(dates: list[str], granularity: str) -> int` — number of distinct period-buckets the dates span at `granularity`.
- Changes: `available_granularities(base_freq: str, dates: list[str], min_buckets: int = 20) -> list[str]` — signature changes from `(base_freq, n_points=0)` to take the actual `dates` and a `min_buckets` floor, returning only grains (from `daily`/`weekly`/`monthly`, starting at `base_freq`) whose `bucket_count >= min_buckets`; `base_freq` itself is always included even if short.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_temporal_agg.py
from backend.utils.temporal_agg import bucket_count, available_granularities


def _daily_dates(n):
    import datetime
    d0 = datetime.date(2025, 1, 1)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


class TestBucketGate:
    def test_bucket_count_daily_weekly_monthly(self):
        dates = _daily_dates(70)  # 70 days
        assert bucket_count(dates, "daily") == 70
        assert 10 <= bucket_count(dates, "weekly") <= 11   # ~10 weeks
        assert bucket_count(dates, "monthly") == 3         # Jan, Feb, Mar

    def test_available_gates_monthly_out_on_short_history(self):
        # 70 daily points: daily(70) and weekly(~10) clear a floor of 8,
        # monthly(3) does not.
        got = available_granularities("daily", _daily_dates(70), min_buckets=8)
        assert got == ["daily", "weekly"]

    def test_available_always_includes_base_even_if_short(self):
        got = available_granularities("daily", _daily_dates(5), min_buckets=20)
        assert got == ["daily"]

    def test_available_from_weekly_base_never_offers_daily(self):
        # Base freq weekly: only weekly/monthly are reachable (never finer).
        weekly_dates = [d for i, d in enumerate(_daily_dates(700)) if i % 7 == 0]
        got = available_granularities("weekly", weekly_dates, min_buckets=8)
        assert "daily" not in got
        assert got[0] == "weekly"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_temporal_agg.py -q -k "BucketGate"`
Expected: FAIL — `bucket_count` undefined / `available_granularities` old signature.

- [ ] **Step 3: Implement**

In `backend/utils/temporal_agg.py`, add `bucket_count` and replace `available_granularities`:

```python
# Only these three are user-facing planning periods (quarterly/yearly exist in
# FREQ_RULES for the chart chip but are not planning grains).
_PLANNING_ORDER = ["daily", "weekly", "monthly"]


def bucket_count(dates: List[str], granularity: str) -> int:
    """Number of distinct period-buckets the dates span at `granularity`."""
    if not dates:
        return 0
    rule = FREQ_RULES.get(granularity, "D")
    try:
        idx = pd.to_datetime(pd.Series(dates)).dropna()
        if idx.empty:
            return 0
        return int(idx.dt.to_period(_period_alias(rule)).nunique())
    except Exception:
        return 0


def _period_alias(rule: str) -> str:
    # pandas Period alias for each resample rule.
    return {"D": "D", "W-MON": "W", "MS": "M", "QS": "Q", "YS": "Y"}.get(rule, "D")


def available_granularities(
    base_freq: str, dates: List[str], min_buckets: int = 20
) -> List[str]:
    """Planning grains (base_freq and coarser) the data can actually train.

    A grain qualifies iff the history spans >= min_buckets buckets at it.
    base_freq is always included (you can always plan at your native grain,
    even with thin data); coarser grains are gated by the bucket count.
    """
    if base_freq not in _PLANNING_ORDER:
        base_freq = "daily"
    start = _PLANNING_ORDER.index(base_freq)
    out = []
    for g in _PLANNING_ORDER[start:]:
        if g == base_freq or bucket_count(dates, g) >= min_buckets:
            out.append(g)
    return out
```

- [ ] **Step 4: Run new + regression**

Run: `cd backend && python -m pytest tests/test_temporal_agg.py -q`
Expected: all pass. Then confirm the `/skus` consumer still works: `cd backend && python -m pytest tests/test_endpoints.py -q -k "intelligence or granular"` — if the old `available_granularities(orig_freq, len(historical_raw))` call in `forecasts.py:465` now passes a wrong 2nd arg, fix that call site to `available_granularities(orig_freq, dates)` in the same commit (it has `dates` in scope at line 463).

- [ ] **Step 5: Commit**

```bash
git add backend/utils/temporal_agg.py backend/tests/test_temporal_agg.py backend/api/v1/forecasts.py
git commit -m "feat(temporal): gate available granularities by real bucket count"
```

---

### Task 3: `family_service.plan_family` (pure planning)

**Files:**
- Create: `backend/sessions/family_service.py`
- Test: `backend/tests/test_session_family.py` (extend)

**Interfaces:**
- Produces:
  - Constants `GENEROUS_REACH = {"daily": 90, "weekly": 26, "monthly": 12}`, `MIN_BUCKETS_FOR_GRANULARITY = 20`, `TARGET_FREQ = {"daily": None, "weekly": "W-MON", "monthly": "MS"}`.
  - `plan_family(dates: list[str]) -> list[dict]` — one entry per available granularity: `{"granularity": str, "target_freq": str|None, "horizon": int, "is_base": bool}`. The base (finest detected) grain has `target_freq=None` (native strategy) and `is_base=True`; coarser grains carry their `target_freq` and `is_base=False`. Ordered finest-first.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_session_family.py
from backend.sessions import family_service as fam


def _daily_dates(n):
    import datetime
    d0 = datetime.date(2025, 1, 1)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


class TestPlanFamily:
    def test_long_daily_data_yields_three_grains(self):
        specs = fam.plan_family(_daily_dates(900))  # ~30 months
        grains = [s["granularity"] for s in specs]
        assert grains == ["daily", "weekly", "monthly"]
        base = specs[0]
        assert base["is_base"] is True and base["target_freq"] is None
        assert base["horizon"] == 90
        monthly = specs[-1]
        assert monthly["target_freq"] == "MS" and monthly["horizon"] == 12
        assert monthly["is_base"] is False

    def test_short_daily_data_yields_only_base(self):
        specs = fam.plan_family(_daily_dates(10))
        assert [s["granularity"] for s in specs] == ["daily"]
        assert specs[0]["is_base"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_session_family.py -q -k "PlanFamily"`
Expected: FAIL — module/function absent.

- [ ] **Step 3: Implement `backend/sessions/family_service.py`**

```python
"""
Session family fan-out (multi-period planning, Phase A).

A single training launch produces one session per supported granularity
(daily/weekly/monthly, gated by how much history the data holds), all sharing
a family_id, each pre-forecast to a generous reach. The engine is unchanged:
each sibling just carries a different granularity_cfg (aggregate + target_freq)
and forecast_cfg.horizon, which runner.py already consumes.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.utils.temporal_agg import detect_frequency, available_granularities

log = logging.getLogger(__name__)

# Steps of the grain to pre-forecast, so the admin's chosen horizon (Phase B)
# is a window into an already-computed reach rather than a re-train.
GENEROUS_REACH = {"daily": 90, "weekly": 26, "monthly": 12}
# A grain is offered only if the history spans >= this many of its buckets.
MIN_BUCKETS_FOR_GRANULARITY = 20
# pandas resample rule each grain trains at; None = native (no aggregation).
TARGET_FREQ = {"daily": None, "weekly": "W-MON", "monthly": "MS"}


def plan_family(dates: list[str]) -> list[dict]:
    """Decide which granularities to train and with what config. Pure — no DB.

    Returns finest-first, one dict per available grain:
      {granularity, target_freq, horizon, is_base}.
    The base (finest detected) grain trains natively (target_freq None).
    """
    base_freq = detect_frequency(dates)
    if base_freq not in GENEROUS_REACH:
        base_freq = "daily"
    grains = available_granularities(base_freq, dates, MIN_BUCKETS_FOR_GRANULARITY)
    specs = []
    for g in grains:
        specs.append({
            "granularity": g,
            "target_freq": None if g == base_freq else TARGET_FREQ[g],
            "horizon": GENEROUS_REACH[g],
            "is_base": g == base_freq,
        })
    return specs
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_session_family.py -q -k "PlanFamily"`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/sessions/family_service.py backend/tests/test_session_family.py
git commit -m "feat(sessions): plan_family decides granularities and per-grain config"
```

---

### Task 4: `family_service.launch_training_family` (fan-out + enqueue)

**Files:**
- Modify: `backend/sessions/family_service.py` (add `launch_training_family` + a date-reading helper)
- Test: `backend/tests/test_session_family.py` (extend)

**Interfaces:**
- Consumes: `sessions.service` (`create_session`, `attach_dataset`, `force_status`, `set_last_job`, `transition`, `get_session`), `db.session_store` (`get_field`, `set_field`), `datasets.service.get_dataset`, `training.job_service.create_job`, `db.connection.execute`, Task 3 `plan_family`.
- Produces: `launch_training_family(tenant_id, base_session_id, user_id) -> dict` — tags the base session with a `family_id` (= base_session_id) + its granularity, sets the base's `forecast_cfg.horizon` to the base reach, creates+configures+enqueues one sibling per coarser grain, enqueues the base too, and returns `{"family_id": str, "base_job_id": str, "sessions": [{"session_id","granularity","job_id"}]}` (base first). The base session must already be validated + in a pre-train state (the callers do that today).

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_session_family.py
import datetime

from backend.db.connection import query, query_one, execute
from backend.db import session_store
from backend.sessions import service as session_svc
from backend.utils.ids import generate_id


def _make_ready_session(tid, uid, dates):
    """A session with a small CSV dataset (date col 'fecha') and the demo
    configs, forced to MODELS_CONFIGURED — the state callers reach before
    launching training."""
    import tempfile, os, csv
    from backend.sessions.defaults import default_quickstart_configs

    fd, path = tempfile.mkstemp(suffix=".csv"); os.close(fd)
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["sku", "fecha", "cantidad"])
        for d in dates:
            w.writerow(["A", d, 5])
    ds_id = generate_id("ds")
    execute(
        """INSERT INTO datasets (id, tenant_id, name, original_filename,
             file_type, file_path, size_bytes, uploaded_by, uploaded_at)
           VALUES (%s,%s,'t','t.csv','csv',%s,%s,%s,NOW())""",
        (ds_id, tid, path, os.path.getsize(path), uid))
    s = session_svc.create_session(tid, uid, "Base")
    sid = s["id"]
    session_svc.attach_dataset(tid, sid, ds_id)
    for field, cfg in default_quickstart_configs().items():
        session_store.set_field(tid, sid, field, cfg)
    session_svc.force_status(tid, sid, "MODELS_CONFIGURED")
    return sid


def _daily_dates(n):
    d0 = datetime.date(2025, 1, 1)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


class TestLaunchFamily:
    def test_long_data_launches_three_queued_siblings(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(900))

        result = fam.launch_training_family(tid, sid, uid)

        assert result["family_id"] == sid
        rows = query(
            "SELECT id, granularity, family_id, status FROM sessions "
            "WHERE tenant_id=%s AND family_id=%s ORDER BY granularity", (tid, sid))
        assert len(rows) == 3
        assert {r["granularity"] for r in rows} == {"daily", "weekly", "monthly"}
        assert all(r["status"] == "QUEUED" for r in rows)
        # The base keeps native granularity_cfg; a sibling carries aggregate+freq.
        monthly = next(r for r in rows if r["granularity"] == "monthly")
        gcfg = session_store.get_field(tid, monthly["id"], "granularity_cfg")
        assert gcfg["strategy"] == "aggregate" and gcfg["target_freq"] == "MS"
        fcfg = session_store.get_field(tid, monthly["id"], "forecast_cfg")
        assert fcfg["horizon"] == 12
        # Base horizon set to its generous reach.
        base_fcfg = session_store.get_field(tid, sid, "forecast_cfg")
        assert base_fcfg["horizon"] == 90

    def test_short_data_launches_only_base(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(10))
        result = fam.launch_training_family(tid, sid, uid)
        rows = query("SELECT granularity FROM sessions WHERE family_id=%s", (sid,))
        assert [r["granularity"] for r in rows] == ["daily"]
        assert result["base_job_id"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_session_family.py -q -k "LaunchFamily"`
Expected: FAIL — `launch_training_family` undefined.

- [ ] **Step 3: Implement in `backend/sessions/family_service.py`**

```python
def _read_dataset_dates(tenant_id: str, session_id: str) -> list[str]:
    """Read just the date column of the session's dataset. pandas here is a
    bounded exception to the no-pandas-in-backend rule (same as forecasts.py's
    dataset read): we need the real dates to gate granularities before enqueue.
    """
    from backend.datasets.service import get_dataset
    from backend.db import session_store
    from backend.sessions import service as session_svc

    s = session_svc.get_session(tenant_id, session_id)
    ds = get_dataset(tenant_id, s["dataset_id"]) if s and s.get("dataset_id") else None
    if not ds or not ds.get("file_path"):
        return []
    cols = session_store.get_field(tenant_id, session_id, "columns_cfg") or {}
    if cols.get("schema_version") == "canonical_v1":
        date_col = (cols.get("canonical_mapping") or {}).get("date")
    else:
        date_col = cols.get("date_column") or cols.get("date")
    if not date_col:
        return []
    import pandas as pd
    path = ds["file_path"]
    try:
        df = pd.read_csv(path, usecols=[date_col]) if str(path).endswith(".csv") \
            else pd.read_excel(path, usecols=[date_col])
        return [str(v)[:10] for v in df[date_col].dropna().tolist()]
    except Exception as e:
        log.warning("family: could not read dates for session=%s: %s", session_id, e)
        return []


def _enqueue(tenant_id: str, session_id: str, user_id: str) -> str:
    """create_job + set_last_job + transition to QUEUED; returns job_id."""
    from backend.training import job_service
    from backend.sessions import service as session_svc

    job = job_service.create_job(tenant_id, session_id, user_id)
    session_svc.set_last_job(tenant_id, session_id, job["id"])
    try:
        session_svc.transition(tenant_id, session_id, "QUEUED", "training")
    except ValueError:
        pass
    return job["id"]


def launch_training_family(tenant_id: str, base_session_id: str, user_id: str) -> dict:
    """Fan a ready-to-train base session out into its granularity family and
    enqueue every member. The base session must already be validated and in a
    pre-train state (callers guarantee this). Returns the family descriptor.
    """
    from backend.db.connection import execute
    from backend.db import session_store
    from backend.sessions import service as session_svc

    dates = _read_dataset_dates(tenant_id, base_session_id)
    specs = plan_family(dates)  # always >= 1 (the base)
    base_spec = specs[0]
    family_id = base_session_id

    # Tag + finalize the base session.
    execute(
        "UPDATE sessions SET family_id=%s, granularity=%s, updated_at=NOW() "
        "WHERE id=%s AND tenant_id=%s",
        (family_id, base_spec["granularity"], base_session_id, tenant_id))
    base_fcfg = dict(session_store.get_field(tenant_id, base_session_id, "forecast_cfg") or {})
    base_fcfg["horizon"] = base_spec["horizon"]
    session_store.set_field(tenant_id, base_session_id, "forecast_cfg", base_fcfg)

    base_session = session_svc.get_session(tenant_id, base_session_id)
    dataset_id = base_session.get("dataset_id")

    members = [{"session_id": base_session_id, "granularity": base_spec["granularity"]}]

    # Coarser siblings: clone configs, override granularity + horizon, enqueue.
    for spec in specs[1:]:
        sib = session_svc.create_session(
            tenant_id, user_id, f"{base_session['name']} · {spec['granularity']}")
        sib_id = sib["id"]
        if dataset_id:
            session_svc.attach_dataset(tenant_id, sib_id, dataset_id)
        for field in ("columns_cfg", "features_cfg", "models_cfg",
                      "validation_cfg", "business_cfg", "forecast_cfg"):
            val = session_store.get_field(tenant_id, base_session_id, field)
            if val is not None:
                if field == "forecast_cfg":
                    val = {**dict(val), "horizon": spec["horizon"]}
                session_store.set_field(tenant_id, sib_id, field, val)
        session_store.set_field(tenant_id, sib_id, "granularity_cfg",
                                {"strategy": "aggregate", "target_freq": spec["target_freq"]})
        execute(
            "UPDATE sessions SET family_id=%s, granularity=%s, updated_at=NOW() "
            "WHERE id=%s AND tenant_id=%s",
            (family_id, spec["granularity"], sib_id, tenant_id))
        session_svc.force_status(tenant_id, sib_id, "MODELS_CONFIGURED")
        members.append({"session_id": sib_id, "granularity": spec["granularity"]})

    # Enqueue base FIRST (finest grain → semáforo usable soonest), then siblings.
    base_job_id = _enqueue(tenant_id, base_session_id, user_id)
    members[0]["job_id"] = base_job_id
    for m in members[1:]:
        m["job_id"] = _enqueue(tenant_id, m["session_id"], user_id)

    log.info("[family] tenant=%s family=%s members=%d",
             tenant_id, family_id, len(members))
    return {"family_id": family_id, "base_job_id": base_job_id, "sessions": members}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_session_family.py -q -k "LaunchFamily"`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/sessions/family_service.py backend/tests/test_session_family.py
git commit -m "feat(sessions): launch_training_family fans out and enqueues the family"
```

---

### Task 5: Wire the three training entry points to the family launcher

**Files:**
- Modify: `backend/api/v1/training.py:51-57` (`start_training`)
- Modify: `backend/api/v1/demo.py:134-140` (`demo_quickstart`)
- Modify: `backend/integrations/sync_service.py` (the `create_job`/`transition QUEUED` tail — grep `transition.*QUEUED` in that file)
- Test: `backend/tests/test_session_family.py` (extend), and confirm existing demo/train/sync tests still pass

**Interfaces:**
- Consumes: Task 4 `launch_training_family`.
- Each entry point replaces its inline `create_job → set_last_job → transition QUEUED` with `fam.launch_training_family(tenant_id, session_id, user_id)` and returns the family's `base_job_id` where it used to return the single `job["id"]`. Response shapes stay compatible (same `job_id`/`status` keys); optionally add `family` to the body.

- [ ] **Step 1: Write the failing integration test**

```python
# append to backend/tests/test_session_family.py
class TestEntryPoints:
    def test_demo_quickstart_creates_a_family(self, client, auth_headers, test_tenant):
        from backend.db.connection import query
        r = client.post("/api/v1/demo/quickstart", headers=auth_headers)
        assert r.status_code == 202, r.text
        sid = r.json()["data"]["session_id"]
        # The demo CSV spans enough history for >1 grain.
        rows = query("SELECT granularity FROM sessions WHERE tenant_id=%s AND family_id=%s",
                     (test_tenant["id"], sid))
        assert len(rows) >= 1
        assert all(row["granularity"] for row in rows)  # every family member tagged
```

Check the real demo endpoint path first (grep `quickstart` in `backend/api/v1/demo.py` and the router prefix) and adjust the URL; the demo CSV's span decides whether the family is 1 or 3 — assert `>= 1` and that every member carries a granularity.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_session_family.py -q -k "EntryPoints"`
Expected: FAIL — demo session has NULL granularity (family not launched).

- [ ] **Step 3: Implement — training endpoint**

In `backend/api/v1/training.py`, replace the tail of `start_training` (lines ~51-59) with:

```python
    from backend.sessions import family_service as fam
    family = fam.launch_training_family(user.tenant_id, session_id, user.user_id)
    return ok({"job_id": family["base_job_id"], "status": "QUEUED", "family": family})
```

- [ ] **Step 4: Implement — demo quickstart**

In `backend/api/v1/demo.py`, replace the "4. Train" block (lines ~134-140) with:

```python
    from backend.sessions import family_service as fam
    family = fam.launch_training_family(user.tenant_id, session_id, user.user_id)
    job_id = family["base_job_id"]
```

(the `return ok({...})` below already uses `job["id"]` — change it to `job_id`).

- [ ] **Step 5: Implement — integrations sync**

In `backend/integrations/sync_service.py`, find the tail that does
`create_job` + `transition(..., "QUEUED", "training")` and replace it with
`fam.launch_training_family(tenant_id, session_id, _SYSTEM_USER_ID)`, keeping
whatever it returns for `session_id`/`job_id` in the result dict (use the
returned `base_job_id`). Import `from backend.sessions import family_service as fam`.

- [ ] **Step 6: Run new + regression**

Run: `cd backend && python -m pytest tests/test_session_family.py tests/test_demo_and_alerts.py tests/test_forecast_flow.py tests/test_integrations_sync.py tests/test_edge_cases.py -q`
Expected: all pass. (These cover demo, train-endpoint, sync, and the viewer/analyst permission pairs on `/train` — the family launch must not change status codes.)

- [ ] **Step 7: Commit**

```bash
git add backend/api/v1/training.py backend/api/v1/demo.py backend/integrations/sync_service.py backend/tests/test_session_family.py
git commit -m "feat(training): all training launches fan out into a session family"
```

---

### Task 6: Full regression + plan doc note

**Files:**
- Modify: `docs/plan_general_faro_2026-07-18.md` (add a line under a new "multi-period" note)

- [ ] **Step 1: Full backend suite**

Run: `cd backend && python -m pytest tests/ -q`
Expected: 0 failures beyond the known machine-load-flaky `test_stress.py::test_login_responds_under_2s` (re-run it alone to confirm). Watch specifically for: session-count assertions elsewhere that now see extra family sessions (e.g. `test_edge_cases` tenant-isolation counts, `count_sessions`) — if any test asserted an exact session count after a demo/train, update it to account for the family (the family is correct behavior, not a regression).

- [ ] **Step 2: Note it in the plan doc**

Add under the 2026-07-23 area of `docs/plan_general_faro_2026-07-18.md`:
`Multi-period planning Phase A: training launches now fan out into a session family (daily/weekly/monthly gated by data span), each pre-forecast to a generous reach. Phases B (active period setting + resolver + UI) and C (per-period coverage/semáforo) pending — spec 2026-07-23-multi-period-planning-design.md.`

- [ ] **Step 3: Commit**

```bash
git add docs/plan_general_faro_2026-07-18.md
git commit -m "docs: note multi-period planning Phase A landed"
```

## Out of scope (this phase)

- The active-period tenant setting, the resolver, and any UI (Phase B).
- Per-period coverage/semáforo reinterpretation and horizon windowing (Phase C).
- Rate-limit tuning for the family burst: a family launch may create up to 3 jobs where the `/train` endpoint's 3-concurrent gate expects fewer. In testing mode the gate is bypassed; in production a single family launch is one user action, so leave the gate as-is for Phase A and revisit if it bites (noted, not fixed here).
