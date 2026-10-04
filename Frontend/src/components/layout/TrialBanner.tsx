'use client'
/**
 * Shown on every authed page of a trial account (tier `demo`, handed out from
 * the landing — backend/trial/). It says the two things the visitor must not
 * find out by surprise: the account ends, and what they did in it goes with it.
 * And it carries the one action the trial exists for: talking to us.
 *
 * Not dismissable on purpose: it is the only place that says the data is about
 * to be erased.
 */
import { useEffect, useState } from 'react'
import { Clock } from 'lucide-react'
import { useEntitlements } from '@/lib/entitlements'
import { useUpgradePrompt } from '@/components/limits/UpgradeDialog'
import { useLanguage } from '@/contexts/LanguageContext'

function hoursLeft(endsAt: string): number {
  return Math.max(0, Math.ceil((new Date(endsAt).getTime() - Date.now()) / 3_600_000))
}

export default function TrialBanner() {
  const { ent } = useEntitlements()
  const openContact = useUpgradePrompt()
  const { t } = useLanguage()
  // Re-render once a minute so "ends in N hours" does not sit stale on a tab
  // left open all afternoon.
  const [, tick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => tick(n => n + 1), 60_000)
    return () => clearInterval(id)
  }, [])

  if (ent?.tier !== 'demo' || !ent.trial.ends_at) return null
  const hours = hoursLeft(ent.trial.ends_at)

  return (
    <div
      role="status"
      style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        padding: '10px 14px', borderRadius: 10, margin: '0 0 14px',
        background: 'color-mix(in srgb, var(--accent) 7%, transparent)',
        border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)',
      }}
    >
      <Clock size={14} style={{ flexShrink: 0, color: 'var(--accent)' }} />
      <span style={{ fontSize: 12.5, color: 'var(--text)', flex: 1, minWidth: 220 }}>
        <strong>{t('trial.banner.title')}</strong>{' · '}
        {hours <= 1 ? t('trial.banner.ends_soon') : t('trial.banner.ends_in', { hours })}
        {' '}{t('trial.banner.erased')}
      </span>
      <button
        type="button"
        onClick={() => openContact(null)}
        style={{
          all: 'unset', cursor: 'pointer',
          padding: '6px 12px', borderRadius: 7,
          background: 'var(--accent)', color: '#fff',
          fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap',
        }}
      >
        {t('trial.banner.cta')}
      </button>
    </div>
  )
}
