'use client'
/**
 * /inventario on a phone.
 *
 * The desktop screen is nine views of wide tables (the stock table alone is 13
 * columns). Squeezed into 360px it measured 514px wide and scrolled sideways,
 * which on a phone means the number the buyer came for is off-screen. Here the
 * same data becomes cards, every per-SKU detail opens in a bottom sheet, the
 * row editor becomes a form, and the stock count is typed into one card per
 * product with the save pinned above the tab bar.
 *
 * Nothing here fetches or decides anything: page.tsx keeps all state, all
 * calls and all the rules (who may edit, what a save sends, the network-total
 * guard). These components only lay that out for a thumb. A Next.js page file
 * cannot export helpers, so the calculation panels that live in page.tsx come
 * in as a slot (`calc`) instead of being imported.
 */
import { useState } from 'react'
import { ChevronDown, Edit2, Trash2, Minus, Plus } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { BottomSheet, MobileList, MobileCard, StatusBadge, signalTone } from '@/components/mobile'
import SharedSignalBadge from '@/components/ui/SignalBadge'
import { SIGNAL_STYLES } from '@/components/ui/SignalBadge'
import { formatMoney, formatMoneyCompact } from '@/lib/currency'
import { coverageUnitShort } from '@/lib/period'
import { incomingText } from '@/lib/incomingCopy'
import { fmtNum } from '@/lib/numberLocale'
import type { InventoryStatusItem, InventorySignal, CoverageUnit, Supplier } from '@/lib/types'

// ── Shared form styles for phone forms ──────────────────────────────────────
/** 44px tall, 16px text: big enough for a thumb, and 16px is what stops iOS
 *  zooming the page when the field takes focus. */
export const mInput: React.CSSProperties = {
  boxSizing: 'border-box', width: '100%', minHeight: 44,
  padding: '10px 12px', borderRadius: 10, fontSize: 16,
  background: 'var(--surface-2)', border: '1px solid var(--border)',
  color: 'var(--text)', outline: 'none', minWidth: 0,
}

export function MField({ label, hint, children, htmlFor }: {
  label: React.ReactNode; hint?: React.ReactNode; children: React.ReactNode; htmlFor?: string
}) {
  return (
    <label htmlFor={htmlFor} style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--muted)' }}>{label}</span>
      {children}
      {hint && <span style={{ fontSize: 12, color: 'var(--dim)', lineHeight: 1.4 }}>{hint}</span>}
    </label>
  )
}

export function signalLabel(t: (k: string) => string, s: InventorySignal | string | null | undefined): string {
  const st = s ? SIGNAL_STYLES[s as InventorySignal] : undefined
  return st ? t(st.labelKey) : '—'
}

// ── KPI / metric grid ────────────────────────────────────────────────────────
export interface MobileMetric {
  label: string
  value: React.ReactNode
  color: string
  sub?: string
  onClick?: () => void
  active?: boolean
}

/** Two columns of compact figures. The desktop row of six measured 514px at
 *  360; two per row is what fits a thumb and still reads at a glance. A metric
 *  that filters the list is a real button with its pressed state announced. */
