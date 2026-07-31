'use client'
import { useState, useEffect, useCallback } from 'react'
import { getPriceBreaks, upsertPriceBreak, deletePriceBreak } from '@/lib/api'
import type { PriceBreak, Supplier } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import { InlineError } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'
import { Plus, Trash2, Tag } from 'lucide-react'

const C = {
  card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', indigo: 'var(--accent)', red: '#ef4444',
}

const inputS: React.CSSProperties = {
  background: 'var(--surface)', border: `1px solid ${C.border}`,
  borderRadius: 6, color: C.text, fontSize: 12, outline: 'none',
  padding: '6px 9px', width: '100%', boxSizing: 'border-box',
}

// Feature 3.5 authoring UI — lists and manages one supplier's price-break
// tiers ("from min_qty units on, each unit costs unit_price").
export default function PriceBreakManager({ supplier }: { supplier: Supplier }) {
  const { t } = useLanguage()
  const { undoable } = useToast()

  const [breaks,  setBreaks]  = useState<PriceBreak[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)
  const [saving,  setSaving]  = useState(false)

  const [sku,       setSku]       = useState('')
  const [minQty,    setMinQty]    = useState('')
  const [unitPrice, setUnitPrice] = useState('')
  const [notes,     setNotes]     = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const rows = await getPriceBreaks({ supplier_id: supplier.id })
      setBreaks([...rows].sort((a, b) => a.sku.localeCompare(b.sku) || a.min_qty - b.min_qty))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('suppliers.pb_err_loading'))
    } finally {
      setLoading(false)
    }
  }, [supplier.id, t])

  useEffect(() => { load() }, [load])

  const minQtyNum = Number(minQty)
  const unitPriceNum = Number(unitPrice)
  // `> 0` on the price, not `>= 0`: the API rejects 0 with a 422, so allowing
  // it here only bought a round-trip and an error the user could have been
  // spared. The two bounds now say the same thing.
  const canAdd = sku.trim().length > 0
    && minQty.trim().length > 0 && Number.isFinite(minQtyNum) && minQtyNum > 0
    && unitPrice.trim().length > 0 && Number.isFinite(unitPriceNum) && unitPriceNum > 0

  // Why the button is dim, said next to the button. Typing -50 greyed it out
  // and volunteered nothing, which reads as "the app is broken" rather than
  // "that number cannot be right". Only shown once the user has actually typed
  // something wrong — an empty form is not a mistake yet.
  const blockedReason =
    (minQty.trim().length > 0 && (!Number.isFinite(minQtyNum) || minQtyNum <= 0))
      ? t('suppliers.pb_min_qty_positive')
      : (unitPrice.trim().length > 0 && (!Number.isFinite(unitPriceNum) || unitPriceNum <= 0))
        ? t('suppliers.pb_price_positive')
        : null

  async function handleAdd() {
    if (!canAdd) return
    setSaving(true); setError(null)
    try {
      await upsertPriceBreak(supplier.id, {
        sku: sku.trim(),
        min_qty: minQtyNum,
        unit_price: unitPriceNum,
        notes: notes.trim() || undefined,
      })
      setSku(''); setMinQty(''); setUnitPrice(''); setNotes('')
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('suppliers.pb_err_saving'))
    } finally {
      setSaving(false)
    }
  }

  // A tier is three numbers off a price list: deleting one is a typo-level
  // mistake, not a decision worth a modal. The row goes now; the DELETE only
  // leaves when the undo window closes.
  function handleDelete(pb: PriceBreak) {
    setError(null)
    undoable({
      title:     t('suppliers.pb_deleted'),
      message:   `${pb.sku} · ${pb.min_qty}+`,
      undoLabel: t('common.undo'),
      apply:  () => setBreaks(prev => prev.filter(b => b.id !== pb.id)),
      revert: () => setBreaks(prev => prev.some(b => b.id === pb.id)
        ? prev
        // The list has one canonical order; re-sorting puts the tier back in
        // its own slot instead of at the end.
        : [...prev, pb].sort((a, b) => a.sku.localeCompare(b.sku) || a.min_qty - b.min_qty)),
      commit: () => deletePriceBreak(pb.id),
      onCommitError: (e: unknown) => {
        setError(e instanceof Error ? e.message : t('suppliers.pb_err_deleting'))
        load()
      },
    })
  }

  return (
    <div style={{
      background: C.card, border: `1px solid ${C.border}`, borderRadius: 8,
      padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Tag size={12} color={C.indigo} aria-hidden="true" />
        <span style={{ fontSize: 12, fontWeight: 700, color: C.text }}>
          {t('suppliers.pb_title')}
        </span>
      </div>
      <p style={{ margin: 0, fontSize: 11, color: C.dim }}>
        {t('suppliers.pb_hint')}
      </p>

      {error && <InlineError error={new Error(error)} onDismiss={() => setError(null)} />}

      {blockedReason && (
        <p style={{ margin: 0, fontSize: 11, color: '#f59e0b' }}>{blockedReason}</p>
      )}

      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0' }}>
          <Spinner size={12} />
          <span style={{ fontSize: 11, color: C.dim }}>{t('suppliers.pb_loading')}</span>
        </div>
      ) : breaks.length === 0 ? (
        <p style={{ margin: 0, fontSize: 11, color: C.dim, fontStyle: 'italic' }}>
          {t('suppliers.pb_empty')}
        </p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              <tr>
                {[
                  t('suppliers.pb_col_sku'),
                  t('suppliers.pb_col_min_qty'),
                  t('suppliers.pb_col_unit_price'),
                  t('suppliers.pb_col_notes'),
                  t('suppliers.pb_col_actions'),
                ].map(label => (
                  <th key={label} style={{
                    padding: '4px 8px', textAlign: 'left', whiteSpace: 'nowrap',
                    color: C.dim, fontWeight: 600, fontSize: 9,
                    textTransform: 'uppercase', letterSpacing: '0.05em',
                    borderBottom: `1px solid ${C.border}`,
                  }}>
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {breaks.map(pb => (
                <tr key={pb.id} style={{ borderBottom: `1px solid ${C.border}` }}>
                  <td style={{ padding: '6px 8px', fontFamily: 'monospace', color: C.text }}>{pb.sku}</td>
                  <td style={{ padding: '6px 8px', color: C.text }}>{pb.min_qty}</td>
                  <td style={{ padding: '6px 8px', color: C.text }}>{pb.unit_price}</td>
                  <td style={{ padding: '6px 8px', color: C.dim }}>{pb.notes || '—'}</td>
                  <td style={{ padding: '6px 8px' }}>
                    <button
                      onClick={() => handleDelete(pb)}
                      title={t('suppliers.pb_row_delete')}
                      aria-label={`${t('suppliers.pb_row_delete')}: ${pb.sku}`}
                      style={{ all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5, color: C.dim, display: 'flex' }}
                      onMouseEnter={e => (e.currentTarget.style.color = C.red)}
                      onMouseLeave={e => (e.currentTarget.style.color = C.dim)}
                    >
                      <Trash2 size={12} aria-hidden="true" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add-tier form */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 0.8fr 0.8fr 1.4fr auto', gap: 8, alignItems: 'end' }}>
        <div>
          <label style={{ fontSize: 9, fontWeight: 600, color: C.dim, display: 'block', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            {t('suppliers.pb_form_sku_label')}
          </label>
          <input style={inputS} name="pricebreak_sku" aria-label={t('suppliers.pb_form_sku_label')} placeholder={t('suppliers.pb_form_sku_placeholder')} value={sku} onChange={e => setSku(e.target.value)} />
        </div>
        <div>
          <label style={{ fontSize: 9, fontWeight: 600, color: C.dim, display: 'block', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            {t('suppliers.pb_form_min_qty_label')}
          </label>
          <input style={inputS} name="pricebreak_min_qty" type="number" min={0} step="any" value={minQty} onChange={e => setMinQty(e.target.value)} aria-label={t('suppliers.pb_form_min_qty_label')} />
        </div>
        <div>
          <label style={{ fontSize: 9, fontWeight: 600, color: C.dim, display: 'block', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            {t('suppliers.pb_form_unit_price_label')}
          </label>
          <input style={inputS} name="pricebreak_unit_price" type="number" min={0} step="any" value={unitPrice} onChange={e => setUnitPrice(e.target.value)} aria-label={t('suppliers.pb_form_unit_price_label')} />
        </div>
        <div>
          <label style={{ fontSize: 9, fontWeight: 600, color: C.dim, display: 'block', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            {t('suppliers.pb_form_notes_label')}
          </label>
          <input style={inputS} name="pricebreak_notes" aria-label={t('suppliers.pb_form_notes_label')} placeholder={t('suppliers.pb_form_notes_placeholder')} value={notes} onChange={e => setNotes(e.target.value)} />
        </div>
        <button
          onClick={handleAdd}
          disabled={!canAdd || saving}
          style={{
            all: 'unset', cursor: canAdd && !saving ? 'pointer' : 'default',
            display: 'flex', alignItems: 'center', gap: 5, justifyContent: 'center',
            padding: '6px 12px', borderRadius: 6, fontSize: 11, fontWeight: 600,
            background: C.indigo, color: '#fff', opacity: canAdd && !saving ? 1 : 0.5,
            whiteSpace: 'nowrap',
          }}
        >
          {saving ? <Spinner size={11} /> : <Plus size={11} aria-hidden="true" />}
          {t('suppliers.pb_form_submit')}
        </button>
      </div>
    </div>
  )
}
