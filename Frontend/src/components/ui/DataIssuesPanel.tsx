'use client'
// Problems the profiler found in the uploaded file, shown WHILE the user can
// still fix the file — before training.
//
// The profiler has always produced `profile.data_quality.issues`, and the
// backend has always stored them, but nothing rendered them: the user uploaded
// a file with 5.000 duplicate date+SKU rows and the wizard said "listo".
//
// `issue.type` is a stable English code; every sentence comes from i18n. The
// counts travel as named fields on the issue, so the Spanish sentence can place
// them wherever it reads best.

import { AlertTriangle, AlertCircle, Info } from 'lucide-react'
import type { DataQualityIssue, GranularityDetection } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'

const SEVERITY_RANK: Record<string, number> = { error: 0, warning: 1, info: 2 }

function tone(severity: string) {
  if (severity === 'error')   return { color: '#dc2626', bg: 'rgba(220,38,38,0.06)' }
  if (severity === 'warning') return { color: '#d97706', bg: 'rgba(217,119,6,0.07)' }
  return { color: '#0284c7', bg: 'rgba(2,132,199,0.06)' }
}

// A mixed-granularity file is its own issue kind: the profiler has always
// detected it and the API has always returned it, but it never had a renderer.
// Modelled as a synthetic issue so it sorts and reads with the rest.
//
// `t` is threaded in because the buckets arrive as pandas frequency aliases
// ("D", "MS"). Those are an implementation detail of the engine — a user told
// to "use MS for everything" has no way to act on that — so they are rendered
// as words.
function granularityIssue(
  g: GranularityDetection | undefined,
  t: (key: string, params?: Record<string, unknown>) => string,
): DataQualityIssue | null {
  if (!g || g.status !== 'conflict') return null
  const freqLabel = (code: string) => {
    const label = t(`freq.${code}`)
    return label === `freq.${code}` ? code : label
  }
  const perBucket = g.detected
    .map(b => `${freqLabel(b)}: ${(g.skus_by_frequency[b] || []).length}`)
    .join(', ')
  return {
    type: 'granularity_conflict',
    severity: 'error',
    message: `Mixed reporting frequency (${perBucket})`,
    detected: g.detected.map(freqLabel).join(', '),
    per_bucket: perBucket,
    suggested: g.suggested_target ? freqLabel(g.suggested_target) : '',
  }
}

export default function DataIssuesPanel({ issues, granularity }: {
  issues: DataQualityIssue[]
  granularity?: GranularityDetection
}) {
  const { t } = useLanguage()
  const conflict = granularityIssue(granularity, t)
  const all = conflict ? [conflict, ...(issues || [])] : (issues || [])
  if (all.length === 0) return null

  // A blocking issue outranks every severity: it is the reason the user is
  // being stopped, so it reads first no matter what else the file contains.
  const sorted = [...all].sort(
    (a, b) =>
      Number(!!b.blocking) - Number(!!a.blocking) ||
      (SEVERITY_RANK[a.severity] ?? 3) - (SEVERITY_RANK[b.severity] ?? 3),
  )
  // "Este archivo no puede generar un pronóstico" is a statement about a dead
  // end, and it must not be made about a problem that has an answer. Once the
  // gate classifies findings, a `blocking_fixable` one is a QUESTION — asked by
  // RemediationChoices right below this panel — and saying "impossible" above a
  // list of ways forward contradicts the next thing the user reads.
  //
  // The plain `blocking` flag stays the fallback for issues produced before the
  // gate existed, which carry no classification.
  const classified = all.some(i => i.classification)
  const blocked = classified
    ? all.some(i => i.classification === 'blocking_fatal')
    : all.some(i => i.blocking)
  const worst = blocked ? 'error' : sorted[0].severity
  const { color, bg } = tone(worst)

  return (
    <div
      role="status"
      style={{
        marginTop: 16,
        border: `1px solid ${color}44`,
        borderLeft: `4px solid ${color}`,
        borderRadius: 10,
        background: bg,
        padding: '14px 16px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {worst === 'error'
          ? <AlertTriangle size={16} color={color} />
          : worst === 'warning' ? <AlertCircle size={16} color={color} />
          : <Info size={16} color={color} />}
        <span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--text)' }}>
          {t(blocked ? 'dqissue.blocked_title' : 'dqissue.title')}
        </span>
      </div>
      <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 3, lineHeight: 1.5 }}>
        {/* "You may continue anyway" is exactly the wrong promise when the
            file cannot train — that sentence is what let a one-row upload
            spend two minutes on its way to `no_models_trained`. */}
        {t(blocked ? 'dqissue.blocked_subtitle' : 'dqissue.subtitle')}
      </div>

      <ul style={{ listStyle: 'none', margin: '12px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {sorted.map((issue, i) => {
          const key = `dqissue.${issue.type}`
          // Unknown codes fall back to the engine's own message instead of
          // rendering a raw i18n key.
          const label = t(key, issue as Record<string, unknown>)
          const fixKey = `${key}.fix`
          const fix = t(fixKey, issue as Record<string, unknown>)
          const dot = tone(issue.severity).color
          return (
            <li key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
              <span style={{
                width: 6, height: 6, borderRadius: 3, background: dot,
                flexShrink: 0, marginTop: 6,
              }} />
              <div>
                <div style={{ fontSize: 12.5, color: 'var(--text)', lineHeight: 1.55 }}>
                  {label === key ? issue.message : label}
                </div>
                {fix !== fixKey && (
                  <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 2, lineHeight: 1.5 }}>
                    {fix}
                  </div>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
