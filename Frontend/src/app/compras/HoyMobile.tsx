'use client'
/**
 * `/compras` (the Panel) on a phone.
 *
 * The buyer this product is for does not sit at a desk while deciding what to
 * order — they walk the warehouse with a phone in one hand. The desktop screen
 * is a dense table with fixed widths inside inline styles, which at 390px is
 * not "cramped", it is unusable. So this is ONE screen built for the narrow
 * case, shaped like a native daily list:
 *
 *   · today at a glance — greeting, data freshness, the four counters
 *   · the work — one card per SKU to decide, with a thumb-sized stepper and a
 *     single add/remove toggle; everything else about the line (why, supplier,
 *     "no pedir") one tap away in a detail sheet
 *   · the cart — a bar pinned above the tab bar with the total; tapping it
 *     opens the order review (lines, destination warehouse, price breaks, the
 *     cash check) with "generate" as its primary action
 *   · right after generating, a sheet to send the order to its suppliers or
 *     forward it over WhatsApp from this very phone
 *   · the rest of the briefing (narrative, transfers, optimizer plan, demand
 *     peaks and changes, recommendations, overstock) below the work, rendered
 *     by the page as phone-sized sections
 *
 * It is a separate component on purpose: it never renders on desktop and the
 * desktop path never renders it.
 *
 * What it must NOT drop, and does not:
 *   · the stale-data banner and a per-card "sin verificar" chip while the
 *     semáforo is degraded
 *   · the KPI caveat under the counters, and "—" instead of 0 when nothing
 *     was counted or no cost is on file
 *   · the assumptions banner and the "estimado" badge on every value StockAI
 *     guessed instead of receiving
 *   · the uncosted-lines and margin caveats on the cart
 * All of them come from ./shared, so the two views cannot drift apart.
 */
import { useState } from 'react'
import Link from 'next/link'
import {
  AlertTriangle, Clock, Truck, ArrowRight, Minus, Plus, Check, RefreshCw, Send,
  ChevronRight, ChevronUp, ShoppingCart, RotateCcw,
} from 'lucide-react'
import type {
  MorningBriefing, POLogEntry, OverdueReception, Supplier, SupplierContactHealthRow,
  SupplierLeadTimeAlert, SendPOResult, Warehouse,
} from '@/lib/types'
import type { DataFreshnessInfo } from '@/lib/api'
import { formatMoney, formatMoneyCompact } from '@/lib/currency'
import { coverageUnitLabel } from '@/lib/period'
import { renderExplanation } from '@/lib/explanationCopy'
import SignalBadge from '@/components/ui/SignalBadge'
import StaleDataBanner, { StaleSignalChip } from '@/components/ui/StaleDataBanner'
import { ErrorState, LoadingState, SkeletonCards, useErrorDetail } from '@/components/ui/States'
import { ForwardPOActions } from '@/components/po/ForwardPOActions'
import {
  SupplierContactHealthBanner, SupplierLeadTimeAlertBanner,
} from '@/components/suppliers/SupplierHealthBanners'
import BottomSheet from '@/components/mobile/BottomSheet'
import { useLanguage } from '@/contexts/LanguageContext'
import { fmtNum } from '@/lib/numberLocale'
import {
  C, AllClear, AssumptionsBanner, SourceBadge, provenanceText, summarizeAssumptions,
  tOr, type ActionItem, IncomingNote, OrderedNote,
} from './shared'

// Thumb-sized: 44px is the smallest control a finger hits reliably. Every
// tappable thing on this screen is at least this tall.
const TAP = 44

interface HoyMobileProps {
  loading:   boolean
  error:     unknown
  onRetry:   () => void
  briefing:  MorningBriefing | null
  firstName: string | null
  freshness: DataFreshnessInfo | null
  /** The desktop's data-freshness chip, passed in rather than reimplemented. */
  freshnessChip?: React.ReactNode
  semaphoreStale: boolean
  cart:      ActionItem[]
  approved:  ActionItem[]
  onApprove: (sku: string) => void
  /** Takes a line back out of the order without rejecting it — the second tap
   *  of the card's single toggle button. */
  onRemove:  (sku: string) => void
  /** "No pedir": logged as adoption feedback, like the desktop's Reject. */
  onReject:  (sku: string) => void
  onChangeQty: (sku: string, qty: number) => void
  suppliers: Supplier[]
  onChangeSupplier: (sku: string, supplierId: string) => void
  onClearCart: () => void
  onGenerate:  () => void
  /** True while the order is being saved: the generate button is disabled so
   *  a second tap cannot start a second order. */
  generating?: boolean
  /** False for a viewer. The narrow layout makes the same promises as the
   *  desktop one, so the decision surface disappears here too. */
  canDecide:   boolean
  // Destination warehouse for the cart (multi-warehouse tenants only).
  multiWarehouse: boolean
  warehouses: Warehouse[]
  destWarehouse: string
  onDestWarehouse: (name: string) => void
  // Generate → send in one flow.
  generatedPO: POLogEntry | null
  generatedLines: ActionItem[]
  sendState: 'idle' | 'sending' | 'done'
  sendResult: SendPOResult | null
  sendError: unknown
  onSendNow: () => void
  sendReason: (reason: string) => string
  onDismissGenerated: () => void
  pendingReceptions: number
  overduePOs: OverdueReception[]
  /** Opens the reception form for an overdue order (null for a viewer). */
  onReceive: ((poLogId: string) => void) | null
  leadTimeAlerts: SupplierLeadTimeAlert[]
  contactHealth: SupplierContactHealthRow[]
  /** Rendered when the session trained but no stock exists — the desktop's own
   *  empty state, passed in rather than reimplemented. */
  noInventory: React.ReactNode
  /** Narrative, transfer suggestions — above the work. */
  intro?: React.ReactNode
  /** Price breaks and the cash check, shown in the cart review sheet. */
  cartPanels?: React.ReactNode
  /** Optimizer plan, demand peaks/changes, recommendations, overstock. */
  extras?: React.ReactNode
  loadedAtText?: string | null
}

