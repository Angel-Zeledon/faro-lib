'use client'
import { useEffect, useRef, useState } from 'react'

/**
 * How far the on-screen keyboard covers the bottom of the layout viewport, in
 * px — 0 while it is down.
 *
 * Phones disagree about what the keyboard does to the page: iOS Safari and
 * Chrome on Android (by default since v108) leave the layout viewport alone and
 * only shrink the VISUAL viewport, so an element fixed to `bottom: 0` ends up
 * behind the keys. `visualViewport` is the one number both report honestly:
 * whatever part of the layout viewport is below its bottom edge is keyboard.
 */
export function useKeyboardInset(): number {
  const [inset, setInset] = useState(0)
  useEffect(() => {
    const vv = typeof window !== 'undefined' ? window.visualViewport : null
    if (!vv) return
    const check = () => {
      const covered = window.innerHeight - vv.height - vv.offsetTop
      // Below ~120px it is browser chrome settling (URL bar), not a keyboard.
      setInset(covered > 120 ? Math.round(covered) : 0)
    }
    check()
    vv.addEventListener('resize', check)
    vv.addEventListener('scroll', check)
    return () => {
      vv.removeEventListener('resize', check)
      vv.removeEventListener('scroll', check)
    }
  }, [])
  return inset
}

/**
 * The message composer of a chat screen on a phone: pinned right above the tab
 * bar, and right above the keyboard while typing (the tab bar hides then — see
 * MobileTabBar), the way every messaging app behaves.
 *
 * Like StickyActionBar it renders an in-flow spacer of its own measured height,
 * so the newest message is never hidden behind it. `onHeightChange` lets the
 * screen keep the thread pinned to the bottom when the composer grows (a
 * multi-line draft, the suggestion chips appearing).
 */
export default function ComposerDock({ children, onHeightChange, ariaLabel }: {
  children: React.ReactNode
  onHeightChange?: (h: number) => void
  ariaLabel?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [h, setH] = useState(64)
  const keyboard = useKeyboardInset()
  const cb = useRef(onHeightChange)
  cb.current = onHeightChange

  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => { setH(el.offsetHeight); cb.current?.(el.offsetHeight) })
    ro.observe(el)
    setH(el.offsetHeight)
    return () => ro.disconnect()
  }, [])

  return (
    <>
      <div aria-hidden="true" style={{ height: h, flexShrink: 0 }} />
      <div
        ref={ref}
        role="group"
        aria-label={ariaLabel}
        style={{
          position: 'fixed', left: 0, right: 0, zIndex: 45,
          bottom: keyboard > 0 ? keyboard : 'var(--mobile-nav-h, 0px)',
          background: 'var(--surface)', borderTop: '1px solid var(--border)',
          padding: '8px 10px',
          transition: 'bottom var(--dur-2) var(--ease-out)',
        }}
      >
        {children}
      </div>
    </>
  )
}

/** The page's scroll container on a phone (AppShell's `.page-content`). Chat
 *  screens scroll it to the newest message. */
export function scrollPageToBottom(smooth = false) {
  const el = typeof document !== 'undefined'
    ? document.querySelector<HTMLElement>('.page-content')
    : null
  if (!el) return
  el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
}
