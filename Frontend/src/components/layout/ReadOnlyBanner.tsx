'use client'
/**
 * Shown on every authed page once the tenant's trial has expired without an
 * active plan. The backend (`GET /entitlements` → `read_only`) is the single
 * source of truth for this state — this component only renders it.
 */
import { AlertTriangle } from 'lucide-react'
import { useEntitlements } from '@/lib/entitlements'
import { useLanguage } from '@/contexts/LanguageContext'

const AMBER = '#f59e0b'

export default function ReadOnlyBanner() {
  const { readOnly } = useEntitlements()
  const { t } = useLanguage()

  if (!readOnly) return null

  return (
    <div
      role="alert"
      style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '10px 14px', borderRadius: 10, margin: '0 0 14px',
        background: `${AMBER}10`, border: `1px solid ${AMBER}40`,
      }}
    >
      <AlertTriangle size={14} color={AMBER} style={{ flexShrink: 0 }} />
      <span style={{ fontSize: 12, color: 'var(--text)', flex: 1 }}>
        {t('entitlements.readonly_banner')}
      </span>
    </div>
  )
}
