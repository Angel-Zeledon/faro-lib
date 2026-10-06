'use client'
/**
 * /cliente/<token> — a corporate customer's read-only view of their own
 * commitments.
 *
 * No account, no app shell (ConditionalShell lets this prefix through) and no
 * session: the link in the message is the credential. The page shows what the
 * API sends and nothing else: product, quantity, requested date, status and, if
 * the company chose to share it, a promised date. The customer can answer a
 * commitment with "I received it" or "the date does not work" plus a comment.
 * Answering never changes the commitment: the company reads it and decides.
 *
 * Language: the company's choice to start, switchable here, local to this page.
 */
import { useCallback, useEffect, useState } from 'react'
import { translations, type Lang } from '@/i18n/translations'
import { answerCustomerPortal, fetchCustomerPortal, CustomerPortalError } from '@/lib/customerPortal'
import type { CustomerPortalAnswer, CustomerPortalPublicView } from '@/lib/types'

const C = {
  bg: 'var(--bg)', surface: 'var(--surface)', border: 'var(--border)', text: 'var(--text)',
  muted: 'var(--muted)', dim: 'var(--dim)', accent: 'var(--accent)',
  green: '#2E8B62', amber: '#B7791F', red: '#C0504D',
}
const MAX_COMMENT = 500

type Phase = 'loading' | 'ready' | 'invalid' | 'failed'
type Item = CustomerPortalPublicView['commitments'][number]

function fmtDay(iso: string | null | undefined, lang: Lang): string {
  if (!iso) return '—'
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(lang === 'en' ? 'en-US' : 'es-CR', { day: 'numeric', month: 'short', year: 'numeric' })
}

function fmtQty(n: number, lang: Lang): string {
  return n.toLocaleString(lang === 'en' ? 'en-US' : 'es-CR', { maximumFractionDigits: 2 })
}

const STATUS_COLOR: Record<Item['status'], string> = { open: C.amber, fulfilled: C.green, cancelled: C.dim }

