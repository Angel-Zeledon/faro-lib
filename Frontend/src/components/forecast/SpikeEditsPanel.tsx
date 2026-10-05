'use client'
/**
 * "One-off periods" for ONE product, under its chart: what people marked as not
 * part of the baseline (who, when, why, and what the last run did with it), an
 * undo for each, and, for an analyst, a short form to mark another period. The
 * chart's outlier dots are offered as one-click starting points.
 *
 * Marks are append-only on the server and change nothing until the NEXT
 * training or re-forecast: nothing here retrains, and the panel says so.
 */
import { useState } from 'react'
import { Eraser, Undo2 } from 'lucide-react'
import { createSpikeEdit, revertSpikeEdit } from '@/lib/api'
import type { SpikeEdit, SpikeEditReason } from '@/lib/types'
import { useErrorDetail } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D' }
const field: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', fontSize: 12.5, padding: '6px 8px', borderRadius: 7,
  border: `1px solid ${C.border}`, background: 'var(--surface-2)', color: C.text,
}
const FALLBACK_REASONS: SpikeEditReason[] = ['one_off_order', 'other']

export default function SpikeEditsPanel({ sessionId, sku, edits, reasons, outlierDates, onChanged }: {
  sessionId: string
  sku: string
  edits: SpikeEdit[]
  reasons: SpikeEditReason[]
  /** Dates of the outlier dots on the chart: each can start a mark. */
  outlierDates: string[]
  onChanged: () => void
}) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const canEdit = getUser()?.role !== 'viewer'
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState({ start: '', end: '', reason: 'one_off_order' as SpikeEditReason, note: '' })

  if (edits.length === 0 && !canEdit) return null

  const valid = !!form.start && !!form.end && form.end >= form.start
  const needsNote = form.reason === 'other' && !form.note.trim()
  const startFrom = (date: string) => {
    setForm(f => ({ ...f, start: date, end: date })); setOpen(true); setError(null)
  }

  async function save() {
    setBusy(true); setError(null)
    try {
      await createSpikeEdit(sessionId, {
        sku, start_date: form.start, end_date: form.end,
        reason_code: form.reason, reason_note: form.note.trim() || undefined,
      })
      setOpen(false); setForm(f => ({ ...f, start: '', end: '', note: '' }))
      onChanged()
    } catch (e: unknown) {
      setError(errorDetail(e))
    } finally { setBusy(false) }
  }

  async function undo(id: string) {
    setBusy(true); setError(null)
    try { await revertSpikeEdit(id); onChanged() }
    catch (e: unknown) { setError(errorDetail(e)) }
    finally { setBusy(false) }
  }

  const runLine = (e: SpikeEdit) => {
    if (!e.applied) return t('spike.not_applied_yet')
    if (e.applied.points_treated === 0) return t('spike.run_no_data')
    return t('spike.run_treated', { n: e.applied.points_treated })
  }

  return (
    <div style={{ borderTop: `1px solid ${C.border}`, padding: '10px 16px' }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        {t('spike.title')}
      </div>
      {edits.length === 0 && <p style={{ margin: '0 0 8px', fontSize: 12, color: C.dim }}>{t('spike.none')}</p>}
      {edits.length > 0 && (
        <ul style={{ listStyle: 'none', margin: '0 0 8px', padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {edits.map(e => (
            <li key={e.id} style={{ fontSize: 12, color: C.text, overflowWrap: 'anywhere', display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
              <span>
                {t('spike.line', {
                  name: e.created_by_name || '—', from: e.start_date, to: e.end_date,
                  reason: t(`spike.reason.${e.reason_code}`),
                })}
                {e.reason_note ? ` (${e.reason_note})` : ''}
                <span style={{ color: C.dim }}> · {runLine(e)}</span>
              </span>
              {canEdit && (
                <button type="button" disabled={busy} onClick={() => undo(e.id)} style={{
                  all: 'unset', cursor: busy ? 'default' : 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
                  fontSize: 11.5, fontWeight: 600, color: 'var(--accent)', opacity: busy ? 0.5 : 1,
                }}>
                  <Undo2 size={12} aria-hidden="true" /> {t('spike.undo')}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {error && !open && <p role="alert" style={{ margin: '0 0 8px', fontSize: 12, color: C.red }}>{error}</p>}
      {canEdit && !open && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <button type="button" onClick={() => { setOpen(true); setError(null) }} style={{
            all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5,
            padding: '4px 10px', borderRadius: 7, fontSize: 11.5, fontWeight: 600, border: `1px solid ${C.border}`, color: C.text,
          }}>
            <Eraser size={12} aria-hidden="true" /> {t('spike.add')}
          </button>
          {outlierDates.slice(0, 6).map(d => (
            <button key={d} type="button" onClick={() => startFrom(d)} style={{
              all: 'unset', cursor: 'pointer', padding: '4px 8px', borderRadius: 7, fontSize: 11.5,
              border: '1px dashed #B7791F', color: '#B7791F',
            }}>
              {t('spike.exclude_outlier', { date: d })}
            </button>
          ))}
        </div>
      )}
      {canEdit && open && (
        <div style={{ display: 'grid', gap: 8, gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
          <label style={{ fontSize: 11.5, color: C.muted }}>{t('spike.from')}
            <input style={field} type="date" value={form.start}
                   onChange={e => setForm(f => ({ ...f, start: e.target.value, end: f.end < e.target.value ? e.target.value : f.end }))} />
          </label>
          <label style={{ fontSize: 11.5, color: C.muted }}>{t('spike.to')}
            <input style={field} type="date" value={form.end} min={form.start}
                   onChange={e => setForm(f => ({ ...f, end: e.target.value }))} />
          </label>
          <label style={{ fontSize: 11.5, color: C.muted }}>{t('spike.reason_label')}
            <select style={field} value={form.reason} onChange={e => setForm(f => ({ ...f, reason: e.target.value as SpikeEditReason }))}>
              {(reasons.length ? reasons : FALLBACK_REASONS).map(r =>
                <option key={r} value={r}>{t(`spike.reason.${r}`)}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 11.5, color: C.muted }}>{t('spike.note_label')}
            <input style={field} type="text" maxLength={300} value={form.note}
                   onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
          </label>
          {error && <p role="alert" style={{ gridColumn: '1 / -1', margin: 0, fontSize: 12, color: C.red }}>{error}</p>}
          <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 8 }}>
            <button type="button" disabled={busy || !valid || needsNote} onClick={save} style={{
              all: 'unset', cursor: busy || !valid || needsNote ? 'default' : 'pointer', padding: '5px 12px', borderRadius: 7,
              fontSize: 12, fontWeight: 600, border: `1px solid ${C.border}`, color: C.text, opacity: busy || !valid || needsNote ? 0.5 : 1,
            }}>{busy ? t('common.saving') : t('spike.save')}</button>
            <button type="button" onClick={() => setOpen(false)} style={{
              all: 'unset', cursor: 'pointer', padding: '5px 12px', borderRadius: 7, fontSize: 12, color: C.muted,
            }}>{t('common.cancel')}</button>
          </div>
        </div>
      )}
      <p style={{ margin: '8px 0 0', fontSize: 11, color: C.dim }}>{t('spike.hint')}</p>
    </div>
  )
}
