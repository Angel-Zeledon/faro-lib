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

  const reload = useCallback(() => {
    getPlanning().then(setPlanningState).catch(() => setPlanningState(null))
  }, [])

  useEffect(() => { reload() }, [reload])

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
