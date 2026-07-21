# Accounting Integrations (Alegra + Siigo Import) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an Enterprise tenant connect Alegra or Siigo and import products + sales + stock, then auto-train so the semáforo refreshes — no CSV.

**Architecture:** A generic `AccountingProvider` interface with `AlegraProvider` (Basic auth) and `SiigoProvider` (OAuth2) implementations behind a registry. A sync service fetches → upserts `inventory_stock` (via the existing validated `upsert_stock`) → builds a sales dataset + session → enqueues training (reusing the demo/quickstart pipeline). Credentials are Fernet-encrypted at rest. Gated by a new `Feature.INTEGRATIONS` (Enterprise). Manual sync + daily scheduler.

**Tech Stack:** Python 3 / FastAPI / psycopg2 / `cryptography` (Fernet) / `requests` or `httpx`; pytest with a patched HTTP seam (never hits real APIs); Next.js 14 / TS frontend.

## Global Constraints

- All code (identifiers, comments, docstrings, test names, commit messages) is **English**. Only end-user copy may be Spanish. (CLAUDE.md)
- No ML/pandas in `backend/` beyond what the existing dataset pipeline already does.
- `settings.testing_mode == True` **bypasses** entitlement checks (mirror existing gates); enforcement tests `monkeypatch settings.testing_mode = False` themselves.
- Migrations are idempotent (`CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`), appended to `_MIGRATIONS` in `backend/db/migrations.py`.
- Tests assert **state via direct DB queries**; every mutating endpoint has a **permission pair** (viewer 403 + state unchanged AND analyst/admin success).
- **Tests MUST NOT make real network calls** — patch the integrations HTTP seam in every test; a real call must fail loudly.
- Provider values are exactly `'alegra'` and `'siigo'`.
- Credentials are **never** returned in any API response and are **encrypted at rest**.
- Reuse, do not reinvent: `inventory.service.upsert_stock` (already validates + enforces `max_skus`), the dataset/session/training pipeline from `backend/api/v1/demo.py` (`job_service.create_job` + `session_svc.set_last_job` + `session_svc.transition(...,"QUEUED",...)`, `session_svc.create_session/attach_dataset/force_status`, `session_store.set_field`, `backend.storage.paths.dataset_dir`), and the `transaction()` context manager in `backend/db/connection.py`.

---

## File Structure

**Create:**
- `backend/integrations/__init__.py`
- `backend/integrations/crypto.py` — Fernet encrypt/decrypt.
- `backend/integrations/base.py` — DTOs, `AccountingProvider` ABC, error types.
- `backend/integrations/http.py` — one patchable HTTP seam (`get_json`, `post_json`) with timeouts.
- `backend/integrations/alegra.py` — `AlegraProvider`.
- `backend/integrations/siigo.py` — `SiigoProvider`.
- `backend/integrations/registry.py` — `get_provider`, `SUPPORTED_PROVIDERS`.
- `backend/integrations/store.py` — `integration_connections` CRUD.
- `backend/integrations/sync_service.py` — `sync_connection`, `run_daily_integration_syncs`.
- `backend/api/v1/integrations.py` — router.
- `backend/tests/test_integrations_crypto.py`, `test_integrations_providers.py`, `test_integrations_sync.py`, `test_integrations_api.py`.
- `Frontend/src/app/integraciones/page.tsx`

**Modify:**
- `backend/config.py` — `integrations_secret_key`, `alegra_base_url`, `siigo_base_url`.
- `backend/db/migrations.py` — `integration_connections` table.
- `backend/entitlements/plans.py` — `Feature.INTEGRATIONS` (Enterprise).
- `backend/main.py` — register `integrations.router`.
- `backend/workers/worker.py` — daily sync loop.
- `backend/tenants/data_export.py` — add `integration_connections` (credentials excluded) to `_EXPORT_SPECS` + `_DELETE_ORDER`.
- `requirements.txt` — `cryptography` if missing.
- `Frontend/src/lib/api.ts` — integration API + types.
- `Frontend/src/components/layout/Sidebar.tsx` — nav entry gated by `integrations`.
- `Frontend/src/i18n/translations.ts` — copy keys.

