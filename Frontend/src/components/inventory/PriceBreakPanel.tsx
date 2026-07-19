'use client'
/**
 * Supplier price-break opportunities in the /hoy cart (feature 3.5).
 *
 * Pure presentation. The backend decides whether stepping up to a break is
 * worth it — it prices the discount against the cost of carrying the extra
 * units and refuses anything that would become overstock. This component never
 * re-derives that verdict; it renders `worth_it` and, when false, explains
 * which rule blocked it so a visible discount never looks like a bug.
 */
import { TrendingDown, Info } from 'lucide-react'
import type { PriceBreakOpportunity, PriceBreakReason } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'

const GREEN = '#22c55e'
const DIM   = 'var(--dim)'

const REASON_KEY: Record<PriceBreakReason, string> = {
  worth_it:               'pricebreaks.reason_worth_it',
  no_discount:            'pricebreaks.reason_no_discount',
  no_demand:              'pricebreaks.reason_no_demand',
  would_overstock:        'pricebreaks.reason_would_overstock',
  holding_exceeds_saving: 'pricebreaks.reason_holding_exceeds_saving',
  saving_immaterial:      'pricebreaks.reason_saving_immaterial',
}

export function PriceBreakPanel({
  opportunities, totalNetSaving, currency, onApplyStepUp,
}: {
  opportunities:  PriceBreakOpportunity[]
  totalNetSaving: number
  currency:       (n: number) => string
  onApplyStepUp:  (sku: string, quantity: number) => void
}) {
  const { t } = useLanguage()

  const worthIt = opportunities.filter(o => o.worth_it)
  const blocked = opportunities.filter(o => !o.worth_it)

  if (opportunities.length === 0) return null

  return (
    <div style={{
      marginTop: 10, borderRadius: 10, padding: '12px 16px',
      background: worthIt.length > 0 ? `${GREEN}10` : 'var(--surface-2)',
      border: `1px solid ${worthIt.length > 0 ? `${GREEN}40` : 'var(--border)'}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: worthIt.length ? 8 : 4 }}>
        <TrendingDown size={14} color={worthIt.length > 0 ? GREEN : DIM} style={{ flexShrink: 0 }} />
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', flex: 1 }}>
          {worthIt.length > 0
            ? `${t('pricebreaks.title')} — ${t('pricebreaks.net_saving_prefix')} ${currency(totalNetSaving)}`
            : t('pricebreaks.title_none')}
        </span>
      </div>

      {worthIt.map(o => (
        <div
          key={o.sku}
          style={{
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '6px 0', borderTop: '1px solid var(--border)',
          }}
        >
          <div style={{ flex: 1, fontSize: 12, color: 'var(--text)' }}>
            <strong>{o.sku}</strong>{': '}
            {t('pricebreaks.step_prefix')} {o.extra_units.toLocaleString('es-419')}{' '}
            {t('pricebreaks.step_more_units')} ({o.current_quantity.toLocaleString('es-419')}
            {' → '}{o.step_quantity.toLocaleString('es-419')}){', '}
            {t('pricebreaks.unit_price_falls')}{' '}
            <strong style={{ color: GREEN }}>
              {o.unit_price_drop_pct != null ? `${(o.unit_price_drop_pct * 100).toFixed(1)}%` : '—'}
            </strong>
            {'. '}
            <span style={{ color: DIM }}>
              {t('pricebreaks.net_saving_label')}: <strong style={{ color: GREEN }}>{currency(o.net_saving)}</strong>
              {' · '}{t('pricebreaks.holding_label')}: {currency(o.holding_cost)}
              {' · '}{t('pricebreaks.extra_cash_label')}: {currency(o.extra_cash_now)}
              {o.extra_coverage_days != null && (
                <> {' · '}+{o.extra_coverage_days.toFixed(0)} {t('pricebreaks.extra_days')}</>
              )}
            </span>
          </div>
          <button
            onClick={() => onApplyStepUp(o.sku, o.step_quantity)}
            style={{
              all: 'unset', cursor: 'pointer', flexShrink: 0,
              fontSize: 12, fontWeight: 700, color: GREEN,
              padding: '5px 10px', border: `1px solid ${GREEN}60`, borderRadius: 7,
            }}
          >
            {t('pricebreaks.apply_cta')}
          </button>
        </div>
      ))}

      {blocked.length > 0 && (
        <div style={{
          marginTop: worthIt.length ? 8 : 2, paddingTop: worthIt.length ? 8 : 0,
          borderTop: worthIt.length ? '1px solid var(--border)' : 'none',
        }}>
          {blocked.map(o => (
            <div key={o.sku} style={{
              display: 'flex', alignItems: 'flex-start', gap: 6,
              fontSize: 11, color: DIM, padding: '2px 0',
            }}>
              <Info size={11} style={{ flexShrink: 0, marginTop: 2 }} />
              <span>
                <strong>{o.sku}</strong>{': '}
                {o.unit_price_drop_pct != null && o.unit_price_drop_pct > 0 && (
                  <>
                    {t('pricebreaks.blocked_discount_prefix')}{' '}
                    {(o.unit_price_drop_pct * 100).toFixed(1)}%{' '}
                    {t('pricebreaks.blocked_at')} {o.step_quantity.toLocaleString('es-419')}
                    {' '}{t('pricebreaks.step_more_units')}{', '}
                  </>
                )}
                {t(REASON_KEY[o.reason_code])}
                {o.reason_code === 'would_overstock' && o.total_coverage_days != null && (
                  <> ({o.total_coverage_days.toFixed(0)} {t('pricebreaks.coverage_days_vs')}{' '}
                  {o.coverage_limit_days.toFixed(0)})</>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
