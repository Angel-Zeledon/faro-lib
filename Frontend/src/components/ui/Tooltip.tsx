'use client'
import { useCallback, useRef, useState } from 'react'
import FloatingBubble from '@/components/ui/FloatingBubble'

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
 *   · FloatingBubble clamps it into the viewport, flips above/below by the room
 *     available and scrolls internally if the text is taller than that room.
 *
 * For a standalone "?" badge rather than a wrapped label, use HelpTip.
 */
export default function Tooltip({
  text,
  children,
  width = 240,
}: {
  text: string
  children: React.ReactNode
  width?: number
}) {
  const ref = useRef<HTMLSpanElement>(null)
  const [open, setOpen] = useState(false)
  const getRect = useCallback(() => ref.current?.getBoundingClientRect() ?? null, [])

  return (
    <span
      ref={ref}
      style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 4 }}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {children}
      {open && <FloatingBubble getRect={getRect} width={width}>{text}</FloatingBubble>}
    </span>
  )
}
