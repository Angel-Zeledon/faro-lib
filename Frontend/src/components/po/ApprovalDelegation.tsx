'use client'
/**
 * Approval delegation: an approver names a substitute for a date range
 * (a holiday), and sees who covers whom.
 *
 * Appears only for someone who is an approver or who has been named as a
 * substitute (the server answers an empty list and no candidates to everybody
 * else), so a tenant with no approval workflow sees nothing new. The rules the
 * server enforces (never yourself, never a viewer, never beyond the delegator's
 * warehouses or limits, ends by itself on the last day) are not repeated here;
 * a refusal arrives as an error code and is rendered through `errors.<code>`.
 */
import { useCallback, useEffect, useState } from 'react'
import {
  createPOApprovalDelegation, listPOApprovalDelegations, revokePOApprovalDelegation,
} from '@/lib/api'
import type { POApprovalDelegation, POApprovalDelegationCandidate } from '@/lib/types'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'

const C = { border: 'var(--border)', text: 'var(--text)', dim: 'var(--dim)', muted: 'var(--muted)', red: '#C0504D' }

const smallBtn: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
  padding: '4px 11px', borderRadius: 7, fontSize: 12, fontWeight: 600,
  border: `1px solid ${C.border}`, color: C.text,
}

const labelStyle: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: C.muted,
}

const dayString = (offset: number) => {
  const d = new Date()
  d.setDate(d.getDate() + offset)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}

export default function ApprovalDelegation() {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const me = getUser()?.id
  const [items, setItems] = useState<POApprovalDelegation[] | null>(null)
  const [candidates, setCandidates] = useState<POApprovalDelegationCandidate[]>([])
  const [form, setForm] = useState({ delegate: '', from: dayString(0), to: dayString(7), note: '' })
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    listPOApprovalDelegations({ silent: true })
      .then(r => { setItems(r.items); setCandidates(r.candidates) })
      .catch(() => { setItems([]); setCandidates([]) })
  }, [])
  useEffect(() => { load() }, [load])

  if (items === null) return null
  const visible = items.filter(d => d.status === 'active' || d.status === 'scheduled')
  const canDelegate = candidates.length > 0
  if (!canDelegate && visible.length === 0) return null

  const valid = form.delegate !== '' && form.from !== '' && form.to !== '' && form.to >= form.from

  async function submit() {
    if (!valid || busy) return
    setBusy(true); setError(null)
    try {
      await createPOApprovalDelegation({
        delegate_id: form.delegate, starts_on: form.from, ends_on: form.to,
        note: form.note.trim() || null,
      })
      setForm({ delegate: '', from: dayString(0), to: dayString(7), note: '' })
      setOpen(false)
      load()
    } catch (e: unknown) {
      setError(errorDetail(e))
    } finally { setBusy(false) }
  }

  async function revoke(d: POApprovalDelegation) {
    if (!await confirm({
      title: t('po_delegation.revoke_title'),
      message: t('po_delegation.revoke_body', { name: d.delegate_name ?? '-' }),
      confirmLabel: t('po_delegation.revoke'),
    })) return
    setBusy(true); setError(null)
    try { await revokePOApprovalDelegation(d.id); load() }
    catch (e: unknown) { setError(errorDetail(e)) }
    finally { setBusy(false) }
  }

  return (
    <section aria-labelledby="po-delegation" style={{
      background: 'var(--surface)', border: `1px solid ${C.border}`, borderRadius: 12, padding: 16,
    }}>
      <h2 id="po-delegation" style={{ margin: '0 0 4px', fontSize: 14, fontWeight: 700, color: C.text }}>
        {t('po_delegation.title')}
      </h2>
      <p style={{ margin: '0 0 12px', fontSize: 12.5, color: C.muted }}>{t('po_delegation.hint')}</p>
      {error && <p role="alert" style={{ margin: '0 0 10px', fontSize: 12, color: C.red }}>{error}</p>}

      {visible.length > 0 && (
        <ul style={{ listStyle: 'none', margin: '0 0 12px', padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {visible.map(d => {
            const mine = d.delegator_id === me
            return (
              <li key={d.id} style={{
                border: `1px solid ${C.border}`, borderRadius: 10, padding: 10, background: 'var(--surface-2)',
                display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', justifyContent: 'space-between',
              }}>
                <span style={{ fontSize: 13, color: C.text, overflowWrap: 'anywhere' }}>
                  {mine
                    ? t('po_delegation.line_given', { name: d.delegate_name ?? '-', from: d.starts_on, to: d.ends_on })
                    : t('po_delegation.line_received', { name: d.delegator_name ?? '-', from: d.starts_on, to: d.ends_on })}
                  {d.status === 'scheduled' && <span style={{ color: C.dim }}> · {t('po_delegation.status_scheduled')}</span>}
                </span>
                {mine && (
                  <button type="button" style={smallBtn} disabled={busy} onClick={() => revoke(d)}>
                    {t('po_delegation.revoke')}
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {canDelegate && !open && (
        <button type="button" style={smallBtn} onClick={() => { setOpen(true); setError(null) }}>
          {t('po_delegation.add')}
        </button>
      )}
      {canDelegate && open && (
        <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))' }}>
          <label style={labelStyle}>
            {t('po_delegation.field_delegate')}
            <select className="form-input" value={form.delegate}
                    onChange={e => setForm(f => ({ ...f, delegate: e.target.value }))}>
              <option value="">{t('po_delegation.pick')}</option>
              {candidates.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label style={labelStyle}>
            {t('po_delegation.field_from')}
            <input className="form-input" type="date" value={form.from} min={dayString(0)}
                   onChange={e => setForm(f => ({ ...f, from: e.target.value }))} />
          </label>
          <label style={labelStyle}>
            {t('po_delegation.field_to')}
            <input className="form-input" type="date" value={form.to} min={form.from || dayString(0)}
                   onChange={e => setForm(f => ({ ...f, to: e.target.value }))} />
          </label>
          <label style={{ ...labelStyle, gridColumn: '1 / -1' }}>
            {t('po_delegation.field_note')}
            <input className="form-input" type="text" maxLength={500} value={form.note}
                   onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
          </label>
          <div style={{ display: 'flex', gap: 8, gridColumn: '1 / -1' }}>
            <button type="button" style={{ ...smallBtn, opacity: valid && !busy ? 1 : 0.5 }}
                    disabled={!valid || busy} onClick={submit}>
              {t('po_delegation.save')}
            </button>
            <button type="button" style={smallBtn} onClick={() => setOpen(false)}>{t('common.cancel')}</button>
          </div>
          <p style={{ gridColumn: '1 / -1', margin: 0, fontSize: 11.5, color: C.dim }}>{t('po_delegation.limits_note')}</p>
        </div>
      )}
    </section>
  )
}