export function MobileMetricGrid({ metrics, ariaLabel }: { metrics: MobileMetric[]; ariaLabel?: string }) {
  return (
    <div role="group" aria-label={ariaLabel}
      style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
      {metrics.map(m => {
        const inner = (
          <>
            <span style={{ display: 'block', fontSize: 20, fontWeight: 600, color: 'var(--text)', lineHeight: 1.15,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
              {m.value}
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--muted)', marginTop: 3, lineHeight: 1.3 }}>
              <span aria-hidden="true" style={{ width: 7, height: 7, borderRadius: '50%', background: m.color, flexShrink: 0 }} />
              {m.label}
            </span>
            {m.sub && <span style={{ display: 'block', fontSize: 11, color: 'var(--dim)', marginTop: 2, lineHeight: 1.3 }}>{m.sub}</span>}
          </>
        )
        const box: React.CSSProperties = {
          boxSizing: 'border-box', minWidth: 0, minHeight: 64, padding: '10px 12px',
          borderRadius: 12,
          border: `1px solid ${m.active ? 'var(--accent)' : 'var(--border)'}`,
          background: m.active ? 'var(--accent-dim)' : 'var(--surface)',
          textAlign: 'left',
        }
        return m.onClick ? (
          <button key={m.label} type="button" onClick={m.onClick} aria-pressed={!!m.active}
            className="tap-feedback" style={{ all: 'unset', cursor: 'pointer', ...box }}>
            {inner}
          </button>
        ) : (
          <div key={m.label} style={box}>{inner}</div>
        )
      })}
    </div>
  )
}

// ── Stock cards (simple / table / provider views) ───────────────────────────
export function MobileStockCards({ items, coverageUnit, mode, effectiveQty, editedQty, notYet, onOpen, ariaLabel }: {
  items: InventoryStatusItem[]
  coverageUnit?: CoverageUnit
  /** `simple` leads with the quantity to order; `table` with the stock on hand. */
  mode: 'simple' | 'table'
  effectiveQty: (item: InventoryStatusItem) => number
  editedQty: Record<string, number>
  notYet: (item: InventoryStatusItem) => string
  onOpen: (item: InventoryStatusItem) => void
  ariaLabel?: string
}) {
  const { t } = useLanguage()
  return (
    <MobileList ariaLabel={ariaLabel}>
      {items.map(item => {
        const qty = effectiveQty(item)
        const hasQty = item.recommended_qty != null && item.recommended_qty > 0
        const cov = item.coverage_days != null
          ? `${fmtNum(Math.round(item.coverage_days))} ${coverageUnitShort(coverageUnit, t)}`
          : null
        const secondary = [
          item.display_name ? item.sku : null,
          mode === 'table'
            ? (item.has_stock ? `${t('inventory.m_stock_short')} ${fmtNum(Math.round(item.current_stock ?? 0))}` : t('inventory.no_record'))
            : item.supplier,
          cov,
        ].filter(Boolean).join(' · ')
        return (
          <MobileCard
            key={item.sku}
            title={item.display_name || item.sku}
            subtitle={secondary || undefined}
            status={{ label: signalLabel(t, item.signal), tone: signalTone(item.signal) }}
            value={hasQty
              ? <span style={{ color: editedQty[item.sku] != null ? 'var(--accent)' : undefined }}>{fmtNum(qty)}</span>
              : <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--dim)' }}>
                  {item.recommended_qty === 0 ? notYet(item) : '—'}
                </span>}
            valueCaption={hasQty
              ? (editedQty[item.sku] != null ? t('inventory.m_qty_caption_edited') : t('inventory.m_qty_caption'))
              : undefined}
            onClick={() => onOpen(item)}
            ariaLabel={`${item.display_name || item.sku}, ${signalLabel(t, item.signal)}`}
          >
            {(item.incoming_qty ?? 0) > 0 && (
              <span style={{ fontSize: 12, color: 'var(--muted)' }}>
                {incomingText(t, item.incoming_qty, item.incoming_sources)}
              </span>
            )}
          </MobileCard>
        )
      })}
    </MobileList>
  )
}

/** Provider view: one titled group per supplier, the most urgent first (the
 *  order `byProvider` already computed). Collapsible like the desktop groups. */
export function MobileProviderGroups({ groups, render }: {
  groups: [string, InventoryStatusItem[]][]
  render: (items: InventoryStatusItem[]) => React.ReactNode
}) {
  const { t } = useLanguage()
  const [closed, setClosed] = useState<Record<string, boolean>>({})
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {groups.map(([name, items]) => {
        const key = name || '__none__'
        const open = !closed[key]
        const urgent = items.filter(i => i.signal === 'PEDIR_YA').length
        const soon = items.filter(i => i.signal === 'PEDIR_PRONTO').length
        return (
          <section key={key} aria-label={name || t('inventory.no_provider')}>
            <button type="button" onClick={() => setClosed(c => ({ ...c, [key]: open }))} aria-expanded={open}
              className="tap-feedback"
              style={{ all: 'unset', boxSizing: 'border-box', width: '100%', minHeight: 44, cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 8, padding: '4px 2px 8px' }}>
              <span style={{ flex: 1, minWidth: 0, fontSize: 15, fontWeight: 700, color: 'var(--text)',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {name || t('inventory.no_provider')}
              </span>
              {urgent > 0 && <StatusBadge tone="danger" label={`${urgent} ${urgent !== 1 ? t('inventory.urgent_plural') : t('inventory.urgent_singular')}`} />}
              {soon > 0 && <StatusBadge tone="warning" label={`${soon} ${t('inventory.soon_suffix')}`} />}
              <span style={{ fontSize: 12, color: 'var(--dim)', whiteSpace: 'nowrap' }}>{items.length}</span>
              <ChevronDown size={16} color="var(--dim)" aria-hidden="true"
                style={{ transform: open ? 'rotate(180deg)' : undefined, transition: 'transform var(--dur-2, 160ms) var(--ease-out, ease)' }} />
            </button>
            {open && render(items)}
          </section>
        )
      })}
    </div>
  )
}

// ── SKU detail sheet ─────────────────────────────────────────────────────────
function Fact({ label, children }: { label: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 12, color: 'var(--dim)' }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)', marginTop: 2, overflowWrap: 'anywhere' }}>{children}</div>
    </div>
  )
}

