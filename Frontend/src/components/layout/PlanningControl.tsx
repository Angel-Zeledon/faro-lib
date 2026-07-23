'use client'
import { useState, useEffect } from 'react'
import { getPlanning, setPlanning, isApiError } from '@/lib/api'
import type { PlanningState, PlanningPeriod } from '@/lib/types'
import { getUser } from '@/lib/auth'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'

const PERIOD_KEY: Record<PlanningPeriod, string> = {
  daily: 'planning.daily', weekly: 'planning.weekly', monthly: 'planning.monthly',
}
const UNIT_KEY: Record<PlanningPeriod, string> = {
  daily: 'planning.unit_daily', weekly: 'planning.unit_weekly', monthly: 'planning.unit_monthly',
}

export default function PlanningControl() {
  const { t } = useLanguage()
  const { addToast } = useToast()
  const [state, setState] = useState<PlanningState | null>(null)
  const [busy,  setBusy]  = useState(false)
  const isAdmin = getUser()?.role === 'admin'

  useEffect(() => { getPlanning().then(setState).catch(() => setState(null)) }, [])

  // Mono-period (family of one) or not loaded → render nothing.
  if (!state || state.available_periods.length <= 1) return null

  const disabled = !isAdmin || busy

  async function apply(period: PlanningPeriod, horizon: number) {
    if (!state) return
    const capped = Math.max(1, Math.min(horizon, state.max_horizon))
    setBusy(true)
    try {
      const next = await setPlanning(period, capped)
      setState(next)
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

      <span style={{ color: 'var(--dim)', marginLeft: 4 }}>{t('planning.horizon_label')}</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <button
          disabled={disabled || state.horizon <= 1}
          onClick={() => apply(state.period, state.horizon - 1)}
          style={stepBtn(disabled || state.horizon <= 1)}
        >−</button>
        <span style={{ minWidth: 62, textAlign: 'center', fontVariantNumeric: 'tabular-nums' }}>
          {state.horizon} {t(UNIT_KEY[state.period])}
        </span>
        <button
          disabled={disabled || state.horizon >= state.max_horizon}
          onClick={() => apply(state.period, state.horizon + 1)}
          style={stepBtn(disabled || state.horizon >= state.max_horizon)}
        >+</button>
      </div>
    </div>
  )
}

function stepBtn(off: boolean): React.CSSProperties {
  return {
    width: 22, height: 22, borderRadius: 6,
    border: '1px solid var(--border)', background: 'var(--surface-2)',
    color: off ? 'var(--dim)' : 'var(--text)',
    cursor: off ? 'default' : 'pointer', lineHeight: 1,
  }
}
