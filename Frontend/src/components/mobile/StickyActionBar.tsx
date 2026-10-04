'use client'
import { useEffect, useRef, useState } from 'react'

/**
 * The screen's primary action, pinned to the bottom of a phone screen just
 * above the tab bar — where the thumb already is.
 *
 * ```tsx
 * <StickyActionBar>
 *   <button className="mobile-btn mobile-btn-secondary" onClick={discard}>{t('inventory.btn_discard')}</button>
 *   <button className="mobile-btn mobile-btn-primary" onClick={save} disabled={!dirty}>
 *     {t('inventory.btn_save_prefix')} {n}
 *   </button>
 * </StickyActionBar>
 * ```
 *
 * It renders an in-flow spacer of its own measured height, so the last row of
 * the page is never hidden behind it — no screen has to guess a padding.
 * It sits at `bottom: var(--mobile-nav-h)` (the tab bar's height including the
 * home-indicator inset, set in globals.css), so it never covers the tabs.
 * Children are laid out in a row and share the width (`flex: 1` each).
 * Use the `.mobile-btn` classes from globals.css for 48px buttons.
 */
export default function StickyActionBar({ children, hidden = false, style }: {
  children: React.ReactNode
  /** Slide it away (e.g. nothing to save yet) without unmounting. */
  hidden?: boolean
  style?: React.CSSProperties
}) {
  const barRef = useRef<HTMLDivElement>(null)
  const [h, setH] = useState(72)

  useEffect(() => {
    const el = barRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setH(el.offsetHeight))
    ro.observe(el)
    setH(el.offsetHeight)
    return () => ro.disconnect()
  }, [])

  return (
    <>
      <div aria-hidden="true" style={{ height: hidden ? 0 : h, flexShrink: 0 }} />
      <div
        ref={barRef}
        className="sticky-action-bar"
        aria-hidden={hidden || undefined}
        style={{
          position: 'fixed', left: 0, right: 0, bottom: 'var(--mobile-nav-h, 0px)', zIndex: 45,
          display: 'flex', gap: 10, padding: '10px 12px',
          background: 'var(--surface)', borderTop: '1px solid var(--border)',
          boxShadow: '0 -6px 20px rgba(0,0,0,0.12)',
          transform: hidden ? 'translateY(calc(100% + var(--mobile-nav-h, 0px)))' : 'translateY(0)',
          transition: 'transform var(--dur-3) var(--ease-out)',
          visibility: hidden ? 'hidden' : 'visible',
          ...style,
        }}
      >
        {children}
      </div>
    </>
  )
}
