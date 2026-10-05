'use client'
/**
 * `/pedidos` on a phone.
 *
 * Registering a delivery is warehouse work: the person doing it is standing at
 * a pallet with a box in one hand and a phone in the other. So the narrow view
 * is built like a native app's list screen, not like a shrunken table:
 *
 *   · segmented tabs — "Por recibir" (the work), "Ya registrados" (history) and,
 *     for multi-warehouse tenants, "Transferencias"
 *   · one card per order: number, date, size, value and status, with the
 *     reception button right on the card for the orders still waiting — the
 *     one thing this user came for, one tap away
 *   · tapping a card opens the order as a full-height sheet: its lines, when it
 *     was sent and received, and every action the desktop row offers — register
 *     arrival, send to suppliers, forward over WhatsApp, undo a reception or a
 *     send — at thumb size
 *   · "Nueva orden" (a manual PO) as the screen's primary action, pinned above
 *     the tab bar
 *
 * Everything that decides WHICH orders are still open comes from ./shared, and
 * every mutation goes through the same components the desktop table uses
 * (ReceptionModal, SendPOButton, ForwardPOActions, UndoPOActions), so the two
 * views cannot record different things.
 */
import { useEffect, useState } from 'react'
import BulkImportButton from '@/components/inventory/BulkImportButton'
import { ClipboardList, Truck, ShoppingCart, Plus, Package, RefreshCw } from 'lucide-react'
import type { POLogEntry, POItemLine, OverdueReception } from '@/lib/types'
import { getPOItems } from '@/lib/api'
import { formatMoney } from '@/lib/currency'
import { formatPoNumber } from '@/lib/poNumber'
import { ForwardPOActions } from '@/components/po/ForwardPOActions'
import { SendPOButton } from '@/components/po/POHistory'
import { UndoPOActions } from '@/components/po/UndoPOActions'
import { PaidPOActions } from '@/components/po/PaidPOActions'
import { CancelPOActions, CancelledBadge } from '@/components/po/CancelPOActions'
import AttentionChip from '@/components/layout/AttentionChip'
import { EmptyState, ErrorState, LoadingState, SkeletonCards, useErrorDetail } from '@/components/ui/States'
import Spinner from '@/components/ui/Spinner'
import BottomSheet from '@/components/mobile/BottomSheet'
import MobileTabs from '@/components/mobile/MobileTabs'
import StickyActionBar from '@/components/mobile/StickyActionBar'
import { StatusBadge, type StatusTone } from '@/components/mobile/MobileList'
import { useLanguage } from '@/contexts/LanguageContext'
import {
  C, fmtShortDateTime, fmtUnits, isAwaitingReception, receptionStatus, tOr,
} from './shared'

type View = 'awaiting' | 'history' | 'transfers'

interface PedidosMobileProps {
  loading: boolean
  error:   unknown
  onRetry: () => void
  entries: POLogEntry[]
  /** Every supplier the send path would skip (the send confirmation names them). */
  suppliersWithoutContact: string[]
  /** Open orders past their expected arrival, by order id. Shown as a chip on
   *  the card, never as a banner. */
  overdueById: Record<string, OverdueReception>
  onReceive: (poId: string) => void
  /** Reload the list after an action rewrote an order (send, undo). */
  onChanged: () => void
  /** False for a viewer: no reception, no send, no new order. */
  canEdit: boolean
  onCreate: () => void
  multiWarehouse: boolean
  /** The transfers panel, passed as a node rather than reimplemented. */
  transfers: React.ReactNode
}

// The reception status as a badge tone, same colours as the desktop table.
const STATUS_TONE: Record<string, StatusTone> = {
  pending: 'warning', partial: 'info', received: 'success', not_received: 'danger',
}
const STATUS_KEY: Record<string, string> = {
  pending: 'po.reception_pending', partial: 'po.reception_partial',
  received: 'po.reception_received', not_received: 'po.reception_not_received',
}