export function MobileSkuSheet({
  item, coverageUnit, onClose, canEdit, onEdit, onDelete,
  qty, onQtyChange, notYet, calc,
}: {
  item: InventoryStatusItem | null
  coverageUnit?: CoverageUnit
  onClose: () => void
  canEdit: boolean
  onEdit: (item: InventoryStatusItem) => void
  onDelete: (sku: string) => void
  /** The quantity the buyer will order (their edit, else the recommendation). */
  qty: number
  onQtyChange: (sku: string, qty: number) => void
  notYet: (item: InventoryStatusItem) => string
  /** The calculation, "why it changed", planning values and the what-if
   *  simulator — rendered by page.tsx, which owns those panels. */
  calc?: React.ReactNode
}) {
  const { t } = useLanguage()
  const [showCalc, setShowCalc] = useState(false)
  const [draft, setDraft] = useState<string | null>(null)
  const open = !!item
  const it = item
  const hasQty = !!it && it.recommended_qty != null && it.recommended_qty > 0
  const shownQty = draft ?? String(qty)

  function commit(raw: string) {
    if (!it) return
    const n = parseInt(raw, 10)
    if (!isNaN(n) && n > 0) onQtyChange(it.sku, n)
    setDraft(null)
  }
  function step(d: number) {
    if (!it) return
    const n = Math.max(1, (parseInt(shownQty, 10) || qty) + d)
    onQtyChange(it.sku, n)
    setDraft(null)
  }
  function close() { setShowCalc(false); setDraft(null); onClose() }

  return (
    <BottomSheet
      open={open}
      onClose={close}
      title={it ? (it.display_name || it.sku) : ''}
      footer={it && canEdit ? (
        <div style={{ display: 'flex', gap: 8, width: '100%' }}>
          {it.has_stock && (
            <button type="button" className="mobile-btn mobile-btn-secondary" onClick={() => onDelete(it.sku)}
              aria-label={`${t('inventory.title_delete')}: ${it.sku}`} style={{ flex: '0 0 auto', color: 'var(--signal-order-now-fg)' }}>
              <Trash2 size={16} aria-hidden="true" />
            </button>
          )}
          <button type="button" className="mobile-btn mobile-btn-primary" onClick={() => onEdit(it)}>
            <Edit2 size={16} aria-hidden="true" /> {t('inventory.m_edit_product')}
          </button>
        </div>
      ) : undefined}
    >
      {it && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, paddingBottom: 4 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <SharedSignalBadge signal={it.signal} />
            {it.display_name && <span style={{ fontFamily: 'monospace', fontSize: 13, color: 'var(--muted)' }}>{it.sku}</span>}
          </div>

          {/* The quantity to order, editable — the phone twin of the dashed
              number in the desktop table, feeding the same "edited order"
              export. */}
          <div style={{ padding: '12px 14px', borderRadius: 12, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--muted)', marginBottom: 8 }}>{t('inventory.col_qty_to_order')}</div>
            {hasQty ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <button type="button" className="mobile-btn mobile-btn-secondary" style={{ flex: '0 0 48px', padding: 0 }}
                  onClick={() => step(-1)} aria-label={t('mobile.qty_decrease')}><Minus size={18} aria-hidden="true" /></button>
                <input
                  type="number" inputMode="numeric" min={1} enterKeyHint="done"
                  name={`m-order-qty-${it.sku}`} aria-label={t('inventory.edit_qty_title')}
                  value={shownQty}
                  onChange={e => setDraft(e.target.value)}
                  onBlur={e => commit(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
                  style={{ ...mInput, textAlign: 'center', fontSize: 22, fontWeight: 800, flex: 1 }}
                />
                <button type="button" className="mobile-btn mobile-btn-secondary" style={{ flex: '0 0 48px', padding: 0 }}
                  onClick={() => step(1)} aria-label={t('mobile.qty_increase')}><Plus size={18} aria-hidden="true" /></button>
              </div>
            ) : (
              <div style={{ fontSize: 15, color: 'var(--dim)' }}>{it.recommended_qty === 0 ? notYet(it) : '—'}</div>
            )}
            {qty !== (it.recommended_qty ?? 0) && hasQty && (
              <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 6 }}>
                {t('inventory.m_qty_recommended', { n: fmtNum(it.recommended_qty ?? 0) })}
              </div>
            )}
            {(it.incoming_qty ?? 0) > 0 && (
              <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6 }}>
                {incomingText(t, it.incoming_qty, it.incoming_sources)}
              </div>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '14px 12px' }}>
            <Fact label={t('inventory.col_stock')}>
              {it.has_stock ? fmtNum(Math.round(it.current_stock ?? 0)) : <span style={{ color: 'var(--dim)', fontWeight: 400 }}>{t('inventory.no_record')}</span>}
            </Fact>
            <Fact label={t('inventory.wh_col_coverage')}>
              {it.coverage_days != null ? `${fmtNum(Math.round(it.coverage_days))} ${coverageUnitShort(coverageUnit, t)}` : '—'}
            </Fact>
            <Fact label={t('inventory.col_demand_lt')}>{it.lead_time_demand != null ? fmtNum(Math.round(it.lead_time_demand)) : '—'}</Fact>
            <Fact label={t('inventory.col_lead_time')}>{it.lead_time_days != null ? `${it.lead_time_days}d` : '—'}</Fact>
            <Fact label="MOQ">{it.moq != null ? fmtNum(it.moq) : '—'}</Fact>
            <Fact label="ABC-XYZ">{it.abc_xyz || '—'}</Fact>
            <Fact label={t('inventory.col_warehouse_value')}>{it.inventory_value != null ? formatMoney(it.inventory_value) : '—'}</Fact>
            <Fact label={t('inventory.col_provider')}>{it.supplier || '—'}</Fact>
          </div>

          {calc && (
            <div>
              <button type="button" onClick={() => setShowCalc(v => !v)} aria-expanded={showCalc}
                className="mobile-btn mobile-btn-secondary" style={{ width: '100%', justifyContent: 'space-between' }}>
                <span>{showCalc ? t('inventory.m_hide_calc') : t('inventory.m_show_calc')}</span>
                <ChevronDown size={18} aria-hidden="true" style={{ transform: showCalc ? 'rotate(180deg)' : undefined, transition: 'transform var(--dur-2, 160ms) var(--ease-out, ease)' }} />
              </button>
              {showCalc && <div className="page-enter" style={{ marginTop: 10, minWidth: 0 }}>{calc}</div>}
            </div>
          )}
        </div>
      )}
    </BottomSheet>
  )
}

