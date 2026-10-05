'use client'
import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { openBillingPortal, type BillingStatus } from '@/lib/api'
import { getUser } from '@/lib/auth'
import Button from '@/components/ui/Button'
import UpgradeToFull, { useBillingStatus } from './UpgradeToFull'

/** BillingSection that reads its own status, under a divider — for Mi cuenta's
 *  "Uso y límites" card. Renders nothing until the status arrives, and nothing
 *  at all if it cannot be read (the limits above still say what matters). */
export function BillingPanel() {
  const { t } = useLanguage()
  const { status } = useBillingStatus()
  if (!status) return null
  return (
    <div style={{ marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
      <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text)', marginBottom: 8 }}>
        {t('billing.section.title')}
      </div>
      <BillingSection status={status} />
    </div>
  )
}

/**
 * The plan as money sees it: what this account is on, whether it renews, and
 * — the one thing worth shouting — a payment that failed and the date the plan
 * drops to free if nobody fixes it.
 *
 * Lives under "Uso y límites" in Mi cuenta and on /facturacion. Quiet by
 * design: an account nobody pays for online (free with no billing configured,
 * corporate, or a paid tier we set by hand) reads one line and no buttons.
 */
export default function BillingSection({ status }: { status: BillingStatus | null }) {
  const { t, lang } = useLanguage()
  const [opening, setOpening] = useState(false)
  const [failed, setFailed] = useState(false)
  const isAdmin = getUser()?.role === 'admin'

  if (!status) return null
  const sub = status.subscription
  const fmt = (iso: string | null) => iso
    ? new Date(iso).toLocaleDateString(lang === 'es' ? 'es' : 'en', {
        day: 'numeric', month: 'long', year: 'numeric',
      })
    : ''

  const pastDue = sub?.status === 'past_due'
  const ending = !!sub && (sub.cancel_at_period_end || sub.status === 'canceled') && !!sub.access_until
  const renewing = !!sub && (sub.status === 'active' || sub.status === 'trialing') && !sub.cancel_at_period_end

  async function manage() {
    setOpening(true); setFailed(false)
    try {
      const { url } = await openBillingPortal(sub?.provider)
      window.location.assign(url)
    } catch {
      setFailed(true)
      setOpening(false)
    }
  }

  // Who decides how this plan is paid, in one sentence.
  let line: string
  if (status.tier === 'corporate') line = t('billing.line.corporate')
  else if (status.tier === 'demo') line = t('billing.line.trial')
  else if (status.tier === 'paid' && status.tier_source === 'manual') line = t('billing.line.manual')
  else if (renewing && sub) line = t('billing.line.renews', { date: fmt(sub.current_period_end), provider: t(`billing.provider.${sub.provider}`) })
  else if (ending && sub) line = t('billing.line.ends', { date: fmt(sub.access_until) })
  else if (status.tier === 'free') line = t('billing.line.free')
  else line = t('billing.line.paid')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {pastDue && sub && (
        <div role="alert" style={{
          display: 'flex', gap: 10, alignItems: 'flex-start',
          padding: '12px 14px', borderRadius: 10,
          background: 'color-mix(in srgb, #B7791F 12%, transparent)',
          border: '1px solid color-mix(in srgb, #B7791F 45%, transparent)',
        }}>
          <AlertTriangle size={16} aria-hidden="true" style={{ color: '#B7791F', flexShrink: 0, marginTop: 1 }} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>
              {t('billing.past_due.title')}
            </div>
            <p style={{ margin: '3px 0 0', fontSize: 12.5, lineHeight: 1.55, color: 'var(--text)' }}>
              {t('billing.past_due.body', { date: fmt(sub.grace_until) })}
            </p>
          </div>
        </div>
      )}

      <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--text)' }}>{line}</p>

      {ending && sub && (
        <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.55, color: 'var(--dim)' }}>
          {t('billing.cancel_notice', { date: fmt(sub.access_until) })}
        </p>
      )}

      {status.can_manage && isAdmin && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <Button variant={pastDue ? 'primary' : 'secondary'} loading={opening} onClick={manage}>
            {t(pastDue ? 'billing.manage.fix_payment' : 'billing.manage.cta')}
          </Button>
          {sub?.provider === 'paypal' && (
            <span style={{ fontSize: 11.5, color: 'var(--muted)' }}>{t('billing.manage.paypal_note')}</span>
          )}
        </div>
      )}
      {failed && (
        <p role="alert" style={{ margin: 0, fontSize: 12.5, color: '#C0504D' }}>
          {t('billing.manage.failed')}
        </p>
      )}

      <UpgradeToFull status={status} />

      {/* An admin on an installation without billing: say what is missing so
          they can ask whoever runs it, instead of a button that is not there. */}
      {isAdmin && !status.payments.enabled && status.payments.missing && status.tier === 'free' && (
        <p style={{ margin: 0, fontSize: 11.5, lineHeight: 1.55, color: 'var(--muted)' }}>
          {t('billing.off_admin_hint', {
            stripe: (status.payments.missing.stripe ?? []).join(', '),
            paypal: (status.payments.missing.paypal ?? []).join(', '),
          })}
        </p>
      )}
    </div>
  )
}
