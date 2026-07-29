'use client'
/**
 * `/pedidos` on a phone.
 *
 * Registering a delivery is warehouse work. The person doing it is standing at
 * a pallet with a box in one hand and a phone in the other — not at a desk. The
 * desktop screen answers that with an eight-column table whose "registrar
 * llegada" button is the last cell on the right, which on a 375px viewport puts
 * the one action this user came for behind a horizontal scroll.
 *
 * So the narrow view inverts the page instead of shrinking it: the orders still
 * waiting for goods come first, one card each, with a full-width reception
 * button reachable in a single tap. Everything already closed drops to a
 * compact list underneath, where it is history rather than work.
 *
 * Deliberately left on the desktop screen:
 *   · creating a manual order — a grid of SKU / quantity / unit-cost inputs,
 *     which is typing, and typing is what the desk is for
 *   · "enviar pedido" to the suppliers — it previews who receives it and who
 *     gets skipped for missing contact data, then asks for confirmation. That
 *     is a decision to read, not to thumb through in a warehouse aisle
 *   · the numeric columns of the history table (adoption counts, per-column
 *     breakdowns), summarised here to what identifies the order
 *
 * Forwarding the order over WhatsApp does NOT stay behind: it is the one action
 * that is genuinely better from the phone already in the buyer's hand.
 *
 * Everything that decides WHICH orders are still open comes from ./shared, so
 * this list and the desktop table can never disagree about that.
 */
import { useState } from 'react'
import {
  ClipboardList, Truck, Share2, ChevronDown, ChevronUp, ShoppingCart, Monitor,
} from 'lucide-react'
import type {
  POLogEntry, SupplierContactHealthRow, SupplierLeadTimeAlert,
} from '@/lib/types'
import { formatMoney } from '@/lib/currency'
import { formatPoNumber } from '@/lib/poNumber'
import { ForwardPOActions } from '@/components/po/ForwardPOActions'
import {
  SupplierContactHealthBanner, SupplierLeadTimeAlertBanner,
} from '@/components/suppliers/SupplierHealthBanners'
import { EmptyState, ErrorState, LoadingState, SkeletonCards } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import {
  C, fmtShortDateTime, fmtUnits, isAwaitingReception, receptionBadge,
  receptionStatus, tOr,
} from './shared'

// Thumb-sized: 44px is the smallest control a finger hits reliably. Same
// constant, same reason, as HoyMobile.
const TAP = 44

interface PedidosMobileProps {
  loading: boolean
  error:   unknown
  onRetry: () => void
  entries: POLogEntry[]
  /** Already narrowed to suppliers on an open order — see ./shared. */
  contactHealth:  SupplierContactHealthRow[]
  leadTimeAlerts: SupplierLeadTimeAlert[]
  onReceive: (poId: string) => void
  multiWarehouse: boolean
  tab:    'orders' | 'transfers'
  onTab:  (tab: 'orders' | 'transfers') => void
  /** The transfers panel, passed as a node rather than reimplemented: it is
   *  already a card list, so it survives a narrow viewport as it is. */
  transfers: React.ReactNode
}

