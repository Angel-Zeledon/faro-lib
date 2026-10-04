'use client'
import { useEffect, useRef, useState } from 'react'

/**
 * Eases a displayed percentage toward the latest REAL value.
 *
 * Polling delivers the truth every few seconds; drawing it as-is makes the bar
 * jump. This only interpolates *toward* the last real value — it never runs
 * ahead of it and never moves backwards, so nothing on screen is invented.
 * Pass `null` for "no progress to show".
 */
export function useSmoothedPercent(target: number | null): number | null {
  const [shown, setShown] = useState<number | null>(target)
  const shownRef = useRef<number | null>(target)
  const targetRef = useRef<number | null>(target)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    targetRef.current = target
    if (target == null) {
      shownRef.current = null
      setShown(null)
      return
    }
    if (shownRef.current == null) {
      shownRef.current = target
      setShown(target)
      return
    }
    if (rafRef.current != null) return

    const tick = () => {
      const goal = targetRef.current
      const cur = shownRef.current
      if (goal == null || cur == null) { rafRef.current = null; return }
      // Ease: close ~8% of the gap per frame, never overshoot, never go back.
      const gap = goal - cur
      const next = gap <= 0.05 ? Math.max(cur, goal) : cur + Math.max(gap * 0.08, 0.04)
      shownRef.current = Math.min(next, goal)
      setShown(shownRef.current)
      if (shownRef.current < goal) {
        rafRef.current = requestAnimationFrame(tick)
      } else {
        rafRef.current = null
      }
    }
    rafRef.current = requestAnimationFrame(tick)
  }, [target])

  useEffect(() => () => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
    rafRef.current = null
  }, [])

  return shown == null ? null : Math.round(shown)
}
