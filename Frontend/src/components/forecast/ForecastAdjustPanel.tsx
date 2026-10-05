'use client'
/**
 * "Adjust the forecast" for ONE product, inside the panel that explains its
 * recommendation. Shows what people already adjusted (who, how much, why) and,
 * for an analyst, a short form to add one.
 *
 * An adjustment is append-only on the server: adding one for a period that
 * overlaps an older one replaces it for planning but never erases it. How the
 * adjustments turned out is graded later, on the precision screen.
 */
import { useCallback, useEffect, useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { createForecastAdjustment, getForecastAdjustments } from '@/lib/api'
import type { AdjustmentReason, ForecastAdjustment } from '@/lib/types'
import { useErrorDetail } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { usePlanning } from '@/contexts/PlanningContext'
import { getUser } from '@/lib/auth'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D' }
const field: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', fontSize: 12.5, padding: '6px 8px', borderRadius: 7,
  border: `1px solid ${C.border}`, background: 'var(--surface-2)', color: C.text,
}

const iso = (d: Date) => d.toISOString().slice(0, 10)
export const ADJUSTMENT_RELOAD_EVENT = 'stockai:forecast-adjusted'

export function adjustmentLine(
  t: (k: string, p?: Record<string, string | number>) => string,
  a: { pct: number; reason_code: AdjustmentReason; reason_note?: string | null; created_by_name?: string | null },
): string {
  const pct = `${a.pct > 0 ? '+' : ''}${Number(a.pct.toFixed(1))}%`
  const reason = t(`forecast_adj.reason.${a.reason_code}`)
  const note = a.reason_note ? ` (${a.reason_note})` : ''
  return t('forecast_adj.applied_line', { name: a.created_by_name || '—', pct, reason }) + note
}

