'use client'
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { getActiveTraining, getJob } from '@/lib/api'
import type { ActiveTrainingFamily } from '@/lib/api'

/**
 * Server-side truth about training, for the whole app shell.
 *
 * Polls `GET /jobs/active` (one indexed query): every 3 s while something is
 * running, every 30 s otherwise, and not at all while the tab is hidden. The
 * top-bar pill and the training screen both read it, so leaving the screen and
 * coming back (or reloading) resumes from what the server says is running —
 * never from component state.
 *
 * When a family disappears from the active list its outcome is fetched once
 * (`getJob`) and exposed as `outcome` for a few seconds, so the pill can say
 * "finished" or "failed" exactly once instead of just vanishing.
 */

export interface TrainingOutcome {
  name:   string
  status: 'COMPLETED' | 'FAILED' | 'CANCELLED'
}

interface TrainingValue {
  families: ActiveTrainingFamily[]
  outcome:  TrainingOutcome | null
  /** Poll right now (the wizard calls it right after launching a run). */
  refresh:  () => void
}

const TrainingContext = createContext<TrainingValue | null>(null)

const POLL_ACTIVE_MS = 3000
const POLL_IDLE_MS = 30000
const OUTCOME_VISIBLE_MS = 10000

export function TrainingProvider({ children }: { children: React.ReactNode }) {
  const [families, setFamilies] = useState<ActiveTrainingFamily[]>([])
  const [outcome, setOutcome] = useState<TrainingOutcome | null>(null)
  const prevRef = useRef<ActiveTrainingFamily[]>([])
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const outcomeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelledRef = useRef(false)
  const pollRef = useRef<() => Promise<void>>(async () => {})

  const announce = useCallback((o: TrainingOutcome) => {
    setOutcome(o)
    if (outcomeTimerRef.current) clearTimeout(outcomeTimerRef.current)
    outcomeTimerRef.current = setTimeout(() => setOutcome(null), OUTCOME_VISIBLE_MS)
  }, [])

  const poll = useCallback(async () => {
    if (cancelledRef.current) return
    // A hidden tab keeps its timer but must not hit the API; it re-polls the
    // moment it becomes visible again (see the visibilitychange listener).
    if (document.visibilityState === 'hidden') {
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => { void pollRef.current() }, POLL_IDLE_MS)
      return
    }
    let next: ActiveTrainingFamily[] | null = null
    try {
      next = (await getActiveTraining()).families ?? []
    } catch {
      next = null // a failed poll says nothing: keep what we had
    }
    if (cancelledRef.current) return
    if (next) {
      const gone = prevRef.current.filter(p => !next!.some(n => n.family_id === p.family_id))
      prevRef.current = next
      setFamilies(next)
      for (const f of gone) {
        getJob(f.base_job_id)
          .then(j => {
            if (j.status === 'COMPLETED' || j.status === 'FAILED' || j.status === 'CANCELLED') {
              announce({ name: f.name, status: j.status })
            }
          })
          .catch(() => { /* no outcome to show; the list is already correct */ })
      }
    }
    const active = (next ?? prevRef.current).length > 0
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => { void pollRef.current() }, active ? POLL_ACTIVE_MS : POLL_IDLE_MS)
  }, [announce])

  pollRef.current = poll

  useEffect(() => {
    cancelledRef.current = false
    void poll()
    const onVisible = () => {
      if (document.visibilityState === 'visible') void pollRef.current()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      cancelledRef.current = true
      document.removeEventListener('visibilitychange', onVisible)
      if (timerRef.current) clearTimeout(timerRef.current)
      if (outcomeTimerRef.current) clearTimeout(outcomeTimerRef.current)
    }
  }, [poll])

  const value = useMemo<TrainingValue>(() => ({
    families,
    outcome,
    refresh: () => { void pollRef.current() },
  }), [families, outcome])

  return <TrainingContext.Provider value={value}>{children}</TrainingContext.Provider>
}

export function useTraining(): TrainingValue | null {
  return useContext(TrainingContext)
}
