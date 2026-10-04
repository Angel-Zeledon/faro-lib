'use client'
/**
 * Undoing a reception and undoing a send.
 *
 * Both write real state — an un-receive takes units back out of stock and
 * removes the lead-time observation that reception taught the supplier's
 * scorecard; an un-send returns the order to "not sent", which the payables
 * calendar reads (`incoming_qty` does not: an open order counts as on its way
 * whether or not it was sent from here). So both confirm first and both say
 * plainly what will happen, rather than asking "are you sure?" about a verb.
 *
 * They refuse rather than guess, and the refusal is the useful part: the
 * server declines an un-receive whose units have already been sold, naming the
 * SKU and warehouse that is short, and declines an un-send once goods have
 * arrived. Those messages are rendered as they come back instead of being
 * flattened into "something went wrong".
 */
import { useState } from 'react'
import { Undo2 } from 'lucide-react'
import { unreceivePO, unsendPO } from '@/lib/api'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'
import { useIsNarrow } from '@/hooks/useIsNarrow'

const C = {
  border: 'var(--border)', text: 'var(--text)',
  green: '#2E8B62', red: '#C0504D',
}

const BTN: React.CSSProperties = {
  all: 'unset', cursor: 'pointer',
  display: 'inline-flex', alignItems: 'center', gap: 4,
  padding: '3px 10px', borderRadius: 7, fontSize: 11, fontWeight: 600,
  border: `1px solid ${C.border}`, color: C.text,
}

export function UndoPOActions({ poLogId, receptionStatus, sent, onDone }: {
  poLogId: string
  /** 'pending' | 'partial' | 'received' | 'not_received' */
  receptionStatus?: string | null
  /** Whether the order has been marked as sent to the supplier. */
  sent?: boolean
  /** Called after a successful undo so the table can reload. */
  onDone?: () => void
}) {
  const { t } = useLanguage()
  const confirm = useConfirm()
  const errorDetail = useErrorDetail()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 25px tall on desktop; on a phone they are 48px rows of their own.
  const narrow = useIsNarrow()
  const btnStyle: React.CSSProperties = narrow
    ? { ...BTN, boxSizing: 'border-box', width: '100%', minHeight: 48, justifyContent: 'center',
        padding: '0 16px', borderRadius: 12, fontSize: 14, gap: 8 }
    : BTN

  // A viewer may read the order history but not rewrite it. The server
  // enforces this too; hiding the button keeps the screen honest about what
  // this reader can do.
  if (getUser()?.role === 'viewer') return null

  const status = receptionStatus || 'pending'
  const canUnreceive = status === 'received' || status === 'partial'
  // Once goods have arrived the order cannot go back to "not sent" — the
  // reception is physical proof that it was. Undo the reception first.
  const canUnsend = Boolean(sent) && status === 'pending'

  if (!canUnreceive && !canUnsend) return null

  async function run(
    action: () => Promise<unknown>,
    titleKey: string, messageKey: string, actionKey: string,
  ) {
    setError(null)
    const ok = await confirm({
      title: t(titleKey),
      message: t(messageKey),
      confirmLabel: t(actionKey),
    })
    if (!ok) return
    setBusy(true)
    try {
      await action()
      onDone?.()
    } catch (e) {
      // The server's refusal names the SKU and warehouse that is short, which
      // is the whole point of it refusing instead of clamping.
      setError(errorDetail(e) || t('po.undo_failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {canUnreceive && (
        <button
          disabled={busy}
          onClick={() => run(
            () => unreceivePO(poLogId),
            'po.unreceive_confirm_title',
            'po.unreceive_confirm_message',
            'po.unreceive_confirm_action',
          )}
          style={{ ...btnStyle, cursor: busy ? 'not-allowed' : 'pointer' }}
        >
          <Undo2 size={narrow ? 15 : 11} aria-hidden="true" /> {t('po.unreceive_btn')}
        </button>
      )}
      {canUnsend && (
        <button
          disabled={busy}
          onClick={() => run(
            () => unsendPO(poLogId),
            'po.unsend_confirm_title',
            'po.unsend_confirm_message',
            'po.unsend_confirm_action',
          )}
          style={{ ...btnStyle, cursor: busy ? 'not-allowed' : 'pointer' }}
        >
          <Undo2 size={narrow ? 15 : 11} aria-hidden="true" /> {t('po.unsend_btn')}
        </button>
      )}
      {error && (
        <span role="alert" style={{ fontSize: narrow ? 13 : 11, color: C.red, fontWeight: 600 }}>{error}</span>
      )}
    </>
  )
}
