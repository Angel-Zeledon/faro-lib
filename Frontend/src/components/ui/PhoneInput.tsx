'use client'
/**
 * One control for every phone number in the app: a searchable country picker
 * (flag, name, dial code) next to a national-number field. The value in and out
 * is E.164 (`+50688887777`), the same string the backend validates, so callers
 * hold a plain string and never see the split.
 *
 * - Country defaults from the browser's timezone/language (`inferCountry`) when
 *   the value is empty, and from the stored number itself when it is not.
 * - Typing or pasting a number that starts with `+` re-selects the country.
 * - A stored value that is NOT E.164 (an old free-text entry) is shown as it
 *   was saved and stays editable; nothing is rewritten until the user edits it.
 * - `onChange` emits '' for an empty field and `+<dial><digits>` otherwise. It
 *   emits while the number is still short — callers decide when it is complete
 *   (`isE164`) — so a half-typed number is never silently dropped.
 */
import {
  useCallback, useEffect, useId, useMemo, useRef, useState,
  type CSSProperties, type KeyboardEvent,
} from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, Search } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { controlStyle } from './Input'
import {
  countryByIso, countryOptions, digitsOf, flagOf, inferCountry, splitE164, toE164,
  type CountryOption,
} from '@/lib/phone'

export interface PhoneInputProps {
  /** E.164, or '' for none. Legacy free text is tolerated (see above). */
  value: string
  onChange: (e164: string) => void
  id?: string
  name?: string
  required?: boolean
  disabled?: boolean
  autoFocus?: boolean
  invalid?: boolean
  'aria-label'?: string
  onEnter?: () => void
  /** `auth` is the always-light sign-in/sign-up surface; `app` follows the theme. */
  variant?: 'app' | 'auth'
  style?: CSSProperties
}

const PANEL_MAX = 300
const SEARCH_H = 46        // height of the search row above the list
const MOBILE_MAX = 560     // at or below this width the picker is a bottom sheet

