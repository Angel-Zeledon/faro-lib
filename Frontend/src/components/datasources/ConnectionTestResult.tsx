'use client'
/**
 * The staged connection test, as a checklist: one row per layer (DNS, network,
 * TLS, login, privileges, test read, tables), each with an icon, a status word
 * (colour is never the only signal) and — when it is not plain "ok" — the
 * sentence for its code, in the user's language.
 *
 * When the login can write, the least-privilege statements the backend wrote
 * for this engine are shown with a copy button: the fix, not just the warning.
 */
import { useState } from 'react'
import { AlertTriangle, CheckCircle2, Copy, MinusCircle, XCircle } from 'lucide-react'
import Spinner from '@/components/ui/Spinner'
import { useLanguage } from '@/contexts/LanguageContext'
import { translateErrorParts } from '@/lib/errorMessage'
import type { ConnectionProbe, ProbeStage, ProbeStatus, StoredProbe } from '@/lib/types'

const C = {
  surface: 'var(--surface)', inset: 'var(--surface-3)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', green: 'var(--accent)',
  amber: 'var(--warning)', red: 'var(--danger)',
}
const MONO = "ui-monospace, 'JetBrains Mono', 'SF Mono', 'Cascadia Mono', Consolas, monospace"
const tint = (c: string, pct: number) => `color-mix(in srgb, ${c} ${pct}%, transparent)`

const STATUS_COLOR: Record<ProbeStatus, string> = {
  ok: C.green, warning: C.amber, failed: C.red, skipped: C.muted,
}

function StatusIcon({ status }: { status: ProbeStatus }) {
  const props = { size: 15, 'aria-hidden': true, style: { color: STATUS_COLOR[status], flexShrink: 0 } } as const
  if (status === 'ok') return <CheckCircle2 {...props} />
  if (status === 'warning') return <AlertTriangle {...props} />
  if (status === 'failed') return <XCircle {...props} />
  return <MinusCircle {...props} />
}

/** A driver's reason is worth showing only when it is real text, not a code. */
function driverReason(stage: ProbeStage): string | null {
  const r = stage.params?.reason
  if (typeof r !== 'string' || !r.trim()) return null
  if (/^[a-z_]+$/.test(r)) return null
  return r
}

