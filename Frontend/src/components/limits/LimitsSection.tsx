'use client'
import { useEffect } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useEntitlements, LIMIT_KEYS } from '@/lib/entitlements'
import { useUpgradePrompt, ContactButtons } from './UpgradeDialog'
import Button from '@/components/ui/Button'

/**
 * How much room this tenant has left, on the screen they already visit.
 *
 * A ceiling nobody can see is a trap: the free tier only works as an honest
 * offer if "100 SKUs" is a number you watch yourself approaching, not one you
 * discover the morning an import fails. So this is a panel, not a banner, and
 * it renders on both tiers — a paid tenant reads "unlimited" and learns the
 * same thing.
 */
export default function LimitsSection() {
  const { t } = useLanguage()
  const { ent, refresh } = useEntitlements()
  const openUpgrade = useUpgradePrompt()

  // The provider loads once at app start; by the time somebody opens Mi cuenta
  // the numbers may be an hour old and this is the one screen that shows them.
  useEffect(() => { void refresh() }, [refresh])

  if (!ent) return null
  const free = ent.tier === 'free'

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span style={{
          fontSize: 11, fontWeight: 700, letterSpacing: 0.3, padding: '3px 9px',
          borderRadius: 999, textTransform: 'uppercase',
          background: free ? 'var(--surface-2)' : 'var(--accent)',
          color: free ? 'var(--muted)' : '#fff',
          border: '1px solid var(--border)',
        }}>
          {t(free ? 'limits.tier.free' : 'limits.tier.paid')}
        </span>
      </div>
      <p style={{ margin: '0 0 18px', fontSize: 12.5, lineHeight: 1.6, color: 'var(--dim)' }}>
        {t(free ? 'limits.section.subtitle_free' : 'limits.section.subtitle_paid')}
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {LIMIT_KEYS.map((key) => (
          <LimitRow
            key={key}
            name={t(`limits.name.${key}`)}
            used={ent.usage?.[key] ?? 0}
            max={ent.limits?.[key] ?? null}
            unlimitedLabel={t('limits.unlimited')}
            usageLabel={(current, max) => t('limits.usage_of', { current, max })}
          />
        ))}
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 20 }}>
        <Button variant="primary" onClick={() => openUpgrade(null)}>
          {t('limits.section.cta')}
        </Button>
        <ContactButtons
          whatsapp={ent.contact?.whatsapp || ''}
          email={ent.contact?.email || ''}
          t={t}
        />
      </div>
    </div>
  )
}

function LimitRow({ name, used, max, unlimitedLabel, usageLabel }: {
  name: string
  used: number
  max: number | null
  unlimitedLabel: string
  usageLabel: (current: number, max: number) => string
}) {
  const unlimited = max === null
  // Clamped, because usage CAN exceed a ceiling legitimately: a tenant moved
  // down a tier, or a limit lowered by hand, keeps the rows it already had. A
  // bar past 100% would render outside its track and read as a rendering bug.
  const pct = unlimited || max === 0 ? 0 : Math.min(100, Math.round((used / max) * 100))
  const tone = pct >= 100 ? '#ef4444' : pct >= 80 ? '#f59e0b' : 'var(--accent)'

  return (
    <div>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
        marginBottom: 6, gap: 12,
      }}>
        <span style={{ fontSize: 12.5, color: 'var(--text)', fontWeight: 500 }}>{name}</span>
        <span style={{
          fontSize: 12, fontWeight: 600, fontVariantNumeric: 'tabular-nums',
          color: unlimited ? 'var(--dim)' : tone,
        }}>
          {unlimited ? unlimitedLabel : usageLabel(used, max as number)}
        </span>
      </div>
      {!unlimited && (
        <div style={{
          height: 5, borderRadius: 999, background: 'var(--surface-2)',
          border: '1px solid var(--border)', overflow: 'hidden',
        }}>
          <div style={{
            width: `${pct}%`, height: '100%', background: tone,
            transition: 'width 220ms ease',
          }} />
        </div>
      )}
    </div>
  )
}
