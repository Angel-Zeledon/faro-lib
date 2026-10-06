'use client'
/**
 * Blanket supply contracts ("contratos marco"): a customer's volume agreement
 * called off in releases. The server turns the releases due in the coming
 * months into ordinary commitments (shown in the committed-demand panel next to
 * this one), so the purchase recommendation needs nothing new; this panel is
 * where people record, revise, close and follow the contracts.
 *
 * Reads for every signed-in user, writes for analysts and admins (the server
 * enforces it; the controls are hidden for viewers like the commitments
 * panel). One component for desktop and phone via `useIsNarrow`.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { FileSignature, Plus, ChevronDown, ChevronRight, Trash2 } from 'lucide-react'
import {
  createSupplyContract, getSupplyContracts, isApiError, listWarehouses, previewSupplyContract,
  reviseSupplyContract, setSupplyContractStatus,
} from '@/lib/api'
import type {
  SupplyContract, SupplyContractReleaseInput, SupplyContractScheduleKind, SupplyContractTerms, Warehouse,
} from '@/lib/types'
import { translateErrorParts } from '@/lib/errorMessage'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { getUser } from '@/lib/auth'
import { parseReleaseTable, type ScheduleProblem } from '@/lib/contractScheduleCsv'
import ContractRenewalsPanel from '@/components/inventory/ContractRenewalsPanel'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D', amber: '#B7791F', green: '#2E7D5B' }

type Filter = 'active' | 'draft' | 'ended'
interface LineForm { sku: string; total: string; price: string }
interface RowError { row: number; code: string; params?: Record<string, unknown> }
interface FormState {
  customer: string; reference: string; warehouse: string; start: string; end: string
  kind: SupplyContractScheduleKind; tolerance: string; inHistory: boolean; note: string
  lines: LineForm[]; paste: string
  noticeDays: string; autoRenew: boolean; leadDays: string
}

const iso = (d: Date) => d.toISOString().slice(0, 10)
const emptyForm = (): FormState => {
  const now = new Date()
  const end = new Date(Date.UTC(now.getUTCFullYear() + 1, now.getUTCMonth(), now.getUTCDate() - 1))
  return {
    customer: '', reference: '', warehouse: '', start: iso(now), end: iso(end), kind: 'monthly',
    tolerance: '0', inHistory: false, note: '', lines: [{ sku: '', total: '', price: '' }], paste: '',
    noticeDays: '', autoRenew: false, leadDays: '',
  }
}
const fromContract = (c: SupplyContract): FormState => ({
  customer: c.customer, reference: c.reference ?? '', warehouse: c.warehouse_id ?? '',
  start: c.period_start, end: c.period_end, kind: c.schedule_kind,
  tolerance: String(c.tolerance_pct ?? 0), inHistory: !c.on_top_of_base, note: c.note ?? '',
  lines: c.lines.map(l => ({
    sku: l.sku, total: l.total_quantity == null ? '' : String(l.total_quantity),
    price: l.unit_price == null ? '' : String(l.unit_price),
  })),
  paste: (c.releases ?? []).map(r => `${r.sku ?? ''}\t${r.date}\t${r.quantity ?? ''}`).join('\n'),
  noticeDays: c.notice_days == null ? '' : String(c.notice_days),
  autoRenew: !!c.auto_renew,
  leadDays: (c.renewal_lead_days ?? []).join(', '),
})
const num = (s: string) => (s.trim() === '' ? null : Number(s.replace(',', '.')))
/** '60, 30 7' -> [60, 30, 7]; blank -> null (nobody chose: the default applies);
 *  anything that is not a whole number of days -> undefined (invalid). */
const parseLeadDays = (s: string): number[] | null | undefined => {
  if (s.trim() === '') return null
  const nums = s.split(/[\s,;]+/).filter(Boolean).map(Number)
  if (nums.some(n => !Number.isInteger(n) || n < 1 || n > 730) || nums.length > 6) return undefined
  return nums
}

