# Multi-Warehouse Complete — Backend Implementation Plan (slices 1–5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-warehouse demand, network-aware semáforo with TRANSFER recommendations, send→receive inter-warehouse transfers, and PO destination warehouse — the backend half of feature 5.4 (spec: `docs/superpowers/specs/2026-07-22-multi-warehouse-complete-design.md`).

**Architecture:** All new business logic lives in `backend/inventory/` services over the existing `(tenant_id, sku, warehouse)`-keyed `inventory_stock`. The transfer lifecycle mirrors `reception_service.receive_po` (single `transaction()` block, `conn=` threaded through every write). Store-aware forecast keys use the `│` separator already defined by `forecasting_core.data.canonical.series_key`; the backend re-declares the separator constant (backend must not import forecasting_core outside `workers/runner.py`).

**Tech Stack:** FastAPI + psycopg2 (raw SQL, RealDictCursor), pytest against local Postgres :5544 (docker `faro_db`).

## Global Constraints

- All code, comments, tests, commit messages in **English** (CLAUDE.md). Spanish only in user-facing explanation strings.
- No pandas / ML imports in `backend/` (only `workers/runner.py` may import forecasting_core).
- Persisted signal values stay `PEDIR_YA / PEDIR_PRONTO / OK / SOBRESTOCK / SIN_DATOS` — never renamed. New `recommended_action` values are English: `"order" | "transfer"`.
- Every mutating endpoint: `require_analyst_or_above`; reads: `get_current_user`.
- Tests assert **state changes via direct DB queries** + permission pairs (viewer 403 + state unchanged, analyst success) + cross-tenant denial.
- Migrations are append-only tuples in `backend/db/migrations.py` `MIGRATIONS` list; idempotent (`IF NOT EXISTS`).
- Run tests: `cd backend && python -m pytest tests/<file> -q` (needs local Postgres on :5544).
- Mono-warehouse behavior must remain byte-identical (regression tests included).

---

### Task 1: DB migrations (transfer tables, demand_share, PO destination)

**Files:**
- Modify: `backend/db/migrations.py` (append to `MIGRATIONS` list, after the last entry)
- Test: `backend/tests/test_transfers.py` (new file, schema smoke test)

**Interfaces:**
- Produces tables: `inventory_transfer_log(id, tenant_id, from_warehouse, to_warehouse, status, notes, created_by, created_at, received_at)`, `inventory_transfer_items(id, tenant_id, transfer_id, sku, qty_sent, qty_received)`.
- Produces columns: `warehouses.demand_share FLOAT NULL`, `inventory_po_log.destination_warehouse TEXT NULL`.

- [ ] **Step 1: Write the failing schema test**

```python
# backend/tests/test_transfers.py
"""Inter-warehouse transfers (feature 5.4): schema, lifecycle, API."""

from backend.db.connection import query


def _columns(table: str) -> set[str]:
    rows = query(
        """SELECT column_name FROM information_schema.columns
           WHERE table_name = %s""",
        (table,),
    )
    return {r["column_name"] for r in rows}


class TestTransferSchema:
    def test_transfer_tables_exist(self):
        assert {"id", "tenant_id", "from_warehouse", "to_warehouse",
                "status", "notes", "created_by", "created_at",
                "received_at"} <= _columns("inventory_transfer_log")
        assert {"id", "tenant_id", "transfer_id", "sku",
                "qty_sent", "qty_received"} <= _columns("inventory_transfer_items")

    def test_demand_share_and_po_destination_columns(self):
        assert "demand_share" in _columns("warehouses")
        assert "destination_warehouse" in _columns("inventory_po_log")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_transfers.py -q`
