'use client'
/**
 * `/hoy` on a phone.
 *
 * The buyer this product is for does not sit at a desk while deciding what to
 * order — they walk the warehouse with a phone in one hand. The desktop screen
 * is a dense table with fixed widths inside inline styles, which at 390px is
 * not "cramped", it is unusable. Rather than making 17.000 lines of inline
 * styles responsive, this is ONE screen built for the narrow case: a list of
 * cards, one decision per card, thumb-sized controls.
 *
 * It is a separate component on purpose. It never renders on desktop and the
 * desktop path never renders it, so the blast radius of everything here is a
 * viewport the app previously did not serve at all.
 *
 * What it must NOT drop, and does not:
 *   · the stale-data banner (the semáforo is computed on data nobody refreshed)
 *   · a per-card "sin verificar" chip while the semáforo is degraded — on a
 *     phone the buyer scrolls past the banner in one flick, and a confident red
 *     or green over month-old stock is exactly the failure it exists to prevent
 *   · the KPI caveat under the counters
 *   · the assumptions banner and the "estimado" badge on every value Faro
 *     guessed instead of receiving
 *   · the margin caveat on the cart (which SKUs are excluded and why)
 * All of them come from ./shared, so the two views cannot drift apart.
 */
import { useState } from 'react'
import Link from 'next/link'
import {
  AlertTriangle, Clock, Truck, ArrowRight, ChevronDown, ChevronUp,
  Minus, Plus, Check, RefreshCw, X, Send,
} from 'lucide-react'
import type { MorningBriefing, POLogEntry, OverdueReception } from '@/lib/types'
import type { DataFreshnessInfo } from '@/lib/api'
import { formatMoney } from '@/lib/currency'
import { coverageUnitLabel } from '@/lib/period'
import { renderExplanation } from '@/lib/explanationCopy'
import SignalBadge from '@/components/ui/SignalBadge'
import StaleDataBanner, { StaleSignalChip } from '@/components/ui/StaleDataBanner'
import { ErrorState, LoadingState, SkeletonCards } from '@/components/ui/States'
import { ForwardPOActions } from '@/components/po/ForwardPOActions'
import { useLanguage } from '@/contexts/LanguageContext'
import {
  C, AllClear, AssumptionsBanner, SourceBadge, provenanceText, summarizeAssumptions,
  tOr, type ActionItem,
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
  semaphoreStale: boolean
  cart:      ActionItem[]
  approved:  ActionItem[]
  onApprove: (sku: string) => void
  /** Takes a line back out of the order without rejecting it — the second tap
   *  of the card's single toggle button. */
  onRemove:  (sku: string) => void
  onChangeQty: (sku: string, qty: number) => void
  onClearCart: () => void
  onGenerate:  () => void
  generatedPO: POLogEntry | null
  onDismissGenerated: () => void
  pendingReceptions: number
  overduePOs: OverdueReception[]
  /** Rendered when the session trained but no stock exists — the desktop's own
   *  empty state, passed in rather than reimplemented. */
  noInventory: React.ReactNode
}

