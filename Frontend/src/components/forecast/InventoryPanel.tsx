'use client'
import type {
  InventoryRecommendation, InventorySignal, InventoryStatusItem, CoverageUnit,
  PolicySkuComparison, DemandRiskEntry,
} from '@/lib/types'
import SignalBadge, { signalColor } from '@/components/ui/SignalBadge'
import { useLanguage } from '@/contexts/LanguageContext'
import { modelLabel } from '@/lib/modelLabel'
import { coverageUnitShort } from '@/lib/period'
import { formatMoney } from '@/lib/currency'
import { ACTION_SIGNAL, pct, fmtK } from './shared'

// ── Inventory panel ───────────────────────────────────────────────────────────

// Stock and coverage come from LIVE inventory_stock (`live`, from
// /inventory/status) — the same source the /inventory page uses — so the tab
// reflects post-reception stock, not the training-time snapshot. Forecast-derived
// figures (reorder point, safety stock, stockout risk) stay from the training
// recommendation `inv`, which does not change when stock moves.
export function InventoryPanel({ inv, live, coverageUnit, policy, risk }: {
  inv: InventoryRecommendation
  live?: InventoryStatusItem
  coverageUnit?: CoverageUnit
  /** This SKU's own row of the policy backtest. Absent for a SKU whose model
   *  ran no rolling-origin backtest, and for every pre-existing session. */
  policy?: PolicySkuComparison
  /** Present only when the safety stock above came from measured cumulative
   *  error rather than the classical formula — which is worth saying, because
   *  the two can differ by a lot on a SKU with persistent bias. */
  risk?: DemandRiskEntry
}) {
  const { t } = useLanguage()
  const fmtNum = (n: number | null | undefined, d = 0) => n != null ? n.toFixed(d) : '—'

  // Prefer the live semáforo; fall back to the training-time action only when
  // no live stock row exists for this SKU.
  const signal: InventorySignal = live?.signal ?? ACTION_SIGNAL[inv.action] ?? 'SIN_DATOS'

  const cards: { label: string; value: string; color: string }[] = []
  if (live) {
    cards.push({ label: t('skus.inv_current_stock'), value: fmtNum(live.current_stock), color: 'var(--accent)' })
    cards.push({
      label: `${t('skus.inv_coverage')} (${coverageUnitShort(coverageUnit, t)})`,
      value: live.coverage_days != null ? `${fmtNum(live.coverage_days)} ${coverageUnitShort(coverageUnit, t)}` : '—',
      color: signalColor(signal),
    })
  }
  cards.push({ label: t('skus.inv_reorder_point'), value: fmtNum(inv.reorder_point), color: 'var(--accent)' })
  cards.push({ label: t('skus.inv_safety_stock'),  value: fmtNum(inv.safety_stock),  color: '#3E8E9B' })
  cards.push({ label: t('skus.inv_stockout_risk'), value: pct(inv.stockout_risk),    color: (inv.stockout_risk ?? 0) > 0.2 ? '#C0504D' : '#2E8B62' })
  if (inv.holding_cost != null) {
    cards.push({ label: t('skus.inv_holding_cost'), value: formatMoney(inv.holding_cost), color: '#B7791F' })
  } else if (!live) {
    // No live stock row — keep the training-time coverage as the last resort.
    cards.push({ label: t('skus.inv_days_coverage'), value: fmtNum(inv.days_coverage), color: '#B7791F' })
  }

  const variant: 'danger' | 'warning' | 'success' | 'neutral' =
      signal === 'PEDIR_YA'                                ? 'danger'
    : signal === 'PEDIR_PRONTO' || signal === 'SOBRESTOCK' ? 'warning'
    : signal === 'OK'                                       ? 'success'
    :                                                         'neutral'
  const bannerBg = variant === 'danger'  ? 'rgba(192,80,77,0.08)'
                 : variant === 'warning' ? 'rgba(183,121,31,0.08)'
                 : variant === 'success' ? 'rgba(46,139,98,0.08)'
                 :                         'rgba(100,116,139,0.08)'
  const bannerBorder = variant === 'danger'  ? 'rgba(192,80,77,0.2)'
                     : variant === 'warning' ? 'rgba(183,121,31,0.2)'
                     : variant === 'success' ? 'rgba(46,139,98,0.2)'
                     :                         'rgba(100,116,139,0.2)'
  const message = signal === 'PEDIR_YA'     ? t('skus.action_reorder_msg')
                : signal === 'PEDIR_PRONTO' ? t('skus.action_order_soon_msg')
                : signal === 'SOBRESTOCK'   ? t('skus.action_overstock_msg')
                : signal === 'OK'           ? t('skus.action_ok_msg')
                :                             t('skus.action_sin_datos_msg')

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 }}>
        {cards.map(({ label, value, color }) => (
          <div key={label} style={{ background: 'var(--surface-2)', borderRadius: 8, padding: '12px 14px', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: 17, fontWeight: 700, color }}>{value}</div>
            <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 3 }}>{label}</div>
          </div>
        ))}
      </div>
      <div style={{
        padding: '10px 14px', borderRadius: 8,
        background: bannerBg,
        border: `1px solid ${bannerBorder}`,
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <SignalBadge signal={signal} size="md" />
        <span style={{ fontSize: 12 }}>{message}</span>
      </div>

      {/* Where the cushion above actually comes from. Silent provenance is how
          a buyer ends up distrusting a number that was the more honest one. */}
      {risk && (
        <div style={{ fontSize: 11, color: 'var(--dim)', lineHeight: 1.55 }}>
          {t('skus.risk_measured_caption', { model: modelLabel(t, risk.model) })}
        </div>
      )}

      {/* The same simulation as the session headline, narrowed to this SKU.
          Stated as sentences rather than tiles: at this scale the pair of
          numbers IS the message, and a tile would separate them. */}
      {policy && (
        <div style={{
          borderTop: '1px solid var(--border)', paddingTop: 12,
          display: 'flex', flexDirection: 'column', gap: 4,
        }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text)' }}>
            {t('skus.policy_sku_title')}
          </div>
          <ul style={{
            listStyle: 'disc', margin: 0, paddingLeft: 18,
            display: 'flex', flexDirection: 'column', gap: 3,
          }}>
            <li style={{ fontSize: 11.5, color: 'var(--dim)', lineHeight: 1.55 }}>
              {t('skus.policy_sku_fill', {
                model:    pct(policy.policy.fill_rate),
                baseline: pct(policy.baseline.fill_rate),
              })}
            </li>
            <li style={{ fontSize: 11.5, color: 'var(--dim)', lineHeight: 1.55 }}>
              {t('skus.policy_sku_stockouts', {
                model:    policy.policy.stockout_buckets,
                baseline: policy.baseline.stockout_buckets,
              })}
            </li>
            <li style={{ fontSize: 11.5, color: 'var(--dim)', lineHeight: 1.55 }}>
              {t('skus.policy_sku_inventory', {
                model:    fmtK(policy.policy.avg_inventory),
                baseline: fmtK(policy.baseline.avg_inventory),
              })}
            </li>
          </ul>
        </div>
      )}
    </div>
  )
}
