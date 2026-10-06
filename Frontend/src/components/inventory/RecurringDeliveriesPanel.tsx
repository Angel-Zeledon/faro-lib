'use client'
/**
 * Recurring delivery schedules: "N units of a product every week / fortnight /
 * half month / month, from A to B", for corporate customers who order months
 * ahead. The server turns the deliveries of the coming months into ordinary,
 * contract-locked commitments (listed in the committed-demand panel above), so
 * the purchase recommendation needs nothing new; this panel is where people
 * record, edit, pause and cancel the standing instructions and see what they
 * made.
 *
 * Reads for every signed-in user, writes for analysts and admins (the server
 * enforces it; the controls are hidden for viewers). One component for desktop
 * and phone via `useIsNarrow`.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { CalendarClock, Plus, ChevronDown, ChevronRight } from 'lucide-react'
import {
  createRecurringDelivery, getRecurringDeliveries, getRecurringDelivery, listWarehouses,
  previewRecurringDelivery, reviseRecurringDelivery, setRecurringDeliveryStatus,
} from '@/lib/api'
import type {
  RecurringDelivery, RecurringDeliveryFrequency, RecurringDeliveryPreview, RecurringDeliveryShiftRule,
  RecurringDeliveryStatus, RecurringDeliveryTerms, Warehouse,
} from '@/lib/types'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { getUser } from '@/lib/auth'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D', amber: '#B7791F', green: '#2E7D5B' }

type Filter = 'active' | 'paused' | 'ended'
const FREQUENCIES: RecurringDeliveryFrequency[] = ['weekly', 'fortnightly', 'semimonthly', 'monthly']
const SHIFT_RULES: RecurringDeliveryShiftRule[] = ['after', 'before', 'skip']

interface FormState {
  customer: string; reference: string; sku: string; warehouse: string; quantity: string
  frequency: RecurringDeliveryFrequency; weekday: string; dayOfMonth: string
  start: string; end: string; holidays: string; shift: RecurringDeliveryShiftRule
  avoidWeekends: boolean; inHistory: boolean; note: string
}

const iso = (d: Date) => d.toISOString().slice(0, 10)
const emptyForm = (): FormState => {
  const now = new Date()
  const end = new Date(Date.UTC(now.getUTCFullYear() + 1, now.getUTCMonth(), now.getUTCDate() - 1))
  return {
    customer: '', reference: '', sku: '', warehouse: '', quantity: '', frequency: 'weekly', weekday: '0',
    dayOfMonth: '1', start: iso(now), end: iso(end), holidays: '', shift: 'after', avoidWeekends: false,
    inHistory: false, note: '',
  }
}
const fromSchedule = (s: RecurringDelivery): FormState => ({
  customer: s.customer, reference: s.reference ?? '', sku: s.sku, warehouse: s.warehouse_id ?? '',
  quantity: String(s.quantity), frequency: s.frequency, weekday: String(s.weekday ?? 0),
  dayOfMonth: String(s.day_of_month ?? 1), start: s.start_date, end: s.end_date,
  holidays: s.holiday_dates.join('\n'), shift: s.shift_rule, avoidWeekends: s.avoid_weekends,
  inHistory: !s.on_top_of_base, note: s.note ?? '',
})
const num = (s: string) => (s.trim() === '' ? null : Number(s.replace(',', '.')))

export default function RecurringDeliveriesPanel({ onChanged, reloadToken }: { onChanged?: () => void; reloadToken?: number }) {
  const { t, lang } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const narrow = useIsNarrow()
  const role = getUser()?.role
  const canWrite = role === 'admin' || role === 'analyst'

  const [items, setItems] = useState<RecurringDelivery[]>([])
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [warehouses, setWarehouses] = useState<Warehouse[]>([])
  const [filter, setFilter] = useState<Filter>('active')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Record<string, RecurringDelivery | 'loading'>>({})
  const [editing, setEditing] = useState<RecurringDelivery | 'new' | null>(null)
  const [form, setForm] = useState<FormState>(emptyForm)
  const [preview, setPreview] = useState<RecurringDeliveryPreview | null>(null)

  const load = useCallback(() => {
    getRecurringDeliveries()
      .then(r => { setItems(r.items); setLoadError(null) })
      .catch(e => { setItems([]); setLoadError(errorDetail(e)) })
      .finally(() => setLoaded(true))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { load() }, [load, reloadToken])
  useEffect(() => { listWarehouses().then(setWarehouses).catch(() => setWarehouses([])) }, [])

  const shown = useMemo(() => items.filter(s =>
    filter === 'ended' ? s.status === 'cancelled' : s.status === filter), [items, filter])

  const field: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box', fontSize: narrow ? 16 : 12.5, padding: narrow ? '10px 10px' : '6px 8px',
    borderRadius: narrow ? 10 : 7, border: `1px solid ${C.border}`, background: 'var(--surface-2)', color: C.text,
    minHeight: narrow ? 44 : undefined,
  }
  const btn = (primary = false, disabled = false): React.CSSProperties => ({
    all: 'unset', cursor: disabled ? 'default' : 'pointer', boxSizing: 'border-box', display: 'inline-flex',
    alignItems: 'center', gap: 5, padding: narrow ? '0 14px' : '5px 12px', minHeight: narrow ? 44 : undefined,
    borderRadius: narrow ? 10 : 7, fontSize: narrow ? 14 : 12, fontWeight: 600, opacity: disabled ? 0.5 : 1,
    border: `1px solid ${C.border}`, color: C.text,
    ...(primary ? { background: 'color-mix(in srgb, var(--accent) 10%, transparent)' } : {}),
  })
  const chip = (color: string): React.CSSProperties => ({
    fontSize: 10.5, fontWeight: 700, color, border: `1px solid ${color}`, borderRadius: 6, padding: '1px 6px',
  })
  const lbl: React.CSSProperties = { fontSize: narrow ? 13 : 11.5, color: C.muted }
  const card: React.CSSProperties = { background: 'var(--surface)', border: `1px solid ${C.border}`, borderRadius: 8, padding: '12px 16px' }
  const fmt = (n: number) => n.toLocaleString(lang === 'en' ? 'en-US' : 'es-CO', { maximumFractionDigits: 2 })
  const weekdayName = (i: number) => t(`rdel.weekday_${i}`)
  const warehouseName = (s: RecurringDelivery) => s.warehouse_name ?? (s.warehouse_id ? s.warehouse_id : t('rdel.warehouse_all'))
  const rule = (s: RecurringDelivery) =>
    s.frequency === 'monthly' ? t('rdel.rule_monthly', { day: s.day_of_month ?? 1 })
      : s.frequency === 'semimonthly' ? t('rdel.rule_semimonthly')
        : t('rdel.rule_weekday', { weekday: weekdayName(s.weekday ?? 0) })

  const holidayList = form.holidays.split(/[\s,;]+/).map(x => x.trim()).filter(Boolean)

  function terms(): RecurringDeliveryTerms {
    const weekly = form.frequency === 'weekly' || form.frequency === 'fortnightly'
    return {
      customer: form.customer.trim(),
      reference: form.reference.trim() || null,
      sku: form.sku.trim(),
      warehouse_id: form.warehouse || null,
      quantity: num(form.quantity) ?? 0,
      frequency: form.frequency,
      weekday: weekly ? Number(form.weekday) : null,
      day_of_month: form.frequency === 'monthly' ? Number(form.dayOfMonth) : null,
      start_date: form.start,
      end_date: form.end,
      holiday_dates: holidayList,
      avoid_weekends: !weekly && form.avoidWeekends,
      shift_rule: form.shift,
      on_top_of_base: !form.inHistory,
      note: form.note.trim() || null,
    }
  }

  const qty = num(form.quantity)
  const dom = Number(form.dayOfMonth)
  const valid = form.customer.trim() !== '' && form.sku.trim() !== '' && qty !== null && Number.isFinite(qty) && qty > 0
    && form.start !== '' && form.end !== '' && form.end >= form.start
    && (form.frequency !== 'monthly' || (Number.isInteger(dom) && dom >= 1 && dom <= 31))

  function openForm(target: RecurringDelivery | 'new') {
    setEditing(target); setError(null); setNotice(null); setPreview(null)
    setForm(target === 'new' ? emptyForm() : fromSchedule(target))
  }

  async function runPreview() {
    setBusy(true); setError(null)
    try { setPreview(await previewRecurringDelivery(terms())) } catch (e: unknown) { setPreview(null); setError(errorDetail(e)) } finally { setBusy(false) }
  }

  async function save() {
    setBusy(true); setError(null); setNotice(null)
    try {
      const saved = editing && editing !== 'new'
        ? await reviseRecurringDelivery(editing.id, terms(), editing.revision)
        : await createRecurringDelivery(terms())
      setNotice(editing && editing !== 'new'
        ? t('rdel.revised', { n: saved.materialised ?? 0, updated: saved.updated ?? 0, withdrawn: saved.withdrawn ?? 0 })
        : t('rdel.saved', { n: saved.materialised ?? 0 }))
      setEditing(null); setFilter('active'); setExpanded({})
      load(); onChanged?.()
    } catch (e: unknown) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  async function changeStatus(s: RecurringDelivery, next: RecurringDeliveryStatus) {
    if (next !== 'active') {
      const ok = await confirm({
        title: t(`rdel.${next}_confirm_title`),
        message: t(`rdel.${next}_confirm_message`, { customer: s.customer, open: s.progress.open }),
        confirmLabel: t(`rdel.${next}_confirm_label`),
        cancelLabel: t('rdel.confirm_keep'),
        danger: next === 'cancelled',
      })
      if (!ok) return
    }
    setBusy(true); setError(null); setNotice(null)
    try {
      const saved = await setRecurringDeliveryStatus(s.id, next, s.revision)
      setNotice(t(`rdel.status_done_${next}`, { n: saved.materialised ?? 0, withdrawn: saved.withdrawn ?? 0 }))
      setExpanded({})
      load(); onChanged?.()
    } catch (e: unknown) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  async function toggle(s: RecurringDelivery) {
    if (expanded[s.id]) {
      setExpanded(({ [s.id]: _gone, ...rest }) => rest)
      return
    }
    setExpanded(x => ({ ...x, [s.id]: 'loading' }))
    try {
      const full = await getRecurringDelivery(s.id)
      setExpanded(x => ({ ...x, [s.id]: full }))
    } catch (e: unknown) {
      setExpanded(({ [s.id]: _gone, ...rest }) => rest)
      setError(errorDetail(e))
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: narrow ? 0 : 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <CalendarClock size={15} color="var(--accent)" aria-hidden="true" />
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: C.text }}>{t('rdel.title')}</h3>
      </div>
      <p style={{ margin: 0, fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('rdel.intro')}</p>

      {notice && <p role="status" style={{ margin: 0, fontSize: 12.5, color: C.text }}>{notice}</p>}
      {error && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{error}</p>}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', justifyContent: 'space-between' }}>
        <div role="tablist" aria-label={t('rdel.filter_aria')} style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {(['active', 'paused', 'ended'] as Filter[]).map(f => (
            <button key={f} type="button" role="tab" aria-selected={filter === f} onClick={() => setFilter(f)}
              style={{ ...btn(filter === f), fontWeight: filter === f ? 700 : 500 }}>
              {t(`rdel.filter_${f}`)}
            </button>
          ))}
        </div>
        {canWrite && (
          <button type="button" style={btn()} onClick={() => (editing ? setEditing(null) : openForm('new'))}>
            <Plus size={12} aria-hidden="true" /> {t('rdel.add')}
          </button>
        )}
      </div>

      {canWrite && editing && (
        <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            {editing === 'new' ? t('rdel.form_new') : t('rdel.form_revise', { revision: editing.revision + 1 })}
          </div>
          <div style={{ display: 'grid', gap: 8, gridTemplateColumns: narrow ? '1fr' : 'repeat(auto-fit, minmax(170px, 1fr))' }}>
            <label style={lbl}>{t('rdel.field_customer')}
              <input style={field} type="text" maxLength={200} value={form.customer}
                onChange={e => setForm(f => ({ ...f, customer: e.target.value }))} />
            </label>
            <label style={lbl}>{t('rdel.field_reference')}
              <input style={field} type="text" maxLength={100} value={form.reference}
                onChange={e => setForm(f => ({ ...f, reference: e.target.value }))} />
            </label>
            <label style={lbl}>{t('rdel.field_sku')}
              <input style={field} type="text" maxLength={200} value={form.sku} readOnly={editing !== 'new'}
                onChange={e => setForm(f => ({ ...f, sku: e.target.value }))} />
            </label>
            <label style={lbl}>{t('rdel.field_quantity')}
              <input style={field} type="number" inputMode="decimal" min={0} value={form.quantity}
                onChange={e => setForm(f => ({ ...f, quantity: e.target.value }))} />
            </label>
            <label style={lbl}>{t('rdel.field_warehouse')}
              <select style={field} value={form.warehouse} onChange={e => setForm(f => ({ ...f, warehouse: e.target.value }))}>
                <option value="">{t('rdel.warehouse_all')}</option>
                {warehouses.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            </label>
            <label style={lbl}>{t('rdel.field_frequency')}
              <select style={field} value={form.frequency}
                onChange={e => { setPreview(null); setForm(f => ({ ...f, frequency: e.target.value as RecurringDeliveryFrequency })) }}>
                {FREQUENCIES.map(k => <option key={k} value={k}>{t(`rdel.freq_${k}`)}</option>)}
              </select>
            </label>
            {(form.frequency === 'weekly' || form.frequency === 'fortnightly') && (
              <label style={lbl}>{t('rdel.field_weekday')}
                <select style={field} value={form.weekday} onChange={e => setForm(f => ({ ...f, weekday: e.target.value }))}>
                  {[0, 1, 2, 3, 4, 5, 6].map(i => <option key={i} value={i}>{weekdayName(i)}</option>)}
                </select>
              </label>
            )}
            {form.frequency === 'monthly' && (
              <label style={lbl}>{t('rdel.field_day_of_month')}
                <input style={field} type="number" inputMode="numeric" min={1} max={31} value={form.dayOfMonth}
                  onChange={e => setForm(f => ({ ...f, dayOfMonth: e.target.value }))} />
              </label>
            )}
            <label style={lbl}>{t('rdel.field_start')}
              <input style={field} type="date" value={form.start} onChange={e => setForm(f => ({ ...f, start: e.target.value }))} />
            </label>
            <label style={lbl}>{t('rdel.field_end')}
              <input style={field} type="date" value={form.end} onChange={e => setForm(f => ({ ...f, end: e.target.value }))} />
            </label>
            <label style={lbl}>{t('rdel.field_shift')}
              <select style={field} value={form.shift}
                onChange={e => setForm(f => ({ ...f, shift: e.target.value as RecurringDeliveryShiftRule }))}>
                {SHIFT_RULES.map(k => <option key={k} value={k}>{t(`rdel.shift_${k}`)}</option>)}
              </select>
            </label>
            <label style={lbl}>{t('rdel.field_note')}
              <input style={field} type="text" maxLength={300} value={form.note}
                onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
            </label>
          </div>
          {form.frequency === 'semimonthly' && <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('rdel.semimonthly_hint')}</p>}
          {form.frequency === 'fortnightly' && <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('rdel.fortnightly_hint')}</p>}

          <label style={lbl}>{t('rdel.field_holidays')}
            <textarea style={{ ...field, minHeight: 70, fontFamily: 'var(--font-mono, monospace)' }} value={form.holidays}
              placeholder="2026-12-25"
              onChange={e => { setPreview(null); setForm(f => ({ ...f, holidays: e.target.value })) }} />
            <span style={{ display: 'block', marginTop: 4, fontSize: 11, color: C.dim }}>{t('rdel.holidays_hint')}</span>
          </label>
          {form.frequency !== 'weekly' && form.frequency !== 'fortnightly' && (
            <label style={{ ...lbl, display: 'flex', alignItems: 'center', gap: 8, minHeight: narrow ? 44 : undefined }}>
              <input type="checkbox" checked={form.avoidWeekends} onChange={e => setForm(f => ({ ...f, avoidWeekends: e.target.checked }))} />
              <span>{t('rdel.avoid_weekends')}</span>
            </label>
          )}
          <label style={{ ...lbl, display: 'flex', alignItems: 'center', gap: 8, minHeight: narrow ? 44 : undefined }}>
            <input type="checkbox" checked={form.inHistory} onChange={e => setForm(f => ({ ...f, inHistory: e.target.checked }))} />
            <span>{t('rdel.in_history')}</span>
          </label>

          {preview && (
            <div style={{ fontSize: 12, color: C.text }}>
              <div style={{ color: C.muted, marginBottom: 4 }}>
                {t('rdel.preview_summary', { n: preview.total, units: fmt(preview.total_units), skipped: preview.skipped })}
                {preview.truncated && <> {t('rdel.preview_truncated')}</>}
              </div>
              <div style={{ maxHeight: 180, overflowY: 'auto', border: `1px solid ${C.border}`, borderRadius: 6 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <tbody>
                    {preview.deliveries.map((d, i) => (
                      <tr key={i} style={{ borderTop: i ? `1px solid ${C.border}` : undefined, color: d.past ? C.dim : C.text }}>
                        <td style={{ padding: '4px 8px' }}>{d.delivery_date}</td>
                        <td style={{ padding: '4px 8px', color: C.muted }}>
                          {d.shifted ? t('rdel.preview_shifted', { date: d.nominal_date }) : ''}
                          {d.past ? ` ${t('rdel.preview_past')}` : ''}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" disabled={busy || !valid} onClick={runPreview} style={btn(false, busy || !valid)}>
              {t('rdel.preview')}
            </button>
            <button type="button" disabled={busy || !valid} onClick={save} style={btn(true, busy || !valid)}>
              {busy ? t('common.saving') : editing === 'new' ? t('rdel.save') : t('rdel.save_revision')}
            </button>
            <button type="button" onClick={() => setEditing(null)} style={{ ...btn(), border: 'none', color: C.muted }}>
              {t('common.cancel')}
            </button>
          </div>
          {editing !== 'new' && <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('rdel.revise_hint')}</p>}
        </div>
      )}

      {loadError && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{loadError}</p>}
      {loaded && !loadError && shown.length === 0 && (
        <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t(`rdel.empty_${filter}`)}</p>
      )}

      {shown.length > 0 && (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {shown.map(s => {
            const p = s.progress
            const detail = expanded[s.id]
            return (
              <li key={s.id} style={{ ...card, display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                  <div style={{ minWidth: 0, flex: '1 1 260px', overflowWrap: 'anywhere' }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: C.text, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                      <span>{s.customer}</span>
                      {s.reference && <span style={{ fontWeight: 500, color: C.muted }}>· {s.reference}</span>}
                      <span style={chip(s.status === 'active' ? C.green : s.status === 'cancelled' ? C.red : C.muted)}>
                        {t(`rdel.status_${s.status}`)}
                      </span>
                      {p.overdue > 0 && <span style={chip(C.amber)}>{t('rdel.overdue_chip', { n: p.overdue })}</span>}
                      {p.missing > 0 && (
                        <span style={chip(C.amber)} title={t('rdel.missing_hint')}>{t('rdel.missing_chip', { n: p.missing })}</span>
                      )}
                      {s.last_materialise_error && (
                        <span style={chip(C.red)} title={s.last_materialise_error}>{t('rdel.error_chip')}</span>
                      )}
                      {s.status === 'active' && s.period_ended && <span style={chip(C.amber)}>{t('rdel.period_ended')}</span>}
                    </div>
                    <div style={{ fontSize: 12, color: C.text, marginTop: 2 }}>
                      {t('rdel.summary', { quantity: fmt(s.quantity), sku: s.sku, frequency: t(`rdel.freq_${s.frequency}`) })}
                      {' · '}{rule(s)}
                    </div>
                    <div style={{ fontSize: 12, color: C.dim, marginTop: 2 }}>
                      {t('rdel.period', { start: s.start_date, end: s.end_date })} · {warehouseName(s)}
                      {s.holiday_dates.length > 0 && <> · {t('rdel.holidays_count', { n: s.holiday_dates.length, rule: t(`rdel.shift_${s.shift_rule}`) })}</>}
                      {!s.on_top_of_base && <> · {t('rdel.in_history_badge')}</>}
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 14px', fontSize: 12, color: C.dim, marginTop: 4 }}>
                      <span>{t('rdel.progress', { open: p.open, fulfilled: p.fulfilled, delivered: fmt(p.delivered_units) })}</span>
                      {p.next_delivery && (
                        <span>{t('rdel.next_delivery', { date: p.next_delivery.date, units: fmt(p.next_delivery.quantity) })}</span>
                      )}
                    </div>
                  </div>
                  {canWrite && s.status !== 'cancelled' && (
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      <button type="button" disabled={busy} style={btn(false, busy)} onClick={() => openForm(s)}>{t('rdel.revise')}</button>
                      {s.status === 'active' ? (
                        <button type="button" disabled={busy} style={btn(false, busy)} onClick={() => changeStatus(s, 'paused')}>{t('rdel.pause')}</button>
                      ) : (
                        <button type="button" disabled={busy} style={btn(true, busy)} onClick={() => changeStatus(s, 'active')}>{t('rdel.resume')}</button>
                      )}
                      <button type="button" disabled={busy} style={{ ...btn(false, busy), color: C.red }} onClick={() => changeStatus(s, 'cancelled')}>
                        {t('rdel.cancel')}
                      </button>
                    </div>
                  )}
                </div>

                <div>
                  <button type="button" onClick={() => toggle(s)} aria-expanded={!!detail}
                    style={{ ...btn(), border: 'none', padding: 0, color: C.muted, fontWeight: 500 }}>
                    {detail ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
                    {t('rdel.show_deliveries', { n: p.open + p.fulfilled + p.cancelled })}
                  </button>
                  {detail === 'loading' && <p style={{ margin: '6px 0 0', fontSize: 12, color: C.dim }}>{t('common.loading')}</p>}
                  {detail && detail !== 'loading' && (
                    <div style={{ marginTop: 6, maxHeight: 260, overflowY: 'auto', border: `1px solid ${C.border}`, borderRadius: 6 }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                        <thead>
                          <tr style={{ color: C.muted, textAlign: 'left' }}>
                            <th style={{ padding: '4px 8px', fontWeight: 600 }}>{t('rdel.col_date')}</th>
                            <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }}>{t('rdel.col_quantity')}</th>
                            <th style={{ padding: '4px 8px', fontWeight: 600 }}>{t('rdel.col_state')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(detail.deliveries ?? []).map(d => (
                            <tr key={d.id} style={{ borderTop: `1px solid ${C.border}` }}>
                              <td style={{ padding: '4px 8px' }}>{d.delivery_date}</td>
                              <td style={{ padding: '4px 8px', textAlign: 'right' }}>{fmt(d.quantity)}</td>
                              <td style={{ padding: '4px 8px' }}>{t(`rdel.state_${d.status}`)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
                <div style={{ fontSize: 11, color: C.dim }}>
                  {t('rdel.revision_by', { revision: s.revision, date: s.updated_at.slice(0, 10) })}
                  {s.last_materialised_at && <> · {t('rdel.last_run', { date: s.last_materialised_at.slice(0, 16).replace('T', ' ') })}</>}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
