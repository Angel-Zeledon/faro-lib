'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { getSessions, getPlanning } from '@/lib/api'
import type { SessionInfo } from '@/lib/types'
import { usePlanning } from '@/contexts/PlanningContext'

export interface AutoSessionResult {
  sessionId:         string
  setSessionId:      (id: string) => void
  currentSession:    SessionInfo | undefined
  completedSessions: SessionInfo[]
  loading:           boolean
  /** The RAW failure when the session list could not be loaded — distinct from
   *  "no sessions exist yet".
   *
   *  This used to be a pre-rendered string built as
   *  `e instanceof Error ? e.message : '<Spanish fallback>'`. Every failure
   *  from `getSessions()` is an `ApiError`, which IS an `Error`, so the left
   *  branch always won and the fallback was dead code (a hardcoded Spanish
   *  literal in logic, which the repo forbids outright). `ApiError.message` is
   *  `detail || \`HTTP ${status}\``, and a network failure is constructed as
   *  status 0 — so the buyer's main screen rendered a large centred
   *  **"HTTP 0"** when the wifi dropped, and the backend's raw English
   *  sentence on a 500. Handing back the error itself lets each screen render
   *  it through `useErrorDetail` / `ErrorState`, which already translate
   *  `error_code` + `params`. */
  error:             unknown
  refresh:           () => void
}

export function useAutoSession(): AutoSessionResult {
  const [sessions,   setSessions]   = useState<SessionInfo[]>([])
  const [sessionId,  setSessionId]  = useState('')
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState<unknown>(null)

  // `load` is created once (stable identity, called only from the mount
  // effect and manual refresh) — it must read the *current* sessionId, not
  // the '' it closed over at creation time. Without this ref, every refresh()
  // call (e.g. the "Reintentar" button after a transient fetch error) would
  // silently overwrite a session the user had since picked manually, because
  // the closure's `!sessionId` check would always see the original ''.
  const sessionIdRef = useRef(sessionId)
  useEffect(() => { sessionIdRef.current = sessionId }, [sessionId])

  // Follow the active-period session (multi-period). When the admin changes the
  // period/horizon, the shared planning context re-resolves active_session_id;
  // switch to it UNLESS the user has manually picked a specific session since
  // the last auto-applied one (then respect their choice, but track the new
  // baseline so a later period change is still detected). Fixes QA Bug 1:
  // the semáforo used to keep the old period until a manual reload.
  const planningCtx = usePlanning()
  const activeFromPlanning = planningCtx?.planning?.active_session_id ?? ''
  const lastAppliedActiveRef = useRef('')
  useEffect(() => {
    if (!activeFromPlanning) return
    if (!sessionIdRef.current || sessionIdRef.current === lastAppliedActiveRef.current) {
      lastAppliedActiveRef.current = activeFromPlanning
      setSessionId(activeFromPlanning)
    } else {
      lastAppliedActiveRef.current = activeFromPlanning
    }
  }, [activeFromPlanning])

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    getSessions()
      .then(async list => {
        setSessions(list)
        if (sessionIdRef.current) return
        // Prefer the resolver's active-period session (multi-period Phase B);
        // fall back to latest-completed — identical for family-less tenants,
        // whose resolver returns exactly that session.
        let preferred = ''
        try { preferred = (await getPlanning()).active_session_id ?? '' } catch { /* fall back */ }
        if (!preferred) {
          const completed = list
            .filter(s => s.status === 'COMPLETED')
            .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
          preferred = completed.length ? completed[0].session_id : ''
        }
        if (preferred && !sessionIdRef.current) setSessionId(preferred)
      })
      .catch((e: unknown) => setError(e))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const currentSession    = sessions.find(s => s.session_id === sessionId)
  const completedSessions = sessions
    .filter(s => s.status === 'COMPLETED')
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))

  return { sessionId, setSessionId, currentSession, completedSessions, loading, error, refresh: load }
}