export default function CustomerPortalPage({ params }: { params: { token: string } }) {
  const token = params.token
  const [phase, setPhase] = useState<Phase>('loading')
  const [view, setView] = useState<CustomerPortalPublicView | null>(null)
  const [lang, setLang] = useState<Lang>('es')
  // The commitment whose "date does not work" comment box is open.
  const [objecting, setObjecting] = useState<string | null>(null)
  const [comment, setComment] = useState('')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ id: string; text: string; ok: boolean } | null>(null)

  const t = useCallback((key: string, p?: Record<string, unknown>) => {
    const dict = translations[lang] as Record<string, string>
    let s = dict[key] ?? (translations.es as Record<string, string>)[key] ?? key
    if (p) for (const [k, v] of Object.entries(p)) s = s.split(`{${k}}`).join(String(v))
    return s
  }, [lang])

  useEffect(() => { document.documentElement.lang = lang }, [lang])

  const load = useCallback(async (keepLang = false) => {
    try {
      const v = await fetchCustomerPortal(token)
      setView(v)
      if (!keepLang) setLang(v.language === 'en' ? 'en' : 'es')
      setPhase('ready')
    } catch (e) {
      // Any bad link is one answer on purpose: say so without guessing why.
      setPhase(e instanceof CustomerPortalError && e.kind === 'invalid' ? 'invalid' : 'failed')
    }
  }, [token])

  useEffect(() => { void load() }, [load])

  async function answer(item: Item, response: CustomerPortalAnswer) {
    if (busyId) return
    const text = comment.trim()
    if (response === 'date_objection' && !text) return
    setBusyId(item.id)
    setNotice(null)
    try {
      await answerCustomerPortal(token, item.id, response, response === 'date_objection' ? text : undefined)
      setObjecting(null)
      setComment('')
      setNotice({
        id: item.id, ok: true,
        text: response === 'received'
          ? t('cportal_page.sent_received')
          : t('cportal_page.sent_objection', { company: view?.company ?? '' }),
      })
      await load(true)
    } catch (e) {
      if (e instanceof CustomerPortalError && e.kind === 'invalid') setPhase('invalid')
      else setNotice({
        id: item.id, ok: false,
        text: t(`cportal_page.err_${e instanceof CustomerPortalError ? e.kind : 'server'}`),
      })
    } finally {
      setBusyId(null)
    }
  }

  const frame = (children: React.ReactNode) => (
    <main style={{
      minHeight: '100dvh', background: C.bg, color: C.text,
      padding: '20px 16px calc(24px + env(safe-area-inset-bottom))',
    }}>
      <div style={{ maxWidth: 720, margin: '0 auto' }}>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
          <div role="group" aria-label={t('cportal_page.language')} style={{ display: 'inline-flex', gap: 4 }}>
            {(['es', 'en'] as const).map(l => (
              <button key={l} type="button" onClick={() => setLang(l)} aria-pressed={lang === l} lang={l}
                style={{
                  minHeight: 36, minWidth: 44, padding: '0 10px', borderRadius: 8, cursor: 'pointer',
                  fontSize: 13, fontWeight: 600, fontFamily: 'inherit',
                  border: `1px solid ${lang === l ? C.accent : C.border}`,
                  background: lang === l ? C.accent : 'transparent',
                  color: lang === l ? '#fff' : C.muted,
                }}>
                {l === 'es' ? 'Español' : 'English'}
              </button>
            ))}
          </div>
        </div>
        {children}
        <p style={{ margin: '28px 0 0', fontSize: 12.5, color: C.dim }}>{t('cportal_page.footer')}</p>
      </div>
    </main>
  )

  if (phase === 'loading') {
    return frame(<p role="status" style={{ color: C.dim, fontSize: 15 }}>{t('cportal_page.loading')}</p>)
  }

  if (phase === 'invalid' || phase === 'failed' || !view) {
    const invalid = phase === 'invalid'
    return frame(
      <section role="alert" style={{ padding: '28px 4px' }}>
        <h1 style={{ margin: '0 0 10px', fontSize: 24, lineHeight: 1.2 }}>
          {invalid ? t('cportal_page.invalid_title') : t('cportal_page.failed')}
        </h1>
        {invalid && (
          <p style={{ margin: 0, fontSize: 16, lineHeight: 1.55, color: C.muted, maxWidth: '60ch' }}>
            {t('cportal_page.invalid_body')}
          </p>
        )}
        {!invalid && (
          <button type="button" onClick={() => { setPhase('loading'); void load(true) }}
            style={{
              marginTop: 18, minHeight: 48, padding: '0 20px', borderRadius: 10, border: 'none',
              background: C.accent, color: '#fff', fontSize: 15, fontWeight: 600, cursor: 'pointer',
            }}>
            {t('cportal_page.retry')}
          </button>
        )}
      </section>,
    )
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box', fontSize: 16, padding: '10px 12px', borderRadius: 10,
    border: `1px solid ${C.border}`, background: 'var(--surface-2)', color: C.text, fontFamily: 'inherit',
  }
  const btn = (primary: boolean, disabled = false): React.CSSProperties => ({
    minHeight: 44, padding: '0 16px', borderRadius: 10, fontSize: 15, fontWeight: 600, fontFamily: 'inherit',
    cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.55 : 1,
    border: `1px solid ${primary ? C.accent : C.border}`,
    background: primary ? C.accent : 'transparent', color: primary ? '#fff' : C.text,
  })

  return frame(
    <>
      <header style={{ marginBottom: 20 }}>
        <h1 style={{ margin: 0, fontSize: 26, lineHeight: 1.2, letterSpacing: '-0.01em', overflowWrap: 'anywhere' }}>
          {t('cportal_page.title', { company: view.company })}
        </h1>
        <p style={{ margin: '8px 0 0', fontSize: 15, color: C.muted, lineHeight: 1.5 }}>{view.customer}</p>
        <p style={{ margin: '14px 0 0', fontSize: 15, lineHeight: 1.55, maxWidth: '62ch' }}>
          {t('cportal_page.intro', { company: view.company })}
        </p>
      </header>

      {view.commitments.length === 0 ? (
        <p role="status" style={{ color: C.muted, fontSize: 15 }}>{t('cportal_page.empty')}</p>
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
          {view.commitments.map(item => {
            const color = STATUS_COLOR[item.status]
            const open = item.status === 'open'
            const mine = notice && notice.id === item.id ? notice : null
            const commentId = `comment-${item.id}`
            return (
              <li key={item.id} style={{
                background: C.surface, border: `1px solid ${C.border}`, borderLeft: `5px solid ${color}`,
                borderRadius: 12, padding: '14px 16px',
              }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, justifyContent: 'space-between' }}>
                  <div style={{ minWidth: 0 }}>
                    <h2 style={{ margin: 0, fontSize: 17, lineHeight: 1.3, overflowWrap: 'anywhere' }}>{item.description}</h2>
                    <div style={{ marginTop: 2, fontSize: 13, color: C.dim, fontFamily: 'ui-monospace, monospace', overflowWrap: 'anywhere' }}>
                      {item.sku}
                    </div>
                  </div>
                  <span style={{
                    flexShrink: 0, padding: '3px 10px', borderRadius: 20, fontSize: 13, fontWeight: 600,
                    color, border: `1px solid ${color}`, whiteSpace: 'nowrap',
                  }}>
                    {t(`cportal_page.status.${item.status}`)}
                  </span>
                </div>

                <dl style={{ margin: '12px 0 0', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '6px 16px', fontSize: 14 }}>
                  <div>
                    <dt style={{ color: C.dim }}>{t('cportal_page.quantity')}</dt>
                    <dd style={{ margin: 0, fontWeight: 700, fontSize: 18, fontVariantNumeric: 'tabular-nums' }}>
                      {fmtQty(item.quantity, lang)}
                    </dd>
                  </div>
                  <div>
                    <dt style={{ color: C.dim }}>{t('cportal_page.requested')}</dt>
                    <dd style={{ margin: 0, fontWeight: 600 }}>{fmtDay(item.requested_date, lang)}</dd>
                  </div>
                  {view.share_dates && item.promised_date && (
                    <div>
                      <dt style={{ color: C.dim }}>{t('cportal_page.promised')}</dt>
                      <dd style={{ margin: 0, fontWeight: 600 }}>{fmtDay(item.promised_date, lang)}</dd>
                    </div>
                  )}
                </dl>

                {item.my_response && (
                  <p style={{ margin: '12px 0 0', fontSize: 14, color: item.my_response === 'received' ? C.green : C.amber, fontWeight: 600 }}>
                    {item.my_response === 'received' ? '✓ ' : '↻ '}
                    {t(item.my_response === 'received' ? 'cportal_page.answered_received' : 'cportal_page.answered_objection')}
                  </p>
                )}

                {item.status !== 'cancelled' && objecting !== item.id && (
                  <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                    {item.my_response !== 'received' && (
                      <button type="button" style={btn(false, busyId === item.id)} disabled={busyId === item.id}
                        onClick={() => void answer(item, 'received')}>
                        {t('cportal_page.received_btn')}
                      </button>
                    )}
                    {open && (
                      <button type="button" style={btn(false)} onClick={() => { setObjecting(item.id); setComment(''); setNotice(null) }}>
                        {t('cportal_page.objection_btn')}
                      </button>
                    )}
                  </div>
                )}

                {objecting === item.id && (
                  <div style={{ marginTop: 12 }}>
                    <label htmlFor={commentId} style={{ display: 'block', fontSize: 14, fontWeight: 600, marginBottom: 6 }}>
                      {t('cportal_page.comment_label')}
                    </label>
                    <textarea id={commentId} value={comment} rows={3} maxLength={MAX_COMMENT}
                      placeholder={t('cportal_page.comment_placeholder')}
                      onChange={e => setComment(e.target.value)} style={{ ...inputStyle, resize: 'vertical' }} />
                    <div style={{ marginTop: 4, fontSize: 12.5, color: C.dim }}>
                      {t('cportal_page.comment_count', { n: comment.length, max: MAX_COMMENT })}
                    </div>
                    <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      <button type="button" style={btn(true, !comment.trim() || busyId === item.id)}
                        disabled={!comment.trim() || busyId === item.id}
                        onClick={() => void answer(item, 'date_objection')}>
                        {t('cportal_page.send')}
                      </button>
                      <button type="button" style={btn(false)} onClick={() => { setObjecting(null); setComment('') }}>
                        {t('cportal_page.cancel')}
                      </button>
                    </div>
                  </div>
                )}

                {mine && (
                  <p role={mine.ok ? 'status' : 'alert'}
                    style={{ margin: '10px 0 0', fontSize: 14, color: mine.ok ? C.green : C.red }}>
                    {mine.text}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </>,
  )
}
