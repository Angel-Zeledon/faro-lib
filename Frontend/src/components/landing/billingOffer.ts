'use client'
import { useEffect, useState } from 'react'
import { FULL_PLAN } from '@/components/landing/pricingModel'

/**
 * Whether the landing may honestly say "start the Full plan" online.
 *
 * True only when this installation's backend says it sells the Full plan
 * online (GET /api/v1/billing/offer) AND the monthly price it would charge is
 * the one this page prints (pricingModel.ts). Anything else — the backend
 * unreachable, billing not configured, a price that drifted — is false, and
 * the page keeps its "write to us" story, which is then the true one.
 *
 * Unauthenticated and read once per page view; failure is silent on purpose:
 * a pricing page must never show an error because a payment provider is off.
 */
export function useOnlineCheckout(): boolean {
  const [ok, setOk] = useState(false)
  useEffect(() => {
    let alive = true
    fetch('/api/v1/billing/offer', { headers: { Accept: 'application/json' } })
      .then(r => (r.ok ? r.json() : null))
      .then(body => {
        const data = body?.data
        const honest = !!data?.online_checkout
          && typeof data?.price_usd_monthly === 'number'
          && Math.abs(data.price_usd_monthly - FULL_PLAN.baseMonthly) < 0.005
        if (alive) setOk(honest)
      })
      .catch(() => { if (alive) setOk(false) })
    return () => { alive = false }
  }, [])
  return ok
}
