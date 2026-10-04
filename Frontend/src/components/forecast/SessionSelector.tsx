'use client'
import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { SessionInfo } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { granularityLabel } from '@/lib/enumLabels'
import { localeFor } from '@/lib/numberLocale'

// ── Session selector ──────────────────────────────────────────────────────────

export function SessionSelector({ sessions, selected, onSelect, selectId = 'skus-session-select', name = 'skus_session', compact = false, tourAnchor }: {
  sessions: SessionInfo[]; selected: string | null; onSelect: (id: string) => void
  selectId?: string; name?: string
  /** Drops the eyebrow and tightens the padding for the compare bar, where the
   *  surrounding copy already says what the control picks. */
  compact?: boolean
  /** Set on the primary selector only — a tour anchor has to be unique in the DOM. */
  tourAnchor?: string
}) {
  const { t, lang } = useLanguage()
  const [focused, setFocused] = useState(false)
  const trained = sessions.filter(s => s.status === 'COMPLETED')
  const current = trained.find(s => s.session_id === selected)

  // Granularity and run date answer "which one is this?" once the name is
  // ambiguous — they are context, so they sit under the name in the dim tone
  // rather than competing with it.
  const context = current
    ? [
        current.granularity ? granularityLabel(t, current.granularity) : null,
        current.updated_at ? new Date(current.updated_at).toLocaleDateString(localeFor(lang)) : null,
      ].filter(Boolean).join(' · ')
    : ''

  return (
    <div data-tour={tourAnchor} style={{ position: 'relative', minWidth: compact ? 200 : 236 }}>
      {/* The native <select> stays the control: it is stretched invisibly over
          the card below, so the dropdown, the keyboard behaviour and the
          accessible name remain the browser's, while the visible layer is free
          to give the name and its context two different weights — something a
          styled <select> cannot do, since option text has one style. */}
      <select
        id={selectId}
        name={name}
        aria-label={t('skus.session_label')}
        value={selected ?? ''}
        onChange={e => onSelect(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        style={{
          position: 'absolute', inset: 0, width: '100%', height: '100%',
          margin: 0, padding: 0, border: 'none', appearance: 'none',
          opacity: 0, cursor: 'pointer', zIndex: 1,
        }}
      >
        <option value="" disabled>{t('skus.select_trained_session')}</option>
        {trained.map(s => <option key={s.session_id} value={s.session_id}>{s.name}</option>)}
      </select>
      <div
        aria-hidden
        style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: compact ? '5px 10px' : '6px 12px',
          background: 'var(--surface)',
          border: `1px solid ${focused ? 'var(--accent)' : 'var(--border)'}`,
          borderRadius: 9,
          // The real focus ring lands on the transparent <select>, where nobody
          // could see it — so the visible layer redraws the very same ring the
          // global :focus-visible rule uses. Not a new treatment, just relocated.
          outline: focused ? '2px solid var(--accent)' : 'none',
          outlineOffset: 2,
          transition: 'border-color var(--dur-1) var(--ease-out)',
        }}
      >
        <div style={{ minWidth: 0, flex: 1 }}>
          {!compact && (
            <div style={{
              fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
              textTransform: 'uppercase', color: 'var(--dim)', lineHeight: 1.4,
            }}>
              {t('skus.session_eyebrow')}
            </div>
          )}
          <div style={{
            fontSize: 12, fontWeight: 600, lineHeight: 1.35,
            color: current ? 'var(--text)' : 'var(--dim)',
            overflow: 'hidden', overflowWrap: 'anywhere',
          }}>
            {current?.name ?? t('skus.select_trained_session')}
          </div>
          {context && (
            <div style={{
              fontSize: 10, color: 'var(--dim)', lineHeight: 1.35,
              overflow: 'hidden', overflowWrap: 'anywhere',
            }}>
              {context}
            </div>
          )}
        </div>
        <ChevronDown size={13} style={{ flexShrink: 0, color: 'var(--dim)' }} />
      </div>
    </div>
  )
}
