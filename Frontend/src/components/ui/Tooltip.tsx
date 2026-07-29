'use client'
import { useLayoutEffect, useRef, useState } from 'react'

/**
 * Hover explanation attached to arbitrary content — typically a table column
 * heading, where the heading text itself is the target.
 *
 * Use this rather than a local copy. There were two, and they had drifted:
 * `/proveedores` positioned its bubble `absolute`, so inside the table's
 * `Card overflow="hidden"` it was clipped the moment it tried to open above the
 * header row; `/inventario` had been moved to `fixed` but still painted the
 * pre-brand dark hexes, which read as a black box dropped onto a light screen.
 *
 * Two things make it reliable:
 *   · `position: fixed` off the trigger's measured rect, so no ancestor's
 *     overflow or stacking context can cut it off.
 *   · It opens above the trigger, but flips below when there is not room —
 *     a column heading sits near the top of the page, which is exactly where
 *     "above" runs out of viewport.
 *
 * For a standalone "?" badge rather than a wrapped label, use HelpTip.
 */
const GAP = 8

export default function Tooltip({
  text,
  children,
  width = 240,
}: {
  text: string
  children: React.ReactNode
  width?: number
}) {
  const [rect, setRect] = useState<DOMRect | null>(null)
  const [below, setBelow] = useState(false)
  const bubbleRef = useRef<HTMLSpanElement>(null)

  // Measure the bubble itself instead of guessing a height: the text is
  // caller-supplied and wraps to however many lines it wraps to.
  useLayoutEffect(() => {
    if (!rect) { setBelow(false); return }
    const h = bubbleRef.current?.getBoundingClientRect().height ?? 0
    setBelow(rect.top - GAP - h < 8)
  }, [rect, text])

  return (
    <span
      style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 4 }}
      onMouseEnter={e => setRect(e.currentTarget.getBoundingClientRect())}
      onMouseLeave={() => setRect(null)}
      onFocus={e => setRect(e.currentTarget.getBoundingClientRect())}
      onBlur={() => setRect(null)}
    >
      {children}
      {rect && (
        <span
          ref={bubbleRef}
          role="tooltip"
          style={{
            position: 'fixed',
            left: rect.left + rect.width / 2,
            top: below ? rect.bottom + GAP : rect.top - GAP,
            transform: below ? 'translate(-50%, 0)' : 'translate(-50%, -100%)',
            background: 'var(--surface-3)', color: 'var(--text)',
            fontSize: 11.5, lineHeight: 1.55, fontWeight: 400, textAlign: 'left',
            padding: '9px 12px', borderRadius: 8, width, zIndex: 9999,
            border: '1px solid var(--border-strong)',
            boxShadow: '0 6px 20px rgba(0,0,0,0.28)',
            pointerEvents: 'none', whiteSpace: 'normal',
          }}
        >
          {text}
          <span style={{
            position: 'absolute',
            [below ? 'bottom' : 'top']: '100%',
            left: '50%', transform: 'translateX(-50%)',
            borderLeft: '5px solid transparent', borderRight: '5px solid transparent',
            ...(below
              ? { borderBottom: '5px solid var(--surface-3)' }
              : { borderTop: '5px solid var(--surface-3)' }),
          }} />
        </span>
      )}
    </span>
  )
}
