'use client'
/**
 * Plan gate for a whole page.
 *
 * The sidebar hides links the tenant's plan does not include, which is only half
 * a gate: the four Professional pages (Escenarios, Asistente IA, Automatización,
 * Mensajes) rendered in full for anyone who typed the URL or kept a bookmark
 * from their trial. They then hit the backend's own 403 mid-action, which is a
 * worse way to learn about a plan than being told up front. `/integraciones`
 * already did this properly; this is that pattern, factored out.
 *
 * While entitlements are still loading it shows a spinner rather than a padlock:
 * `has()` fails closed now, so rendering the locked state during the request
 * would flash "not on your plan" at a customer who is on it.
 */
import { AlertTriangle, Lock } from 'lucide-react'
import { useEntitlements } from '@/lib/entitlements'
import { useLanguage } from '@/contexts/LanguageContext'
import { EmptyState, LoadingState } from '@/components/ui/States'

export default function FeatureGate({ feature, children }: {
  /** Feature key as `/entitlements` reports it, e.g. `event_simulator`. */
  feature: string
  children: React.ReactNode
}) {
  const { ent, has, loading } = useEntitlements()
  const { t } = useLanguage()

  if (loading) return <LoadingState />

  // "We checked and you do not have it" and "we could not check" are different
  // facts and must not share a screen. `has()` fails closed, which is right for
  // a permission — but rendering the upsell when the entitlements request simply
  // failed tells a paying customer their plan does not include something it
  // does, and sends them to /planes to buy it again.
  if (!ent) {
    return (
      <EmptyState
        icon={<AlertTriangle size={22} aria-hidden="true" />}
        title={t('entitlements.unverified_title')}
        body={t('entitlements.unverified_body')}
        actions={[{ label: t('common.retry'), onClick: () => window.location.reload() }]}
      />
    )
  }

  if (has(feature)) return <>{children}</>

  return (
    <EmptyState
      icon={<Lock size={22} aria-hidden="true" />}
      title={t('entitlements.upsell_title')}
      body={t('entitlements.upsell_body')}
      actions={[{ label: t('entitlements.upsell_cta'), href: '/planes' }]}
    />
  )
}
