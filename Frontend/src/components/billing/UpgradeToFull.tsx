'use client'
import { useCallback, useEffect, useState } from 'react'
import { CreditCard } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import {
  getBillingStatus, startCheckout, type BillingProvider, type BillingStatus,
} from '@/lib/api'
import { getUser } from '@/lib/auth'

/**
 * Buying the Full plan online, from wherever a ceiling or a locked feature
 * sends somebody — and, on an installation without payments, the same choice
 * drawn inert so the shape of it is already there.
 *
 * Two modes, one layout (owner, 2026-10-05):
 *
 * - **live**: the backend says this tenant can buy (a provider is configured,
 *   the tenant is on free, not a trial, not corporate, not already paying).
 *   One button per CONFIGURED method — card (Stripe), Link (Stripe's one-click
 *   wallet, offered on the same Stripe checkout, so only with Stripe) and
 *   PayPal. A click asks our backend for the provider's hosted URL and the
 *   browser leaves for it. No card field ever renders here.
 * - **placeholder**: nothing is configured. The three methods render disabled
 *   with a "coming soon" tag and a tooltip saying payments are not active yet;
 *   "write to us" around it stays the action that works.
 *
 * Enabling a provider later is configuration, not new UI: the same component
 * switches from placeholder to live when /billing/status says so.
 */

/** Billing status, read once and silently: a failure reads as "not live". */
export function useBillingStatus(): {
  status: BillingStatus | null
  /** False until the first answer (or failure): callers draw nothing yet, so
   *  the inert placeholder never flashes before the live buttons. */
  loaded: boolean
  reload: () => Promise<void>
} {
  const [status, setStatus] = useState<BillingStatus | null>(null)
  const [loaded, setLoaded] = useState(false)
  const reload = useCallback(async () => {
    try {
      setStatus(await getBillingStatus({ silent: true }))
    } catch {
      setStatus(null)
    } finally {
      setLoaded(true)
    }
  }, [])
  useEffect(() => { void reload() }, [reload])
  return { status, loaded, reload }
}

export function formatPrice(amount: number | null, currency: string, lang: string): string {
  if (amount == null) return ''
  try {
    return new Intl.NumberFormat(lang === 'es' ? 'es' : 'en', {
      style: 'currency', currency, maximumFractionDigits: amount % 1 === 0 ? 0 : 2,
    }).format(amount)
  } catch {
    return `${amount} ${currency}`
  }
}

/** True when this tenant can buy online right now. */
export function canBuyOnline(status: BillingStatus | null): boolean {
  return !!status?.can_purchase && status.payments.providers.length > 0
}

/** True when the inert payment choice should be drawn: payments are not
 *  configured (or the status could not be read) and nothing about the tenant
 *  rules a purchase out anyway — a corporate, trial or hand-set paid account
 *  has nothing to buy, so it gets no mould. */
export function showPaymentPlaceholder(status: BillingStatus | null): boolean {
  if (!status) return true
  if (status.payments.enabled) return false
  return status.tier === 'free'
}

// The methods, in the order a buyer expects them. `via` is the backend
// provider that actually takes the payment.
type Method = 'card' | 'link' | 'paypal'
const METHODS: { key: Method; via: BillingProvider }[] = [
  { key: 'card', via: 'stripe' },
  { key: 'link', via: 'stripe' },
  { key: 'paypal', via: 'paypal' },
]

