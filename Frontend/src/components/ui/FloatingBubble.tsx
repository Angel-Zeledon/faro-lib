'use client'
import { useLayoutEffect, useRef, useState } from 'react'

/**
 * The bubble shared by Tooltip and HelpTip.
 *
 * Why it exists: both used to place themselves centred on the trigger with a
 * fixed width and no bounds, so a trigger near a screen edge pushed half the
 * text off-screen, and a long explanation (or one with a visual) ran past the
 * top of the viewport with no way to scroll to it. This one measures itself and
 * guarantees the whole content is reachable:
 *
 *   · horizontally clamped into the viewport (no arrow: an overflow-scrolling
 *     box clips it, and the trigger is highlighted by its own hover/focus)
 *   · opens on whichever side (above/below) has more room
 *   · `max-height` = the room on that side, with internal scroll
 *   · width never exceeds the viewport minus an 8px gutter (phones)
 *
 * `interactive` bubbles accept the pointer (scrollable, clickable); the plain
 * hover kind keeps `pointer-events: none` so it never steals the hover.
 */
const GAP = 8
const EDGE = 8

export default function FloatingBubble({
  getRect, width, interactive = false, children, role = 'tooltip', labelledBy,
}: {
  getRect: () => DOMRect | null
  width: number
  interactive?: boolean
  children: React.ReactNode
  role?: string
  labelledBy?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [place, setPlace] = useState<{
    left: number; top: number; maxH: number; below: boolean; w: number
  } | null>(null)

  useLayoutEffect(() => {
    const compute = () => {
      const r = getRect()
      const el = ref.current
      if (!r || !el) return
      const vw = window.innerWidth
      const vh = window.innerHeight
      const w = Math.min(width, vw - EDGE * 2)
      // scrollHeight, not the clamped box height: we want the content's need.
      const need = el.scrollHeight
      const roomAbove = r.top - GAP - EDGE
      const roomBelow = vh - r.bottom - GAP - EDGE
      const below = need > roomAbove && roomBelow > roomAbove
      const maxH = Math.max(80, below ? roomBelow : roomAbove)
      const h = Math.min(need, maxH)
      const center = r.left + r.width / 2
      const left = Math.min(Math.max(EDGE, center - w / 2), vw - w - EDGE)
      const top = below ? r.bottom + GAP : r.top - GAP - h
      setPlace({ left, top, maxH, below, w })
    }
    compute()
    window.addEventListener('resize', compute)
    window.addEventListener('scroll', compute, true)
    return () => {
      window.removeEventListener('resize', compute)
      window.removeEventListener('scroll', compute, true)
    }
  }, [getRect, width, children])

  return (
    <div
      ref={ref}
      role={role}
      aria-labelledby={labelledBy}
      style={{
        position: 'fixed',
        left: place?.left ?? 0, top: place?.top ?? 0,
        width: place?.w ?? width,
        maxHeight: place?.maxH,
        overflowY: 'auto', overscrollBehavior: 'contain',
        // Hidden until measured: avoids a one-frame flash in the wrong spot.
        visibility: place ? 'visible' : 'hidden',
        background: 'var(--surface-3)', color: 'var(--text)',
        fontSize: 11.5, lineHeight: 1.55, fontWeight: 400, textAlign: 'left',
        padding: '9px 12px', borderRadius: 8, zIndex: 9999,
        border: '1px solid var(--border-strong)',
        boxShadow: '0 6px 20px rgba(0,0,0,0.28)',
        pointerEvents: interactive ? 'auto' : 'none',
        whiteSpace: 'normal', boxSizing: 'border-box',
      }}
    >
      {children}
    </div>
  )
}
