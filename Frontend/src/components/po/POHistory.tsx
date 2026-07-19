'use client'
import { useState, useEffect, useCallback } from 'react'
import { getPOItems, receivePO, sendPOToSuppliers } from '@/lib/api'
import type { POLogEntry, POItemLine } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import { Truck, X, Send } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'

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

function fmtCurrency(n: number): string {
  return '$' + n.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

function fmtUnits(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

const RECEPTION_LABEL: Record<string, { label: string; color: string; bg: string }> = {
  pending:      { label: 'En camino',   color: C.amber,  bg: 'rgba(245,158,11,0.1)' },
  partial:      { label: 'Parcial',     color: C.indigo, bg: 'rgba(129,140,248,0.1)' },
  received:     { label: 'Recibida',    color: C.green,  bg: 'rgba(34,197,94,0.1)' },
  not_received: { label: 'No llegó',    color: C.red,    bg: 'rgba(239,68,68,0.1)' },
}

export function ReceptionModal({ poId, onClose, onSaved }: {
  poId: string
  onClose: () => void
  onSaved: () => void
}) {
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
          String(Math.max(0, (i.cantidad_final || 0) - (i.cantidad_recibida || 0))),
        ])))
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Error'))
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
            cantidad_recibida: Math.max(0, Number(qty[i.sku] ?? 0) || 0),
          })),
        })
      }
      onSaved()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Error al registrar la recepción')
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
          <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>Registrar llegada del pedido</span>
          <button onClick={onClose} style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', color: C.dim }}>
            <X size={16} />
          </button>
        </div>
        <p style={{ margin: '0 0 16px', fontSize: 12, color: C.dim, lineHeight: 1.5 }}>
          El stock se actualiza solo con lo recibido, y Faro aprende el tiempo real de entrega de cada proveedor.
        </p>

        {!items && !error && <div style={{ padding: 24, textAlign: 'center' }}><Spinner size={16} /></div>}

        {items && (
          <>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr>
                  {['Producto', 'Proveedor', 'Pedido', 'Recibido antes', 'Llega ahora'].map(h => (
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
                    <td style={{ padding: '8px', color: C.muted }}>{i.proveedor || '—'}</td>
                    <td style={{ padding: '8px', color: C.text, fontFamily: 'monospace' }}>
                      {i.cantidad_final.toLocaleString()}
                    </td>
                    <td style={{ padding: '8px', color: C.dim, fontFamily: 'monospace' }}>
                      {(i.cantidad_recibida || 0).toLocaleString()}
                    </td>
                    <td style={{ padding: '8px' }}>
                      <input
                        type="number" min={0}
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
                Guardar cantidades
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
                {saving ? 'Guardando…' : '✓ Llegó todo completo'}
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

function SendPOButton({ poLogId }: { poLogId: string }) {
  const { t } = useLanguage()
  const [state, setState] = useState<'idle' | 'confirm' | 'sending' | 'done'>('idle')
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)

  useEffect(() => {
    if (state !== 'confirm') return
    const timer = setTimeout(() => setState('idle'), 4000)
    return () => clearTimeout(timer)
  }, [state])

  async function handleConfirm() {
    setState('sending')
    try {
      const res = await sendPOToSuppliers(poLogId)
      const anySent = res.sent.length > 0
      const anySkipped = res.skipped.length > 0
      const message = !anySent
        ? t('roi.send_po_none_sent')
        : anySkipped ? t('roi.send_po_partial') : t('roi.send_po_success')
      setResult({ ok: anySent, message })
    } catch (e: unknown) {
      setResult({ ok: false, message: e instanceof Error ? e.message : t('roi.send_po_error') })
    } finally {
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
      onClick={() => (state === 'confirm' ? handleConfirm() : setState('confirm'))}
      disabled={state === 'sending'}
      style={{
        all: 'unset', cursor: state === 'sending' ? 'not-allowed' : 'pointer',
        display: 'inline-flex', alignItems: 'center', gap: 4,
        padding: '3px 10px', borderRadius: 7, fontSize: 11, fontWeight: 600,
        border: `1px solid ${state === 'confirm' ? C.indigo : C.border}`,
        color: state === 'confirm' ? C.indigo : C.text,
      }}
    >
      <Send size={11} />
      {state === 'sending' ? t('roi.send_po_sending') : state === 'confirm' ? t('roi.send_po_confirm') : t('roi.send_po')}
    </button>
  )
}

export function POHistoryTable({ entries, onReceive }: { entries: POLogEntry[]; onReceive: (id: string) => void }) {
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
    t('roi.col_datetime'),
    t('roi.col_skus_in_order'),
    t('roi.col_urgent'),
    t('roi.col_upcoming'),
    t('roi.col_total_units'),
    t('roi.col_total_value'),
    'Recepción',
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
              <td style={{ padding: '11px 14px', color: C.text, fontVariantNumeric: 'tabular-nums' }}>
                {fmtDateTime(entry.generated_at)}
              </td>
              <td style={{ padding: '11px 14px', fontWeight: 600, color: C.text }}>
                {entry.sku_count}
              </td>
              <td style={{ padding: '11px 14px' }}>
                {entry.skus_pedir_ya > 0
                  ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 9px', borderRadius: 20, background: 'rgba(239,68,68,0.1)', color: C.red, fontWeight: 700, fontSize: 11 }}>
                      {entry.skus_pedir_ya}
                    </span>
                  : <span style={{ color: C.dim }}>—</span>
                }
              </td>
              <td style={{ padding: '11px 14px' }}>
                {entry.skus_pedir_pronto > 0
                  ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 9px', borderRadius: 20, background: 'rgba(245,158,11,0.1)', color: C.amber, fontWeight: 700, fontSize: 11 }}>
                      {entry.skus_pedir_pronto}
                    </span>
                  : <span style={{ color: C.dim }}>—</span>
                }
              </td>
              <td style={{ padding: '11px 14px', color: C.muted, fontFamily: 'monospace' }}>
                {fmtUnits(entry.total_units)}
              </td>
              <td style={{ padding: '11px 14px', color: entry.total_value ? C.green : C.dim, fontFamily: 'monospace', fontWeight: entry.total_value ? 600 : 400 }}>
                {entry.total_value != null ? fmtCurrency(entry.total_value) : '—'}
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
                        {badge.label}
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
                          <Truck size={11} /> Registrar llegada
                        </button>
                      )}
                      <SendPOButton poLogId={entry.id} />
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