---

## Task 1: `Feature.INTEGRATIONS` in the entitlements catalog

**Files:**
- Modify: `backend/entitlements/plans.py`
- Test: `backend/tests/test_entitlements.py` (append)

**Interfaces:**
- Produces: `Feature.INTEGRATIONS = "integrations"`, present ONLY in the `enterprise` plan's features.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_entitlements.py`:

```python
def test_integrations_is_enterprise_only():
    from backend.entitlements.plans import Feature, PLAN_CATALOG
    assert Feature.INTEGRATIONS not in PLAN_CATALOG["starter"].features
    assert Feature.INTEGRATIONS not in PLAN_CATALOG["professional"].features
    assert Feature.INTEGRATIONS in PLAN_CATALOG["enterprise"].features
```

- [ ] **Step 2: Run it, expect FAIL** (`AttributeError: INTEGRATIONS`).
Run: `cd backend && "$PY" -m pytest tests/test_entitlements.py -k integrations_is_enterprise -q`

- [ ] **Step 3: Implement**

In `backend/entitlements/plans.py`: add `INTEGRATIONS = "integrations"` to the `Feature` enum (Enterprise section), and add `Feature.INTEGRATIONS` to `_ENT_EXTRA`.

- [ ] **Step 4: Run it, expect PASS.** Also run the catalog-superset test to confirm tiers still compose.

- [ ] **Step 5: Commit**
```bash
git add backend/entitlements/plans.py backend/tests/test_entitlements.py
git commit -m "feat(entitlements): INTEGRATIONS feature (enterprise-only)"
```

---

## Task 2: Credential encryption (`crypto.py`) + config

**Files:**
- Modify: `backend/config.py`
- Create: `backend/integrations/__init__.py` (empty), `backend/integrations/crypto.py`
- Modify: `requirements.txt` (add `cryptography>=42` if absent — grep first)
- Test: `backend/tests/test_integrations_crypto.py`

**Interfaces:**
- Produces: `encrypt_credentials(data: dict) -> str`, `decrypt_credentials(token: str) -> dict`, `integrations_enabled() -> bool`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_integrations_crypto.py`:

```python
import pytest


