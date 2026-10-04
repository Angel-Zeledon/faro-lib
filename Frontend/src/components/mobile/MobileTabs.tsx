'use client'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

/**
 * Horizontally scrollable segmented tabs for a phone.
 *
 * Built for /inventario, whose nine-view strip measured 719px inside a clipping
 * 336px box: five views simply could not be reached. Here the strip scrolls
 * with a finger, snaps to a tab, fades at whichever edge has more tabs behind
 * it (the hint that it scrolls), and keeps the active tab in view — including
 * when the value is changed from outside.
 *
 * ```tsx
 * <MobileTabs
 *   ariaLabel={t('inventory.views_aria')}
 *   value={view}
 *   onChange={setView}
 *   tabs={[
 *     { id: 'simple', label: t('inventory.view_simple'), icon: <Package size={14} /> },
 *     { id: 'capital', label: t('inventory.view_dead_capital'), badge: 3 },
 *   ]}
 * />
 * ```
 *
 * Accessibility: `role="tablist"` / `role="tab"` + `aria-selected`, roving
 * tabindex, ←/→/Home/End move AND select (automatic activation — every view
 * here is cheap to show). Pass `panelId` to wire `aria-controls`.
 * Every tab is at least 44px tall.
 */
export interface MobileTab<Id extends string = string> {
  id: Id
  label: string
  icon?: React.ReactNode
  /** A count or short marker shown after the label; hidden when 0/empty. */
  badge?: number | string
}

export interface MobileTabsProps<Id extends string = string> {
  tabs: MobileTab<Id>[]
  value: Id
  onChange: (id: Id) => void
  ariaLabel: string
  /** id of the element that shows the selected view (for aria-controls). */
  panelId?: string
  style?: React.CSSProperties
}

const FADE = 28

export default function MobileTabs<Id extends string = string>({
  tabs, value, onChange, ariaLabel, panelId, style,
}: MobileTabsProps<Id>) {
  const scroller = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ start: false, end: false })

  const measure = useCallback(() => {
    const el = scroller.current
    if (!el) return
    const max = el.scrollWidth - el.clientWidth
    setEdges({ start: el.scrollLeft > 2, end: el.scrollLeft < max - 2 })
  }, [])

  // Keep the selected tab fully visible, scrolling only the strip (never the
  // page — scrollIntoView would also scroll the document vertically).
  const reveal = useCallback((smooth: boolean) => {
    const el = scroller.current
    const tab = el?.querySelector<HTMLElement>('[aria-selected="true"]')
    if (!el || !tab) return
    const left = tab.offsetLeft - FADE
    const right = tab.offsetLeft + tab.offsetWidth + FADE - el.clientWidth
    let target: number | null = null
    if (el.scrollLeft > left) target = Math.max(0, left)
    else if (el.scrollLeft < right) target = right
    if (target === null) return
    const reduce = typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    el.scrollTo({ left: target, behavior: smooth && !reduce ? 'smooth' : 'auto' })
  }, [])

  useLayoutEffect(() => { reveal(false); measure() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { reveal(true) }, [value, reveal])

  useEffect(() => {
    const el = scroller.current
    if (!el) return
    measure()
    el.addEventListener('scroll', measure, { passive: true })
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null
    ro?.observe(el)
    return () => { el.removeEventListener('scroll', measure); ro?.disconnect() }
  }, [measure, tabs.length])

  function onKeyDown(e: React.KeyboardEvent) {
    const i = tabs.findIndex(t => t.id === value)
    let next = -1
    if (e.key === 'ArrowRight') next = (i + 1) % tabs.length
    else if (e.key === 'ArrowLeft') next = (i - 1 + tabs.length) % tabs.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = tabs.length - 1
    if (next < 0) return
    e.preventDefault()
    onChange(tabs[next].id)
    // Focus follows selection once the new tab has rendered as selected.
    requestAnimationFrame(() => {
      scroller.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus()
    })
  }

  const mask = `linear-gradient(to right, ${edges.start ? 'transparent' : '#000'} 0, #000 ${FADE}px, #000 calc(100% - ${FADE}px), ${edges.end ? 'transparent' : '#000'} 100%)`

  return (
    <div
      ref={scroller}
      role="tablist"
      aria-label={ariaLabel}
      onKeyDown={onKeyDown}
      className="mobile-tabs-scroller"
      style={{
        display: 'flex', gap: 6, overflowX: 'auto', overflowY: 'hidden',
        scrollSnapType: 'x proximity', WebkitOverflowScrolling: 'touch',
        padding: '2px 0', maxWidth: '100%', minWidth: 0,
        WebkitMaskImage: mask, maskImage: mask,
        ...style,
      }}
    >
      {tabs.map(tab => {
        const selected = tab.id === value
        const showBadge = tab.badge !== undefined && tab.badge !== 0 && tab.badge !== ''
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={panelId}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            className="tap-feedback"
            style={{
              all: 'unset', boxSizing: 'border-box', cursor: 'pointer', flexShrink: 0,
              scrollSnapAlign: 'start', scrollMarginLeft: FADE,
              display: 'inline-flex', alignItems: 'center', gap: 6,
              minHeight: 44, padding: '0 14px', borderRadius: 999,
              fontSize: 13, fontWeight: selected ? 700 : 500, whiteSpace: 'nowrap',
              background: selected ? 'var(--accent-dim)' : 'var(--surface)',
              color: selected ? 'var(--accent)' : 'var(--muted)',
              border: `1px solid ${selected ? 'color-mix(in srgb, var(--accent) 45%, transparent)' : 'var(--border)'}`,
              transition: 'background var(--dur-2) var(--ease-out), color var(--dur-2) var(--ease-out)',
            }}
          >
            {tab.icon && <span aria-hidden="true" style={{ display: 'inline-flex' }}>{tab.icon}</span>}
            {tab.label}
            {showBadge && (
              <span style={{
                minWidth: 18, height: 18, borderRadius: 9, padding: '0 5px', boxSizing: 'border-box',
                background: selected ? 'var(--accent)' : 'var(--surface-2)',
                color: selected ? '#fff' : 'var(--muted)', fontSize: 10.5, fontWeight: 700,
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              }}>{tab.badge}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}
