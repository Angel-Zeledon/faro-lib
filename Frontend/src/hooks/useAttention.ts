'use client'
/**
 * What is waiting on the buyer, derived from three lists the app already has.
 *
 * Nothing here is new data: it reads the overdue-reception list, the supplier
 * contact-health list and the lead-time alert list, the same ones the Panel used
 * to turn into banners. They are now delivered by quieter channels — the bell,
 * a count a navigation entry can show, a chip inside the row they concern —
 * and all of those read this one hook, so they can never disagree.
 *
 * Because the entries are derived from the live lists they stay until the cause
 * is fixed, are grouped (one entry per kind, never one per order) and cannot
 * duplicate.
 *
 * One shared store with a short TTL: the bell and the Panel mount together and
 * must not each poll three endpoints.
 */
import { useCallback, useEffect, useState } from 'react'
import { getOverduePOs, getPOApprovalPending, getSupplierContactHealth, getSupplierLeadTimeAlerts } from '@/lib/api'
import type {
  OverdueReception, POApprovalPendingItem, SupplierContactHealthRow, SupplierLeadTimeAlert,
} from '@/lib/types'

export interface AttentionState {
  overdue: OverdueReception[]
  /** Suppliers whose missing contact data would make a send skip them. */
  contactHealth: SupplierContactHealthRow[]
  leadTimeAlerts: SupplierLeadTimeAlert[]
  /** Orders waiting for THIS person's decision (empty unless they are an
   *  approver of a tenant that configured an approval rule). */
  approvals: POApprovalPendingItem[]
  loaded: boolean
}

const EMPTY: AttentionState = { overdue: [], contactHealth: [], leadTimeAlerts: [], approvals: [], loaded: false }
const TTL_MS = 30_000
const POLL_MS = 120_000

let state: AttentionState = EMPTY
let fetchedAt = 0
let inflight: Promise<void> | null = null
const listeners = new Set<(s: AttentionState) => void>()

function publish(next: AttentionState) {
  state = next
  listeners.forEach(l => l(state))
}

async function refresh(force = false): Promise<void> {
  if (inflight) return inflight
  if (!force && Date.now() - fetchedAt < TTL_MS) return
  const silent = { silent: true }
  inflight = Promise.allSettled([
    getOverduePOs(silent), getSupplierContactHealth(silent), getSupplierLeadTimeAlerts(silent),
    getPOApprovalPending(silent),
  ]).then(([o, c, l, a]) => {
    // A list that failed to load keeps what it had: a blink of the backend
    // must not read as "nothing is waiting on you".
    publish({
      overdue:        o.status === 'fulfilled' ? o.value : state.overdue,
      contactHealth:  c.status === 'fulfilled' ? c.value : state.contactHealth,
      leadTimeAlerts: l.status === 'fulfilled' ? l.value : state.leadTimeAlerts,
      approvals:      a.status === 'fulfilled' ? a.value.items.filter(i => i.can_decide) : state.approvals,
      loaded: true,
    })
    fetchedAt = Date.now()
  }).finally(() => { inflight = null })
  return inflight
}

export function useAttention(): AttentionState & { reload: () => void } {
  const [s, setS] = useState<AttentionState>(state)
  useEffect(() => {
    listeners.add(setS)
    setS(state)
    void refresh()
    const id = setInterval(() => { void refresh(true) }, POLL_MS)
    return () => { listeners.delete(setS); clearInterval(id) }
  }, [])
  const reload = useCallback(() => { void refresh(true) }, [])
  return { ...s, reload }
}

/** Counts for a navigation entry's dot. `orders` is the Orders entry,
 *  `suppliers` the Suppliers one. Contact gaps only count while an open order
 *  could not be sent because of them. */
export function useAttentionCounts(): { orders: number; suppliers: number; total: number } {
  const { overdue, contactHealth, leadTimeAlerts, approvals } = useAttention()
  // An approval waiting for you is an order waiting on you: same entry.
  const orders = overdue.length + approvals.length
  const suppliers = contactHealth.filter(r => r.has_open_pos).length + leadTimeAlerts.length
  return { orders, suppliers, total: orders + suppliers }
}