export default function PedidosMobile(props: PedidosMobileProps) {
  const { t } = useLanguage()
  const {
    loading, error, onRetry, entries, suppliersWithoutContact, overdueById,
    onReceive, onChanged, canEdit, onCreate, multiWarehouse, transfers,
  } = props

  const awaiting = entries.filter(isAwaitingReception)
  const closed   = entries.filter(e => !isAwaitingReception(e))
  const [view, setView] = useState<View>('awaiting')
  const [detailId, setDetailId] = useState<string | null>(null)
  // Re-read from the list on every render, so a reload after an action shows
  // the order's new state inside the open sheet.
  const detail = detailId ? entries.find(e => e.id === detailId) ?? null : null

  const tabs = [
    { id: 'awaiting', label: tOr(t, 'mobile.pedidos_awaiting_title', 'Awaiting arrival'), badge: awaiting.length || undefined },
    { id: 'history',  label: tOr(t, 'mobile.pedidos_history_title', 'Already recorded') },
    ...(multiWarehouse ? [{ id: 'transfers', label: t('transfers.tab_transfers') }] : []),
  ]
  const shown = view === 'awaiting' ? awaiting : closed

  return (
    <div style={{
      // Nothing here may push the document sideways, or every vertical swipe
      // becomes a fight with a scrollbar.
      display: 'flex', flexDirection: 'column', gap: 12,
      maxWidth: '100%', overflowX: 'hidden', minWidth: 0,
    }}>
      <MobileTabs
        ariaLabel={t('transfers.tablist_aria')}
        value={view}
        onChange={v => setView(v as View)}
        tabs={tabs}
      />

      {view === 'transfers' && multiWarehouse ? transfers : (
        <>
          {loading ? (
            <LoadingState label={t('orders.loading_label')}>
              <SkeletonCards count={4} height={92} />
            </LoadingState>
          ) : error ? (
            <ErrorState error={error} onRetry={onRetry} />
          ) : entries.length === 0 ? (
            <EmptyState
              icon={<ClipboardList size={22} />}
              title={t('orders.empty_title')}
              body={t('orders.empty_hint')}
              actions={[{ label: t('orders.go_to_hoy'), href: '/compras', icon: <ShoppingCart size={14} /> }]}
            />
          ) : shown.length === 0 ? (
            <div style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10,
              padding: '36px 16px', textAlign: 'center',
            }}>
              <Package size={28} color={C.dim} aria-hidden="true" />
              <p style={{ margin: 0, fontSize: 14, color: C.muted, lineHeight: 1.5, maxWidth: 300 }}>
                {view === 'awaiting'
                  ? tOr(t, 'mobile.pedidos_awaiting_none', 'Nothing to receive among your recent orders.')
                  : tOr(t, 'mobile.pedidos_history_none', 'No order has been recorded yet.')}
              </p>
            </div>
          ) : (
            <ul
              className="page-enter"
              aria-label={tabs.find(x => x.id === view)?.label}
              style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}
            >
              {shown.map(entry => (
                <OrderCard
                  key={entry.id}
                  entry={entry}
                  overdue={isAwaitingReception(entry) ? overdueById[entry.id] : undefined}
                  onOpen={() => setDetailId(entry.id)}
                  onReceive={canEdit && isAwaitingReception(entry) ? () => onReceive(entry.id) : undefined}
                />
              ))}
            </ul>
          )}

          {/* Only the recent orders are listed (same page size as the desktop
              table); say so instead of letting an old order look missing. */}
          {!loading && !error && entries.length > 0 && (
            <p style={{ margin: '2px 4px 0', fontSize: 12, color: C.dim, lineHeight: 1.5, textAlign: 'center' }}>
              {tOr(t, 'mobile.pedidos_recent_note', 'Showing your most recent orders.')}
            </p>
          )}
        </>
      )}

      {canEdit && view !== 'transfers' && (
        <div style={{ marginTop: 12 }}>
          <BulkImportButton kind="orders" onImported={onChanged} />
        </div>
      )}

      {canEdit && view !== 'transfers' && (
        <StickyActionBar>
          {/* Secondary on purpose: on this screen the cards' "registrar llegada"
              is the job; a new manual order is the occasional one. */}
          <button className="mobile-btn mobile-btn-secondary" onClick={onCreate}>
            <Plus size={18} aria-hidden="true" /> {t('po.manual_new_order')}
          </button>
        </StickyActionBar>
      )}

      <OrderDetailSheet
        entry={detail}
        onClose={() => setDetailId(null)}
        canEdit={canEdit}
        suppliersWithoutContact={suppliersWithoutContact}
        // Close the order first: the reception form is a sheet of its own,
        // and two stacked sheets would fight over Esc and the focus trap.
        onReceive={id => { setDetailId(null); onReceive(id) }}
        onChanged={onChanged}
      />
    </div>
  )
}

