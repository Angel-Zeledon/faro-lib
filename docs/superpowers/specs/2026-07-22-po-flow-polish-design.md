# PO Flow Polish — Three Fixes from the 0.3 Walkthrough

**Date:** 2026-07-22
**Source:** Manual browser walkthrough of the daily flow (item 0.3, `docs/plan_general_faro_2026-07-18.md` → "Actualización 2026-07-22"). All three findings were observed live; none is blocking, all three erode trust in the send/receive loop.

## Fix 1 — Replace the self-resetting send confirmation with a dialog

**Problem.** `SendPOButton` (`Frontend/src/components/po/POHistory.tsx`) uses an inline arm-then-confirm pattern with a 4-second auto-reset (`setTimeout(..., 4000)`). If the user hesitates past 4 s, the armed state silently reverts; their next click re-arms instead of sending, with no feedback that anything reset. Observed directly during the walkthrough.

**Design.** Replace the inline confirm with the existing `ConfirmDialog` component (`Frontend/src/components/ui/ConfirmDialog.tsx`):

- Click "Enviar pedido" → dialog opens. No timers anywhere.
- Dialog body states what will happen before it happens: which suppliers will receive the order by email/WhatsApp, and which will be **skipped** for missing contact info. The data already exists client-side — `/inventory/suppliers/contact-health` is fetched by the `/pedidos` page for the warning banner; the dialog reuses it (no new endpoint).
- Confirm → same `sendPOToSuppliers(poLogId)` call and result handling as today ("Pedido enviado" / "Enviado parcialmente…" / "No se pudo enviar…").
- The `'confirm'` state and its `useEffect` timer are deleted; `'idle' | 'sending' | 'done'` remain.

**Rejected alternative.** Raising the timeout to 8 s: mitigates but keeps the silent-reset class of bug.

**New i18n keys** (es/en): dialog title, body intro, "se enviará a" list label, "se omitirá (sin contacto)" list label, confirm/cancel labels — added to `translations.ts`.

## Fix 2 — Human-readable per-tenant PO number

**Problem.** The email a supplier receives has subject `Orden de compra — a432074b-d187-46ba-…` and the same UUID as the body's "Referencia". A buyer cannot reference that number on a phone call, and it reads as machine plumbing leaking into customer-facing copy.

**Design.** Sequential per-tenant order number, formatted `OC-000123`:

- **Schema:** new nullable integer column `po_number` on `inventory_po_log`, added via an idempotent migration in `backend/db/migrations.py`, plus unique index on `(tenant_id, po_number)`.
- **Backfill:** same migration numbers existing rows per tenant in `created_at` order (window function), so history is consistent and the unique index can be created after backfill.
- **Assignment:** `log_po_generation` (`backend/inventory/roi_service.py`) computes `COALESCE(MAX(po_number), 0) + 1` for the tenant inside the same transaction as the INSERT. Concurrency: a rare duplicate is prevented by the unique index; on conflict retry once. (Volume is human-driven — a buyer exporting an order — so contention is negligible; a counters table is overkill.)
- **Display format:** `OC-{po_number:06d}` — formatting lives in one helper each side (backend `format_po_number()`, frontend `formatPoNumber()`), not inlined at call sites.
- **Surfaces updated:**
  - Email to supplier (`backend/notifications/email.py`): subject `Orden de compra OC-000123`, body "Referencia: OC-000123".
  - `/pedidos` table: new leading "Orden" column showing `OC-000123`.
  - API: `po-history` responses include `po_number` (already `SELECT *`; the Pydantic/TS types gain the field).
- **Out of scope:** renaming the PDF filename, the old `purchase_orders` table (different feature), and any WhatsApp copy.

**Rejected alternative.** Derived reference (`OC-YYYYMMDD-` + UUID prefix): no migration, but not sequential — buyers expect order numbering, and support conversations benefit from "what's your last order number?".

## Fix 3 — Cart margin copy no longer reads as a broken total

**Problem.** In `/hoy`'s approved cart, "N SKUs quedaron fuera del cálculo (sin precio de venta o sin costo)" renders directly under the money total, implying the *total* is incomplete. It actually refers to the 2.10 margin computation — which isn't even rendered when no approved SKU has a sale price (exactly the demo case).

**Design.** Copy + render-condition change only, in `Frontend/src/app/hoy/page.tsx` and `translations.ts`:

- When margin IS shown (some SKUs priced): "El margen protegido no incluye N SKU(s) sin precio de venta registrado." / EN: "Protected margin excludes N SKU(s) with no sale price on file."
- When margin is NOT shown (zero priced SKUs): "Para ver el margen que protege este pedido, registra el precio de venta de tus SKUs." / EN: "To see the margin this order protects, add sale prices to your SKUs." — an invitation, not a warning.
- The total's rendering logic does not change (it uses `unit_cost` and is correct).
- Old keys `hoy.cart_margin_excluded_singular/_plural` are replaced (not kept alongside).

## Testing

- **Fix 2 (backend, pytest):** numbers are sequential per tenant starting at 1; two tenants' sequences are independent (cross-tenant isolation); backfill assigns numbers by `created_at` order to pre-existing rows; `po-history` response carries `po_number`. Assertions query the DB directly per the testing mandate.
- **Fixes 1 & 3 (frontend):** `npx tsc --noEmit` clean, then browser verification of the send flow with the existing demo tenant (`verificacion-0-3@example.com`): dialog lists sent/skipped suppliers before sending; cart shows the new copy in both the some-priced and none-priced cases.

## Order of work

Fix 2 first (backend, has tests), then Fix 1, then Fix 3 (both frontend). One branch: `polish/po-flow-0-3-findings`.
