'use client'
/**
 * What this tenant already has, as counts — the input of every "appears when it
 * is needed" rule (docs/simplicity-audit.md section 2).
 *
 * Presentation only. A count decides whether an entry is DRAWN in the sidebar,
 * the tab strips and the settings hub; it never locks a screen. Every route
 * stays reachable by URL and from the command palette, and the entry for the
 * screen you are standing on is always drawn.
 *
 * Contract:
 *   · Every count is `number | null`. `null` means "not known (yet)" — the call
 *     is in flight or failed — and every rule treats it as "show". Hiding a
 *     working feature on no evidence is the worse failure (same stance as
 *     lib/capabilities.tsx). Use `has(count, min)` to apply a rule.
 *   · No endpoint of its own: it reads lists the app already serves
 *     (suppliers, session summaries, messaging contacts, API keys, webhooks,
 *     schedules, tenant services).
 *   · The core group (suppliers, completed sessions, teammates) loads at app
 *     start and again, at most every 20 s, when the route changes. The
 *     "connect" group (keys, webhooks, schedules, operator flag) is loaded only
 *     once a screen asks for it with `useTenantFacts('connect')`.
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { usePathname } from 'next/navigation'
import {
  listSuppliers, getSessionSummaries, getDmContacts,
  listApiKeys, listWebhooks, listSchedules, getTenantServices,
} from '@/lib/api'
import { getUser } from '@/lib/auth'

export interface TenantFacts {
  /** Suppliers on file. */
  suppliers: number | null
  /** Sessions that finished training (a forecast exists). */
  completedSessions: number | null
  /** Other people in the tenant you can write to (users minus yourself). */
  teammates: number | null
  /** connect group: */
  apiKeys: number | null
  webhooks: number | null
  schedules: number | null
  /** connect group, admins only: this installation's operator. */
  isInstanceOperator: boolean | null
}

export const UNKNOWN_FACTS: TenantFacts = {
  suppliers: null, completedSessions: null, teammates: null,
  apiKeys: null, webhooks: null, schedules: null, isInstanceOperator: null,
}

/** A rule's test: true when the count is unknown (fails open) or at least `min`. */
export const has = (count: number | null, min = 1): boolean => count === null || count >= min

const STALE_MS = 20_000

interface Ctx {
  facts: TenantFacts
  wantConnect: () => void
}
const FactsContext = createContext<Ctx>({ facts: UNKNOWN_FACTS, wantConnect: () => {} })

// The last counts seen are remembered per user (a convenience, never the source
// of truth): without them every full page load draws the whole menu for a
// moment and then takes entries away. The first load ever still shows all.
const CACHE_KEY = 'stockai_tenant_facts'

function readCache(): TenantFacts {
  try {
    const raw = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null')
    if (raw && raw.user === getUser()?.id && raw.facts) return { ...UNKNOWN_FACTS, ...raw.facts }
  } catch { /* storage blocked or corrupt: start unknown */ }
  return UNKNOWN_FACTS
}

function writeCache(facts: TenantFacts) {
  try { localStorage.setItem(CACHE_KEY, JSON.stringify({ user: getUser()?.id, facts })) } catch { /* ignore */ }
}

const count = <T,>(p: Promise<T[]>): Promise<number | null> =>
  p.then(r => (Array.isArray(r) ? r.length : null)).catch(() => null)

export function TenantFactsProvider({ children }: { children: React.ReactNode }) {
  const path = usePathname()
  const [facts, setFacts] = useState<TenantFacts>(readCache)
  const coreAt = useRef(0)
  const connectAt = useRef(0)
  const connectWanted = useRef(false)
  const alive = useRef(true)
  const silent = { silent: true } as const

  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const loadCore = useCallback(async () => {
    coreAt.current = Date.now()
    const [suppliers, completed, teammates] = await Promise.all([
      count(listSuppliers(silent)),
      getSessionSummaries(0, 1, { status: 'COMPLETED' }, silent)
        .then(r => (typeof r?.total === 'number' ? r.total : null)).catch(() => null),
      count(getDmContacts(silent)),
    ])
    if (alive.current) setFacts(f => { const n = { ...f, suppliers, completedSessions: completed, teammates }; writeCache(n); return n })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadConnect = useCallback(async () => {
    connectAt.current = Date.now()
    const admin = getUser()?.role === 'admin'
    const [apiKeys, webhooks, schedules, operator] = await Promise.all([
      count(listApiKeys(silent)),
      count(listWebhooks(silent)),
      count(listSchedules(silent)),
      admin
        ? getTenantServices(silent).then(r => (typeof r?.is_instance_operator === 'boolean' ? r.is_instance_operator : null)).catch(() => null)
        : Promise.resolve<boolean | null>(false),
    ])
    if (alive.current) setFacts(f => { const n = { ...f, apiKeys, webhooks, schedules, isInstanceOperator: operator }; writeCache(n); return n })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // On mount and on route change, when the last read is stale.
  useEffect(() => {
    if (Date.now() - coreAt.current > STALE_MS) void loadCore()
    if (connectWanted.current && Date.now() - connectAt.current > STALE_MS) void loadConnect()
  }, [path, loadCore, loadConnect])

  const wantConnect = useCallback(() => {
    connectWanted.current = true
    if (Date.now() - connectAt.current > STALE_MS) void loadConnect()
  }, [loadConnect])

  return <FactsContext.Provider value={{ facts, wantConnect }}>{children}</FactsContext.Provider>
}

/** `useTenantFacts('connect')` also loads keys / webhooks / schedules / operator. */
export function useTenantFacts(group?: 'connect'): TenantFacts {
  const { facts, wantConnect } = useContext(FactsContext)
  useEffect(() => { if (group === 'connect') wantConnect() }, [group, wantConnect])
  return facts
}
