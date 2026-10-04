'use client'
import { createContext, useContext, useEffect, useRef, useState } from 'react'

/**
 * Lets a screen drive the compact mobile header (title + back button).
 *
 * By default the header needs nothing from the screen: the title comes from
 * the route (TopBar's title map, then the nav label), and a back button appears
 * on nested routes (`/proveedores/scorecard` → `/proveedores`). A screen with
 * an in-page detail view — a card tapped open without changing the URL —
 * calls this hook while that view is showing:
 *
 * ```tsx
 * const [detail, setDetail] = useState<Item | null>(null)
 * useMobileHeader(detail
 *   ? { title: detail.display_name, onBack: () => setDetail(null) }
 *   : null)
 * ```
 *
 * Pass `null` to give the header back to the route. The override is cleared
 * automatically when the component unmounts or the route changes. `backHref`
 * navigates; `onBack` runs a callback; with both, `onBack` wins.
 */
export interface MobileHeaderOverride {
  title?: string
  onBack?: () => void
  backHref?: string
}

interface Ctx {
  override: MobileHeaderOverride | null
  setOverride: (o: MobileHeaderOverride | null) => void
}

const HeaderCtx = createContext<Ctx>({ override: null, setOverride: () => {} })

export function MobileHeaderProvider({ children }: { children: React.ReactNode }) {
  const [override, setOverride] = useState<MobileHeaderOverride | null>(null)
  return <HeaderCtx.Provider value={{ override, setOverride }}>{children}</HeaderCtx.Provider>
}

/** Read by the header. */
export function useMobileHeaderOverride(): MobileHeaderOverride | null {
  return useContext(HeaderCtx).override
}

/** Called by a screen; see the header comment. */
export function useMobileHeader(o: MobileHeaderOverride | null): void {
  const { setOverride } = useContext(HeaderCtx)
  // Callbacks change identity every render; keep the latest in a ref so the
  // effect only re-runs when what the header SHOWS changes.
  const backRef = useRef(o?.onBack)
  backRef.current = o?.onBack
  const active = o !== null
  const title = o?.title
  const backHref = o?.backHref
  const hasOnBack = !!o?.onBack

  useEffect(() => {
    if (!active) { setOverride(null); return }
    setOverride({
      title,
      backHref,
      onBack: hasOnBack ? () => backRef.current?.() : undefined,
    })
  }, [active, title, backHref, hasOnBack, setOverride])

  useEffect(() => () => setOverride(null), [setOverride])
}
