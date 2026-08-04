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

export default function RunWarningsPanel(
  { sessionId, collapsible = false }: { sessionId: string | null; collapsible?: boolean },
) {
  const { t } = useLanguage()
  const [data, setData] = useState<RunWarnings | null>(null)
  // Collapsed by default where the page's job is to show something else.
  // On /pronosticos this panel had grown to ~600px of prose above the fold, so
  // a page called "Predicciones" opened without a single prediction in view.
  // The finding still has to be reachable — it is the only place the user can
  // learn the accuracy is inflated — so it keeps its line and its colour, and
  // gives up only the room.
  const [open, setOpen] = useState(!collapsible)

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
  const findings = groups.length + (corrections.length > 0 ? 1 : 0)
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
      <button
        type="button"
        onClick={collapsible ? () => setOpen(v => !v) : undefined}
        aria-expanded={collapsible ? open : undefined}
        style={{
          all: 'unset', width: '100%',
          cursor: collapsible ? 'pointer' : 'default',
          display: 'flex', alignItems: 'flex-start', gap: 10,
        }}
      >
        {hasError
          ? <AlertTriangle size={17} color={accent} style={{ flexShrink: 0, marginTop: 1 }} />
          : <AlertCircle   size={17} color={accent} style={{ flexShrink: 0, marginTop: 1 }} />}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>
            {t('runwarn.title')}
            {collapsible && findings > 0 && (
              <span style={{
                marginLeft: 8, fontSize: 12, fontWeight: 700, color: accent,
                background: `${accent}1a`, borderRadius: 20, padding: '1px 8px',
              }}>{findings}</span>
            )}
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--dim)', marginTop: 3, lineHeight: 1.5 }}>
            {t('runwarn.subtitle')}
          </div>
        </div>
        {collapsible && (
          <ChevronDown
            size={16} color="var(--dim)" aria-hidden="true"
            style={{ flexShrink: 0, marginTop: 2,
                     transform: open ? 'rotate(180deg)' : 'none',
                     transition: 'transform .15s' }}
          />
        )}
      </button>

      {open && groups.map(g => <Group key={g.code} group={g} />)}

      {open && corrections.length > 0 && (
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
            {corrections.map((c, i) => {
              // `action` is a stable code; `description` is the engine's own
              // English and is only a fallback for a code with no copy yet.
              //
              // A leftover {placeholder} means this run predates the field the
              // sentence needs — sessions trained before `n_skus` existed still
              // have to read as prose, so they fall back too.
              const key = `runcorr.${c.action}`
              const text = t(key, c as unknown as Record<string, unknown>)
              const usable = text !== key && !/\{[a-z_]+\}/i.test(text)
              const line = usable ? text : c.description
              // Both unusable means we have nothing to say about a change we
              // made to the user's data. An empty bullet is the worst of the
              // three options: it claims something happened and then refuses to
              // say what — and that is exactly how the censored-demand notice
              // shipped, silently blank, while the engine rewrote sales figures.
              // Rendering nothing at least does not pretend.
              if (!line) return null
              return (
                <li key={i} style={{ fontSize: 12.5, color: 'var(--dim)', lineHeight: 1.55 }}>
                  {line}
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}
