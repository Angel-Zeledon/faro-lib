# Accounting Integrations — Import Connector (Alegra + Siigo) — Design

**Date:** 2026-07-21
**Status:** Approved (design), pending implementation plan
**Author:** brainstorming session

## Goal

Let a tenant connect their accounting/invoicing software (**Alegra** and
**Siigo**) and have Faro **import** the data it needs — product catalog, sales
history, and current stock — so the semáforo works **without uploading a CSV**.
After a sync, Faro **auto-trains** so the user sees an updated semáforo with no
extra step. This is the recurring-CSV killer (plan item 5.1) and a moat.

**Direction:** import only (accounting → Faro). Pushing POs back to the
accounting system is out of scope for this work.

## Scope decisions (confirmed)

- **Import:** products (SKUs + cost) + sales invoices (history) + inventory
  (current stock) — the full set.
- **Providers in this work:** both Alegra and Siigo, behind one generic
  provider abstraction.
- **After sync:** auto-train (enqueue a training job) so the semáforo refreshes
  automatically.
- **Gating:** Enterprise-only — a new `Feature.INTEGRATIONS` in the entitlements
  catalog, granted only to the `enterprise` plan.

## External APIs (grounded)

**Alegra** — base `https://api.alegra.com/api/v1`, **HTTP Basic auth**
(`email` + API `token`). Endpoints: `GET /items` (products: code/name, `price`,
`cost`, `inventory.availableQuantity`), `GET /invoices` (sales: date, line items
with item id + quantity + price). Paginated (`start`/`limit`).
Ref: <https://developer.alegra.com/>

**Siigo** — base `https://api.siigo.com/v1`, **OAuth2** (header `Partner-Id` +
POST `/auth` with `username` + `access_key` → Bearer `access_token`, ~24h TTL).
Endpoints: `GET /products` (code/name, prices, `available_quantity`),
`GET /invoices` (sales). **Rate-limited** (invoice listing more restrictive) —
the provider must paginate and back off. Ref: <https://developers.siigo.com/>

Both expose the same three concepts (products, sales, stock) with different auth
and response shapes — a shared interface fits; each provider maps its own shape.

## Architecture

New package `backend/integrations/`:

- `base.py` — the provider contract and canonical DTOs:
  ```python
  @dataclass
  class ProviderProduct:  sku: str; name: str; unit_cost: float | None
  @dataclass
  class ProviderStock:    sku: str; quantity: float; warehouse: str  # 'principal' if provider has none
  @dataclass
  class ProviderSaleLine: date: date; sku: str; quantity: float; unit_price: float | None

  class AccountingProvider(ABC):
      def __init__(self, credentials: dict): ...
      def test_connection(self) -> None:            # raises IntegrationAuthError on bad creds
      def fetch_products(self) -> list[ProviderProduct]:
      def fetch_stock(self) -> list[ProviderStock]:
      def fetch_sales(self, since: date | None) -> list[ProviderSaleLine]:
  ```
  Each method handles its provider's pagination + rate-limit backoff internally.
  Errors normalized to `IntegrationAuthError` / `IntegrationSyncError`.
- `alegra.py` — `AlegraProvider(AccountingProvider)`: Basic auth header from
  `{email, token}`; maps `/items` → products+stock, `/invoices` → sales.
- `siigo.py` — `SiigoProvider(AccountingProvider)`: fetches+caches an OAuth token
  from `{partner_id, username, access_key}`; maps `/products` and `/invoices`.
- `registry.py` — `get_provider(name, credentials) -> AccountingProvider`
  (`{"alegra": AlegraProvider, "siigo": SiigoProvider}`), and the list of
  supported providers + their required credential fields (for the UI).
- `sync_service.py` — the orchestration (see Sync flow).

**HTTP client:** a thin wrapper (requests/httpx already a dep — check) so tests
can patch one seam. Every outbound call has a timeout.

## Data model

New table (idempotent migration, appended to `_MIGRATIONS`):

```sql
CREATE TABLE IF NOT EXISTS integration_connections (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL,                 -- 'alegra' | 'siigo'
    credentials     TEXT NOT NULL,                 -- Fernet-encrypted JSON
    status          TEXT NOT NULL DEFAULT 'connected',  -- connected | error
    last_sync_at    TIMESTAMPTZ,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, provider)
);
```

- **Credentials are encrypted at rest** with Fernet
  (`cryptography.fernet`), keyed by a new `settings.integrations_secret_key`
  (env `INTEGRATIONS_SECRET_KEY`). Never returned in any API response.
- Add this table to `backend/tenants/data_export.py`'s `_EXPORT_SPECS`
  (credentials column EXCLUDED from export) and `_DELETE_ORDER`, so the
  tenant export/delete built earlier stays complete.
- `integration_connections` also CASCADE-deletes via the FK — good, but the
  explicit `_DELETE_ORDER` list must still include it (that module deletes
  explicitly).

## Sync flow (`sync_service.sync_connection(connection_id)`)

