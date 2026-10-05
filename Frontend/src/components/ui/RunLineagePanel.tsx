'use client'
// "How this forecast was produced" — the run's lineage manifest, for a person.
//
// The manifest is written once when a training run ends and cannot be edited
// afterwards (`GET /sessions/{id}/manifest`). This panel is the compact reading
// of it; the full record, configuration included, is what "Copy JSON" and
// "Download JSON" hand over, so a buyer can attach it to an audit question
// without anybody opening a database.
//
// Every sentence comes from i18n (`lineage.*`); the backend sends machine
// values only. Model ids go through `modelLabel` like everywhere else on the
// forecast screens.

import { useEffect, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Copy, Download, Fingerprint } from 'lucide-react'
import { getSessionManifest } from '@/lib/api'
import type { LineageManifest } from '@/lib/api'
import { useLanguage } from '@/contexts/LanguageContext'
import { modelLabel } from '@/lib/modelLabel'
import { localeFor } from '@/lib/numberLocale'

// The raw stages group into four phases a person can read. The seconds per raw
// stage stay in the JSON export.
const PHASE_OF: Record<string, 'prepare' | 'train' | 'forecast' | 'finish'> = {
  init: 'prepare', load: 'prepare', gap_fill: 'prepare', outliers: 'prepare', sync: 'prepare',
  inspect: 'prepare', routing: 'prepare', pipeline_load: 'prepare', validate: 'prepare',
  quality: 'prepare', assign_models: 'prepare', features: 'prepare',
  ml_training: 'train', global_model: 'train', stat_training: 'train', ensemble: 'train',
  future_forecast: 'forecast', inventory: 'forecast', registry: 'forecast', results: 'forecast',
  saving: 'forecast', forecast: 'forecast',
  indexing: 'finish', artifacts: 'finish',
}
const PHASES = ['prepare', 'train', 'forecast', 'finish'] as const

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 12, padding: '6px 0', fontSize: 12.5,
                  borderTop: '1px solid var(--border)', flexWrap: 'wrap' }}>
      <div style={{ width: 150, flexShrink: 0, color: 'var(--dim)' }}>{label}</div>
      <div style={{ flex: 1, minWidth: 0, color: 'var(--text)', overflowWrap: 'anywhere' }}>{children}</div>
    </div>
  )
}

const mono: React.CSSProperties = { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 11.5 }

