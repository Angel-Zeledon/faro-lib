'use client'
/**
 * Warns, above the cart, when the order about to be generated goes past what a
 * purchasing budget has left. The check is the server's (`/inventory/budget/check`,
 * the same one the order endpoint runs): this component only asks and shows.
 *
 *  - soft budget: a warning; the order still goes through. A reason is optional
 *    and is kept in the audit trail.
 *  - hard-capped budget: the order is refused unless an administrator gives a
 *    reason, which the audit trail records. Anyone else is told who can lift it.
 *
 * With no budget in force the check returns nothing and this renders nothing.
 */
import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { checkBudgetOrder } from '@/lib/api'
import type { BudgetExceeded } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { formatMoney } from '@/lib/currency'
import { getUser } from '@/lib/auth'

export interface CartCheckLine {
  sku: string; qty: number; unit_cost: number | null
  /** ISO code `unit_cost` is quoted in when it is not the company's own currency. */
  currency?: string | null
  supplier?: string | null; supplier_id?: string | null
}

export default function BudgetCartCheck({ lines, destination, costCenterId, reason, onReason, onExceeded }: {
  lines: CartCheckLine[]
  destination?: string
  costCenterId?: string
  reason: string
  onReason: (v: string) => void
  /** Lets the page know whether the cart is over a budget (for its own copy). */
  onExceeded?: (exceeded: BudgetExceeded[]) => void
}) {
  const { t } = useLanguage()
  const narrow = useIsNarrow()
  const isAdmin = getUser()?.role === 'admin'
  const [exceeded, setExceeded] = useState<BudgetExceeded[]>([])

  const signature = useMemo(
    () => JSON.stringify([destination ?? '', costCenterId ?? '', lines.map(l => [l.sku, l.qty, l.unit_cost, l.currency ?? '', l.supplier_id ?? l.supplier ?? ''])]),
    [lines, destination, costCenterId])

  useEffect(() => {
    if (lines.length === 0) { setExceeded([]); onExceeded?.([]); return }
    let alive = true
    const timer = setTimeout(() => {
      checkBudgetOrder(lines, destination, { silent: true }, costCenterId)
        .then(r => { if (alive) { setExceeded(r.exceeded); onExceeded?.(r.exceeded) } })
        // A failed preview must not look like "fits": the server check at
        // submit time still decides, so say nothing rather than guess.
        .catch(() => { if (alive) { setExceeded([]); onExceeded?.([]) } })
    }, 400)
    return () => { alive = false; clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature])

  if (exceeded.length === 0) return null
  const anyHard = exceeded.some(e => e.hard_cap)

  return (
    <div role="status" style={{
      marginBottom: 14, padding: narrow ? '12px 14px' : '12px 16px', borderRadius: 10,
      border: `1px solid ${anyHard ? '#C0504D66' : '#B7791F66'}`, background: 'var(--surface-2)',
      display: 'grid', gap: 8,
    }}>
      {exceeded.map((e, i) => (
        <p key={e.root_id ?? `hidden-${i}`} style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: 'var(--text)', display: 'flex', gap: 8, alignItems: 'flex-start' }}>
          <AlertTriangle size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 3, color: e.hard_cap ? '#C0504D' : '#B7791F' }} />
          <span>
            {e.exceeds === false
              ? t(e.hard_cap ? 'budget.cart_unconverted_hard' : 'budget.cart_unconverted_soft', { n: e.unconverted_lines ?? 0 })
              : e.visible
              ? t(e.hard_cap ? 'budget.cart_over_hard' : 'budget.cart_over_soft', {
                  over: formatMoney(e.over_by ?? 0), remaining: formatMoney(e.remaining ?? 0),
                })
              : t(e.hard_cap ? 'budget.cart_over_hidden_hard' : 'budget.cart_over_hidden_soft')}
            {e.exceeds !== false && e.unconverted_lines ? ` ${t('budget.cart_unconverted_extra', { n: e.unconverted_lines })}` : ''}
            {e.unknown_cost_lines ? ` ${t('budget.cart_unknown_cost', { n: e.unknown_cost_lines })}` : ''}
          </span>
        </p>
      ))}
      {(anyHard ? isAdmin : true) && (
        <label style={{ fontSize: narrow ? 13 : 12, color: 'var(--muted)', display: 'grid', gap: 4 }}>
          {anyHard ? t('budget.cart_reason_required') : t('budget.cart_reason_optional')}
          <input type="text" maxLength={500} value={reason} onChange={e => onReason(e.target.value)}
            style={{
              width: '100%', boxSizing: 'border-box', fontSize: narrow ? 16 : 12.5, padding: narrow ? '10px' : '6px 8px',
              borderRadius: narrow ? 10 : 7, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)',
              minHeight: narrow ? 44 : 32,
            }} />
        </label>
      )}
      {anyHard && !isAdmin && (
        <p style={{ margin: 0, fontSize: 12.5, color: 'var(--muted)' }}>{t('budget.cart_hard_ask_admin')}</p>
      )}
    </div>
  )
}