Expected: FAIL (empty column sets — tables/columns don't exist).

- [ ] **Step 3: Append migrations**

Append to the `MIGRATIONS` list in `backend/db/migrations.py` (keep the existing tuple style):

```python
    # ── Multi-warehouse complete (feature 5.4) ───────────────────────────────
    ("create_inventory_transfer_log",
     """CREATE TABLE IF NOT EXISTS inventory_transfer_log (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL,
         from_warehouse TEXT NOT NULL,
         to_warehouse   TEXT NOT NULL,
         status         TEXT NOT NULL DEFAULT 'in_transit',
         notes          TEXT,
         created_by     TEXT NOT NULL,
         created_at     TIMESTAMPTZ DEFAULT NOW(),
         received_at    TIMESTAMPTZ
     )"""),
    ("create_inventory_transfer_log_idx",
     "CREATE INDEX IF NOT EXISTS transfer_log_tenant_idx ON inventory_transfer_log (tenant_id, created_at DESC)"),
    ("create_inventory_transfer_items",
     """CREATE TABLE IF NOT EXISTS inventory_transfer_items (
         id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id    TEXT NOT NULL,
         transfer_id  TEXT NOT NULL,
         sku          TEXT NOT NULL,
         qty_sent     FLOAT NOT NULL,
         qty_received FLOAT NOT NULL DEFAULT 0
     )"""),
    ("create_inventory_transfer_items_idx",
     "CREATE INDEX IF NOT EXISTS transfer_items_transfer_idx ON inventory_transfer_items (tenant_id, transfer_id)"),
    # Manual demand split for tenants whose sales history has no store column:
    # per-warehouse demand = SKU-global demand x normalized share (0-100).
    ("add_warehouses_demand_share",
     "ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS demand_share FLOAT"),
    # Where a PO's goods physically arrive. NULL = tenant default warehouse
    # (today's implicit behavior, preserved).
    ("add_po_log_destination_warehouse",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS destination_warehouse TEXT"),
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_transfers.py -q`
Expected: 2 passed (conftest boots the app → `run_all()` applies migrations).

- [ ] **Step 5: Commit**

```bash
git add backend/db/migrations.py backend/tests/test_transfers.py
git commit -m "feat(db): transfer tables, warehouse demand_share, PO destination warehouse"
```

---

### Task 2: Series-key helpers (`backend/inventory/series.py`)

**Files:**
- Create: `backend/inventory/series.py`
- Test: `backend/tests/test_series_keys.py`

**Interfaces:**
- Produces: `SERIES_SEPARATOR = "│"`, `split_key(key: str) -> tuple[str, str | None]`, `rollup_by_sku(forecasts: dict) -> dict`, `stores_in(forecasts: dict) -> set[str]`, `for_store(forecasts: dict, store: str) -> dict` (all pure Python, no DB).
- `forecasts` shape (existing session_store blob): `{key: {model: {"historical": [...], "forecast": [{"date","value","lower","upper",...}]}}}` where `key` is either a bare SKU (single-store session) or `f"{sku}│{store}"`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_series_keys.py
"""Store-aware forecast key helpers (feature 5.4)."""

from backend.inventory.series import (
    SERIES_SEPARATOR, split_key, rollup_by_sku, stores_in, for_store,
)


def _fc(values):
    """Minimal single-model forecasts entry with the given forecast values."""
    return {"m1": {
        "historical": [],
        "forecast": [
            {"date": f"2026-08-{i+1:02d}", "value": v, "lower": None, "upper": None}
            for i, v in enumerate(values)
        ],
    }}


class TestSplitKey:
    def test_bare_sku(self):
        assert split_key("SKU_A") == ("SKU_A", None)

    def test_sku_with_store(self):
        assert split_key(f"SKU_A{SERIES_SEPARATOR}Norte") == ("SKU_A", "Norte")

    def test_only_first_separator_splits(self):
        key = f"SKU{SERIES_SEPARATOR}Store{SERIES_SEPARATOR}X"
        assert split_key(key) == ("SKU", f"Store{SERIES_SEPARATOR}X")


class TestRollup:
    def test_single_store_session_passthrough(self):
        fc = {"SKU_A": _fc([1.0, 2.0])}
        assert rollup_by_sku(fc) == fc

    def test_sums_across_stores_per_date(self):
        fc = {
            f"SKU_A{SERIES_SEPARATOR}Norte": _fc([1.0, 2.0]),
            f"SKU_A{SERIES_SEPARATOR}Sur":   _fc([10.0, 20.0]),
        }
        rolled = rollup_by_sku(fc)
        assert set(rolled) == {"SKU_A"}
        vals = [p["value"] for p in rolled["SKU_A"]["m1"]["forecast"]]
        assert vals == [11.0, 22.0]

    def test_stores_in_and_for_store(self):
        fc = {
            f"SKU_A{SERIES_SEPARATOR}Norte": _fc([1.0]),
            f"SKU_B{SERIES_SEPARATOR}Sur":   _fc([2.0]),
        }
        assert stores_in(fc) == {"Norte", "Sur"}
        norte = for_store(fc, "Norte")
        assert set(norte) == {"SKU_A"}
        assert norte["SKU_A"]["m1"]["forecast"][0]["value"] == 1.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_series_keys.py -q`
Expected: FAIL — `ModuleNotFoundError: backend.inventory.series`.

- [ ] **Step 3: Implement `backend/inventory/series.py`**

```python
"""
Store-aware forecast key helpers (feature 5.4).

Session forecasts are stored as {key: {model: {historical, forecast}}}. For
single-store sessions the key is the bare SKU (unchanged legacy shape); when
the sales history has a mapped store column the key is "sku│store" — the same
series key forecasting_core builds (see forecasting_core/data/canonical.py
series_key()). The separator is re-declared here because backend/ must not
import forecasting_core outside workers/runner.py (layer rule in CLAUDE.md).
"""

from __future__ import annotations

# Mirror of forecasting_core.data.canonical._SEPARATOR — keep in sync.
SERIES_SEPARATOR = "│"


def split_key(key: str) -> tuple[str, str | None]:
    """'sku│store' -> (sku, store); bare 'sku' -> (sku, None)."""
    if SERIES_SEPARATOR in key:
        sku, store = key.split(SERIES_SEPARATOR, 1)
        return sku, store
    return key, None


def stores_in(forecasts: dict) -> set[str]:
    """Distinct store names present in a forecasts dict (empty for legacy keys)."""
    out: set[str] = set()
    for key in forecasts:
        _, store = split_key(key)
        if store is not None:
            out.add(store)
    return out


def for_store(forecasts: dict, store: str) -> dict:
    """Subset of a store-keyed forecasts dict for one store, re-keyed by bare SKU."""
    out: dict = {}
    for key, models in forecasts.items():
        sku, key_store = split_key(key)
        if key_store == store:
            out[sku] = models
    return out


def rollup_by_sku(forecasts: dict) -> dict:
    """
    Collapse store-keyed forecasts to per-SKU by summing forecast values (and
    lower/upper bands when every store has them) date-by-date per model.
    Legacy (bare-SKU) dicts pass through unchanged. Historical series are taken
    from the first store seen — SKU-level consumers use them for charting, and
    summing histories would double-count dates missing in some stores.
    """
    if not any(SERIES_SEPARATOR in k for k in forecasts):
        return forecasts

    out: dict = {}
    for key, models in forecasts.items():
        sku, _ = split_key(key)
        if sku not in out:
            # Deep-enough copy: new dicts/lists so summing never mutates input.
            out[sku] = {
                m: {
                    "historical": list(entry.get("historical") or []),
                    "forecast": [dict(p) for p in entry.get("forecast") or []],
                }
                for m, entry in models.items()
            }
            continue
        for model, entry in models.items():
            base = out[sku].setdefault(
                model, {"historical": [], "forecast": []}
            )
            by_date = {p["date"]: p for p in base["forecast"]}
            for p in entry.get("forecast") or []:
                tgt = by_date.get(p["date"])
                if tgt is None:
                    base["forecast"].append(dict(p))
                    by_date[p["date"]] = base["forecast"][-1]
                    continue
                tgt["value"] = round(float(tgt["value"]) + float(p["value"]), 4)
                for band in ("lower", "upper"):
                    if tgt.get(band) is not None and p.get(band) is not None:
                        tgt[band] = round(float(tgt[band]) + float(p[band]), 4)
                    else:
                        tgt[band] = None
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_series_keys.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/series.py backend/tests/test_series_keys.py
git commit -m "feat(inventory): store-aware forecast series key helpers"
```

---

### Task 3: Store-aware `_generate_forecast_series` in the runner

**Files:**
- Modify: `backend/workers/runner.py:341-419` (`_generate_forecast_series`)
- Test: `backend/tests/test_runner_store_series.py`

**Interfaces:**
- Consumes: `forecasting_core` engine result rows. When trained with two group keys, `engine.get_forecast()["rows"]` items carry `"sku"` already set to the series key (`sku│store`) by the trainer's `series_key()` usage; with one group key it is the bare SKU. The historical grouping must follow the same rule.
- Produces: session forecasts dict whose keys match what Task 2 helpers parse.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_runner_store_series.py
"""_generate_forecast_series must keep the store dimension when present."""

import pandas as pd

from backend.workers.runner import _generate_forecast_series
from backend.inventory.series import SERIES_SEPARATOR


class _StubEngine:
    def __init__(self, rows, df):
        self._rows = rows
        self._df = df

    def get_forecast(self):
        return {"rows": self._rows, "n_skus": 2, "horizon": 1}


def _row(sku_key, model="lightgbm", value=5.0):
    return {"sku": sku_key, "model": model, "date": "2026-08-01",
            "forecast": value, "p90_lo": 1.0, "p90_hi": 9.0, "step": 1}


def test_two_group_keys_produce_store_keys():
    key_a = f"A{SERIES_SEPARATOR}Norte"
    key_b = f"A{SERIES_SEPARATOR}Sur"
    df = pd.DataFrame({
        "sku":   ["A", "A"],
        "store": ["Norte", "Sur"],
        "date":  ["2026-07-01", "2026-07-01"],
        "sales": [3.0, 4.0],
    })
    config = {"columns": {"target": "sales", "date": "date",
                          "group_keys": ["sku", "store"]}}
    out = _generate_forecast_series(_StubEngine([_row(key_a), _row(key_b)], df), config)
    assert set(out) == {key_a, key_b}
    # Historical series split per store, not shared
    assert [p["value"] for p in out[key_a]["lightgbm"]["historical"]] == [3.0]
    assert [p["value"] for p in out[key_b]["lightgbm"]["historical"]] == [4.0]


def test_single_group_key_unchanged():
    df = pd.DataFrame({
        "sku": ["A"], "date": ["2026-07-01"], "sales": [3.0],
    })
    config = {"columns": {"target": "sales", "date": "date", "group_keys": ["sku"]}}
    out = _generate_forecast_series(_StubEngine([_row("A")], df), config)
    assert set(out) == {"A"}
    assert [p["value"] for p in out["A"]["lightgbm"]["historical"]] == [3.0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_runner_store_series.py -q`
Expected: `test_two_group_keys_produce_store_keys` FAILS — historical series are
grouped by the primary column only, so both store keys share the same combined
history (or keys don't match). `test_single_group_key_unchanged` passes.

- [ ] **Step 3: Make historical grouping store-aware**

In `_generate_forecast_series` (`backend/workers/runner.py`), replace the
historical-series block. Current code groups by `group_col = _primary_group_col(col_cfg)`.
New code:

```python
    col_cfg    = config["columns"]
    dt_col     = col_cfg["date"]
    target_col = col_cfg["target"]
    group_keys = [c for c in (col_cfg.get("group_keys") or []) if c]

    df = engine._df.copy() if engine._df is not None else pd.DataFrame()

    # Historical series per forecast key. With two group keys the engine keys
    # its forecast rows by series_key(sku, store) = "sku│store" — group the
    # history the same way so each store keeps its own past, instead of every
    # store sharing the combined SKU history.
    from backend.inventory.series import SERIES_SEPARATOR

    historical_by_sku: dict = {}
    if not df.empty and dt_col in df.columns and target_col in df.columns:
        df[dt_col] = pd.to_datetime(df[dt_col])
        present_keys = [c for c in group_keys if c in df.columns]
        if len(present_keys) >= 2:
            grouped = df.groupby(present_keys[:2])
            src = (
                (f"{g[0]}{SERIES_SEPARATOR}{g[1]}", frame)
                for g, frame in grouped
            )
        elif len(present_keys) == 1:
            src = df.groupby(present_keys[0])
        else:
            src = [("__all__", df)]
        for sku, g in src:
            historical_by_sku[str(sku)] = [
                {"date": str(row[dt_col])[:10], "value": round(float(row[target_col]), 4)}
                for _, row in g.sort_values(dt_col).iterrows()
            ]
```

Everything below (grouping forecast rows by `(sku, model)`, quantile detection,
result assembly) stays as-is — the row `"sku"` field already carries the series
key when two group keys were trained.

- [ ] **Step 4: Run new + regression tests**

Run: `cd backend && python -m pytest tests/test_runner_store_series.py tests/test_integration_forecasting.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/workers/runner.py backend/tests/test_runner_store_series.py
git commit -m "feat(runner): keep store dimension in forecast series keys"
```

---

### Task 4: Demand shares (`warehouses.demand_share` service + API)

**Files:**
- Modify: `backend/inventory/warehouse_service.py` (add `set_demand_share`, `get_demand_shares`)
- Modify: `backend/api/v1/inventory.py` (PATCH `/inventory/warehouses/{name}` — near the existing warehouse endpoints; find them with `grep -n "warehouses" backend/api/v1/inventory.py`)
- Test: `backend/tests/test_demand_shares.py`

**Interfaces:**
- Produces: `get_demand_shares(tenant_id) -> dict[str, float]` — warehouse name → **fraction summing to 1.0**. Rules: shares normalized over warehouses with non-NULL `demand_share`; all NULL → `{default_or_first_warehouse: 1.0}`; no warehouses at all → `{}`.
- Produces: `set_demand_share(tenant_id, name, share: float | None) -> dict` (returns updated row; raises `ValueError` if warehouse missing or share out of 0–100).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_demand_shares.py
"""Manual per-warehouse demand split (feature 5.4, spec §1b)."""

import pytest

from backend.db.connection import query_one
from backend.inventory import warehouse_service as wh_svc


@pytest.fixture()
def three_warehouses(test_tenant):
    tid = test_tenant["id"]
    wh_svc.create_warehouse(tid, "principal", is_default=True)
    wh_svc.create_warehouse(tid, "Norte")
    wh_svc.create_warehouse(tid, "Sur")
    return tid


class TestShares:
    def test_all_null_defaults_to_default_warehouse(self, three_warehouses):
        shares = wh_svc.get_demand_shares(three_warehouses)
        assert shares == {"principal": 1.0}

    def test_normalizes_set_shares(self, three_warehouses):
        tid = three_warehouses
        wh_svc.set_demand_share(tid, "Norte", 30)
        wh_svc.set_demand_share(tid, "Sur", 10)
        shares = wh_svc.get_demand_shares(tid)
        assert shares == {"Norte": 0.75, "Sur": 0.25}
        # Persisted raw value, not the normalized one
        row = query_one(
            "SELECT demand_share FROM warehouses WHERE tenant_id=%s AND name=%s",
            (tid, "Norte"))
        assert row["demand_share"] == 30

    def test_share_out_of_range_rejected(self, three_warehouses):
        with pytest.raises(ValueError):
            wh_svc.set_demand_share(three_warehouses, "Norte", 101)
        with pytest.raises(ValueError):
            wh_svc.set_demand_share(three_warehouses, "Norte", -1)

    def test_unknown_warehouse_rejected(self, three_warehouses):
        with pytest.raises(ValueError):
            wh_svc.set_demand_share(three_warehouses, "Ghost", 50)

    def test_clearing_share_returns_to_default(self, three_warehouses):
        tid = three_warehouses
        wh_svc.set_demand_share(tid, "Norte", 40)
        wh_svc.set_demand_share(tid, "Norte", None)
        assert wh_svc.get_demand_shares(tid) == {"principal": 1.0}


class TestSharesApi:
    def test_viewer_denied_and_unchanged(self, client, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "principal", is_default=True)
        r = client.patch("/api/v1/inventory/warehouses/principal",
                         json={"demand_share": 40}, headers=viewer_headers)
        assert r.status_code == 403
        row = query_one(
            "SELECT demand_share FROM warehouses WHERE tenant_id=%s AND name=%s",
            (tid, "principal"))
        assert row["demand_share"] is None

    def test_analyst_sets_share(self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "principal", is_default=True)
        r = client.patch("/api/v1/inventory/warehouses/principal",
                         json={"demand_share": 40}, headers=analyst_headers)
        assert r.status_code == 200
        row = query_one(
            "SELECT demand_share FROM warehouses WHERE tenant_id=%s AND name=%s",
            (tid, "principal"))
        assert row["demand_share"] == 40
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_demand_shares.py -q`
Expected: FAIL — `AttributeError: set_demand_share` / 404 on PATCH.

- [ ] **Step 3: Implement service functions**

Append to `backend/inventory/warehouse_service.py`:

```python
def set_demand_share(tenant_id: str, name: str, share: float | None) -> dict:
    """
    Store the manual demand share (0-100, or None to clear) for one warehouse.
    Used only by tenants whose sales history has no store column — see
    get_demand_shares() for how raw values become the actual split.
    """
    if share is not None and not (0 <= float(share) <= 100):
        raise ValueError("demand_share must be between 0 and 100")
    row = get_warehouse_by_name(tenant_id, name)
    if not row:
        raise ValueError(f"Warehouse '{name}' not found")
    execute(
        "UPDATE warehouses SET demand_share = %s WHERE tenant_id = %s AND name = %s",
        (share, tenant_id, name),
    )
    return get_warehouse_by_name(tenant_id, name)


def get_demand_shares(tenant_id: str) -> dict[str, float]:
    """
    Warehouse name -> demand fraction (sums to 1.0).

    Shares are normalized over the warehouses that have a non-NULL
    demand_share. With none set anywhere, the whole demand belongs to the
    default warehouse (falling back to the alphabetically-first one) — which
    is exactly the pre-multi-warehouse behavior for mono-warehouse tenants.
    """
    rows = list_warehouses(tenant_id)
    if not rows:
        return {}
    set_rows = [r for r in rows if r.get("demand_share") is not None]
    total = sum(float(r["demand_share"]) for r in set_rows)
    if set_rows and total > 0:
        return {r["name"]: float(r["demand_share"]) / total for r in set_rows}
    default = next((r for r in rows if r.get("is_default")), None) or \
        sorted(rows, key=lambda r: r["name"])[0]
    return {default["name"]: 1.0}
```

- [ ] **Step 4: Implement the API endpoint**

In `backend/api/v1/inventory.py`, next to the existing warehouse endpoints:

```python
class WarehousePatch(BaseModel):
    demand_share: Optional[float] = Field(default=None, ge=0, le=100)


@router.patch("/warehouses/{name}")
def patch_warehouse(
    name: str,
    body: WarehousePatch,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Set or clear the manual demand share for one warehouse (spec 5.4 §1b)."""
    try:
        row = wh_svc.set_demand_share(user.tenant_id, name, body.demand_share)
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e) else 422,
                            detail=str(e))
    return ok(row)
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_demand_shares.py tests/test_warehouses.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/inventory/warehouse_service.py backend/api/v1/inventory.py backend/tests/test_demand_shares.py
git commit -m "feat(inventory): manual per-warehouse demand shares"
```

---

### Task 5: Per-warehouse status + network transfer pass

**Files:**
- Modify: `backend/inventory/service.py` (new `get_inventory_status_by_warehouse`, `_network_transfer_pass`; constant `TRANSFER_MIN_DONOR_COVERAGE_DAYS = 30`)
- Modify: `backend/api/v1/inventory.py:280-321` (`/status` gains `by_warehouse` query param)
- Test: `backend/tests/test_status_by_warehouse.py`

**Interfaces:**
- Consumes: Task 2 helpers (`stores_in`, `for_store`, `rollup_by_sku`), Task 4 `get_demand_shares`.
- Produces: `get_inventory_status_by_warehouse(tenant_id, session_id, service_level=0.95) -> list[dict]` — items shaped like `get_inventory_status` items **plus** `warehouse: str`, `recommended_action: "order"|"transfer"|None`, `transfer_suggestion: {"from_warehouse": str, "qty": float, "donor_coverage_days_after": float} | None`.
- Demand resolution per warehouse: if the session forecasts have store keys → per-store forecast (store name matched to warehouse name case-insensitively after trim); else → SKU-global daily demand × `get_demand_shares()` fraction (warehouses with no share get no row unless they hold stock, in which case demand 0 → SOBRESTOCK semantics apply naturally).
- Network pass rule (spec §2): donor `d` for needy `(sku, w)` qualifies iff after donating `qty = min(need, donor_surplus)`: donor stock ≥ donor reorder point AND donor coverage ≥ 30 days. Best donor = max post-donation coverage. Transfer covers full need → `transfer` only; covers ≥ 80% → `transfer` only (partial); < 80% → stays `order`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_status_by_warehouse.py
"""Network-aware per-warehouse semaphore (feature 5.4, spec §2)."""

import pytest

from backend.db import session_store
from backend.inventory import service as inv_svc
from backend.inventory import warehouse_service as wh_svc
from backend.inventory.series import SERIES_SEPARATOR


def _forecast_entry(daily, days=30):
    return {"lightgbm": {
        "historical": [],
        "forecast": [
            {"date": f"2026-08-{i+1:02d}", "value": daily, "lower": None, "upper": None}
            for i in range(days)
        ],
    }}


@pytest.fixture()
def session(test_tenant, completed_session):
    """completed_session fixture from conftest gives a COMPLETED session row."""
    return completed_session


def _seed_stock(tid, sku, warehouse, stock, lead_time=5):
    inv_svc.upsert_stock(tid, sku, {
        "current_stock": stock, "lead_time_days": lead_time,
        "warehouse": warehouse, "moq": 1,
    })


class TestPerWarehouseDemand:
    def test_share_split_demand(self, test_tenant, session):
        tid = test_tenant["id"]
        sid = session["id"]
        # Global demand 10/day, split 80/20
        session_store.set_forecasts(tid, sid, {"A": _forecast_entry(10.0)})
        _seed_stock(tid, "A", "principal", 100)
        _seed_stock(tid, "A", "Norte", 100)
        wh_svc.set_demand_share(tid, "principal", 80)
        wh_svc.set_demand_share(tid, "Norte", 20)

        items = inv_svc.get_inventory_status_by_warehouse(tid, sid)
        by_wh = {i["warehouse"]: i for i in items if i["sku"] == "A"}
        assert by_wh["principal"]["daily_demand"] == pytest.approx(8.0)
        assert by_wh["Norte"]["daily_demand"] == pytest.approx(2.0)

    def test_store_keyed_forecasts_override_shares(self, test_tenant, session):
        tid = test_tenant["id"]
        sid = session["id"]
        session_store.set_forecasts(tid, sid, {
            f"A{SERIES_SEPARATOR}Norte": _forecast_entry(3.0),
            f"A{SERIES_SEPARATOR}principal": _forecast_entry(7.0),
        })
        _seed_stock(tid, "A", "principal", 100)
        _seed_stock(tid, "A", "Norte", 100)
        items = inv_svc.get_inventory_status_by_warehouse(tid, sid)
        by_wh = {i["warehouse"]: i for i in items if i["sku"] == "A"}
        assert by_wh["Norte"]["daily_demand"] == pytest.approx(3.0)
        assert by_wh["principal"]["daily_demand"] == pytest.approx(7.0)


class TestNetworkPass:
    def _seed_donor_and_needy(self, tid, sid, donor_stock=300.0):
        # 10/day everywhere; needy warehouse has 5 units (0.5 days), donor plenty.
        session_store.set_forecasts(tid, sid, {
            f"A{SERIES_SEPARATOR}Norte": _forecast_entry(10.0),
            f"A{SERIES_SEPARATOR}principal": _forecast_entry(10.0),
        })
        _seed_stock(tid, "A", "Norte", 5)
        _seed_stock(tid, "A", "principal", donor_stock)

    def test_donor_converts_order_to_transfer(self, test_tenant, session):
        tid, sid = test_tenant["id"], session["id"]
        self._seed_donor_and_needy(tid, sid)
        items = inv_svc.get_inventory_status_by_warehouse(tid, sid)
        needy = next(i for i in items if i["warehouse"] == "Norte" and i["sku"] == "A")
        assert needy["signal"] == "PEDIR_YA"
        assert needy["recommended_action"] == "transfer"
        ts = needy["transfer_suggestion"]
        assert ts["from_warehouse"] == "principal"
        assert ts["qty"] > 0
        assert ts["donor_coverage_days_after"] >= 30

    def test_no_donor_stays_order(self, test_tenant, session):
        tid, sid = test_tenant["id"], session["id"]
        # Donor has 60 units = 6 days of coverage -> can't donate
        self._seed_donor_and_needy(tid, sid, donor_stock=60.0)
        items = inv_svc.get_inventory_status_by_warehouse(tid, sid)
        needy = next(i for i in items if i["warehouse"] == "Norte" and i["sku"] == "A")
        assert needy["recommended_action"] == "order"
        assert needy["transfer_suggestion"] is None

    def test_aggregated_status_unchanged(self, test_tenant, session):
        """Regression: the SKU-level status must not learn about warehouses."""
        tid, sid = test_tenant["id"], session["id"]
        self._seed_donor_and_needy(tid, sid)
        items = inv_svc.get_inventory_status(tid, sid)
        row = next(i for i in items if i["sku"] == "A")
        assert row["current_stock"] == 305.0  # summed across warehouses
        assert "recommended_action" not in row


class TestApi:
    def test_by_warehouse_param(self, client, auth_headers, test_tenant, session):
        tid, sid = test_tenant["id"], session["id"]
        session_store.set_forecasts(tid, sid, {"A": _forecast_entry(10.0)})
        _seed_stock(tid, "A", "principal", 100)
        r = client.get(
            f"/api/v1/inventory/status?session_id={sid}&by_warehouse=true",
            headers=auth_headers)
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        assert all("warehouse" in i for i in items)
```

Note for the implementer: check `backend/tests/conftest.py` for the fixture that
creates a COMPLETED session (grep `completed_session`; if it doesn't exist, look
at how `test_inventory.py` builds one and copy that helper into this file).

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_status_by_warehouse.py -q`
Expected: FAIL — `AttributeError: get_inventory_status_by_warehouse`.

- [ ] **Step 3: Implement in `backend/inventory/service.py`**

Add after `get_inventory_status` (reusing its private helpers — `_avg_daily_forecast`,
`_calc_signal`, `_calc_recommended`, `_gate_recommended_by_signal`, `_Z`,
`resolve_lead_time`, `get_learned_lead_times`, `build_explanation`):

```python
# Minimum days of coverage a donor warehouse must keep AFTER donating for the
# network pass to suggest a transfer instead of a purchase (spec 5.4 §2).
TRANSFER_MIN_DONOR_COVERAGE_DAYS = 30.0


def get_inventory_status_by_warehouse(
    tenant_id: str, session_id: str, service_level: float = 0.95
) -> list[dict]:
    """
    Per-(sku, warehouse) semaphore rows (feature 5.4).

    Demand per warehouse comes from, in order of preference:
      1. store-keyed session forecasts ("sku│store"), store matched to the
         warehouse name case-insensitively;
      2. the SKU-global forecast split by warehouses.demand_share fractions.

    Each row gets the same signal/recommendation math as the aggregated
    status, then _network_transfer_pass() converts purchases into transfer
    suggestions where another warehouse can donate.
    """
    from backend.db import session_store
    from backend.inventory import warehouse_service as wh_svc
    from backend.inventory.series import stores_in, for_store, rollup_by_sku

    forecasts: dict = session_store.get_forecasts(tenant_id, session_id) or {}
    stock_rows = list_stock(tenant_id)
    learned_lead_times = get_learned_lead_times(tenant_id)

    warehouses = [w["name"] for w in wh_svc.list_warehouses(tenant_id)] or ["principal"]
    store_names = stores_in(forecasts)
    # Case-insensitive store -> warehouse name resolution
    wh_by_lower = {w.lower().strip(): w for w in warehouses}

    if store_names:
        demand_mode = "store"
        per_wh_forecasts = {}
        for store in store_names:
            wh = wh_by_lower.get(store.lower().strip(), store)
            per_wh_forecasts[wh] = for_store(forecasts, store)
        sku_forecasts = rollup_by_sku(forecasts)
    else:
        demand_mode = "share"
        shares = wh_svc.get_demand_shares(tenant_id)
        per_wh_forecasts = {}
        sku_forecasts = forecasts

    stock_by_pair = {(r["sku"], r.get("warehouse") or "principal"): r for r in stock_rows}
    all_skus = sorted(sku_forecasts.keys())

    items: list[dict] = []
    for sku in all_skus:
        for wh in warehouses:
            stock = stock_by_pair.get((sku, wh))
            if demand_mode == "store":
                model_forecasts = per_wh_forecasts.get(wh, {}).get(sku, {})
                share = 1.0
            else:
                model_forecasts = sku_forecasts.get(sku, {})
                share = shares.get(wh, 0.0)
            # Skip pairs with neither stock nor demand — they don't exist.
            if stock is None and (not model_forecasts or share == 0.0):
                continue

            supplier = stock.get("supplier") if stock else None
            lead_time_config = int(stock["lead_time_days"]) if stock else 15
            lead_time, lead_time_source, _ = resolve_lead_time(
                lead_time_config, supplier, learned_lead_times)
            current_stock = float(stock["current_stock"]) if stock else 0.0
            moq = float(stock["moq"]) if stock else 1.0

            if model_forecasts:
                sku_service_level = (
                    float(stock.get("service_level") or service_level)
                    if stock else service_level)
                avg_daily, avg_std = _avg_daily_forecast(model_forecasts, lead_time)
                avg_daily *= share
                avg_std *= share
                coverage_days = current_stock / avg_daily if avg_daily > 0 else 9999.0
                signal = _calc_signal(coverage_days, lead_time)
                recommended = _calc_recommended(
                    current_stock, avg_daily, avg_std, lead_time, moq,
                    sku_service_level)
                recommended = _gate_recommended_by_signal(signal, recommended)
                z = _Z.get(sku_service_level, 1.645)
                reorder_point = round(
                    avg_daily * lead_time
                    + z * avg_std * math.sqrt(lead_time), 2)
            else:
                avg_daily = avg_std = None
                coverage_days = None
                signal = "SIN_DATOS"
                recommended = None
                reorder_point = None

            items.append({
                "sku": sku,
                "warehouse": wh,
                "display_name": stock.get("display_name") if stock else None,
                "supplier": supplier,
                "current_stock": current_stock if stock else None,
                "lead_time_days": lead_time,
                "lead_time_source": lead_time_source,
                "moq": moq,
                "daily_demand": round(avg_daily, 4) if avg_daily is not None else None,
                "coverage_days": (round(coverage_days, 1)
                                  if coverage_days is not None and coverage_days < 9990
                                  else None),
                "reorder_point": reorder_point,
                "signal": signal,
                "recommended_qty": recommended,
                "recommended_action": None,
                "transfer_suggestion": None,
                "unit_cost": (float(stock["unit_cost"])
                              if stock and stock.get("unit_cost") is not None else None),
            })

    _network_transfer_pass(items)
    return items


def _network_transfer_pass(items: list[dict]) -> None:
    """
    Convert purchase recommendations into transfer suggestions where another
    warehouse of the same SKU can donate (spec 5.4 §2). Mutates items in place.

    A donor qualifies iff after donating qty = min(need, surplus):
      - its stock stays >= its own reorder point, and
      - its remaining coverage stays >= TRANSFER_MIN_DONOR_COVERAGE_DAYS.
    Best donor = highest post-donation coverage. The transfer replaces the
    order when it covers >= 80% of the need; below that the purchase stands.
    """
    by_sku: dict[str, list[dict]] = {}
    for it in items:
        by_sku.setdefault(it["sku"], []).append(it)

    for sku, rows in by_sku.items():
        needy = [r for r in rows
                 if r["signal"] in ("PEDIR_YA", "PEDIR_PRONTO")
                 and (r.get("recommended_qty") or 0) > 0]
        for r in needy:
            r["recommended_action"] = "order"
            need = float(r["recommended_qty"])
            best = None
            for d in rows:
                if d is r or not d.get("current_stock"):
                    continue
                daily = d.get("daily_demand") or 0.0
                reorder = d.get("reorder_point") or 0.0
                donatable = min(need, float(d["current_stock"]) - reorder)
                if daily > 0:
                    donatable = min(
                        donatable,
                        float(d["current_stock"])
                        - daily * TRANSFER_MIN_DONOR_COVERAGE_DAYS)
                if donatable <= 0:
                    continue
                after = float(d["current_stock"]) - donatable
                cov_after = after / daily if daily > 0 else 9999.0
                if cov_after < TRANSFER_MIN_DONOR_COVERAGE_DAYS:
                    continue
                if best is None or cov_after > best["cov_after"]:
                    best = {"donor": d, "qty": donatable, "cov_after": cov_after}
            if best and best["qty"] >= 0.8 * need:
                r["recommended_action"] = "transfer"
                r["transfer_suggestion"] = {
                    "from_warehouse": best["donor"]["warehouse"],
                    "qty": round(best["qty"], 2),
                    "donor_coverage_days_after": round(best["cov_after"], 1),
                }
```

(`import math` is already at the top of service.py.)

- [ ] **Step 4: Wire the API param**

In `backend/api/v1/inventory.py` `/status` endpoint (line ~280), add the param
and branch before the existing logic:

```python
    by_warehouse: bool = Query(default=False, description="Per-(sku, warehouse) rows with network transfer suggestions"),
```

```python
    if by_warehouse:
        items = svc.get_inventory_status_by_warehouse(user.tenant_id, session_id, service_level)
        items = _strip_abc_xyz_unless_entitled(items, user.tenant_id)
        if signal:
            items = [i for i in items if i["signal"] == signal.upper()]
        if supplier:
            items = [i for i in items if (i.get("supplier") or "").lower() == supplier.lower()]
        return ok({
            "items": items,
            "summary": {
                "total_rows": len(items),
                "order_now": sum(1 for i in items if i["signal"] == "PEDIR_YA"),
                "order_soon": sum(1 for i in items if i["signal"] == "PEDIR_PRONTO"),
                "transfers_suggested": sum(
                    1 for i in items if i.get("recommended_action") == "transfer"),
            },
        })
```

- [ ] **Step 5: Run new + regression tests**

Run: `cd backend && python -m pytest tests/test_status_by_warehouse.py tests/test_inventory.py tests/test_inventory_multi_bodega.py -q`
Expected: all pass (aggregated status untouched).

- [ ] **Step 6: Commit**

```bash
git add backend/inventory/service.py backend/api/v1/inventory.py backend/tests/test_status_by_warehouse.py
git commit -m "feat(inventory): per-warehouse semaphore with network transfer pass"
```

---

### Task 6: Transfer lifecycle service

**Files:**
- Create: `backend/inventory/transfer_service.py`
- Test: extend `backend/tests/test_transfers.py`

**Interfaces:**
- Consumes: `db.connection.transaction/execute/query/query_one`, `service.get_stock`, `warehouse_service.get_warehouse_by_name`, entitlements `enforce_limit` + `service.list_stock_keys/count_stock` (receive can create new (sku, warehouse) stock rows — same pre-checks as `receive_po`).
- Produces:
  - `create_transfer(tenant_id, user_id, from_warehouse, to_warehouse, items: list[dict], notes=None) -> dict` — `items: [{sku, qty}]`; decrements origin, status `in_transit`; `ValueError` on same warehouse, unknown warehouse, qty ≤ 0, or insufficient origin stock.
  - `receive_transfer(tenant_id, transfer_id, lines: list[dict] | None) -> dict` — `lines: [{sku, received_qty}]`, None = everything outstanding; increments destination; status `partial`/`received`.
  - `cancel_transfer(tenant_id, transfer_id) -> dict` — only `in_transit` with zero received; restores origin.
  - `list_transfers(tenant_id, status=None) -> list[dict]` (items embedded).
  - `get_transfer(tenant_id, transfer_id) -> dict | None`.

- [ ] **Step 1: Write the failing lifecycle tests** (append to `backend/tests/test_transfers.py`)

```python
import pytest

from backend.db.connection import query_one
from backend.inventory import service as inv_svc
from backend.inventory import transfer_service as tr_svc
from backend.inventory import warehouse_service as wh_svc


def _stock(tid, sku, wh):
    row = query_one(
        "SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND warehouse=%s",
        (tid, sku, wh))
    return float(row["current_stock"]) if row else None


@pytest.fixture()
def two_warehouses(test_tenant):
    tid = test_tenant["id"]
    wh_svc.create_warehouse(tid, "principal", is_default=True)
    wh_svc.create_warehouse(tid, "Norte")
    inv_svc.upsert_stock(tid, "A", {"current_stock": 100, "warehouse": "principal"})
    inv_svc.upsert_stock(tid, "A", {"current_stock": 10, "warehouse": "Norte"})
    return tid


class TestTransferLifecycle:
    def test_send_decrements_origin_only(self, two_warehouses, test_tenant, registered_user):
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, registered_user["id"], "principal", "Norte",
                                   [{"sku": "A", "qty": 30}])
        assert t["status"] == "in_transit"
        assert _stock(tid, "A", "principal") == 70.0
        assert _stock(tid, "A", "Norte") == 10.0  # in transit is nowhere

    def test_receive_full_completes(self, two_warehouses, test_tenant, registered_user):
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, registered_user["id"], "principal", "Norte",
                                   [{"sku": "A", "qty": 30}])
        out = tr_svc.receive_transfer(tid, t["id"], None)
        assert out["status"] == "received"
        assert _stock(tid, "A", "Norte") == 40.0
        header = query_one(
            "SELECT status, received_at FROM inventory_transfer_log WHERE id=%s", (t["id"],))
        assert header["status"] == "received"
        assert header["received_at"] is not None

    def test_partial_reception(self, two_warehouses, test_tenant, registered_user):
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, registered_user["id"], "principal", "Norte",
                                   [{"sku": "A", "qty": 30}])
        out = tr_svc.receive_transfer(tid, t["id"], [{"sku": "A", "received_qty": 20}])
        assert out["status"] == "partial"
        assert _stock(tid, "A", "Norte") == 30.0
        out2 = tr_svc.receive_transfer(tid, t["id"], [{"sku": "A", "received_qty": 10}])
        assert out2["status"] == "received"
        assert _stock(tid, "A", "Norte") == 40.0

    def test_over_reception_rejected(self, two_warehouses, test_tenant, registered_user):
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, registered_user["id"], "principal", "Norte",
                                   [{"sku": "A", "qty": 30}])
        with pytest.raises(ValueError):
            tr_svc.receive_transfer(tid, t["id"], [{"sku": "A", "received_qty": 31}])
        assert _stock(tid, "A", "Norte") == 10.0  # unchanged

    def test_cancel_restores_origin(self, two_warehouses, test_tenant, registered_user):
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, registered_user["id"], "principal", "Norte",
                                   [{"sku": "A", "qty": 30}])
        out = tr_svc.cancel_transfer(tid, t["id"])
        assert out["status"] == "cancelled"
        assert _stock(tid, "A", "principal") == 100.0

    def test_cancel_after_reception_rejected(self, two_warehouses, test_tenant, registered_user):
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, registered_user["id"], "principal", "Norte",
                                   [{"sku": "A", "qty": 30}])
        tr_svc.receive_transfer(tid, t["id"], [{"sku": "A", "received_qty": 5}])
        with pytest.raises(ValueError):
            tr_svc.cancel_transfer(tid, t["id"])

    def test_insufficient_stock_rejected_atomically(self, two_warehouses, test_tenant, registered_user):
        tid = two_warehouses
        with pytest.raises(ValueError):
            tr_svc.create_transfer(tid, registered_user["id"], "principal", "Norte",
                                   [{"sku": "A", "qty": 50},
                                    {"sku": "A", "qty": 60}])  # 110 > 100 total
        # First line must NOT have been applied
        assert _stock(tid, "A", "principal") == 100.0
        assert query_one(
            "SELECT COUNT(*)::int AS c FROM inventory_transfer_log WHERE tenant_id=%s",
            (tid,))["c"] == 0

    def test_same_warehouse_rejected(self, two_warehouses, test_tenant, registered_user):
        with pytest.raises(ValueError):
            tr_svc.create_transfer(two_warehouses, registered_user["id"],
                                   "principal", "principal", [{"sku": "A", "qty": 1}])
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_transfers.py -q`
Expected: schema tests pass; lifecycle tests FAIL with `ModuleNotFoundError: transfer_service`.

- [ ] **Step 3: Implement `backend/inventory/transfer_service.py`**

```python
"""
Inter-warehouse transfers (feature 5.4).

Send -> receive lifecycle mirroring PO reception: creating a transfer
decrements the origin warehouse inside one transaction (goods become
in-transit — owned by no warehouse), and the destination confirms arrival,
possibly partially. Stock therefore never shows units in two places, and
never shows in-transit units as available anywhere.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.db.connection import execute, query, query_one, transaction

log = logging.getLogger(__name__)

RECEIVABLE = ("in_transit", "partial")


def get_transfer(tenant_id: str, transfer_id: str, conn: Optional[Any] = None) -> Optional[dict]:
    header = query_one(
        "SELECT * FROM inventory_transfer_log WHERE id = %s AND tenant_id = %s",
        (transfer_id, tenant_id), conn=conn)
    if not header:
        return None
    header = dict(header)
    header["items"] = query(
        """SELECT id, sku, qty_sent, qty_received FROM inventory_transfer_items
           WHERE transfer_id = %s AND tenant_id = %s ORDER BY sku""",
        (transfer_id, tenant_id), conn=conn)
    return header


def list_transfers(tenant_id: str, status: Optional[str] = None) -> list[dict]:
    if status:
        headers = query(
            """SELECT * FROM inventory_transfer_log
               WHERE tenant_id = %s AND status = %s ORDER BY created_at DESC""",
            (tenant_id, status))
    else:
        headers = query(
            "SELECT * FROM inventory_transfer_log WHERE tenant_id = %s ORDER BY created_at DESC",
            (tenant_id,))
    if not headers:
        return []
    ids = tuple(h["id"] for h in headers)
    items = query(
        """SELECT id, transfer_id, sku, qty_sent, qty_received
           FROM inventory_transfer_items
           WHERE tenant_id = %s AND transfer_id IN %s ORDER BY sku""",
        (tenant_id, ids))
    by_transfer: dict[str, list[dict]] = {}
    for it in items:
        by_transfer.setdefault(it["transfer_id"], []).append(it)
    out = []
    for h in headers:
        d = dict(h)
        d["items"] = by_transfer.get(h["id"], [])
        out.append(d)
    return out


def create_transfer(
    tenant_id: str,
    user_id: str,
    from_warehouse: str,
    to_warehouse: str,
    items: list[dict],
    notes: Optional[str] = None,
) -> dict:
    """
    Create AND send a transfer: validates everything up front, then decrements
    the origin stock and writes header+items in one transaction.
    items: [{sku, qty}].
    """
    from backend.inventory import service as inv_svc
    from backend.inventory import warehouse_service as wh_svc

    from_warehouse = (from_warehouse or "").strip()
    to_warehouse = (to_warehouse or "").strip()
    if not from_warehouse or not to_warehouse:
        raise ValueError("Origin and destination warehouses are required")
    if from_warehouse == to_warehouse:
        raise ValueError("Origin and destination warehouses must differ")
    if not wh_svc.get_warehouse_by_name(tenant_id, from_warehouse):
        raise ValueError(f"Warehouse '{from_warehouse}' not found")
    if not wh_svc.get_warehouse_by_name(tenant_id, to_warehouse):
        raise ValueError(f"Warehouse '{to_warehouse}' not found")
    if not items:
        raise ValueError("A transfer needs at least one item")

    # Merge duplicate SKUs so the availability check sees the real total.
    qty_by_sku: dict[str, float] = {}
    for ln in items:
        sku = str(ln.get("sku") or "").strip()
        qty = float(ln.get("qty") or 0)
        if not sku:
            raise ValueError("Every transfer line needs a SKU")
        if qty <= 0:
            raise ValueError(f"Quantity for '{sku}' must be positive")
        qty_by_sku[sku] = qty_by_sku.get(sku, 0.0) + qty

    # Availability check BEFORE any write.
    for sku, qty in qty_by_sku.items():
        row = inv_svc.get_stock(tenant_id, sku, warehouse=from_warehouse)
        available = float(row["current_stock"]) if row else 0.0
        if qty > available:
            raise ValueError(
                f"Insufficient stock of '{sku}' in '{from_warehouse}' "
                f"({available:g} available, {qty:g} requested)")

    with transaction() as conn:
        header = query_one(
            """INSERT INTO inventory_transfer_log
                   (tenant_id, from_warehouse, to_warehouse, status, notes, created_by)
               VALUES (%s, %s, %s, 'in_transit', %s, %s)
               RETURNING *""",
            (tenant_id, from_warehouse, to_warehouse, notes, user_id),
            conn=conn)
        for sku, qty in sorted(qty_by_sku.items()):
            execute(
                """INSERT INTO inventory_transfer_items
                       (tenant_id, transfer_id, sku, qty_sent)
                   VALUES (%s, %s, %s, %s)""",
                (tenant_id, header["id"], sku, qty), conn=conn)
            execute(
                """UPDATE inventory_stock
                   SET current_stock = current_stock - %s, updated_at = NOW()
                   WHERE tenant_id = %s AND sku = %s AND warehouse = %s""",
                (qty, tenant_id, sku, from_warehouse), conn=conn)
            new_row = inv_svc.get_stock(tenant_id, sku, warehouse=from_warehouse, conn=conn)
            execute(
                "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock) VALUES (%s, %s, %s)",
                (tenant_id, sku, new_row["current_stock"]), conn=conn)
        result = get_transfer(tenant_id, header["id"], conn=conn)

    log.info("[transfer] sent tenant=%s id=%s %s->%s skus=%d",
             tenant_id, result["id"], from_warehouse, to_warehouse, len(qty_by_sku))
    return result


def receive_transfer(
    tenant_id: str, transfer_id: str, lines: Optional[list[dict]] = None
) -> dict:
    """
    Record arrival at the destination. lines: [{sku, received_qty}]; None means
    "everything outstanding arrived". Partial receptions accumulate.
    """
    from backend.entitlements.service import enforce_limit
    from backend.inventory import service as inv_svc
    from backend.inventory import warehouse_service as wh_svc

    t = get_transfer(tenant_id, transfer_id)
    if not t:
        raise ValueError("Transfer not found")
    if t["status"] not in RECEIVABLE:
        raise ValueError(f"This transfer cannot be received (status: {t['status']})")

    outstanding = {
        i["sku"]: float(i["qty_sent"]) - float(i["qty_received"] or 0)
        for i in t["items"]
    }
    if lines is None:
        to_receive = {sku: qty for sku, qty in outstanding.items() if qty > 0}
    else:
        to_receive = {}
        for ln in lines:
            sku = str(ln.get("sku") or "")
            if sku not in outstanding:
                raise ValueError(f"SKU '{sku}' is not part of this transfer")
            qty = float(ln.get("received_qty") or 0)
            if qty < 0:
                raise ValueError(f"Negative received quantity for '{sku}'")
            if qty > outstanding[sku]:
                raise ValueError(
                    f"'{sku}': receiving {qty:g} but only {outstanding[sku]:g} outstanding")
            if qty > 0:
                to_receive[sku] = qty
    if not to_receive:
        raise ValueError("Nothing to receive")

    dest = t["to_warehouse"]
    # Pre-check limits: destination rows that don't exist yet are NEW stock
    # rows created through upsert_stock (same chokepoint rules as PO reception).
    existing_keys = inv_svc.list_stock_keys(tenant_id)
    new_pairs = {(sku, dest) for sku in to_receive} - existing_keys
    if new_pairs:
        enforce_limit(tenant_id, "max_skus", inv_svc.count_stock(tenant_id),
                      adding=len(new_pairs))
    if dest not in wh_svc.list_warehouse_names(tenant_id):
        enforce_limit(tenant_id, "max_locations", wh_svc.count_warehouses(tenant_id))

    with transaction() as conn:
        for sku, qty in sorted(to_receive.items()):
            execute(
                """UPDATE inventory_transfer_items
                   SET qty_received = COALESCE(qty_received, 0) + %s
                   WHERE transfer_id = %s AND tenant_id = %s AND sku = %s""",
                (qty, transfer_id, tenant_id, sku), conn=conn)
            existing = inv_svc.get_stock(tenant_id, sku, warehouse=dest, conn=conn)
            if existing:
                execute(
                    """UPDATE inventory_stock
                       SET current_stock = current_stock + %s, updated_at = NOW()
                       WHERE tenant_id = %s AND sku = %s AND warehouse = %s""",
                    (qty, tenant_id, sku, dest), conn=conn)
            else:
                origin_row = inv_svc.get_stock(
                    tenant_id, sku, warehouse=t["from_warehouse"], conn=conn)
                inv_svc.upsert_stock(tenant_id, sku, {
                    "current_stock": qty,
                    "display_name": (origin_row or {}).get("display_name"),
                    "supplier": (origin_row or {}).get("supplier"),
                    "warehouse": dest,
                }, conn=conn)
            new_row = inv_svc.get_stock(tenant_id, sku, warehouse=dest, conn=conn)
            execute(
                "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock) VALUES (%s, %s, %s)",
                (tenant_id, sku, new_row["current_stock"]), conn=conn)

        fresh = get_transfer(tenant_id, transfer_id, conn=conn)
        fully = all(float(i["qty_received"] or 0) >= float(i["qty_sent"])
                    for i in fresh["items"])
        status = "received" if fully else "partial"
        execute(
            """UPDATE inventory_transfer_log
               SET status = %s, received_at = CASE WHEN %s THEN NOW() ELSE received_at END
               WHERE id = %s AND tenant_id = %s""",
            (status, fully, transfer_id, tenant_id), conn=conn)
        result = get_transfer(tenant_id, transfer_id, conn=conn)

    log.info("[transfer] received tenant=%s id=%s status=%s", tenant_id, transfer_id, status)
    return result


def cancel_transfer(tenant_id: str, transfer_id: str) -> dict:
    """Cancel an in-transit transfer with nothing received: goods go back home."""
    t = get_transfer(tenant_id, transfer_id)
    if not t:
        raise ValueError("Transfer not found")
    if t["status"] != "in_transit" or any(
            float(i["qty_received"] or 0) > 0 for i in t["items"]):
        raise ValueError("Only in-transit transfers with nothing received can be cancelled")

    from backend.inventory import service as inv_svc

    with transaction() as conn:
        for i in t["items"]:
            execute(
                """UPDATE inventory_stock
                   SET current_stock = current_stock + %s, updated_at = NOW()
                   WHERE tenant_id = %s AND sku = %s AND warehouse = %s""",
                (float(i["qty_sent"]), tenant_id, i["sku"], t["from_warehouse"]),
                conn=conn)
            new_row = inv_svc.get_stock(
                tenant_id, i["sku"], warehouse=t["from_warehouse"], conn=conn)
            execute(
                "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock) VALUES (%s, %s, %s)",
                (tenant_id, i["sku"], new_row["current_stock"]), conn=conn)
        execute(
            """UPDATE inventory_transfer_log SET status = 'cancelled'
               WHERE id = %s AND tenant_id = %s""",
            (transfer_id, tenant_id), conn=conn)
        result = get_transfer(tenant_id, transfer_id, conn=conn)

    log.info("[transfer] cancelled tenant=%s id=%s", tenant_id, transfer_id)
    return result
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_transfers.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/transfer_service.py backend/tests/test_transfers.py
git commit -m "feat(inventory): atomic send/receive/cancel transfer lifecycle"
```

---

### Task 7: Transfer API endpoints

**Files:**
- Modify: `backend/api/v1/inventory.py` (new endpoints after the reception endpoints; find with `grep -n "receive" backend/api/v1/inventory.py`)
- Test: extend `backend/tests/test_transfers.py`

**Interfaces:**
- Consumes: Task 6 service functions.
- Produces REST surface:
  - `POST /inventory/transfers` (analyst+) body `{from_warehouse, to_warehouse, items: [{sku, qty}], notes?}` → 201 `ok(transfer)`
  - `GET /inventory/transfers?status=` (any user) → `ok([transfers])`
  - `POST /inventory/transfers/{transfer_id}/receive` (analyst+) body `{lines: [{sku, received_qty}] | null}` → `ok(transfer)`
  - `POST /inventory/transfers/{transfer_id}/cancel` (analyst+) → `ok(transfer)`
  - `ValueError` from the service maps to 422 (404 when message is "Transfer not found").

- [ ] **Step 1: Write the failing API tests** (append to `backend/tests/test_transfers.py`)

```python
class TestTransferApi:
    def _setup(self, tid):
        wh_svc.create_warehouse(tid, "principal", is_default=True)
        wh_svc.create_warehouse(tid, "Norte")
        inv_svc.upsert_stock(tid, "A", {"current_stock": 100, "warehouse": "principal"})

    def test_viewer_denied_state_unchanged(self, client, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        self._setup(tid)
        r = client.post("/api/v1/inventory/transfers", json={
            "from_warehouse": "principal", "to_warehouse": "Norte",
            "items": [{"sku": "A", "qty": 10}],
        }, headers=viewer_headers)
        assert r.status_code == 403
        assert _stock(tid, "A", "principal") == 100.0
        assert query_one(
            "SELECT COUNT(*)::int AS c FROM inventory_transfer_log WHERE tenant_id=%s",
            (tid,))["c"] == 0

    def test_analyst_full_cycle(self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        self._setup(tid)
        r = client.post("/api/v1/inventory/transfers", json={
            "from_warehouse": "principal", "to_warehouse": "Norte",
            "items": [{"sku": "A", "qty": 10}],
        }, headers=analyst_headers)
        assert r.status_code == 201, r.text
        transfer_id = r.json()["data"]["id"]
        assert _stock(tid, "A", "principal") == 90.0

        r = client.get("/api/v1/inventory/transfers?status=in_transit",
                       headers=analyst_headers)
        assert r.status_code == 200
        assert len(r.json()["data"]) == 1

        r = client.post(f"/api/v1/inventory/transfers/{transfer_id}/receive",
                        json={"lines": None}, headers=analyst_headers)
        assert r.status_code == 200
        assert r.json()["data"]["status"] == "received"
        assert _stock(tid, "A", "Norte") == 10.0

    def test_cross_tenant_denied(self, client, analyst_headers, test_tenant, second_tenant_admin_headers):
        """A transfer of tenant A must be invisible/untouchable from tenant B.
        conftest note: check for an existing second-tenant fixture (grep
        'second_tenant' in conftest.py); if absent, create a second tenant via
        the same create_tenant helper test_tenant uses and log its admin in."""
        tid = test_tenant["id"]
        self._setup(tid)
        r = client.post("/api/v1/inventory/transfers", json={
            "from_warehouse": "principal", "to_warehouse": "Norte",
            "items": [{"sku": "A", "qty": 10}],
        }, headers=analyst_headers)
        transfer_id = r.json()["data"]["id"]
        r2 = client.post(f"/api/v1/inventory/transfers/{transfer_id}/receive",
                         json={"lines": None}, headers=second_tenant_admin_headers)
        assert r2.status_code == 404
        assert _stock(tid, "A", "Norte") is None  # nothing arrived
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_transfers.py::TestTransferApi -q`
Expected: 404s — endpoints don't exist.

- [ ] **Step 3: Implement the endpoints**

In `backend/api/v1/inventory.py` (import `from backend.inventory import transfer_service as tr_svc` at the top with its siblings):

```python
# ── Inter-warehouse transfers (feature 5.4) ──────────────────────────────────

class TransferItemIn(BaseModel):
    sku: str
    qty: float = Field(gt=0)


class TransferCreate(BaseModel):
    from_warehouse: str
    to_warehouse: str
    items: list[TransferItemIn]
    notes: Optional[str] = None


class TransferReceive(BaseModel):
    lines: Optional[list[dict]] = None  # [{sku, received_qty}] | null = all


def _transfer_error(e: ValueError) -> HTTPException:
    msg = str(e)
    return HTTPException(status_code=404 if "not found" in msg.lower() else 422,
                         detail=msg)


@router.post("/transfers", status_code=201)
def create_transfer(
    body: TransferCreate,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    try:
        t = tr_svc.create_transfer(
            user.tenant_id, user.id, body.from_warehouse, body.to_warehouse,
            [i.model_dump() for i in body.items], body.notes)
    except ValueError as e:
        raise _transfer_error(e)
    return ok(t)


@router.get("/transfers")
def list_transfers(
    status: Optional[str] = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
):
    return ok(tr_svc.list_transfers(user.tenant_id, status))


@router.post("/transfers/{transfer_id}/receive")
def receive_transfer(
    transfer_id: str,
    body: TransferReceive,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    try:
        t = tr_svc.receive_transfer(user.tenant_id, transfer_id, body.lines)
    except ValueError as e:
        raise _transfer_error(e)
    return ok(t)


@router.post("/transfers/{transfer_id}/cancel")
def cancel_transfer(
    transfer_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    try:
        t = tr_svc.cancel_transfer(user.tenant_id, transfer_id)
    except ValueError as e:
        raise _transfer_error(e)
    return ok(t)
```

Check `user.id` vs the actual attribute on `CurrentUser` (grep `class CurrentUser` in `backend/auth/guards.py`) — use whatever field carries the user id.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_transfers.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/api/v1/inventory.py backend/tests/test_transfers.py
git commit -m "feat(api): transfer endpoints with permission and tenant isolation"
```

---

### Task 8: PO destination warehouse

**Files:**
- Modify: `backend/inventory/roi_service.py` (`log_po_generation` — persist `destination_warehouse`; find with `grep -n "def log_po_generation" backend/inventory/roi_service.py`)
- Modify: `backend/api/v1/inventory.py:742-771` (`log_po` accepts `destination_warehouse`)
- Modify: `backend/inventory/reception_service.py` (`receive_po` — PO lines without their own warehouse fall back to the header's `destination_warehouse` before `'principal'`)
- Test: `backend/tests/test_po_destination.py`

**Interfaces:**
- Consumes: existing `POLogRequest` body model in `api/v1/inventory.py` (add optional `destination_warehouse: Optional[str] = None`); `log_po_generation(tenant_id, session_id, po_items, destination_warehouse=None)` gains the kwarg and stores it on the header.
- Produces: reception line warehouse resolution order: `item.warehouse` → `po.destination_warehouse` → `'principal'`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_po_destination.py
"""PO destination warehouse (feature 5.4, spec §4)."""

from backend.db.connection import query_one
from backend.inventory import warehouse_service as wh_svc


def _login_po(client, headers, session_id, destination=None):
    body = {
        "items": [{
            "sku": "A", "display_name": "A", "supplier": "Acme",
            "signal": "PEDIR_YA", "status": "approved",
            "recommended_qty": 10, "final_qty": 10, "unit_cost": 2.0,
        }],
    }
    if destination:
        body["destination_warehouse"] = destination
    return client.post(f"/api/v1/inventory/log-po?session_id={session_id}",
                       json=body, headers=headers)


class TestPoDestination:
    def test_destination_persisted(self, client, auth_headers, test_tenant, completed_session):
        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "Norte")
        r = _login_po(client, auth_headers, completed_session["id"], destination="Norte")
        assert r.status_code == 201, r.text
        po_id = r.json()["data"]["po_log_id"]
        row = query_one(
            "SELECT destination_warehouse FROM inventory_po_log WHERE id=%s", (po_id,))
        assert row["destination_warehouse"] == "Norte"

    def test_reception_lands_in_destination(self, client, auth_headers, test_tenant, completed_session):
        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "Norte")
        r = _login_po(client, auth_headers, completed_session["id"], destination="Norte")
        po_id = r.json()["data"]["po_log_id"]
        r = client.post(f"/api/v1/inventory/po/{po_id}/receive", json={},
                        headers=auth_headers)
        assert r.status_code == 200, r.text
        row = query_one(
            """SELECT current_stock FROM inventory_stock
               WHERE tenant_id=%s AND sku='A' AND warehouse='Norte'""", (tid,))
        assert row is not None and float(row["current_stock"]) == 10.0

    def test_null_destination_keeps_today_behavior(self, client, auth_headers, test_tenant, completed_session):
        r = _login_po(client, auth_headers, completed_session["id"])
        assert r.status_code == 201
        po_id = r.json()["data"]["po_log_id"]
        row = query_one(
            "SELECT destination_warehouse FROM inventory_po_log WHERE id=%s", (po_id,))
        assert row["destination_warehouse"] is None
```

Check the actual reception endpoint path (grep `"/po/" backend/api/v1/inventory.py`) and the exact `POLogRequest` item field names (grep `class POLogRequest`) — adjust the test body to match before running.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_po_destination.py -q`
Expected: FAIL — `destination_warehouse` is None / stock lands in `'principal'`.

- [ ] **Step 3: Implement**

1. `POLogRequest` gains `destination_warehouse: Optional[str] = None`; `log_po` passes it: `log_po_generation(user.tenant_id, session_id, po_items, destination_warehouse=body.destination_warehouse if body else None)`.
2. `log_po_generation` inserts it into the `inventory_po_log` INSERT column list (add the column and `%s` param).
3. `reception_service.receive_po`: everywhere a line's warehouse is resolved as `i.get("warehouse") or "principal"` (four occurrences: the max_locations pre-check, the max_skus pre-check, and the two in the stock-write loop), change to `i.get("warehouse") or po.get("destination_warehouse") or "principal"`. Extract it once per line into a small helper to avoid repeating the chain:

```python
def _line_warehouse(item: dict, po: dict) -> str:
    """A PO line lands in its own warehouse if set, else the PO's destination,
    else the historical default."""
    return item.get("warehouse") or po.get("destination_warehouse") or "principal"
```

- [ ] **Step 4: Run new + regression tests**

Run: `cd backend && python -m pytest tests/test_po_destination.py tests/test_reception_bodega.py tests/test_po_send.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/api/v1/inventory.py backend/inventory/roi_service.py backend/inventory/reception_service.py backend/tests/test_po_destination.py
git commit -m "feat(po): destination warehouse on PO log and reception"
```

---

### Task 9: Full regression + plan doc update

**Files:**
- Modify: `docs/plan_general_faro_2026-07-18.md` (status table: 5.4 backend done)

- [ ] **Step 1: Run the full backend suite**

Run: `cd backend && python -m pytest tests/ -q`
Expected: 0 failures (baseline was 818 passed / 19 skipped plus the new tests).

- [ ] **Step 2: Typecheck untouched frontend**

Run: `cd Frontend && npx tsc --noEmit`
Expected: exit 0 (no frontend files touched in this plan).

- [ ] **Step 3: Update the plan doc and commit**

Add a row/update to the status table in `docs/plan_general_faro_2026-07-18.md`:
`5.4 (backend) | ✅ | per-warehouse semaphore + transfers + PO destination; UI pending (see 2026-07-22 UI plan)`.

```bash
git add docs/plan_general_faro_2026-07-18.md
git commit -m "docs: mark 5.4 backend complete in general plan"
```
