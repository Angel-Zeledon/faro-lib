'use client'
/**
 * Purchase budget: a cap on what gets ordered in a period, and what to order
 * first when the recommendations cost more than what is left.
 *
 * Everything shown comes from the server (`/inventory/budget/status` and
 * `/inventory/budget/plan`): the burn, the projection and the funded / deferred
 * split are computed there. The plan only ANNOTATES: it creates no order and
 * changes no recommended quantity. With no budget set the panel is a single
 * quiet line (hidden for a viewer), and nothing on the screen changes.
 *
 * Writing is for analysts and admins (the server enforces it). A user limited to
 * some warehouses sees and edits only budgets scoped to those warehouses.
 * One component serves desktop and phone: it changes touch-target sizes only.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Wallet, Plus, Pencil, ChevronDown, ChevronRight, AlertTriangle } from 'lucide-react'
import {
  createBudget, getBudgetPlan, getBudgetStatus, listBudgets, listCostCenters, listSuppliers, reviseBudget,
} from '@/lib/api'
import type {
  BudgetInput, BudgetLineStatus, BudgetPeriodType, BudgetPlan, BudgetPlanLine, BudgetScopeType,
  BudgetStatus, BudgetWarning, CostCenter, PurchaseBudget, Supplier,
} from '@/lib/types'
import { useErrorDetail } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { useWarehouses } from '@/components/inventory/WarehouseControls'
import { formatMoney } from '@/lib/currency'
import { getUser } from '@/lib/auth'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D', amber: '#B7791F', green: '#2E8B62' }

/** What a purchase screen shows next to a recommended line. */
export interface BudgetNote { status: BudgetLineStatus; reason: string | null }

const WORST: Record<BudgetLineStatus, number> = { deferred: 4, partial: 3, cost_unknown: 2, funded: 1, ignored: 0 }

/** One note per SKU (a SKU can have a line per warehouse): the least funded wins. */
export function notesBySku(plan: BudgetPlan | null): Record<string, BudgetNote> {
  const out: Record<string, BudgetNote> = {}
  if (!plan || !plan.budget) return out
  for (const ln of plan.lines) {
    if (ln.status === 'ignored') continue
    const cur = out[ln.sku]
    if (!cur || WORST[ln.status] > WORST[cur.status]) out[ln.sku] = { status: ln.status, reason: ln.reason }
  }
  return out
}

export function BudgetChip({ note }: { note: BudgetNote }) {
  const { t } = useLanguage()
  const color = note.status === 'funded' ? C.green : note.status === 'cost_unknown' ? C.muted : C.amber
  const label = t(`budget.chip_${note.status}`)
  const why = note.reason ? t(`budget.reason_${note.reason}`) : ''
  return (
    <span title={why || undefined} style={{ fontSize: 12, color, display: 'inline-flex', alignItems: 'center', gap: 5 }}>
      <Wallet size={12} aria-hidden="true" />
      {label}{why && note.status !== 'funded' ? ` · ${why}` : ''}
    </span>
  )
}

const iso = (d: Date) => d.toISOString().slice(0, 10)

interface FormState {
  period_type: BudgetPeriodType; period_start: string; period_end: string; amount: string
  scope_type: BudgetScopeType; scope_value: string; parent: string
  hard_cap: boolean; active: boolean; note: string
}

const EMPTY_FORM = (): FormState => ({
  period_type: 'month', period_start: iso(new Date()), period_end: iso(new Date()), amount: '',
  scope_type: 'company', scope_value: '', parent: '', hard_cap: false, active: true, note: '',
})