export default function ForecastAdjustPanel({ sku }: { sku: string }) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const sessionId = usePlanning()?.planning?.active_session_id ?? ''
  const canAdjust = getUser()?.role !== 'viewer'
  const [items, setItems] = useState<ForecastAdjustment[]>([])
  const [reasons, setReasons] = useState<AdjustmentReason[]>([])
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const today = new Date()
  const [form, setForm] = useState({
    mode: 'percent' as 'percent' | 'absolute', value: '', start: iso(today),
    end: iso(new Date(today.getTime() + 14 * 86400000)), reason: 'promotion' as AdjustmentReason, note: '',
  })

  const load = useCallback(() => {
    if (!sessionId) return
    getForecastAdjustments(sessionId, sku)
      .then(r => { setItems(r.items); setReasons(r.reasons) })
      .catch(() => setItems([]))
  }, [sessionId, sku])
  useEffect(() => { load() }, [load])

  if (!sessionId) return null
  if (items.length === 0 && !canAdjust) return null

  const valueNum = Number(form.value)
  // 0 is allowed on purpose: it clears the period without erasing its history.
  const valid = form.value.trim() !== '' && Number.isFinite(valueNum)
  const needsNote = form.reason === 'other' && !form.note.trim()

  async function save() {
    setBusy(true); setError(null)
    try {
      await createForecastAdjustment(sessionId, {
        sku, start_date: form.start, end_date: form.end, mode: form.mode, value: valueNum,
        reason_code: form.reason, reason_note: form.note.trim() || undefined,
      })
      setOpen(false); setForm(f => ({ ...f, value: '', note: '' }))
      load()
      window.dispatchEvent(new Event(ADJUSTMENT_RELOAD_EVENT))
    } catch (e: unknown) {
      setError(errorDetail(e))
    } finally { setBusy(false) }
  }

  return (
    <div style={{ background: 'var(--surface)', border: `1px solid ${C.border}`, borderRadius: 8, padding: '12px 16px', marginTop: 8 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        {t('forecast_adj.title')}
      </div>
      {items.length === 0 && <p style={{ margin: '0 0 8px', fontSize: 12, color: C.dim }}>{t('forecast_adj.none')}</p>}
      {items.length > 0 && (
        <ul style={{ listStyle: 'none', margin: '0 0 8px', padding: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
          {items.map(a => (
            <li key={a.id} style={{ fontSize: 12, color: C.text, overflowWrap: 'anywhere' }}>
              {adjustmentLine(t, a)}
              <span style={{ color: C.dim }}> · {a.start_date} → {a.end_date}</span>
            </li>
          ))}
        </ul>
      )}
      {canAdjust && !open && (
        <button type="button" onClick={() => { setOpen(true); setError(null) }} style={{
          all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5,
          padding: '4px 10px', borderRadius: 7, fontSize: 11.5, fontWeight: 600, border: `1px solid ${C.border}`, color: C.text,
        }}>
          <SlidersHorizontal size={12} aria-hidden="true" /> {t('forecast_adj.add')}
        </button>
      )}
      {canAdjust && open && (
        <div style={{ display: 'grid', gap: 8, gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
          <label style={{ fontSize: 11.5, color: C.muted }}>{t('forecast_adj.mode')}
            <select style={field} value={form.mode} onChange={e => setForm(f => ({ ...f, mode: e.target.value as 'percent' | 'absolute' }))}>
              <option value="percent">{t('forecast_adj.mode_percent')}</option>
              <option value="absolute">{t('forecast_adj.mode_absolute')}</option>
            </select>
          </label>
          <label style={{ fontSize: 11.5, color: C.muted }}>
            {form.mode === 'percent' ? t('forecast_adj.value_percent') : t('forecast_adj.value_units')}
            <input style={field} type="number" inputMode="decimal" value={form.value}
                   onChange={e => setForm(f => ({ ...f, value: e.target.value }))} />
          </label>
          <label style={{ fontSize: 11.5, color: C.muted }}>{t('forecast_adj.from')}
            <input style={field} type="date" value={form.start} onChange={e => setForm(f => ({ ...f, start: e.target.value }))} />
          </label>
          <label style={{ fontSize: 11.5, color: C.muted }}>{t('forecast_adj.to')}
            <input style={field} type="date" value={form.end} min={form.start} onChange={e => setForm(f => ({ ...f, end: e.target.value }))} />
          </label>
          <label style={{ fontSize: 11.5, color: C.muted }}>{t('forecast_adj.reason_label')}
            <select style={field} value={form.reason} onChange={e => setForm(f => ({ ...f, reason: e.target.value as AdjustmentReason }))}>
              {(reasons.length ? reasons : ['promotion', 'other'] as AdjustmentReason[]).map(r =>
                <option key={r} value={r}>{t(`forecast_adj.reason.${r}`)}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 11.5, color: C.muted }}>{t('forecast_adj.note_label')}
            <input style={field} type="text" maxLength={300} value={form.note}
                   onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
          </label>
          {error && <p role="alert" style={{ gridColumn: '1 / -1', margin: 0, fontSize: 12, color: C.red }}>{error}</p>}
          <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 8 }}>
            <button type="button" disabled={busy || !valid || needsNote} onClick={save} style={{
              all: 'unset', cursor: busy || !valid || needsNote ? 'default' : 'pointer', padding: '5px 12px', borderRadius: 7,
              fontSize: 12, fontWeight: 600, border: `1px solid ${C.border}`, color: C.text, opacity: busy || !valid || needsNote ? 0.5 : 1,
            }}>{busy ? t('common.saving') : t('forecast_adj.save')}</button>
            <button type="button" onClick={() => setOpen(false)} style={{
              all: 'unset', cursor: 'pointer', padding: '5px 12px', borderRadius: 7, fontSize: 12, color: C.muted,
            }}>{t('common.cancel')}</button>
          </div>
          <p style={{ gridColumn: '1 / -1', margin: 0, fontSize: 11, color: C.dim }}>{t('forecast_adj.hint')}</p>
        </div>
      )}
    </div>
  )
}
