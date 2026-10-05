'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { getPOItems, receivePO, sendPOToSuppliers } from '@/lib/api'
import type { POLogEntry, POItemLine, OverdueReception, POApprovalBadge } from '@/lib/types'
import AttentionChip from '@/components/layout/AttentionChip'
import Spinner from '@/components/ui/Spinner'
import { useErrorDetail } from '@/components/ui/States'
import { Truck, X, Send, ChevronDown } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { formatMoney } from '@/lib/currency'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { formatPoNumber } from '@/lib/poNumber'
import { ForwardPOActions } from '@/components/po/ForwardPOActions'
import { UndoPOActions } from '@/components/po/UndoPOActions'
import { PaidPOActions } from '@/components/po/PaidPOActions'
import { ApprovalChip, RequestApprovalButton } from '@/components/po/POApproval'
import { CancelPOActions, CancelledBadge } from '@/components/po/CancelPOActions'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import BottomSheet from '@/components/mobile/BottomSheet'

// ── Palette (same CSS vars as the rest of the app) ───────────────────────────
const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  red: '#C0504D', amber: '#B7791F', green: '#2E8B62', indigo: 'var(--accent)',
}

// The locale has to follow the interface language: hardcoding 'es' printed
// "22 ago 2026" to a user reading an English screen.
function fmtDateTime(iso: string, lang: string): string {
  return new Date(iso).toLocaleString(lang === 'en' ? 'en-US' : 'es-CR', {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtUnits(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

const RECEPTION_LABEL: Record<string, { labelKey: string; color: string; bg: string }> = {
  pending:      { labelKey: 'po.reception_pending',      color: 'var(--signal-order-soon-fg)', bg: 'var(--signal-order-soon-bg)' },
  partial:      { labelKey: 'po.reception_partial',      color: C.indigo, bg: 'color-mix(in srgb, var(--accent) 12%, transparent)' },
  received:     { labelKey: 'po.reception_received',     color: 'var(--signal-ok-fg)',           bg: 'var(--signal-ok-bg)' },
  not_received: { labelKey: 'po.reception_not_received', color: 'var(--signal-order-now-fg)',     bg: 'var(--signal-order-now-bg)' },
}

export function ReceptionModal({ poId, onClose, onSaved }: {
  poId: string
  onClose: () => void
  onSaved: () => void
}) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const narrow = useIsNarrow()
  const [items,   setItems]   = useState<POItemLine[] | null>(null)
  const [qty,     setQty]     = useState<Record<string, string>>({})
  const [saving,  setSaving]  = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  // Read through a ref so a parent that passes a fresh arrow each render does
  // not re-run the open/close effect (which would steal focus mid-typing).
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  // A modal that says it is one: Esc closes it, and focus moves into it on
  // open and back to whatever opened it on close, so a screen-reader or
  // keyboard user is not left behind on the page underneath.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    dialogRef.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCloseRef.current() }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      opener?.focus?.()
    }
  }, [])

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
      .catch(e => setError(errorDetail(e) || t('common.error')))
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
      setError(errorDetail(e) || t('po.reception_err_save'))
      setSaving(false)
    }
  }, [items, poId, qty, onSaved])

  // On a phone the form is a bottom sheet: the quantity boxes scroll inside it
  // and the two decisions stay pinned under the thumb instead of at the end of
  // a list that may be longer than the screen.
  if (narrow) {
    return (
      <BottomSheet
        open
        onClose={onClose}
        maxHeight="94dvh"
        title={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <Truck size={17} color={C.indigo} aria-hidden="true" /> {t('po.reception_title')}
        </span>}
        footer={items ? (
          <>
            <button className="mobile-btn mobile-btn-secondary" onClick={() => save(false)} disabled={saving}>
              {t('po.reception_btn_save_quantities')}
            </button>
            <button
              className="mobile-btn"
              onClick={() => save(true)}
              disabled={saving}
              aria-busy={saving}
              style={{ background: C.green, color: '#fff' }}
            >
              {saving ? t('common.saving') : t('po.reception_btn_all_arrived')}
            </button>
          </>
        ) : undefined}
      >
        <p style={{ margin: '0 0 14px', fontSize: 13, color: C.dim, lineHeight: 1.5 }}>
          {t('po.reception_subtitle')}
        </p>
        {!items && !error && <div style={{ padding: 24, textAlign: 'center' }}><Spinner size={18} /></div>}
        {items && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {items.map(i => {
              const pending = Math.max(0, (i.final_qty || 0) - (i.received_qty || 0))
              return (
                <div key={i.sku} style={{
                  border: `1px solid ${C.border}`, borderRadius: 12, padding: '12px 14px',
                  background: C.card,
                }}>
                  <div style={{ fontWeight: 600, fontSize: 15, color: C.text, overflowWrap: 'anywhere' }}>
                    {i.display_name || i.sku}
                  </div>
                  <div style={{ fontSize: 12, color: C.dim, fontFamily: 'monospace', marginTop: 2, overflowWrap: 'anywhere' }}>
                    {i.sku}{i.supplier ? ` · ${i.supplier}` : ''}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'flex-end', gap: 12, marginTop: 10 }}>
                    <div style={{ flex: 1, minWidth: 0, fontSize: 12.5, color: C.muted, lineHeight: 1.5 }}>
                      {t('po.reception_col_ordered')}:{' '}
                      <strong style={{ color: C.text, fontVariantNumeric: 'tabular-nums' }}>{i.final_qty.toLocaleString()}</strong>
                      {(i.received_qty || 0) > 0 && (
                        <><br />{t('po.reception_col_received_before')}:{' '}
                          <span style={{ fontVariantNumeric: 'tabular-nums' }}>{(i.received_qty || 0).toLocaleString()}</span>
                        </>
                      )}
                    </div>
                    <label style={{ width: 120, flexShrink: 0, fontSize: 11.5, color: C.dim }}>
                      {t('po.reception_col_arriving')}
                      <input
                        type="number" min={0} inputMode="numeric" enterKeyHint="done"
                        name={`reception-qty-${i.sku}`}
                        aria-label={`${t('po.reception_col_arriving')} — ${i.display_name || i.sku}`}
                        value={qty[i.sku] ?? ''}
                        placeholder={String(pending)}
                        onFocus={e => e.currentTarget.select()}
                        onChange={e => setQty(prev => ({ ...prev, [i.sku]: e.target.value }))}
                        style={{
                          display: 'block', boxSizing: 'border-box', width: '100%', marginTop: 4,
                          minHeight: 48, padding: '0 12px', borderRadius: 10, textAlign: 'right',
                          border: `1px solid ${C.border}`, background: C.surface,
                          color: C.text, fontSize: 18, fontWeight: 700, fontVariantNumeric: 'tabular-nums',
                        }}
                      />
                    </label>
                  </div>
                </div>
              )
            })}
          </div>
        )}
        {error && (
          <div role="alert" style={{ marginTop: 12, padding: '10px 12px', borderRadius: 10, background: 'rgba(192,80,77,0.08)', fontSize: 13, color: C.red }}>
            {error}
          </div>
        )}
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
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="po-reception-title"
        tabIndex={-1}
        onClick={e => e.stopPropagation()}
        style={{
          outline: 'none',
          width: '100%', maxWidth: 520, maxHeight: '85vh', overflowY: 'auto',
          background: C.surface, border: `1px solid ${C.border}`,
          borderRadius: 14, padding: 24,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <Truck size={16} color={C.indigo} />
          <span id="po-reception-title" style={{ fontSize: 15, fontWeight: 700, color: C.text }}>{t('po.reception_title')}</span>
          <button
            onClick={onClose}
            aria-label={t('common.close')}
            style={{
              all: 'unset', cursor: 'pointer', marginLeft: 'auto', color: C.dim,
            }}
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <p style={{ margin: '0 0 16px', fontSize: 12, color: C.dim, lineHeight: 1.5 }}>
          {t('po.reception_subtitle')}
        </p>

        {!items && !error && <div style={{ padding: 24, textAlign: 'center' }}><Spinner size={16} /></div>}

        {items && (
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
        )}

        {items && (
          <>
            {error && (
              <div style={{ marginTop: 12, padding: '8px 12px', borderRadius: 8, background: 'rgba(192,80,77,0.08)', fontSize: 12, color: C.red }}>
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
          <div style={{ padding: '8px 12px', borderRadius: 8, background: 'rgba(192,80,77,0.08)', fontSize: 12, color: C.red }}>
            {error}
          </div>
        )}
      </div>
    </div>
  )
}

export function SendPOButton({ poLogId, suppliersWithoutContact, onSent, approval }: {
  poLogId: string
  suppliersWithoutContact: string[]
  /** Set by the server only for a tenant with an approval rule. While the order
   *  needs an approval it lacks, "send" is replaced by "request approval". */
  approval?: POApprovalBadge | null
  /** Called after a send that reached at least one supplier (the phone detail
   *  sheet reloads the order so its "sent" state is current). */
  onSent?: () => void
}) {
  const { t } = useLanguage()
  // 25px tall on desktop; a 48px full-width button on a phone.
  const narrow = useIsNarrow()
  const confirm = useConfirm()
  const errorDetail = useErrorDetail()
  const [state, setState] = useState<'idle' | 'sending' | 'done'>('idle')
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)
  // The hooks above are all unconditional; this return only changes the output.
  if (approval?.required) {
    return <RequestApprovalButton poLogId={poLogId} approval={approval} onChanged={onSent} />
  }

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
      if (anySent) onSent?.()
    } catch (e: unknown) {
      setResult({ ok: false, message: errorDetail(e) || t('roi.send_po_error') })
      setState('done')
    }
  }

  if (state === 'done' && result) {
    return (
      <span role="status" style={{ fontSize: narrow ? 13 : 11, color: result.ok ? C.green : C.red, fontWeight: 600 }}>
        {result.message}
      </span>
    )
  }

  if (narrow) {
    return (
      <button
        className="mobile-btn mobile-btn-secondary"
        onClick={handleClick}
        disabled={state === 'sending'}
        aria-busy={state === 'sending'}
        style={{ width: '100%' }}
      >
        <Send size={16} aria-hidden="true" />
        {state === 'sending' ? t('roi.send_po_sending') : t('roi.send_po')}
      </button>
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

/** Disclosure for the secondary actions of an order row. */
function MoreRowActions({ children }: { children: React.ReactNode }) {
  const { t } = useLanguage()
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
        style={{
          all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 3,
          padding: '3px 8px', borderRadius: 7, fontSize: 11, fontWeight: 600, color: C.dim,
          border: `1px solid ${C.border}`, whiteSpace: 'nowrap',
        }}
      >
        {t('mobile.more_actions')}
        <ChevronDown size={11} aria-hidden="true" style={{ transform: open ? 'rotate(180deg)' : undefined }} />
      </button>
      {open && (
        <span style={{ flexBasis: '100%', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
          {children}
        </span>
      )}
    </>
  )
}

export function POHistoryTable({ entries, onReceive, onUndone, suppliersWithoutContact = [], overdueById = {} }: {
  entries: POLogEntry[]
  /** Open orders past their expected arrival, by id: a calm chip in the row. */
  overdueById?: Record<string, OverdueReception>
  onReceive: (id: string) => void
  /** Reload after an undo rewrote stock or the sent flag, or the order was
   *  marked paid / unpaid. */
  onUndone?: () => void
  suppliersWithoutContact?: string[]
}) {
  const { t, lang } = useLanguage()
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
                padding: '9px 14px', textAlign: 'left', verticalAlign: 'bottom', lineHeight: 1.3,
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
              <td style={{ padding: '11px 14px', color: C.text, fontFamily: 'monospace', fontWeight: 600, whiteSpace: 'nowrap' }}>
                {formatPoNumber(entry.po_number)}
              </td>
              <td style={{ padding: '11px 14px', color: C.text, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
                {fmtDateTime(entry.generated_at, lang)}
              </td>
              <td style={{ padding: '11px 14px', fontWeight: 600, color: C.text }}>
                {entry.sku_count}
              </td>
              <td style={{ padding: '11px 14px' }}>
                {entry.skus_order_now > 0
                  ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 9px', borderRadius: 20, background: 'rgba(192,80,77,0.1)', color: C.red, fontWeight: 700, fontSize: 11 }}>
                      {entry.skus_order_now}
                    </span>
                  : <span style={{ color: C.dim }}>—</span>
                }
              </td>
              <td style={{ padding: '11px 14px' }}>
                {entry.skus_order_soon > 0
                  ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 9px', borderRadius: 20, background: 'rgba(183,121,31,0.1)', color: C.amber, fontWeight: 700, fontSize: 11 }}>
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
              <td style={{ padding: '11px 14px', minWidth: 280 }}>
                {(() => {
                  const status = entry.reception_status || 'pending'
                  const badge = RECEPTION_LABEL[status] || RECEPTION_LABEL.pending
                  const receivable = status === 'pending' || status === 'partial'
                  // A cancelled order offers only its badge and "reopen": it
                  // cannot be received, sent, paid or un-sent until reopened
                  // (the server refuses each of those too).
                  if (entry.cancelled_at) {
                    return (
                      <span style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
                        <CancelledBadge cancelledAt={entry.cancelled_at} />
                        <CancelPOActions poLogId={entry.id} receptionStatus={status}
                                         paidAt={entry.paid_at} cancelledAt={entry.cancelled_at}
                                         onChanged={onUndone} />
                      </span>
                    )
                  }
                  return (
                    <span style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
                      <span style={{
                        padding: '2px 9px', whiteSpace: 'nowrap', borderRadius: 20, fontSize: 11, fontWeight: 700,
                        background: badge.bg, color: badge.color,
                      }}>
                        {t(badge.labelKey)}
                      </span>
                      {receivable && overdueById[entry.id] && (
                        <>
                          <AttentionChip>{t('attention.chip_arrival_to_confirm')}</AttentionChip>
                          <AttentionChip dot={false}>{t('attention.chip_late_days', { n: overdueById[entry.id].days_overdue })}</AttentionChip>
                        </>
                      )}
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
                          <Truck size={11} aria-hidden="true" /> {t(overdueById[entry.id] ? 'attention.confirm_arrival' : 'po.reception_btn_register')}
                        </button>
                      )}
                      <ApprovalChip approval={entry.approval} />
                      <SendPOButton poLogId={entry.id} suppliersWithoutContact={suppliersWithoutContact}
                                    approval={entry.approval} onSent={onUndone} />
                      {/* The everyday actions (receive, send) stay beside the
                          status; the rest is one tap away so a row is one line
                          tall instead of a stack of eight buttons. */}
                      <MoreRowActions>
                        <ForwardPOActions poLogId={entry.id} approval={entry.approval} />
                        {/* A paid order cannot be un-sent (the server refuses:
                            the invoice is evidence it reached the supplier), so
                            the undo is not offered until the payment is unmarked. */}
                        <UndoPOActions
                          poLogId={entry.id}
                          receptionStatus={status}
                          sent={Boolean(entry.sent_at) && !entry.paid_at}
                          onDone={onUndone}
                        />
                        <PaidPOActions
                          poLogId={entry.id}
                          sent={Boolean(entry.sent_at)}
                          paidAt={entry.paid_at}
                          onChanged={onUndone}
                        />
                        <CancelPOActions
                          poLogId={entry.id}
                          receptionStatus={status}
                          paidAt={entry.paid_at}
                          cancelledAt={entry.cancelled_at}
                          onChanged={onUndone}
                        />
                      </MoreRowActions>
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
