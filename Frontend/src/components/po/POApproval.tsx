'use client'
/**
 * Purchase-order approval, the pieces that appear ONLY when the tenant has an
 * approval rule (the server leaves `approval` off every order otherwise, so none
 * of this renders and the order screens are exactly what they were):
 *
 *  - ApprovalChip         a calm chip on the order row
 *  - RequestApprovalButton  replaces "send" while the order may not leave yet
 *  - ApprovalInbox        what an approver has to decide, with approve / reject
 *  - usePOApproval        one order's state, for the create-and-send step
 */
import { useCallback, useEffect, useState } from 'react'
import { Check, Send, X } from 'lucide-react'
import {
  approvePO, getPOApproval, getPOApprovalPending, rejectPO, requestPOApproval,
} from '@/lib/api'
import type { POApproval, POApprovalBadge, POApprovalPendingItem } from '@/lib/types'
import AttentionChip from '@/components/layout/AttentionChip'
import { useErrorDetail } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { formatMoney } from '@/lib/currency'

const C = { border: 'var(--border)', text: 'var(--text)', dim: 'var(--dim)', muted: 'var(--muted)', red: '#C0504D' }

const smallBtn: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
  padding: '3px 10px', borderRadius: 7, fontSize: 11, fontWeight: 600,
  border: `1px solid ${C.border}`, color: C.text,
}

/** True while the order may not be sent: it needs approval and does not have it. */
export const blocksSending = (a?: POApprovalBadge | null) => Boolean(a?.required)

export function ApprovalChip({ approval }: { approval?: POApprovalBadge | null }) {
  const { t } = useLanguage()
  if (!approval?.required) return null
  const key = approval.status === 'pending_approval' ? 'po_approval.chip_pending'
    : approval.status === 'rejected' ? 'po_approval.chip_rejected'
    : 'po_approval.chip_needed'
  return <AttentionChip>{t(key)}</AttentionChip>
}

export function usePOApproval(poLogId: string | null | undefined) {
  const [data, setData] = useState<POApproval | null>(null)
  const reload = useCallback(() => {
    if (!poLogId) { setData(null); return }
    getPOApproval(poLogId).then(setData).catch(() => setData(null))
  }, [poLogId])
  useEffect(() => { reload() }, [reload])
  return { data, reload, setData }
}

/** The step that replaces "send" while the order needs an approval it lacks. */
export function RequestApprovalButton({ poLogId, approval, onChanged }: {
  poLogId: string
  approval?: POApprovalBadge | null
  onChanged?: () => void
}) {
  const { t } = useLanguage()
  const narrow = useIsNarrow()
  const errorDetail = useErrorDetail()
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; msg: string } | null>(null)
  if (!approval?.required) return null

  const pending = approval.status === 'pending_approval'
  const style: React.CSSProperties = narrow
    ? { ...smallBtn, boxSizing: 'border-box', minHeight: 48, width: '100%', justifyContent: 'center',
        fontSize: 14, borderRadius: 12, background: 'var(--surface)', opacity: pending ? 0.6 : 1 }
    : { ...smallBtn, opacity: pending ? 0.6 : 1, cursor: pending ? 'default' : 'pointer' }

  async function ask() {
    if (busy || pending) return
    setBusy(true); setNote(null)
    try {
      const res = await requestPOApproval(poLogId)
      setNote({ ok: true, msg: t('po_approval.requested_note') })
      void res
      onChanged?.()
    } catch (e: unknown) {
      setNote({ ok: false, msg: errorDetail(e) })
    } finally { setBusy(false) }
  }

  return (
    <span style={{ display: 'inline-flex', flexDirection: 'column', gap: 4, alignItems: narrow ? 'stretch' : 'flex-start' }}>
      <button type="button" style={style} onClick={ask} disabled={busy || pending}
              title={t('po_approval.request_hint')}>
        <Send size={11} aria-hidden="true" />
        {pending ? t('po_approval.waiting') : busy ? t('common.saving') : t('po_approval.request_btn')}
      </button>
      {note && <span style={{ fontSize: 11, color: note.ok ? C.muted : C.red }}>{note.msg}</span>}
    </span>
  )
}

/** The approver's list. Renders nothing for someone who cannot approve or when
 *  nothing is waiting, unless `alwaysShow` (the page was opened from a link). */
