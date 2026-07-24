'use client'
// Active planning view (multi-period). One shared source of truth so that
// changing the period/horizon in the top-bar control re-resolves the active
// session and every screen re-fetches — without a manual reload (QA Bug 1).
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { getPlanning, setPlanning } from '@/lib/api'
import type { PlanningState, PlanningPeriod } from '@/lib/types'

interface PlanningValue {
  planning: PlanningState | null
  // Persist a new period/horizon and refresh the shared state so consumers
  // (useAutoSession, the inventory/hoy pages) react to the new active session.
  apply: (period: PlanningPeriod, horizon: number) => Promise<void>
  reload: () => void
}

const PlanningContext = createContext<PlanningValue | null>(null)

export function PlanningProvider({ children }: { children: React.ReactNode }) {
  const [planning, setPlanningState] = useState<PlanningState | null>(null)

  // Load the active planning view. A transient failure (e.g. a /planning 401
  // that races the silent token refresh right after the quick-start redirect)
  // must NOT blank the top-bar period control: retry a few times, and never
  // clobber an already-good state to null on error. Only the very first load,
  // which starts from null, stays null if every attempt genuinely fails.
  const load = useCallback((retriesLeft: number) => {
    getPlanning()
      .then(setPlanningState)
      .catch(() => {
        if (retriesLeft > 0) setTimeout(() => load(retriesLeft - 1), 600)
      })
  }, [])

  const reload = useCallback(() => load(1), [load])

  useEffect(() => { load(2) }, [load])

  const apply = useCallback(async (period: PlanningPeriod, horizon: number) => {
    await setPlanning(period, horizon)
    // Re-GET so we pick up the freshly-resolved active_session_id, which the
    // PUT response does not carry — this is what flips every consumer.
    const next = await getPlanning()
    setPlanningState(next)
  }, [])

  return (
    <PlanningContext.Provider value={{ planning, apply, reload }}>
      {children}
    </PlanningContext.Provider>
  )
}

// Returns null outside the provider (e.g. auth screens) so callers degrade to
// their standalone behavior.
export function usePlanning(): PlanningValue | null {
  return useContext(PlanningContext)
}
