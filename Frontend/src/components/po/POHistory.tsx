'use client'
import { useState, useEffect, useCallback } from 'react'
import { getPOItems, receivePO, sendPOToSuppliers } from '@/lib/api'
import type { POLogEntry, POItemLine } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import { Truck, X, Send } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { formatMoney } from '@/lib/currency'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { formatPoNumber } from '@/lib/poNumber'

// ── Palette (same CSS vars as the rest of the app) ───────────────────────────
const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  red: '#ef4444', amber: '#f59e0b', green: '#22c55e', indigo: '#818cf8',
}

function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString('es', {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtUnits(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

const RECEPTION_LABEL: Record<string, { labelKey: string; color: string; bg: string }> = {
  pending:      { labelKey: 'po.reception_pending',      color: 'var(--signal-order-soon-fg)', bg: 'var(--signal-order-soon-bg)' },
  partial:      { labelKey: 'po.reception_partial',      color: C.indigo, bg: 'rgba(129,140,248,0.12)' },
  received:     { labelKey: 'po.reception_received',     color: 'var(--signal-ok-fg)',           bg: 'var(--signal-ok-bg)' },
  not_received: { labelKey: 'po.reception_not_received', color: 'var(--signal-order-now-fg)',     bg: 'var(--signal-order-now-bg)' },
}

export function ReceptionModal({ poId, onClose, onSaved }: {
  poId: string
  onClose: () => void
  onSaved: () => void
}) {
  const { t } = useLanguage()
  const [items,   setItems]   = useState<POItemLine[] | null>(null)
  const [qty,     setQty]     = useState<Record<string, string>>({})
  const [saving,  setSaving]  = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  useEffect(() => {
    getPOItems(poId)
      .then(res => {
        const ordered = res.items.filter(i => i.status === 'approved' || i.status === 'modified')
        setItems(ordered)
        // Pre-fill with what's still pending per line
        setQty(Object.fromEntries(ordered.map(i => [
          i.sku,
          String(Math.max(0, (i.final_qty || 0) - (i.received_qty || 0))),
        ])))
      })
      .catch(e => setError(e instanceof Error ? e.message : t('common.error')))
  }, [poId])

  const save = useCallback(async (complete: boolean) => {
    if (!items) return
    setSaving(true)
    setError(null)
    try {
      if (complete) {
        await receivePO(poId)
      } else {
        await receivePO(poId, {
          lines: items.map(i => ({
            sku: i.sku,
            received_qty: Math.max(0, Number(qty[i.sku] ?? 0) || 0),
          })),
        })
      }
      onSaved()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('po.reception_err_save'))
      setSaving(false)
    }
  }, [items, poId, qty, onSaved])

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
          width: '100%', maxWidth: 520, maxHeight: '85vh', overflowY: 'auto',
          background: C.surface, border: `1px solid ${C.border}`,
          borderRadius: 14, padding: 24,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <Truck size={16} color={C.indigo} />
          <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>{t('po.reception_title')}</span>
          <button
            onClick={onClose}
            aria-label={t('common.close')}
            style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', color: C.dim }}
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <p style={{ margin: '0 0 16px', fontSize: 12, color: C.dim, lineHeight: 1.5 }}>
          {t('po.reception_subtitle')}
        </p>

        {!items && !error && <div style={{ padding: 24, textAlign: 'center' }}><Spinner size={16} /></div>}

        {items && (
          <>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr>
                  {[t('po.reception_col_product'), t('po.reception_col_supplier'), t('po.reception_col_ordered'), t('po.reception_col_received_before'), t('po.reception_col_arriving')].map(h => (
                    <th key={h} style={{
                      textAlign: 'left', padding: '6px 8px', color: C.dim,
                      fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em',
                      borderBottom: `1px solid ${C.border}`,
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map(i => (
                  <tr key={i.sku} style={{ borderBottom: `1px solid ${C.border}` }}>
                    <td style={{ padding: '8px' }}>
                      <div style={{ fontWeight: 600, color: C.text }}>{i.display_name || i.sku}</div>
                      <div style={{ fontSize: 10, color: C.dim, fontFamily: 'monospace' }}>{i.sku}</div>
                    </td>
                    <td style={{ padding: '8px', color: C.muted }}>{i.supplier || '—'}</td>
                    <td style={{ padding: '8px', color: C.text, fontFamily: 'monospace' }}>
                      {i.final_qty.toLocaleString()}
                    </td>
                    <td style={{ padding: '8px', color: C.dim, fontFamily: 'monospace' }}>
                      {(i.received_qty || 0).toLocaleString()}
                    </td>
                    <td style={{ padding: '8px' }}>
                      <input
                        type="number" min={0}
                        name={`reception-qty-${i.sku}`} aria-label={t('po.reception_col_arriving')}
                        value={qty[i.sku] ?? ''}
                        onChange={e => setQty(prev => ({ ...prev, [i.sku]: e.target.value }))}
                        style={{
                          width: 80, padding: '6px 8px', borderRadius: 7,
                          border: `1px solid ${C.border}`, background: C.card,
                          color: C.text, fontSize: 12, fontFamily: 'monospace',
                        }}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {error && (
              <div style={{ marginTop: 12, padding: '8px 12px', borderRadius: 8, background: 'rgba(239,68,68,0.08)', fontSize: 12, color: C.red }}>
                {error}
              </div>
            )}

            <div style={{ display: 'flex', gap: 10, marginTop: 18, justifyContent: 'flex-end' }}>
              <button
                onClick={() => save(false)}
                disabled={saving}
                style={{
                  padding: '10px 18px', borderRadius: 9, fontSize: 13, fontWeight: 600,
                  background: 'transparent', color: C.text,
                  border: `1px solid ${C.border}`, cursor: saving ? 'not-allowed' : 'pointer',
                }}
              >
                {t('po.reception_btn_save_quantities')}
              </button>
              <button
                onClick={() => save(true)}
                disabled={saving}
                style={{
                  padding: '10px 18px', borderRadius: 9, fontSize: 13, fontWeight: 700,
                  background: C.green, color: '#fff', border: 'none',
                  cursor: saving ? 'not-allowed' : 'pointer', opacity: saving ? 0.7 : 1,
                }}
              >
                {saving ? t('common.saving') : t('po.reception_btn_all_arrived')}
              </button>
            </div>
          </>
        )}
        {error && !items && (
          <div style={{ padding: '8px 12px', borderRadius: 8, background: 'rgba(239,68,68,0.08)', fontSize: 12, color: C.red }}>
            {error}
          </div>
        )}
      </div>
    </div>
  )
}

function SendPOButton({ poLogId, suppliersWithoutContact }: {
  poLogId: string
  suppliersWithoutContact: string[]
}) {
  const { t } = useLanguage()
  const confirm = useConfirm()
  const [state, setState] = useState<'idle' | 'sending' | 'done'>('idle')
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)

  async function handleClick() {
    setState('sending')
    try {
      // Preview what the send will actually do BEFORE doing it: which
      // suppliers get the order, and which get silently skipped for having
      // no contact info on file.
      const res = await getPOItems(poLogId)
      const names = Array.from(new Set(
        res.items
          .filter(i => i.status === 'approved' || i.status === 'modified')
          .map(i => (i.supplier || '').trim())
          .filter(Boolean),
      ))
      const skipped = names.filter(n => suppliersWithoutContact.includes(n))
      const toSend  = names.filter(n => !suppliersWithoutContact.includes(n))

      const lines = [
        toSend.length > 0 ? `${t('po.send_confirm_to')}: ${toSend.join(', ')}.` : '',
        skipped.length > 0 ? `${t('po.send_confirm_skipped')}: ${skipped.join(', ')}.` : '',
      ].filter(Boolean).join(' ')

      const ok = await confirm({
        title: t('po.send_confirm_title'),
        message: lines || t('po.send_confirm_no_suppliers'),
        confirmLabel: t('po.send_confirm_action'),
      })
      if (!ok) { setState('idle'); return }

      const sendRes = await sendPOToSuppliers(poLogId)
      const anySent = sendRes.sent.length > 0
      const anySkipped = sendRes.skipped.length > 0
      const message = !anySent
        ? t('roi.send_po_none_sent')
        : anySkipped ? t('roi.send_po_partial') : t('roi.send_po_success')
      setResult({ ok: anySent, message })
      setState('done')
    } catch (e: unknown) {
      setResult({ ok: false, message: e instanceof Error ? e.message : t('roi.send_po_error') })
      setState('done')
    }
  }

  if (state === 'done' && result) {
    return (
      <span style={{ fontSize: 11, color: result.ok ? C.green : C.red, fontWeight: 600 }}>
        {result.message}
      </span>
    )
  }

  return (
    <button
      onClick={handleClick}
      disabled={state === 'sending'}
      style={{
        all: 'unset', cursor: state === 'sending' ? 'not-allowed' : 'pointer',
        display: 'inline-flex', alignItems: 'center', gap: 4,
        padding: '3px 10px', borderRadius: 7, fontSize: 11, fontWeight: 600,
        border: `1px solid ${C.border}`, color: C.text,
      }}
    >
      <Send size={11} />
      {state === 'sending' ? t('roi.send_po_sending') : t('roi.send_po')}
    </button>
  )
}

export function POHistoryTable({ entries, onReceive, suppliersWithoutContact = [] }: {
  entries: POLogEntry[]
  onReceive: (id: string) => void
  suppliersWithoutContact?: string[]
}) {
  const { t } = useLanguage()
  if (entries.length === 0) {
    return (
      <div style={{ padding: '40px 24px', textAlign: 'center', color: C.dim, fontSize: 13 }}>
        {t('roi.no_po_history')}
        <br />
        <span style={{ fontSize: 12, opacity: 0.7, marginTop: 6, display: 'block' }}>
          {t('roi.no_po_history_hint')}
        </span>
      </div>
    )
  }

  const columns = [
    t('roi.col_order'),
    t('roi.col_datetime'),
    t('roi.col_skus_in_order'),
    t('roi.col_urgent'),
    t('roi.col_upcoming'),
    t('roi.col_total_units'),
    t('roi.col_total_value'),
    t('roi.col_reception'),
  ]

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr style={{ background: C.card }}>
            {columns.map(h => (
              <th key={h} style={{
                padding: '9px 14px', textAlign: 'left', whiteSpace: 'nowrap',
                color: C.dim, fontWeight: 600, fontSize: 10,
                borderBottom: `1px solid ${C.border}`,
                textTransform: 'uppercase', letterSpacing: '0.06em',
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, idx) => (
            <tr key={entry.id} style={{
              background: idx % 2 === 0 ? C.surface : C.card,
              borderBottom: `1px solid ${C.border}`,
            }}>
              <td style={{ padding: '11px 14px', color: C.text, fontFamily: 'monospace', fontWeight: 600 }}>
                {formatPoNumber(entry.po_number)}
              </td>
              <td style={{ padding: '11px 14px', color: C.text, fontVariantNumeric: 'tabular-nums' }}>
                {fmtDateTime(entry.generated_at)}
              </td>
              <td style={{ padding: '11px 14px', fontWeight: 600, color: C.text }}>
                {entry.sku_count}
              </td>
              <td style={{ padding: '11px 14px' }}>
                {entry.skus_order_now > 0
                  ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 9px', borderRadius: 20, background: 'rgba(239,68,68,0.1)', color: C.red, fontWeight: 700, fontSize: 11 }}>
                      {entry.skus_order_now}
                    </span>
                  : <span style={{ color: C.dim }}>—</span>
                }
              </td>
              <td style={{ padding: '11px 14px' }}>
                {entry.skus_order_soon > 0
                  ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 9px', borderRadius: 20, background: 'rgba(245,158,11,0.1)', color: C.amber, fontWeight: 700, fontSize: 11 }}>
                      {entry.skus_order_soon}
                    </span>
                  : <span style={{ color: C.dim }}>—</span>
                }
              </td>
              <td style={{ padding: '11px 14px', color: C.muted, fontFamily: 'monospace' }}>
                {fmtUnits(entry.total_units)}
              </td>
              <td style={{ padding: '11px 14px', color: entry.total_value ? C.green : C.dim, fontFamily: 'monospace', fontWeight: entry.total_value ? 600 : 400 }}>
                {entry.total_value != null ? formatMoney(entry.total_value) : '—'}
              </td>
              <td style={{ padding: '11px 14px', whiteSpace: 'nowrap' }}>
                {(() => {
                  const status = entry.reception_status || 'pending'
                  const badge = RECEPTION_LABEL[status] || RECEPTION_LABEL.pending
                  const receivable = status === 'pending' || status === 'partial'
                  return (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                      <span style={{
                        padding: '2px 9px', borderRadius: 20, fontSize: 11, fontWeight: 700,
                        background: badge.bg, color: badge.color,
                      }}>
                        {t(badge.labelKey)}
                      </span>
                      {receivable && (
                        <button
                          onClick={() => onReceive(entry.id)}
                          style={{
                            all: 'unset', cursor: 'pointer',
                            display: 'inline-flex', alignItems: 'center', gap: 4,
                            padding: '3px 10px', borderRadius: 7, fontSize: 11, fontWeight: 600,
                            border: `1px solid ${C.border}`, color: C.text,
                          }}
                        >
                          <Truck size={11} aria-hidden="true" /> {t('po.reception_btn_register')}
                        </button>
                      )}
                      <SendPOButton poLogId={entry.id} suppliersWithoutContact={suppliersWithoutContact} />
                    </span>
                  )
                })()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
