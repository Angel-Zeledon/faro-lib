'use client'
import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { MoreHorizontal, MessageSquare, HelpCircle, LifeBuoy } from 'lucide-react'
import { siteHref } from '@/lib/siteUrls'
import { useLanguage } from '@/contexts/LanguageContext'
import { useTour } from '@/contexts/TourContext'
import { useDmUnread } from '@/lib/dmUnread'

/**
 * The top bar's secondary actions on a narrow screen, behind one "⋯".
 *
 * At 360px the bar could not hold a clock, messages, the tour, "report a
 * problem" and the bell: it measured 409–461px and the last three controls sat
 * past the right edge of the screen, unreachable. The bell stays outside (it is
 * the one that carries news); everything else is one tap deeper here — nothing
 * was removed.
 *
 * A real menu: `aria-haspopup`/`aria-expanded` on the trigger, `role="menu"`
 * with `menuitem`s, Esc and a tap outside close it, arrow keys move between
 * items, and focus returns to the trigger. Every row is 48px tall.
 */
export default function TopBarOverflowMenu() {
  const { t, lang } = useLanguage()
  const { available, active, start, stop } = useTour()
  const unread = useDmUnread()
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const tourRunning = !!available && active?.id === available.id

  useEffect(() => {
    if (!open) return
    // Focus the first item once the menu is painted.
    const first = menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')
    first?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); close() }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        const items = Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])
        if (!items.length) return
        e.preventDefault()
        const i = items.indexOf(document.activeElement as HTMLElement)
        const next = e.key === 'ArrowDown' ? (i + 1) % items.length : (i - 1 + items.length) % items.length
        items[next].focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  function close() {
    setOpen(false)
    triggerRef.current?.focus()
  }

  const itemStyle: React.CSSProperties = {
    all: 'unset', boxSizing: 'border-box', cursor: 'pointer', width: '100%',
    display: 'flex', alignItems: 'center', gap: 12, minHeight: 48, padding: '0 16px',
    fontSize: 14, color: 'var(--text)', textDecoration: 'none',
  }

  const label = t('mobile.more_actions')

  return (
    <div style={{ position: 'relative' }}>
      <button
        ref={triggerRef}
        onClick={() => (open ? close() : setOpen(true))}
        aria-label={unread > 0 ? `${label} · ${t('mobile.unread_count', { n: unread })}` : label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        className="tap-feedback"
        style={{
          all: 'unset', boxSizing: 'border-box', cursor: 'pointer', position: 'relative',
          width: 44, height: 44, borderRadius: 10,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: 'var(--muted)',
        }}
      >
        <MoreHorizontal size={20} aria-hidden="true" />
        {unread > 0 && (
          <span aria-hidden="true" style={{
            position: 'absolute', top: 9, right: 8, width: 8, height: 8, borderRadius: '50%',
            background: 'var(--accent)', border: '2px solid var(--surface)',
          }} />
        )}
      </button>

      {open && (
        <>
          <div onClick={close} aria-hidden="true" style={{ position: 'fixed', inset: 0, zIndex: 98 }} />
          <div
            ref={menuRef}
            role="menu"
            aria-label={label}
            className="popover-enter"
            style={{
              position: 'absolute', top: 'calc(100% + 6px)', right: 0, zIndex: 99,
              width: 'min(280px, calc(100vw - 16px))', padding: '6px 0',
              background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12,
              boxShadow: '0 12px 32px rgba(0,0,0,0.22)',
            }}
          >
            <Link href="/mensajes" role="menuitem" onClick={() => setOpen(false)} style={itemStyle}>
              <MessageSquare size={18} color="var(--muted)" aria-hidden="true" />
              <span style={{ flex: 1 }}>{t('messages.page_title')}</span>
              {unread > 0 && (
                <span style={{
                  minWidth: 20, height: 20, borderRadius: 10, padding: '0 6px',
                  background: 'var(--accent)', color: '#fff', fontSize: 11, fontWeight: 700,
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  {unread > 99 ? '99+' : unread}
                </span>
              )}
            </Link>
            <a href={siteHref('/docs')} target="_blank" rel="noopener" role="menuitem" onClick={() => setOpen(false)} style={itemStyle}>
              <LifeBuoy size={18} color="var(--muted)" aria-hidden="true" />
              <span style={{ flex: 1 }}>{t('help.center')}</span>
            </a>
            {/* Only on screens that have a tour — never a dead control. */}
            {available && (
              <button
                role="menuitem"
                onClick={() => { setOpen(false); if (tourRunning) stop(); else start() }}
                style={itemStyle}
              >
                <HelpCircle size={18} color="var(--muted)" aria-hidden="true" />
                <span style={{ flex: 1, minWidth: 0 }}>
                  {tourRunning
                    ? t('tour.close')
                    : `${t('tour.launch')}: ${(lang === 'en' ? available.copy.en : available.copy.es)[available.name] ?? ''}`}
                </span>
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}