export default function PedidosMobile(props: PedidosMobileProps) {
  const { t } = useLanguage()
  const {
    loading, error, onRetry, entries, contactHealth, leadTimeAlerts,
    onReceive, multiWarehouse, tab, onTab, transfers,
  } = props

  const awaiting = entries.filter(isAwaitingReception)
  const closed   = entries.filter(e => !isAwaitingReception(e))

  return (
    <div style={{
      // The shell already pads narrow viewports (AppShell). The guard below is
      // the same one HoyMobile carries: nothing here may push the document
      // sideways, or every vertical swipe becomes a fight with a scrollbar.
      display: 'flex', flexDirection: 'column', gap: 14,
      maxWidth: '100%', overflowX: 'hidden', paddingBottom: 24,
    }}>

      {/* ── Header ──
          No "Pedidos" title here: the top bar already names the route, and on a
          phone the first screenful is the scarcest thing on the page — spending
          it on a word that is already visible two rows above buys nothing. How
          many deliveries are still outstanding is the headline instead. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <p style={{ margin: 0, flex: 1, minWidth: 0, fontSize: 12, color: C.dim, lineHeight: 1.45 }}>
          {t('orders.page_subtitle')}
        </p>
        {awaiting.length > 0 && (
          <span style={{
            flexShrink: 0,
            fontSize: 12, fontWeight: 700, padding: '4px 11px', borderRadius: 20,
            background: 'rgba(245,158,11,0.1)', color: C.amber,
          }}>
            {awaiting.length} {t('orders.pending_suffix')}
          </span>
        )}
      </div>

      {/* Orders vs. transfers — only ever shown to multi-warehouse tenants */}
      {multiWarehouse && (
        <div role="tablist" aria-label={t('transfers.tablist_aria')} style={{ display: 'flex', gap: 6 }}>
          <button role="tab" aria-selected={tab === 'orders'} onClick={() => onTab('orders')}
                  style={mobileTabStyle(tab === 'orders')}>{t('transfers.tab_orders')}</button>
          <button role="tab" aria-selected={tab === 'transfers'} onClick={() => onTab('transfers')}
                  style={mobileTabStyle(tab === 'transfers')}>{t('transfers.tab_transfers')}</button>
        </div>
      )}

      {tab === 'transfers' && multiWarehouse ? transfers : (
        <>
          {/* The two supplier-health warnings survive the trip to the phone:
              they are the reason an order may never reach the supplier, which
              is worth more here than on a screen the buyer visits weekly. */}
          <SupplierContactHealthBanner rows={contactHealth} />
          <SupplierLeadTimeAlertBanner alerts={leadTimeAlerts} />

          {loading ? (
            <LoadingState label={t('orders.loading_label')}>
              <SkeletonCards count={3} height={120} />
            </LoadingState>
          ) : error ? (
            <ErrorState error={error} onRetry={onRetry} />
          ) : entries.length === 0 ? (
            <EmptyState
              icon={<ClipboardList size={22} />}
              title={t('orders.empty_title')}
              body={t('orders.empty_hint')}
              actions={[{ label: t('orders.go_to_hoy'), href: '/hoy', icon: <ShoppingCart size={14} /> }]}
            />
          ) : (
            // The skeleton above already has this shape, so fading the cards in
            // reads as the placeholder becoming the data, not as a blink.
            <div className="page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>

              {/* ── The job: orders whose goods have not arrived ── */}
              <Section
                color={C.amber}
                title={tOr(t, 'mobile.pedidos_awaiting_title', 'Awaiting arrival')}
              >
                {awaiting.length === 0 ? (
                  <p style={{ margin: 0, fontSize: 12.5, color: C.dim, lineHeight: 1.5 }}>
                    {tOr(t, 'mobile.pedidos_awaiting_none',
                      'Nothing to receive: every order you generated has already been recorded as arrived.')}
                  </p>
                ) : awaiting.map(entry => (
                  <OrderCard key={entry.id} entry={entry} onReceive={() => onReceive(entry.id)} />
                ))}
              </Section>

              {/* ── Already closed: history, not work ── */}
              {closed.length > 0 && (
                <Section
                  color={C.dim}
                  title={tOr(t, 'mobile.pedidos_history_title', 'Already recorded')}
                >
                  {closed.map(entry => <ClosedRow key={entry.id} entry={entry} />)}
                </Section>
              )}
            </div>
          )}

          {/* Says plainly what this screen does not do, instead of letting the
              buyer hunt for a button that was never rendered. */}
          <div style={{
            display: 'flex', alignItems: 'flex-start', gap: 8,
            marginTop: 6, paddingTop: 14, borderTop: `1px solid ${C.border}`,
          }}>
            <Monitor size={14} color={C.dim} style={{ flexShrink: 0, marginTop: 2 }} aria-hidden="true" />
            <p style={{ margin: 0, fontSize: 11.5, color: C.dim, lineHeight: 1.55 }}>
              {tOr(t, 'mobile.pedidos_desktop_hint',
                'This is the phone version: record arrivals and forward orders. Creating an order by hand and sending it to your suppliers are on the desktop screen.')}
            </p>
          </div>
        </>
      )}
    </div>
  )
}