1. Load connection, decrypt credentials, build the provider via `registry`.
2. `fetch_products()` + `fetch_stock()` → **upsert `inventory_stock`** (sku,
   name, unit_cost, current_stock, warehouse) via the existing
   `inventory.service.upsert_stock` (which already enforces `max_skus` and
   sanitizes bad numerics — so the connector inherits the plan limits and the
   validation added earlier). Merge products+stock by sku.
3. `fetch_sales(since=last_sync_at or None)` → rows (date, sku, quantity) →
   build a canonical sales-history **dataset** and attach it to a session,
   reusing the existing dataset/session pipeline (the same canonical schema CSV
   upload uses). Incremental: only invoices since `last_sync_at`.
4. **Auto-train:** enqueue a training job for that session (the same job the
   training endpoint enqueues), so the semáforo refreshes. The in-process worker
   runs it; the connector does not block on training.
5. Update `last_sync_at`, clear/set `last_error`, set `status`.
- The whole DB write portion runs inside one `transaction()` (the context
  manager added earlier) where it makes sense, so a mid-sync failure doesn't
  leave half-imported stock. (Fetching happens before the transaction; only the
  DB writes are transactional.)
- Errors: caught, recorded in `last_error` + `status='error'`, surfaced to the
  UI; never crash the scheduler loop.

**Scheduler:** a daily loop in `backend/workers/worker.py` iterates active
connections and calls `sync_connection` (like the existing daily alert loop).

## Entitlements

- Add `Feature.INTEGRATIONS = "integrations"` to `backend/entitlements/plans.py`,
  granted ONLY in the `enterprise` plan (`_ENT_EXTRA`).
- All integration API routes are gated with
  `require_feature(Feature.INTEGRATIONS)` (bypassed under `testing_mode`, like
  every other gate). Add `integrations` to the frontend feature-matrix / nav
  gating if it gets a nav entry.

## API (`backend/api/v1/integrations.py`, prefix `/integrations`)

- `GET /integrations` — list the tenant's connections (provider, status,
  last_sync_at, last_error; NEVER credentials) + the catalog of supported
  providers and their required credential fields. Read (`get_current_user`).
- `POST /integrations/{provider}/connect` — body = provider credential fields;
  validates via `provider.test_connection()`, encrypts + upserts the connection.
  **admin only** (`require_admin`). 400 on bad creds.
- `POST /integrations/{id}/sync` — trigger a manual sync (runs `sync_connection`,
  which enqueues training). `require_analyst_or_above`.
- `DELETE /integrations/{id}` — remove a connection. **admin only**.
- All gated by `require_feature(Feature.INTEGRATIONS)`.

## Frontend

A connections screen (new `/integraciones` page, or a section under
Configuración/Datos — pick the one that fits the nav; likely a card on the Data
screen + a dedicated page):
- Lists supported providers (Alegra, Siigo) with connect state.
- Connect form rendered from the provider's required-fields metadata (Alegra:
  email + token; Siigo: partner_id + username + access_key). Credentials are
  write-only (never shown back).
- Per connection: status badge, last sync time, last error, a **Sync now**
  button, and disconnect.
- Gated in the UI by `has('integrations')` (Enterprise) with the standard
  upsell/lock for lower tiers.
- `Frontend/src/lib/api.ts`: `listIntegrations`, `connectIntegration`,
  `syncIntegration`, `deleteIntegration` + types.

## Testing (mandatory — never hit the real APIs)

- **Provider mapping**: feed each provider a canned API JSON payload (via a
  patched HTTP seam) and assert it maps to the right `ProviderProduct` /
  `ProviderStock` / `ProviderSaleLine` (pagination handled, rate-limit path
  covered for Siigo).
- **Sync service**: with a fake provider returning known products/stock/sales,
  assert (direct DB queries) that `inventory_stock` rows are upserted, a dataset
  + session are created, and a training job is enqueued; `last_sync_at` set.
  Incremental: a second sync only pulls sales since the first.
- **Encryption**: credentials stored are ciphertext (not the plaintext token),
  decrypt round-trips, and no endpoint response contains the token.
- **Permission pairs + gating**: viewer denied on connect/sync/delete (403 +
  state unchanged via DB); admin/analyst success. A non-Enterprise tenant →
  403 `PLAN_UPGRADE_REQUIRED` on every route (tests `monkeypatch
  settings.testing_mode = False`).
- **No network in tests**: the HTTP seam is patched in every test; a guard
  ensures a real request would fail loudly, not silently hit the internet.

## Configuration

- `INTEGRATIONS_SECRET_KEY` (Fernet key) — required to enable integrations; if
  unset, the connect endpoint returns a clear "integrations not configured"
  error rather than storing plaintext.
- Provider base URLs default to the production hosts above; overridable via
  settings for tests/staging.
- Add `cryptography` to `requirements.txt` if not already present.

## Out of scope

- Pushing POs / bills back to the accounting system (export direction).
- Providers beyond Alegra and Siigo (the interface is built to add more).
- Real-time webhooks from the providers (this is scheduled + manual pull).
- Reconciling/merging conflicting edits between Faro and the accounting system
  (import overwrites Faro's stock/catalog for synced SKUs; last sync wins).
- Multi-country endpoint routing for Siigo (`.mx` etc.) beyond a configurable
  base URL.
