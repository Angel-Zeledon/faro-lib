# Multi-Warehouse Complete (feature 5.4) — Design

> Date: 2026-07-22
> Status: approved in brainstorm (network-aware semáforo, send→receive transfers, hybrid demand)
> Related: `docs/superpowers/specs/2026-07-12-multi-warehouse-milp-design.md` (MILP optimizer, already shipped)

## Goal

Make Faro fully multi-warehouse for the daily flow. Today the backend stores stock
per `(tenant, sku, warehouse)` and the MILP optimizer already suggests inter-warehouse
transfers, but the semáforo collapses all warehouses into one row per SKU and the UI
shows nothing per location. A store that is empty while the central warehouse holds
300 units shows 🟢 OK — the exact lie this feature removes.

Every target customer operates multiple warehouses/stores, so this moves from
post-launch (Fase 5) to now.

## Decisions (from brainstorm, 2026-07-22)

1. **Hybrid per-warehouse demand**: real per-`(sku, store)` forecast when the sales
   history has a store column (some customers have it, some don't); manual demand
   shares per warehouse as fallback.
2. **Mixed purchasing flow**: every PO gets a destination warehouse; transfers are a
   first-class flow (some suppliers deliver per store, others only to central).
3. **Network-aware semáforo** (approach A — heuristic, no solver in the daily path):
   per-`(sku, warehouse)` signal; when another warehouse has surplus, the
   recommended action becomes TRANSFER instead of ORDER.
4. **Transfers are send → receive**: in-transit state, partial receptions, mirroring
   the PO reception pattern. Stock never lies.
5. **Entitlements unchanged**: Starter stays `max_locations=1`; multi-warehouse
   remains the Professional upgrade argument. No new feature flag needed — every
   multi-warehouse surface activates only when the tenant has ≥2 warehouses.

## Verified current state (what already exists)

- `inventory_stock` is keyed `(tenant_id, sku, warehouse)`; warehouses auto-create on
  first stock write (`service._ensure_warehouse`); `warehouses` table + CRUD exists.
- `max_locations` enforced at the `upsert_stock` chokepoint.
- ForecastingCore trains per `(sku, store)` when `group_keys=["sku","store"]`;
  `series_key(sku, store)` / `parse_series_key` exist in
  `forecasting_core/data/canonical.py` (separator `│`).
- The canonical upload mapping already has an optional `store` field, and
  `backend/workers/runner.py` already builds `group_keys=[sku_col, store_col]`
  when it is mapped. **The store dimension is lost afterward**:
  `_generate_forecast_series` groups history by the primary group column only and
  the session forecasts dict is keyed by bare SKU.
- `get_inventory_status` aggregates stock across warehouses
  (`_aggregate_stock_rows_by_sku`) — one row per SKU.
- MILP optimizer (`/inventory/optimize`) already returns per-warehouse orders and
  transfers; frontend types for `OptimizationTransfer` exist. No execution flow.
- PO reception already writes stock per warehouse (`test_reception_bodega.py`).

## 1. Per-warehouse demand

### 1a. Real per-store forecast (when the sales CSV has a store column)

- `_generate_forecast_series` becomes store-aware: when the config's `group_keys`
  has two columns, historical series group by both and the resulting forecasts dict
  is keyed by `series_key(sku, store)` (`"SKU│Store"`). With one group key, output
  is keyed by bare SKU exactly as today — zero change for existing tenants.
- `backend/db/session_store` forecasts blob keeps its shape
  `{key: {model: {historical, forecast}}}`; only the key gains the optional store
  part. A new backend helper module (`backend/inventory/series.py`) wraps
  `parse_series_key` so `backend/` never imports pandas-level core internals:
  `split_key(key) -> (sku, store|None)`, `rollup_by_sku(forecasts) -> {sku: ...}`
  (sums forecast values across stores per model/date).
- Consumers that must stay SKU-level (forecast charts, accuracy, RAG, ROI) go
  through `rollup_by_sku` — behavior identical to today for single-store sessions.
- Store names in the sales data are matched to `warehouses.name` case-insensitively
  after trim; unmatched store names auto-create warehouses through the existing
  `_ensure_warehouse` path subject to `max_locations` (same rule as stock writes).

### 1b. Demand-share fallback (no store column)

- New nullable column `warehouses.demand_share` (FLOAT, 0–100). Per-warehouse
  demand = SKU-global demand × normalized share.
- Normalization: shares are normalized over the warehouses that have a value; NULL
  everywhere → 100% to the default warehouse (preserves current mono-warehouse
  behavior exactly).
- UI: editable on the warehouse selector management panel; when a tenant has ≥2
  warehouses and no shares set, a one-line nudge appears ("¿Qué % de la venta sale
  de cada bodega?").
- Precedence per session: if the session's forecasts carry store keys → use them;
  else → shares. Never mixed within one session.

### 1c. Integrations (Alegra/Siigo)

Both APIs expose a warehouse/branch on invoices. The sync service maps it into the
canonical `store` column when present. Scoped as the final slice; the connector
keeps working store-less until then.

## 2. Network-aware semáforo

- `get_inventory_status` keeps its exact current contract (aggregated per SKU).
  New sibling `get_inventory_status_by_warehouse(tenant_id, session_id, ...)`
  returns rows per `(sku, warehouse)`:
  - `daily_demand` for THAT warehouse (per-store forecast or share split).
  - Same signal thresholds (`_calc_signal`), same persisted signal values
    (PEDIR_YA / PEDIR_PRONTO / OK / SOBRESTOCK — unchanged, they are urgency).
  - New orthogonal fields: `recommended_action: "order" | "transfer"`, and for
    transfers `transfer_suggestion: {from_warehouse, qty, donor_coverage_days_after}`.
- **Donor rule** (the network pass, pure Python over the already-built rows):
  for each `(sku, w)` in PEDIR_YA/PEDIR_PRONTO, a donor warehouse `d` qualifies iff
  after donating `qty = recommended_qty` (capped at donor surplus):
  1. donor stock stays ≥ its own reorder point, and
  2. donor post-donation coverage ≥ `TRANSFER_MIN_DONOR_COVERAGE_DAYS` (default 30,
     module constant).
  Best donor = highest post-donation coverage. If any donor qualifies →
  `recommended_action="transfer"`; partial cover allowed (transfer what the donor
  can give; remainder stays as an order suggestion only when the shortfall is
  > 20% of the need, else transfer-only). No donor → `"order"`.
- Explanation strings follow the existing `build_explanation` business-language
  style: "Central tiene 300 uds (45 días de cobertura) — mover 80 evita comprar."
- API: `GET /inventory/status` gains `?by_warehouse=true` (same endpoint, existing
  feature gates); response items carry `warehouse` when in that mode.
- Daily alert loop (email + WhatsApp): when the tenant has ≥2 warehouses, critical
  items mention their warehouse and a transfer line is added
  ("🔁 2 productos se resuelven moviendo stock, sin comprar").

## 3. Transfers (send → receive)

### Data

```
inventory_transfer_log:
  id, tenant_id, from_warehouse TEXT, to_warehouse TEXT,
  status TEXT (in_transit | partial | received | cancelled),
  notes, created_by, created_at, received_at
inventory_transfer_items:
  id, tenant_id, transfer_id, sku, qty_sent FLOAT, qty_received FLOAT DEFAULT 0
```

### Lifecycle (all transitions in one DB transaction, like the atomic `receive_po`)

- **Create+send** (`POST /inventory/transfers`): validates both warehouses exist and
  differ, origin has ≥ qty per SKU; decrements origin stock rows; status
  `in_transit`. In-transit stock belongs to no warehouse (it is subtracted from
  origin and not yet added to destination) — the semáforo sees the truth.
- **Receive** (`POST /inventory/transfers/{id}/receive`): body lists per-SKU
  received qty (≤ qty_sent − qty_received); increments destination stock (upsert
  through the existing chokepoint — `max_locations`/`max_skus` enforced); status
  `partial` until everything is in, then `received`, `received_at` set.
- **Cancel** (`POST /inventory/transfers/{id}/cancel`): only while `in_transit`
  with zero received; returns full qty to origin.
- List (`GET /inventory/transfers?status=`).
- Permissions: mutations `require_analyst_or_above`; reads `get_current_user` —
  same as every inventory endpoint. Cross-tenant isolation on every query.

## 4. PO destination warehouse

- `inventory_po_log.destination_warehouse TEXT` (nullable; NULL = tenant default
  warehouse, which is exactly today's behavior).
- PO generation (cart approve → `log-po`) accepts `destination_warehouse`;
  reception writes stock into it (reception already supports a warehouse — this
  wires the PO's stored destination as the default choice in the reception UI).
- Cart UI shows a destination selector only when the tenant has ≥2 warehouses.

## 5. UI

- **`/inventory`**: warehouse selector in the toolbar (`Todas | <each warehouse>`),
  fed by the existing warehouses list. "Todas" = current aggregated table, with an
  expandable per-warehouse breakdown row per SKU. A specific warehouse = that
  warehouse's semáforo (by_warehouse mode). TRANSFER suggestions render a
  "Crear transferencia" button pre-filled (from, to, sku, qty) that posts the
  transfer directly.
- **`/hoy`**: urgent cards name the warehouse; transfer suggestions render as 🔁
  cards with approve/reject like purchase cards, but approving creates the
  transfer (in_transit) instead of adding to the PO cart.
- **`/pedidos`**: new "Transferencias" tab — list with status chips, "Registrar
  llegada" opens the partial-reception flow (same component pattern as PO
  reception).
- **Ctrl-K search panel**: per-warehouse stock breakdown lines under the SKU.
- **Mono-warehouse tenants see zero change**: every selector/tab/card above renders
  only when `warehouses.length ≥ 2`.

## 6. Testing (repo mandate)

- Direct DB asserts on stock: origin decremented on send, destination incremented
  on receive, cancel restores origin, partials leave the remainder in transit.
- Atomicity: a failing item mid-send leaves stock untouched (transaction rollback).
- Permission pairs (viewer 403 + state unchanged / analyst success) and
  cross-tenant denial for every new endpoint.
- Network pass unit tests: donor qualifies → transfer; donor below reorder point →
  order; donor below 30-day post-coverage → order; partial donor →
  transfer + order remainder rule.
- Demand split: manual shares normalize correctly; NULL shares → default warehouse
  100%; store-keyed forecasts override shares.
- Store-aware forecast plumbing: two-group-key session produces `sku│store` keys;
  `rollup_by_sku` equals the single-store output; single-group sessions byte-identical
  to today (regression).
- Mono-warehouse regression: `get_inventory_status` output unchanged; UI renders no
  multi-warehouse affordances with one warehouse.

## Implementation slices (for the plan)

1. **DB migrations**: transfer tables, `warehouses.demand_share`,
   `inventory_po_log.destination_warehouse`.
2. **Series plumbing**: store-aware `_generate_forecast_series` +
   `backend/inventory/series.py` (split/rollup) + regression tests.
3. **Per-warehouse demand + network semáforo**: `get_inventory_status_by_warehouse`,
   donor pass, `?by_warehouse=true`.
4. **Transfers service + API**: lifecycle, atomicity, permissions.
5. **PO destination**: log-po, reception default, export label.
6. **UI**: /inventory selector + breakdown + transfer buttons; /hoy transfer cards;
   /pedidos transfers tab; Ctrl-K breakdown; demand-share editor.
7. **Alerts + integrations**: daily alert per-warehouse copy; Alegra/Siigo store
   mapping.

## Out of scope

- Per-warehouse ABC/XYZ (stays SKU-global).
- Transfer cost modeling in the heuristic (MILP keeps that).
- Multi-warehouse shrinkage/cycle-count flows (5.5/5.6 follow their own specs).
- Plan/entitlement changes.
