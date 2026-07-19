'use client'
// Renders the per-row findings of `validateSalesCsv` (feature 1.3): one block
// per problem kind, each listing the first few offending rows and collapsing
// the rest into "y N más". Fatal groups are styled as blockers, the rest as
// advisories the user can proceed past.

import { AlertTriangle, AlertCircle, Download } from 'lucide-react'
import type { CsvIssueGroup } from '@/lib/csvCheck'
import { downloadCsvTemplate } from '@/lib/csvCheck'
import { useLanguage } from '@/contexts/LanguageContext'

export function CsvTemplateButton({ compact = false }: { compact?: boolean }) {
  const { t } = useLanguage()
  return (
    <button
      type="button"
      onClick={() => downloadCsvTemplate()}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 7,
        padding: compact ? '6px 12px' : '9px 16px',
        background: 'transparent',
        color: 'var(--accent)',
        border: '1px solid var(--accent)',
        borderRadius: 8,
        fontSize: compact ? 12 : 13,
        fontWeight: 600,
        cursor: 'pointer',
      }}
    >
      <Download size={compact ? 13 : 15} />
      {t('csv.download_template')}
    </button>
  )
}

export default function CsvIssueReport({ groups, fileName }: {
  groups:    CsvIssueGroup[]
  fileName?: string | null
}) {
  const { t } = useLanguage()
  if (groups.length === 0) return null

  const hasFatal = groups.some(g => g.fatal)
  const accent   = hasFatal ? '#dc2626' : '#d97706'
  const bg       = hasFatal ? 'rgba(220,38,38,0.06)' : 'rgba(217,119,6,0.07)'

  return (
    <div
      role={hasFatal ? 'alert' : 'status'}
      style={{
        marginTop: 14,
        border: `1px solid ${accent}44`,
        borderLeft: `4px solid ${accent}`,
        borderRadius: 10,
        background: bg,
        padding: '16px 18px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 4 }}>
        {hasFatal
          ? <AlertTriangle size={17} color={accent} style={{ flexShrink: 0, marginTop: 1 }} />
          : <AlertCircle   size={17} color={accent} style={{ flexShrink: 0, marginTop: 1 }} />}
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>
            {hasFatal ? t('csv.issues_title_fatal') : t('csv.issues_title_warn')}
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--dim)', marginTop: 3, lineHeight: 1.5 }}>
            {hasFatal ? t('csv.issues_subtitle_fatal') : t('csv.issues_subtitle_warn')}
            {fileName ? ` (${fileName})` : ''}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 14 }}>
        {groups.map(g => (
          <div key={g.kind}>
            <div style={{
              display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap',
              fontSize: 13, fontWeight: 700, color: 'var(--text)',
            }}>
              <span>{g.title}</span>
              <span style={{
                fontSize: 11, fontWeight: 700, color: g.fatal ? '#dc2626' : '#d97706',
                background: g.fatal ? 'rgba(220,38,38,0.12)' : 'rgba(217,119,6,0.14)',
                padding: '1px 8px', borderRadius: 20,
              }}>
                {g.count} {t('csv.rows_affected')}
              </span>
            </div>

            <ul style={{
              listStyle: 'none', margin: '7px 0 0', padding: 0,
              display: 'flex', flexDirection: 'column', gap: 3,
            }}>
              {g.samples.map((msg, i) => (
                <li key={i} style={{
                  fontSize: 12.5, color: 'var(--text)', lineHeight: 1.55,
                  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                }}>
                  {msg}
                </li>
              ))}
              {g.hidden > 0 && (
                <li style={{ fontSize: 12.5, color: 'var(--dim)', fontStyle: 'italic' }}>
                  {t('csv.and_more_prefix')} {g.hidden} {t('csv.and_more_suffix')}
                </li>
              )}
            </ul>

            <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 6, lineHeight: 1.55 }}>
              {g.hint}
            </div>
          </div>
        ))}
      </div>

      <div style={{
        marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
      }}>
        <CsvTemplateButton compact />
        <span style={{ fontSize: 12, color: 'var(--dim)' }}>{t('csv.template_hint')}</span>
      </div>
    </div>
  )
}
