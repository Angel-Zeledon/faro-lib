'use client'
/**
 * The buyer's side of the supplier confirmation link: the "ask the supplier to
 * confirm" checkbox on the send flow, the status chip on an order, and the panel
 * where the answers are read and a proposed change is accepted.
 *
 * Nothing the supplier proposes changes what drives purchasing by itself: the
 * order's expected arrival moves only for a line whose proposal somebody accepts
 * here, and that choice is confirmed first and recorded under their name.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { X, Link2 } from 'lucide-react'
import {
  getPOConfirmations, acceptPOConfirmation, reopenPOConfirmationLink, revokePOConfirmationLink,
} from '@/lib/api'
import type {
  POConfirmationRequest, POConfirmationStatus, POConfirmationSummary, SendPOResult,
} from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useErrorDetail } from '@/components/ui/States'
import Spinner from '@/components/ui/Spinner'
import BottomSheet from '@/components/mobile/BottomSheet'
import { useIsNarrow } from '@/hooks/useIsNarrow'

const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  red: '#C0504D', green: '#2E8B62', indigo: 'var(--accent)',
}

// ── The checkbox, and the choice it remembers ────────────────────────────────

const PREF_KEY = 'po.request_confirmation'

/** On unless the user switched it off. Browser storage is only a convenience
 *  here: where it is blocked the box simply starts checked each time. */
export function readRequestConfirmationPref(): boolean {
  try { return localStorage.getItem(PREF_KEY) !== '0' } catch { return true }
}

export function writeRequestConfirmationPref(value: boolean): void {
  try { localStorage.setItem(PREF_KEY, value ? '1' : '0') } catch { /* blocked storage */ }
}

export function useRequestConfirmationPref(): [boolean, (v: boolean) => void] {
  const [value, setValue] = useState(true)
  useEffect(() => { setValue(readRequestConfirmationPref()) }, [])
  const set = useCallback((v: boolean) => { setValue(v); writeRequestConfirmationPref(v) }, [])
  return [value, set]
}

export function RequestConfirmationCheckbox({ checked, onChange }: {
  checked: boolean
  onChange: (v: boolean) => void
}) {
  const { t } = useLanguage()
  return (
    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, fontSize: 12.5, color: C.text, cursor: 'pointer' }}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)}
             style={{ marginTop: 2, width: 16, height: 16, accentColor: 'var(--accent)' }} />
      <span>
        <span style={{ fontWeight: 600 }}>{t('po.confirm_request')}</span>
        <span style={{ display: 'block', color: C.dim, fontSize: 12, lineHeight: 1.45 }}>
          {t('po.confirm_request_hint')}
        </span>
      </span>
    </label>
  )
}

/** What the buyer is told about the links after a send that asked for them —
 *  which suppliers got one, and which did not (and the order still went out). */
export function confirmationNote(
  res: SendPOResult, t: (k: string, p?: Record<string, unknown>) => string,
): string | null {
  if (!res.confirmation_links && !res.confirmation_failed) return null
  const linked = new Set((res.confirmation_links ?? []).map(l => l.supplier))
  const parts: string[] = []
  if (linked.size > 0) parts.push(t('po.confirm_links_sent', { list: Array.from(linked).join(', ') }))
  const failed = res.confirmation_failed ?? []
  if (failed.length > 0) parts.push(t('po.confirm_links_failed', { list: failed.join(', ') }))
  const without = res.sent.map(s => s.supplier).filter(s => !linked.has(s) && !failed.includes(s))
  if (without.length > 0) parts.push(t('po.confirm_links_missing', { list: without.join(', ') }))
  return parts.join(' ') || null
}

// ── Status chip ──────────────────────────────────────────────────────────────

const CHIP: Record<POConfirmationStatus, { fg: string; bg: string }> = {
  pending:   { fg: 'var(--muted)', bg: 'color-mix(in srgb, var(--muted) 12%, transparent)' },
  confirmed: { fg: 'var(--signal-ok-fg)', bg: 'var(--signal-ok-bg)' },
  changed:   { fg: 'var(--signal-order-soon-fg)', bg: 'var(--signal-order-soon-bg)' },
  declined:  { fg: 'var(--signal-order-now-fg)', bg: 'var(--signal-order-now-bg)' },
}

