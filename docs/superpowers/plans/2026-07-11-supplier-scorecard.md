# Supplier Scorecard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `/inventory/suppliers/scorecard` page showing per-supplier performance — real lead-time range, on-time rate, fill rate, and value purchased — computed entirely from PO-reception data Faro already records (feature 1.4). No external services.

**Architecture:** One backend aggregation function replaces an already-unused one (`get_supplier_lead_time_stats` has no frontend consumer today), combining two grouped queries (`supplier_lead_time_obs` for lead time, `inventory_po_items`/`inventory_po_log` for fill rate) merged in Python. One renamed read-only endpoint. One new frontend page, linked from the existing suppliers CRUD page.

**Tech Stack:** FastAPI + psycopg2 (raw SQL) backend; Next.js 14 / React / TypeScript frontend. No new DB tables or migrations — all source tables (`suppliers`, `supplier_lead_time_obs`, `inventory_po_log`, `inventory_po_items`) already exist.

## Global Constraints

- No pandas/ML logic in `backend/`; no business logic in `Frontend/` (`CLAUDE.md`).
- Tests assert **state changes via direct DB queries**, never just HTTP status codes or mocks (`TESTING_GUIDELINES.md`).
- The lead-time **range** (min–max observed) is a scorecard-only display value — it does not change `lead_time_dias` on `suppliers`, nor any inventory/reorder calculation. Nothing in `backend/inventory/service.py` (the semáforo engine) is touched by this plan.
- "A tiempo" = `lead_time_real ≤ lead_time_declarado`, no tolerance margin, no use of `lead_time_std`.
- Fill rate and value purchased only count PO items whose order has had **at least one reception event** (`inventory_po_log.reception_status <> 'pending'`) — a legitimately-not-yet-due order must not drag the numbers down.
- **The suppliers CRUD page (`Frontend/src/app/inventory/suppliers/page.tsx`) does not use the `useLanguage()`/`t()` i18n system — its labels are hardcoded Spanish strings.** The new scorecard page follows this same convention (hardcoded Spanish, no `t()` calls, no `translations.ts` changes) to stay consistent with the page it's linked from. This is a deliberate deviation from the ROI page's i18n pattern, made because the immediate sibling page it must match doesn't use i18n either.
- Endpoint permission: read-only (`get_current_user`), matching the existing (unused) `/suppliers/lead-times` endpoint it replaces — no analyst-only restriction needed, nothing is mutated.

---

### Task 1: Backend — `get_supplier_scorecard` aggregation function

**Files:**
- Modify: `backend/inventory/reception_service.py:189-222` (replace `get_supplier_lead_time_stats` entirely with `get_supplier_scorecard`)
- Test: `backend/tests/test_supplier_scorecard.py` (new file)

**Interfaces:**
- Consumes: `query(sql, params) -> list[dict]` from `backend.db.connection` (already imported at `backend/inventory/reception_service.py:18`); reads `supplier_lead_time_obs` (columns: `proveedor`, `lead_time_days`, `observed_at`, `tenant_id`), `suppliers` (`name`, `lead_time_dias`, `tenant_id`), `inventory_po_items` (`proveedor`, `cantidad_final`, `cantidad_recibida`, `costo_unitario`, `status`, `tenant_id`, `po_log_id`), `inventory_po_log` (`id`, `reception_status`).
- Produces: `get_supplier_scorecard(tenant_id: str) -> list[dict]`, each row `{proveedor, n_recepciones, lead_time_real_min, lead_time_real_max, lead_time_real_avg, lead_time_declarado, desviacion_dias, on_time_rate, fill_rate, valor_comprado, ultima_recepcion}`. Consumed by Task 2 (endpoint).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_supplier_scorecard.py`:

```python
"""
Tests for the supplier scorecard (feature 2.5): real lead-time range,
on-time rate, and fill rate computed entirely from PO reception data
already recorded by feature 1.4. No external services involved.
"""

import uuid

from backend.db.connection import execute


def _make_supplier(tenant_id: str, name: str, lead_time_dias: int = 10) -> None:
    execute(
        "INSERT INTO suppliers (tenant_id, name, lead_time_dias) VALUES (%s, %s, %s)",
        (tenant_id, name, lead_time_dias),
    )