// ── Edit sheet ──────────────────────────────────────────────────────────────
export interface MobileEditState {
  current_stock: string; lead_time_days: string; unit_cost: string; moq: string; supplier: string
  display_name: string; service_level: string; sale_price: string; category: string; family: string
  brand: string; unit_of_measure: string; barcode: string
}

export function MobileEditSheet({ sku, state, setState, suppliers, onSave, onCancel, saving }: {
  sku: string | null
  state: MobileEditState | null
  setState: (fn: (s: MobileEditState | null) => MobileEditState | null) => void
  suppliers: Supplier[]
  onSave: () => void
  onCancel: () => void
  saving: boolean
}) {
  const { t } = useLanguage()
  const open = !!sku && !!state
  const set = (k: keyof MobileEditState) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const v = e.target.value
    setState(s => s ? { ...s, [k]: v } : s)
  }
  const group: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 12 }
  const h: React.CSSProperties = { margin: '4px 0 0', fontSize: 12, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--dim)' }
  const two: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }
  return (
    <BottomSheet
      open={open}
      onClose={onCancel}
      title={sku ? `${t('inventory.title_edit')} · ${sku}` : ''}
      maxHeight="92dvh"
      footer={(
        <div style={{ display: 'flex', gap: 8, width: '100%' }}>
          <button type="button" className="mobile-btn mobile-btn-secondary" onClick={onCancel}>{t('common.cancel')}</button>
          <button type="button" className="mobile-btn mobile-btn-primary" onClick={onSave} disabled={saving}>
            {saving ? t('inventory.saving_ellipsis') : t('inventory.btn_save')}
          </button>
        </div>
      )}
    >
      {state && sku && (
        <form onSubmit={e => { e.preventDefault(); onSave() }} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div style={group}>
            <h3 style={h}>{t('inventory.m_section_product')}</h3>
            <MField label={t('inventory.edit_display_name_placeholder')} hint={t('inventory.edit_display_name_hint')}>
              <input style={mInput} name={`m-edit-display-name-${sku}`} value={state.display_name} onChange={set('display_name')} enterKeyHint="next" />
            </MField>
            <div style={two}>
              <MField label={t('inventory.edit_category')}><input style={mInput} name={`m-edit-category-${sku}`} value={state.category} onChange={set('category')} /></MField>
              <MField label={t('inventory.edit_family')}><input style={mInput} name={`m-edit-family-${sku}`} value={state.family} onChange={set('family')} /></MField>
              <MField label={t('inventory.edit_brand')}><input style={mInput} name={`m-edit-brand-${sku}`} value={state.brand} onChange={set('brand')} /></MField>
              <MField label={t('inventory.edit_unit')}><input style={mInput} name={`m-edit-unit-${sku}`} value={state.unit_of_measure} onChange={set('unit_of_measure')} /></MField>
            </div>
            <MField label={t('inventory.edit_barcode')}>
              <input style={mInput} name={`m-edit-barcode-${sku}`} inputMode="numeric" value={state.barcode} onChange={set('barcode')} />
            </MField>
          </div>

          <div style={group}>
            <h3 style={h}>{t('inventory.m_section_replenishment')}</h3>
            <div style={two}>
              <MField label={t('inventory.col_current_stock')} hint={t('inventory.edit_stock_hint')}>
                <input style={mInput} type="number" inputMode="decimal" min={0} name={`m-edit-current-stock-${sku}`} value={state.current_stock} onChange={set('current_stock')} />
              </MField>
              <MField label={t('inventory.col_lead_time_days')} hint={t('inventory.edit_provider_days_hint')}>
                <input style={mInput} type="number" inputMode="numeric" min={1} max={365} name={`m-edit-lead-time-${sku}`} value={state.lead_time_days} onChange={set('lead_time_days')} />
              </MField>
            </div>
            <MField label={t('inventory.col_provider')}>
              <select style={mInput} name={`m-edit-supplier-${sku}`} value={state.supplier} onChange={set('supplier')}>
                <option value="">{t('inventory.edit_no_provider_option')}</option>
                {/* A typed supplier that is not in the list yet stays selectable. */}
                {state.supplier && !suppliers.some(s => s.name === state.supplier) && (
                  <option value={state.supplier}>{state.supplier}</option>
                )}
                {suppliers.map(s => <option key={s.id} value={s.name}>{s.name}</option>)}
              </select>
            </MField>
            <MField label={t('inventory.edit_min_per_order')} hint={t('inventory.help_moq')}>
              <input style={mInput} type="number" inputMode="numeric" min={0} name={`m-edit-moq-${sku}`} value={state.moq} onChange={set('moq')} />
            </MField>
          </div>

          <div style={group}>
            <h3 style={h}>{t('inventory.m_section_prices')}</h3>
            <div style={two}>
              <MField label={t('inventory.edit_provider_price_hint')}>
                <input style={mInput} type="number" inputMode="decimal" min={0} placeholder="0" name={`m-edit-unit-cost-${sku}`} value={state.unit_cost} onChange={set('unit_cost')} />
              </MField>
              <MField label={t('inventory.edit_sale_price')}>
                <input style={mInput} type="number" inputMode="decimal" min={0} name={`m-edit-sale-price-${sku}`} value={state.sale_price} onChange={set('sale_price')} />
              </MField>
            </div>
            <MField label={t('inventory.edit_service_level_label')} hint={t('inventory.help_service_level')}>
              <select style={mInput} name={`m-edit-service-level-${sku}`} value={state.service_level} onChange={set('service_level')}>
                <option value="0.90">90% — {t('inventory.service_level_low')}</option>
                <option value="0.95">95% — {t('inventory.service_level_normal')}</option>
                <option value="0.97">97% — {t('inventory.service_level_high')}</option>
                <option value="0.99">99% — {t('inventory.service_level_max')}</option>
              </select>
            </MField>
          </div>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--dim)' }}>{t('inventory.edit_recalculated_on_save')}</p>
          {/* Lets the keyboard's Go/Enter submit the form. */}
          <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
        </form>
      )}
    </BottomSheet>
  )
}