export default function HoyMobile(props: HoyMobileProps) {
  const { t, lang } = useLanguage()
  const {
    loading, error, onRetry, briefing, firstName, freshness, freshnessChip, semaphoreStale,
    cart, approved, onApprove, onRemove, onReject, onChangeQty, suppliers, onChangeSupplier,
    onClearCart, onGenerate, generating = false, canDecide,
    multiWarehouse, warehouses, destWarehouse, onDestWarehouse,
    generatedPO, generatedLines, sendState, sendResult, sendError, onSendNow, sendReason,
    onDismissGenerated, pendingReceptions, overduePOs, onReceive,
    leadTimeAlerts, contactHealth, noInventory, intro, cartPanels, extras, loadedAtText,
  } = props

  const [detailSku, setDetailSku] = useState<string | null>(null)
  const [cartOpen, setCartOpen] = useState(false)
  const detail = detailSku ? cart.find(i => i.sku === detailSku) ?? null : null

  const urgent = cart.filter(i => i.signal === 'PEDIR_YA')
  const soon   = cart.filter(i => i.signal === 'PEDIR_PRONTO')
  const kpis   = briefing?.kpis
  const uncounted = kpis?.sin_datos ?? 0
  const nothingCounted = uncounted > 0 && uncounted >= (kpis?.total_skus ?? 0)
  const noCostOnFile = kpis?.valued_skus === 0 && (kpis?.total_skus ?? 0) > 0
  const pendingDecisions = cart.filter(i => i.status === 'pending' && i.qty > 0).length

  const cardProps = (item: ActionItem) => ({
    item, briefing: briefing!, stale: semaphoreStale, canDecide,
    onApprove: () => onApprove(item.sku),
    onRemove: () => onRemove(item.sku),
    onRestore: () => onApprove(item.sku),
    onChangeQty: (q: number) => onChangeQty(item.sku, q),
    onOpen: () => setDetailSku(item.sku),
  })

  return (
    <div style={{
      background: C.bg, minHeight: '100%', padding: '2px 0 0',
      // Guard: nothing on this screen may push the document sideways.
      maxWidth: '100%', overflowX: 'hidden', minWidth: 0,
    }}>

      {/* ── Greeting ── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 14 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h1 style={{ fontSize: 21, fontWeight: 700, color: C.text, margin: '0 0 3px', lineHeight: 1.25 }}>
            {t('hoy.greeting_good_morning')}{firstName ? `, ${firstName}` : ''}.
          </h1>
          <p style={{ fontSize: 13, color: C.dim, margin: 0, lineHeight: 1.45 }}>
            {briefing
              ? <>
                  {new Date(`${briefing.date}T12:00:00`).toLocaleDateString(lang === 'en' ? 'en-US' : 'es-CR', { weekday: 'long', day: 'numeric', month: 'long' })}
                  {pendingDecisions > 0 && <> · {tOr(t, 'mobile.hoy_pending_decisions', `${pendingDecisions} to decide`, { n: pendingDecisions })}</>}
                </>
              : t('hoy.date_loading')}
          </p>
        </div>
      </div>
      {freshnessChip && <div style={{ marginBottom: 14, maxWidth: '100%', overflowX: 'auto' }}>{freshnessChip}</div>}

      {loading && (
        <LoadingState label={t('hoy.loading_label')}>
          <SkeletonCards count={4} height={110} />
        </LoadingState>
      )}

      {!loading && error != null && <ErrorState error={error} onRetry={onRetry} />}

      {!loading && briefing && !briefing.has_data && noInventory}

      {!loading && briefing && briefing.has_data && (
        <>
          {/* Above everything else, exactly as on desktop: it is not one more
              alert, it is the caveat that applies to all of them. */}
          {freshness && <StaleDataBanner freshness={freshness} />}

          <AssumptionsBanner summary={summarizeAssumptions(briefing)} stacked />

          {leadTimeAlerts.length > 0 && (
            <div style={{ marginBottom: 12 }}><SupplierLeadTimeAlertBanner alerts={leadTimeAlerts} /></div>
          )}

          {/* ── Counters: 2×2, the four the desktop row shows ── */}
          {kpis && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
              <MiniKpi label={t('hoy.kpi_risk_today')}
                value={nothingCounted ? '—' : String(kpis.order_now)}
                color={kpis.order_now > 0 ? C.red : C.text} />
              <MiniKpi label={t('hoy.kpi_this_week')}
                value={nothingCounted ? '—' : String(kpis.order_soon)}
                color={kpis.order_soon > 0 ? C.amber : C.text} />
              <MiniKpi label={t('hoy.kpi_total_skus')} value={String(kpis.total_skus)} color={C.text} />
              <MiniKpi label={t('hoy.kpi_inventory_value')}
                value={(nothingCounted || noCostOnFile) ? '—' : formatMoneyCompact(kpis.total_inventory_value)}
                color={C.text} />
            </div>
          )}
          {semaphoreStale && (
            <p style={{ fontSize: 12, color: C.dim, margin: '0 0 10px', lineHeight: 1.55 }}>
              {t('freshness.kpi_caveat')}
            </p>
          )}
          {uncounted > 0 && kpis && (
            <p style={{ fontSize: 12, color: C.dim, margin: '0 0 10px', lineHeight: 1.55 }}>
              {(nothingCounted ? t('hoy.kpi_nothing_counted') : t('hoy.kpi_partially_counted'))
                .replace('{count}', String(uncounted))
                .replace('{total}', String(kpis.total_skus))}
            </p>
          )}

          {/* ── Arrivals: receiving goods is the other thing done phone in hand ── */}
          {overduePOs.length > 0 && (
            <DailySection
              color={C.red}
              icon={<AlertTriangle size={13} aria-hidden="true" />}
              title={t('hoy.overdue_section_title')}
            >
              <ul style={listReset}>
                {overduePOs.map(o => (
                  <li key={`${o.po_log_id}-${o.supplier}`} style={{
                    display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px',
                    borderRadius: 12, marginBottom: 8, minHeight: 56, boxSizing: 'border-box',
                    background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.25)',
                  }}>
                    <span style={{ flex: 1, minWidth: 0, fontSize: 13.5, color: C.text, lineHeight: 1.45 }}>
                      {t('hoy.overdue_line_prefix')} <strong>{o.supplier}</strong>{' '}
                      {t('hoy.overdue_line_suffix')} <strong>{o.days_overdue}</strong> {t('hoy.overdue_days_ago_suffix')}
                    </span>
                    {onReceive && (
                      <button
                        onClick={() => onReceive(o.po_log_id)}
                        aria-label={`${t('hoy.overdue_cta')} — ${o.supplier}`}
                        style={{
                          all: 'unset', boxSizing: 'border-box', cursor: 'pointer', flexShrink: 0,
                          minHeight: TAP, padding: '0 12px', borderRadius: 10,
                          display: 'flex', alignItems: 'center', gap: 6,
                          fontSize: 13, fontWeight: 700, color: C.red, border: `1px solid ${C.red}55`,
                        }}
                      >
                        <Truck size={15} aria-hidden="true" /> {t('hoy.overdue_cta')}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </DailySection>
          )}
          {pendingReceptions > 0 && (
            <Link href="/pedidos" className="tap-feedback" style={{
              display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14,
              padding: '10px 12px', borderRadius: 12, minHeight: 52, boxSizing: 'border-box',
              background: 'color-mix(in srgb, var(--accent) 6%, transparent)',
              border: '1px solid color-mix(in srgb, var(--accent) 25%, transparent)',
              textDecoration: 'none',
            }}>
              <Truck size={17} color={C.indigo} style={{ flexShrink: 0 }} aria-hidden="true" />
              <span style={{ fontSize: 13.5, color: C.text, flex: 1, lineHeight: 1.4 }}>
                <strong>{pendingReceptions}</strong>{' '}
                {pendingReceptions === 1 ? t('hoy.receptions_pending_singular') : t('hoy.receptions_pending_plural')}
              </span>
              <ChevronRight size={18} color={C.indigo} aria-hidden="true" />
            </Link>
          )}

          {intro}

          {/* ── The work ── */}
          {urgent.length > 0 && (
            <DailySection
              color="var(--signal-order-now-fg)"
              icon={<AlertTriangle size={13} aria-hidden="true" />}
              title={t('hoy.section_urgent')}
              count={urgent.filter(i => i.status !== 'rejected').length}
            >
              {urgent.map(item => <MobileActionCard key={item.sku} {...cardProps(item)} />)}
            </DailySection>
          )}

          {soon.length > 0 && (
            <DailySection
              color="var(--signal-order-soon-fg)"
              icon={<Clock size={13} aria-hidden="true" />}
              title={t('hoy.section_this_week')}
              count={soon.filter(i => i.status !== 'rejected').length}
            >
              {soon.map(item => <MobileActionCard key={item.sku} {...cardProps(item)} />)}
            </DailySection>
          )}

          {(cart.length === 0 || cart.every(i => i.status === 'rejected')) && (
            <AllClear stale={semaphoreStale} unmeasured={nothingCounted} />
          )}

          {contactHealth.length > 0 && (
            <div style={{ marginBottom: 14 }}><SupplierContactHealthBanner rows={contactHealth} /></div>
          )}

          {extras}

          {/* ── Footer ── */}
          <div style={{
            marginTop: 20, paddingTop: 14, borderTop: `1px solid ${C.border}`,
            display: 'flex', flexDirection: 'column', gap: 10,
          }}>
            {loadedAtText && (
              <p style={{ fontSize: 12, color: C.dim, margin: 0, textAlign: 'center' }}>
                {t('hoy.footer_last_update')}: {loadedAtText}
                {briefing.session_name && <> · {t('hoy.footer_session')}: {briefing.session_name}</>}
              </p>
            )}
            <div style={{ display: 'flex', gap: 10 }}>
              <button className="mobile-btn mobile-btn-secondary" onClick={onRetry}>
                <RefreshCw size={16} aria-hidden="true" /> {t('hoy.btn_refresh_data')}
              </button>
              <Link href="/inventario" className="mobile-btn mobile-btn-secondary" style={{ textDecoration: 'none' }}>
                <ShoppingCart size={16} aria-hidden="true" /> {tOr(t, 'mobile.hoy_inventory_link', 'Inventory')}
              </Link>
            </div>
          </div>
        </>
      )}

      {/* ── Sticky cart ── */}
      {approved.length > 0 && (
        <>
          {/* In-flow room for the fixed bar, so the last card is never under it. */}
          <div aria-hidden="true" style={{ height: 92 }} />
          <MobileCartBar
            approved={approved}
            onOpen={() => setCartOpen(true)}
            onGenerate={onGenerate}
            generating={generating}
          />
        </>
      )}

      {/* ── One line, in full ── */}
      <LineDetailSheet
        item={detail}
        briefing={briefing}
        onClose={() => setDetailSku(null)}
        canDecide={canDecide}
        suppliers={suppliers}
        onChangeSupplier={id => detail && onChangeSupplier(detail.sku, id)}
        onReject={() => { if (detail) { onReject(detail.sku); setDetailSku(null) } }}
        onRestore={() => detail && onApprove(detail.sku)}
      />

      {/* ── The order, before it is generated ── */}
      <BottomSheet
        open={cartOpen && approved.length > 0}
        onClose={() => setCartOpen(false)}
        maxHeight="94dvh"
        title={tOr(t, 'mobile.hoy_cart_title', 'Your order')}
        footer={<>
          <button className="mobile-btn mobile-btn-secondary" onClick={() => { onClearCart(); setCartOpen(false) }} disabled={generating}>
            {t('hoy.btn_clear')}
          </button>
          <button
            className="mobile-btn"
            onClick={() => { onGenerate(); setCartOpen(false) }}
            disabled={generating}
            aria-busy={generating}
            style={{ background: '#22c55e', color: '#fff', flex: 2 }}
          >
            {generating ? t('hoy.btn_download_po_busy') : t('hoy.btn_download_po')}
          </button>
        </>}
      >
        <CartSummary approved={approved} />
        <ul style={{ ...listReset, margin: '12px 0', border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>
          {approved.map((i, idx) => (
            <li key={i.sku} style={{
              display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px',
              borderTop: idx === 0 ? 'none' : `1px solid ${C.border}`,
            }}>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontSize: 14, fontWeight: 600, color: C.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{i.name}</span>
                <span style={{ display: 'block', fontSize: 12, color: C.dim, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {i.supplier || t('hoy.cart_supplier_none')}
                </span>
              </span>
              <span style={{ flexShrink: 0, textAlign: 'right' }}>
                <span style={{ display: 'block', fontSize: 15, fontWeight: 700, color: C.text, fontVariantNumeric: 'tabular-nums' }}>{fmtNum(i.qty)}</span>
                <span style={{ display: 'block', fontSize: 11.5, color: C.dim }}>
                  {i.unit_cost != null ? formatMoney(i.qty * i.unit_cost) : '—'}
                </span>
              </span>
              <button
                onClick={() => onRemove(i.sku)}
                aria-label={`${tOr(t, 'mobile.hoy_cart_remove', 'Remove')} — ${i.name}`}
                style={{
                  all: 'unset', boxSizing: 'border-box', cursor: 'pointer', flexShrink: 0,
                  width: TAP, height: TAP, borderRadius: 10,
                  display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.dim,
                }}
              >
                <Minus size={18} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
        {multiWarehouse && warehouses.length > 0 && (
          <label style={{ display: 'block', fontSize: 12.5, fontWeight: 600, color: C.dim, marginBottom: 12 }}>
            {t('hoy.cart_destination')}
            <select
              name="cart_destination"
              value={destWarehouse}
              onChange={e => onDestWarehouse(e.target.value)}
              style={{
                display: 'block', boxSizing: 'border-box', width: '100%', marginTop: 4, minHeight: 48,
                padding: '0 12px', borderRadius: 10, border: `1px solid ${C.border}`,
                background: 'var(--surface-2)', color: C.text, fontSize: 16,
              }}
            >
              {warehouses.map(w => <option key={w.id} value={w.name}>{w.name}</option>)}
            </select>
          </label>
        )}
        {cartPanels && <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0 }}>{cartPanels}</div>}
      </BottomSheet>

      {/* ── Order generated: send it from this very phone ── */}
      <GeneratedSheet
        po={generatedPO}
        lines={generatedLines}
        sendState={sendState}
        sendResult={sendResult}
        sendError={sendError}
        onSendNow={onSendNow}
        sendReason={sendReason}
        canDecide={canDecide}
        onClose={onDismissGenerated}
      />
    </div>
  )
}

const listReset: React.CSSProperties = { listStyle: 'none', margin: 0, padding: 0 }

// ── One decision, one card ───────────────────────────────────────────────────
function MobileActionCard({ item, briefing, stale, onApprove, onRemove, onRestore, onChangeQty, onOpen, canDecide }: {
  item:        ActionItem
  briefing:    MorningBriefing
  stale:       boolean
  onApprove:   () => void
  onRemove:    () => void
  onRestore:   () => void
  onChangeQty: (qty: number) => void
  onOpen:      () => void
  canDecide:   boolean
}) {
  const { t } = useLanguage()

  const isUrgent   = item.signal === 'PEDIR_YA'
  const accent     = isUrgent ? 'var(--signal-order-now-fg)' : 'var(--signal-order-soon-fg)'
  const inCart     = item.status === 'approved' || item.status === 'modified'
  const isOrdered  = item.status === 'ordered'
  const isRejected = item.status === 'rejected'
  const value      = item.qty * (item.unit_cost ?? 0)
  const canOrder   = item.qty > 0
  const coverage   = item.days != null ? Math.round(item.days) : null

  // "Estimado" on the card front, not only inside the detail sheet: most
  // buyers will never open it, so the fact that this quantity rests on values
  // we invented has to be visible while deciding.
  const anyAssumed = [
    item.lead_time_source, item.service_level_source, item.unit_cost_source, item.moq_source,
  ].some(s => !s || s === 'default')

  // Steps scale with the order size so the buyer is not tapping 200 times.
  const step = item.qty >= 500 ? 50 : item.qty >= 100 ? 10 : item.qty >= 20 ? 5 : 1
  const bump = (delta: number) => onChangeQty(Math.max(0, item.qty + delta))

  // A line the buyer said not to order collapses to one row they can undo.
  if (isRejected) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10,
        padding: '8px 8px 8px 14px', borderRadius: 12, minHeight: 56, boxSizing: 'border-box',
        border: `1px dashed ${C.border}`, background: 'var(--surface-2)',
      }}>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: 14, color: C.muted, textDecoration: 'line-through', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {item.name}
          </span>
          <span style={{ display: 'block', fontSize: 12, color: C.dim }}>{tOr(t, 'mobile.hoy_rejected', 'Not ordering')}</span>
        </span>
        {canDecide && (
          <button onClick={onRestore} style={{
            all: 'unset', boxSizing: 'border-box', cursor: 'pointer', flexShrink: 0,
            minHeight: TAP, padding: '0 12px', borderRadius: 10, display: 'flex', alignItems: 'center', gap: 6,
            fontSize: 13, fontWeight: 600, color: C.text, border: `1px solid ${C.border}`, background: 'var(--surface)',
          }}>
            <RotateCcw size={14} aria-hidden="true" /> {t('hoy.btn_restore')}
          </button>
        )}
      </div>
    )
  }

  return (
    <div style={{
      border: `1px solid ${inCart ? '#22c55e55' : C.border}`,
      borderLeft: `4px solid ${inCart ? '#22c55e' : accent}`,
      borderRadius: 14, marginBottom: 10, overflow: 'hidden',
      background: inCart ? 'rgba(34,197,94,0.04)' : 'var(--surface)',
      transition: 'background var(--dur-3) var(--ease-out), border-color var(--dur-3) var(--ease-out)',
    }}>
      {/* The head of the card opens the line in full: why, supplier, "no pedir". */}
      <button
        type="button"
        onClick={onOpen}
        className="tap-feedback"
        style={{
          all: 'unset', boxSizing: 'border-box', width: '100%', cursor: 'pointer',
          display: 'flex', alignItems: 'flex-start', gap: 10, padding: '12px 12px 0 14px', minHeight: 48,
        }}
      >
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: 15.5, fontWeight: 700, color: C.text, lineHeight: 1.3, overflowWrap: 'anywhere' }}>
            {item.name}
          </span>
          <span style={{ display: 'block', fontSize: 12, fontFamily: 'monospace', color: C.dim, marginTop: 2, overflowWrap: 'anywhere' }}>
            {/* The SKU only when the name is not already the SKU. */}
            {item.name !== item.sku ? item.sku : null}
            {item.supplier ? <span style={{ fontFamily: 'var(--font-sans, inherit)' }}>{item.name !== item.sku ? ' · ' : ''}{item.supplier}</span> : null}
          </span>
        </span>
        <span style={{
          flexShrink: 0, display: 'flex', alignItems: 'center', gap: 2, minHeight: 28,
          fontSize: 12.5, color: C.indigo, fontWeight: 600,
        }}>
          {t('hoy.why_toggle_show')} <ChevronRight size={15} aria-hidden="true" />
        </span>
      </button>

      <div style={{ padding: '0 14px 12px' }}>
        {/* Signal + honesty chips */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          <SignalBadge signal={item.signal} size="md" />
          {stale && <StaleSignalChip title={t('freshness.banner_title')} />}
          {anyAssumed && (
            <span style={{
              display: 'inline-flex', alignItems: 'center',
              padding: '3px 8px', borderRadius: 20, fontSize: 10.5, fontWeight: 700,
              letterSpacing: '0.03em', textTransform: 'uppercase',
              color: 'var(--dim)', border: '1px dashed var(--border)',
            }}>
              {tOr(t, 'inventory.source_assumed_badge', 'estimated')}
            </span>
          )}
        </div>

        <div style={{ fontSize: 13, color: C.muted, marginTop: 8, lineHeight: 1.5 }}>
          {item.reason}
          {coverage != null && (
            <> · {t('hoy.why_coverage_label')}: <strong style={{ color: C.text }}>
              {coverage} {coverageUnitLabel(briefing.coverage_unit, coverage, t)}
            </strong></>
          )}
        </div>
        <IncomingNote item={item} />
        <OrderedNote item={item} />

        {/* Quantity stepper — read-only for a viewer */}
        {isOrdered ? null : !canDecide ? (
          <div style={{ marginTop: 12, textAlign: 'center' }}>
            <div style={{ color: accent, fontSize: 22, fontWeight: 800 }}>{fmtNum(item.qty)}</div>
            <div style={{ fontSize: 11.5, color: C.dim, marginTop: 3 }}>
              {t('hoy.label_units')}{value > 0 && <> · ≈ {formatMoney(value)}</>}
            </div>
            <div style={{ fontSize: 11.5, color: C.dim, marginTop: 8, fontStyle: 'italic' }}>
              {t('hoy.decide_role_readonly')}
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12 }}>
            <StepButton onClick={() => bump(-step)} disabled={item.qty <= 0}
              label={tOr(t, 'mobile.qty_decrease', 'Reduce quantity')}>
              <Minus size={18} />
            </StepButton>
            <div style={{ flex: 1, textAlign: 'center', minWidth: 0 }}>
              <input
                type="number" inputMode="numeric" min={0} enterKeyHint="done"
                value={item.qty}
                aria-label={`${t('hoy.label_order_qty')} — ${item.name}`}
                onFocus={e => e.currentTarget.select()}
                onChange={e => {
                  const n = parseInt(e.target.value, 10)
                  onChangeQty(isNaN(n) || n < 0 ? 0 : n)
                }}
                style={{
                  width: '100%', textAlign: 'center', background: 'transparent',
                  border: 'none', borderBottom: `2px dashed ${accent}60`,
                  color: accent, fontSize: 22, fontWeight: 800, outline: 'none',
                  padding: '2px 0', minHeight: TAP, boxSizing: 'border-box',
                }}
              />
              <div style={{ fontSize: 11.5, color: C.dim, marginTop: 3 }}>
                {t('hoy.label_units')}{value > 0 && <> · ≈ {formatMoney(value)}</>}
              </div>
            </div>
            <StepButton onClick={() => bump(step)} label={tOr(t, 'mobile.qty_increase', 'Increase quantity')}>
              <Plus size={18} />
            </StepButton>
          </div>
        )}

        {/* Primary action — full width, thumb height. Gone once the line is on
            a PO: a second tap must not order the same units again. */}
        {canDecide && !isOrdered && (
          <button
            onClick={inCart ? onRemove : onApprove}
            disabled={!canOrder && !inCart}
            aria-pressed={inCart}
            style={{
              all: 'unset', boxSizing: 'border-box', width: '100%', marginTop: 12,
              minHeight: 48, borderRadius: 12, cursor: canOrder || inCart ? 'pointer' : 'not-allowed',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
              fontSize: 15, fontWeight: 700,
              background: inCart ? 'transparent' : canOrder ? '#22c55e' : 'var(--surface-2)',
              color: inCart ? '#16a34a' : canOrder ? '#fff' : C.dim,
              border: inCart ? '1px solid #22c55e' : '1px solid transparent',
              transition: 'background var(--dur-2) var(--ease-out), color var(--dur-2) var(--ease-out)',
            }}
          >
            {inCart
              ? <><Check size={17} aria-hidden="true" /> {tOr(t, 'mobile.in_cart', 'In the order — tap to remove')}</>
              : canOrder
                ? <><Plus size={17} aria-hidden="true" /> {tOr(t, 'mobile.add_to_cart', 'Add to the order')}</>
                : (item.incoming_qty ?? 0) > 0 ? t('hoy.covered_by_incoming') : t('hoy.enough_stock')}
          </button>
        )}
      </div>
    </div>
  )
}