def test_encrypt_roundtrip_and_ciphertext(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setattr("backend.config.settings.integrations_secret_key", Fernet.generate_key().decode())
    from backend.integrations import crypto
    creds = {"email": "a@b.com", "token": "SECRET-123"}
    enc = crypto.encrypt_credentials(creds)
    assert "SECRET-123" not in enc          # not plaintext
    assert crypto.decrypt_credentials(enc) == creds


def test_disabled_without_key(monkeypatch):
    monkeypatch.setattr("backend.config.settings.integrations_secret_key", "")
    from backend.integrations import crypto
    assert crypto.integrations_enabled() is False
    with pytest.raises(RuntimeError):
        crypto.encrypt_credentials({"x": "y"})
```

- [ ] **Step 2: Run it, expect FAIL** (module missing).

- [ ] **Step 3: Implement**

In `backend/config.py` add fields:
```python
    integrations_secret_key: str = ""
    alegra_base_url: str = "https://api.alegra.com/api/v1"
    siigo_base_url: str = "https://api.siigo.com/v1"
```

`backend/integrations/crypto.py`:
```python
"""Encrypt integration credentials at rest with Fernet."""
import json
from cryptography.fernet import Fernet

from backend.config import settings


def integrations_enabled() -> bool:
    return bool(settings.integrations_secret_key)


def _fernet() -> Fernet:
    if not settings.integrations_secret_key:
        raise RuntimeError("INTEGRATIONS_SECRET_KEY not configured")
    return Fernet(settings.integrations_secret_key.encode())


def encrypt_credentials(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_credentials(token: str) -> dict:
    return json.loads(_fernet().decrypt(token.encode()).decode())
```

`grep -q "^cryptography" requirements.txt || echo "cryptography>=42" >> requirements.txt` (confirm the venv already has it: `"$PY" -c "import cryptography"`).

- [ ] **Step 4: Run it, expect PASS.**

- [ ] **Step 5: Commit** `feat(integrations): Fernet credential encryption + config`

---

## Task 3: Migration — `integration_connections` table

**Files:**
- Modify: `backend/db/migrations.py`
- Test: `backend/tests/test_integrations_crypto.py` (append a DB test) or a new `test_integrations_store.py`

**Interfaces:**
- Produces: table `integration_connections` per the spec DDL.

- [ ] **Step 1: Write the failing test**

```python
def test_integration_connections_table_exists(client):  # client fixture runs migrations
    from backend.db.connection import query_one
    row = query_one("""SELECT column_name FROM information_schema.columns
                       WHERE table_name='integration_connections' AND column_name='credentials'""")
    assert row is not None
```

- [ ] **Step 2: Run it, expect FAIL** (table missing).

- [ ] **Step 3: Implement** — append to `_MIGRATIONS`:
```python
    ("create_integration_connections",
     """CREATE TABLE IF NOT EXISTS integration_connections (
         id           TEXT PRIMARY KEY,
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         provider     TEXT NOT NULL,
         credentials  TEXT NOT NULL,
         status       TEXT NOT NULL DEFAULT 'connected',
         last_sync_at TIMESTAMPTZ,
         last_error   TEXT,
         created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_integration_connections_uniq",
     "CREATE UNIQUE INDEX IF NOT EXISTS integration_conn_tenant_provider_idx "
     "ON integration_connections (tenant_id, provider)"),
```

- [ ] **Step 4: Run it, expect PASS.**

- [ ] **Step 5: Commit** `feat(integrations): integration_connections table`

---

## Task 4: Provider base — DTOs, ABC, errors, HTTP seam

**Files:**
- Create: `backend/integrations/base.py`, `backend/integrations/http.py`
- Test: `backend/tests/test_integrations_providers.py`

**Interfaces:**
- Produces: `ProviderProduct`, `ProviderStock`, `ProviderSaleLine` dataclasses; `AccountingProvider` ABC with `test_connection`, `fetch_products`, `fetch_stock`, `fetch_sales(since)`; `IntegrationAuthError`, `IntegrationSyncError`; `http.get_json(url, headers=..., params=..., auth=...)`, `http.post_json(...)`.

- [ ] **Step 1: Write the failing test** (a tiny concrete subclass proves the ABC + DTO shapes)

```python
def test_provider_abc_and_dtos():
    from datetime import date
    from backend.integrations.base import (
        AccountingProvider, ProviderProduct, ProviderStock, ProviderSaleLine,
    )
    p = ProviderProduct(sku="A", name="Aceite", unit_cost=5.0)
    s = ProviderStock(sku="A", quantity=10.0, warehouse="principal")
    line = ProviderSaleLine(date=date(2026, 1, 1), sku="A", quantity=3.0, unit_price=8.0)
    assert (p.sku, s.quantity, line.quantity) == ("A", 10.0, 3.0)

    class Dummy(AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return [p]
        def fetch_stock(self): return [s]
        def fetch_sales(self, since=None): return [line]
    d = Dummy({})
    assert d.fetch_products()[0].sku == "A"
```

- [ ] **Step 2: Run it, expect FAIL.**

- [ ] **Step 3: Implement**

`backend/integrations/base.py`:
```python
"""Provider-agnostic contract + canonical DTOs for accounting imports."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Optional


class IntegrationAuthError(Exception):
    """Credentials rejected by the provider."""


class IntegrationSyncError(Exception):
    """A recoverable failure while fetching/mapping provider data."""


@dataclass
class ProviderProduct:
    sku: str
    name: str
    unit_cost: Optional[float]


@dataclass
class ProviderStock:
    sku: str
    quantity: float
    warehouse: str  # 'principal' when the provider has no warehouse concept


@dataclass
class ProviderSaleLine:
    date: date
    sku: str
    quantity: float
    unit_price: Optional[float]


class AccountingProvider(ABC):
    def __init__(self, credentials: dict):
        self.credentials = credentials

    @abstractmethod
    def test_connection(self) -> None: ...
    @abstractmethod
    def fetch_products(self) -> list[ProviderProduct]: ...
    @abstractmethod
    def fetch_stock(self) -> list[ProviderStock]: ...
    @abstractmethod
    def fetch_sales(self, since: Optional[date] = None) -> list[ProviderSaleLine]: ...
```

`backend/integrations/http.py` (the single patchable seam — tests patch `backend.integrations.http._request`):
```python
"""One HTTP seam for all providers so tests can patch a single function."""
from typing import Any, Optional
import requests

_TIMEOUT = 20


def _request(method: str, url: str, **kwargs) -> Any:
    kwargs.setdefault("timeout", _TIMEOUT)
    resp = requests.request(method, url, **kwargs)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def get_json(url: str, *, headers: Optional[dict] = None, params: Optional[dict] = None, auth=None) -> Any:
    return _request("GET", url, headers=headers, params=params, auth=auth)


def post_json(url: str, *, headers: Optional[dict] = None, json: Optional[dict] = None, auth=None) -> Any:
    return _request("POST", url, headers=headers, json=json, auth=auth)
```

- [ ] **Step 4: Run it, expect PASS.**

- [ ] **Step 5: Commit** `feat(integrations): provider ABC, DTOs, HTTP seam`

---

## Task 5: `AlegraProvider` (Basic auth)

**Files:**
- Create: `backend/integrations/alegra.py`
- Test: `backend/tests/test_integrations_providers.py` (append)

**Interfaces:**
- Consumes: base DTOs, `http`, `settings.alegra_base_url`.
- Produces: `AlegraProvider(AccountingProvider)` mapping `/items`→products+stock, `/invoices`→sales.

- [ ] **Step 1: Write the failing test** (patch the HTTP seam with canned Alegra payloads)

```python
def test_alegra_maps_items_and_invoices(monkeypatch):
    from backend.integrations.alegra import AlegraProvider
    from backend.integrations import http

    items = [{"reference": "A", "name": "Aceite", "price": [{"price": 8}],
              "unitCost": 5, "inventory": {"availableQuantity": 10, "unit": "und"}}]
    invoices = [{"date": "2026-01-01", "items": [{"reference": "A", "quantity": 3, "price": 8}]}]

    def fake_get(url, **kw):
        return items if url.endswith("/items") else invoices if url.endswith("/invoices") else []
    monkeypatch.setattr(http, "get_json", fake_get)

    p = AlegraProvider({"email": "a@b.com", "token": "t"})
    prods = p.fetch_products(); stock = p.fetch_stock(); sales = p.fetch_sales()
    assert prods[0].sku == "A" and prods[0].unit_cost == 5
    assert stock[0].sku == "A" and stock[0].quantity == 10
    assert sales[0].sku == "A" and sales[0].quantity == 3
```

> **Field names are indicative.** Before finalizing, the implementer confirms the exact Alegra `/items` and `/invoices` JSON field names against the live API docs (<https://developer.alegra.com/>). Adjust the mapper AND this test's canned payload together to the real shape. The mapping logic (product/stock/sale extraction) is what matters; the exact keys are pinned at implementation time.

- [ ] **Step 2: Run it, expect FAIL.**

- [ ] **Step 3: Implement** `AlegraProvider`:
- Basic auth = `requests` `HTTPBasicAuth(email, token)` (or the `auth=` kwarg on `http.get_json`).
- `test_connection`: `GET {base}/items?limit=1`; on 401 raise `IntegrationAuthError`.
- `fetch_products`/`fetch_stock`: page `GET {base}/items` (`start`/`limit`), map each item → `ProviderProduct` (sku=reference/code, name, unit_cost) and `ProviderStock` (sku, quantity=inventory.availableQuantity, warehouse="principal"). Skip items with no reference.
- `fetch_sales(since)`: page `GET {base}/invoices` (filter by date ≥ since if supported), flatten line items → `ProviderSaleLine`.
- Wrap request errors in `IntegrationSyncError`; a 401 → `IntegrationAuthError`.

- [ ] **Step 4: Run it, expect PASS.**

- [ ] **Step 5: Commit** `feat(integrations): AlegraProvider (items + invoices import)`

---

## Task 6: `SiigoProvider` (OAuth2 + rate limits)

**Files:**
- Create: `backend/integrations/siigo.py`
- Test: `backend/tests/test_integrations_providers.py` (append)

**Interfaces:**
- Produces: `SiigoProvider(AccountingProvider)` — fetches a Bearer token from `{partner_id, username, access_key}` via `POST {base}/auth`, then maps `/products`→products+stock, `/invoices`→sales; paginates and backs off on HTTP 429.

- [ ] **Step 1: Write the failing test** (patch `http.post_json` for auth + `http.get_json` for data)

```python
def test_siigo_auths_then_maps(monkeypatch):
    from backend.integrations.siigo import SiigoProvider
    from backend.integrations import http

    monkeypatch.setattr(http, "post_json", lambda url, **kw: {"access_token": "TOK"})
    products = [{"code": "A", "name": "Aceite", "available_quantity": 10,
                 "prices": [{"price_list": [{"value": 8}]}], "unit_cost": 5}]
    invoices = [{"date": "2026-01-01", "items": [{"code": "A", "quantity": 3, "price": 8}]}]
    def fake_get(url, **kw):
        return products if "/products" in url else invoices if "/invoices" in url else []
    monkeypatch.setattr(http, "get_json", fake_get)

    p = SiigoProvider({"partner_id": "faro", "username": "u", "access_key": "k"})
    assert p.fetch_products()[0].sku == "A"
    assert p.fetch_stock()[0].quantity == 10
    assert p.fetch_sales()[0].quantity == 3
```

> Same note as Task 5: confirm Siigo's real field names (<https://developers.siigo.com/>) and adjust mapper + canned payload together. Rate-limit backoff: on `requests.HTTPError` with status 429, sleep-and-retry a bounded number of times (make the sleep patchable/zero in tests).

- [ ] **Step 2: Run it, expect FAIL.**

- [ ] **Step 3: Implement** `SiigoProvider` — lazy `_token()` (cache after first `POST /auth` with `Partner-Id` header), Bearer header on all reads, pagination, bounded 429 backoff, error normalization.

- [ ] **Step 4: Run it, expect PASS.**

- [ ] **Step 5: Commit** `feat(integrations): SiigoProvider (OAuth2 + products/invoices import)`

---

## Task 7: Registry + connections store

**Files:**
- Create: `backend/integrations/registry.py`, `backend/integrations/store.py`
- Test: `backend/tests/test_integrations_sync.py` (store part)

**Interfaces:**
- Produces:
  - `registry.SUPPORTED_PROVIDERS = {"alegra": {"fields": ["email","token"]}, "siigo": {"fields": ["partner_id","username","access_key"]}}`; `registry.get_provider(name, credentials) -> AccountingProvider` (raises for unknown).
  - `store.create_connection(tenant_id, provider, credentials_dict) -> dict` (encrypts), `store.list_connections(tenant_id) -> list[dict]` (NO credentials), `store.get_connection(tenant_id, id) -> dict|None` (internal, includes decrypted creds only via a separate `store.get_credentials(id)`), `store.delete_connection(tenant_id, id)`, `store.mark_synced(id, error=None)`.

- [ ] **Step 1: Write the failing test**

```python
def test_store_encrypts_and_hides_credentials(client, test_tenant, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setattr("backend.config.settings.integrations_secret_key", Fernet.generate_key().decode())
    from backend.integrations import store
    from backend.db.connection import query_one
    tid = test_tenant["id"]
    conn = store.create_connection(tid, "alegra", {"email": "a@b.com", "token": "SECRET"})
    # stored ciphertext, not plaintext
    row = query_one("SELECT credentials FROM integration_connections WHERE id=%s", (conn["id"],))
    assert "SECRET" not in row["credentials"]
    # list never exposes credentials
    listed = store.list_connections(tid)
    assert "credentials" not in listed[0]
    assert store.get_credentials(conn["id"]) == {"email": "a@b.com", "token": "SECRET"}
```

- [ ] **Step 2–4:** Implement `registry.py` + `store.py` (psycopg2 via `backend.db.connection`; `generate_id("intg")`), run, PASS.

- [ ] **Step 5: Commit** `feat(integrations): provider registry + encrypted connection store`

---

## Task 8: Sync service (fetch → stock → dataset → auto-train)

**Files:**
- Create: `backend/integrations/sync_service.py`
- Test: `backend/tests/test_integrations_sync.py` (append)

**Interfaces:**
- Consumes: `store`, `registry`, `inventory.service.upsert_stock`, the demo pipeline functions, `backend.db.connection.transaction`.
- Produces: `sync_connection(connection_id) -> dict` (upserts stock, builds dataset+session from sales, enqueues training, updates last_sync_at); `run_daily_integration_syncs()`.

- [ ] **Step 1: Write the failing test** (fake provider via monkeypatching `registry.get_provider`)

```python
def test_sync_imports_stock_dataset_and_enqueues_training(client, test_tenant, monkeypatch):
    from datetime import date
    from cryptography.fernet import Fernet
    monkeypatch.setattr("backend.config.settings.integrations_secret_key", Fernet.generate_key().decode())
    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    from backend.integrations import store, registry, sync_service, base
    from backend.db.connection import query_one, query

    class FakeProvider(base.AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return [base.ProviderProduct("SKU-Z", "Zeta", 5.0)]
        def fetch_stock(self): return [base.ProviderStock("SKU-Z", 12.0, "principal")]
        def fetch_sales(self, since=None):
            return [base.ProviderSaleLine(date(2026, 1, d), "SKU-Z", 3.0, 8.0) for d in range(1, 20)]
    monkeypatch.setattr(registry, "get_provider", lambda name, creds: FakeProvider(creds))

    tid = test_tenant["id"]
    conn = store.create_connection(tid, "alegra", {"email": "a@b.com", "token": "t"})
    sync_service.sync_connection(conn["id"])

    # stock imported
    assert query_one("SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku=%s",
                     (tid, "SKU-Z"))["current_stock"] == 12.0
    # a dataset + session were created and a job enqueued
    assert query_one("SELECT COUNT(*) c FROM datasets WHERE tenant_id=%s", (tid,))["c"] >= 1
    assert query_one("SELECT COUNT(*) c FROM jobs WHERE tenant_id=%s", (tid,))["c"] >= 1
    # last_sync_at set, no error
    row = query_one("SELECT last_sync_at, last_error, status FROM integration_connections WHERE id=%s", (conn["id"],))
    assert row["last_sync_at"] is not None and row["last_error"] is None and row["status"] == "connected"
```

- [ ] **Step 2: Run it, expect FAIL.**

- [ ] **Step 3: Implement `sync_connection(connection_id)`** — mirror `backend/api/v1/demo.py:demo_quickstart` steps but from provider data:
1. `conn = store.get_connection(tenant_id-agnostic by id)`; `creds = store.get_credentials(id)`; `provider = registry.get_provider(conn["provider"], creds)`.
2. **Stock:** merge `fetch_products()` + `fetch_stock()` by sku → for each, `inv_svc.upsert_stock(tenant_id, sku, {"display_name": name, "unit_cost": cost, "current_stock": qty, "warehouse": wh})`. (Inherits max_skus enforcement + numeric sanitation.) Wrap in `enforce_limit` pre-check for `max_skus` like demo does, before the loop, to avoid partial writes.
3. **Sales dataset:** turn `fetch_sales(since=last_sync_at)` into a canonical CSV (`sku,date,demand` — check the canonical column names the wizard expects; reuse the same header the demo CSV uses). Write it to `paths.dataset_dir(tenant_id, dataset_id)/data.csv`, INSERT a `datasets` row (as demo does).
4. **Session + configs + train:** `session_svc.create_session`, `attach_dataset`, seed default configs (reuse `demo._DEMO_CONFIGS` or an equivalent default set — factor a shared `default_quickstart_configs()` if cleaner, but do NOT change demo behavior), `force_status("MODELS_CONFIGURED")`, then `job_service.create_job` + `set_last_job` + `transition(...,"QUEUED","training")`.
5. `store.mark_synced(id)`; on any exception, `store.mark_synced(id, error=str(e))` and re-raise for the manual-sync endpoint (but the daily loop swallows+logs per connection).
- Wrap the DB-write portion (stock upserts + dataset/session/job inserts) in `with transaction() as conn_db:` where the reused helpers accept a `conn` (they do after the earlier F1 work: `upsert_stock`, `execute`, `query_one` take `conn=`); if a reused helper does NOT accept `conn`, keep it outside the transaction and document the boundary — do NOT change those signatures here.

> **Ambiguity resolved:** the auto-train reuses the demo pipeline exactly (`demo.py` lines 113-150). If `_DEMO_CONFIGS` is demo-private, lift the default-config dict into a small shared helper (e.g. `backend/sessions/defaults.py`) imported by BOTH demo and sync — a refactor limited to extracting a constant, with demo tests still green.

- [ ] **Step 4: Run it, expect PASS.** Also add `test_sync_records_error_on_provider_failure` (provider raises → `status='error'`, `last_error` set, no dataset/job created).

- [ ] **Step 5: Commit** `feat(integrations): sync service (import stock + sales + auto-train)`

---

## Task 9: API endpoints (gated + permission pairs)

**Files:**
- Create: `backend/api/v1/integrations.py`
- Modify: `backend/main.py` (register router)
- Test: `backend/tests/test_integrations_api.py`

**Interfaces:**
- `GET /api/v1/integrations` (read) → `{connections: [...], providers: SUPPORTED_PROVIDERS}` (no credentials). Gated `require_feature(INTEGRATIONS)`.
- `POST /api/v1/integrations/{provider}/connect` (admin) → validates via `provider.test_connection()`, `store.create_connection`. 400 on bad creds; 404 unknown provider.
- `POST /api/v1/integrations/{id}/sync` (analyst_or_above) → `sync_service.sync_connection`.
- `DELETE /api/v1/integrations/{id}` (admin).

- [ ] **Step 1: Write the failing tests** (use `make_tenant_user_headers(plan="enterprise", role=...)`; monkeypatch `settings.testing_mode=False` for the gating test; monkeypatch `registry.get_provider` to a fake that passes `test_connection`):
  - non-enterprise plan → 403 `PLAN_UPGRADE_REQUIRED` on connect.
  - viewer → 403 on connect/sync/delete + no `integration_connections` row created (DB assert).
  - admin (enterprise) connect → 200 + row exists + response has NO `credentials`.
  - sync → 200 and a job row appears (fake provider).
  - delete → row gone.

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement** the router (thin; delegates to `store`/`registry`/`sync_service`). Register in `main.py` with the shared `_PREFIX`. Use `require_admin` / `require_analyst_or_above` / `get_current_user` + `Depends(require_feature(Feature.INTEGRATIONS))` per route.

- [ ] **Step 4: Run, expect PASS + full targeted regression** (`test_integrations_*`).

- [ ] **Step 5: Commit** `feat(integrations): connect/list/sync/delete API (enterprise-gated)`

---

## Task 10: Daily scheduler loop

**Files:**
- Modify: `backend/workers/worker.py`
- Test: `backend/tests/test_integrations_sync.py` (append)

**Interfaces:**
- Produces: `sync_service.run_daily_integration_syncs()` iterating active connections, calling `sync_connection`, swallowing+logging per-connection errors; wired into a daily loop like the existing `_inventory_alert_loop`.

- [ ] **Step 1: Write the failing test** — two tenants each with a connection (fake provider); call `run_daily_integration_syncs()`; assert both `last_sync_at` set. One provider raises → its `status='error'` but the OTHER still synced (loop didn't crash).

- [ ] **Steps 2–4:** implement `run_daily_integration_syncs()` in `sync_service.py` + add a daily thread in `worker.py` mirroring `_inventory_alert_loop` (guard with `integrations_enabled()`), run, PASS.

- [ ] **Step 5: Commit** `feat(integrations): daily scheduled sync loop`

---

## Task 11: Tenant export/delete completeness

**Files:**
- Modify: `backend/tenants/data_export.py`
- Test: `backend/tests/test_tenant_data.py` (append)

- [ ] **Step 1: Write the failing test** — create an `integration_connections` row for a tenant, export the tenant, assert: the export manifest lists `integration_connections`, and the exported JSON for it does NOT contain the `credentials` value (excluded). Then delete the tenant and assert the connection row is gone.

- [ ] **Step 2–4:** add `integration_connections` to `_EXPORT_SPECS` with the `credentials` column EXCLUDED (mirror how `users` excludes `hashed_password`), and to `_DELETE_ORDER` (before `tenants`). Run, PASS.

- [ ] **Step 5: Commit** `feat(integrations): include connections in tenant export/delete (creds excluded)`

---

## Task 12: Frontend — API client + connections page + nav gating

**Files:**
- Modify: `Frontend/src/lib/api.ts`, `Frontend/src/components/layout/Sidebar.tsx`, `Frontend/src/i18n/translations.ts`
- Create: `Frontend/src/app/integraciones/page.tsx`
- Test: `cd Frontend && npx tsc --noEmit`

**Interfaces:**
- `api.ts`: `listIntegrations()`, `connectIntegration(provider, creds)`, `syncIntegration(id)`, `deleteIntegration(id)` + `Integration`/`ProviderInfo` types (mirror a neighboring `get*`/`request` call).

- [ ] **Step 1: api.ts** — add the four functions + types (use the existing `request` helper).
- [ ] **Step 2: page** — `Frontend/src/app/integraciones/page.tsx` (`"use client"`): list supported providers + connection state; a connect form rendered from each provider's `fields` (write-only inputs); per-connection status badge + last sync + last error + "Sync now" + disconnect (shared confirm dialog). Match the app's inline-style + CSS-var idiom; copy via `t()` (add es+en keys). Gate the whole page with `useEntitlements().has('integrations')` → show the upsell/lock for non-Enterprise (reuse the pattern from the nav lock).
- [ ] **Step 3: nav** — add a Sidebar entry `{ href: '/integraciones', labelKey: 'nav.integrations', Icon: <plug icon>, group: 'system', feature: 'integrations' }` (the `feature` field already drives the lock from the entitlements work).
- [ ] **Step 4: i18n** — add `nav.integrations` + `integrations.*` keys in BOTH `es` and `en`.
- [ ] **Step 5: verify** `cd Frontend && npx tsc --noEmit` → exit 0.
- [ ] **Step 6: Commit** `feat(integrations): connections UI + nav (enterprise-gated)`

---

## Self-Review checklist (controller runs before execution)

- Every spec section maps to a task: providers (T4–6), sync+auto-train (T8), credentials/encryption (T2,T7), table (T3), entitlement gating (T1,T9,T12), API (T9), scheduler (T10), export/delete (T11), frontend (T12). ✅
- No real network in tests: every provider/sync/api test patches the `http` seam or `registry.get_provider`. ✅
- Reuses (not reinvents) `upsert_stock`, the demo dataset/session/train pipeline, `transaction()`, entitlement guards. ✅
- Credentials never returned / encrypted at rest / excluded from export — T2, T7, T9, T11. ✅

## Final verification

- [ ] `cd backend && "$PY" -m pytest tests/test_integrations_crypto.py tests/test_integrations_providers.py tests/test_integrations_sync.py tests/test_integrations_api.py tests/test_entitlements.py tests/test_tenant_data.py -q` → all pass (each on an isolated DB per the parallel-exec convention).
- [ ] `cd Frontend && npx tsc --noEmit` → clean.
- [ ] `python -c "import backend.main"` → clean (new router + no circular imports).
- [ ] Manual: set `INTEGRATIONS_SECRET_KEY`, connect a (fake/staging) Alegra account, Sync now, confirm stock imported + a training job ran + semáforo populated.
