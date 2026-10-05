import type { TourDefinition, TourStep } from './types'

/** True when a step can be shown: it has no anchor (a screen-level card), or its
 *  anchor is on screen with a real size. An anchor inside a closed disclosure,
 *  behind a feature that is off for this account, or in markup that moved, is
 *  not shown, and neither is its step. */
export function stepShown(step: TourStep): boolean {
  if (!step.anchor) return true
  if (typeof document === 'undefined') return true
  const el = document.querySelector<HTMLElement>(`[data-tour="${CSS.escape(step.anchor)}"]`)
  if (!el) return false
  // Content of a closed <details> keeps a layout box in some browsers; it is not on screen.
  if (el.closest('details:not([open])') && !el.closest('summary')) return false
  const r = el.getBoundingClientRect()
  return r.width > 0 || r.height > 0
}

/** Index of the nearest shown step from `from` going in `dir`, or -1. */
export function nextShown(tour: TourDefinition, from: number, dir: 1 | -1): number {
  for (let j = from; j >= 0 && j < tour.steps.length; j += dir) {
    if (stepShown(tour.steps[j])) return j
  }
  return -1
}

/** Where a tour opens: its first shown step (0 when none is, so it never opens empty). */
export function firstStep(tour: TourDefinition): number {
  const j = nextShown(tour, 0, 1)
  return j < 0 ? 0 : j
}