// ── The line in full: why, supplier, "no pedir" ──────────────────────────────
function LineDetailSheet({ item, briefing, onClose, canDecide, suppliers, onChangeSupplier, onReject, onRestore }: {
  item: ActionItem | null
  briefing: MorningBriefing | null
  onClose: () => void
  canDecide: boolean
  suppliers: Supplier[]
  onChangeSupplier: (id: string) => void
  onReject: () => void
  onRestore: () => void
}) {
  const { t } = useLanguage()
  // Keep the last line on screen while the sheet slides away.
  const [last, setLast] = useState<ActionItem | null>(item)
  if (item && item !== last) setLast(item)
  const it = item ?? last
  if (!it) return null
  const isOrdered = it.status === 'ordered'
  const isRejected = it.status === 'rejected'
  const sentence = renderExplanation(t, it.explanation_code, it.explanation_params, it.explanation)

  return (
    <BottomSheet
      open={!!item}
      onClose={onClose}
      maxHeight="90dvh"
      title={it.name}
      footer={canDecide && !isOrdered ? (
        isRejected
          ? <button className="mobile-btn mobile-btn-secondary" onClick={onRestore}>
              <RotateCcw size={16} aria-hidden="true" /> {t('hoy.btn_restore')}
            </button>
          : <button className="mobile-btn mobile-btn-secondary" onClick={onReject}>
              {tOr(t, 'mobile.hoy_reject', 'Do not order this')}
            </button>
      ) : undefined}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        <span style={{ fontSize: 12.5, fontFamily: 'monospace', color: C.dim }}>{it.sku}</span>
        <SignalBadge signal={it.signal} size="md" />
      </div>

      {sentence && (
        <p style={{ margin: '0 0 12px', fontSize: 14, lineHeight: 1.55, color: C.text }}>{sentence}</p>
      )}

      {/* Supplier: re-pointing a line is a buyer decision (counts as modified). */}
      {canDecide && !isOrdered && suppliers.length > 0 ? (
        <label style={{ display: 'block', fontSize: 12.5, fontWeight: 600, color: C.dim, marginBottom: 12 }}>
          {t('hoy.cart_supplier_label')}
          <select
            value={it.supplier_id ?? (suppliers.find(s => s.name === it.supplier)?.id ?? '')}
            onChange={e => onChangeSupplier(e.target.value)}
            style={{
              display: 'block', boxSizing: 'border-box', width: '100%', marginTop: 4, minHeight: 48,
              padding: '0 12px', borderRadius: 10, border: `1px solid ${C.border}`,
              background: 'var(--surface-2)', color: C.text, fontSize: 16,
            }}
          >
            <option value="">{t('hoy.cart_supplier_none')}</option>
            {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
      ) : null}

      <div style={{ background: 'var(--surface-2)', border: `1px solid ${C.border}`, borderRadius: 12, padding: '4px 12px' }}>
        <WhyRow
          label={t('hoy.why_lead_time_label')}
          value={`${it.lead_time} ${t('hoy.why_days')}`}
          source={it.lead_time_source}
          note={provenanceText(t, it.lead_time_source, it.lead_time_rule_scope)}
          first
        />
        {it.service_level != null && (
          <WhyRow
            label={tOr(t, 'hoy.why_service_level_label', 'Service level')}
            value={`${Math.round(it.service_level * 100)}%`}
            source={it.service_level_source}
            note={it.service_level_caveat
              ? t(`hoy.service_level_caveat_${it.service_level_caveat}`, { pct: Math.round(it.service_level * 100) })
              : provenanceText(t, it.service_level_source, it.service_level_rule_scope)}
          />
        )}
        <WhyRow
          label={tOr(t, 'hoy.why_unit_cost_label', 'Unit cost')}
          value={it.unit_cost != null ? formatMoney(it.unit_cost) : '—'}
          source={it.unit_cost_source}
          note={provenanceText(t, it.unit_cost_source)}
        />
        {it.moq != null && (
          <WhyRow label="MOQ" value={fmtNum(Math.round(it.moq))} source={it.moq_source}
            note={provenanceText(t, it.moq_source, it.moq_rule_scope)} />
        )}
        {it.current_stock != null && (
          <WhyRow label={t('hoy.why_stock_label')} value={`${fmtNum(Math.round(it.current_stock))} ${t('hoy.why_units')}`} />
        )}
        {it.daily_demand != null && (
          <WhyRow label={t('hoy.why_demand_label')}
            value={`${fmtNum(it.daily_demand, { maximumFractionDigits: 1 })} ${t('hoy.why_units_day')}`} />
        )}
        {it.reorder_point != null && (
          <WhyRow label={t('hoy.why_reorder_point_label')} value={`${fmtNum(Math.round(it.reorder_point))} ${t('hoy.why_units')}`} />
        )}
        {it.days != null && briefing && (
          <WhyRow label={t('hoy.why_coverage_label')}
            value={`${Math.round(it.days)} ${coverageUnitLabel(briefing.coverage_unit, Math.round(it.days), t)}`} />
        )}
      </div>
    </BottomSheet>
  )
}

// One "campo: valor [estimado]" line, with the provenance sentence under it.
function WhyRow({ label, value, source, note, first }: {
  label: string
  value: string
  source?: ActionItem['lead_time_source']
  note?:  string
  first?: boolean
}) {
  return (
    <div style={{
      display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
      gap: 10, padding: '9px 0', borderTop: first ? 'none' : `1px solid ${C.border}`,
    }}>
      <div style={{ color: C.dim, fontSize: 13, flexShrink: 0, maxWidth: '45%' }}>{label}</div>
      <div style={{ textAlign: 'right', minWidth: 0 }}>
        <div style={{ color: C.text, fontWeight: 700, fontSize: 14 }}>
          {value}
          {source && <SourceBadge source={source} />}
        </div>
        {note && <div style={{ color: C.dim, fontSize: 11.5, marginTop: 2, lineHeight: 1.4 }}>{note}</div>}
      </div>
    </div>
  )
}

function StepButton({ children, onClick, disabled, label }: {
  children: React.ReactNode
  onClick:  () => void
  disabled?: boolean
  label:    string
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className="tap-feedback"
      style={{
        all: 'unset', boxSizing: 'border-box', cursor: disabled ? 'not-allowed' : 'pointer',
        width: 48, height: 48, flexShrink: 0, borderRadius: 12,
        border: `1px solid ${C.border}`, background: 'var(--surface-2)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: disabled ? C.dim : C.text, opacity: disabled ? 0.45 : 1,
      }}
    >
      {children}
    </button>
  )
}

function MiniKpi({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{
      background: 'var(--surface)', border: `1px solid ${C.border}`,
      borderRadius: 12, padding: '10px 12px', minWidth: 0,
    }}>
      <div style={{ fontSize: 11.5, color: C.dim, marginBottom: 3, overflowWrap: 'anywhere' }}>{label}</div>
      <div style={{ fontSize: 21, fontWeight: 700, color, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    </div>
  )
}

function DailySection({ color, icon, title, count, children }: {
  color: string
  icon: React.ReactNode
  title: string
  count?: number
  children: React.ReactNode
}) {
  return (
    <section style={{ marginBottom: 16 }}>
      <h2 style={{
        fontSize: 12, fontWeight: 700, color, margin: '4px 0 8px',
        textTransform: 'uppercase', letterSpacing: '0.07em',
        display: 'flex', alignItems: 'center', gap: 6,
      }}>
        {icon}<span style={{ flex: 1, minWidth: 0 }}>{title}</span>
        {count != null && count > 0 && (
          <span style={{ fontSize: 12, fontWeight: 700, letterSpacing: 0 }}>{count}</span>
        )}
      </h2>
      {children}
    </section>
  )
}

// What the cart totals say, with the same caveats as the desktop cart.
function CartSummary({ approved }: { approved: ActionItem[] }) {
  const { t } = useLanguage()
  const total = approved.reduce((s, i) => s + i.qty * (i.unit_cost ?? 0), 0)
  const uncostedLines = approved.filter(i => i.unit_cost == null).length
  const priced   = approved.filter(i => i.unit_margin != null && i.sale_price != null)
  const unpriced = approved.filter(i => i.unit_margin == null || i.sale_price == null)
  const marginProtected = priced.reduce((s, i) => s + i.qty * (i.unit_margin ?? 0), 0)
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: '#16a34a' }}>
        {approved.length} {t('hoy.cart_products_approved')}
      </div>
      {total > 0 && (
        <div key={total} className="value-changed" style={{ fontSize: 13, color: C.muted, marginTop: 1, borderRadius: 4 }}>
          {t('hoy.cart_total_label')}: <strong style={{ color: C.text }}>{formatMoney(total)}</strong>
        </div>
      )}
      {total > 0 && uncostedLines > 0 && (
        <div style={{ fontSize: 11.5, color: C.amber, marginTop: 1 }}>{t('hoy.cart_total_uncosted', { count: uncostedLines })}</div>
      )}
      {priced.length > 0 && marginProtected > 0 && (
        <div style={{ fontSize: 12, color: C.green, marginTop: 1 }}>
          {formatMoney(marginProtected)} {t('hoy.cart_protects_margin_suffix')}
        </div>
      )}
      {unpriced.length > 0 && priced.length > 0 && (
        <div style={{ fontSize: 11.5, color: C.amber, marginTop: 1 }}>
          {t('hoy.cart_margin_excludes_prefix')} {unpriced.length} {t('hoy.cart_margin_excludes_suffix')}
        </div>
      )}
      {unpriced.length > 0 && priced.length === 0 && (
        <div style={{ fontSize: 11.5, color: C.dim, marginTop: 1 }}>{t('hoy.cart_margin_add_prices')}</div>
      )}
    </div>
  )
}

