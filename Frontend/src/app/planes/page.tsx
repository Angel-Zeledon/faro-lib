'use client'
/**
 * Plan comparison page. Reached from the "Ver planes" entry at the foot of the
 * sidebar, from the gate on /integraciones, and from the read-only banner when
 * a trial has expired. Shows the three plans, highlights the tenant's current
 * one, and — since automated billing lands later — routes upgrades to a
 * contact action instead of a checkout.
 *
 * It used to be reached through a modal opened by the padlocked nav items.
 * Those padlocks are gone (four of fourteen nav entries taught a new tenant
 * mainly about what they could not use), and with them the modal's only
 * trigger, so the modal went too rather than sit unreferenced. Contextual
 * upsell — offering the plan at the moment someone reaches for the feature —
 * is worth building deliberately, with a real trigger, not by keeping a
 * component alive in the hope of one.
 */
import { useEntitlements } from '@/lib/entitlements'
import { useLanguage } from '@/contexts/LanguageContext'
import { Check } from 'lucide-react'

type PlanId = 'starter' | 'professional' | 'enterprise'

interface PlanRow {
  id: PlanId
  price: number
  custom?: boolean
  skus: string
  users: string
  locations: string
  taglineKey: string
}

const PLANS: PlanRow[] = [
  { id: 'starter',      price: 99,   skus: '1.000', users: '2',  locations: '1', taglineKey: 'planes.starter_tagline' },
  { id: 'professional', price: 649,  skus: '5.000', users: '10', locations: '5', taglineKey: 'planes.pro_tagline' },
  { id: 'enterprise',   price: 1990, custom: true, skus: '∞',     users: '∞',  locations: '∞', taglineKey: 'planes.ent_tagline' },
]

const RANK: Record<PlanId, number> = { starter: 0, professional: 1, enterprise: 2 }
const PLAN_LABEL: Record<PlanId, string> = { starter: 'Starter', professional: 'Professional', enterprise: 'Enterprise' }

export default function PlanesPage() {
  const { t } = useLanguage()
  const { ent } = useEntitlements()
  const currentPlan = (ent?.plan as PlanId) || 'starter'
  const currentRank = RANK[currentPlan] ?? 0

  return (
    <div style={{ maxWidth: 1040, margin: '0 auto', padding: '40px 24px 64px' }}>
      <header style={{ marginBottom: 36 }}>
        <h1 style={{ margin: 0, fontSize: 26, fontWeight: 800, letterSpacing: '-0.02em', color: 'var(--text)' }}>
          {t('planes.title')}
        </h1>
        <p style={{ margin: '8px 0 0', fontSize: 14.5, lineHeight: 1.55, color: 'var(--dim)', maxWidth: 560 }}>
          {t('planes.subtitle')}
        </p>
      </header>

      <div
        style={{
          display: 'grid', gap: 18,
          gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
          alignItems: 'stretch',
        }}
      >
        {PLANS.map((p) => {
          const isCurrent = p.id === currentPlan
          const isUpgrade = RANK[p.id] > currentRank
          return (
            <section
              key={p.id}
              aria-current={isCurrent ? 'true' : undefined}
              style={{
                display: 'flex', flexDirection: 'column',
                background: 'var(--surface)',
                border: isCurrent
                  ? '1.5px solid var(--accent)'
                  : '1px solid var(--border)',
                borderRadius: 16, padding: 24,
                boxShadow: isCurrent
                  ? '0 0 0 4px color-mix(in srgb, var(--accent) 12%, transparent)'
                  : 'none',
                position: 'relative',
              }}
            >
              {isCurrent && (
                <span
                  style={{
                    position: 'absolute', top: -11, left: 24,
                    padding: '3px 10px', borderRadius: 999,
                    background: 'var(--accent)', color: '#fff',
                    fontSize: 11, fontWeight: 700, letterSpacing: '0.01em',
                  }}
                >
                  {t('planes.current_badge')}
                </span>
              )}

              <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: 'var(--text)' }}>
                {PLAN_LABEL[p.id]}
              </h2>

              {p.custom ? (
                <div style={{ margin: '10px 0 4px' }}>
                  <div style={{ fontSize: 26, fontWeight: 800, color: 'var(--text)', letterSpacing: '-0.02em', lineHeight: 1.1 }}>
                    Personalizado
                  </div>
                  <div style={{ fontSize: 13, color: 'var(--dim)', marginTop: 3 }}>
                    Desde ${p.price.toLocaleString('es-CR')}{t('planes.per_month')}
                  </div>
                </div>
              ) : (
                <div style={{ margin: '10px 0 4px', display: 'flex', alignItems: 'baseline', gap: 4 }}>
                  <span style={{ fontSize: 30, fontWeight: 800, color: 'var(--text)', letterSpacing: '-0.02em' }}>
                    ${p.price}
                  </span>
                  <span style={{ fontSize: 13, color: 'var(--dim)' }}>{t('planes.per_month')}</span>
                </div>
              )}

              <p style={{ margin: '10px 0 18px', fontSize: 13, lineHeight: 1.6, color: 'var(--dim)', minHeight: 62 }}>
                {t(p.taglineKey)}
              </p>

              <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 9 }}>
                {[
                  `${p.skus} SKUs`,
                  `${p.users} ${t(p.users === '1' ? 'planes.limit_user' : 'planes.limit_users')}`,
                  `${p.locations} ${t(p.locations === '1' ? 'planes.limit_location' : 'planes.limit_locations')}`,
                ].map((line) => (
                  <li key={line} style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 13, color: 'var(--text)' }}>
                    <Check size={15} style={{ color: 'var(--accent)', flexShrink: 0 }} aria-hidden />
                    <span>{line}</span>
                  </li>
                ))}
              </ul>

              <div style={{ flex: 1 }} />

              <div style={{ marginTop: 22 }}>
                {isCurrent ? (
                  <div
                    style={{
                      textAlign: 'center', padding: '10px 16px', borderRadius: 10,
                      border: '1px solid var(--border)', color: 'var(--dim)',
                      fontSize: 13, fontWeight: 600,
                    }}
                  >
                    {t('planes.cta_current')}
                  </div>
                ) : (
                  <a
                    href={`mailto:hola@usefaro.io?subject=${encodeURIComponent(`Faro — activar plan ${PLAN_LABEL[p.id]}`)}`}
                    style={{
                      display: 'block', textAlign: 'center', padding: '10px 16px', borderRadius: 10,
                      textDecoration: 'none', fontSize: 13, fontWeight: 700,
                      background: isUpgrade ? 'var(--accent)' : 'transparent',
                      color: isUpgrade ? '#fff' : 'var(--dim)',
                      border: isUpgrade ? '1px solid var(--accent)' : '1px solid var(--border)',
                    }}
                  >
                    {isUpgrade ? t('planes.cta_upgrade') : t('planes.cta_switch')}
                  </a>
                )}
              </div>
            </section>
          )
        })}
      </div>

      <p style={{ margin: '28px 0 0', fontSize: 12.5, lineHeight: 1.6, color: 'var(--dim)', textAlign: 'center' }}>
        {t('planes.billing_note')}
      </p>
    </div>
  )
}
