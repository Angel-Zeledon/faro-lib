'use client'
/**
 * Cash calendar for the /hoy cart (feature 3.6).
 *
 * Shows what already falls due from POs the business has SENT, and — once the
 * buyer types the cash they have available — whether the cart on screen fits.
 * Every number and the fits/does-not-fit verdict come from the backend, which
 * dates each line by its own supplier's credit terms. The budget is typed by
 * the user because Faro stores no cash balance; with no budget the panel
 * reports totals and shows no verdict rather than guessing one.
 */
import { useState } from 'react'
import { Wallet, AlertTriangle, Check } from 'lucide-react'
import type { CashCalendar, CashFitResult } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'

const GREEN = '#22c55e'
const AMBER = '#f59e0b'
const RED   = '#ef4444'

export function CashFitPanel({
  calendar, fit, currency, onBudgetChange, busy,
}: {
  calendar:       CashCalendar | null
  fit:            CashFitResult | null
  currency:       (n: number) => string
  onBudgetChange: (budget: number | null) => void
  busy:           boolean
}) {
  const { t } = useLanguage()
  const [budgetInput, setBudgetInput] = useState('')

  if (!calendar) return null

  const hasCommitments =
    calendar.this_week_total > 0 || calendar.overdue_total > 0 || calendar.horizon_total > 0
  if (!hasCommitments && !fit) return null

  const verdictColor = fit?.fits == null ? 'var(--border)' : fit.fits ? GREEN : RED

  function submitBudget(raw: string) {
    const parsed = Number(raw.replace(/[^\d.-]/g, ''))
    onBudgetChange(Number.isFinite(parsed) && parsed > 0 ? parsed : null)
  }

  return (
    <div style={{
      marginTop: 10, borderRadius: 10, padding: '12px 16px',
      background: 'var(--surface-2)', border: `1px solid ${verdictColor}40`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <Wallet size={14} color={AMBER} style={{ flexShrink: 0 }} />
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', flex: 1 }}>
          {t('cash.title')}
        </span>
      </div>

      <div style={{ fontSize: 12, color: 'var(--text)' }}>
        {t('cash.this_week_prefix')}{' '}
        <strong>{currency(calendar.this_week_total)}</strong> {t('cash.this_week_suffix')}
        {calendar.overdue_total > 0 && (
          <span style={{ color: RED }}>
            {' · '}<strong>{currency(calendar.overdue_total)}</strong> {t('cash.overdue_suffix')}
          </span>
        )}
        {calendar.horizon_total > 0 && (
          <span style={{ color: 'var(--dim)' }}>
            {' · '}{currency(calendar.horizon_total)} {t('cash.horizon_suffix')}
          </span>
        )}
      </div>

      {calendar.unknown_terms_total > 0 && (
        <div style={{ fontSize: 11, color: AMBER, marginTop: 4 }}>
          <AlertTriangle size={10} style={{ verticalAlign: -1, marginRight: 4 }} />
          {currency(calendar.unknown_terms_total)} {t('cash.unknown_terms')}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
        <label htmlFor="cash-budget" style={{ fontSize: 12, color: 'var(--dim)' }}>
          {t('cash.budget_label')}
        </label>
        <input
          id="cash-budget"
          value={budgetInput}
          onChange={e => setBudgetInput(e.target.value)}
          onBlur={e => submitBudget(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') submitBudget(budgetInput) }}
          inputMode="decimal"
          placeholder={t('cash.budget_placeholder')}
          style={{
            width: 130, padding: '5px 9px', fontSize: 12,
            background: 'var(--surface)', color: 'var(--text)',
            border: '1px solid var(--border)', borderRadius: 7,
          }}
        />
        {busy && <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('cash.checking')}</span>}
      </div>

      {fit && fit.fits != null && (
        <div style={{
          marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--border)',
          fontSize: 12, color: fit.fits ? GREEN : RED, fontWeight: 600,
          display: 'flex', alignItems: 'flex-start', gap: 6,
        }}>
          {fit.fits
            ? <Check size={13} style={{ flexShrink: 0, marginTop: 1 }} />
            : <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />}
          <span>
            {fit.fits ? t('cash.fits') : t('cash.does_not_fit')}
            {' — '}
            <span style={{ color: 'var(--dim)', fontWeight: 400 }}>
              {t('cash.committed_label')}: {currency(fit.committed_total)}
              {' + '}{t('cash.purchase_label')}: {currency(fit.purchase_in_horizon)}
              {' = '}{currency(fit.required_total)}
              {!fit.fits && fit.shortfall != null && fit.shortfall > 0 && (
                <strong style={{ color: RED }}>
                  {' · '}{t('cash.shortfall_label')}: {currency(fit.shortfall)}
                </strong>
              )}
            </span>
          </span>
        </div>
      )}

      {fit && fit.purchase_total > fit.purchase_in_horizon && (
        <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 4 }}>
          {t('cash.deferred_note_prefix')}{' '}
          {currency(fit.purchase_total - fit.purchase_in_horizon)}{' '}
          {t('cash.deferred_note_suffix')}
        </div>
      )}

      {fit && fit.suppliers_assumed_immediate.length > 0 && (
        <div style={{ fontSize: 11, color: AMBER, marginTop: 4 }}>
          <AlertTriangle size={10} style={{ verticalAlign: -1, marginRight: 4 }} />
          {t('cash.assumed_immediate')}: {fit.suppliers_assumed_immediate.join(', ')}
        </div>
      )}
    </div>
  )
}
