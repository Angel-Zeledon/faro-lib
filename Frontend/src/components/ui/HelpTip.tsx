'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import FloatingBubble from '@/components/ui/FloatingBubble'

/**
 * Small "?" help affordance. Use it next to delicate processes, ambiguous
 * terms, or inputs whose meaning isn't obvious — NOT for self-evident fields.
 *
 * Two modes:
 *   · text only — shows on hover/focus, never takes the pointer.
 *   · with `visual` (a diagram, example table, steps…) — a click/Enter opens a
 *     pinned, scrollable popover (hover alone cannot hold a diagram the user
 *     needs to read), closed by Escape, outside click or its close button.
 *
 * The bubble is positioned with fixed coords and clamped to the viewport by
 * FloatingBubble, so no overflow:hidden ancestor and no screen edge can cut it.
 */
export default function HelpTip({
  text,
  size = 14,
  width = 250,
  visual,
  title,
}: {
  text: string
  size?: number
  width?: number
  visual?: React.ReactNode
  title?: string
}) {
  const { t } = useLanguage()
  const ref = useRef<HTMLSpanElement>(null)
  const [hover, setHover] = useState(false)
  const [pinned, setPinned] = useState(false)
  const rich = visual != null
  const open = pinned || (!rich && hover)
  const getRect = useCallback(() => ref.current?.getBoundingClientRect() ?? null, [])

  useEffect(() => {
    if (!pinned) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setPinned(false) }
    const onDown = (e: MouseEvent) => {
      const n = e.target as Node
      if (ref.current?.contains(n)) return
      if ((n as HTMLElement).closest?.('[data-helptip-bubble]')) return
      setPinned(false)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onDown)
    }
  }, [pinned])

  return (
    <span
      ref={ref}
      tabIndex={0}
      role={rich ? 'button' : undefined}
      aria-expanded={rich ? pinned : undefined}
      aria-label={title ?? t('common.help')}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        cursor: rich ? 'pointer' : 'help', verticalAlign: 'middle', outline: 'none',
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onFocus={() => setHover(true)}
      onBlur={() => setHover(false)}
      onKeyDown={e => { if (rich && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); setPinned(p => !p) } }}
      onClick={e => { e.preventDefault(); e.stopPropagation(); if (rich) setPinned(p => !p) }}
    >
      <span style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: size, height: size, borderRadius: '50%',
        border: `1px solid ${open ? 'var(--accent)' : 'var(--border)'}`,
        background: 'var(--surface-2)',
        color: open ? 'var(--accent)' : 'var(--dim)',
        fontSize: size - 5, fontWeight: 700, lineHeight: 1,
      }}>?</span>

      {open && (
        <span data-helptip-bubble onClick={e => e.stopPropagation()} style={{ display: 'contents' }}>
          <FloatingBubble
            getRect={getRect}
            width={rich ? Math.max(width, 340) : width}
            interactive={pinned}
            role={pinned ? 'dialog' : 'tooltip'}
          >
            {pinned && (
              <button
                type="button" aria-label={t('common.close')}
                onClick={() => setPinned(false)}
                style={{ all: 'unset', cursor: 'pointer', float: 'right', color: 'var(--dim)', padding: 2, marginLeft: 8 }}
              >
                <X size={14} aria-hidden="true" />
              </button>
            )}
            {title && <div style={{ fontWeight: 700, fontSize: 12.5, marginBottom: 6 }}>{title}</div>}
            <div style={{ marginBottom: rich ? 10 : 0 }}>{text}</div>
            {rich && pinned && visual}
            {rich && !pinned && <div style={{ color: 'var(--dim)', fontSize: 11 }}>{t('xv.click_to_expand')}</div>}
          </FloatingBubble>
        </span>
      )}
    </span>
  )
}