export function ApprovalInbox({ onChanged, alwaysShow = false, focusId }: {
  onChanged?: () => void
  alwaysShow?: boolean
  focusId?: string | null
}) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const [items, setItems] = useState<POApprovalPendingItem[] | null>(null)
  const [isApprover, setIsApprover] = useState(false)
  const [rejecting, setRejecting] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    getPOApprovalPending({ silent: true })
      .then(r => { setItems(r.items); setIsApprover(r.is_approver) })
      .catch(() => setItems([]))
  }, [])
  useEffect(() => { load() }, [load])

  async function decide(it: POApprovalPendingItem, approve: boolean) {
    setBusy(it.po_log_id); setError(null)
    try {
      if (approve) await approvePO(it.po_log_id)
      else await rejectPO(it.po_log_id, reason.trim())
      setRejecting(null); setReason('')
      load(); onChanged?.()
    } catch (e: unknown) {
      setError(errorDetail(e))
    } finally { setBusy(null) }
  }

  if (!isApprover || items === null) return null
  if (items.length === 0 && !alwaysShow) return null

  return (
    <section aria-labelledby="po-approval-inbox" style={{
      background: 'var(--surface)', border: `1px solid ${C.border}`, borderRadius: 12, padding: 16,
    }}>
      <h2 id="po-approval-inbox" style={{ margin: '0 0 4px', fontSize: 14, fontWeight: 700, color: C.text }}>
        {t('po_approval.inbox_title')}
      </h2>
      <p style={{ margin: '0 0 12px', fontSize: 12.5, color: C.muted }}>{t('po_approval.inbox_hint')}</p>
      {items.length === 0 && <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t('po_approval.inbox_empty')}</p>}
      {error && <p role="alert" style={{ margin: '0 0 10px', fontSize: 12, color: C.red }}>{error}</p>}
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {items.map(it => (
          <li key={it.approval_id} style={{
            border: `1px solid ${it.po_log_id === focusId ? 'var(--accent)' : C.border}`,
            borderRadius: 10, padding: 12, background: 'var(--surface-2)',
          }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'space-between' }}>
              <strong style={{ fontSize: 13, color: C.text }}>{it.reference}</strong>
              <strong style={{ fontSize: 13, color: C.text, fontVariantNumeric: 'tabular-nums' }}>{formatMoney(it.amount)}</strong>
            </div>
            <div style={{ fontSize: 12, color: C.muted, marginTop: 4, overflowWrap: 'anywhere' }}>
              {t('po_approval.inbox_by', { name: it.requested_by_name ?? '—' })}
              {it.suppliers ? ` · ${it.suppliers}` : ''}
              {it.warehouse ? ` · ${it.warehouse}` : ''}
              {` · ${t('po_approval.inbox_lines', { n: it.sku_count })}`}
            </div>
            {it.note && <div style={{ fontSize: 12, color: C.text, marginTop: 6, overflowWrap: 'anywhere' }}>“{it.note}”</div>}
            {!it.can_decide && (
              <div style={{ fontSize: 12, color: C.dim, marginTop: 8 }}>{t('po_approval.inbox_own')}</div>
            )}
            {it.can_decide && rejecting !== it.po_log_id && (
              <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                <button type="button" style={smallBtn} disabled={busy === it.po_log_id}
                        onClick={() => decide(it, true)}>
                  <Check size={12} aria-hidden="true" /> {t('po_approval.approve')}
                </button>
                <button type="button" style={smallBtn} disabled={busy === it.po_log_id}
                        onClick={() => { setRejecting(it.po_log_id); setReason(''); setError(null) }}>
                  <X size={12} aria-hidden="true" /> {t('po_approval.reject')}
                </button>
              </div>
            )}
            {it.can_decide && rejecting === it.po_log_id && (
              <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                <label style={{ fontSize: 12, color: C.muted }} htmlFor={`reject-${it.po_log_id}`}>
                  {t('po_approval.reject_reason_label')}
                </label>
                <textarea id={`reject-${it.po_log_id}`} value={reason} maxLength={500} rows={2}
                          onChange={e => setReason(e.target.value)}
                          className="form-input" style={{ width: '100%', boxSizing: 'border-box', fontSize: 13 }} />
                <div style={{ display: 'flex', gap: 8 }}>
                  <button type="button" style={{ ...smallBtn, opacity: reason.trim().length < 3 ? 0.5 : 1 }}
                          disabled={busy === it.po_log_id || reason.trim().length < 3}
                          onClick={() => decide(it, false)}>
                    {t('po_approval.reject_confirm')}
                  </button>
                  <button type="button" style={smallBtn} onClick={() => setRejecting(null)}>
                    {t('common.cancel')}
                  </button>
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
