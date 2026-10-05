'use client'
import { useEffect } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useEntitlements } from '@/lib/entitlements'
import { useUpgradePrompt } from './UpgradeDialog'

/**
 * How many trainings the plan still allows today, said next to the button that
 * spends one. Shown only when it matters (the day's ceiling is spent, or one or
 * two are left): a plan with room to spare says nothing, and an unlimited plan
 * never renders anything. The launch itself is still refused by the backend
 * (`PLAN_LIMIT_REACHED`, which opens the "write to us" dialog) — this is the
 * warning BEFORE the click, so the refusal is never the first the user hears.
 */
export default function TrainingBudgetNote() {
  const { t } = useLanguage()
  const { ent, refresh } = useEntitlements()
  const openUpgrade = useUpgradePrompt()

  // The ceilings load once at app start; a launch made on this screen or
  // another tab since then has changed today's usage.
  useEffect(() => { void refresh() }, [refresh])

  const max = ent?.limits?.max_trainings_per_day
  if (!ent || max === null || max === undefined) return null
  const used = ent.usage?.trainings_today ?? 0
  const left = Math.max(0, max - used)
  if (left > Math.max(1, Math.floor(max * 0.2))) return null

  const spent = left === 0
  return (
    <p
      role="status"
      style={{
        margin: '12px 0 0', fontSize: 12.5, lineHeight: 1.5, textAlign: 'center',
        color: spent ? '#C0504D' : 'var(--dim)',
      }}
    >
      {spent
        ? t('limits.trainings.spent', { max })
        : t('limits.trainings.left', { left, max })}
      {spent && (
        <>
          {' '}
          <button
            type="button"
            onClick={() => openUpgrade('max_trainings_per_day')}
            style={{
              background: 'none', border: 'none', padding: 0, cursor: 'pointer',
              color: 'var(--accent)', fontSize: 'inherit', fontWeight: 600,
              textDecoration: 'underline',
            }}
          >
            {t('limits.trainings.ask_more')}
          </button>
        </>
      )}
    </p>
  )
}
