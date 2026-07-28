'use client'
import Link from 'next/link'
import { EyeOff, Upload } from 'lucide-react'
import type { DataFreshnessInfo } from '@/lib/api'
import { useLanguage } from '@/contexts/LanguageContext'

/**
 * "El semáforo está desactualizado."
 *
 * Shown on `/hoy` when either input behind the traffic light has gone blind
 * (stock ≥21 days, sales ≥45). It is deliberately grey, not red: the point is
 * not another alarm, it is that the colours below cannot be trusted. A traffic
 * light that lies confidently is worse than one that admits it is blind.
 *
 * It always states WHICH clock is late and offers the corresponding one-click
 * way out, because a warning the user cannot act on is just guilt.
 */
export default function StaleDataBanner({ freshness }: { freshness: DataFreshnessInfo }) {
  const { t } = useLanguage()
  if (freshness.semaphore !== 'degraded') return null

  const staleStock = freshness.degraded_by.includes('stock')
  const staleSales = freshness.degraded_by.includes('sales')

  const lines: string[] = []
  if (staleStock && freshness.stock.age_days != null) {
    lines.push(t('freshness.banner_stock_line', { days: freshness.stock.age_days }))
  }
  if (staleSales && freshness.sales.age_days != null) {
    lines.push(t('freshness.banner_sales_line', { days: freshness.sales.age_days }))
  }

  const cta = {
    display: 'inline-flex', alignItems: 'center', gap: 6,
    fontSize: 12, fontWeight: 700, padding: '7px 13px', borderRadius: 8,
    textDecoration: 'none', border: '1px solid var(--border)',
    color: 'var(--text)', background: 'var(--surface-2)',
  } as const

  return (
    <div
      role="status"
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 20,
        padding: '14px 18px', borderRadius: 12,
        background: 'var(--surface-2)',
        border: '1px dashed var(--border)',
      }}
    >
      <EyeOff size={17} color="var(--muted)" style={{ flexShrink: 0, marginTop: 2 }} />
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>
          {t('freshness.banner_title')}
        </div>
        {lines.map(line => (
          <div key={line} style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.6 }}>
            {line}
          </div>
        ))}
        <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 6, lineHeight: 1.6 }}>
          {t('freshness.banner_explainer')}
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
          {staleSales && (
            <Link href="/quick-start" style={cta}>
              <Upload size={12} /> {t('freshness.banner_cta_sales')}
            </Link>
          )}
          {staleStock && (
            <Link href="/inventory" style={cta}>
              <Upload size={12} /> {t('freshness.banner_cta_stock')}
            </Link>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * The chip that replaces a confident colour while the semáforo is degraded.
 * Used next to counters and in place of the green "todo bajo control" line.
 */
export function StaleSignalChip({ title }: { title?: string }) {
  const { t } = useLanguage()
  return (
    <span
      title={title}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        padding: '3px 9px', borderRadius: 20, fontSize: 11, fontWeight: 700,
        lineHeight: 1.3, whiteSpace: 'nowrap',
        color: 'var(--muted)', background: 'var(--surface-2)',
        border: '1px dashed var(--border)',
      }}
    >
      <EyeOff size={11} aria-hidden="true" style={{ flexShrink: 0 }} />
      {t('freshness.signal_outdated')}
    </span>
  )
}