export default function UpgradeToFull({ status, compact = false }: {
  status: BillingStatus | null
  compact?: boolean
}) {
  const { t, lang } = useLanguage()
  const [busy, setBusy] = useState<Method | null>(null)
  const [failed, setFailed] = useState(false)
  const isAdmin = getUser()?.role === 'admin'

  const live = canBuyOnline(status)
  if (!live && !showPaymentPlaceholder(status)) return null

  const price = live && status
    ? formatPrice(status.payments.price_usd_monthly, status.payments.currency, lang)
    : ''

  if (live && !isAdmin) {
    // Paying is the account owner's decision; say who can make it rather than
    // offering a button that answers 403.
    return (
      <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.55, color: 'var(--dim)' }}>
        {t('billing.upgrade.ask_admin', { price })}
      </p>
    )
  }

  async function go(method: Method, provider: BillingProvider) {
    setBusy(method); setFailed(false)
    try {
      const { url } = await startCheckout(provider)
      window.location.assign(url)
    } catch {
      // The api layer already toasted the specific reason (errors.billing_*).
      setFailed(true)
      setBusy(null)
    }
  }

  const configured = new Set(status?.payments.providers ?? [])
  const methods = live ? METHODS.filter(m => configured.has(m.via)) : METHODS
  const soonTip = t('billing.placeholder.tooltip')

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 10,
      padding: compact ? 0 : '14px 16px', borderRadius: 10,
      border: compact ? 'none' : `1px solid ${live ? 'var(--accent)' : 'var(--border)'}`,
      background: compact ? 'transparent'
        : live ? 'color-mix(in srgb, var(--accent) 7%, transparent)' : 'var(--surface-2)',
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13.5, fontWeight: 700, color: live ? 'var(--text)' : 'var(--dim)' }}>
          {t('billing.upgrade.title')}
        </span>
        {price && (
          <span style={{ fontSize: 13, color: 'var(--dim)', fontVariantNumeric: 'tabular-nums' }}>
            {t('billing.upgrade.price', { price })}
          </span>
        )}
      </div>

      <div
        role="group"
        aria-label={t('billing.upgrade.methods_label')}
        style={{
          display: 'grid', gap: 8,
          gridTemplateColumns: 'repeat(auto-fit, minmax(132px, 1fr))',
        }}
      >
        {methods.map(m => live ? (
          <button
            key={m.key}
            type="button"
            className="btn"
            onClick={() => go(m.key, m.via)}
            disabled={busy !== null}
            aria-busy={busy === m.key}
            style={methodStyle(m.key, true)}
          >
            <MethodMark method={m.key} label={t(`billing.method.${m.key}`)} />
            {busy === m.key && <span style={{ fontSize: 11, opacity: 0.8 }}>{t('billing.upgrade.opening')}</span>}
          </button>
        ) : (
          // Inert on purpose: aria-disabled (not `disabled`) keeps it
          // focusable, so a keyboard or screen-reader user still hears why.
          <button
            key={m.key}
            type="button"
            aria-disabled="true"
            title={soonTip}
            aria-label={`${t(`billing.method.${m.key}`)}. ${t('billing.placeholder.soon')}. ${soonTip}`}
            onClick={(e) => e.preventDefault()}
            style={methodStyle(m.key, false)}
          >
            <MethodMark method={m.key} label={t(`billing.method.${m.key}`)} />
            <span style={{
              fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 999,
              background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--muted)',
              whiteSpace: 'nowrap',
            }}>
              {t('billing.placeholder.soon')}
            </span>
          </button>
        ))}
      </div>

      <p style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5, color: 'var(--muted)' }}>
        {live ? t('billing.upgrade.hosted_note') : soonTip}
      </p>
      {failed && (
        <p role="alert" style={{ margin: 0, fontSize: 12.5, color: '#C0504D' }}>
          {t('billing.upgrade.failed')}
        </p>
      )}
    </div>
  )
}

// ── The marks ────────────────────────────────────────────────────────────────
// Wordmark-style text in each method's recognisable colour, never a copied
// logo asset. Colours are the methods' public brand hues, used as accents.

const BRAND: Record<Method, string> = {
  card: '#635BFF',   // Stripe's indigo
  link: '#00A85A',   // Link's green, darkened enough to read on white
  paypal: '#003087', // PayPal's navy
}

function methodStyle(method: Method, live: boolean): React.CSSProperties {
  return {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
    minHeight: 44, padding: '8px 12px', borderRadius: 8,
    background: 'var(--surface)', color: 'var(--text)',
    border: `1px solid ${live ? BRAND[method] : 'var(--border)'}`,
    borderLeft: `4px solid ${BRAND[method]}`,
    cursor: live ? 'pointer' : 'not-allowed',
    opacity: live ? 1 : 0.62,
    fontSize: 13, fontWeight: 600, textAlign: 'left',
  }
}

function MethodMark({ method, label }: { method: Method; label: string }) {
  if (method === 'card') {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
        <CreditCard size={15} aria-hidden="true" style={{ color: BRAND.card, flexShrink: 0 }} />
        <span>{label}</span>
      </span>
    )
  }
  if (method === 'paypal') {
    // Two-tone italic wordmark, set in type: "Pay" navy, "Pal" lighter blue.
    return (
      <span aria-label={label} style={{ fontStyle: 'italic', fontWeight: 800, letterSpacing: -0.2, fontSize: 14 }}>
        <span aria-hidden="true" style={{ color: BRAND.paypal }}>Pay</span>
        <span aria-hidden="true" style={{ color: '#0070E0' }}>Pal</span>
      </span>
    )
  }
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
      <span aria-hidden="true" style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: 18, height: 18, borderRadius: 5, background: BRAND.link, color: '#fff',
        fontSize: 11, fontWeight: 800,
      }}>›</span>
      <span>{label}</span>
    </span>
  )
}
