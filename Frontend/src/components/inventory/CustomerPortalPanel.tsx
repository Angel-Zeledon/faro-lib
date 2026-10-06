'use client'
/**
 * Customer portal: private, read-only links where a corporate customer sees only
 * their own commitments (the mirror of the supplier confirmation link). People
 * create a link per customer, choose whether the customer sees a promised date,
 * set that date per commitment, read what the customer answered, and revoke or
 * reopen the link.
 *
 * The link itself is shown ONCE, when it is created: the server keeps only a
 * hash. Reads are open to every signed-in user; writing is for analysts and
 * admins (the server enforces it; the controls are hidden for viewers like the
 * commitments panel). One component serves desktop and phone via `useIsNarrow`.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link2, ChevronDown, ChevronRight, Copy } from 'lucide-react'
import {
  createPortalLink, getPortalLink, listPortalCustomers, listPortalLinks, reopenPortalLink,
  revokePortalLink, setPortalLinkSharing, setPortalPromisedDate,
} from '@/lib/api'
import type { CustomerPortalCreated, CustomerPortalCustomer, CustomerPortalDetail, CustomerPortalLink } from '@/lib/types'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { getUser } from '@/lib/auth'
import { localeFor } from '@/lib/numberLocale'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D', amber: '#B7791F', green: '#2E7D5B' }
const STATUS_COLOR: Record<CustomerPortalLink['status'], string> = { active: C.green, expired: C.amber, revoked: C.red }

export default function CustomerPortalPanel({ reloadToken }: { reloadToken?: number } = {}) {
  const { t, lang } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const narrow = useIsNarrow()
  const role = getUser()?.role
  const canWrite = role === 'admin' || role === 'analyst'

  const [links, setLinks] = useState<CustomerPortalLink[]>([])
  const [customers, setCustomers] = useState<CustomerPortalCustomer[]>([])
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [form, setForm] = useState({ customer: '', language: lang as 'es' | 'en', share: false, days: '90' })
  const [created, setCreated] = useState<CustomerPortalCreated | null>(null)
  const [copied, setCopied] = useState(false)

  const [openId, setOpenId] = useState<string | null>(null)
  const [detail, setDetail] = useState<CustomerPortalDetail | null>(null)
  const [dates, setDates] = useState<Record<string, string>>({})

  const fmtDate = (iso: string | null) =>
    iso ? new Date(iso).toLocaleDateString(localeFor(lang), { day: 'numeric', month: 'short', year: 'numeric' }) : '—'

  const load = useCallback(() => {
    Promise.all([listPortalLinks(), listPortalCustomers()])
      .then(([l, c]) => { setLinks(l); setCustomers(c); setLoadError(null) })
      .catch(e => { setLinks([]); setCustomers([]); setLoadError(errorDetail(e)) })
      .finally(() => setLoaded(true))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { load() }, [load, reloadToken])

  const loadDetail = useCallback((id: string) => {
    getPortalLink(id)
      .then(d => {
        setDetail(d)
        setDates(Object.fromEntries(d.commitments.map(c => [c.id, c.promised_date ?? ''])))
      })
      .catch(e => { setDetail(null); setError(errorDetail(e)) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function toggle(id: string) {
    if (openId === id) { setOpenId(null); setDetail(null); return }
    setOpenId(id); setDetail(null); loadDetail(id)
  }

  const field: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box', fontSize: narrow ? 16 : 12.5, padding: narrow ? '10px 10px' : '6px 8px',
    borderRadius: narrow ? 10 : 7, border: `1px solid ${C.border}`, background: 'var(--surface-2)', color: C.text,
    minHeight: narrow ? 44 : 32,
  }
  const btn = (solid = false, disabled = false): React.CSSProperties => ({
    all: 'unset', cursor: disabled ? 'default' : 'pointer', boxSizing: 'border-box', display: 'inline-flex',
    alignItems: 'center', justifyContent: 'center', gap: 5, padding: narrow ? '0 14px' : '5px 12px',
    minHeight: narrow ? 44 : undefined, borderRadius: narrow ? 10 : 7, fontSize: narrow ? 14 : 12, fontWeight: 600,
    opacity: disabled ? 0.5 : 1, border: `1px solid ${C.border}`, color: C.text,
    ...(solid ? { background: 'var(--accent)', borderColor: 'var(--accent)', color: '#fff' } : {}),
  })
  const lbl: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: C.muted, fontWeight: 600 }
  const card: React.CSSProperties = { border: `1px solid ${C.border}`, borderRadius: 10, padding: 12, background: 'var(--surface)' }

  const days = Number(form.days)
  const valid = form.customer !== '' && Number.isInteger(days) && days >= 1 && days <= 365

  async function create() {
    setBusy(true); setError(null); setCreated(null); setCopied(false)
    try {
      const res = await createPortalLink({
        customer: form.customer, language: form.language, share_dates: form.share, expires_in_days: days,
      })
      setCreated(res)
      setForm(f => ({ ...f, customer: '' }))
      load()
    } catch (e: unknown) {
      setError(errorDetail(e))
    } finally { setBusy(false) }
  }

  async function copyLink() {
    if (!created) return
    try { await navigator.clipboard.writeText(created.url); setCopied(true) } catch { /* the field is selectable */ }
  }

  async function act(fn: () => Promise<unknown>, id?: string) {
    setBusy(true); setError(null)
    try { await fn(); load(); if (id && openId === id) loadDetail(id) }
    catch (e: unknown) { setError(errorDetail(e)) }
    finally { setBusy(false) }
  }

  async function revoke(l: CustomerPortalLink) {
    if (!(await confirm({ title: t('cportal.revoke_title'), message: t('cportal.revoke_body'), confirmLabel: t('cportal.revoke'), danger: true }))) return
    await act(() => revokePortalLink(l.id), l.id)
  }
  async function reopen(l: CustomerPortalLink) {
    if (!(await confirm({ title: t('cportal.reopen_title'), message: t('cportal.reopen_body'), confirmLabel: t('cportal.reopen') }))) return
    await act(() => reopenPortalLink(l.id), l.id)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: narrow ? 0 : 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Link2 size={15} color="var(--accent)" aria-hidden="true" />
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: C.text }}>{t('cportal.title')}</h3>
      </div>
      <p style={{ margin: 0, fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('cportal.intro')}</p>

      {error && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{error}</p>}
      {loadError && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{t('cportal.load_failed')} {loadError}</p>}

      {canWrite && (
        <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {customers.length === 0 && loaded ? (
            <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('cportal.customer_none')}</p>
          ) : (
            <>
              <div style={{ display: 'grid', gap: 8, gridTemplateColumns: narrow ? '1fr' : 'repeat(auto-fit, minmax(170px, 1fr))' }}>
                <label style={lbl}>{t('cportal.customer')}
                  <select style={field} value={form.customer} onChange={e => setForm(f => ({ ...f, customer: e.target.value }))}>
                    <option value="">{t('cportal.customer_pick')}</option>
                    {customers.map(c => (
                      <option key={c.customer} value={c.customer}>
                        {t('cportal.customer_option', { customer: c.customer, n: c.commitments })}
                      </option>
                    ))}
                  </select>
                </label>
                <label style={lbl}>{t('cportal.language')}
                  <select style={field} value={form.language} onChange={e => setForm(f => ({ ...f, language: e.target.value === 'en' ? 'en' : 'es' }))}>
                    <option value="es">Español</option>
                    <option value="en">English</option>
                  </select>
                </label>
                <label style={lbl}>{t('cportal.expires_days')}
                  <input style={field} type="number" min={1} max={365} step={1} value={form.days}
                    onChange={e => setForm(f => ({ ...f, days: e.target.value }))} />
                </label>
              </div>
              <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, fontSize: 12.5, color: C.text, cursor: 'pointer' }}>
                <input type="checkbox" checked={form.share} style={{ marginTop: 2, accentColor: 'var(--accent)' }}
                  onChange={e => setForm(f => ({ ...f, share: e.target.checked }))} />
                <span>
                  <strong>{t('cportal.share_dates')}</strong>
                  <span style={{ display: 'block', color: C.dim }}>{t('cportal.share_dates_hint')}</span>
                </span>
              </label>
              <div>
                <button type="button" style={btn(true, !valid || busy)} disabled={!valid || busy} onClick={() => void create()}>
                  {t('cportal.create')}
                </button>
              </div>
            </>
          )}
          {created && (
            <div role="status" style={{ ...card, borderColor: C.green, display: 'flex', flexDirection: 'column', gap: 6 }}>
              <strong style={{ fontSize: 13 }}>{t('cportal.created_title', { customer: created.link.customer })}</strong>
              <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('cportal.created_once')}</p>
              <input readOnly style={{ ...field, fontFamily: 'ui-monospace, monospace' }} value={created.url}
                onFocus={e => e.currentTarget.select()} aria-label={t('cportal.copy')} />
              <div>
                <button type="button" style={btn()} onClick={() => void copyLink()}>
                  <Copy size={12} aria-hidden="true" /> {copied ? t('cportal.copied') : t('cportal.copy')}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      {!canWrite && <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('cportal.viewer_note')}</p>}

      <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        {t('cportal.list_title')}
      </div>
      {loaded && links.length === 0 && !loadError && (
        <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('cportal.list_empty')}</p>
      )}
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {links.map(l => (
          <li key={l.id} style={card}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <strong style={{ fontSize: 13.5, overflowWrap: 'anywhere' }}>{l.customer}</strong>
                  <span style={{
                    padding: '1px 8px', borderRadius: 20, fontSize: 11.5, fontWeight: 600,
                    color: STATUS_COLOR[l.status], border: `1px solid ${STATUS_COLOR[l.status]}`,
                  }}>{t(`cportal.status.${l.status}`)}</span>
                </div>
                <div style={{ marginTop: 3, fontSize: 12, color: C.dim, lineHeight: 1.5 }}>
                  {t('cportal.expires_on', { date: fmtDate(l.expires_at) })} · {' '}
                  {l.last_viewed_at ? t('cportal.last_viewed', { date: fmtDate(l.last_viewed_at) }) : t('cportal.never_viewed')} · {' '}
                  {t('cportal.commitments_count', { n: l.commitments })} · {' '}
                  {t('cportal.answers', { received: l.received, objections: l.objections })}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <button type="button" style={btn()} aria-expanded={openId === l.id} onClick={() => toggle(l.id)}>
                  {openId === l.id ? <ChevronDown size={12} aria-hidden="true" /> : <ChevronRight size={12} aria-hidden="true" />}
                  {openId === l.id ? t('cportal.hide_details') : t('cportal.details')}
                </button>
                {canWrite && l.status === 'active' && (
                  <button type="button" style={btn(false, busy)} disabled={busy} onClick={() => void revoke(l)}>{t('cportal.revoke')}</button>
                )}
                {canWrite && l.status !== 'active' && (
                  <button type="button" style={btn(false, busy)} disabled={busy} onClick={() => void reopen(l)}>{t('cportal.reopen')}</button>
                )}
              </div>
            </div>

            {openId === l.id && detail && detail.link.id === l.id && (
              <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 12.5 }}>
                  <span>{detail.link.share_dates ? t('cportal.shares_dates_on') : t('cportal.shares_dates_off')}</span>
                  {canWrite && (
                    <button type="button" style={btn(false, busy)} disabled={busy}
                      onClick={() => void act(() => setPortalLinkSharing(l.id, !detail.link.share_dates), l.id)}>
                      {t('cportal.toggle_share')}
                    </button>
                  )}
                </div>
                <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  {t('cportal.commitments_title')}
                </div>
                {detail.commitments.length === 0 ? (
                  <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('cportal.no_commitments')}</p>
                ) : (
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                      <thead>
                        <tr style={{ textAlign: 'left', color: C.muted }}>
                          {['col_product', 'col_qty', 'col_requested', 'col_status', 'col_promised', 'col_response'].map(k => (
                            <th key={k} style={{ padding: '4px 8px 4px 0', fontWeight: 600 }}>{t(`cportal.${k}`)}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {detail.commitments.map(c => (
                          <tr key={c.id} style={{ borderTop: `1px solid ${C.border}`, verticalAlign: 'top' }}>
                            <td style={{ padding: '6px 8px 6px 0' }}>
                              <div style={{ fontWeight: 600 }}>{c.description}</div>
                              <div style={{ color: C.dim, fontFamily: 'ui-monospace, monospace' }}>{c.sku}</div>
                            </td>
                            <td style={{ padding: '6px 8px 6px 0', fontVariantNumeric: 'tabular-nums' }}>
                              {c.quantity.toLocaleString(localeFor(lang), { maximumFractionDigits: 2 })}
                            </td>
                            <td style={{ padding: '6px 8px 6px 0' }}>{fmtDate(c.requested_date)}</td>
                            <td style={{ padding: '6px 8px 6px 0' }}>{t(`cportal.commitment_status.${c.status}`)}</td>
                            <td style={{ padding: '6px 8px 6px 0', minWidth: 190 }}>
                              {canWrite && c.status === 'open' ? (
                                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                                  <input type="date" style={{ ...field, width: 'auto' }} value={dates[c.id] ?? ''}
                                    aria-label={t('cportal.col_promised')}
                                    onChange={e => setDates(d => ({ ...d, [c.id]: e.target.value }))} />
                                  <button type="button" style={btn(false, busy || !dates[c.id])} disabled={busy || !dates[c.id]}
                                    onClick={() => void act(() => setPortalPromisedDate(l.id, c.id, dates[c.id]), l.id)}>
                                    {t('cportal.promised_save')}
                                  </button>
                                  {c.promised_date && (
                                    <button type="button" style={btn(false, busy)} disabled={busy}
                                      onClick={() => void act(() => setPortalPromisedDate(l.id, c.id, null), l.id)}>
                                      {t('cportal.promised_clear')}
                                    </button>
                                  )}
                                </div>
                              ) : fmtDate(c.promised_date)}
                            </td>
                            <td style={{ padding: '6px 0' }}>
                              {c.response ? (
                                <>
                                  <div style={{ fontWeight: 600, color: c.response === 'received' ? C.green : C.amber }}>
                                    {t(`cportal.response.${c.response}`)}
                                  </div>
                                  {c.response_comment && <div style={{ color: C.dim, overflowWrap: 'anywhere' }}>{c.response_comment}</div>}
                                </>
                              ) : <span style={{ color: C.dim }}>{t('cportal.no_response')}</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <p style={{ margin: 0, fontSize: 11.5, color: C.dim }}>{t('cportal.promised_hint')}</p>

                <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  {t('cportal.events_title')}
                </div>
                {detail.events.length === 0 ? (
                  <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('cportal.events_empty')}</p>
                ) : (
                  <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12.5 }}>
                    {detail.events.map(ev => {
                      const c = detail.commitments.find(x => x.id === ev.commitment_id)
                      return (
                        <li key={ev.id}>
                          <span style={{ color: C.dim }}>{fmtDate(ev.created_at)}</span>{' '}
                          <strong>{c ? `${c.description} (${c.sku})` : ev.commitment_id}</strong>{' · '}
                          <span style={{ color: ev.response === 'received' ? C.green : C.amber }}>{t(`cportal.response.${ev.response}`)}</span>
                          {ev.comment && <span style={{ overflowWrap: 'anywhere' }}>{': '}{ev.comment}</span>}
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
