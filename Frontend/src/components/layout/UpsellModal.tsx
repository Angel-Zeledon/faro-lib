'use client'
/**
 * Opened when the user clicks a nav item locked by their plan's entitlements
 * (see Sidebar.tsx). Names the gap and links to the upgrade page rather than
 * letting the click fall through to a route the backend will 403 on.
 *
 * Styling mirrors ConfirmDialog (components/ui/ConfirmDialog.tsx) — the
 * app's existing full-screen overlay + centered card idiom.
 */
import Link from 'next/link'
import { X } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useEntitlements } from '@/lib/entitlements'

export interface UpsellModalProps {
  feature: string
  onClose: () => void
}

// Tier names are brand proper nouns, shown as-is in both languages.
const PLAN_LABEL: Record<string, string> = {
  starter: 'Starter', professional: 'Professional', enterprise: 'Enterprise',
}

export default function UpsellModal({ feature, onClose }: UpsellModalProps) {
  const { t } = useLanguage()
  const { ent } = useEntitlements()
  const requiredPlan = ent?.feature_plans?.[feature]
  const planLabel = requiredPlan ? PLAN_LABEL[requiredPlan] ?? requiredPlan : null

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t('entitlements.upsell_title')}
      style={{
        position: 'fixed', inset: 0, zIndex: 10000,
        background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(3px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 14, padding: 24, width: '100%', maxWidth: 380,
          boxShadow: '0 24px 60px -20px rgba(0,0,0,0.5)', position: 'relative',
        }}
      >
        <button
          onClick={onClose}
          aria-label={t('cancel')}
          style={{
            all: 'unset', cursor: 'pointer', position: 'absolute', top: 14, right: 14,
            color: 'var(--dim)', display: 'flex',
          }}
        >
          <X size={16} />
        </button>
        <h3 style={{ margin: '0 0 8px', fontSize: 16, fontWeight: 700, color: 'var(--text)' }}>
          {t('entitlements.upsell_title')}
        </h3>
        {planLabel && (
          <div
            style={{
              display: 'inline-flex', alignItems: 'baseline', gap: 6,
              margin: '0 0 12px', padding: '4px 10px', borderRadius: 999,
              background: 'color-mix(in srgb, var(--accent) 14%, transparent)',
              border: '1px solid color-mix(in srgb, var(--accent) 35%, transparent)',
            }}
          >
            <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('entitlements.upsell_plan_label')}</span>
            <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--accent)' }}>{planLabel}</span>
          </div>
        )}
        <p style={{ margin: '0 0 20px', fontSize: 13.5, lineHeight: 1.55, color: 'var(--dim)' }}>
          {t('entitlements.upsell_body')}
        </p>
        <Link
          href="/planes"
          style={{
            display: 'inline-block', padding: '9px 16px', borderRadius: 9,
            background: 'var(--accent)', color: '#fff', fontSize: 13, fontWeight: 600,
            textDecoration: 'none',
          }}
        >
          {t('entitlements.upsell_cta')}
        </Link>
      </div>
    </div>
  )
}