export default function ConnectionTestResult({ probe, running }: {
  probe: ConnectionProbe | StoredProbe | null
  running?: boolean
}) {
  const { t, lang } = useLanguage()
  const [copied, setCopied] = useState(false)

  if (running) {
    return (
      <div role="status" aria-live="polite" style={{ display: 'flex', alignItems: 'center', gap: 8,
        padding: '10px 14px', borderRadius: 8, border: `1px solid ${C.border}`, background: C.surface,
        color: C.muted, fontSize: 12.5 }}>
        <Spinner size={14} /> {t('data.probe.running')}
      </div>
    )
  }
  if (!probe) return null

  const stages = probe.stages ?? []
  const failed = stages.find(s => s.status === 'failed')
  const warned = stages.some(s => s.status === 'warning')
  const headColor = failed ? C.red : warned ? C.amber : C.green
  const headline = failed
    ? t('data.probe.failed_at', { stage: t(`data.probe.stage.${failed.stage}`) })
    : warned ? t('data.probe.ok_with_warnings') : t('data.probe.ok')

  const sentence = (s: ProbeStage): string => {
    if (s.code) {
      return translateErrorParts({ status: 400, code: s.code, params: s.params ?? {}, fieldErrors: [] }, lang)
    }
    if (s.stage === 'tables' && s.status === 'ok' && typeof s.params?.count === 'number') {
      return t('data.probe.tables_count', { count: s.params.count })
    }
    return ''
  }

  const full = 'grant_sql' in probe ? probe as ConnectionProbe : null
  const grant = full?.grant_sql ?? null
  const when = probe.tested_at ? new Date(probe.tested_at).toLocaleString(lang === 'en' ? 'en-US' : 'es-CR') : ''

  const copyGrant = async () => {
    if (!grant) return
    try {
      await navigator.clipboard.writeText(grant.join('\n'))
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch { /* clipboard unavailable: the text stays selectable */ }
  }

  return (
    <section aria-label={t('data.probe.title')} aria-live="polite"
      style={{ borderRadius: 10, border: `1px solid ${tint(headColor, 35)}`, borderLeft: `3px solid ${headColor}`,
        background: C.surface, padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <StatusIcon status={failed ? 'failed' : warned ? 'warning' : 'ok'} />
        <strong style={{ color: C.text, fontSize: 13 }}>{headline}</strong>
        <span style={{ color: C.muted, fontSize: 11.5, marginLeft: 'auto' }}>
          {when && t('data.probe.last_tested', { when })}
          {probe.server_version ? ` · ${t('data.probe.server_version', { version: probe.server_version })}` : ''}
        </span>
      </div>
      <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
        {stages.map(s => {
          const text = sentence(s)
          const reason = driverReason(s)
          return (
            <li key={s.stage} style={{ display: 'grid', gridTemplateColumns: '18px minmax(0, 1fr) auto',
              columnGap: 8, alignItems: 'start', padding: '5px 0', borderTop: `1px solid ${C.border}` }}>
              <span style={{ paddingTop: 1 }}><StatusIcon status={s.status} /></span>
              <div style={{ minWidth: 0 }}>
                <div style={{ color: s.status === 'skipped' ? C.muted : C.text, fontSize: 12.5, fontWeight: 600 }}>
                  {t(`data.probe.stage.${s.stage}`)}
                  <span style={{ color: C.muted, fontWeight: 400 }}> · {t(`data.probe.status.${s.status}`)}</span>
                </div>
                {text && (
                  <div style={{ color: s.status === 'failed' ? C.red : C.muted, fontSize: 12, lineHeight: 1.5, marginTop: 2 }}>
                    {text}
                  </div>
                )}
                {reason && (
                  <div style={{ color: C.muted, fontSize: 11, fontFamily: MONO, marginTop: 2, overflowWrap: 'anywhere' }}>
                    {t('data.probe.driver_detail', { reason })}
                  </div>
                )}
              </div>
              <span style={{ color: C.muted, fontSize: 10.5, fontVariantNumeric: 'tabular-nums', paddingTop: 2 }}>
                {typeof s.duration_ms === 'number' && s.status !== 'skipped' ? `${s.duration_ms} ms` : ''}
              </span>
            </li>
          )
        })}
      </ol>
      {full && full.tables_sample?.length > 0 && (
        <div style={{ color: C.muted, fontSize: 11.5, fontFamily: MONO, overflowWrap: 'anywhere' }}>
          {full.tables_sample.join(' · ')}{(full.table_count ?? 0) > full.tables_sample.length ? ' …' : ''}
        </div>
      )}
      {grant && grant.length > 0 && (
        <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <strong style={{ color: C.text, fontSize: 12.5 }}>{t('data.probe.grant_title')}</strong>
            <button type="button" onClick={copyGrant} className="btn"
              style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px',
                borderRadius: 7, border: `1px solid ${C.border}`, background: 'transparent', color: C.muted,
                fontSize: 11.5, fontWeight: 600, cursor: 'pointer', minHeight: 32 }}>
              <Copy size={12} aria-hidden="true" /> {copied ? t('data.probe.copied') : t('data.probe.copy')}
            </button>
          </div>
          <p style={{ color: C.muted, fontSize: 11.5, margin: '4px 0 6px', lineHeight: 1.5 }}>{t('data.probe.grant_hint')}</p>
          <pre style={{ margin: 0, padding: '8px 10px', background: C.inset, borderRadius: 8, fontFamily: MONO,
            fontSize: 11.5, color: C.text, overflowX: 'auto', whiteSpace: 'pre', maxWidth: '100%' }}>
            {grant.join('\n')}
          </pre>
        </div>
      )}
    </section>
  )
}