export default function SupplyContractsPanel({ onChanged, reloadToken }: { onChanged?: () => void; reloadToken?: number }) {
  const { t, lang } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const narrow = useIsNarrow()
  const role = getUser()?.role
  const canWrite = role === 'admin' || role === 'analyst'

  const [items, setItems] = useState<SupplyContract[]>([])
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [warehouses, setWarehouses] = useState<Warehouse[]>([])
  const [filter, setFilter] = useState<Filter>('active')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  // null = form closed; otherwise the contract being revised (or 'new').
  const [editing, setEditing] = useState<SupplyContract | 'new' | null>(null)
  const [form, setForm] = useState<FormState>(emptyForm)
  const [rowErrors, setRowErrors] = useState<RowError[]>([])
  const [badRows, setBadRows] = useState(0)
  const [preview, setPreview] = useState<{ sku: string; date: string; quantity: number }[] | null>(null)

  const load = useCallback(() => {
    getSupplyContracts()
      .then(r => { setItems(r.items); setLoadError(null) })
      .catch(e => { setItems([]); setLoadError(errorDetail(e)) })
      .finally(() => setLoaded(true))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { load() }, [load, reloadToken])
  useEffect(() => { listWarehouses().then(setWarehouses).catch(() => setWarehouses([])) }, [])

  const shown = useMemo(() => items.filter(c =>
    filter === 'ended' ? (c.status === 'closed' || c.status === 'cancelled') : c.status === filter), [items, filter])

  const parsed = useMemo(() => (form.kind === 'explicit' && form.paste.trim() ? parseReleaseTable(form.paste) : null), [form.kind, form.paste])

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
  const warehouseName = (c: SupplyContract) => c.warehouse_name ?? (c.warehouse_id ? c.warehouse_id : t('contracts.warehouse_all'))

  function terms(): SupplyContractTerms {
    const explicit = form.kind === 'explicit'
    return {
      customer: form.customer.trim(),
      reference: form.reference.trim() || null,
      lines: form.lines.filter(l => l.sku.trim()).map(l => ({
        sku: l.sku.trim(), total_quantity: num(l.total), unit_price: num(l.price),
      })),
      period_start: form.start, period_end: form.end, schedule_kind: form.kind,
      releases: explicit ? (parsed?.rows ?? []) as SupplyContractReleaseInput[] : null,
      tolerance_pct: num(form.tolerance) ?? 0,
      warehouse_id: form.warehouse || null,
      on_top_of_base: !form.inHistory,
      note: form.note.trim() || null,
      notice_days: num(form.noticeDays),
      auto_renew: form.autoRenew,
      renewal_lead_days: parseLeadDays(form.leadDays) ?? null,
    }
  }

  const linesOk = form.lines.some(l => l.sku.trim()) && form.lines.every(l => {
    if (!l.sku.trim()) return true
    const total = num(l.total)
    const price = num(l.price)
    const totalOk = form.kind === 'explicit' ? (total === null || (Number.isFinite(total) && total > 0)) : (total !== null && Number.isFinite(total) && total > 0)
    return totalOk && (price === null || (Number.isFinite(price) && price >= 0))
  })
  const tol = num(form.tolerance)
  const noticeN = num(form.noticeDays)
  const renewalOk = (noticeN === null || (Number.isInteger(noticeN) && noticeN >= 0 && noticeN <= 730))
    && parseLeadDays(form.leadDays) !== undefined
  const scheduleOk = form.kind !== 'explicit' || (!!parsed && !parsed.fatal && parsed.problems.length === 0 && parsed.rows.length > 0)
  const valid = form.customer.trim() !== '' && form.start !== '' && form.end !== '' && form.end >= form.start
    && linesOk && scheduleOk && renewalOk && (tol === null || (Number.isFinite(tol) && tol >= 0 && tol <= 100))

  function openForm(target: SupplyContract | 'new') {
    setEditing(target); setError(null); setNotice(null); setRowErrors([]); setBadRows(0); setPreview(null)
    setForm(target === 'new' ? emptyForm() : fromContract(target))
  }

  function handleSaveError(e: unknown) {
    if (isApiError(e) && e.code === 'supply_contract_releases_invalid') {
      const list = Array.isArray(e.params?.errors) ? (e.params.errors as RowError[]) : []
      setRowErrors(list); setBadRows(Number(e.params?.bad_rows ?? list.length))
    } else {
      setError(errorDetail(e))
    }
  }

  async function runPreview() {
    setBusy(true); setError(null); setRowErrors([]); setBadRows(0)
    try {
      const r = await previewSupplyContract(terms())
      setPreview(r.releases)
    } catch (e: unknown) {
      setPreview(null); handleSaveError(e)
    } finally { setBusy(false) }
  }

  async function save(status: 'draft' | 'active') {
    setBusy(true); setError(null); setNotice(null); setRowErrors([]); setBadRows(0)
    try {
      const saved = editing && editing !== 'new'
        ? await reviseSupplyContract(editing.root_id, terms(), editing.revision)
        : await createSupplyContract(terms(), status)
      setEditing(null)
      setNotice(saved.status === 'active'
        ? t('contracts.saved_active', { n: saved.materialised ?? 0, withdrawn: saved.withdrawn ?? 0 })
        : t('contracts.saved_draft'))
      setFilter(saved.status === 'draft' ? 'draft' : 'active')
      load(); onChanged?.()
    } catch (e: unknown) {
      handleSaveError(e)
    } finally { setBusy(false) }
  }

  async function changeStatus(c: SupplyContract, next: 'active' | 'closed' | 'cancelled') {
    if (next !== 'active') {
      const ok = await confirm({
        title: t(`contracts.${next}_confirm_title`),
        message: t(`contracts.${next}_confirm_message`, { customer: c.customer, remaining: fmt(c.progress.remaining) }),
        confirmLabel: t(`contracts.${next}_confirm_label`),
        cancelLabel: t('contracts.confirm_keep'),
        danger: next === 'cancelled',
      })
      if (!ok) return
    }
    setBusy(true); setError(null); setNotice(null)
    try {
      const saved = await setSupplyContractStatus(c.root_id, next, c.revision)
      setNotice(t(`contracts.status_done_${next}`, { n: saved.materialised ?? 0, withdrawn: saved.withdrawn ?? 0 }))
      load(); onChanged?.()
    } catch (e: unknown) {
      setError(errorDetail(e))
    } finally { setBusy(false) }
  }

  const rowReason = (e: RowError) =>
    translateErrorParts({ status: 422, code: e.code, params: e.params ?? {}, fieldErrors: [] }, lang)
    || t('contracts.row_invalid')

  const toggle = (id: string) => setExpanded(s => {
    const n = new Set(s)
    if (n.has(id)) n.delete(id); else n.add(id)
    return n
  })

  const setLine = (i: number, patch: Partial<LineForm>) =>
    setForm(f => ({ ...f, lines: f.lines.map((l, j) => (j === i ? { ...l, ...patch } : l)) }))

  const problemList = (title: string, list: { row: number; text: string }[]) => (
    <div role="alert" style={{ fontSize: 12, color: C.red }}>
      <div>{title}</div>
      <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
        {list.slice(0, 20).map((p, i) => <li key={i}>{t('contracts.row_error', { row: p.row, reason: p.text })}</li>)}
      </ul>
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: narrow ? 0 : 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <FileSignature size={15} color="var(--accent)" aria-hidden="true" />
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: C.text }}>{t('contracts.title')}</h3>
      </div>
      <p style={{ margin: 0, fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('contracts.intro')}</p>

      <ContractRenewalsPanel reloadToken={reloadToken} onChanged={() => { load(); onChanged?.() }} />

      {notice && <p role="status" style={{ margin: 0, fontSize: 12.5, color: C.text }}>{notice}</p>}
      {error && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{error}</p>}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', justifyContent: 'space-between' }}>
        <div role="tablist" aria-label={t('contracts.filter_aria')} style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {(['active', 'draft', 'ended'] as Filter[]).map(f => (
            <button key={f} type="button" role="tab" aria-selected={filter === f} onClick={() => setFilter(f)}
              style={{ ...btn(filter === f), fontWeight: filter === f ? 700 : 500 }}>
              {t(`contracts.filter_${f}`)}
            </button>
          ))}
        </div>
        {canWrite && (
          <button type="button" style={btn()} onClick={() => (editing ? setEditing(null) : openForm('new'))}>
            <Plus size={12} aria-hidden="true" /> {t('contracts.add')}
          </button>
        )}
      </div>

      {canWrite && editing && (
        <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            {editing === 'new' ? t('contracts.form_new') : t('contracts.form_revise', { revision: editing.revision + 1 })}
          </div>
          <div style={{ display: 'grid', gap: 8, gridTemplateColumns: narrow ? '1fr' : 'repeat(auto-fit, minmax(170px, 1fr))' }}>
            <label style={lbl}>{t('contracts.field_customer')}
              <input style={field} type="text" maxLength={200} value={form.customer}
                onChange={e => setForm(f => ({ ...f, customer: e.target.value }))} />
            </label>
            <label style={lbl}>{t('contracts.field_reference')}
              <input style={field} type="text" maxLength={100} value={form.reference}
                onChange={e => setForm(f => ({ ...f, reference: e.target.value }))} />
            </label>
            <label style={lbl}>{t('contracts.field_warehouse')}
              <select style={field} value={form.warehouse} onChange={e => setForm(f => ({ ...f, warehouse: e.target.value }))}>
                <option value="">{t('contracts.warehouse_all')}</option>
                {warehouses.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            </label>
            <label style={lbl}>{t('contracts.field_start')}
              <input style={field} type="date" value={form.start} onChange={e => setForm(f => ({ ...f, start: e.target.value }))} />
            </label>
            <label style={lbl}>{t('contracts.field_end')}
              <input style={field} type="date" value={form.end} onChange={e => setForm(f => ({ ...f, end: e.target.value }))} />
            </label>
            <label style={lbl}>{t('contracts.field_schedule')}
              <select style={field} value={form.kind}
                onChange={e => { setPreview(null); setForm(f => ({ ...f, kind: e.target.value as SupplyContractScheduleKind })) }}>
                {(['monthly', 'weekly', 'explicit'] as SupplyContractScheduleKind[]).map(k => (
                  <option key={k} value={k}>{t(`contracts.schedule_${k}`)}</option>
                ))}
              </select>
            </label>
            <label style={lbl}>{t('contracts.field_tolerance')}
              <input style={field} type="number" inputMode="decimal" min={0} max={100} value={form.tolerance}
                onChange={e => setForm(f => ({ ...f, tolerance: e.target.value }))} />
            </label>
            <label style={lbl}>{t('contracts.field_note')}
              <input style={field} type="text" maxLength={300} value={form.note}
                onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
            </label>
            <label style={lbl}>{t('contracts.field_notice_days')}
              <input style={field} type="number" inputMode="numeric" min={0} max={730} value={form.noticeDays}
                onChange={e => setForm(f => ({ ...f, noticeDays: e.target.value }))} />
            </label>
            <label style={lbl}>{t('contracts.field_lead_days')}
              <input style={field} type="text" inputMode="numeric" value={form.leadDays} placeholder="60, 30, 7"
                onChange={e => setForm(f => ({ ...f, leadDays: e.target.value }))} />
            </label>
          </div>
          <label style={{ ...lbl, display: 'flex', alignItems: 'center', gap: 8, minHeight: narrow ? 44 : undefined }}>
            <input type="checkbox" checked={form.autoRenew} onChange={e => setForm(f => ({ ...f, autoRenew: e.target.checked }))} />
            <span>{t('contracts.field_auto_renew')}</span>
          </label>
          <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('contracts.renewal_hint')}</p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={lbl}>{t('contracts.lines_title')}</div>
            {form.lines.map((l, i) => (
              <div key={i} style={{ display: 'grid', gap: 6, alignItems: 'end', gridTemplateColumns: narrow ? '1fr 1fr' : '2fr 1fr 1fr auto' }}>
                <label style={{ ...lbl, gridColumn: narrow ? '1 / -1' : undefined }}>{t('contracts.field_sku')}
                  <input style={field} type="text" maxLength={200} value={l.sku} onChange={e => setLine(i, { sku: e.target.value })} />
                </label>
                <label style={lbl}>{form.kind === 'explicit' ? t('contracts.field_total_optional') : t('contracts.field_total')}
                  <input style={field} type="number" inputMode="decimal" min={0} value={l.total} onChange={e => setLine(i, { total: e.target.value })} />
                </label>
                <label style={lbl}>{t('contracts.field_price')}
                  <input style={field} type="number" inputMode="decimal" min={0} value={l.price} onChange={e => setLine(i, { price: e.target.value })} />
                </label>
                {form.lines.length > 1 && (
                  <button type="button" aria-label={t('contracts.remove_line')} title={t('contracts.remove_line')}
                    style={{ ...btn(), border: 'none', color: C.muted }}
                    onClick={() => setForm(f => ({ ...f, lines: f.lines.filter((_, j) => j !== i) }))}>
                    <Trash2 size={13} aria-hidden="true" />
                  </button>
                )}
              </div>
            ))}
            <div>
              <button type="button" style={{ ...btn(), border: 'none', color: C.muted }}
                onClick={() => setForm(f => ({ ...f, lines: [...f.lines, { sku: '', total: '', price: '' }] }))}>
                <Plus size={12} aria-hidden="true" /> {t('contracts.add_line')}
              </button>
            </div>
          </div>

          {form.kind === 'explicit' && (
            <label style={lbl}>{t('contracts.paste_label')}
              <textarea style={{ ...field, minHeight: 110, fontFamily: 'var(--font-mono, monospace)' }} value={form.paste}
                placeholder={t('contracts.paste_placeholder')}
                onChange={e => { setPreview(null); setForm(f => ({ ...f, paste: e.target.value })) }} />
              <span style={{ display: 'block', marginTop: 4, fontSize: 11, color: C.dim }}>{t('contracts.paste_hint')}</span>
            </label>
          )}
          {parsed?.fatal && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{t(parsed.fatal)}</p>}
          {parsed && !parsed.fatal && parsed.problems.length === 0 && (
            <p style={{ margin: 0, fontSize: 12, color: C.dim }}>{t('contracts.paste_ok', { n: parsed.rows.length })}</p>
          )}
          {parsed && parsed.problems.length > 0 && problemList(
            t('contracts.paste_problems', { n: parsed.problems.length }),
            parsed.problems.map((p: ScheduleProblem) => ({ row: p.row, text: t(p.reasonKey) })))}
          {rowErrors.length > 0 && problemList(
            t('contracts.server_rejected', { n: badRows }),
            rowErrors.map(e => ({ row: e.row, text: rowReason(e) })))}

          <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('contracts.tolerance_hint')}</p>
          <label style={{ ...lbl, display: 'flex', alignItems: 'center', gap: 8, minHeight: narrow ? 44 : undefined }}>
            <input type="checkbox" checked={form.inHistory} onChange={e => setForm(f => ({ ...f, inHistory: e.target.checked }))} />
            <span>{t('contracts.in_history')}</span>
          </label>

          {preview && (
            <div style={{ fontSize: 12, color: C.text }}>
              <div style={{ color: C.muted, marginBottom: 4 }}>
                {t('contracts.preview_summary', { n: preview.length, units: fmt(preview.reduce((s, r) => s + r.quantity, 0)) })}
              </div>
              <div style={{ maxHeight: 180, overflowY: 'auto', border: `1px solid ${C.border}`, borderRadius: 6 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <tbody>
                    {preview.map((r, i) => (
                      <tr key={i} style={{ borderTop: i ? `1px solid ${C.border}` : undefined }}>
                        <td style={{ padding: '4px 8px' }}>{r.date}</td>
                        <td style={{ padding: '4px 8px', color: C.muted, overflowWrap: 'anywhere' }}>{r.sku}</td>
                        <td style={{ padding: '4px 8px', textAlign: 'right' }}>{fmt(r.quantity)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" disabled={busy || !valid} onClick={runPreview} style={btn(false, busy || !valid)}>
              {t('contracts.preview')}
            </button>
            {editing === 'new' ? (
              <>
                <button type="button" disabled={busy || !valid} onClick={() => save('active')} style={btn(true, busy || !valid)}>
                  {busy ? t('common.saving') : t('contracts.save_active')}
                </button>
                <button type="button" disabled={busy || !valid} onClick={() => save('draft')} style={btn(false, busy || !valid)}>
                  {t('contracts.save_draft')}
                </button>
              </>
            ) : (
              <button type="button" disabled={busy || !valid} onClick={() => save('active')} style={btn(true, busy || !valid)}>
                {busy ? t('common.saving') : t('contracts.save_revision')}
              </button>
            )}
            <button type="button" onClick={() => setEditing(null)} style={{ ...btn(), border: 'none', color: C.muted }}>
              {t('common.cancel')}
            </button>
          </div>
          {editing !== 'new' && editing.status === 'active' && (
            <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('contracts.revise_hint')}</p>
          )}
        </div>
      )}

      {loadError && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{loadError}</p>}
      {loaded && !loadError && shown.length === 0 && (
        <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t(`contracts.empty_${filter}`)}</p>
      )}

      {shown.length > 0 && (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {shown.map(c => {
            const p = c.progress
            const pct = p.scheduled_total > 0 ? Math.min(100, (p.delivered / p.scheduled_total) * 100) : 0
            const duePct = p.scheduled_total > 0 ? Math.min(100, (p.due_to_date / p.scheduled_total) * 100) : 0
            const open = expanded.has(c.root_id)
            return (
              <li key={c.root_id} style={{ ...card, display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                  <div style={{ minWidth: 0, flex: '1 1 260px', overflowWrap: 'anywhere' }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: C.text, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                      <span>{c.customer}</span>
                      {c.reference && <span style={{ fontWeight: 500, color: C.muted }}>· {c.reference}</span>}
                      <span style={chip(c.status === 'active' ? C.green : c.status === 'cancelled' ? C.red : C.muted)}>
                        {t(`contracts.status_${c.status}`)}
                      </span>
                      {p.behind_schedule && <span style={chip(C.red)}>{t('contracts.behind')}</span>}
                      {p.overdue_count > 0 && (
                        <span style={chip(C.amber)}>{t('contracts.overdue_chip', { n: p.overdue_count, units: fmt(p.overdue_units) })}</span>
                      )}
                      {p.unmaterialised_due > 0 && (
                        <span style={chip(C.amber)} title={t('contracts.unmaterialised_hint')}>
                          {t('contracts.unmaterialised_chip', { n: p.unmaterialised_due })}
                        </span>
                      )}
                      {c.status === 'active' && c.period_ended && <span style={chip(C.amber)}>{t('contracts.period_ended')}</span>}
                      {c.auto_renew && <span style={chip(C.green)}>{t('renewals.auto_renew_chip')}</span>}
                      {c.renewed_from_root_id && <span style={chip(C.muted)}>{t('contracts.renewal_of')}</span>}
                    </div>
                    <div style={{ fontSize: 12, color: C.dim, marginTop: 2 }}>
                      {t('contracts.period', { start: c.period_start, end: c.period_end })} · {t(`contracts.schedule_${c.schedule_kind}`)}
                      {' · '}{warehouseName(c)}
                      {c.tolerance_pct > 0 && <> · {t('contracts.tolerance', { pct: fmt(c.tolerance_pct) })}</>}
                      {!c.on_top_of_base && <> · {t('contracts.in_history_badge')}</>}
                    </div>
                    <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>
                      {c.lines.map(l => `${l.sku}: ${fmt(l.total_quantity ?? 0)}`).join(' · ')}
                    </div>
                  </div>
                  {canWrite && (c.status === 'draft' || c.status === 'active') && (
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      <button type="button" disabled={busy} style={btn(false, busy)} onClick={() => openForm(c)}>
                        {t('contracts.revise')}
                      </button>
                      {c.status === 'draft' && (
                        <button type="button" disabled={busy} style={btn(true, busy)} onClick={() => changeStatus(c, 'active')}>
                          {t('contracts.activate')}
                        </button>
                      )}
                      {c.status === 'active' && (
                        <button type="button" disabled={busy} style={btn(false, busy)} onClick={() => changeStatus(c, 'closed')}>
                          {t('contracts.close')}
                        </button>
                      )}
                      <button type="button" disabled={busy} style={{ ...btn(false, busy), color: C.red }} onClick={() => changeStatus(c, 'cancelled')}>
                        {t('contracts.cancel')}
                      </button>
                    </div>
                  )}
                </div>

                <div>
                  <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}
                    aria-label={t('contracts.progress_aria', { customer: c.customer })}
                    style={{ position: 'relative', height: 8, borderRadius: 4, background: 'var(--surface-2)', border: `1px solid ${C.border}`, overflow: 'hidden' }}>
                    <div style={{ position: 'absolute', inset: 0, width: `${pct}%`, background: p.behind_schedule ? C.red : 'var(--accent)' }} />
                    {c.status === 'active' && duePct > 0 && (
                      <div title={t('contracts.due_marker')} style={{ position: 'absolute', top: 0, bottom: 0, left: `calc(${duePct}% - 1px)`, width: 2, background: C.text, opacity: 0.6 }} />
                    )}
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 14px', fontSize: 12, color: C.dim, marginTop: 4 }}>
                    <span>{t('contracts.delivered_of', { delivered: fmt(p.delivered), total: fmt(p.scheduled_total), pct: p.progress_pct ?? 0 })}</span>
                    {c.status === 'active' && <span>{t('contracts.due_to_date', { units: fmt(p.due_to_date) })}</span>}
                    <span>{t('contracts.remaining', { units: fmt(p.remaining) })}</span>
                    {p.projected_shortfall != null && p.projected_shortfall > 0 && (
                      <span style={{ color: p.shortfall_beyond_tolerance ? C.red : C.dim, fontWeight: p.shortfall_beyond_tolerance ? 600 : 400 }}>
                        {c.status === 'active'
                          ? t('contracts.projected_shortfall', { units: fmt(p.projected_shortfall) })
                          : t('contracts.final_shortfall', { units: fmt(p.projected_shortfall) })}
                      </span>
                    )}
                    {c.status === 'active' && p.projected_shortfall == null && (
                      <span>{t('contracts.no_projection')}</span>
                    )}
                    {p.next_release && (
                      <span>{t('contracts.next_release', { date: p.next_release.date, units: fmt(p.next_release.quantity), sku: p.next_release.sku })}</span>
                    )}
                  </div>
                </div>

                <div>
                  <button type="button" onClick={() => toggle(c.root_id)} aria-expanded={open}
                    style={{ ...btn(), border: 'none', padding: 0, color: C.muted, fontWeight: 500 }}>
                    {open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
                    {t('contracts.show_releases', { n: p.releases.length })}
                  </button>
                  {open && (
                    <div style={{ marginTop: 6, maxHeight: 260, overflowY: 'auto', border: `1px solid ${C.border}`, borderRadius: 6 }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                        <thead>
                          <tr style={{ color: C.muted, textAlign: 'left' }}>
                            <th style={{ padding: '4px 8px', fontWeight: 600 }}>{t('contracts.col_date')}</th>
                            <th style={{ padding: '4px 8px', fontWeight: 600 }}>{t('contracts.col_sku')}</th>
                            <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }}>{t('contracts.col_quantity')}</th>
                            <th style={{ padding: '4px 8px', fontWeight: 600 }}>{t('contracts.col_state')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {p.releases.map((r, i) => (
                            <tr key={i} style={{ borderTop: `1px solid ${C.border}`, color: r.overdue ? C.amber : C.text }}>
                              <td style={{ padding: '4px 8px' }}>{r.date}</td>
                              <td style={{ padding: '4px 8px', overflowWrap: 'anywhere' }}>{r.sku}</td>
                              <td style={{ padding: '4px 8px', textAlign: 'right' }}>{fmt(r.quantity)}</td>
                              <td style={{ padding: '4px 8px', fontWeight: r.overdue ? 700 : 400 }}>
                                {r.overdue ? t('contracts.state_overdue') : t(`contracts.state_${r.state}`)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
                <div style={{ fontSize: 11, color: C.dim }}>
                  {t('contracts.revision_by', { revision: c.revision, who: c.created_by_name || t('contracts.someone'), date: c.created_at.slice(0, 10) })}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
