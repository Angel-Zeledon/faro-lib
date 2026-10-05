'use client'
import { useEffect, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import BottomSheet from '@/components/mobile/BottomSheet'

export interface MenuItem {
  label: string
  icon?: React.ReactNode
  onSelect: () => void
  disabled?: boolean
  /** Renders in the signal red. For the one destructive item in a menu. */
  danger?: boolean
}

/**
 * A button that opens a short list of actions.
 *
 * Extracted from the copy that lives inline in `/pronosticos`, because
 * `/inventario` needed the same thing and a third hand-rolled dropdown is how
 * three menus end up closing on different keys. Only two behaviours matter and
 * both were missing from the original: `Escape` closes it, and focus returns to
 * the trigger — a menu you can open with the keyboard and not leave is a trap.
 *
 * Deliberately not a generic popover: fixed placement, no submenus, no portal.
 * A toolbar overflow is all this is for.
 */
export default function MenuButton({
  label,
  items,
  icon,
  align = 'right',
  title,
  disabled = false,
}: {
  label?: string
  items: MenuItem[]
  icon?: React.ReactNode
  align?: 'left' | 'right'
  title?: string
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  // On a phone the menu is an action sheet: thumb-sized rows from the bottom
  // edge instead of a 12px dropdown anchored to a 34px trigger.
  const narrow = useIsNarrow()
  const triggerRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      setOpen(false)
      triggerRef.current?.focus()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  const usable = items.filter(i => !i.disabled)

  return (
    <div style={{ position: 'relative' }}>
      <button
        ref={triggerRef}
        onClick={() => setOpen(v => !v)}
        disabled={disabled || usable.length === 0}
        title={title}
        aria-haspopup="menu"
        aria-expanded={open}
        style={{
          all: 'unset',
          cursor: disabled || usable.length === 0 ? 'default' : 'pointer',
          display: 'flex', alignItems: 'center', gap: 6,
          padding: label ? '7px 12px' : '7px 10px',
          borderRadius: 8, fontSize: 12, fontWeight: 600,
          border: '1px solid var(--border)', color: 'var(--muted)',
          opacity: disabled || usable.length === 0 ? 0.5 : 1,
          ...(narrow ? { minHeight: 44, minWidth: 44, boxSizing: 'border-box', justifyContent: 'center', fontSize: 14, borderRadius: 10 } : {}),
        }}
      >
        {icon}
        {label}
        {label && <ChevronDown size={11} style={{ color: 'var(--dim)' }} />}
      </button>

      {narrow && (
        <BottomSheet open={open} onClose={() => setOpen(false)} title={label || title || ''}>
          <div role="menu" style={{ display: 'flex', flexDirection: 'column' }}>
            {items.map((item, i) => (
              <button
                key={item.label}
                role="menuitem"
                disabled={item.disabled}
                onClick={() => { setOpen(false); item.onSelect() }}
                className="tap-feedback"
                style={{
                  all: 'unset', boxSizing: 'border-box', minHeight: 52, width: '100%',
                  cursor: item.disabled ? 'default' : 'pointer',
                  display: 'flex', alignItems: 'center', gap: 12, padding: '0 4px', fontSize: 16,
                  color: item.danger ? 'var(--signal-order-now-fg)' : 'var(--text)',
                  opacity: item.disabled ? 0.45 : 1,
                  borderBottom: i < items.length - 1 ? '1px solid var(--border)' : 'none',
                }}
              >
                {item.icon}
                {item.label}
              </button>
            ))}
          </div>
        </BottomSheet>
      )}
      {open && !narrow && (
        <>
          {/* Click-away. Fixed, so it also catches clicks on scrolled content. */}
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 99 }} />
          <div
            role="menu"
            style={{
              position: 'absolute', top: 'calc(100% + 4px)',
              [align]: 0, zIndex: 100,
              background: 'var(--surface)',
              border: '1px solid var(--border)', borderRadius: 8,
              boxShadow: '0 8px 24px rgba(0,0,0,0.18)',
              minWidth: 210, overflow: 'hidden',
            }}
          >
            {items.map((item, i) => (
              <button
                key={item.label}
                role="menuitem"
                disabled={item.disabled}
                onClick={() => { setOpen(false); item.onSelect() }}
                style={{
                  all: 'unset', boxSizing: 'border-box',
                  cursor: item.disabled ? 'default' : 'pointer',
                  display: 'flex', alignItems: 'center', gap: 9, width: '100%',
                  padding: '9px 13px', fontSize: 12,
                  color: item.danger ? 'var(--signal-order-now-fg)' : 'var(--text)',
                  opacity: item.disabled ? 0.45 : 1,
                  borderBottom: i < items.length - 1 ? '1px solid var(--border)' : 'none',
                }}
                onMouseEnter={e => { if (!item.disabled) e.currentTarget.style.background = 'var(--surface-2)' }}
                onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
              >
                {item.icon}
                {item.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
