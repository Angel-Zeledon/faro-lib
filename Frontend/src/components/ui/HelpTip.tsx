'use client'
import { useState } from 'react'

/**
 * Small "?" help affordance. On hover (or focus) it shows a short explanation.
 * Use it next to delicate processes, ambiguous terms, or inputs whose meaning
 * isn't obvious — NOT for self-evident fields (e.g. "password").
 *
 * Positioned with fixed coords from the trigger's rect so it never gets clipped
 * by overflow:hidden containers (tables, cards).
 */
export default function HelpTip({
  text,
  size = 14,
  width = 250,
}: {
  text: string
  size?: number
  width?: number
}) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null)

  const show = (el: HTMLElement) => {
    const r = el.getBoundingClientRect()
    setPos({ x: r.left + r.width / 2, y: r.top })
  }

  return (
    <span
      tabIndex={0}
      aria-label="Ayuda"
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        cursor: 'help', verticalAlign: 'middle', outline: 'none',
      }}
      onMouseEnter={e => show(e.currentTarget)}
      onMouseLeave={() => setPos(null)}
      onFocus={e => show(e.currentTarget)}
      onBlur={() => setPos(null)}
      onClick={e => { e.preventDefault(); e.stopPropagation() }}
    >
      <span style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: size, height: size, borderRadius: '50%',
        border: '1px solid var(--border)', background: 'var(--surface-2)',
        color: 'var(--dim)', fontSize: size - 5, fontWeight: 700, lineHeight: 1,
      }}>?</span>

      {pos && (
        <span style={{
          position: 'fixed', left: pos.x, top: pos.y - 8,
          transform: 'translate(-50%, -100%)',
          background: '#1e293b', color: '#e2e8f0',
          fontSize: 11.5, lineHeight: 1.55, fontWeight: 400, textAlign: 'left',
          padding: '9px 12px', borderRadius: 8, width, zIndex: 9999,
          border: '1px solid #334155', boxShadow: '0 6px 20px rgba(0,0,0,0.5)',
          pointerEvents: 'none', whiteSpace: 'normal',
        }}>
          {text}
          <span style={{
            position: 'absolute', top: '100%', left: '50%', transform: 'translateX(-50%)',
            borderLeft: '5px solid transparent', borderRight: '5px solid transparent',
            borderTop: '5px solid #1e293b',
          }} />
        </span>
      )}
    </span>
  )
}
