'use client'
/**
 * "Cancelada" badge and the cancel / reopen action for one purchase order.
 *
 * Cancelling takes the order's units out of "on the way", so the Panel may
 * ask for them again — it confirms first and says so. It is only offered
 * while nothing was received and the order is not marked paid; the server
 * refuses both cases too, and its refusal is shown as it comes back.
 * Reopening is the undo and restores everything exactly.
 */
import { useState } from 'react'
import { Ban, RotateCcw } from 'lucide-react'
import { cancelPO, uncancelPO } from '@/lib/api'
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

export function CancelledBadge({ cancelledAt }: { cancelledAt?: string | null }) {
  const { t, lang } = useLanguage()
  if (!cancelledAt) return null
  const date = new Date(cancelledAt).toLocaleDateString(lang === 'en' ? 'en-US' : 'es-CR', {
    day: 'numeric', month: 'short', year: 'numeric',
  })
  return (
    <span
      title={t('po.cancelled_on', { date })}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        padding: '2px 9px', borderRadius: 20, fontSize: 11, fontWeight: 700,
        background: 'color-mix(in srgb, var(--dim) 18%, transparent)', color: 'var(--text)',
      }}
    >
      <Ban size={11} aria-hidden="true" /> {t('po.cancelled_badge')}
    </span>
  )
}

export function CancelPOActions({ poLogId, receptionStatus, paidAt, cancelledAt, onChanged }: {
  poLogId: string
  receptionStatus?: string | null
  paidAt?: string | null
  cancelledAt?: string | null
  onChanged?: () => void
}) {
  const { t } = useLanguage()
  const confirm = useConfirm()
  const errorDetail = useErrorDetail()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (getUser()?.role === 'viewer') return null

  const cancelled = Boolean(cancelledAt)
  const status = receptionStatus || 'pending'
  // Nothing received and not paid — the server's own two refusals.
  const canCancel = !cancelled && !paidAt && (status === 'pending' || status === 'not_received')
  if (!cancelled && !canCancel) return null

  async function run(action: () => Promise<unknown>, titleKey: string,
                     messageKey: string, actionKey: string, danger: boolean) {
    setError(null)
    const ok = await confirm({
      title: t(titleKey), message: t(messageKey), confirmLabel: t(actionKey), danger,
    })
    if (!ok) return
    setBusy(true)
    try {
      await action()
      onChanged?.()
    } catch (e) {
      setError(errorDetail(e) || t('po.cancel_failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {canCancel && (
        <button disabled={busy}
                onClick={() => run(() => cancelPO(poLogId), 'po.cancel_confirm_title',
                                   'po.cancel_confirm_message', 'po.cancel_confirm_action', true)}
                style={{ ...BTN, cursor: busy ? 'not-allowed' : 'pointer' }}>
          <Ban size={11} aria-hidden="true" /> {t('po.cancel_btn')}
        </button>
      )}
      {cancelled && (
        <button disabled={busy}
                onClick={() => run(() => uncancelPO(poLogId), 'po.uncancel_confirm_title',
                                   'po.uncancel_confirm_message', 'po.uncancel_confirm_action', false)}
                style={{ ...BTN, cursor: busy ? 'not-allowed' : 'pointer' }}>
          <RotateCcw size={11} aria-hidden="true" /> {t('po.uncancel_btn')}
        </button>
      )}
      {error && (
        <span style={{ fontSize: 11, color: '#C0504D', fontWeight: 600 }}>{error}</span>
      )}
    </>
  )
}
