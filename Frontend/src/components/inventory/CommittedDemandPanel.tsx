'use client'
/**
 * Committed demand: customer orders placed months or years ahead. The purchase
 * recommendation adds them on top of the forecast (the server does the maths);
 * this panel is where people record, close and import them.
 *
 * Reads are open to every signed-in user; writing is for analysts and admins
 * (the server enforces it, the controls are hidden for viewers the same way the
 * forecast-adjustment panel does it). One component serves desktop and phone:
 * it only changes touch-target sizes with `useIsNarrow`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CalendarClock, Upload, Plus } from 'lucide-react'
import {
  bulkCreateCommittedDemand, createCommittedDemand, getCommittedDemand,
  listWarehouses, setCommittedDemandStatus,
} from '@/lib/api'
import type { CommittedDemand, CommittedDemandInput, CommittedDemandStatus, Warehouse } from '@/lib/types'
import { isApiError } from '@/lib/api'
import { translateErrorParts } from '@/lib/errorMessage'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { getUser } from '@/lib/auth'
import { parseCommittedCsv, type CsvProblem } from '@/lib/committedDemandCsv'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D', amber: '#B7791F' }

const iso = (d: Date) => d.toISOString().slice(0, 10)

interface BulkRowError { row: number; code: string; params?: Record<string, unknown> }

export default function CommittedDemandPanel() {
  const { t, lang } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const narrow = useIsNarrow()
  const role = getUser()?.role
  const canWrite = role === 'admin' || role === 'analyst'

  const [status, setStatus] = useState<CommittedDemandStatus>('open')
  const [items, setItems] = useState<CommittedDemand[]>([])
  const [loaded, setLoaded] = useState(false)
  const [warehouses, setWarehouses] = useState<Warehouse[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [formOpen, setFormOpen] = useState(false)
  const [form, setForm] = useState({
    sku: '', date: iso(new Date()), quantity: '', customer: '', probability: '100',
    warehouse: '', inHistory: false, note: '',
  })

  const fileRef = useRef<HTMLInputElement>(null)
  const [csvRows, setCsvRows] = useState<CommittedDemandInput[] | null>(null)
  const [csvProblems, setCsvProblems] = useState<CsvProblem[]>([])
  const [csvFatal, setCsvFatal] = useState<string | null>(null)
  const [bulkErrors, setBulkErrors] = useState<BulkRowError[]>([])
  const [bulkBadRows, setBulkBadRows] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(() => {
    getCommittedDemand({ status })
      .then(r => { setItems(r.items); setLoadError(null) })
      .catch(e => { setItems([]); setLoadError(errorDetail(e)) })
      .finally(() => setLoaded(true))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status])
  useEffect(() => { load() }, [load])
  useEffect(() => { listWarehouses().then(setWarehouses).catch(() => setWarehouses([])) }, [])

  const sorted = useMemo(
    () => [...items].sort((a, b) => a.delivery_date.localeCompare(b.delivery_date)),
    [items],
  )
  const warehouseName = (id: string | null) =>
    id ? (warehouses.find(w => w.id === id)?.name ?? id) : t('committed.warehouse_all')

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

  const qty = Number(form.quantity)
  const pct = Number(form.probability)
  const valid = form.sku.trim() !== '' && form.date !== '' && Number.isFinite(qty) && qty > 0
    && Number.isFinite(pct) && pct >= 1 && pct <= 100

  async function save() {
    setBusy(true); setError(null); setNotice(null)
    try {
      await createCommittedDemand({
        sku: form.sku.trim(), delivery_date: form.date, quantity: qty,
        customer: form.customer.trim() || null, probability: pct / 100,
        warehouse_id: form.warehouse || null, on_top_of_base: !form.inHistory,
        note: form.note.trim() || null,
      })
      setFormOpen(false)
      setForm(f => ({ ...f, sku: '', quantity: '', customer: '', note: '', probability: '100', inHistory: false }))
      setStatus('open'); load()
    } catch (e: unknown) {
      setError(errorDetail(e))
    } finally { setBusy(false) }
  }

  async function changeStatus(c: CommittedDemand, next: CommittedDemandStatus) {
    if (next === 'cancelled') {
      const ok = await confirm({
        title: t('committed.cancel_confirm_title'),
        message: t('committed.cancel_confirm_message', { sku: c.sku, quantity: c.quantity, date: c.delivery_date }),
        confirmLabel: t('committed.cancel_confirm_label'),
        cancelLabel: t('committed.cancel_confirm_keep'),
        danger: true,
      })
      if (!ok) return
    }
    setBusy(true); setError(null); setNotice(null)
    try {
      await setCommittedDemandStatus(c.id, next)
      load()
    } catch (e: unknown) {
      setError(errorDetail(e))
    } finally { setBusy(false) }
  }

  function resetCsv() {
    setCsvRows(null); setCsvProblems([]); setCsvFatal(null); setBulkErrors([]); setBulkBadRows(0)
    if (fileRef.current) fileRef.current.value = ''
  }

  async function onFile(file: File | undefined) {
    resetCsv(); setNotice(null); setError(null)
    if (!file) return
    const text = await file.text()
    const parsed = parseCommittedCsv(text)
    if (parsed.fatal) { setCsvFatal(parsed.fatal); return }
    setCsvRows(parsed.rows); setCsvProblems(parsed.problems)
  }

  async function sendCsv() {
    if (!csvRows || csvProblems.length > 0) return
    setBusy(true); setError(null); setBulkErrors([]); setBulkBadRows(0)
    try {
      const r = await bulkCreateCommittedDemand(csvRows)
      setNotice(t('committed.import_done', { n: r.created }))
      resetCsv(); setStatus('open'); load()
    } catch (e: unknown) {
      if (isApiError(e) && e.code === 'committed_demand_bulk_invalid') {
        const list = Array.isArray(e.params?.errors) ? (e.params.errors as BulkRowError[]) : []
        setBulkErrors(list)
        setBulkBadRows(Number(e.params?.bad_rows ?? list.length))
      } else {
        setError(errorDetail(e))
      }
    } finally { setBusy(false) }
  }

  const rowReason = (e: BulkRowError) =>
    translateErrorParts({ status: 422, code: e.code, params: e.params ?? {}, fieldErrors: [] }, lang)
    || t('committed.row_invalid')

  const lbl: React.CSSProperties = { fontSize: narrow ? 13 : 11.5, color: C.muted }
  const card: React.CSSProperties = {
    background: 'var(--surface)', border: `1px solid ${C.border}`, borderRadius: 8, padding: '12px 16px',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: narrow ? 0 : 16 }}>
      <p style={{ margin: 0, fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('committed.intro')}</p>

      {notice && <p role="status" style={{ margin: 0, fontSize: 12.5, color: C.text }}>{notice}</p>}
      {error && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{error}</p>}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', justifyContent: 'space-between' }}>
        <div role="tablist" aria-label={t('committed.status_aria')} style={{ display: 'flex', gap: 4 }}>
          {(['open', 'fulfilled', 'cancelled'] as CommittedDemandStatus[]).map(s => (
            <button key={s} type="button" role="tab" aria-selected={status === s} onClick={() => setStatus(s)}
              style={{ ...btn(status === s), fontWeight: status === s ? 700 : 500 }}>
              {t(`committed.status_${s}`)}
            </button>
          ))}
        </div>
        {canWrite && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" style={btn()} onClick={() => { setFormOpen(o => !o); setError(null) }}>
              <Plus size={12} aria-hidden="true" /> {t('committed.add')}
            </button>
            <button type="button" style={btn()} onClick={() => fileRef.current?.click()}>
              <Upload size={12} aria-hidden="true" /> {t('committed.import')}
            </button>
            <input ref={fileRef} type="file" accept=".csv,text/csv,text/plain" hidden
              onChange={e => onFile(e.target.files?.[0])} />
          </div>
        )}
      </div>

      {canWrite && formOpen && (
        <div style={{ ...card, display: 'grid', gap: 8, gridTemplateColumns: narrow ? '1fr' : 'repeat(auto-fit, minmax(170px, 1fr))' }}>
          <label style={lbl}>{t('committed.field_sku')}
            <input style={field} type="text" maxLength={200} value={form.sku}
              onChange={e => setForm(f => ({ ...f, sku: e.target.value }))} />
          </label>
          <label style={lbl}>{t('committed.field_date')}
            <input style={field} type="date" value={form.date}
              onChange={e => setForm(f => ({ ...f, date: e.target.value }))} />
          </label>
          <label style={lbl}>{t('committed.field_quantity')}
            <input style={field} type="number" inputMode="decimal" min={0} value={form.quantity}
              onChange={e => setForm(f => ({ ...f, quantity: e.target.value }))} />
          </label>
          <label style={lbl}>{t('committed.field_customer')}
            <input style={field} type="text" maxLength={200} value={form.customer}
              onChange={e => setForm(f => ({ ...f, customer: e.target.value }))} />
          </label>
          <label style={lbl}>{t('committed.field_probability')}
            <input style={field} type="number" inputMode="decimal" min={1} max={100} value={form.probability}
              onChange={e => setForm(f => ({ ...f, probability: e.target.value }))} />
          </label>
          <label style={lbl}>{t('committed.field_warehouse')}
            <select style={field} value={form.warehouse} onChange={e => setForm(f => ({ ...f, warehouse: e.target.value }))}>
              <option value="">{t('committed.warehouse_all')}</option>
              {warehouses.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          </label>
          <label style={lbl}>{t('committed.field_note')}
            <input style={field} type="text" maxLength={500} value={form.note}
              onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
          </label>
          <label style={{ ...lbl, display: 'flex', alignItems: 'center', gap: 8, gridColumn: '1 / -1', minHeight: narrow ? 44 : undefined }}>
            <input type="checkbox" checked={form.inHistory}
              onChange={e => setForm(f => ({ ...f, inHistory: e.target.checked }))} />
            <span>{t('committed.in_history')}</span>
          </label>
          <p style={{ gridColumn: '1 / -1', margin: 0, fontSize: 11, color: C.dim }}>{t('committed.in_history_hint')}</p>
          <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 8 }}>
            <button type="button" disabled={busy || !valid} onClick={save} style={btn(true, busy || !valid)}>
              {busy ? t('common.saving') : t('committed.save')}
            </button>
            <button type="button" onClick={() => setFormOpen(false)} style={{ ...btn(), border: 'none', color: C.muted }}>
              {t('common.cancel')}
            </button>
          </div>
        </div>
      )}

      {canWrite && (csvFatal || csvRows) && (
        <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            {t('committed.import_title')}
          </div>
          {csvFatal && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{t(csvFatal)}</p>}
          {csvRows && (
            <p style={{ margin: 0, fontSize: 12.5, color: C.text }}>{t('committed.import_preview', { n: csvRows.length })}</p>
          )}
          {csvProblems.length > 0 && (
            <div role="alert" style={{ fontSize: 12, color: C.red }}>
              <div>{t('committed.import_file_problems', { n: csvProblems.length })}</div>
              <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                {csvProblems.slice(0, 20).map((p, i) => (
                  <li key={i}>{t('committed.import_row_error', { row: p.row, reason: t(p.reasonKey) })}</li>
                ))}
              </ul>
            </div>
          )}
          {bulkErrors.length > 0 && (
            <div role="alert" style={{ fontSize: 12, color: C.red }}>
              <div>{t('committed.import_server_rejected', { n: bulkBadRows })}</div>
              <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                {bulkErrors.map((e, i) => (
                  <li key={i}>{t('committed.import_row_error', { row: e.row, reason: rowReason(e) })}</li>
                ))}
              </ul>
            </div>
          )}
          <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('committed.import_hint')}</p>
          <div style={{ display: 'flex', gap: 8 }}>
            {csvRows && (
              <button type="button" onClick={sendCsv} disabled={busy || csvProblems.length > 0 || csvRows.length === 0}
                style={btn(true, busy || csvProblems.length > 0 || csvRows.length === 0)}>
                {busy ? t('common.saving') : t('committed.import_send', { n: csvRows.length })}
              </button>
            )}
            <button type="button" onClick={resetCsv} style={{ ...btn(), border: 'none', color: C.muted }}>
              {t('common.cancel')}
            </button>
          </div>
        </div>
      )}

      {loadError && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{loadError}</p>}
      {loaded && !loadError && sorted.length === 0 && (
        <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t(`committed.empty_${status}`)}</p>
      )}

      {sorted.length > 0 && (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {sorted.map(c => (
            <li key={c.id} style={{ ...card, display: 'flex', flexWrap: 'wrap', gap: '6px 16px', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ minWidth: 0, flex: '1 1 240px', overflowWrap: 'anywhere' }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: C.text, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  <CalendarClock size={13} color="var(--accent)" aria-hidden="true" />
                  <span>{c.sku}</span>
                  <span style={{ fontWeight: 500, color: C.muted }}>
                    {t('committed.line_units', { quantity: c.quantity.toLocaleString(), probability: Math.round(c.probability * 100) })}
                  </span>
                  {c.overdue && (
                    <span style={{ fontSize: 10.5, fontWeight: 700, color: C.amber, border: `1px solid ${C.amber}`, borderRadius: 6, padding: '1px 6px' }}>
                      {t('committed.overdue')}
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 12, color: C.dim, marginTop: 2 }}>
                  {c.delivery_date} · {c.customer || t('committed.customer_unknown')} · {warehouseName(c.warehouse_id)}
                  {!c.on_top_of_base && <> · {t('committed.in_history_badge')}</>}
                </div>
                {c.note && <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>{c.note}</div>}
              </div>
              {canWrite && (
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {c.status === 'open' ? (
                    <>
                      <button type="button" disabled={busy} style={btn(false, busy)} onClick={() => changeStatus(c, 'fulfilled')}>
                        {t('committed.mark_fulfilled')}
                      </button>
                      <button type="button" disabled={busy} style={{ ...btn(false, busy), color: C.red }} onClick={() => changeStatus(c, 'cancelled')}>
                        {t('committed.cancel_commitment')}
                      </button>
                    </>
                  ) : (
                    <button type="button" disabled={busy} style={btn(false, busy)} onClick={() => changeStatus(c, 'open')}>
                      {t('committed.reopen')}
                    </button>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
