'use client'
/**
 * Supplier health banners shared by /hoy and /orders.
 *
 * Both are pure presentation: the backend decides WHAT counts as incomplete
 * contact data (feature 2.5) and WHAT counts as a significant lead-time
 * deviation (feature 3.3). These components only choose which of the rows
 * the server already flagged belong on this particular screen.
 */
import Link from 'next/link'
import { UserX, Timer } from 'lucide-react'
import type { SupplierContactHealthRow, SupplierLeadTimeAlert } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'

const AMBER = '#f59e0b'
const RED   = '#ef4444'

const bannerStyle = (color: string): React.CSSProperties => ({
  display: 'flex', alignItems: 'center', gap: 10,
  padding: '10px 14px', borderRadius: 10,
  background: `${color}10`, border: `1px solid ${color}40`,
})

const ctaStyle = (color: string): React.CSSProperties => ({
  fontSize: 12, fontWeight: 700, color, textDecoration: 'none',
  whiteSpace: 'nowrap', flexShrink: 0,
})

/**
 * "3 suppliers with no email/WhatsApp — PO sending will skip them",
 * with one-click access to complete the ficha.
 */
export function SupplierContactHealthBanner({ rows }: { rows: SupplierContactHealthRow[] }) {
  const { t } = useLanguage()
  if (rows.length === 0) return null

  const names = rows.map(r => r.supplier)
  // One click to fix: ?focus=<name> opens that supplier's edit form directly,
  // or a pre-filled new-supplier form when there is no ficha yet (reason
  // 'no_supplier_record') — see SuppliersPageInner.
  const focus = `/inventory/suppliers?focus=${encodeURIComponent(rows[0].supplier)}`

  return (
    <div style={bannerStyle(AMBER)}>
      <UserX size={14} color={AMBER} style={{ flexShrink: 0 }} />
      <span style={{ fontSize: 12, color: 'var(--text)', flex: 1 }}>
        <strong>{rows.length}</strong>{' '}
        {rows.length === 1 ? t('suppliers.health_singular') : t('suppliers.health_plural')}
        {' — '}{t('suppliers.health_consequence')}
        {': '}<span style={{ color: 'var(--muted)' }}>{names.join(', ')}</span>
      </span>
      <Link href={focus} style={ctaStyle(AMBER)}>
        {t('suppliers.health_cta')}
      </Link>
    </div>
  )
}

/**
 * "Acme está tardando 12 días, no 7" — the supplier drifted significantly
 * off its own historical lead time (robust 3-sigma rule, computed server-side).
 */
export function SupplierLeadTimeAlertBanner({ alerts }: { alerts: SupplierLeadTimeAlert[] }) {
  const { t } = useLanguage()
  if (alerts.length === 0) return null

  const worst = alerts[0]
  const color = alerts.some(a => a.severity === 'high') ? RED : AMBER

  return (
    <div style={{ ...bannerStyle(color), alignItems: 'flex-start', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, width: '100%' }}>
        <Timer size={14} color={color} style={{ flexShrink: 0 }} />
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text)', flex: 1 }}>
          {t('suppliers.leadtime_title')}
        </span>
        <Link href="/proveedores/scorecard" style={ctaStyle(color)}>
          {t('suppliers.leadtime_cta')}
        </Link>
      </div>
      {alerts.map(a => (
        <div key={a.supplier} style={{ fontSize: 12, color: 'var(--text)', paddingLeft: 24 }}>
          <strong>{a.supplier}</strong>{' '}
          {t('suppliers.leadtime_now')} <strong style={{ color }}>{a.lead_time_recent}</strong>{' '}
          {t('suppliers.leadtime_days_not')}{' '}
          <strong>{a.lead_time_historical}</strong>{' '}
          <span style={{ color: 'var(--dim)' }}>
            ({t('suppliers.leadtime_based_on')} {a.n_recent}/{a.n_baseline})
          </span>
        </div>
      ))}
      {worst.severity === 'high' && (
        <div style={{ fontSize: 11, color: 'var(--dim)', paddingLeft: 24 }}>
          {t('suppliers.leadtime_advice')}
        </div>
      )}
    </div>
  )
}