export function ConfirmationChip({ summary, onOpen }: {
  summary: POConfirmationSummary
  onOpen: () => void
}) {
  const { t } = useLanguage()
  const look = CHIP[summary.status] ?? CHIP.pending
  const label = t(`po.confirm_chip_${summary.status}`)
  const pending = summary.pending_acceptance > 0
    ? ` · ${t('po.confirm_pending_count', { n: summary.pending_acceptance })}` : ''
  return (
    <button type="button" onClick={onOpen}
            aria-label={`${t('po.confirm_section_title')}: ${label}${pending}`}
            style={{
              all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
              padding: '2px 9px', borderRadius: 20, fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap',
              background: look.bg, color: look.fg,
            }}>
      <Link2 size={11} aria-hidden="true" /> {label}{pending}
    </button>
  )
}

// ── The panel ────────────────────────────────────────────────────────────────

function fmtDay(iso: string | null, lang: string): string {
  if (!iso) return '—'
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`)
  return Number.isNaN(d.getTime()) ? iso
    : d.toLocaleDateString(lang === 'en' ? 'en-US' : 'es-CR', { day: 'numeric', month: 'short', year: 'numeric' })
}

function fmtQty(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

function Panel({ poId, onChanged }: { poId: string; onChanged: () => void }) {
  const { t, lang } = useLanguage()
  const confirm = useConfirm()
  const errorDetail = useErrorDetail()
  const [requests, setRequests] = useState<POConfirmationRequest[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(() => {
    getPOConfirmations(poId)
      .then(r => { setRequests(r); setError(null) })
      .catch(e => setError(errorDetail(e) || t('common.error')))
  }, [poId])
  useEffect(() => { load() }, [load])

  async function act(key: string, fn: () => Promise<unknown>) {
    setBusy(key)
    try { await fn(); load(); onChanged() }
    catch (e) { setError(errorDetail(e) || t('common.error')) }
    finally { setBusy(null) }
  }

  async function accept(confirmationId: string, supplier: string) {
    const ok = await confirm({
      title: t('po.confirm_accept_title'),
      message: t('po.confirm_accept_body', { supplier }),
      confirmLabel: t('po.confirm_accept'),
    })
    if (ok) await act(confirmationId, () => acceptPOConfirmation(poId, confirmationId))
  }

  async function revoke(requestId: string) {
    const ok = await confirm({
      title: t('po.confirm_revoke_title'), message: t('po.confirm_revoke_body'),
      confirmLabel: t('po.confirm_revoke'), danger: true,
    })
    if (ok) await act(requestId, () => revokePOConfirmationLink(poId, requestId))
  }

  if (error && !requests) {
    return <div role="alert" style={{ fontSize: 12, color: C.red }}>{error}</div>
  }
  if (!requests) return <div style={{ padding: 24, textAlign: 'center' }}><Spinner size={16} /></div>
  if (requests.length === 0) {
    return <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t('po.confirm_none')}</p>
  }

  const stateNote = (r: POConfirmationRequest) =>
    r.state === 'revoked' ? t('po.confirm_state_revoked')
    : r.state === 'expired' ? t('po.confirm_state_expired')
    : r.locked ? t('po.confirm_locked', { date: fmtDay(r.submitted_at, lang) })
    : r.submitted_at ? t('po.confirm_reopened') : t('po.confirm_waiting')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      {error && <div role="alert" style={{ fontSize: 12, color: C.red }}>{error}</div>}
      {requests.map(r => (
        <section key={r.request_id} aria-label={r.supplier}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
            <strong style={{ fontSize: 14, color: C.text }}>{r.supplier}</strong>
            <span style={{ fontSize: 12, color: C.dim }}>{stateNote(r)}</span>
            <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 6 }}>
              {r.state === 'active' && r.locked && (
                <button type="button" disabled={busy !== null}
                        onClick={() => void act(r.request_id, () => reopenPOConfirmationLink(poId, r.request_id))}
                        style={smallButton}>
                  {t('po.confirm_reopen')}
                </button>
              )}
              {r.state === 'active' && (
                <button type="button" disabled={busy !== null} onClick={() => void revoke(r.request_id)}
                        style={{ ...smallButton, color: C.red }}>
                  {t('po.confirm_revoke')}
                </button>
              )}
            </span>
          </div>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {r.lines.map(l => {
              const resp = l.response
              const status: POConfirmationStatus = resp ? resp.status : 'pending'
              const look = CHIP[status]
              return (
                <li key={l.line_id} style={{
                  border: `1px solid ${C.border}`, borderRadius: 10, padding: '10px 12px', background: C.card,
                }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, justifyContent: 'space-between' }}>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: 13, color: C.text, overflowWrap: 'anywhere' }}>{l.name}</div>
                      <div style={{ fontSize: 11, color: C.dim, fontFamily: 'monospace' }}>{l.sku}</div>
                    </div>
                    <span style={{
                      padding: '2px 9px', borderRadius: 20, fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap',
                      background: look.bg, color: look.fg,
                    }}>
                      {t(`po.confirm_chip_${status}`)}
                    </span>
                  </div>
                  {resp && (
                    <div style={{ marginTop: 6, fontSize: 12.5, color: C.muted, lineHeight: 1.5 }}>
                      {resp.status === 'declined' ? (
                        <span>{t('po.confirm_declined_line')}</span>
                      ) : (
                        <>
                          <div>{t('po.confirm_promised', { date: fmtDay(resp.promised_date, lang) })}</div>
                          {resp.confirmed_qty != null && resp.confirmed_qty !== l.ordered_qty && (
                            <div>{t('po.confirm_qty_proposed', { qty: fmtQty(resp.confirmed_qty), ordered: fmtQty(l.ordered_qty) })}</div>
                          )}
                        </>
                      )}
                      {resp.note && (
                        // The supplier's own words, rendered as a text node: React
                        // escapes it, and it is never injected as HTML.
                        <div style={{ marginTop: 4, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
                          <span style={{ color: C.dim }}>{t('po.confirm_note')}: </span>{resp.note}
                        </div>
                      )}
                      {resp.acceptable && (
                        <div style={{ marginTop: 8 }}>
                          <button type="button" disabled={busy !== null}
                                  onClick={() => void accept(resp.confirmation_id, r.supplier)}
                                  style={{ ...smallButton, background: C.indigo, color: '#fff', border: 'none' }}>
                            {busy === resp.confirmation_id ? t('common.saving') : t('po.confirm_accept')}
                          </button>
                          <span style={{ marginLeft: 8, fontSize: 11.5, color: C.dim }}>{t('po.confirm_accept_hint')}</span>
                        </div>
                      )}
                      {resp.status === 'changed' && resp.accepted && (
                        <div style={{ marginTop: 6, fontWeight: 600, color: C.green }}>
                          ✓ {t('po.confirm_accepted', { date: fmtDay(resp.accepted_at, lang) })}
                        </div>
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      ))}
    </div>
  )
}

const smallButton: React.CSSProperties = {
  cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4, minHeight: 28,
  padding: '3px 10px', borderRadius: 7, fontSize: 11.5, fontWeight: 600, fontFamily: 'inherit',
  border: `1px solid ${C.border}`, background: 'transparent', color: C.text,
}

export function SupplierConfirmationModal({ poId, onClose, onChanged }: {
  poId: string
  onClose: () => void
  onChanged: () => void
}) {
  const { t } = useLanguage()
  const narrow = useIsNarrow()
  const dialogRef = useRef<HTMLDivElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    dialogRef.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCloseRef.current() }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); opener?.focus?.() }
  }, [])

  if (narrow) {
    return (
      <BottomSheet open onClose={onClose} maxHeight="94dvh"
                   title={<span>{t('po.confirm_section_title')}</span>}>
        <Panel poId={poId} onChanged={onChanged} />
      </BottomSheet>
    )
  }
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="po-confirm-title" tabIndex={-1}
           onClick={e => e.stopPropagation()}
           style={{
             outline: 'none', width: '100%', maxWidth: 560, maxHeight: '85vh', overflowY: 'auto',
             background: C.surface, border: `1px solid ${C.border}`, borderRadius: 14, padding: 24,
           }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
          <Link2 size={16} color={C.indigo} aria-hidden="true" />
          <span id="po-confirm-title" style={{ fontSize: 15, fontWeight: 700, color: C.text }}>
            {t('po.confirm_section_title')}
          </span>
          <button type="button" onClick={onClose} aria-label={t('common.close')}
                  style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', color: C.dim }}>
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <Panel poId={poId} onChanged={onChanged} />
      </div>
    </div>
  )
}