// ── Stock entry (the "Actualizar stock" view) ───────────────────────────────
type Draft = { current_stock: string; lead_time_days: string; supplier: string }

export function MobileStockEntry({ items, draft, modified, readOnly, savingRow, onChange, onKeyDown }: {
  items: InventoryStatusItem[]
  draft: Record<string, Draft>
  modified: Set<string>
  readOnly: boolean
  savingRow: string | null
  onChange: (sku: string, field: keyof Draft, value: string) => void
  onKeyDown: (e: React.KeyboardEvent<HTMLInputElement>, sku: string, field: string) => void
}) {
  const { t } = useLanguage()
  return (
    <MobileList ariaLabel={t('inventory.view_update')}>
      {items.map((item, idx) => {
        const d = draft[item.sku]
        const isMod = modified.has(item.sku)
        const busy = savingRow === item.sku
        const name = item.display_name || item.sku
        const field = (col: string) => `${col} — ${name}`
        return (
          <li key={item.sku} className="mobile-list-item"
            style={{ padding: '12px 14px', background: isMod ? 'color-mix(in srgb, var(--signal-order-soon-fg) 6%, transparent)' : undefined, opacity: busy ? 0.6 : 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, minWidth: 0 }}>
              {isMod && (
                <span title={t('inventory.bulk_row_unsaved')}
                  style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--signal-order-soon-fg)', flexShrink: 0 }}>
                  <span className="sr-only">{t('inventory.bulk_row_unsaved')}</span>
                </span>
              )}
              <span style={{ flex: 1, minWidth: 0, fontSize: 15, fontWeight: 600, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
              {item.display_name && <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--dim)', flexShrink: 0 }}>{item.sku}</span>}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.3fr) minmax(0, 1fr)', gap: 10 }}>
              <MField label={t('inventory.col_current_stock')}>
                <input
                  style={{ ...mInput, fontSize: 18, fontWeight: 700 }}
                  type="number" inputMode="decimal" min={0} enterKeyHint="next"
                  name={`m-bulk-current-stock-${item.sku}`} aria-label={field(t('inventory.col_current_stock'))}
                  disabled={readOnly}
                  value={d?.current_stock ?? ''}
                  onChange={e => onChange(item.sku, 'current_stock', e.target.value)}
                  onKeyDown={e => onKeyDown(e, item.sku, 'current_stock')}
                  data-bulk-field="current_stock"
                  data-tour={idx === 0 ? 'inv.update_stock' : undefined}
                />
              </MField>
              <MField label={t('inventory.col_lead_time_days')}>
                <input
                  style={mInput}
                  type="number" inputMode="numeric" min={1} max={365} enterKeyHint="next"
                  name={`m-bulk-lead-time-${item.sku}`} aria-label={field(t('inventory.col_lead_time_days'))}
                  disabled={readOnly}
                  value={d?.lead_time_days ?? ''}
                  onChange={e => onChange(item.sku, 'lead_time_days', e.target.value)}
                  onKeyDown={e => onKeyDown(e, item.sku, 'lead_time_days')}
                  data-bulk-field="lead_time_days"
                />
              </MField>
            </div>
            <div style={{ marginTop: 10 }}>
              <MField label={t('inventory.col_provider')}>
                <input
                  style={mInput}
                  type="text" enterKeyHint="next" autoComplete="off"
                  name={`m-bulk-supplier-${item.sku}`} aria-label={field(t('inventory.col_provider'))}
                  disabled={readOnly}
                  value={d?.supplier ?? ''}
                  onChange={e => onChange(item.sku, 'supplier', e.target.value)}
                  onKeyDown={e => onKeyDown(e, item.sku, 'supplier')}
                  data-bulk-field="supplier"
                />
              </MField>
            </div>
          </li>
        )
      })}
    </MobileList>
  )
}

// ── Small helpers for the analytical views ──────────────────────────────────
/** The window / threshold controls above each analytical view: one per row,
 *  full width, so a thumb can reach every one and the keyboard does not cover
 *  a field squeezed into a third of the screen. */
export function MobileControls({ children }: { children: React.ReactNode }) {
  return <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10, marginBottom: 14 }}>{children}</div>
}

export function moneyOr(v: number | null | undefined, unknown: string): string {
  return v == null ? unknown : formatMoneyCompact(v)
}