export default function BudgetPanel({ sessionId, onNotes, reloadToken, onChanged }: {
  sessionId?: string | null
  /** Called with the per-SKU annotation each time the plan is (re)loaded. */
  onNotes?: (notes: Record<string, BudgetNote>) => void
  reloadToken?: number
  onChanged?: () => void
}) {
  const { t, lang } = useLanguage()
  const errorDetail = useErrorDetail()
  const narrow = useIsNarrow()
  const { warehouses } = useWarehouses()
  const role = getUser()?.role
  const canWrite = role === 'admin' || role === 'analyst'

  const [status, setStatus] = useState<BudgetStatus | null>(null)
  const [plan, setPlan] = useState<BudgetPlan | null>(null)
  const [planBusy, setPlanBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | undefined>(undefined)
  const [showLines, setShowLines] = useState(false)

  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<PurchaseBudget | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [scopeKind, setScopeKind] = useState<'company' | 'warehouses'>('company')
  const [suppliers, setSuppliers] = useState<Supplier[]>([])
  const [costCenters, setCostCenters] = useState<CostCenter[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const st = await getBudgetStatus(selected, { silent: true })
      setStatus(st)
      if (!st.budget) { setPlan(null); onNotes?.({}); return }
      setPlanBusy(true)
      try {
        const p = await getBudgetPlan(st.budget.root_id, sessionId ?? undefined, { silent: true })
        setPlan(p)
        onNotes?.(notesBySku(p))
      } catch (e) {
        // The status is still true; only the funded / deferred split is missing.
        setPlan(null); onNotes?.({})
        setLoadError(errorDetail(e))
      } finally { setPlanBusy(false) }
    } catch (e) {
      setStatus(null); setPlan(null); onNotes?.({})
      setLoadError(errorDetail(e))
    } finally { setLoaded(true) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, sessionId])

  useEffect(() => { load() }, [load, reloadToken])

  const money = (n: number | null | undefined) => formatMoney(n)
  const fmtDate = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString(lang === 'es' ? 'es' : 'en', { day: 'numeric', month: 'short' })

  const field: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box', fontSize: narrow ? 16 : 12.5, padding: narrow ? '10px 10px' : '6px 8px',
    borderRadius: narrow ? 10 : 7, border: `1px solid ${C.border}`, background: 'var(--surface-2)', color: C.text,
    minHeight: narrow ? 44 : 32,
  }
  const btn = (disabled = false, solid = false): React.CSSProperties => ({
    all: 'unset', cursor: disabled ? 'default' : 'pointer', boxSizing: 'border-box', display: 'inline-flex',
    alignItems: 'center', justifyContent: 'center', gap: 5, padding: narrow ? '0 14px' : '5px 12px', minHeight: narrow ? 44 : undefined,
    borderRadius: narrow ? 10 : 7, fontSize: narrow ? 14 : 12, fontWeight: 600, opacity: disabled ? 0.5 : 1,
    border: `1px solid ${C.border}`, color: C.text,
    ...(solid ? { background: 'var(--accent)', borderColor: 'var(--accent)', color: '#fff' } : {}),
  })
  const lbl: React.CSSProperties = { fontSize: narrow ? 13 : 11.5, color: C.muted }

  async function openForm(b: PurchaseBudget | null) {
    setError(null); setEditing(b)
    setForm(b ? {
      period_type: b.period_type, period_start: b.period_start, period_end: b.period_end,
      amount: String(b.amount), scope_type: b.scope_type, scope_value: b.scope_value ?? '',
      parent: b.parent_root_id ?? '', hard_cap: b.hard_cap, active: b.active, note: b.note ?? '',
    } : EMPTY_FORM())
    setFormOpen(true)
    try {
      const l = await listBudgets()
      setScopeKind(l.scope)
      if (l.scope === 'warehouses' && !b) setForm(f => ({ ...f, scope_type: 'warehouse' }))
    } catch { /* the form still works; the server decides */ }
    listSuppliers().then(setSuppliers).catch(() => {})
    // Served by the Rust API: when it is down there are simply no centers to pick.
    listCostCenters({ silent: true }).then(r => setCostCenters(r.items.filter(c => c.active))).catch(() => {})
  }

  const amountNum = Number(form.amount)
  const needsValue = form.scope_type !== 'company'
  const valid = form.amount.trim() !== '' && Number.isFinite(amountNum) && amountNum >= 0
    && (!needsValue || form.scope_value.trim() !== '')
    && (form.period_type !== 'custom' || form.period_end >= form.period_start)

  async function save() {
    setBusy(true); setError(null)
    const body: BudgetInput = {
      period_type: form.period_type, period_start: form.period_start,
      period_end: form.period_type === 'custom' ? form.period_end : null,
      amount: amountNum, scope_type: form.scope_type,
      scope_value: needsValue ? form.scope_value.trim() : null,
      parent_root_id: form.parent || null, hard_cap: form.hard_cap, active: form.active,
      note: form.note.trim() || null,
    }
    try {
      let rootId: string
      if (editing) {
        const row = await reviseBudget(editing.root_id, editing.revision, body)
        rootId = row.root_id
      } else {
        const row = await createBudget(body)
        rootId = row.root_id
      }
      setFormOpen(false); setEditing(null)
      setSelected(rootId)
      await load()
      onChanged?.()
    } catch (e) {
      setError(errorDetail(e))
    } finally { setBusy(false) }
  }

  const budget = status?.budget ?? null
  const usage = status?.usage ?? null
  const warnings: BudgetWarning[] = useMemo(
    () => [...(plan?.warnings ?? status?.warnings ?? [])].filter(w => w.code !== 'no_budget'), [plan, status])

  if (!loaded && !status) return null
  // A viewer with nothing set up has nothing to read and nothing to do.
  if (loaded && !budget && !canWrite && !loadError) return null

  const burn = usage?.burn
  const ordered = usage?.ordered ?? 0
  const pctOf = (n: number) => (budget && budget.amount > 0 ? Math.min(100, (n / budget.amount) * 100) : 0)
  const paceColor = burn?.pace === 'over' ? C.red : burn?.pace === 'ahead' ? C.amber : C.green
  const scopeName = budget
    ? (budget.scope_type === 'company' ? t('budget.scope_company') : `${t(`budget.scope_${budget.scope_type}`)}: ${budget.scope_label ?? ''}`)
    : ''

  return (
    <section aria-label={t('budget.title')} style={{
      display: 'flex', flexDirection: 'column', gap: 12, marginBottom: narrow ? 14 : 24,
      ...(narrow ? {} : { padding: '16px 20px', border: `1px solid ${C.border}`, borderRadius: 12, background: 'var(--surface)' }),
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Wallet size={15} color={C.muted} aria-hidden="true" />
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: C.text, flex: 1 }}>{t('budget.title')}</h2>
        {canWrite && (
          <>
            {budget && (
              <button type="button" style={btn()} onClick={() => openForm(budget)}>
                <Pencil size={12} aria-hidden="true" /> {t('budget.edit')}
              </button>
            )}
            <button type="button" style={btn(false, !budget)} onClick={() => openForm(null)}>
              <Plus size={12} aria-hidden="true" /> {t('budget.new')}
            </button>
          </>
        )}
      </div>

      {status && status.budgets.length > 1 && (
        <label style={lbl}>{t('budget.pick')}
          <select style={field} value={budget?.root_id ?? ''} onChange={e => setSelected(e.target.value || undefined)}>
            {status.budgets.map(b => (
              <option key={b.root_id} value={b.root_id}>
                {(b.scope_type === 'company' ? t('budget.scope_company') : (b.scope_label ?? t(`budget.scope_${b.scope_type}`)))}
                {' · '}{fmtDate(b.period_start)} – {fmtDate(b.period_end)}{' · '}{money(b.amount)}
              </option>
            ))}
          </select>
        </label>
      )}

      {loadError && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{loadError}</p>}

      {!budget && loaded && !loadError && (
        <p style={{ margin: 0, fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('budget.empty')}</p>
      )}

      {budget && usage && burn && (
        <>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', alignItems: 'baseline', fontSize: 12.5, color: C.muted }}>
            <span style={{ fontWeight: 600, color: C.text }}>{scopeName}</span>
            <span>{fmtDate(budget.period_start)} – {fmtDate(budget.period_end)}</span>
            {budget.hard_cap && <span style={{ color: C.amber, fontWeight: 600 }}>{t('budget.hard_cap')}</span>}
            {!budget.active && <span>{t('budget.inactive')}</span>}
          </div>

          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 6, fontVariantNumeric: 'tabular-nums' }}>
              <span style={{ color: C.text, fontWeight: 600 }}>{t('budget.remaining_of', { remaining: money(usage.remaining), amount: money(budget.amount) })}</span>
              <span style={{ color: C.muted }}>{t('budget.days_left', { n: burn.days_left })}</span>
            </div>
            {/* Money used (solid: received, soft: on its way) against the calendar. The
                vertical mark is where the period itself stands today. */}
            <div role="img" aria-label={t('budget.bar_aria', {
              used: Math.round(pctOf(ordered)), elapsed: Math.round(burn.elapsed_fraction * 100),
            })} style={{ position: 'relative', height: 10, borderRadius: 6, background: 'var(--surface-2)', border: `1px solid ${C.border}`, overflow: 'visible' }}>
              <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${pctOf(usage.spent)}%`, background: paceColor, borderRadius: '6px 0 0 6px' }} />
              <div style={{ position: 'absolute', left: `${pctOf(usage.spent)}%`, top: 0, bottom: 0, width: `${Math.max(0, pctOf(ordered) - pctOf(usage.spent))}%`, background: `color-mix(in srgb, ${paceColor} 45%, transparent)` }} />
              <div style={{ position: 'absolute', left: `${burn.elapsed_fraction * 100}%`, top: -4, bottom: -4, width: 2, background: C.text, borderRadius: 1 }} />
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 14px', marginTop: 8, fontSize: 12, color: C.muted, fontVariantNumeric: 'tabular-nums' }}>
              <span>{t('budget.spent')}: <b style={{ color: C.text }}>{money(usage.spent)}</b></span>
              <span>{t('budget.committed')}: <b style={{ color: C.text }}>{money(usage.committed)}</b></span>
              {usage.limited_by === 'parent' && (
                <span style={{ color: C.amber }}>{t('budget.limited_by_parent', { free: money(usage.free) })}</span>
              )}
            </div>
            {burn.projected_overrun != null && burn.projected_overrun > 0 && (
              <p role="status" style={{ margin: '8px 0 0', fontSize: 12.5, color: C.amber, display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                <AlertTriangle size={13} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
                {t('budget.projected_overrun', { projected: money(burn.projected_total), over: money(burn.projected_overrun) })}
              </p>
            )}
            {!burn.projection_reliable && burn.state === 'running' && (
              <p style={{ margin: '6px 0 0', fontSize: 12, color: C.dim }}>{t('budget.projection_early')}</p>
            )}
          </div>

          {warnings.map(w => (
            <p key={w.code} role="status" style={{ margin: 0, fontSize: 12.5, color: C.muted, lineHeight: 1.5 }}>
              {t(`budget.warn_${w.code}`, w.params as Record<string, string | number>)}
            </p>
          ))}

          <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: 10 }}>
            {planBusy && <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('budget.plan_loading')}</p>}
            {!planBusy && plan?.summary && (
              <>
                <p style={{ margin: 0, fontSize: 13, color: C.text, lineHeight: 1.55 }}>
                  {plan.summary.deferred_lines + plan.summary.partial_lines === 0
                    ? t('budget.plan_all_fit', { cost: money(plan.summary.funded_cost) })
                    : t('budget.plan_split', {
                        funded: plan.summary.funded_lines, partial: plan.summary.partial_lines,
                        deferred: plan.summary.deferred_lines, cost: money(plan.summary.funded_cost),
                      })}
                </p>
                {plan.summary.money_at_risk_uncovered > 0 && (
                  <p style={{ margin: '4px 0 0', fontSize: 12.5, color: C.amber }}>
                    {t('budget.risk_uncovered', { amount: money(plan.summary.money_at_risk_uncovered) })}
                  </p>
                )}
                <p style={{ margin: '4px 0 0', fontSize: 12, color: C.dim }}>{t('budget.annotate_only')}</p>
                {plan.lines.length > 0 && (
                  <button type="button" style={{ ...btn(), marginTop: 8 }} aria-expanded={showLines} onClick={() => setShowLines(s => !s)}>
                    {showLines ? <ChevronDown size={12} aria-hidden="true" /> : <ChevronRight size={12} aria-hidden="true" />}
                    {t('budget.show_lines', { n: plan.lines.filter(l => l.status !== 'ignored').length })}
                  </button>
                )}
                {showLines && <PlanLines lines={plan.lines} money={money} narrow={narrow} />}
              </>
            )}
          </div>
        </>
      )}

      {formOpen && (
        <form onSubmit={e => { e.preventDefault(); if (valid && !busy) save() }}
          style={{ display: 'grid', gridTemplateColumns: narrow ? '1fr' : 'repeat(2, minmax(0, 1fr))', gap: 10, borderTop: `1px solid ${C.border}`, paddingTop: 12 }}>
          <label style={lbl}>{t('budget.field_period')}
            <select style={field} value={form.period_type} onChange={e => setForm(f => ({ ...f, period_type: e.target.value as BudgetPeriodType }))}>
              <option value="month">{t('budget.period_month')}</option>
              <option value="quarter">{t('budget.period_quarter')}</option>
              <option value="custom">{t('budget.period_custom')}</option>
            </select>
          </label>
          <label style={lbl}>{form.period_type === 'custom' ? t('budget.field_from') : t('budget.field_any_day')}
            <input style={field} type="date" value={form.period_start} onChange={e => setForm(f => ({ ...f, period_start: e.target.value }))} />
          </label>
          {form.period_type === 'custom' && (
            <label style={lbl}>{t('budget.field_to')}
              <input style={field} type="date" value={form.period_end} onChange={e => setForm(f => ({ ...f, period_end: e.target.value }))} />
            </label>
          )}
          <label style={lbl}>{t('budget.field_amount')}
            <input style={field} type="number" inputMode="decimal" min={0} value={form.amount} onChange={e => setForm(f => ({ ...f, amount: e.target.value }))} />
          </label>
          <label style={lbl}>{t('budget.field_scope')}
            <select style={field} value={form.scope_type} disabled={scopeKind === 'warehouses'}
              onChange={e => setForm(f => ({ ...f, scope_type: e.target.value as BudgetScopeType, scope_value: '' }))}>
              {scopeKind === 'company' && <option value="company">{t('budget.scope_company')}</option>}
              <option value="warehouse">{t('budget.scope_warehouse')}</option>
              {scopeKind === 'company' && <option value="supplier">{t('budget.scope_supplier')}</option>}
              {scopeKind === 'company' && <option value="category">{t('budget.scope_category')}</option>}
              {scopeKind === 'company' && (costCenters.length > 0 || form.scope_type === 'cost_center') && (
                <option value="cost_center">{t('budget.scope_cost_center')}</option>
              )}
            </select>
          </label>
          {form.scope_type === 'warehouse' && (
            <label style={lbl}>{t('budget.scope_warehouse')}
              <select style={field} value={form.scope_value} onChange={e => setForm(f => ({ ...f, scope_value: e.target.value }))}>
                <option value="">{t('budget.choose')}</option>
                {warehouses.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            </label>
          )}
          {form.scope_type === 'supplier' && (
            <label style={lbl}>{t('budget.scope_supplier')}
              <select style={field} value={form.scope_value} onChange={e => setForm(f => ({ ...f, scope_value: e.target.value }))}>
                <option value="">{t('budget.choose')}</option>
                {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
          )}
          {form.scope_type === 'cost_center' && (
            <label style={lbl}>{t('budget.scope_cost_center')}
              <select style={field} value={form.scope_value} onChange={e => setForm(f => ({ ...f, scope_value: e.target.value }))}>
                <option value="">{t('budget.choose')}</option>
                {costCenters.map(c => <option key={c.id} value={c.id}>{c.path ?? c.code}</option>)}
              </select>
              <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('budget.scope_cost_center_hint')}</span>
            </label>
          )}
          {form.scope_type === 'category' && (
            <label style={lbl}>{t('budget.scope_category')}
              <input style={field} type="text" maxLength={120} value={form.scope_value} onChange={e => setForm(f => ({ ...f, scope_value: e.target.value }))} />
            </label>
          )}
          {scopeKind === 'company' && (status?.budgets.length ?? 0) > 0 && (
            <label style={lbl}>{t('budget.field_parent')}
              <select style={field} value={form.parent} onChange={e => setForm(f => ({ ...f, parent: e.target.value }))}>
                <option value="">{t('budget.parent_none')}</option>
                {status!.budgets.filter(b => b.root_id !== editing?.root_id).map(b => (
                  <option key={b.root_id} value={b.root_id}>
                    {(b.scope_type === 'company' ? t('budget.scope_company') : (b.scope_label ?? t(`budget.scope_${b.scope_type}`)))}
                    {' · '}{fmtDate(b.period_start)} – {fmtDate(b.period_end)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label style={{ ...lbl, gridColumn: '1 / -1' }}>{t('budget.field_note')}
            <input style={field} type="text" maxLength={500} value={form.note} onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
          </label>
          <label style={{ ...lbl, display: 'flex', alignItems: 'flex-start', gap: 8, gridColumn: '1 / -1', minHeight: narrow ? 44 : undefined }}>
            <input type="checkbox" checked={form.hard_cap} onChange={e => setForm(f => ({ ...f, hard_cap: e.target.checked }))} style={{ marginTop: 3 }} />
            <span>{t('budget.field_hard_cap')}<br /><span style={{ color: C.dim }}>{t('budget.field_hard_cap_help')}</span></span>
          </label>
          {editing && (
            <label style={{ ...lbl, display: 'flex', alignItems: 'center', gap: 8, gridColumn: '1 / -1', minHeight: narrow ? 44 : undefined }}>
              <input type="checkbox" checked={form.active} onChange={e => setForm(f => ({ ...f, active: e.target.checked }))} />
              {t('budget.field_active')}
            </label>
          )}
          {error && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red, gridColumn: '1 / -1' }}>{error}</p>}
          <div style={{ display: 'flex', gap: 8, gridColumn: '1 / -1' }}>
            <button type="submit" style={btn(!valid || busy, true)} disabled={!valid || busy}>{t('budget.save')}</button>
            <button type="button" style={btn()} onClick={() => { setFormOpen(false); setEditing(null) }}>{t('budget.cancel')}</button>
          </div>
        </form>
      )}
    </section>
  )
}

function PlanLines({ lines, money, narrow }: { lines: BudgetPlanLine[]; money: (n: number | null) => string; narrow: boolean }) {
  const { t } = useLanguage()
  const rows = lines.filter(l => l.status !== 'ignored')
  const color = (s: BudgetLineStatus) => (s === 'funded' ? C.green : s === 'cost_unknown' ? C.muted : C.amber)
  return (
    <ul style={{ listStyle: 'none', margin: '10px 0 0', padding: 0, display: 'grid', gap: 1, background: C.border, border: `1px solid ${C.border}`, borderRadius: 10, overflow: 'hidden' }}>
      {rows.map(l => (
        <li key={l.key} style={{ background: 'var(--surface)', padding: narrow ? '10px 12px' : '8px 12px', display: 'grid', gap: 2 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 13 }}>
            <span style={{ color: C.text, fontWeight: 600, minWidth: 0, overflowWrap: 'anywhere' }}>
              {l.display_name || l.sku}{l.warehouse ? ` · ${l.warehouse}` : ''}
            </span>
            <span style={{ color: color(l.status), fontWeight: 600, whiteSpace: 'nowrap' }}>{t(`budget.chip_${l.status}`)}</span>
          </div>
          <div style={{ fontSize: 12, color: C.muted, display: 'flex', flexWrap: 'wrap', gap: '2px 12px', fontVariantNumeric: 'tabular-nums' }}>
            <span>{t('budget.line_qty', { funded: l.funded_qty, qty: l.recommended_qty })}</span>
            <span>{l.full_cost != null ? money(l.full_cost) : t('budget.cost_unknown')}</span>
            {l.money_at_risk != null && <span>{t('budget.line_risk', { amount: money(l.money_at_risk) })}</span>}
            {l.reason && <span style={{ color: color(l.status) }}>{t(`budget.reason_${l.reason}`)}</span>}
          </div>
        </li>
      ))}
    </ul>
  )
}