export default function PhoneInput({
  value, onChange, id, name, required, disabled, autoFocus, invalid,
  'aria-label': ariaLabel, onEnter, variant = 'app', style,
}: PhoneInputProps) {
  const { t, lang } = useLanguage()
  const autoId = useId()
  const inputId = id ?? `phone-${autoId}`
  const listId = `${inputId}-countries`

  // The first render (server and hydration) uses a fixed country; the browser's
  // guess lands after mount so server and client markup match.
  const [iso, setIso] = useState('US')
  const [text, setText] = useState('')
  const picked = useRef(false)          // the user chose a country by hand
  const emitted = useRef('')            // last E.164 we told the parent about

  // Value → local state. Runs on mount and whenever the parent sets a value we
  // did not just emit (a reset, a stored number loading in, "change number").
  useEffect(() => {
    if (value === emitted.current && (value !== '' || text !== '')) return
    if (!value) {
      if (!picked.current) setIso(inferCountry())
      setText('')
      emitted.current = ''
      return
    }
    const parts = splitE164(value, picked.current ? iso : inferCountry())
    setIso(parts.iso)
    setText(parts.national)
    emitted.current = parts.parsed ? value : ''
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  const emit = useCallback((nextIso: string, nextText: string) => {
    const e164 = toE164(nextIso, nextText)
    emitted.current = e164
    onChange(e164)
  }, [onChange])

  const onNumberChange = (raw: string) => {
    // A pasted/typed international number picks its own country.
    if (raw.trimStart().startsWith('+')) {
      const digits = digitsOf(raw)
      const parts = splitE164(`+${digits}`, iso)
      if (parts.parsed) {
        picked.current = true
        setIso(parts.iso); setText(parts.national)
        emit(parts.iso, parts.national)
        return
      }
      setText(digits)
      emit(iso, digits)
      return
    }
    const cleaned = raw.replace(/[^\d\s().-]/g, '')
    setText(cleaned)
    emit(iso, cleaned)
  }

  const chooseCountry = (next: string) => {
    picked.current = true
    setIso(next)
    emit(next, text)
  }

  const isAuth = variant === 'auth'

  const selectorStyle: CSSProperties = isAuth
    ? {
        all: 'unset', boxSizing: 'border-box', display: 'flex', alignItems: 'center', gap: 6,
        padding: '0 10px', minHeight: 44, borderRadius: 11, cursor: disabled ? 'default' : 'pointer',
        background: 'var(--a-field, rgba(250,250,250,0.9))', border: '1px solid var(--a-line, rgba(9,9,11,0.085))',
        color: 'var(--a-ink, #0a0a0a)', fontSize: 14, whiteSpace: 'nowrap', flexShrink: 0,
      }
    : {
        ...controlStyle({ invalid }), width: 'auto', display: 'flex', alignItems: 'center',
        gap: 6, cursor: disabled ? 'default' : 'pointer', whiteSpace: 'nowrap', flexShrink: 0,
      }

  return (
    <div style={{ display: 'flex', gap: 6, alignItems: 'stretch', width: '100%', ...style }}>
      <CountryPicker
        iso={iso}
        listId={listId}
        lang={lang}
        disabled={disabled}
        buttonStyle={selectorStyle}
        isAuth={isAuth}
        label={t('phone.country_label')}
        searchLabel={t('phone.search_placeholder')}
        emptyLabel={t('phone.no_results')}
        onChoose={chooseCountry}
      />
      <input
        id={inputId}
        name={name}
        type="tel"
        inputMode="tel"
        autoComplete="tel-national"
        aria-label={ariaLabel}
        aria-invalid={invalid || undefined}
        required={required}
        disabled={disabled}
        autoFocus={autoFocus}
        value={text}
        placeholder={t('phone.number_placeholder')}
        onChange={e => onNumberChange(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && onEnter) onEnter() }}
        className={isAuth ? 'auth-input' : 'form-input'}
        style={isAuth ? { flex: 1, minWidth: 0 } : { ...controlStyle({ invalid }), flex: 1, minWidth: 0 }}
      />
    </div>
  )
}

/* ── Country picker ─────────────────────────────────────────────────────────── */

function CountryPicker({
  iso, listId, lang, disabled, buttonStyle, isAuth, label, searchLabel, emptyLabel, onChoose,
}: {
  iso: string; listId: string; lang: string; disabled?: boolean
  buttonStyle: CSSProperties; isAuth: boolean
  label: string; searchLabel: string; emptyLabel: string
  onChoose: (iso: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [rect, setRect] = useState<{
    left: number; top: number; width: number; up: boolean; listMax: number; sheet: boolean
  } | null>(null)
  // The auth surface paints with `--a-*` tokens scoped to `.auth-split`; the panel
  // lives in <body>, outside that scope, so the values are carried over at open.
  const [tone, setTone] = useState<{ bg: string; ink: string; mute: string; line: string } | null>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  const all = useMemo(() => countryOptions(lang), [lang])
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase().replace(/^\+/, '')
    if (!q) return all
    return all.filter(c =>
      c.name.toLowerCase().includes(q) || c.iso.toLowerCase() === q || c.dial.startsWith(q))
  }, [all, query])

  const current = countryByIso(iso)

  const place = useCallback(() => {
    const b = buttonRef.current?.getBoundingClientRect()
    if (!b) return
    const vw = window.innerWidth
    const vh = window.visualViewport?.height ?? window.innerHeight
    if (vw <= MOBILE_MAX) {
      setRect({ left: 0, top: 0, width: vw, up: false, listMax: Math.max(120, Math.min(PANEL_MAX + 60, vh * 0.7 - SEARCH_H - 24)), sheet: true })
      return
    }
    const width = Math.min(320, vw - 16)
    const left = Math.max(8, Math.min(b.left, vw - width - 8))
    const spaceBelow = vh - b.bottom - 12
    const spaceAbove = b.top - 12
    // Flip only when the list would be cramped below and there is more room above.
    const up = spaceBelow < 180 && spaceAbove > spaceBelow
    const room = (up ? spaceAbove : spaceBelow) - 4 - SEARCH_H - 10
    setRect({
      left, top: up ? b.top - 4 : b.bottom + 4, width, up,
      listMax: Math.max(96, Math.min(PANEL_MAX, room)), sheet: false,
    })
  }, [])

  const close = useCallback(() => { setOpen(false); setQuery('') }, [])

  const openPanel = () => {
    if (disabled) return
    place()
    if (isAuth && buttonRef.current) {
      const cs = getComputedStyle(buttonRef.current)
      const v = (n: string, d: string) => cs.getPropertyValue(n).trim() || d
      setTone({
        bg: v('--a-bg', '#ffffff'), ink: v('--a-ink', '#0a0a0a'), mute: v('--a-muted', '#71717a'),
        line: v('--a-line', 'rgba(9,9,11,0.12)'),
      })
    }
    setActive(Math.max(0, all.findIndex(c => c.iso === iso)))
    setOpen(true)
  }

  // Focus the search box on open; close on outside press, Escape, scroll/resize.
  useEffect(() => {
    if (!open) return
    searchRef.current?.focus()
    const onDown = (e: MouseEvent | TouchEvent) => {
      const node = e.target as Node
      if (panelRef.current?.contains(node) || buttonRef.current?.contains(node)) return
      close()
    }
    // Follow the field while the page scrolls or the viewport changes (the phone
    // keyboard resizes it); never close on those, or typing in search would.
    const onMove = () => place()
    document.addEventListener('mousedown', onDown)
    document.addEventListener('touchstart', onDown)
    window.addEventListener('resize', onMove)
    window.addEventListener('scroll', onMove, true)
    window.visualViewport?.addEventListener('resize', onMove)
    const lock = window.innerWidth <= MOBILE_MAX
    const prevOverflow = document.body.style.overflow
    if (lock) document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('touchstart', onDown)
      window.removeEventListener('resize', onMove)
      window.removeEventListener('scroll', onMove, true)
      window.visualViewport?.removeEventListener('resize', onMove)
      if (lock) document.body.style.overflow = prevOverflow
    }
  }, [open, close, place])

  useEffect(() => { setActive(0) }, [query])

  // Keep the highlighted row in view while arrowing through the list.
  useEffect(() => {
    if (!open) return
    listRef.current?.children[active]?.scrollIntoView({ block: 'nearest' })
  }, [active, open])

  const choose = (c: CountryOption) => { onChoose(c.iso); close(); buttonRef.current?.focus() }

  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); close(); buttonRef.current?.focus() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setActive(i => Math.min(shown.length - 1, i + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(0, i - 1)) }
    else if (e.key === 'Home') { e.preventDefault(); setActive(0) }
    else if (e.key === 'End') { e.preventDefault(); setActive(Math.max(0, shown.length - 1)) }
    else if (e.key === 'Enter') { e.preventDefault(); if (shown[active]) choose(shown[active]) }
  }

  const ink = isAuth ? (tone?.ink ?? '#0a0a0a') : 'var(--text)'
  const mute = isAuth ? (tone?.mute ?? '#71717a') : 'var(--muted)'
  const line = isAuth ? (tone?.line ?? 'rgba(9,9,11,0.12)') : 'var(--border)'
  const panelBg = isAuth ? (tone?.bg ?? '#fff') : 'var(--surface)'
  const rowHover = isAuth ? 'color-mix(in srgb, currentColor 9%, transparent)' : 'var(--surface-2)'
  const sheet = !!rect?.sheet

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        disabled={disabled}
        aria-label={`${label}: ${current ? `+${current.dial}` : ''}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => (open ? close() : openPanel())}
        onKeyDown={e => { if (!open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) { e.preventDefault(); openPanel() } }}
        style={buttonStyle}
      >
        <span aria-hidden style={{ fontSize: 16, lineHeight: 1 }}>{flagOf(iso)}</span>
        <span style={{ fontVariantNumeric: 'tabular-nums' }}>+{current?.dial ?? ''}</span>
        <ChevronDown size={13} aria-hidden style={{ color: mute }} />
      </button>

      {open && rect && createPortal(
        <>
        {sheet && <div aria-hidden style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.45)' }} />}
        <div
          ref={panelRef}
          role="dialog"
          aria-label={label}
          onKeyDown={onKey}
          style={{
            position: 'fixed', zIndex: 1001, boxSizing: 'border-box',
            ...(sheet
              ? { left: 0, right: 0, bottom: 0, borderRadius: '16px 16px 0 0', paddingBottom: 'env(safe-area-inset-bottom)' }
              : {
                  left: rect.left, width: rect.width, borderRadius: 10,
                  ...(rect.up ? { bottom: (window.visualViewport?.height ?? window.innerHeight) - rect.top } : { top: rect.top }),
                }),
            background: panelBg, border: `1px solid ${line}`,
            boxShadow: '0 12px 32px rgba(0,0,0,0.28)', overflow: 'hidden', color: ink,
          }}
        >
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px',
            borderBottom: `1px solid ${line}`, minHeight: SEARCH_H, boxSizing: 'border-box',
          }}>
            <Search size={14} aria-hidden style={{ color: mute, flexShrink: 0 }} />
            <input
              ref={searchRef}
              type="text"
              role="combobox"
              aria-expanded="true"
              aria-controls={listId}
              aria-activedescendant={shown[active] ? `${listId}-${shown[active].iso}` : undefined}
              aria-label={searchLabel}
              placeholder={searchLabel}
              value={query}
              onChange={e => setQuery(e.target.value)}
              autoComplete="off"
              style={{
                all: 'unset', flex: 1, minWidth: 0, fontSize: sheet ? 16 : 13, color: ink,
              }}
            />
          </div>
          <ul
            ref={listRef}
            id={listId}
            role="listbox"
            aria-label={label}
            style={{ listStyle: 'none', margin: 0, padding: 4, maxHeight: rect.listMax, overflowY: 'auto', overscrollBehavior: 'contain' }}
          >
            {shown.map((c, i) => (
              <li
                key={c.iso}
                id={`${listId}-${c.iso}`}
                role="option"
                aria-selected={c.iso === iso}
                onMouseEnter={() => setActive(i)}
                onMouseDown={e => e.preventDefault()}
                onClick={() => choose(c)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: sheet ? '11px 8px' : '7px 8px',
                  borderRadius: 7, cursor: 'pointer', fontSize: 13,
                  background: i === active
                    ? rowHover : 'transparent',
                }}
              >
                <span aria-hidden style={{ fontSize: 16, lineHeight: 1 }}>{flagOf(c.iso)}</span>
                <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {c.name}
                </span>
                <span style={{ color: mute, fontVariantNumeric: 'tabular-nums' }}>+{c.dial}</span>
                {c.iso === iso && <Check size={13} aria-hidden style={{ color: mute }} />}
              </li>
            ))}
            {shown.length === 0 && (
              <li role="presentation" style={{ padding: '10px 8px', fontSize: 12.5, color: mute }}>
                {emptyLabel}
              </li>
            )}
          </ul>
        </div>
        </>,
        document.body,
      )}
    </>
  )
}
