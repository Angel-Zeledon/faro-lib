'use client'
import { useState } from 'react'
import { isApiError } from '@/lib/api'
import type { PlanningPeriod } from '@/lib/types'
import { getUser } from '@/lib/auth'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'
import { usePlanning } from '@/contexts/PlanningContext'

const PERIOD_KEY: Record<PlanningPeriod, string> = {
  daily: 'planning.daily', weekly: 'planning.weekly', monthly: 'planning.monthly',
}

export default function PlanningControl() {
  const { t } = useLanguage()
  const { addToast } = useToast()
  const planningCtx = usePlanning()
  const state = planningCtx?.planning ?? null
  const [busy,  setBusy]  = useState(false)
  const isAdmin = getUser()?.role === 'admin'

  // Mono-period (family of one) or not loaded → render nothing.
  if (!state || state.available_periods.length <= 1) return null

  const disabled = !isAdmin || busy

  // The horizon is no longer edited here (it is chosen in the Quick Start
  // wizard at launch); switching the period keeps the stored horizon, clamped
  // to the new period's reach.
  async function apply(period: PlanningPeriod, horizon: number) {
    if (!state || !planningCtx) return
    const capped = Math.max(1, Math.min(horizon, state.max_horizon))
    setBusy(true)
    try {
      // Shared context: persists AND re-resolves the active session, so the
      // inventory/hoy screens re-fetch in the new period without a reload.
      await planningCtx.apply(period, capped)
      addToast(t('planning.saved'), '', 'success')
    } catch (e) {
      addToast(t('planning.save_error'), isApiError(e) ? e.detail : '', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
      <span style={{ color: 'var(--dim)' }}>{t('planning.period_label')}</span>
      <select
        name="planning_period"
        aria-label={t('planning.period_label')}
        value={state.period}
        disabled={disabled}
        onChange={e => apply(e.target.value as PlanningPeriod, state.horizon)}
        style={{
          background: 'var(--surface-2)', color: 'var(--text)',
          border: '1px solid var(--border)', borderRadius: 7,
          padding: '4px 8px', fontSize: 12, cursor: disabled ? 'default' : 'pointer',
        }}
      >
        {state.available_periods.map(p => (
          <option key={p} value={p}>{t(PERIOD_KEY[p])}</option>
        ))}
      </select>
    </div>
  )
}
