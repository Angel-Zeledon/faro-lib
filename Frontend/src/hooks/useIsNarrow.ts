'use client'
import { useEffect, useState } from 'react'

/**
 * Is the viewport narrow enough that a desktop table is unusable?
 *
 * The app is built with inline `style={{...}}` objects, so Tailwind's
 * breakpoints (and CSS media queries in general) cannot reach it. The only way
 * a component can react to the viewport is to ask JavaScript — hence this hook.
 *
 * Two rules it must never break:
 *
 * 1. **Desktop is the fallback.** The first render — on the server and on the
 *    client's hydration pass — always returns `false`. Reading `window` during
 *    render would either crash Next's SSR or produce a server/client mismatch,
 *    and a hydration error on `/hoy` is a worse bug than a table on a phone.
 *    The real answer arrives one paint later, from `useEffect`.
 *
 * 2. **One source of truth for the breakpoint.** 768px is the line between "a
 *    phone in a warehouse" and "a laptop". Every consumer imports it from here
 *    so the sidebar drawer, the `/hoy` card list and the desktop-only notice
 *    can never disagree about which side of it the user is on.
 */
export const NARROW_BREAKPOINT_PX = 768

export const NARROW_MEDIA_QUERY = `(max-width: ${NARROW_BREAKPOINT_PX}px)`

export function useIsNarrow(): boolean {
  // Desktop until proven otherwise — see rule 1 above.
  const [narrow, setNarrow] = useState(false)

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const mql = window.matchMedia(NARROW_MEDIA_QUERY)

    const apply = () => setNarrow(mql.matches)
    apply()

    // `addEventListener` on a MediaQueryList is the modern API; Safari < 14 and
    // some in-app browsers on older Android only have the deprecated
    // `addListener`. Both are handled because the target device for this whole
    // change is exactly the phone likely to be running one of them.
    if (typeof mql.addEventListener === 'function') {
      mql.addEventListener('change', apply)
      return () => mql.removeEventListener('change', apply)
    }
    mql.addListener(apply)
    return () => mql.removeListener(apply)
  }, [])

  return narrow
}

export default useIsNarrow