def _make_po(client, auth_headers, *, sku: str, qty: float, proveedor: str, costo_unitario: float = 2.0) -> str:
    resp = client.post(
        "/api/v1/inventory/log-po",
        params={"session_id": f"sess_test_{uuid.uuid4().hex[:6]}"},
        json={"items": [{
            "sku": sku, "display_name": f"Prod {sku}", "proveedor": proveedor,
            "signal": "PEDIR_YA", "cantidad_recomendada": qty,
            "cantidad_final": qty, "costo_unitario": costo_unitario, "status": "approved",
        }]},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _set_generated_at(po_log_id: str, iso_dt: str) -> None:
    execute(
        "UPDATE inventory_po_log SET generated_at = %s WHERE id = %s",
        (iso_dt, po_log_id),
    )


class TestGetSupplierScorecard:
    def test_computes_lead_time_range_on_time_rate_and_fill_rate(self, client, auth_headers, test_tenant):
        from backend.inventory.reception_service import get_supplier_scorecard

        tid = test_tenant["id"]
        prov = f"Prov-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, lead_time_dias=10)

        # PO 1: generated 2026-01-01, received 2026-01-09 -> 8 days, on time (<=10).
        sku1 = f"SC1-{uuid.uuid4().hex[:6]}"
        po1 = _make_po(client, auth_headers, sku=sku1, qty=50, proveedor=prov, costo_unitario=2.0)
        _set_generated_at(po1, "2026-01-01T00:00:00Z")
        resp1 = client.post(
            f"/api/v1/inventory/po/{po1}/receive",
            json={"received_at": "2026-01-09T00:00:00Z"},
            headers=auth_headers,
        )
        assert resp1.status_code == 200, resp1.text

        # PO 2: generated 2026-02-01, received 2026-02-15 -> 14 days, late (>10).
        sku2 = f"SC2-{uuid.uuid4().hex[:6]}"
        po2 = _make_po(client, auth_headers, sku=sku2, qty=20, proveedor=prov, costo_unitario=3.0)
        _set_generated_at(po2, "2026-02-01T00:00:00Z")
        resp2 = client.post(
            f"/api/v1/inventory/po/{po2}/receive",
            json={"received_at": "2026-02-15T00:00:00Z"},
            headers=auth_headers,
        )
        assert resp2.status_code == 200, resp2.text

        # PO 3: never received (still pending) -> must be excluded from fill_rate/valor_comprado.
        sku3 = f"SC3-{uuid.uuid4().hex[:6]}"
        _make_po(client, auth_headers, sku=sku3, qty=30, proveedor=prov, costo_unitario=1.0)

        rows = get_supplier_scorecard(tid)
        row = next(r for r in rows if r["proveedor"] == prov)

        assert row["n_recepciones"] == 2
        assert row["lead_time_real_min"] == 8.0
        assert row["lead_time_real_max"] == 14.0
        assert row["lead_time_real_avg"] == 11.0
        assert row["lead_time_declarado"] == 10
        assert row["on_time_rate"] == 0.5          # 1 of 2 receptions on time
        assert row["fill_rate"] == 1.0              # 70/70 received, PO3 excluded (pending)
        assert row["valor_comprado"] == 160.0        # 50*2.0 + 20*3.0, PO3's 30*1.0 excluded

    def test_supplier_without_receptions_not_included(self, client, auth_headers, test_tenant):
        from backend.inventory.reception_service import get_supplier_scorecard

        tid = test_tenant["id"]
        prov = f"NoRecep-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, lead_time_dias=5)

        rows = get_supplier_scorecard(tid)

        assert not any(r["proveedor"] == prov for r in rows)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_supplier_scorecard.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_supplier_scorecard'`

- [ ] **Step 3: Replace `get_supplier_lead_time_stats` with `get_supplier_scorecard`**

In `backend/inventory/reception_service.py`, replace the entire function at lines 189-222 (from `def get_supplier_lead_time_stats(tenant_id: str) -> list[dict]:` through the end of the file) with:

```python
def get_supplier_scorecard(tenant_id: str) -> list[dict]:
    """
    Per-supplier performance: real lead time range (min-max observed, not a
    single misleading average), on-time rate (real <= declared), fill rate
    and value purchased. Anchored to suppliers with at least one recorded
    reception — nothing to score before that.
    """
    lead_rows = query(
        """SELECT o.proveedor,
                  COUNT(*)::int                      AS n_recepciones,
                  MIN(o.lead_time_days)              AS lead_time_real_min,
                  MAX(o.lead_time_days)              AS lead_time_real_max,
                  AVG(o.lead_time_days)              AS lead_time_real_avg,
                  MAX(o.observed_at)                 AS ultima_recepcion,
                  s.lead_time_dias                   AS lead_time_declarado,
                  AVG(CASE WHEN o.lead_time_days <= s.lead_time_dias THEN 1.0 ELSE 0.0 END)
                      FILTER (WHERE s.lead_time_dias IS NOT NULL) AS on_time_rate
           FROM supplier_lead_time_obs o
           LEFT JOIN suppliers s
             ON s.tenant_id = o.tenant_id AND LOWER(s.name) = LOWER(o.proveedor)
           WHERE o.tenant_id = %s
           GROUP BY o.proveedor, s.lead_time_dias
           ORDER BY n_recepciones DESC, o.proveedor""",
        (tenant_id,),
    )

    fill_rows = query(
        """SELECT poi.proveedor,
                  COALESCE(SUM(poi.cantidad_recibida), 0) AS total_recibido,
                  COALESCE(SUM(poi.cantidad_final), 0)    AS total_pedido,
                  COALESCE(SUM(poi.cantidad_final * poi.costo_unitario), 0) AS valor_comprado
           FROM inventory_po_items poi
           JOIN inventory_po_log pol ON pol.id = poi.po_log_id
           WHERE poi.tenant_id = %s
             AND poi.status IN ('approved', 'modified')
             AND poi.proveedor IS NOT NULL AND poi.proveedor <> ''
             AND pol.reception_status <> 'pending'
           GROUP BY poi.proveedor""",
        (tenant_id,),
    )
    fill_by_proveedor = {r["proveedor"]: r for r in fill_rows}

    out = []
    for r in lead_rows:
        d = dict(r)
        if isinstance(d.get("ultima_recepcion"), datetime):
            d["ultima_recepcion"] = d["ultima_recepcion"].isoformat()
        for k in ("lead_time_real_avg", "lead_time_real_min", "lead_time_real_max"):
            if d.get(k) is not None:
                d[k] = round(float(d[k]), 1)
        if d.get("on_time_rate") is not None:
            d["on_time_rate"] = round(float(d["on_time_rate"]), 3)

        declared = d.get("lead_time_declarado")
        avg = d.get("lead_time_real_avg")
        d["desviacion_dias"] = round(avg - declared, 1) if (declared is not None and avg is not None) else None

        fill = fill_by_proveedor.get(d["proveedor"])
        total_pedido = float(fill["total_pedido"]) if fill else 0.0
        d["fill_rate"] = round(float(fill["total_recibido"]) / total_pedido, 3) if fill and total_pedido > 0 else None
        d["valor_comprado"] = round(float(fill["valor_comprado"]), 2) if fill else 0.0

        out.append(d)
    return out
```

`datetime` is already imported at the top of the file (`from datetime import datetime, timezone`) — no new imports needed.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_supplier_scorecard.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/reception_service.py backend/tests/test_supplier_scorecard.py
git commit -m "feat(inventory): supplier scorecard aggregation (lead time range, on-time rate, fill rate)"
```

---

### Task 2: Backend — `GET /inventory/suppliers/scorecard` endpoint

**Files:**
- Modify: `backend/api/v1/inventory.py:553-557` (rename the existing `supplier_lead_times` endpoint)
- Test: `backend/tests/test_supplier_scorecard.py` (append)

**Interfaces:**
- Consumes: `get_supplier_scorecard(tenant_id)` from Task 1; `CurrentUser`, `get_current_user` from `backend.auth.guards` (already imported at `backend/api/v1/inventory.py:19`); `ok()` from `backend.schemas.common` (already imported at line 23).
- Produces: `GET /api/v1/inventory/suppliers/scorecard` → `{"data": [...]}` (list shape from Task 1). Consumed by Task 3 (frontend `getSupplierScorecard`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_supplier_scorecard.py`:

```python


class TestSupplierScorecardEndpoint:
    def test_viewer_can_read(self, client, viewer_headers):
        resp = client.get("/api/v1/inventory/suppliers/scorecard", headers=viewer_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_unauthenticated_rejected(self, client):
        resp = client.get("/api/v1/inventory/suppliers/scorecard")
        assert resp.status_code == 401
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_supplier_scorecard.py::TestSupplierScorecardEndpoint -v`
Expected: FAIL with 404 (route `/suppliers/scorecard` doesn't exist yet — the old route is still `/suppliers/lead-times`)

- [ ] **Step 3: Rename the endpoint**

In `backend/api/v1/inventory.py`, replace lines 553-557:

```python
@router.get("/suppliers/lead-times")
def supplier_lead_times(user: CurrentUser = Depends(get_current_user)):
    """Real lead time per supplier learned from receptions, vs declared."""
    from backend.inventory import reception_service as rec_svc
    return ok(rec_svc.get_supplier_lead_time_stats(user.tenant_id))
```

with:

```python
@router.get("/suppliers/scorecard")
def supplier_scorecard(user: CurrentUser = Depends(get_current_user)):
    """Per-supplier performance: real lead time range, on-time rate, fill rate."""
    from backend.inventory import reception_service as rec_svc
    return ok(rec_svc.get_supplier_scorecard(user.tenant_id))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_supplier_scorecard.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the full backend test suite to check for regressions**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_data_seeding.py`
Expected: all tests pass (no regressions). `test_data_seeding.py` fails to import for an unrelated, pre-existing reason (`ModuleNotFoundError: No module named 'tests'`) — not something this task causes or fixes.

- [ ] **Step 6: Commit**

```bash
git add backend/api/v1/inventory.py backend/tests/test_supplier_scorecard.py
git commit -m "feat(inventory): expose GET /inventory/suppliers/scorecard"
```

---

### Task 3: Frontend — API client + types

**Files:**
- Modify: `Frontend/src/lib/types.ts:815-824` (replace `SupplierLeadTimeStat` with `SupplierScorecardRow`)
- Modify: `Frontend/src/lib/api.ts:648-649` (replace `getSupplierLeadTimes` with `getSupplierScorecard`)

**Interfaces:**
- Consumes: `request<T>(method, path)` helper (existing, used throughout `api.ts`).
- Produces: `SupplierScorecardRow` type and `getSupplierScorecard(): Promise<SupplierScorecardRow[]>`. Consumed by Task 4 (page component).

**Note:** `SupplierLeadTimeStat` and `getSupplierLeadTimes` have no consumers anywhere in the frontend today (confirmed: only defined, never imported by a page/component) — this is a clean replacement, not a breaking change.

- [ ] **Step 1: Replace the type**

In `Frontend/src/lib/types.ts`, replace lines 815-824:

```typescript
export interface SupplierLeadTimeStat {
  proveedor:            string
  n_recepciones:        number
  lead_time_real_avg:   number | null
  lead_time_real_max:   number | null
  lead_time_real_min:   number | null
  ultima_recepcion:     string | null
  lead_time_declarado:  number | null
  desviacion_dias:      number | null
}
```

with:

```typescript
export interface SupplierScorecardRow {
  proveedor:            string
  n_recepciones:        number
  lead_time_real_min:   number | null
  lead_time_real_max:   number | null
  lead_time_real_avg:   number | null
  lead_time_declarado:  number | null
  desviacion_dias:      number | null
  on_time_rate:         number | null
  fill_rate:            number | null
  valor_comprado:       number
  ultima_recepcion:     string | null
}
```

- [ ] **Step 2: Replace the API client function**

In `Frontend/src/lib/api.ts`, replace lines 648-649:

```typescript
export const getSupplierLeadTimes = () =>
  request<import('./types').SupplierLeadTimeStat[]>('GET', '/inventory/suppliers/lead-times')
```

with:

```typescript
export const getSupplierScorecard = () =>
  request<import('./types').SupplierScorecardRow[]>('GET', '/inventory/suppliers/scorecard')
```

- [ ] **Step 3: Typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add Frontend/src/lib/types.ts Frontend/src/lib/api.ts
git commit -m "feat(inventory): frontend client for supplier scorecard endpoint"
```

---

### Task 4: Frontend — scorecard page + link from Suppliers

**Files:**
- Create: `Frontend/src/app/inventory/suppliers/scorecard/page.tsx`
- Modify: `Frontend/src/app/inventory/suppliers/page.tsx` (add imports + a "Scorecard" link in the header)

**Interfaces:**
- Consumes: `getSupplierScorecard` and `SupplierScorecardRow` from Task 3; `Spinner` from `@/components/ui/Spinner` (existing, used the same way in `Frontend/src/app/inventory/roi/page.tsx`).
- Produces: nothing consumed elsewhere — this is the leaf UI change.

**Note on i18n:** per Global Constraints, this page and the suppliers page it's linked from use hardcoded Spanish strings, not `t()`/`useLanguage()`. Do not add `translations.ts` entries for this task.

- [ ] **Step 1: Create the scorecard page**

Create `Frontend/src/app/inventory/suppliers/scorecard/page.tsx`:

```typescript
'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { getSupplierScorecard } from '@/lib/api'
import type { SupplierScorecardRow } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import { BarChart3, ArrowLeft, AlertTriangle, Truck } from 'lucide-react'

// ── Palette ───────────────────────────────────────────────────────────────────
const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  red: '#ef4444', amber: '#f59e0b', green: '#22c55e', indigo: '#818cf8',
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('es', { day: 'numeric', month: 'short', year: 'numeric' })
}

function fmtCurrency(n: number): string {
  return '$' + n.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

function fmtPct(n: number | null): string {
  return n == null ? '—' : `${Math.round(n * 100)}%`
}

function fmtRange(min: number | null, max: number | null): string {
  if (min == null || max == null) return '—'
  return min === max ? `${min}d` : `${min}–${max}d`
}

// ── Table ─────────────────────────────────────────────────────────────────────
function ScorecardTable({ rows }: { rows: SupplierScorecardRow[] }) {
  const columns = [
    'Proveedor', 'Recepciones', 'Lead time real', 'Declarado',
    '% A tiempo', '% Fill rate', 'Valor comprado', 'Última recepción',
  ]

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr style={{ background: C.card }}>
            {columns.map(h => (
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
          {rows.map((row, idx) => {
            const onTimeColor = row.on_time_rate == null
              ? C.dim
              : row.on_time_rate >= 0.7 ? C.green : row.on_time_rate >= 0.4 ? C.amber : C.red
            return (
              <tr key={row.proveedor} style={{
                background: idx % 2 === 0 ? C.surface : C.card,
                borderBottom: `1px solid ${C.border}`,
              }}>
                <td style={{ padding: '11px 14px', color: C.text, fontWeight: 600 }}>{row.proveedor}</td>
                <td style={{ padding: '11px 14px', color: C.text }}>{row.n_recepciones}</td>
                <td style={{ padding: '11px 14px', color: C.text, fontFamily: 'monospace' }}>
                  {fmtRange(row.lead_time_real_min, row.lead_time_real_max)}
                </td>
                <td style={{ padding: '11px 14px', color: C.muted, fontFamily: 'monospace' }}>
                  {row.lead_time_declarado != null ? `${row.lead_time_declarado}d` : '—'}
                </td>
                <td style={{ padding: '11px 14px', color: onTimeColor, fontWeight: 700 }}>
                  {fmtPct(row.on_time_rate)}
                </td>
                <td style={{ padding: '11px 14px', color: C.text }}>{fmtPct(row.fill_rate)}</td>
                <td style={{ padding: '11px 14px', color: C.green, fontFamily: 'monospace', fontWeight: 600 }}>
                  {fmtCurrency(row.valor_comprado)}
                </td>
                <td style={{ padding: '11px 14px', color: C.dim }}>{fmtDate(row.ultima_recepcion)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function SupplierScorecardPage() {
  const [rows,    setRows]    = useState<SupplierScorecardRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try { setRows(await getSupplierScorecard()) }
    catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error cargando el scorecard') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, animation: 'fadeIn 0.3s ease-out' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 9,
            background: 'linear-gradient(135deg, #818cf8, #6366f1)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <BarChart3 size={17} color="#fff" strokeWidth={2.5} />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>
              Scorecard de proveedores
            </h1>
            <p style={{ margin: 0, fontSize: 11, color: C.dim }}>
              Lead time real, cumplimiento y fill rate — calculado de tus recepciones registradas
            </p>
          </div>
        </div>
        <Link href="/inventory/suppliers" style={{
          display: 'flex', alignItems: 'center', gap: 6,
          fontSize: 12, color: C.dim, textDecoration: 'none',
          padding: '7px 12px', border: `1px solid ${C.border}`, borderRadius: 8,
        }}>
          <ArrowLeft size={12} /> Volver a Proveedores
        </Link>
      </div>

      {/* Error */}
      {error && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '10px 14px', borderRadius: 8,
          background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.2)',
          fontSize: 13, color: C.red,
        }}>
          <AlertTriangle size={13} style={{ flexShrink: 0 }} /> {error}
        </div>
      )}

      {loading ? (
        <div style={{ padding: 64, display: 'flex', justifyContent: 'center' }}>
          <Spinner />
        </div>
      ) : rows.length > 0 ? (
        <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>
          <ScorecardTable rows={rows} />
        </div>
      ) : (
        <div style={{
          padding: '40px 24px', textAlign: 'center', borderRadius: 12,
          background: C.card, border: `1px solid ${C.border}`,
        }}>
          <Truck size={32} color={C.dim} style={{ margin: '0 auto 12px', opacity: 0.4 }} />
          <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginBottom: 6 }}>
            Aún no hay recepciones registradas
          </div>
          <div style={{ fontSize: 12, color: C.dim, marginBottom: 16 }}>
            Registra la llegada de una orden de compra desde el historial de Impacto para que Faro empiece a aprender el desempeño de tus proveedores.
          </div>
          <Link href="/inventory/suppliers" style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '8px 16px', borderRadius: 8, fontSize: 12, fontWeight: 600,
            background: 'rgba(129,140,248,0.1)', border: '1px solid rgba(129,140,248,0.3)',
            color: C.indigo, textDecoration: 'none',
          }}>
            <Truck size={13} /> Ir a Proveedores
          </Link>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Add the "Scorecard" link on the Suppliers page**

In `Frontend/src/app/inventory/suppliers/page.tsx`, update the top import block (lines 1-10):

```typescript
'use client'
import { useState, useEffect, useCallback } from 'react'
import {
  listSuppliers, createSupplier, updateSupplier, deleteSupplier,
} from '@/lib/api'
import type { Supplier } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import {
  Truck, Plus, Edit2, Trash2, X, Save, Info, ChevronDown,
} from 'lucide-react'
```

to:

```typescript
'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import {
  listSuppliers, createSupplier, updateSupplier, deleteSupplier,
} from '@/lib/api'
import type { Supplier } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import {
  Truck, Plus, Edit2, Trash2, X, Save, Info, ChevronDown, BarChart3,
} from 'lucide-react'
```

Then replace the header's right-side button block (currently lines 364-375):

```typescript
        {!isFormOpen && (
          <button
            onClick={() => { setEditing(null); setShowForm(true) }}
            style={{
              all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6,
              padding: '8px 16px', borderRadius: 8, fontSize: 13, fontWeight: 600,
              background: C.indigo, color: '#fff',
            }}
          >
            <Plus size={14} /> Agregar proveedor
          </button>
        )}
```

with:

```typescript
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Link href="/inventory/suppliers/scorecard" style={{
            display: 'flex', alignItems: 'center', gap: 6,
            fontSize: 12, color: C.dim, textDecoration: 'none',
            padding: '7px 12px', border: `1px solid ${C.border}`, borderRadius: 8,
          }}>
            <BarChart3 size={13} /> Scorecard
          </Link>
          {!isFormOpen && (
            <button
              onClick={() => { setEditing(null); setShowForm(true) }}
              style={{
                all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6,
                padding: '8px 16px', borderRadius: 8, fontSize: 13, fontWeight: 600,
                background: C.indigo, color: '#fff',
              }}
            >
              <Plus size={14} /> Agregar proveedor
            </button>
          )}
        </div>
```

- [ ] **Step 3: Typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 4: Manual verification**

Start the backend (`backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --port 8010`) and frontend (`cd Frontend && set BACKEND_URL=http://localhost:8010&& npm run dev`), log in, go to `/inventory/suppliers`, click "Scorecard", and confirm the new page renders (either the empty state, or a populated table if there's reception history) with no console errors.

- [ ] **Step 5: Commit**

```bash
git add Frontend/src/app/inventory/suppliers/scorecard/page.tsx Frontend/src/app/inventory/suppliers/page.tsx
git commit -m "feat(inventory): supplier scorecard page — lead time range, on-time rate, fill rate"
```

---

## Self-Review Notes

- **Spec coverage:** backend aggregation (spec §1) → Task 1; endpoint rename (spec §1) → Task 2; frontend types/client (spec §2) → Task 3; page + link (spec §2) → Task 4; testing plan (spec §3) → covered inline in Tasks 1–2.
- **Out of scope confirmed:** no change to `lead_time_dias` in the inventory/reorder engine, no external services, no i18n additions (deliberate deviation, documented in Global Constraints).
- **Type consistency checked:** `SupplierScorecardRow` fields match exactly between Task 1's Python dict, Task 2's endpoint passthrough, and Task 3/4's TypeScript interface and usage. `desviacion_dias` field name preserved from the original function (no consumers break, but kept for continuity/possible future use — it's part of the natural output of the same query, not new surface area).
