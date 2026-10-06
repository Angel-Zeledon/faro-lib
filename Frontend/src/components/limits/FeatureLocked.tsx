'use client'
import { Lock } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useFeature } from '@/lib/entitlements'
import type { PlanFeature } from '@/lib/api'
import { useUpgradePrompt } from './UpgradeDialog'
import Button from '@/components/ui/Button'
import UpgradeToFull, {
  canBuyOnline, showPaymentPlaceholder, useBillingStatus,
} from '@/components/billing/UpgradeToFull'

/**
 * The calm replacement for a screen section the plan does not include.
 *
 * It says what the feature gives, that it comes with the Full plan, and offers
 * the way to get it: buying the Full plan online when this installation has
 * payments configured, and the "write to us" dialog always. Never
 * an error and never a dead end: the user can read what they would get and ask.
 */
export default function FeatureLocked({ feature, compact = false }: {
  feature: PlanFeature
  compact?: boolean
}) {
  const { t } = useLanguage()
  const openUpgrade = useUpgradePrompt()
  // Online purchase, when this installation offers it and this tenant can buy:
  // it becomes the primary action and "write to us" the secondary one.
  // Without payments configured, the same spot shows the payment choice inert
  // ("coming soon") and "write to us" stays the primary action.
  const { status: billing, loaded } = useBillingStatus()
  const canBuy = canBuyOnline(billing)
  const drawPayments = loaded && (canBuy || showPaymentPlaceholder(billing))
  return (
    <div
      role="status"
      style={{
        display: 'flex', gap: 14, alignItems: 'flex-start', flexWrap: 'wrap',
        padding: compact ? '14px 16px' : '20px 22px', borderRadius: 12,
        background: 'var(--surface-2)', border: '1px solid var(--border)',
      }}
    >
      <Lock size={16} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2, color: 'var(--accent)' }} />
      <div style={{ flex: '1 1 240px', minWidth: 0 }}>
        <div style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>
          {t('limits.feature.title')}
        </div>
        <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: 'var(--dim)' }}>
          {t(`limits.feature.gives.${feature}`)}
        </p>
        {drawPayments && (
          <div style={{ marginTop: 12 }}>
            <UpgradeToFull status={billing} compact />
          </div>
        )}
      </div>
      <Button variant={canBuy ? 'secondary' : 'primary'} size="sm" onClick={() => openUpgrade(null, feature)}>
        {t('limits.feature.cta')}
      </Button>
    </div>
  )
}

/** Renders `children` for a plan that includes the feature and the locked card
 *  otherwise. While the plan is still unknown it renders `children`, so a paid
 *  tenant never sees the card flash. */
export function FeatureGate({ feature, children, compact }: {
  feature: PlanFeature
  children: React.ReactNode
  compact?: boolean
}) {
  const { locked } = useFeature(feature)
  return locked ? <FeatureLocked feature={feature} compact={compact} /> : <>{children}</>
}
