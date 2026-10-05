'use client'
/**
 * "Pagada" badge and the mark-paid / unmark action for one purchase order.
 *
 * Paying the supplier's invoice is what takes an order off the payments
 * calendar (math audit O3): before this existed every order ever sent stayed
 * owed forever, so the overdue total only grew. Marking paid is one click,
 * because the undo is right next to it; unmarking confirms first, because it
 * puts money back on the calendar.
 *
 * Only a SENT order offers the action — a draft was never invoiced, and the
 * server refuses it too. A viewer sees the badge but no button.
 */
import { useState } from 'react'
import { BadgeCheck, Undo2, Wallet } from 'lucide-react'
import { markPOPaid, markPOUnpaid } from '@/lib/api'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'

const BTN: React.CSSProperties = {
  all: 'unset', cursor: 'pointer',
  display: 'inline-flex', alignItems: 'center', gap: 4,
  padding: '3px 10px', borderRadius: 7, fontSize: 11, fontWeight: 600,
  border: '1px solid var(--border)', color: 'var(--text)',
}

export function PaidBadge({ paidAt }: { paidAt?: string | null }) {
  const { t, lang } = useLanguage()
  if (!paidAt) return null
  const date = new Date(paidAt).toLocaleDateString(lang === 'en' ? 'en-US' : 'es-CR', {
    day: 'numeric', month: 'short', year: 'numeric',
  })
  return (
    <span
      title={t('po.paid_on', { date })}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        padding: '2px 9px', borderRadius: 20, fontSize: 11, fontWeight: 700,
        background: 'var(--signal-ok-bg)', color: 'var(--signal-ok-fg)',
      }}
    >
      <BadgeCheck size={11} aria-hidden="true" /> {t('po.paid_badge')}
    </span>
  )
}

export function PaidPOActions({ poLogId, sent, paidAt, onChanged, showBadge = true }: {
  poLogId: string
  sent: boolean
  paidAt?: string | null
  /** Called after a successful change so the list can reload. */
  onChanged?: () => void
  showBadge?: boolean
}) {
  const { t } = useLanguage()
  const confirm = useConfirm()
  const errorDetail = useErrorDetail()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const canEdit = getUser()?.role !== 'viewer'
  const paid = Boolean(paidAt)

  async function run(action: () => Promise<unknown>) {
    setError(null)
    setBusy(true)
    try {
      await action()
      onChanged?.()
    } catch (e) {
      setError(errorDetail(e) || t('po.paid_failed'))
    } finally {
      setBusy(false)
    }
  }

  async function unmark() {
    const ok = await confirm({
      title: t('po.mark_unpaid_confirm_title'),
      message: t('po.mark_unpaid_confirm_message'),
      confirmLabel: t('po.mark_unpaid_confirm_action'),
    })
    if (ok) await run(() => markPOUnpaid(poLogId))
  }

  return (
    <>
      {showBadge && <PaidBadge paidAt={paidAt} />}
      {canEdit && paid && (
        <button disabled={busy} onClick={unmark}
                style={{ ...BTN, cursor: busy ? 'not-allowed' : 'pointer' }}>
          <Undo2 size={11} aria-hidden="true" /> {t('po.mark_unpaid_btn')}
        </button>
      )}
      {canEdit && !paid && sent && (
        <button disabled={busy} onClick={() => run(() => markPOPaid(poLogId))}
                style={{ ...BTN, cursor: busy ? 'not-allowed' : 'pointer' }}>
          <Wallet size={11} aria-hidden="true" /> {t('po.mark_paid_btn')}
        </button>
      )}
      {error && (
        <span style={{ fontSize: 11, color: '#C0504D', fontWeight: 600 }}>{error}</span>
      )}
    </>
  )
}