// ── One order in the list ────────────────────────────────────────────────────
function OrderCard({ entry, overdue, onOpen, onReceive }: {
  entry: POLogEntry
  overdue?: OverdueReception
  onOpen: () => void
  onReceive?: () => void
}) {
  const { t, lang } = useLanguage()
  const status = receptionStatus(entry)

  return (
    <li style={{
      border: `1px solid ${C.border}`, borderRadius: 14, background: C.surface,
      overflow: 'hidden',
    }}>
      {/* The whole top of the card opens the order; the reception button below
          is its own target, never nested inside this one. */}
      <button
        type="button"
        onClick={onOpen}
        className="tap-feedback"
        aria-label={`${formatPoNumber(entry.po_number)} — ${t(STATUS_KEY[status] ?? STATUS_KEY.pending)}`}
        style={{
          all: 'unset', boxSizing: 'border-box', width: '100%', cursor: 'pointer',
          display: 'flex', alignItems: 'flex-start', gap: 12, padding: '12px 14px', minHeight: 64,
        }}
      >
        <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 16, fontWeight: 700, fontFamily: 'monospace', color: C.text }}>
              {formatPoNumber(entry.po_number)}
            </span>
            <StatusBadge label={t(STATUS_KEY[status] ?? STATUS_KEY.pending)} tone={STATUS_TONE[status] ?? 'warning'} />
          </span>
          <span style={{ fontSize: 13, color: C.muted, overflow: 'hidden', overflowWrap: 'anywhere', }}>
            {fmtShortDateTime(entry.generated_at, lang)} · {skuCountText(t, entry.sku_count)} · {unitCountText(t, entry.total_units)}
          </span>
          {overdue && (
            <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <AttentionChip>{t('attention.chip_arrival_to_confirm')}</AttentionChip>
              <AttentionChip dot={false}>{t('attention.chip_late_days', { n: overdue.days_overdue })}</AttentionChip>
            </span>
          )}
          {(entry.skus_order_now > 0 || entry.skus_order_soon > 0 || entry.source === 'manual') && (
            <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 2 }}>
              {entry.skus_order_now > 0 && (
                <StatusBadge tone="danger" label={countText(t, entry.skus_order_now, 'urgent', '1 urgent', 'urgent')} />
              )}
              {entry.skus_order_soon > 0 && (
                <StatusBadge tone="warning" label={countText(t, entry.skus_order_soon, 'soon', '1 upcoming', 'upcoming')} />
              )}
              {entry.source === 'manual' && (
                <StatusBadge tone="neutral" label={tOr(t, 'mobile.pedidos_manual_badge', 'Manual')} />
              )}
            </span>
          )}
        </span>
        <span style={{ flexShrink: 0, textAlign: 'right', display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ fontSize: 15, fontWeight: 700, color: entry.total_value != null ? C.text : C.dim, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
            {entry.total_value != null ? formatMoney(entry.total_value) : '—'}
          </span>
          <span style={{ fontSize: 11.5, color: entry.sent_at ? C.green : C.dim, whiteSpace: 'nowrap' }}>
            {entry.sent_at
              ? tOr(t, 'mobile.pedidos_sent', 'Sent')
              : tOr(t, 'mobile.pedidos_not_sent', 'Not sent')}
          </span>
        </span>
      </button>

      {onReceive && (
        <div style={{ padding: '0 14px 12px' }}>
          <button
            className="mobile-btn mobile-btn-primary"
            onClick={onReceive}
            style={{ width: '100%' }}
          >
            <Truck size={17} aria-hidden="true" /> {t(overdue ? 'attention.confirm_arrival' : 'po.reception_btn_register')}
          </button>
        </div>
      )}
    </li>
  )
}