export default function HoyMobile(props: HoyMobileProps) {
  const { t } = useLanguage()
  const {
    loading, error, onRetry, briefing, firstName, freshness, semaphoreStale,
    cart, approved, onApprove, onRemove, onChangeQty, onClearCart, onGenerate,
    generatedPO, onDismissGenerated, pendingReceptions, overduePOs, noInventory,
  } = props

  const urgent = cart.filter(i => i.signal === 'PEDIR_YA' && i.status !== 'rejected')
  const soon   = cart.filter(i => i.signal === 'PEDIR_PRONTO' && i.status !== 'rejected')
  const kpis   = briefing?.kpis

  return (
    <div style={{
      // The shell already pads narrow viewports (AppShell); the extra bottom
      // room is for the fixed cart bar, which must never cover the last card.
      // 170px is measured against the bar at its tallest — count, total, margin
      // line and the margin caveat all present — not against its empty state.
      background: C.bg, minHeight: '100%', padding: '4px 0 170px',
      // Guard: nothing on this screen may push the document sideways. A single
      // wide child would turn every vertical swipe into a fight with a
      // horizontal scrollbar.
      maxWidth: '100%', overflowX: 'hidden',
    }}>

      {/* ── Greeting ── */}
      <h1 style={{ fontSize: 19, fontWeight: 700, color: C.text, margin: '0 0 4px' }}>
        {t('hoy.greeting_good_morning')}{firstName ? `, ${firstName}` : ''}.
      </h1>
      <p style={{ fontSize: 12, color: C.dim, margin: '0 0 16px' }}>
        {briefing
          ? <>{t('hoy.date_active_session')}: {briefing.session_name}</>
          : t('hoy.date_loading')}
      </p>

      {loading && (
        <LoadingState label={t('hoy.loading_label')}>
          <SkeletonCards count={3} height={96} />
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

          {/* Receiving goods is the other thing done with a phone in hand, so
              both reception nudges stay — as links, since the reception form
              itself is a desktop screen. */}
          {overduePOs.length > 0 && (
            <MobileNudge
              tone="danger"
              icon={<AlertTriangle size={15} color={C.red} />}
              text={`${overduePOs.length} ${t('hoy.overdue_section_title')}`}
              href="/pedidos"
              cta={t('hoy.overdue_cta')}
            />
          )}
          {pendingReceptions > 0 && (
            <MobileNudge
              tone="info"
              icon={<Truck size={15} color={C.indigo} />}
              text={`${pendingReceptions} ${pendingReceptions === 1
                ? t('hoy.receptions_pending_singular')
                : t('hoy.receptions_pending_plural')}`}
              href="/pedidos"
              cta={t('hoy.receptions_cta')}
            />
          )}

          {/* ── Counters: two per row, the two that decide today's work ── */}
          {kpis && (
            <div style={{
              display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8,
            }}>
              <MiniKpi
                label={t('hoy.kpi_risk_today')}
                value={String(kpis.order_now)}
                color={kpis.order_now > 0 ? C.red : C.text}
              />
              <MiniKpi
                label={t('hoy.kpi_this_week')}
                value={String(kpis.order_soon)}
                color={kpis.order_soon > 0 ? C.amber : C.text}
              />
            </div>
          )}
          {semaphoreStale && (
            <div style={{ fontSize: 11.5, color: C.dim, marginBottom: 16, lineHeight: 1.55 }}>
              {t('freshness.kpi_caveat')}
            </div>
          )}

          {/* ── Urgent ── */}
          {urgent.length > 0 && (
            <MobileSection
              color="var(--signal-order-now-fg)"
              icon={<AlertTriangle size={12} aria-hidden="true" />}
              title={t('hoy.section_urgent')}
            >
              {urgent.map(item => (
                <MobileActionCard
                  key={item.sku} item={item} briefing={briefing}
                  stale={semaphoreStale}
                  onApprove={() => onApprove(item.sku)}
                  onRemove={() => onRemove(item.sku)}
                  onChangeQty={q => onChangeQty(item.sku, q)}
                />
              ))}
            </MobileSection>
          )}

          {/* ── This week ── */}
          {soon.length > 0 && (
            <MobileSection
              color="var(--signal-order-soon-fg)"
              icon={<Clock size={12} aria-hidden="true" />}
              title={t('hoy.section_this_week')}
            >
              {soon.map(item => (
                <MobileActionCard
                  key={item.sku} item={item} briefing={briefing}
                  stale={semaphoreStale}
                  onApprove={() => onApprove(item.sku)}
                  onRemove={() => onRemove(item.sku)}
                  onChangeQty={q => onChangeQty(item.sku, q)}
                />
              ))}
            </MobileSection>
          )}

          {urgent.length === 0 && soon.length === 0 && <AllClear stale={semaphoreStale} />}

          {/* Order just generated: on a phone the useful next step is not
              "go to /pedidos", it is forwarding the PO from this very device. */}
          {generatedPO && (
            <div style={{
              marginTop: 16, background: 'var(--surface)',
              border: '1px solid color-mix(in srgb, var(--accent) 40%, transparent)', borderRadius: 12, padding: 14,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <Send size={14} color={C.indigo} />
                <span style={{ fontSize: 14, fontWeight: 700, color: C.text, flex: 1 }}>
                  {t('hoy.generate_send_title')}
                </span>
                <button
                  onClick={onDismissGenerated}
                  aria-label={t('common.close')}
                  style={{
                    all: 'unset', cursor: 'pointer', color: 'var(--dim)',
                    display: 'flex', width: 32, height: 32,
                    alignItems: 'center', justifyContent: 'center',
                  }}
                >
                  <X size={16} aria-hidden="true" />
                </button>
              </div>
              <p style={{ fontSize: 12, color: 'var(--dim)', margin: '0 0 10px', lineHeight: 1.5 }}>
                {t('po.forward_hint')}
              </p>
              <ForwardPOActions poLogId={generatedPO.id} />
              <Link href="/pedidos" style={{
                display: 'inline-flex', alignItems: 'center', gap: 5, marginTop: 12,
                fontSize: 13, color: C.indigo, textDecoration: 'none',
              }}>
                {t('hoy.generate_send_go_orders')} <ArrowRight size={13} />
              </Link>
            </div>
          )}

          {/* ── The rest of the briefing lives on the desktop screen ── */}
          <div style={{
            marginTop: 24, paddingTop: 16, borderTop: `1px solid ${C.border}`,
            display: 'flex', flexDirection: 'column', gap: 12,
          }}>
            <button
              onClick={onRetry}
              style={{
                all: 'unset', boxSizing: 'border-box', cursor: 'pointer',
                minHeight: TAP, display: 'flex', alignItems: 'center',
                justifyContent: 'center', gap: 8, borderRadius: 9,
                border: `1px solid ${C.border}`, fontSize: 14, color: C.text,
              }}
            >
              <RefreshCw size={14} /> {t('hoy.btn_refresh_data')}
            </button>
            <p style={{ fontSize: 11.5, color: C.dim, margin: 0, lineHeight: 1.55 }}>
              {tOr(t, 'mobile.hoy_desktop_hint',
                'This is the short version for your phone. Demand changes, overstock and the full inventory are on the desktop screen.')}
            </p>
          </div>
        </>
      )}

      {/* ── Sticky cart ── */}
      {approved.length > 0 && (
        <MobileCartBar
          approved={approved}
          onClear={onClearCart}
          onGenerate={onGenerate}
        />
      )}
    </div>
  )
}

// ── One decision, one card ───────────────────────────────────────────────────
function MobileActionCard({ item, briefing, stale, onApprove, onRemove, onChangeQty }: {
  item:        ActionItem
  briefing:    MorningBriefing
  stale:       boolean
  onApprove:   () => void
  onRemove:    () => void
  onChangeQty: (qty: number) => void
}) {
  const { t } = useLanguage()
  const [showWhy, setShowWhy] = useState(false)

  const isUrgent   = item.signal === 'PEDIR_YA'
  const accent     = isUrgent ? 'var(--signal-order-now-fg)' : 'var(--signal-order-soon-fg)'
  const inCart     = item.status === 'approved' || item.status === 'modified'
  const value      = item.qty * (item.unit_cost ?? 0)
  const canOrder   = item.qty > 0
  const coverage   = item.days != null ? Math.round(item.days) : null

  // "Estimado" on the card front, not only inside the why-panel: on a phone the
  // panel is a tap away and most buyers will never open it, so the fact that
  // this quantity rests on values we invented has to be visible while deciding.
  const assumedSources = [
    item.lead_time_source, item.service_level_source,
    item.unit_cost_source, item.moq_source,
  ]
  const anyAssumed = assumedSources.some(s => !s || s === 'default')

  // Steps scale with the order size so the buyer is not tapping 200 times to
  // move a 2.000-unit line, and never rounds a small line into nonsense.
  const step = item.qty >= 500 ? 50 : item.qty >= 100 ? 10 : item.qty >= 20 ? 5 : 1
  const bump = (delta: number) => onChangeQty(Math.max(0, item.qty + delta))

  return (
    <div style={{
      border: `1px solid ${inCart ? '#22c55e55' : C.border}`,
      borderLeft: `4px solid ${inCart ? '#22c55e' : accent}`,
      borderRadius: 12, padding: 14, marginBottom: 10,
      background: inCart ? 'rgba(34,197,94,0.04)' : 'var(--surface)',
    }}>

      {/* Name + SKU */}
      <div style={{ fontSize: 15, fontWeight: 700, color: C.text, lineHeight: 1.3, overflowWrap: 'anywhere' }}>
        {item.name}
      </div>
      <div style={{
        fontSize: 10.5, fontFamily: 'monospace', color: C.dim, marginTop: 3,
        overflowWrap: 'anywhere',
      }}>
        {item.sku}
      </div>

      {/* Signal + honesty chips */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
        <SignalBadge signal={item.signal} size="md" />
        {/* The semáforo is blind: say so on the card itself, not only in the
            banner the buyer already scrolled past. */}
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

      {/* Why this SKU is here */}
      <div style={{ fontSize: 12.5, color: C.muted, marginTop: 8, lineHeight: 1.5 }}>
        {item.reason}
      </div>
      {coverage != null && (
        <div style={{ fontSize: 12, color: C.dim, marginTop: 4 }}>
          {t('hoy.why_coverage_label')}: <strong style={{ color: C.text }}>
            {coverage} {coverageUnitLabel(briefing.coverage_unit, coverage, t)}
          </strong>
        </div>
      )}

      {/* Quantity stepper */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, marginTop: 12,
      }}>
        <StepButton
          onClick={() => bump(-step)}
          disabled={item.qty <= 0}
          label={tOr(t, 'mobile.qty_decrease', 'Reduce quantity')}
        >
          <Minus size={17} />
        </StepButton>

        <div style={{ flex: 1, textAlign: 'center', minWidth: 0 }}>
          <input
            type="number"
            inputMode="numeric"
            min={0}
            value={item.qty}
            aria-label={t('hoy.label_order_qty')}
            onChange={e => {
              const n = parseInt(e.target.value, 10)
              onChangeQty(isNaN(n) || n < 0 ? 0 : n)
            }}
            style={{
              width: '100%', textAlign: 'center', background: 'transparent',
              border: 'none', borderBottom: `2px dashed ${accent}60`,
              color: accent, fontSize: 22, fontWeight: 800, outline: 'none',
              padding: '2px 0', minHeight: 34,
            }}
          />
          <div style={{ fontSize: 11, color: C.dim, marginTop: 3 }}>
            {t('hoy.label_units')}
            {value > 0 && <> · ≈ {formatMoney(value)}</>}
          </div>
        </div>

        <StepButton
          onClick={() => bump(step)}
          label={tOr(t, 'mobile.qty_increase', 'Increase quantity')}
        >
          <Plus size={17} />
        </StepButton>
      </div>

      {/* Primary action — full width, thumb height */}
      <button
        onClick={inCart ? onRemove : onApprove}
        disabled={!canOrder && !inCart}
        style={{
          all: 'unset', boxSizing: 'border-box', width: '100%', marginTop: 12,
          minHeight: TAP, borderRadius: 10, cursor: canOrder || inCart ? 'pointer' : 'not-allowed',
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
          fontSize: 14.5, fontWeight: 700,
          background: inCart ? 'transparent' : canOrder ? '#22c55e' : 'var(--surface-2)',
          color: inCart ? '#22c55e' : canOrder ? '#fff' : C.dim,
          border: inCart ? '1px solid #22c55e' : '1px solid transparent',
        }}
      >
        {inCart
          ? <><Check size={16} /> {tOr(t, 'mobile.in_cart', 'In the order — tap to remove')}</>
          : canOrder
            ? tOr(t, 'mobile.add_to_cart', 'Add to the order')
            : t('hoy.enough_stock')}
      </button>

      {/* Why panel */}
      <button
        onClick={() => setShowWhy(v => !v)}
        style={{
          all: 'unset', boxSizing: 'border-box', cursor: 'pointer', width: '100%',
          minHeight: 38, marginTop: 4, display: 'flex', alignItems: 'center',
          justifyContent: 'center', gap: 5, fontSize: 12.5, color: C.dim,
        }}
      >
        {showWhy ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        {showWhy ? t('hoy.why_toggle_hide') : t('hoy.why_toggle_show')}
      </button>

      {showWhy && (
        <div style={{
          background: 'var(--surface-2)', border: `1px solid ${C.border}`,
          borderRadius: 9, padding: 12, marginTop: 4, fontSize: 12,
        }}>
          {(() => {
            const sentence = renderExplanation(
              t, item.explanation_code, item.explanation_params, item.explanation)
            return sentence ? (
              <p style={{ margin: '0 0 10px', fontSize: 12.5, lineHeight: 1.55, color: C.text }}>
                {sentence}
              </p>
            ) : null
          })()}

          <WhyRow
            label={t('hoy.why_lead_time_label')}
            value={`${item.lead_time} ${t('hoy.why_days')}`}
            source={item.lead_time_source}
            note={provenanceText(t, item.lead_time_source, item.lead_time_rule_scope)}
          />
          {item.service_level != null && (
            <WhyRow
              label={tOr(t, 'hoy.why_service_level_label', 'Service level')}
              value={`${Math.round(item.service_level * 100)}%`}
              source={item.service_level_source}
              note={provenanceText(t, item.service_level_source, item.service_level_rule_scope)}
            />
          )}
          <WhyRow
            label={tOr(t, 'hoy.why_unit_cost_label', 'Unit cost')}
            value={item.unit_cost != null ? formatMoney(item.unit_cost) : '—'}
            source={item.unit_cost_source}
            note={provenanceText(t, item.unit_cost_source)}
          />
          {item.moq != null && (
            <WhyRow
              label="MOQ"
              value={Math.round(item.moq).toLocaleString('es')}
              source={item.moq_source}
              note={provenanceText(t, item.moq_source, item.moq_rule_scope)}
            />
          )}
          {item.current_stock != null && (
            <WhyRow
              label={t('hoy.why_stock_label')}
              value={`${Math.round(item.current_stock).toLocaleString('es')} ${t('hoy.why_units')}`}
            />
          )}
          {item.daily_demand != null && (
            <WhyRow
              label={t('hoy.why_demand_label')}
              value={`${item.daily_demand.toLocaleString('es', { maximumFractionDigits: 1 })} ${t('hoy.why_units_day')}`}
            />
          )}
          {item.reorder_point != null && (
            <WhyRow
              label={t('hoy.why_reorder_point_label')}
              value={`${Math.round(item.reorder_point).toLocaleString('es')} ${t('hoy.why_units')}`}
            />
          )}
          {item.supplier && (
            <WhyRow label={t('hoy.cart_supplier_label')} value={item.supplier} />
          )}
        </div>
      )}
    </div>
  )
}

// One "campo: valor [estimado]" line, with the provenance sentence under it.
function WhyRow({ label, value, source, note }: {
  label: string
  value: string
  source?: ActionItem['lead_time_source']
  note?:  string
}) {
  return (
    <div style={{
      display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
      gap: 10, padding: '6px 0', borderTop: `1px solid ${C.border}`,
    }}>
      <div style={{ color: C.dim, fontSize: 11.5, flexShrink: 0 }}>{label}</div>
      <div style={{ textAlign: 'right', minWidth: 0 }}>
        <div style={{ color: C.text, fontWeight: 700, fontSize: 12.5 }}>
          {value}
          {source && <SourceBadge source={source} />}
        </div>
        {note && <div style={{ color: C.dim, fontSize: 10.5, marginTop: 1 }}>{note}</div>}
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
      style={{
        all: 'unset', boxSizing: 'border-box', cursor: disabled ? 'not-allowed' : 'pointer',
        width: TAP, height: TAP, flexShrink: 0, borderRadius: 10,
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
      borderRadius: 10, padding: '10px 12px', minWidth: 0,
    }}>
      <div style={{ fontSize: 10.5, color: C.dim, marginBottom: 4, overflowWrap: 'anywhere' }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
    </div>
  )
}

function MobileSection({ color, icon, title, children }: {
  color: string
  icon: React.ReactNode
  title: string
  children: React.ReactNode
}) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{
        fontSize: 11, fontWeight: 700, color,
        textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8,
        display: 'flex', alignItems: 'center', gap: 6,
      }}>
        {icon}{title}
      </div>
      {children}
    </div>
  )
}

function MobileNudge({ tone, icon, text, href, cta }: {
  tone: 'danger' | 'info'
  icon: React.ReactNode
  text: string
  href: string
  cta:  string
}) {
  const danger = tone === 'danger'
  return (
    <Link href={href} style={{ textDecoration: 'none' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 9, marginBottom: 10,
        padding: '11px 13px', borderRadius: 10, minHeight: TAP, boxSizing: 'border-box',
        background: danger ? 'rgba(239,68,68,0.06)' : 'color-mix(in srgb, var(--accent) 6%, transparent)',
        border: `1px solid ${danger ? 'rgba(239,68,68,0.25)' : 'color-mix(in srgb, var(--accent) 25%, transparent)'}`,
      }}>
        <span style={{ flexShrink: 0, display: 'flex' }}>{icon}</span>
        <span style={{ fontSize: 12.5, color: C.text, flex: 1, lineHeight: 1.4 }}>{text}</span>
        <span style={{
          fontSize: 12, fontWeight: 700, flexShrink: 0,
          color: danger ? C.red : C.indigo,
          display: 'inline-flex', alignItems: 'center', gap: 3,
        }}>
          {cta} <ArrowRight size={12} />
        </span>
      </div>
    </Link>
  )
}

// ── Sticky cart ──────────────────────────────────────────────────────────────
// Fixed to the bottom of the viewport, not `position: sticky` inside the list:
// on a phone the list is long enough that a sticky element scrolls away, and
// the total the buyer is about to commit must stay in sight.
function MobileCartBar({ approved, onClear, onGenerate }: {
  approved: ActionItem[]
  onClear:  () => void
  onGenerate: () => void
}) {
  const { t } = useLanguage()
  const total = approved.reduce((s, i) => s + i.qty * (i.unit_cost ?? 0), 0)

  // Same split as the desktop cart: lines without a price or without a cost are
  // excluded from the margin figure and reported, so the number is never
  // inflated by SKUs we cannot value.
  const priced   = approved.filter(i => i.unit_margin != null && i.sale_price != null)
  const unpriced = approved.filter(i => i.unit_margin == null || i.sale_price == null)
  const marginProtected = priced.reduce((s, i) => s + i.qty * (i.unit_margin ?? 0), 0)

  // The bar is fixed to the bottom edge, so on a phone it appears entirely
  // outside the buyer's field of view when they tap Approve mid-screen. It
  // slides up from its own edge to connect the two. Enter only.
  return (
    <div className="cart-bar-enter" style={{
      position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 40,
      background: 'var(--surface)', borderTop: '1px solid rgba(34,197,94,0.45)',
      boxShadow: '0 -6px 24px rgba(0,0,0,0.25)',
      padding: '10px 12px calc(10px + env(safe-area-inset-bottom))',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        gap: 10, marginBottom: 8,
      }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 12.5, fontWeight: 700, color: '#22c55e' }}>
            {approved.length} {t('hoy.cart_products_approved')}
          </div>
          {/* Background flash on change, never a count-up: the digits are the
              amount being committed and must be true in every frame. `key` is
              the value, so the animation re-runs whenever it changes. */}
          {total > 0 && (
            <div key={total} className="value-changed" style={{ fontSize: 12, color: C.muted, marginTop: 1, borderRadius: 4, padding: '0 3px' }}>
              {t('hoy.cart_total_label')}: {formatMoney(total)}
            </div>
          )}
          {priced.length > 0 && marginProtected > 0 && (
            <div style={{ fontSize: 11, color: C.green, marginTop: 1 }}>
              {formatMoney(marginProtected)} {t('hoy.cart_protects_margin_suffix')}
            </div>
          )}
          {/* The caveat is tied to the MARGIN figure, never to the total above
              it — the total uses cost and is complete. */}
          {unpriced.length > 0 && priced.length > 0 && (
            <div style={{ fontSize: 10.5, color: C.amber, marginTop: 1 }}>
              {t('hoy.cart_margin_excludes_prefix')} {unpriced.length} {t('hoy.cart_margin_excludes_suffix')}
            </div>
          )}
          {unpriced.length > 0 && priced.length === 0 && (
            <div style={{ fontSize: 10.5, color: C.dim, marginTop: 1 }}>
              {t('hoy.cart_margin_add_prices')}
            </div>
          )}
        </div>
        <button
          onClick={onClear}
          style={{
            all: 'unset', boxSizing: 'border-box', cursor: 'pointer', flexShrink: 0,
            minHeight: 36, padding: '0 12px', borderRadius: 8,
            border: `1px solid ${C.border}`, color: C.dim, fontSize: 12,
            display: 'flex', alignItems: 'center',
          }}
        >
          {t('hoy.btn_clear')}
        </button>
      </div>
      <button
        onClick={onGenerate}
        style={{
          all: 'unset', boxSizing: 'border-box', cursor: 'pointer', width: '100%',
          minHeight: TAP, borderRadius: 10, background: '#22c55e', color: '#fff',
          fontSize: 15, fontWeight: 700,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}
      >
        {t('hoy.btn_download_po')}
      </button>
    </div>
  )
}
