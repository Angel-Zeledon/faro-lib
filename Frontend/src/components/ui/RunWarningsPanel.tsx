'use client'
// Data problems the engine found while training a session.
//
// The validation layers run in WARNING mode — they never abort a run — so until
// this panel existed the only trace was the server log. TARGET_FEATURE_LEAKAGE
// is the reason it matters: it produces a near-perfect accuracy on a forecast
// that is worthless, and the user has no way to diagnose that alone.
//
// The backend sends stable English codes; every sentence here comes from i18n.

import { useEffect, useState } from 'react'
import { AlertTriangle, AlertCircle, ChevronDown, ChevronRight, Wrench } from 'lucide-react'
import { getRunWarnings } from '@/lib/api'
import type { RunWarnings, RunWarningGroup } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'

// Codes with no dedicated copy fall back to the engine's English message
// rather than rendering a raw i18n key.
function useCodeText() {
  const { t } = useLanguage()
  return (code: string, part: 'title' | 'what' | 'fix', fallback = '') => {
    const key = `runwarn.${code}.${part}`
    const text = t(key)
    return text === key ? fallback : text
  }
}

function contextLine(context: Record<string, unknown>): string {
  return Object.entries(context)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}: ${String(v)}`)
    .join('  ·  ')
}

function Group({ group }: { group: RunWarningGroup }) {
  const { t } = useLanguage()
  const codeText = useCodeText()
  const [open, setOpen] = useState(false)

  const isError = group.severity === 'error'
  const accent  = isError ? '#dc2626' : '#d97706'
  const title   = codeText(group.code, 'title', group.samples[0]?.message || group.code)
  const what    = codeText(group.code, 'what')
  const fix     = codeText(group.code, 'fix')

  return (
    <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12, marginTop: 12 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>{title}</span>
        {group.count > 1 && (
          <span style={{
            fontSize: 11, fontWeight: 700, color: accent,
            background: isError ? 'rgba(220,38,38,0.12)' : 'rgba(217,119,6,0.14)',
            padding: '1px 8px', borderRadius: 20,
          }}>
            {group.count}
          </span>
        )}
      </div>

      {what && (
        <div style={{ fontSize: 12.5, color: 'var(--text)', marginTop: 5, lineHeight: 1.55 }}>
          {what}
        </div>
      )}
      {fix && (
        <div style={{ fontSize: 12.5, color: 'var(--dim)', marginTop: 5, lineHeight: 1.55 }}>
          <strong style={{ color: 'var(--text)' }}>{t('runwarn.how_to_fix')}</strong> {fix}
        </div>
      )}

      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 7,
          background: 'none', border: 'none', padding: 0, cursor: 'pointer',
          fontSize: 12, fontWeight: 600, color: 'var(--accent)',
        }}
      >
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        {open ? t('runwarn.hide_detail') : t('runwarn.show_detail')}
      </button>

      {open && (
        <ul style={{
          listStyle: 'none', margin: '7px 0 0', padding: 0,
          display: 'flex', flexDirection: 'column', gap: 4,
        }}>
          {group.samples.map((s, i) => (
            <li key={i} style={{
              fontSize: 12, color: 'var(--dim)', lineHeight: 1.55,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            }}>
              {/* Findings raised by the backend's own prep step carry no
                  sentence — only the numbers that identify the offending
                  rows — so the context is what the user actually needs. */}
              {s.message || contextLine(s.context)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function RunWarningsPanel({ sessionId }: { sessionId: string | null }) {
  const { t } = useLanguage()
  const [data, setData] = useState<RunWarnings | null>(null)

  useEffect(() => {
    if (!sessionId) { setData(null); return }
    let cancelled = false
    // A failure here must stay invisible: this panel is an extra, and a session
    // trained before the field existed simply has no warnings to show.
    getRunWarnings(sessionId)
      .then(r => { if (!cancelled) setData(r) })
      .catch(() => { if (!cancelled) setData(null) })
    return () => { cancelled = true }
  }, [sessionId])

  const groups = data?.validation ?? []
  const corrections = data?.corrections ?? []
  if (groups.length === 0 && corrections.length === 0) return null

  const hasError = groups.some(g => g.severity === 'error')
  const accent   = hasError ? '#dc2626' : '#d97706'

  return (
    <div
      role="status"
      style={{
        border: `1px solid ${accent}44`,
        borderLeft: `4px solid ${accent}`,
        borderRadius: 10,
        background: hasError ? 'rgba(220,38,38,0.06)' : 'rgba(217,119,6,0.07)',
        padding: '16px 18px',
        marginBottom: 18,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        {hasError
          ? <AlertTriangle size={17} color={accent} style={{ flexShrink: 0, marginTop: 1 }} />
          : <AlertCircle   size={17} color={accent} style={{ flexShrink: 0, marginTop: 1 }} />}
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>
            {t('runwarn.title')}
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--dim)', marginTop: 3, lineHeight: 1.5 }}>
            {t('runwarn.subtitle')}
          </div>
        </div>
      </div>

      {groups.map(g => <Group key={g.code} group={g} />)}

      {corrections.length > 0 && (
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12, marginTop: 12 }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            fontSize: 13, fontWeight: 700, color: 'var(--text)',
          }}>
            <Wrench size={13} />
            {t('runwarn.auto_corrected')}
          </div>
          <ul style={{
            listStyle: 'none', margin: '7px 0 0', padding: 0,
            display: 'flex', flexDirection: 'column', gap: 3,
          }}>
            {corrections.map((c, i) => (
              <li key={i} style={{ fontSize: 12.5, color: 'var(--dim)', lineHeight: 1.55 }}>
                {c.description}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