// ── The order, full height ───────────────────────────────────────────────────
function OrderDetailSheet({ entry, onClose, canEdit, suppliersWithoutContact, onReceive, onChanged }: {
  entry: POLogEntry | null
  onClose: () => void
  canEdit: boolean
  suppliersWithoutContact: string[]
  onReceive: (id: string) => void
  onChanged: () => void
}) {
  const { t, lang } = useLanguage()
  const errorDetail = useErrorDetail()
  const [lines, setLines] = useState<POItemLine[] | null>(null)
  const [linesError, setLinesError] = useState<string | null>(null)
  // The sheet stays mounted through its exit animation; keep showing the last
  // order rather than an empty sheet sliding away.
  const [shownEntry, setShownEntry] = useState<POLogEntry | null>(entry)
  useEffect(() => { if (entry) setShownEntry(entry) }, [entry])

  const id = entry?.id ?? null
  // Re-fetched when the order's reception state changes, so the received
  // column is current after "registrar llegada" from inside the sheet.
  const statusKey = entry ? `${entry.reception_status ?? ''}|${entry.received_at ?? ''}` : ''
  useEffect(() => {
    if (!id) return
    let cancelled = false
    setLines(null)
    setLinesError(null)
    getPOItems(id)
      .then(res => { if (!cancelled) setLines(res.items.filter(i => i.status === 'approved' || i.status === 'modified')) })
      .catch(e => { if (!cancelled) setLinesError(errorDetail(e) || t('common.error')) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, statusKey])

  const e = entry ?? shownEntry
  if (!e) return null
  const status = receptionStatus(e)
  const awaiting = isAwaitingReception(e)

  return (
    <BottomSheet
      open={!!entry}
      onClose={onClose}
      maxHeight="94dvh"
      title={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontFamily: 'monospace' }}>{formatPoNumber(e.po_number)}</span>
        <StatusBadge label={t(STATUS_KEY[status] ?? STATUS_KEY.pending)} tone={STATUS_TONE[status] ?? 'warning'} />
      </span>}
      footer={canEdit && awaiting ? (
        <button className="mobile-btn mobile-btn-primary" onClick={() => onReceive(e.id)}>
          <Truck size={17} aria-hidden="true" /> {t('po.reception_btn_register')}
        </button>
      ) : undefined}
    >
      {/* Facts: what the desktop row spreads over eight columns */}
      <dl style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, margin: '0 0 16px',
      }}>
        <Fact label={t('roi.col_datetime')} value={fmtShortDateTime(e.generated_at, lang)} />
        <Fact label={t('roi.col_total_value')} value={e.total_value != null ? formatMoney(e.total_value) : '—'} />
        <Fact label={t('roi.col_skus_in_order')} value={String(e.sku_count)} />
        <Fact label={t('roi.col_total_units')} value={fmtUnits(e.total_units)} />
        <Fact
          label={tOr(t, 'mobile.pedidos_sent_label', 'Sent to suppliers')}
          value={e.sent_at ? fmtShortDateTime(e.sent_at, lang) : tOr(t, 'mobile.pedidos_not_sent', 'Not sent')}
        />
        <Fact
          label={tOr(t, 'mobile.pedidos_received_label', 'Received')}
          value={e.received_at ? fmtShortDateTime(e.received_at, lang) : '—'}
        />
      </dl>

      {(e.skus_order_now > 0 || e.skus_order_soon > 0) && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '-6px 0 16px' }}>
          {e.skus_order_now > 0 && <StatusBadge tone="danger" label={countText(t, e.skus_order_now, 'urgent', '1 urgent', 'urgent')} />}
          {e.skus_order_soon > 0 && <StatusBadge tone="warning" label={countText(t, e.skus_order_soon, 'soon', '1 upcoming', 'upcoming')} />}
        </div>
      )}

      {/* The lines — on desktop only visible inside the reception form */}
      <SheetHeading>{tOr(t, 'mobile.pedidos_lines_title', 'Products in this order')}</SheetHeading>
      {!lines && !linesError && (
        <div style={{ padding: 18, display: 'flex', justifyContent: 'center' }}><Spinner size={18} /></div>
      )}
      {linesError && (
        <div role="alert" style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', borderRadius: 10, background: 'rgba(192,80,77,0.08)', fontSize: 13, color: C.red }}>
          <span style={{ flex: 1 }}>{linesError}</span>
          <button
            onClick={() => { setLinesError(null); getPOItems(e.id).then(res => setLines(res.items.filter(i => i.status === 'approved' || i.status === 'modified'))).catch(err => setLinesError(errorDetail(err) || t('common.error'))) }}
            aria-label={t('common.retry')}
            style={{ all: 'unset', cursor: 'pointer', width: 44, height: 44, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          >
            <RefreshCw size={16} aria-hidden="true" />
          </button>
        </div>
      )}
      {lines && (
        <ul style={{ listStyle: 'none', margin: '0 0 18px', padding: 0, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>
          {lines.length === 0 && (
            <li style={{ padding: '14px', fontSize: 13, color: C.dim }}>—</li>
          )}
          {lines.map((l, idx) => {
            const received = l.received_qty ?? 0
            return (
              <li key={l.id} style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
                borderTop: idx === 0 ? 'none' : `1px solid ${C.border}`,
              }}>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block', fontSize: 14, fontWeight: 600, color: C.text, overflow: 'hidden', overflowWrap: 'anywhere', }}>
                    {l.display_name || l.sku}
                  </span>
                  <span style={{ display: 'block', fontSize: 12, color: C.dim, overflow: 'hidden', overflowWrap: 'anywhere', }}>
                    <span style={{ fontFamily: 'monospace' }}>{l.sku}</span>{l.supplier ? ` · ${l.supplier}` : ''}
                  </span>
                </span>
                <span style={{ flexShrink: 0, textAlign: 'right' }}>
                  <span style={{ display: 'block', fontSize: 15, fontWeight: 700, color: C.text, fontVariantNumeric: 'tabular-nums' }}>
                    {fmtUnits(l.final_qty)}
                  </span>
                  {received > 0 && (
                    <span style={{ display: 'block', fontSize: 11.5, color: received >= l.final_qty ? C.green : C.amber, fontVariantNumeric: 'tabular-nums' }}>
                      {tOr(t, 'mobile.pedidos_line_received', `${fmtUnits(received)} received`, { n: fmtUnits(received) })}
                    </span>
                  )}
                </span>
              </li>
            )
          })}
        </ul>
      )}

      {/* Every per-order action the desktop row offers, one per row. */}
      <SheetHeading>{tOr(t, 'mobile.pedidos_actions_title', 'Actions')}</SheetHeading>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {canEdit && (
          <SendPOButton
            key={e.id}
            poLogId={e.id}
            suppliersWithoutContact={suppliersWithoutContact}
            onSent={onChanged}
          />
        )}
        {/* PO actions slot — per-order actions (e.g. "mark as paid", "cancel
            order") render here as full-width `mobile-btn` rows. */}
        <UndoPOActions
          poLogId={e.id}
          receptionStatus={status}
          sent={Boolean(e.sent_at)}
          onDone={onChanged}
        />
        {/* Paid and cancelled — the same components the desktop table uses. */}
        {e.cancelled_at && <CancelledBadge cancelledAt={e.cancelled_at} />}
        {!e.cancelled_at && (e.sent_at || e.paid_at) && (
          <PaidPOActions poLogId={e.id} sent={Boolean(e.sent_at)}
                         paidAt={e.paid_at} onChanged={onChanged} />
        )}
        <CancelPOActions poLogId={e.id} receptionStatus={status}
                         paidAt={e.paid_at} cancelledAt={e.cancelled_at}
                         onChanged={onChanged} />
      </div>

      <div style={{
        marginTop: 16, padding: 12, borderRadius: 12,
        background: C.card, border: `1px solid ${C.border}`,
      }}>
        <p style={{ margin: '0 0 10px', fontSize: 12.5, color: C.dim, lineHeight: 1.5 }}>
          {t('po.forward_hint')}
        </p>
        {/* Shared with the desktop table on purpose — the WhatsApp payload and
            the wa.me link must be the same message wherever it is sent from. */}
        <ForwardPOActions key={e.id} poLogId={e.id} />
      </div>
    </BottomSheet>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ minWidth: 0, padding: '10px 12px', borderRadius: 10, background: C.card, border: `1px solid ${C.border}` }}>
      <dt style={{ fontSize: 11.5, color: C.dim, overflowWrap: 'anywhere' }}>{label}</dt>
      <dd style={{ margin: '3px 0 0', fontSize: 14.5, fontWeight: 700, color: C.text, fontVariantNumeric: 'tabular-nums', overflowWrap: 'anywhere' }}>{value}</dd>
    </div>
  )
}

function SheetHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 style={{
      margin: '0 0 8px', fontSize: 12, fontWeight: 700, color: C.dim,
      textTransform: 'uppercase', letterSpacing: '0.07em',
    }}>
      {children}
    </h3>
  )
}

// ── Counts that agree in number ──────────────────────────────────────────────
function skuCountText(t: (k: string, p?: Record<string, unknown>) => string, n: number): string {
  return n === 1
    ? tOr(t, 'mobile.pedidos_card_skus_one', '1 SKU')
    : tOr(t, 'mobile.pedidos_card_skus', `${n} SKUs`, { n })
}

function unitCountText(t: (k: string, p?: Record<string, unknown>) => string, n: number): string {
  return n === 1
    ? tOr(t, 'mobile.pedidos_card_units_one', '1 unit')
    : tOr(t, 'mobile.pedidos_card_units', `${fmtUnits(n)} units`, { n: fmtUnits(n) })
}

function countText(
  t: (k: string, p?: Record<string, unknown>) => string,
  n: number, kind: 'urgent' | 'soon', oneFallback: string, manyFallback: string,
): string {
  return n === 1
    ? tOr(t, `mobile.pedidos_chip_${kind}_one`, oneFallback)
    : tOr(t, `mobile.pedidos_chip_${kind}`, `${n} ${manyFallback}`, { n })
}
