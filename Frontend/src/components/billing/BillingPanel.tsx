'use client'
/**
 * The company's plan, and the two buttons that change it.
 *
 * Deliberately thin. Both buttons do the same thing: ask the server for a Stripe
 * URL and go there. No card field, no price arithmetic, no plan name posted as
 * state — the plan only ever moves when Stripe calls the webhook back, so
 * anything this component believed about the outcome would be a guess.
 *
 * It renders nothing at all when the deployment has no Stripe key: a buy button
 * that cannot work is worse than no button.
 */
import { useCallback, useEffect, useState } from 'react'
import { CreditCard, ExternalLink, AlertTriangle, Check } from 'lucide-react'

import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'
import { getSubscription, startCheckout, openBillingPortal } from '@/lib/api'
import type { SubscriptionState } from '@/lib/types'
import { getUser } from '@/lib/auth'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'

type Interval = 'monthly' | 'yearly'

/** Statuses that mean money is owed. Stripe's words, not ours. */
const NEEDS_ATTENTION = new Set(['past_due', 'unpaid', 'incomplete'])

export default function BillingPanel() {
  const { t } = useLanguage()
  const toast = useToast()
  const me = getUser()
  const isAdmin = me?.role === 'admin'

  const [state, setState] = useState<SubscriptionState | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [interval, setInterval] = useState<Interval>('monthly')

  const load = useCallback(() => {
    setLoading(true)
    getSubscription({ silent: true })
      .then(setState)
      .catch(() => setState(null))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  // Coming back from a Stripe checkout, the webhook may not have landed yet, so
  // the plan on screen can still be the old one. Re-read shortly after instead
  // of asserting success from a query parameter the browser controls.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('checkout') !== 'done') return
    toast.addToast(t('billing.section_title'), t('billing.checkout_processing'), 'info')
    const id = window.setTimeout(load, 4000)
    return () => window.clearTimeout(id)
  }, [load, toast, t])

  const goToStripe = async (what: 'checkout' | 'portal') => {
    setBusy(what)
    try {
      const { url } = what === 'checkout'
        ? await startCheckout('professional', interval)
        : await openBillingPortal()
      window.location.href = url
    } catch {
      /* the interceptor already surfaced it */
    } finally {
      setBusy(null)
    }
  }

  if (loading) {
    return <div style={{ padding: 16 }}><Spinner size={16} /></div>
  }
  // No key configured, or the endpoint is unreachable: say nothing.
  if (!state || !state.billing_enabled) return null

  const pro = state.purchasable?.professional
  const canBuy = Boolean(pro && Object.keys(pro).length)
  const owes = state.subscription_status
    ? NEEDS_ATTENTION.has(state.subscription_status)
    : false
  const paying = state.plan === 'professional' || state.plan === 'enterprise'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

      {/* What the company is on right now */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        padding: '12px 14px', borderRadius: 9,
        background: 'var(--surface-2)', border: '1px solid var(--border)',
      }}>
        <CreditCard size={15} color="var(--muted)" aria-hidden="true" />
        <span style={{ fontSize: 13, color: 'var(--text)', fontWeight: 600 }}>
          {t(`billing.plan_${state.plan ?? 'starter'}`)}
        </span>
        {state.subscription_status && (
          <span style={{
            fontSize: 11, padding: '2px 8px', borderRadius: 20,
            background: owes ? 'var(--signal-pedir-ya-bg)' : 'var(--surface-3)',
            color: owes ? 'var(--signal-pedir-ya-fg)' : 'var(--muted)',
          }}>
            {t(`billing.status_${state.subscription_status}`)}
          </span>
        )}
        {state.trial_ends_at && (
          <span style={{ fontSize: 11.5, color: 'var(--dim)' }}>
            {t('billing.trial_until', {
              date: new Date(state.trial_ends_at).toLocaleDateString(),
            })}
          </span>
        )}
      </div>

      {owes && (
        <div style={{
          display: 'flex', gap: 8, alignItems: 'flex-start',
          padding: '10px 12px', borderRadius: 9, fontSize: 12, lineHeight: 1.6,
          background: 'var(--signal-pedir-ya-bg)', color: 'var(--signal-pedir-ya-fg)',
        }}>
          <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
          {t('billing.payment_problem')}
        </div>
      )}

      {/* Buying, or managing what is already bought */}
      {!isAdmin ? (
        <p style={{ fontSize: 12, color: 'var(--dim)', lineHeight: 1.6, margin: 0 }}>
          {t('billing.admin_only')}
        </p>
      ) : paying ? (
        <div>
          <Button
            variant="secondary" size="sm"
            loading={busy === 'portal'}
            onClick={() => void goToStripe('portal')}
          >
            <ExternalLink size={13} /> {t('billing.manage')}
          </Button>
          <p style={{ fontSize: 11.5, color: 'var(--dim)', margin: '7px 0 0', lineHeight: 1.6 }}>
            {t('billing.manage_hint')}
          </p>
        </div>
      ) : canBuy ? (
        <div>
          <div style={{ fontSize: 12.5, color: 'var(--text)', fontWeight: 600, marginBottom: 4 }}>
            {t('billing.upgrade_title')}
          </div>
          <p style={{ fontSize: 11.5, color: 'var(--dim)', margin: '0 0 10px', lineHeight: 1.65, maxWidth: 620 }}>
            {t('billing.upgrade_hint')}
          </p>
          {/* Only intervals this deployment actually sells. */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
            {(['monthly', 'yearly'] as Interval[])
              .filter(i => pro?.[i])
              .map(i => (
                <button
                  key={i}
                  onClick={() => setInterval(i)}
                  style={{
                    all: 'unset', cursor: 'pointer', padding: '5px 11px', borderRadius: 7,
                    fontSize: 12, fontWeight: 600,
                    background: interval === i ? 'var(--accent-dim)' : 'transparent',
                    color: interval === i ? 'var(--accent)' : 'var(--muted)',
                    border: `1px solid ${interval === i ? 'var(--accent)' : 'var(--border)'}`,
                  }}
                >
                  {t(`billing.interval_${i}`)}
                </button>
              ))}
          </div>
          <Button
            size="sm"
            loading={busy === 'checkout'}
            onClick={() => void goToStripe('checkout')}
          >
            <Check size={13} /> {t('billing.upgrade_cta')}
          </Button>
          <p style={{ fontSize: 11, color: 'var(--dim)', margin: '8px 0 0', lineHeight: 1.6 }}>
            {t('billing.stripe_hosted')}
          </p>
        </div>
      ) : (
        // Billing is on, but this deployment has no price configured for the
        // plan yet. Say so, rather than offering a checkout that would 400.
        <p style={{ fontSize: 12, color: 'var(--dim)', lineHeight: 1.6, margin: 0 }}>
          {t('billing.not_for_sale_yet')}
        </p>
      )}
    </div>
  )
}
