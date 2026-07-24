'use client'
import Link from 'next/link'
import { Clock, Upload } from 'lucide-react'
import type { SessionInfo } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'

const STALE_DAYS = 14

/**
 * Non-technical replacement for SessionBar: shows how fresh the sales data is
 * and a shortcut to upload new sales, instead of ML jargon ("forecast session").
 * The lightweight header indicator for how fresh the sales data is.
 */
export default function DataFreshness({ currentSession, loading }: {
  currentSession?: SessionInfo
  loading?: boolean
}) {
  const { t, lang } = useLanguage()

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

  const days = currentSession
    ? Math.floor((Date.now() - new Date(currentSession.updated_at).getTime()) / 86_400_000)
    : null
  const stale = days != null && days > STALE_DAYS
  const rel = days == null
    ? null
    : new Intl.RelativeTimeFormat(lang, { numeric: 'auto' }).format(-days, 'day')

  const accent = stale ? '#f59e0b' : 'var(--dim)'

  return (
    <div
      title={stale ? t('freshness.stale_warning') : undefined}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 8,
        padding: '5px 10px', borderRadius: 8, fontSize: 12,
        background: stale ? 'rgba(245,158,11,0.07)' : 'var(--surface-2)',
        border: `1px solid ${stale ? 'rgba(245,158,11,0.35)' : 'var(--border)'}`,
      }}
    >
      <Clock size={12} color={accent} />
      <span style={{ color: stale ? '#f59e0b' : 'var(--muted)', fontWeight: stale ? 600 : 400 }}>
        {rel ? `${t('freshness.updated_prefix')} ${rel}` : t('freshness.no_data')}
      </span>
      <span style={{ color: 'var(--border)' }}>|</span>
      <Link href="/quick-start" style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        color: 'var(--accent)', fontWeight: 600, textDecoration: 'none',
      }}>
        <Upload size={11} /> {t('freshness.upload_new')}
      </Link>
    </div>
  )
}
