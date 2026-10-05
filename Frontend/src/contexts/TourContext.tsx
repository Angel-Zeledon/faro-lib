'use client'
import { createContext, useContext, useCallback, useEffect, useMemo, useState } from 'react'
import { usePathname } from 'next/navigation'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import type { TourDefinition } from '@/components/tour/types'
import { TOURS, tourForRoute } from '@/components/tour/tours'
import { firstStep, nextShown } from '@/components/tour/anchors'

/**
 * Which tours exist, which one is running, and which the user has already
 * seen.
 *
 * Completion lives in localStorage, not in the database. A tutorial is
 * per-device UX: the cost of being shown it again on a new laptop is one
 * dismissal, and `user_preferences` holds only language and theme — adding a
 * column, an endpoint and a migration to remember a dismissal would be
 * disproportionate to that. If tours ever need to follow a user across
 * devices, this is the one place to change.
 */

const SEEN_KEY = 'fp_tours_seen'

function readSeen(): string[] {
  try {
    const raw = localStorage.getItem(SEEN_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter(x => typeof x === 'string') : []
  } catch {
    // Private modes and corrupted values both land here. A tour that cannot
    // remember it was seen is a nuisance; one that throws on boot is a bug.
    return []
  }
}

interface TourApi {
  /** The tour defined for the current route, if any. */
  available: TourDefinition | null
  /** The tour currently running. */
  active: TourDefinition | null
  /** Index of the step being shown within `active`. */
  stepIndex: number
  start: (id?: string) => void
  next: () => void
  back: () => void
  /** Ends the tour and marks it seen, whether finished or abandoned. Someone
   *  who skipped has told us they do not want it; re-offering would nag. */
  stop: () => void
  hasSeen: (id: string) => boolean
}

const Ctx = createContext<TourApi | null>(null)

export function TourProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const narrow = useIsNarrow()
  const [seen, setSeen] = useState<string[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [stepIndex, setStepIndex] = useState(0)

  useEffect(() => { setSeen(readSeen()) }, [])

  // Longest matching route wins, so /inventory/roi can have its own tour
  // without /inventory's shadowing it.
  const available = useMemo(() => tourForRoute(pathname), [pathname])

  const active = useMemo(
    () => (activeId ? TOURS.find(t => t.id === activeId) ?? null : null),
    [activeId],
  )

  const markSeen = useCallback((id: string) => {
    setSeen(prev => {
      if (prev.includes(id)) return prev
      const next = [...prev, id]
      try { localStorage.setItem(SEEN_KEY, JSON.stringify(next)) } catch { /* see readSeen */ }
      return next
    })
  }, [])

  const start = useCallback((id?: string) => {
    const target = id ?? available?.id
    if (!target) return
    setActiveId(target)
    const def = TOURS.find(t => t.id === target)
    setStepIndex(def ? firstStep(def) : 0)
  }, [available])

  const stop = useCallback(() => {
    if (activeId) markSeen(activeId)
    setActiveId(null)
    setStepIndex(0)
  }, [activeId, markSeen])

  // Steps whose anchor is not on screen are skipped in both directions: a card
  // pointing at nothing is noise, and the counter in the overlay counts only the
  // steps that will actually be shown.
  const next = useCallback(() => {
    if (!active) return
    const j = nextShown(active, stepIndex + 1, 1)
    if (j < 0) { stop(); return }
    setStepIndex(j)
  }, [active, stepIndex, stop])

  const back = useCallback(() => {
    if (!active) return
    const j = nextShown(active, stepIndex - 1, -1)
    if (j >= 0) setStepIndex(j)
  }, [active, stepIndex])

  // Leaving the route abandons the tour: its steps point at elements that are
  // no longer on screen, and following a user across a navigation would be
  // hijacking rather than helping.
  useEffect(() => { setActiveId(null); setStepIndex(0) }, [pathname])

  // First visit to a screen that offers a tour: start it once, and never
  // again. The delay is not cosmetic — these screens fetch before they render,
  // and starting against an empty DOM would anchor every step to nothing.
  //
  // Never auto-starts on a phone. The card covers most of a small screen, the
  // tours cover screens we already tell narrow viewports are meant for a
  // computer, and volunteering a tutorial over that notice contradicts it. The
  // launcher in the top bar still offers it to anyone who wants it.
  useEffect(() => {
    if (narrow) return
    if (!available?.autoStart || seen.includes(available.id)) return
    const id = setTimeout(() => {
      setActiveId(prev => {
        if (prev) return prev
        setStepIndex(firstStep(available))
        return available.id
      })
    }, 700)
    return () => clearTimeout(id)
  }, [available, seen, narrow])

  const value = useMemo<TourApi>(() => ({
    available, active, stepIndex, start, next, back, stop,
    hasSeen: (id: string) => seen.includes(id),
  }), [available, active, stepIndex, start, next, back, stop, seen])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useTour(): TourApi {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useTour must be used inside TourProvider')
  return ctx
}
