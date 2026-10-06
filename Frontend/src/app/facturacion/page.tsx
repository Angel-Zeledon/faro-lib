'use client'
/**
 * /facturacion — the plan and its payment, and where Stripe / PayPal send the
 * buyer back to.
 *
 * Coming back from a checkout is NOT the payment being confirmed: the tier
 * only changes when the provider's signed webhook reaches the backend, which
 * is usually seconds and occasionally minutes. So `?checkout=success` shows a
 * "confirming" state and polls /billing/status until the plan is active, and
 * says plainly when it is taking longer — never "done" on the strength of a
 * redirect anybody could type, and never an invitation to pay twice.
 */
import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, Loader2, Info } from 'lucide-react'
import Card from '@/components/ui/Card'
import { useLanguage } from '@/contexts/LanguageContext'
import { useEntitlements } from '@/lib/entitlements'
import { getBillingStatus, type BillingStatus } from '@/lib/api'
import BillingSection from '@/components/billing/BillingSection'

const POLL_MS = 2500
const GIVE_UP_MS = 90_000

type Phase = 'idle' | 'confirming' | 'confirmed' | 'slow' | 'cancelled'

function isPaidByBilling(s: BillingStatus | null): boolean {
  return !!s && s.tier === 'paid' && s.tier_source === 'billing'
}

export default function BillingPage() {
  const { t } = useLanguage()
  const { refresh } = useEntitlements()
  const [status, setStatus] = useState<BillingStatus | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [loadFailed, setLoadFailed] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const result = params.get('checkout')
    let stopped = false
    const started = Date.now()

    async function load(): Promise<BillingStatus | null> {
      try {
        const s = await getBillingStatus({ silent: true })
        if (!stopped) { setStatus(s); setLoadFailed(false) }
        return s
      } catch {
        if (!stopped) setLoadFailed(true)
        return null
      }
    }

    async function poll() {
      const s = await load()
      if (stopped) return
      if (isPaidByBilling(s)) {
        setPhase('confirmed')
        void refresh()
        return
      }
      if (Date.now() - started >= GIVE_UP_MS) {
        setPhase('slow')
        return
      }
      timer.current = setTimeout(poll, POLL_MS)
    }

    if (result === 'success') {
      setPhase('confirming')
      void poll()
    } else {
      if (result === 'cancelled') setPhase('cancelled')
      void load()
    }
    // Drop the query so a reload does not restart the "confirming" state.
    if (result) window.history.replaceState(null, '', window.location.pathname)
    return () => {
      stopped = true
      if (timer.current) clearTimeout(timer.current)
    }
  }, [refresh])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 680 }}>
      {phase !== 'idle' && <ResultNotice phase={phase} />}
      <Card>
        <h2 style={{ margin: '0 0 4px', fontSize: 16, fontWeight: 700, color: 'var(--text)' }}>
          {t('billing.page.title')}
        </h2>
        <p style={{ margin: '0 0 16px', fontSize: 12.5, color: 'var(--dim)' }}>
          {t('billing.page.subtitle')}
        </p>
        {loadFailed && !status ? (
          <p role="alert" style={{ margin: 0, fontSize: 13, color: '#C0504D' }}>
            {t('billing.page.load_failed')}
          </p>
        ) : (
          <BillingSection status={status} />
        )}
      </Card>
    </div>
  )
}

function ResultNotice({ phase }: { phase: Phase }) {
  const { t } = useLanguage()
  const tone = phase === 'confirmed' ? 'var(--accent)' : phase === 'slow' ? '#B7791F' : 'var(--border)'
  const Icon = phase === 'confirmed' ? CheckCircle2 : phase === 'confirming' ? Loader2 : Info
  return (
    <div role="status" aria-live="polite" style={{
      display: 'flex', gap: 12, alignItems: 'flex-start',
      padding: '14px 16px', borderRadius: 12,
      background: 'var(--surface)', border: `1px solid ${tone}`,
    }}>
      <Icon
        size={18} aria-hidden="true"
        style={{
          flexShrink: 0, marginTop: 1,
          color: phase === 'confirmed' ? 'var(--accent)' : 'var(--muted)',
          animation: phase === 'confirming' ? 'spin 1s linear infinite' : undefined,
        }}
      />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--text)' }}>
          {t(`billing.result.${phase}.title`)}
        </div>
        <p style={{ margin: '3px 0 0', fontSize: 12.5, lineHeight: 1.55, color: 'var(--dim)' }}>
          {t(`billing.result.${phase}.body`)}
        </p>
      </div>
    </div>
  )
}
