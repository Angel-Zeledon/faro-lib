'use client'
/**
 * Manual purchase order form (PENDIENTES #1): the buyer picks a supplier,
 * types the lines and confirms — no forecast session involved. Posts to
 * POST /inventory/po and refreshes the history on save.
 */
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { listSuppliers, createManualPO, getTenantCurrency } from '@/lib/api'
import type { Supplier } from '@/lib/types'
import {
  useWarehouses, defaultWarehouse, DEFAULT_WAREHOUSE_NAME,
} from '@/components/inventory/WarehouseControls'
import { useLanguage } from '@/contexts/LanguageContext'
import { useErrorDetail } from '@/components/ui/States'
import Spinner from '@/components/ui/Spinner'
import { ClipboardList, Plus, Trash2, X } from 'lucide-react'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import BottomSheet from '@/components/mobile/BottomSheet'

const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', indigo: 'var(--accent)', red: '#C0504D',
}

interface LineDraft {
  sku: string
  qty: string
  unit_cost: string
  /** '' = the company's own currency; otherwise the ISO code the cost is quoted in. */
  currency: string
}

const EMPTY_LINE: LineDraft = { sku: '', qty: '', unit_cost: '', currency: '' }

export function ManualPOModal({ onClose, onSaved }: {
  onClose: () => void
  onSaved: () => void
}) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const { warehouses, multi } = useWarehouses()
  const [suppliers,  setSuppliers]  = useState<Supplier[] | null>(null)
  const [supplierId, setSupplierId] = useState('')
  const [warehouse,  setWarehouse]  = useState('')
  const [lines,      setLines]      = useState<LineDraft[]>([{ ...EMPTY_LINE }])
  const [saving,     setSaving]     = useState(false)
  const [error,      setError]      = useState<string | null>(null)
  // Currencies a line may be priced in, besides the company's own (exchange
  // rates are entered in Mi cuenta; with none, the order says the line is
  // unconverted instead of valuing it at 1).
  const [baseCode, setBaseCode] = useState('')
  const [otherCurrencies, setOtherCurrencies] = useState<string[]>([])
  const narrow = useIsNarrow()

  useEffect(() => {
    getTenantCurrency({ silent: true })
      .then(d => {
        setBaseCode(d.current.code)
        setOtherCurrencies((d.supported || []).map(c => c.code).filter(c => c !== d.current.code))
      })
      .catch(() => { /* the line stays in the company currency */ })
  }, [])

  useEffect(() => {
    listSuppliers()
      .then(s => {
        setSuppliers(s)
        if (s.length === 1) setSupplierId(s[0].id)
      })
      .catch(e => setError(errorDetail(e) || t('common.error')))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Destination options. The tenant default IS a real warehouse row (the
  // backend auto-creates one named `principal` and resolves every destination-
  // less PO to it BY NAME), so rendering a synthetic "Bodega principal" entry
  // above the list showed the buyer the same place twice. When that row is on
  // file we drop the synthetic entry and instead flag the real warehouse as the
  // default, preselecting it so the PO records its destination explicitly.
  // Tenants with no `principal` row keep the synthetic entry: for them it is
  // the only way to say "leave it to the default", and it duplicates nothing.
  const hasCanonicalDefault = warehouses.some(w => w.name === DEFAULT_WAREHOUSE_NAME)
  const defaultWh = hasCanonicalDefault ? defaultWarehouse(warehouses) : null
  useEffect(() => {
    if (defaultWh && !warehouse) setWarehouse(defaultWh.name)
  }, [defaultWh, warehouse])

  const setLine = (idx: number, patch: Partial<LineDraft>) =>
    setLines(prev => prev.map((l, i) => (i === idx ? { ...l, ...patch } : l)))

  // Mirrors the backend bounds (qty 0 < n <= 1e9, unit_cost >= 0). Without
  // this the API answers with a raw Pydantic sentence in English —
  // "qty: Input should be less than or equal to 1000000000" — which is not
  // something a buyer should ever have to read.
  const MAX_QTY = 1_000_000_000
  const badLine = lines.some(l => {
    if (!l.sku.trim() && !l.qty.trim() && !l.unit_cost.trim()) return false
    const qty = Number(l.qty)
    if (l.qty.trim() && (!Number.isFinite(qty) || qty <= 0 || qty > MAX_QTY)) return true
    const cost = Number(l.unit_cost)
    return !!l.unit_cost.trim() && (!Number.isFinite(cost) || cost < 0)
  })

  const validLines = lines
    .map(l => ({ sku: l.sku.trim(), qty: Number(l.qty), unit_cost: l.unit_cost.trim(), currency: l.currency }))
    .filter(l => l.sku && Number.isFinite(l.qty) && l.qty > 0 && l.qty <= MAX_QTY)

  const canSave = !!supplierId && validLines.length > 0 && !badLine && !saving

  async function save() {
    if (!canSave) return
    setSaving(true)
    setError(null)
    try {
      await createManualPO({
        supplier_id: supplierId,
        lines: validLines.map(l => ({
          sku: l.sku,
          qty: l.qty,
          ...(l.unit_cost !== '' && Number.isFinite(Number(l.unit_cost))
            ? { unit_cost: Number(l.unit_cost) } : {}),
          ...(l.currency ? { currency: l.currency } : {}),
        })),
        ...(multi && warehouse ? { destination_warehouse: warehouse } : {}),
      })
      onSaved()
    } catch (e: unknown) {
      setError(errorDetail(e) || t('common.error'))
      setSaving(false)
    }
  }

  // On a phone: a bottom sheet with one card per product line — a four-column
  // table of inputs does not fit 360px, and the confirm button stays pinned
  // above the keyboard-free area instead of at the end of the form.
  if (narrow) {
    const field: React.CSSProperties = {
      display: 'block', boxSizing: 'border-box', width: '100%', marginTop: 4,
      minHeight: 48, padding: '0 12px', borderRadius: 10,
      border: `1px solid ${C.border}`, background: C.card, color: C.text, fontSize: 16,
    }
    const label: React.CSSProperties = { display: 'block', fontSize: 12.5, fontWeight: 600, color: C.dim }
    return (
      <BottomSheet
        open
        onClose={onClose}
        maxHeight="94dvh"
        title={t('po.manual_title')}
        footer={suppliers && suppliers.length > 0 ? (
          <>
            <button className="mobile-btn mobile-btn-secondary" onClick={onClose}>{t('common.cancel')}</button>
            <button className="mobile-btn mobile-btn-primary" onClick={save} disabled={!canSave} aria-busy={saving}>
              {saving ? t('po.manual_saving') : t('po.manual_confirm')}
            </button>
          </>
        ) : undefined}
      >
        <p style={{ margin: '0 0 14px', fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('po.manual_subtitle')}</p>
        {!suppliers && !error && <div style={{ padding: 24, textAlign: 'center' }}><Spinner size={18} /></div>}
        {suppliers && suppliers.length === 0 && (
          <>
            <p style={{ fontSize: 14, color: C.dim, lineHeight: 1.5 }}>{t('po.manual_no_suppliers')}</p>
            <Link href="/proveedores" className="mobile-btn mobile-btn-secondary" style={{ width: '100%', textDecoration: 'none' }}>
              {t('po.manual_go_suppliers')}
            </Link>
          </>
        )}
        {suppliers && suppliers.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <label style={label}>
              {t('po.manual_supplier_label')}
              <select value={supplierId} onChange={e => setSupplierId(e.target.value)} style={field}>
                <option value="">{t('po.manual_supplier_placeholder')}</option>
                {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
            {multi && (
              <label style={label}>
                {t('po.manual_warehouse_label')}
                <select value={warehouse} onChange={e => setWarehouse(e.target.value)} style={field}>
                  {!defaultWh && <option value="">{t('po.manual_warehouse_default')}</option>}
                  {warehouses.map(w => (
                    <option key={w.name} value={w.name}>
                      {w.name === defaultWh?.name
                        ? t('po.manual_warehouse_default_option').replace('{name}', w.name)
                        : w.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {lines.map((l, idx) => (
              <fieldset key={idx} style={{
                margin: 0, border: `1px solid ${C.border}`, borderRadius: 12,
                padding: '10px 12px 12px', background: C.surface, minWidth: 0,
              }}>
                <legend style={{ padding: '0 4px', fontSize: 12, fontWeight: 700, color: C.dim }}>
                  {t('po.manual_line_n', { n: idx + 1 })}
                </legend>
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
                  <label style={{ ...label, flex: 1, minWidth: 0 }}>
                    {t('po.manual_col_sku')}
                    <input
                      value={l.sku}
                      onChange={e => setLine(idx, { sku: e.target.value })}
                      placeholder="SKU-001"
                      autoCapitalize="characters" autoCorrect="off" spellCheck={false}
                      style={{ ...field, fontFamily: 'monospace' }}
                    />
                  </label>
                  {lines.length > 1 && (
                    <button
                      onClick={() => setLines(prev => prev.filter((_, i) => i !== idx))}
                      aria-label={t('po.manual_remove_line')}
                      style={{
                        all: 'unset', boxSizing: 'border-box', cursor: 'pointer', flexShrink: 0,
                        width: 48, height: 48, borderRadius: 10, border: `1px solid ${C.border}`,
                        display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.red,
                      }}
                    >
                      <Trash2 size={18} aria-hidden="true" />
                    </button>
                  )}
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                  <label style={{ ...label, flex: 1, minWidth: 0 }}>
                    {t('po.manual_col_qty')}
                    <input
                      type="number" min={0} inputMode="numeric"
                      value={l.qty}
                      onChange={e => setLine(idx, { qty: e.target.value })}
                      style={{ ...field, fontVariantNumeric: 'tabular-nums' }}
                    />
                  </label>
                  <label style={{ ...label, flex: 1, minWidth: 0 }}>
                    {t('po.manual_col_cost')}
                    <input
                      type="number" min={0} step="0.01" inputMode="decimal"
                      value={l.unit_cost}
                      onChange={e => setLine(idx, { unit_cost: e.target.value })}
                      placeholder={t('po.manual_cost_optional')}
                      style={{ ...field, fontVariantNumeric: 'tabular-nums' }}
                    />
                  </label>
                  {otherCurrencies.length > 0 && (
                    <label style={{ ...label, flex: 1, minWidth: 0 }}>
                      {t('po.manual_col_currency')}
                      <select value={l.currency} onChange={e => setLine(idx, { currency: e.target.value })} style={field}>
                        <option value="">{baseCode}</option>
                        {otherCurrencies.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </label>
                  )}
                </div>
              </fieldset>
            ))}
            <button
              className="mobile-btn mobile-btn-secondary"
              onClick={() => setLines(prev => [...prev, { ...EMPTY_LINE }])}
              style={{ color: C.indigo, flex: 'none', width: '100%' }}
            >
              <Plus size={17} aria-hidden="true" /> {t('po.manual_add_line')}
            </button>
            {badLine && !error && (
              <p role="alert" style={{ margin: 0, fontSize: 13, color: C.red, lineHeight: 1.5 }}>{t('po.manual_invalid_line')}</p>
            )}
          </div>
        )}
        {error && <p role="alert" style={{ margin: '12px 0 0', fontSize: 13, color: C.red, lineHeight: 1.5 }}>{error}</p>}
      </BottomSheet>
    )
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 200,
        background: 'rgba(0,0,0,0.55)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: '100%', maxWidth: 560, maxHeight: '85vh', overflowY: 'auto',
          background: C.surface, border: `1px solid ${C.border}`,
          borderRadius: 14, padding: 24,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <ClipboardList size={16} color={C.indigo} />
          <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>{t('po.manual_title')}</span>
          <button
            onClick={onClose}
            aria-label={t('common.close')}
            style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', color: C.dim }}
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <p style={{ margin: '0 0 16px', fontSize: 12, color: C.dim, lineHeight: 1.5 }}>
          {t('po.manual_subtitle')}
        </p>

        {!suppliers && !error && (
          <div style={{ padding: 24, textAlign: 'center' }}><Spinner size={16} /></div>
        )}

        {suppliers && suppliers.length === 0 && (
          <p style={{ fontSize: 12, color: C.dim }}>{t('po.manual_no_suppliers')}</p>
        )}

        {suppliers && suppliers.length > 0 && (
          <>
            <label style={{ display: 'block', fontSize: 11, fontWeight: 600, color: C.dim, marginBottom: 4 }}>
              {t('po.manual_supplier_label')}
              <select
                value={supplierId}
                onChange={e => setSupplierId(e.target.value)}
                style={{
                  display: 'block', width: '100%', marginTop: 4, padding: '8px 10px',
                  borderRadius: 7, border: `1px solid ${C.border}`, background: C.card,
                  color: C.text, fontSize: 12,
                }}
              >
                <option value="">{t('po.manual_supplier_placeholder')}</option>
                {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>

            {multi && (
              <label style={{ display: 'block', fontSize: 11, fontWeight: 600, color: C.dim, margin: '12px 0 4px' }}>
                {t('po.manual_warehouse_label')}
                <select
                  value={warehouse}
                  onChange={e => setWarehouse(e.target.value)}
                  style={{
                    display: 'block', width: '100%', marginTop: 4, padding: '8px 10px',
                    borderRadius: 7, border: `1px solid ${C.border}`, background: C.card,
                    color: C.text, fontSize: 12,
                  }}
                >
                  {!defaultWh && <option value="">{t('po.manual_warehouse_default')}</option>}
                  {warehouses.map(w => (
                    <option key={w.name} value={w.name}>
                      {w.name === defaultWh?.name
                        ? t('po.manual_warehouse_default_option').replace('{name}', w.name)
                        : w.name}
                    </option>
                  ))}
                </select>
              </label>
            )}

            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, marginTop: 16 }}>
              <thead>
                <tr>
                  {[t('po.manual_col_sku'), t('po.manual_col_qty'), t('po.manual_col_cost'),
                    ...(otherCurrencies.length > 0 ? [t('po.manual_col_currency')] : []), ''].map((h, i) => (
                    <th key={i} style={{
                      textAlign: 'left', padding: '6px 8px', color: C.dim,
                      fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em',
                      borderBottom: `1px solid ${C.border}`,
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {lines.map((l, idx) => (
                  <tr key={idx} style={{ borderBottom: `1px solid ${C.border}` }}>
                    <td style={{ padding: 8 }}>
                      <input
                        value={l.sku}
                        aria-label={t('po.manual_col_sku')}
                        onChange={e => setLine(idx, { sku: e.target.value })}
                        placeholder="SKU-001"
                        style={{
                          width: '100%', padding: '6px 8px', borderRadius: 7,
                          border: `1px solid ${C.border}`, background: C.card,
                          color: C.text, fontSize: 12, fontFamily: 'monospace',
                        }}
                      />
                    </td>
                    <td style={{ padding: 8 }}>
                      <input
                        type="number" min={0}
                        value={l.qty}
                        aria-label={t('po.manual_col_qty')}
                        onChange={e => setLine(idx, { qty: e.target.value })}
                        style={{
                          width: 80, padding: '6px 8px', borderRadius: 7,
                          border: `1px solid ${C.border}`, background: C.card,
                          color: C.text, fontSize: 12, fontFamily: 'monospace',
                        }}
                      />
                    </td>
                    <td style={{ padding: 8 }}>
                      <input
                        type="number" min={0} step="0.01"
                        value={l.unit_cost}
                        aria-label={t('po.manual_col_cost')}
                        onChange={e => setLine(idx, { unit_cost: e.target.value })}
                        placeholder={t('po.manual_cost_optional')}
                        style={{
                          width: 90, padding: '6px 8px', borderRadius: 7,
                          border: `1px solid ${C.border}`, background: C.card,
                          color: C.text, fontSize: 12, fontFamily: 'monospace',
                        }}
                      />
                    </td>
                    {otherCurrencies.length > 0 && (
                      <td style={{ padding: 8 }}>
                        <select
                          value={l.currency}
                          aria-label={t('po.manual_col_currency')}
                          onChange={e => setLine(idx, { currency: e.target.value })}
                          style={{
                            padding: '6px 6px', borderRadius: 7, border: `1px solid ${C.border}`,
                            background: C.card, color: C.text, fontSize: 12,
                          }}
                        >
                          <option value="">{baseCode}</option>
                          {otherCurrencies.map(c => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </td>
                    )}
                    <td style={{ padding: 8 }}>
                      {lines.length > 1 && (
                        <button
                          onClick={() => setLines(prev => prev.filter((_, i) => i !== idx))}
                          aria-label={t('po.manual_remove_line')}
                          style={{ all: 'unset', cursor: 'pointer', color: C.dim }}
                        >
                          <Trash2 size={13} aria-hidden="true" />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <button
              onClick={() => setLines(prev => [...prev, { ...EMPTY_LINE }])}
              style={{
                all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center',
                gap: 5, marginTop: 10, fontSize: 11.5, fontWeight: 600, color: C.indigo,
              }}
            >
              <Plus size={12} aria-hidden="true" />
              {t('po.manual_add_line')}
            </button>

            {badLine && !error && (
              <p style={{ margin: '12px 0 0', fontSize: 12, color: C.red }}>
                {t('po.manual_invalid_line')}
              </p>
            )}

            {error && (
              <p style={{ margin: '12px 0 0', fontSize: 12, color: C.red }}>{error}</p>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}>
              <button
                onClick={onClose}
                style={{
                  all: 'unset', cursor: 'pointer', padding: '7px 14px', borderRadius: 8,
                  fontSize: 12, fontWeight: 600, color: C.dim, border: `1px solid ${C.border}`,
                }}
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={save}
                disabled={!canSave}
                style={{
                  all: 'unset', cursor: canSave ? 'pointer' : 'not-allowed',
                  padding: '7px 16px', borderRadius: 8, fontSize: 12, fontWeight: 700,
                  background: canSave ? 'var(--accent)' : 'color-mix(in srgb, var(--accent) 35%, transparent)', color: '#fff',
                }}
              >
                {saving ? t('po.manual_saving') : t('po.manual_confirm')}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