export default function RunLineagePanel({ sessionId }: { sessionId: string | null }) {
  const { t, lang } = useLanguage()
  const [found, setFound] = useState<LineageManifest | null | undefined>(undefined)
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    setFound(undefined)
    setOpen(false)
    if (!sessionId) return
    let live = true
    getSessionManifest(sessionId)
      .then(m => { if (live) setFound(m) })
      .catch(() => { if (live) setFound(null) })
    return () => { live = false }
  }, [sessionId])

  if (!sessionId || found === undefined) return null

  const card: React.CSSProperties = {
    border: '1px solid var(--border)', borderRadius: 10, background: 'var(--surface)',
    padding: '10px 14px',
  }

  // A run from before manifests existed: say so instead of showing nothing.
  if (found === null) {
    return (
      <div style={{ ...card, display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, color: 'var(--dim)' }}>
        <Fingerprint size={14} aria-hidden="true" />
        <span>{t('lineage.not_available')}</span>
      </div>
    )
  }

  const m = found.manifest
  const fmt = (iso: string | null) => iso
    ? new Date(iso).toLocaleString(localeFor(lang), { dateStyle: 'medium', timeStyle: 'short' }) : '—'
  const number = (n: number | null | undefined) =>
    n == null ? '—' : n.toLocaleString(localeFor(lang))
  const short = (h: string | null) => (h ? `${h.slice(0, 12)}…` : '—')

  const trigger = {
    user:     t('lineage.trigger_user', { who: m.trigger.label ?? m.trigger.actor_id }),
    schedule: t('lineage.trigger_schedule'),
    api_key:  t('lineage.trigger_api_key', { who: m.trigger.label ?? m.trigger.actor_id }),
    system:   t('lineage.trigger_system'),
  }[m.trigger.kind] ?? m.trigger.actor_id

  const phaseSeconds = PHASES.map(p => ({
    phase: p,
    seconds: Object.entries(m.stage_timings_seconds)
      .filter(([stage]) => (PHASE_OF[stage] ?? 'finish') === p)
      .reduce((sum, [, s]) => sum + s, 0),
  }))

  const json = () => JSON.stringify(found, null, 2)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(json())
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch { /* clipboard blocked: the download below still works */ }
  }
  const download = () => {
    const url = URL.createObjectURL(new Blob([json()], { type: 'application/json' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `lineage-${found.session_id.slice(0, 8)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const btn: React.CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 600,
    padding: '5px 10px', borderRadius: 8, border: '1px solid var(--border)',
    background: 'transparent', color: 'var(--text)', cursor: 'pointer',
  }

  return (
    <div style={card} data-testid="run-lineage-panel">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, width: '100%' }}
      >
        {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
        <Fingerprint size={15} aria-hidden="true" style={{ color: 'var(--accent)' }} />
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>{t('lineage.title')}</span>
        <span style={{ fontSize: 12, color: 'var(--dim)', marginLeft: 4 }}>
          {trigger} · {fmt(m.timing.finished_at ?? found.created_at)}
        </span>
      </button>

      {open && (
        <div style={{ marginTop: 10 }}>
          {m.outcome === 'FAILED' && (
            <div role="alert" style={{ fontSize: 12.5, color: '#B94A4A', marginBottom: 6 }}>
              {t('lineage.failed', { error: m.error ?? '' })}
            </div>
          )}
          <Row label={t('lineage.started_by')}>{trigger}</Row>
          <Row label={t('lineage.when')}>
            {m.timing.started_at ? `${fmt(m.timing.started_at)} → ` : ''}{fmt(m.timing.finished_at)}
            {m.timing.duration_seconds != null && (
              <> · {t('lineage.duration', { seconds: number(Math.round(m.timing.duration_seconds)) })}</>
            )}
          </Row>
          <Row label={t('lineage.data')}>
            {t('lineage.data_value', { name: m.dataset.name ?? '—', rows: number(m.counts.rows) })}
            <div style={{ color: 'var(--dim)', marginTop: 2 }}>
              {t('lineage.data_fingerprint')} <span style={mono} title={m.dataset.content_hash ?? ''}>{short(m.dataset.content_hash)}</span>
            </div>
          </Row>
          <Row label={t('lineage.products')}>
            {t('lineage.products_value', {
              forecast: number(m.counts.skus_forecast), excluded: number(m.counts.skus_excluded),
            })}
          </Row>
          <Row label={t('lineage.models')}>
            {Object.keys(m.models.outcomes).length === 0 ? '—' : Object.entries(m.models.outcomes).map(([id, o]) => (
              <div key={id}>
                {t('lineage.model_line', {
                  model: modelLabel(t, id), series: number(o.series),
                  wape: o.avg_wape != null ? `${(o.avg_wape * 100).toFixed(1)}%` : '—',
                })}
              </div>
            ))}
          </Row>
          <Row label={t('lineage.timings')}>
            {phaseSeconds.map(p => (
              <span key={p.phase} style={{ marginRight: 14, whiteSpace: 'nowrap' }}>
                {t(`lineage.phase_${p.phase}`)} {number(Math.round(p.seconds))} s
              </span>
            ))}
          </Row>
          <Row label={t('lineage.versions')}>
            <span style={mono}>
              {t('lineage.engine_version', { version: m.versions.engine })} · Python {m.versions.python}
              {Object.entries(m.versions.libraries)
                .filter(([, v]) => v)
                .map(([k, v]) => ` · ${k} ${v}`).join('')}
            </span>
          </Row>
          <Row label={t('lineage.forecast_fingerprint')}>
            <span style={mono} title={m.forecast.hash ?? ''}>{short(m.forecast.hash)}</span>
            <span style={{ color: 'var(--dim)' }}> · {t('lineage.forecast_series', { n: number(m.forecast.series_count) })}</span>
          </Row>
          <Row label={t('lineage.configuration')}>
            <span style={{ color: 'var(--dim)' }}>{t('lineage.configuration_note')}</span>
          </Row>

          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            <button type="button" style={btn} onClick={copy}>
              {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
              {copied ? t('lineage.copied') : t('lineage.copy_json')}
            </button>
            <button type="button" style={btn} onClick={download}>
              <Download size={13} aria-hidden="true" />{t('lineage.download_json')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
