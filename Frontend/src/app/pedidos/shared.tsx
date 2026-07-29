'use client'
/**
 * The decisions `/pedidos` makes, shared by the desktop table page and the
 * narrow-screen card list.
 *
 * "Which orders are still waiting for goods" is the only question this screen
 * really answers, and the phone view is the one used standing at the pallet. If
 * each view computed it on its own, the counter in the header and the list of
 * cards below it could disagree about the very same order — so the predicate,
 * the counter and the reception badge live here and both views import them.
 *
 * Extracted the same way `/hoy` extracted `./shared` when its mobile view was
 * added: everything both screens promise, in one place, so they cannot drift.
 */
import type { POLogEntry, SupplierContactHealthRow } from '@/lib/types'

// ── Palette (same CSS vars as the rest of the app) ───────────────────────────
export const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  red: '#ef4444', amber: '#f59e0b', green: '#22c55e', indigo: 'var(--accent)',
}

// ── Is this order still waiting for goods? ───────────────────────────────────
// 'pending' is also the answer for an order with no reception row at all: the
// absence of a reception is not evidence the shipment arrived.
export const OPEN_RECEPTION_STATUSES: readonly string[] = ['pending', 'partial']

export function receptionStatus(entry: POLogEntry): string {
  return entry.reception_status ?? 'pending'
}

export function isAwaitingReception(entry: POLogEntry): boolean {
  return OPEN_RECEPTION_STATUSES.includes(receptionStatus(entry))
}

export function countAwaitingReception(entries: POLogEntry[]): number {
  return entries.filter(isAwaitingReception).length
}

/** On this screen there is no cart, so a supplier with incomplete contact data
 *  is only worth interrupting for when they are on an order that still has to
 *  reach them. */
export function suppliersOnOpenOrders(rows: SupplierContactHealthRow[]): SupplierContactHealthRow[] {
  return rows.filter(r => r.has_open_pos)
}

// ── Reception badge ──────────────────────────────────────────────────────────
// The colours are the semáforo's own `--signal-*` tokens, used exactly as the
// desktop table uses them — an order that has not arrived is the same amber the
// stock semáforo means by "act on this", and calibrating that is not this
// file's business. (`components/po/POHistory.tsx` keeps its own copy for the
// desktop table; it lives outside this route. Both read these same i18n keys.)
export const RECEPTION_BADGE: Record<string, { labelKey: string; color: string; bg: string }> = {
  pending:      { labelKey: 'po.reception_pending',      color: 'var(--signal-order-soon-fg)', bg: 'var(--signal-order-soon-bg)' },
  partial:      { labelKey: 'po.reception_partial',      color: C.indigo,                      bg: 'color-mix(in srgb, var(--accent) 12%, transparent)' },
  received:     { labelKey: 'po.reception_received',     color: 'var(--signal-ok-fg)',         bg: 'var(--signal-ok-bg)' },
  not_received: { labelKey: 'po.reception_not_received', color: 'var(--signal-order-now-fg)',  bg: 'var(--signal-order-now-bg)' },
}

export function receptionBadge(status: string) {
  return RECEPTION_BADGE[status] ?? RECEPTION_BADGE.pending
}

// ── Formatters ───────────────────────────────────────────────────────────────
export function fmtUnits(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

/** Short enough for a phone card: "12 mar, 14:05". The desktop table keeps the
 *  year, which a card next to today's pallet does not need. */
export function fmtShortDateTime(iso: string, lang: string): string {
  return new Date(iso).toLocaleString(lang, {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

// `t` returns the key itself when the catalog has no entry for it; printing
// "pedidos.something" at a warehouse is worse than plain English. Same guard
// `/hoy` uses (app/hoy/shared.tsx).
export function tOr(
  t: (key: string, params?: Record<string, unknown>) => string,
  key: string, fallback: string, params?: Record<string, unknown>,
): string {
  const text = t(key, params)
  return text === key ? fallback : text
}