// ── Sticky cart ──────────────────────────────────────────────────────────────
// Fixed above the tab bar: on a phone the list is long, and the total the
// buyer is about to commit must stay in sight. The summary opens the review
// sheet; "generate" is one tap from anywhere on the list.
function MobileCartBar({ approved, onOpen, onGenerate, generating }: {
  approved: ActionItem[]
  onOpen:  () => void
  onGenerate: () => void
  generating: boolean
}) {
  const { t } = useLanguage()
  const total = approved.reduce((s, i) => s + i.qty * (i.unit_cost ?? 0), 0)
  const uncosted = approved.some(i => i.unit_cost == null)
  return (
    <div className="cart-bar-enter" style={{
      position: 'fixed', left: 0, right: 0, bottom: 'var(--mobile-nav-h, 0px)', zIndex: 40,
      background: 'var(--surface)', borderTop: '1px solid rgba(34,197,94,0.45)',
      boxShadow: '0 -6px 24px rgba(0,0,0,0.18)', padding: '10px 12px',
      display: 'flex', alignItems: 'center', gap: 10,
    }}>
      <button
        onClick={onOpen}
        aria-haspopup="dialog"
        className="tap-feedback"
        style={{
          all: 'unset', boxSizing: 'border-box', cursor: 'pointer', flex: 1, minWidth: 0,
          minHeight: 48, display: 'flex', alignItems: 'center', gap: 8,
        }}
      >
        <span style={{ minWidth: 0, flex: 1 }}>
          <span style={{ display: 'block', fontSize: 13.5, fontWeight: 700, color: '#16a34a' }}>
            {approved.length} {t('hoy.cart_products_approved')}
          </span>
          <span key={total} className="value-changed" style={{ display: 'block', fontSize: 12.5, color: C.muted, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', borderRadius: 4 }}>
            {total > 0 ? `${formatMoney(total)}${uncosted ? ' *' : ''}` : tOr(t, 'mobile.hoy_cart_review', 'Review the order')}
          </span>
        </span>
        <ChevronUp size={18} color={C.dim} aria-hidden="true" style={{ flexShrink: 0 }} />
      </button>
      <button
        onClick={onGenerate}
        disabled={generating}
        aria-busy={generating}
        className="mobile-btn"
        style={{ flex: 'none', background: '#22c55e', color: '#fff', padding: '0 18px' }}
      >
        {generating ? t('hoy.btn_download_po_busy') : tOr(t, 'mobile.hoy_cart_generate', 'Generate')}
      </button>
    </div>
  )
}

// ── Right after generating: send it, or forward it yourself ──────────────────
function GeneratedSheet({ po, lines, sendState, sendResult, sendError, onSendNow, sendReason, canDecide, onClose }: {
  po: POLogEntry | null
  lines: ActionItem[]
  sendState: 'idle' | 'sending' | 'done'
  sendResult: SendPOResult | null
  sendError: unknown
  onSendNow: () => void
  sendReason: (reason: string) => string
  canDecide: boolean
  onClose: () => void
}) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const [last, setLast] = useState<POLogEntry | null>(po)
  if (po && po !== last) setLast(po)
  const shown = po ?? last
  if (!shown) return null
  const bySupplier = Object.entries(lines.reduce<Record<string, ActionItem[]>>((acc, i) => {
    const key = i.supplier || ''
    ;(acc[key] = acc[key] || []).push(i)
    return acc
  }, {}))
  const ref = shown.po_number ? `OC-${String(shown.po_number).padStart(6, '0')}` : ''

  return (
    <BottomSheet
      open={!!po}
      onClose={onClose}
      maxHeight="92dvh"
      title={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        <Check size={18} color="#16a34a" aria-hidden="true" /> {t('hoy.generate_send_title')}
      </span>}
      footer={<>
        <Link href="/pedidos" className="mobile-btn mobile-btn-secondary" style={{ textDecoration: 'none' }}>
          {t('hoy.generate_send_go_orders')} <ArrowRight size={16} aria-hidden="true" />
        </Link>
        <button className="mobile-btn mobile-btn-secondary" onClick={onClose}>{t('hoy.generate_send_dismiss')}</button>
      </>}
    >
      {ref && <p style={{ margin: '0 0 6px', fontSize: 15, fontWeight: 700, fontFamily: 'monospace', color: C.text }}>{ref}</p>}
      <p style={{ fontSize: 13, color: C.dim, margin: '0 0 12px', lineHeight: 1.5 }}>{t('hoy.generate_send_subtitle')}</p>

      <ul style={{ ...listReset, display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 14 }}>
        {bySupplier.map(([supplier, ls]) => (
          <li key={supplier || '__none__'} style={{ padding: '10px 12px', borderRadius: 10, background: 'var(--surface-2)', fontSize: 13 }}>
            <div style={{ fontWeight: 700, color: supplier ? C.text : C.amber }}>
              {supplier || t('hoy.generate_send_no_supplier')}
            </div>
            <div style={{ color: C.dim, marginTop: 2, lineHeight: 1.45 }}>
              {ls.map(l => `${l.name} (${fmtNum(l.qty)} ${t('hoy.generate_send_units_abbrev')})`).join(' · ')}
            </div>
          </li>
        ))}
      </ul>

      {canDecide && (sendState === 'done' && sendResult ? (
        <div role="status" style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 14 }}>
          {sendResult.sent.map(s => (
            <div key={s.supplier} style={{ fontSize: 13, color: C.green }}>
              ✓ {s.supplier}{s.email ? ' · email' : ''}{s.whatsapp ? ' · WhatsApp' : ''}
            </div>
          ))}
          {sendResult.skipped.map((s, idx) => (
            <div key={`${s.supplier}-${idx}`} style={{ fontSize: 13, color: C.amber }}>
              {s.supplier || '—'}: {sendReason(s.reason)}
            </div>
          ))}
          {(sendResult.unresolved ?? []).length > 0 && (
            <div style={{ fontSize: 13, color: C.amber }}>
              {t('roi.send_po_unresolved')} {(sendResult.unresolved ?? []).map(u => u.sku).join(', ')}
            </div>
          )}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 14 }}>
          {sendError != null && (
            <div role="alert" style={{ fontSize: 13, color: C.red, lineHeight: 1.5 }}>
              {t('hoy.generate_send_failed')} {errorDetail(sendError)}
            </div>
          )}
          <button
            className="mobile-btn mobile-btn-primary"
            onClick={onSendNow}
            disabled={sendState === 'sending'}
            aria-busy={sendState === 'sending'}
            style={{ width: '100%' }}
          >
            <Send size={17} aria-hidden="true" />
            {sendState === 'sending' ? t('roi.send_po_sending') : t('hoy.generate_send_btn')}
          </button>
        </div>
      ))}

      <div style={{ padding: 12, borderRadius: 12, background: 'var(--surface-2)', border: `1px solid ${C.border}` }}>
        <p style={{ fontSize: 12.5, color: C.dim, margin: '0 0 10px', lineHeight: 1.5 }}>{t('po.forward_hint')}</p>
        <ForwardPOActions poLogId={shown.id} />
      </div>
    </BottomSheet>
  )
}