// ── One order still waiting for goods ────────────────────────────────────────
function OrderCard({ entry, onReceive }: { entry: POLogEntry; onReceive: () => void }) {
  const { t, lang } = useLanguage()
  const [sharing, setSharing] = useState(false)

  const status = receptionStatus(entry)
  const badge  = receptionBadge(status)

  return (
    <div style={{
      border: `1px solid ${C.border}`, borderRadius: 12,
      background: C.surface, padding: 14, marginBottom: 10,
    }}>

      {/* Which order, and how far along it is */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{
          fontSize: 15, fontWeight: 700, fontFamily: 'monospace', color: C.text,
        }}>
          {formatPoNumber(entry.po_number)}
        </span>
        <span style={{
          padding: '3px 9px', borderRadius: 20, fontSize: 11, fontWeight: 700,
          background: badge.bg, color: badge.color,
        }}>
          {t(badge.labelKey)}
        </span>
      </div>

      <div style={{ fontSize: 12, color: C.dim, marginTop: 5 }}>
        {fmtShortDateTime(entry.generated_at, lang)}
      </div>

      {/* What is inside it — the numbers that identify a pallet */}
      <div style={{ fontSize: 12.5, color: C.muted, marginTop: 6, lineHeight: 1.5, overflowWrap: 'anywhere' }}>
        {skuCountText(t, entry.sku_count)} · {unitCountText(t, entry.total_units)}
        {entry.total_value != null && (
          <> · <span style={{ color: C.green, fontWeight: 700 }}>{formatMoney(entry.total_value)}</span></>
        )}
      </div>

      {(entry.skus_order_now > 0 || entry.skus_order_soon > 0) && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
          {entry.skus_order_now > 0 && (
            <Chip color={C.red} bg="rgba(239,68,68,0.1)">
              {countText(t, entry.skus_order_now, 'urgent', '1 urgent', 'urgent')}
            </Chip>
          )}
          {entry.skus_order_soon > 0 && (
            <Chip color={C.amber} bg="rgba(245,158,11,0.1)">
              {countText(t, entry.skus_order_soon, 'soon', '1 upcoming', 'upcoming')}
            </Chip>
          )}
        </div>
      )}

      {/* The one thing this screen exists for, one tap away */}
      <button
        onClick={onReceive}
        style={{
          all: 'unset', boxSizing: 'border-box', cursor: 'pointer', width: '100%',
          minHeight: TAP, marginTop: 12, borderRadius: 10,
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
          background: 'var(--accent)', color: '#fff', fontSize: 14.5, fontWeight: 700,
        }}
      >
        <Truck size={16} aria-hidden="true" /> {t('po.reception_btn_register')}
      </button>

      {/* Forwarding is secondary, so it is a tap away rather than three more
          buttons competing with the primary one. */}
      <button
        onClick={() => setSharing(v => !v)}
        aria-expanded={sharing}
        style={{
          all: 'unset', boxSizing: 'border-box', cursor: 'pointer', width: '100%',
          minHeight: 38, marginTop: 4, display: 'flex', alignItems: 'center',
          justifyContent: 'center', gap: 6, fontSize: 12.5, color: C.dim,
        }}
      >
        <Share2 size={13} aria-hidden="true" />
        {tOr(t, 'mobile.pedidos_share_toggle', 'Forward this order')}
        {sharing ? <ChevronUp size={13} aria-hidden="true" /> : <ChevronDown size={13} aria-hidden="true" />}
      </button>

      {sharing && (
        <div style={{
          background: C.card, border: `1px solid ${C.border}`, borderRadius: 9,
          padding: 12, marginTop: 4,
        }}>
          <p style={{ margin: '0 0 9px', fontSize: 11.5, color: C.dim, lineHeight: 1.5 }}>
            {t('po.forward_hint')}
          </p>
          {/* Shared with the desktop table on purpose — the WhatsApp payload and
              the wa.me link must be the same message wherever it is sent from. */}
          <ForwardPOActions poLogId={entry.id} />
        </div>
      )}
    </div>
  )
}

// ── An order already recorded: nothing left to decide ────────────────────────
function ClosedRow({ entry }: { entry: POLogEntry }) {
  const { t, lang } = useLanguage()
  const badge = receptionBadge(receptionStatus(entry))

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 9, marginBottom: 6,
      padding: '10px 12px', borderRadius: 10,
      background: C.card, border: `1px solid ${C.border}`,
    }}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 12.5, fontWeight: 700, fontFamily: 'monospace', color: C.text }}>
          {formatPoNumber(entry.po_number)}
        </div>
        <div style={{ fontSize: 11, color: C.dim, marginTop: 2, overflowWrap: 'anywhere' }}>
          {fmtShortDateTime(entry.generated_at, lang)} · {unitCountText(t, entry.total_units)}
        </div>
      </div>
      <span style={{
        flexShrink: 0, padding: '3px 9px', borderRadius: 20, fontSize: 10.5, fontWeight: 700,
        background: badge.bg, color: badge.color,
      }}>
        {t(badge.labelKey)}
      </span>
    </div>
  )
}

// A real <h2>: with the page title left to the top bar, these are the only
// headings the content itself has, and a screen reader needs them to be
// headings rather than styled divs.
function Section({ color, title, children }: {
  color: string
  title: string
  children: React.ReactNode
}) {
  return (
    <div>
      <h2 style={{
        margin: '0 0 9px', fontSize: 11, fontWeight: 700, color,
        textTransform: 'uppercase', letterSpacing: '0.07em',
      }}>
        {title}
      </h2>
      {children}
    </div>
  )
}

// ── Counts that agree in number ──────────────────────────────────────────────
// "1 SKUs · 729 unidades" is the kind of sentence that makes a product feel
// machine-generated, and this card is read while someone counts boxes.
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

/** The desktop shows these two as column headers over a bare number, so their
 *  plural never had to agree. As a chip it does. */
function countText(
  t: (k: string, p?: Record<string, unknown>) => string,
  n: number, kind: 'urgent' | 'soon', oneFallback: string, manyFallback: string,
): string {
  return n === 1
    ? tOr(t, `mobile.pedidos_chip_${kind}_one`, oneFallback)
    : tOr(t, `mobile.pedidos_chip_${kind}`, `${n} ${manyFallback}`, { n })
}

function Chip({ color, bg, children }: { color: string; bg: string; children: React.ReactNode }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center',
      padding: '3px 9px', borderRadius: 20,
      fontSize: 11, fontWeight: 700, background: bg, color,
    }}>
      {children}
    </span>
  )
}

// Wider and taller than the desktop tab: it is hit with a thumb here.
const mobileTabStyle = (active: boolean): React.CSSProperties => ({
  all: 'unset', boxSizing: 'border-box', cursor: 'pointer',
  minHeight: 38, padding: '0 14px', borderRadius: 8,
  display: 'flex', alignItems: 'center',
  fontSize: 12.5, fontWeight: 600,
  background: active ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : 'transparent',
  color: active ? 'var(--accent)' : C.dim,
})
