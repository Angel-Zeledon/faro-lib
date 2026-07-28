'use client'
import Link from 'next/link'
import { Clock, Package, Upload } from 'lucide-react'
import type { SessionInfo } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { useDataFreshness } from '@/hooks/useDataFreshness'

const STALE_DAYS = 14

/**
 * Header indicator for how fresh the data behind the semáforo is, with a
 * shortcut to upload new sales — instead of ML jargon ("forecast session").
 *
 * TWO clocks, not one. Sales age in weeks: a monthly uploader whose file is 20
 * days old is normal. Stock ages in DAYS — every sale moves it — so a stock
 * table nobody has touched in three weeks is a quantity the semáforo is only
 * guessing at. A single 14-day threshold over the sales session (all this
 * component used to have) either nags the monthly uploader or stays silent over
 * month-old stock; it did the second.
 *
 * The stock chip only appears once stock is actually late, so the normal case
 * looks exactly as it did before.
 */
export default function DataFreshness({ currentSession, loading }: {
  currentSession?: SessionInfo
  loading?: boolean
}) {
  const { t, lang } = useLanguage()
  const { freshness } = useDataFreshness()

  if (loading) {
    return (
      <div style={{
        display: 'inline-flex', alignItems: 'center', gap: 8,
        padding: '5px 10px', borderRadius: 8, fontSize: 12,
        background: 'var(--surface-2)', border: '1px solid var(--border)',
        color: 'var(--dim)',
      }}>
        <Clock size={12} /> …
      </div>
    )
  }

  const rel = (days: number) =>
    new Intl.RelativeTimeFormat(lang, { numeric: 'auto' }).format(-days, 'day')

  // Prefer the backend's answer: it counts from the last date INSIDE the file,
  // while `currentSession.updated_at` only knows when it was trained — a file
  // uploaded today can still end three months ago.
  const salesDays = freshness?.sales.age_days ?? (currentSession
    ? Math.floor((Date.now() - new Date(currentSession.updated_at).getTime()) / 86_400_000)
    : null)
  const salesState = freshness?.sales.state
    ?? (salesDays != null && salesDays > STALE_DAYS ? 'stale' : 'fresh')
  const salesLate = salesState === 'stale' || salesState === 'blind'

  const stockDays  = freshness?.stock.age_days ?? null
  const stockState = freshness?.stock.state ?? 'unknown'
  const stockLate  = stockState === 'stale' || stockState === 'blind'

  const amber = '#f59e0b'
  const accent = salesLate ? amber : 'var(--dim)'

  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <div
        title={salesLate ? t('freshness.stale_warning') : undefined}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 8,
          padding: '5px 10px', borderRadius: 8, fontSize: 12,
          background: salesLate ? 'rgba(245,158,11,0.07)' : 'var(--surface-2)',
          border: `1px solid ${salesLate ? 'rgba(245,158,11,0.35)' : 'var(--border)'}`,
        }}
      >
        <Clock size={12} color={accent} />
        <span style={{ color: salesLate ? amber : 'var(--muted)', fontWeight: salesLate ? 600 : 400 }}>
          {salesDays != null
            ? `${t('freshness.updated_prefix')} ${rel(salesDays)}`
            : t('freshness.no_data')}
        </span>
        <span style={{ color: 'var(--border)' }}>|</span>
        <Link href="/quick-start" style={{
          display: 'inline-flex', alignItems: 'center', gap: 4,
          color: 'var(--accent)', fontWeight: 600, textDecoration: 'none',
        }}>
          <Upload size={11} /> {t('freshness.upload_new')}
        </Link>
      </div>

      {/* Stock chip — only once the stock clock itself is late. */}
      {stockLate && stockDays != null && (
        <Link
          href="/inventory"
          title={t(stockState === 'blind'
            ? 'freshness.stock_blind_warning'
            : 'freshness.stock_stale_warning', { days: stockDays })}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '5px 10px', borderRadius: 8, fontSize: 12,
            textDecoration: 'none',
            background: 'rgba(245,158,11,0.07)',
            border: `1px solid rgba(245,158,11,${stockState === 'blind' ? '0.5' : '0.35'})`,
            color: amber, fontWeight: 600,
          }}
        >
          <Package size={12} color={amber} />
          {t('freshness.stock_age', { days: stockDays })}
        </Link>
      )}
    </div>
  )
}
