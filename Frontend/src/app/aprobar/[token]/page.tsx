'use client'
/**
 * /aprobar/<token> — where an approver decides a purchase order from a message.
 *
 * No account, no app shell (ConditionalShell lets this prefix through), no
 * session: the link in the email or WhatsApp message is the credential. Opening
 * the page only READS (a mail scanner that prefetches the URL decides nothing);
 * the order is approved or rejected only by pressing a button here, which sends
 * one explicit POST. The page shows what the approver needs to decide — reference,
 * supplier, lines, total, who asked — and nothing finer: the API never sends unit
 * costs, stock or other orders.
 *
 * Language: the browser's to start, switchable here. The switch is local to this
 * page on purpose; `LanguageContext.setLang` would write to an account that may
 * not be signed in on this device.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { translations, type Lang } from '@/i18n/translations'
import {
  fetchApprovalLink, decideApprovalLink, ApprovalLinkError, type ApprovalLinkView,
} from '@/lib/approvalLink'

const C = {
  bg: 'var(--bg)', surface: 'var(--surface)', border: 'var(--border)', text: 'var(--text)',
  muted: 'var(--muted)', dim: 'var(--dim)', accent: 'var(--accent)',
  green: '#2E8B62', red: '#C0504D',
}
const MIN_REASON = 3

type Phase = 'loading' | 'ready' | 'invalid' | 'failed' | 'done'

function initialLang(): Lang {
  try { return navigator.language?.toLowerCase().startsWith('en') ? 'en' : 'es' } catch { return 'es' }
}

function money(v: ApprovalLinkView, lang: Lang): string {
  try {
    return new Intl.NumberFormat(v.currency.locale || (lang === 'en' ? 'en-US' : 'es-CR'), {
      style: 'currency', currency: v.currency.code,
      minimumFractionDigits: v.currency.decimals, maximumFractionDigits: v.currency.decimals,
    }).format(v.amount)
  } catch {
    return `${v.currency.symbol}${v.amount.toFixed(v.currency.decimals)}`
  }
}

function fmtDate(iso: string | null, lang: Lang): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(lang === 'en' ? 'en-US' : 'es-CR', { dateStyle: 'medium', timeStyle: 'short' })
}

export default function ApprovalLinkPage({ params }: { params: { token: string } }) {
  const token = params.token
  const [phase, setPhase] = useState<Phase>('loading')
  const [view, setView] = useState<ApprovalLinkView | null>(null)
  const [lang, setLang] = useState<Lang>('es')
  const [rejecting, setRejecting] = useState(false)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<'approved' | 'rejected' | null>(null)

  const t = useCallback((key: string, p?: Record<string, unknown>) => {
    const dict = translations[lang] as Record<string, string>
    let s = dict[key] ?? (translations.es as Record<string, string>)[key] ?? key
    if (p) for (const [k, v] of Object.entries(p)) s = s.split(`{${k}}`).join(String(v))
    return s
  }, [lang])

  useEffect(() => { setLang(initialLang()) }, [])
  useEffect(() => { document.documentElement.lang = lang }, [lang])

  const load = useCallback(async () => {
    try {
      setView(await fetchApprovalLink(token))
      setPhase('ready')
    } catch (e) {
      // Any bad link is one answer on purpose: say so without guessing why.
      setPhase(e instanceof ApprovalLinkError && e.kind === 'invalid' ? 'invalid' : 'failed')
    }
  }, [token])

  useEffect(() => { void load() }, [load])

  const reasonOk = useMemo(() => comment.trim().length >= MIN_REASON, [comment])

  async function decide(decision: 'approved' | 'rejected') {
    if (!view || busy) return
    if (decision === 'rejected' && !reasonOk) { setNotice(t('approval_link.err_reason')); return }
    setBusy(true)
    setNotice(null)
    try {
      await decideApprovalLink(token, decision, comment)
      setOutcome(decision)
      setPhase('done')
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (e) {
      if (!(e instanceof ApprovalLinkError)) setNotice(t('approval_link.err_server'))
      else if (e.kind === 'invalid') setPhase('invalid')
      else if (e.kind === 'rejected') {
        const key = `errors.${e.code}`
        const known = (translations[lang] as Record<string, string>)[key]
        setNotice(known ?? t('approval_link.err_rejected'))
      } else setNotice(t(`approval_link.err_${e.kind}`))
    } finally {
      setBusy(false)
    }
  }

  const frame = (children: React.ReactNode) => (
    <main style={{ minHeight: '100dvh', background: C.bg, color: C.text, padding: '20px 16px calc(24px + env(safe-area-inset-bottom))' }}>
      <div style={{ maxWidth: 640, margin: '0 auto' }}>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
          <div role="group" aria-label={t('approval_link.language')} style={{ display: 'inline-flex', gap: 4 }}>
            {(['es', 'en'] as const).map(l => (
              <button key={l} type="button" onClick={() => setLang(l)} aria-pressed={lang === l} lang={l}
                style={{
                  minHeight: 36, minWidth: 44, padding: '0 10px', borderRadius: 8, cursor: 'pointer',
                  fontSize: 13, fontWeight: 600, fontFamily: 'inherit',
                  border: `1px solid ${lang === l ? C.accent : C.border}`,
                  background: lang === l ? C.accent : 'transparent', color: lang === l ? '#fff' : C.muted,
                }}>
                {l === 'es' ? 'Español' : 'English'}
              </button>
            ))}
          </div>
        </div>
        {children}
      </div>
    </main>
  )

  if (phase === 'loading') {
    return frame(<p role="status" style={{ color: C.dim, fontSize: 15 }}>{t('approval_link.loading')}</p>)
  }

  if (phase === 'invalid' || phase === 'failed' || !view) {
    const invalid = phase === 'invalid'
    return frame(
      <section role="alert" style={{ padding: '28px 4px' }}>
        <h1 style={{ margin: '0 0 10px', fontSize: 24, lineHeight: 1.2 }}>
          {t(invalid ? 'approval_link.invalid_title' : 'approval_link.failed_title')}
        </h1>
        <p style={{ margin: 0, fontSize: 16, lineHeight: 1.55, color: C.muted, maxWidth: '60ch' }}>
          {t(invalid ? 'approval_link.invalid_body' : 'approval_link.failed_body')}
        </p>
        {!invalid && (
          <button type="button" onClick={() => { setPhase('loading'); void load() }}
            style={{ marginTop: 18, minHeight: 48, padding: '0 20px', borderRadius: 10, border: 'none',
                     background: C.accent, color: '#fff', fontSize: 15, fontWeight: 600, cursor: 'pointer' }}>
            {t('approval_link.retry')}
          </button>
        )}
      </section>,
    )
  }

  if (phase === 'done' && outcome) {
    const ok = outcome === 'approved'
    return frame(
      <section role="status" style={{ padding: '22px 18px', borderRadius: 12, border: `1px solid ${ok ? C.green : C.red}`, background: C.surface }}>
        <h1 style={{ margin: '0 0 8px', fontSize: 22, color: ok ? C.green : C.red }}>
          {ok ? '✓ ' : '✕ '}{t(ok ? 'approval_link.done_approved_title' : 'approval_link.done_rejected_title', { reference: view.reference })}
        </h1>
        <p style={{ margin: 0, fontSize: 15, lineHeight: 1.55 }}>
          {t(ok ? 'approval_link.done_approved_body' : 'approval_link.done_rejected_body')}
        </p>
        <p style={{ margin: '10px 0 0', fontSize: 14, color: C.muted }}>{t('approval_link.done_close')}</p>
      </section>,
    )
  }

  const btn = (bg: string, outline = false): React.CSSProperties => ({
    flex: '1 1 160px', minHeight: 52, padding: '0 18px', borderRadius: 10, fontSize: 16, fontWeight: 700,
    cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.6 : 1, fontFamily: 'inherit',
    border: `2px solid ${bg}`, background: outline ? 'transparent' : bg, color: outline ? bg : '#fff',
  })
  const more = view.line_count - view.lines.length

  return frame(
    <>
      <header style={{ marginBottom: 18 }}>
        <h1 style={{ margin: 0, fontSize: 26, lineHeight: 1.2, letterSpacing: '-0.01em' }}>
          {t('approval_link.heading', { reference: view.reference })}
        </h1>
        <p style={{ margin: '8px 0 0', fontSize: 15, color: C.muted, lineHeight: 1.5 }}>
          {view.requested_by_name
            ? t('approval_link.requested_by', { name: view.requested_by_name, buyer: view.buyer ?? '' })
            : t('approval_link.requested_unknown')}
        </p>
      </header>

      <section style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: '14px 16px', marginBottom: 14 }}>
        <div style={{ fontSize: 13, color: C.dim }}>{t('approval_link.amount_label')}</div>
        <div style={{ fontSize: 30, fontWeight: 700, letterSpacing: '-0.01em' }}>{money(view, lang)}</div>
        <dl style={{ margin: '10px 0 0', display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 12px', fontSize: 14 }}>
          {view.suppliers.length > 0 && (<><dt style={{ color: C.dim }}>{t('approval_link.suppliers')}</dt><dd style={{ margin: 0, overflowWrap: 'anywhere' }}>{view.suppliers.join(', ')}</dd></>)}
          {view.warehouse && (<><dt style={{ color: C.dim }}>{t('approval_link.warehouse')}</dt><dd style={{ margin: 0 }}>{view.warehouse}</dd></>)}
          <dt style={{ color: C.dim }}>{t('approval_link.requested_at')}</dt><dd style={{ margin: 0 }}>{fmtDate(view.requested_at, lang)}</dd>
          {view.note && (<><dt style={{ color: C.dim }}>{t('approval_link.note')}</dt><dd style={{ margin: 0, overflowWrap: 'anywhere' }}>{view.note}</dd></>)}
        </dl>
      </section>

      <section aria-label={t('approval_link.lines_title', { count: view.line_count })} style={{ marginBottom: 16 }}>
        <h2 style={{ margin: '0 0 8px', fontSize: 16 }}>{t('approval_link.lines_title', { count: view.line_count })}</h2>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {view.lines.map(l => (
            <li key={l.sku} style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: '10px 14px', display: 'flex', justifyContent: 'space-between', gap: 12 }}>
              <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
                {l.name}
                <span style={{ display: 'block', fontSize: 12, color: C.dim, fontFamily: 'ui-monospace, monospace' }}>{l.sku}</span>
              </span>
              <span style={{ whiteSpace: 'nowrap', fontWeight: 600 }}>
                {l.quantity.toLocaleString(lang === 'en' ? 'en-US' : 'es-CR', { maximumFractionDigits: 2 })}
              </span>
            </li>
          ))}
        </ul>
        {more > 0 && <p style={{ margin: '8px 0 0', fontSize: 13, color: C.muted }}>{t('approval_link.lines_more', { n: more })}</p>}
      </section>

      {!view.can_approve && (
        <p role="note" style={{ margin: '0 0 14px', fontSize: 14, color: C.muted, lineHeight: 1.5 }}>{t('approval_link.self_note')}</p>
      )}

      <label htmlFor="al-comment" style={{ display: 'block', fontSize: 14, fontWeight: 600, marginBottom: 6 }}>
        {t(rejecting ? 'approval_link.comment_required' : 'approval_link.comment_optional')}
      </label>
      <textarea id="al-comment" value={comment} maxLength={view.comment_max} rows={3}
        onChange={e => { setComment(e.target.value); setNotice(null) }}
        placeholder={t('approval_link.comment_placeholder')}
        style={{ width: '100%', boxSizing: 'border-box', borderRadius: 10, border: `1px solid ${C.border}`, background: C.surface,
                 color: C.text, padding: '10px 12px', fontSize: 15, fontFamily: 'inherit', resize: 'vertical' }} />

      {notice && <p role="alert" style={{ margin: '10px 0 0', fontSize: 14, color: C.red }}>{notice}</p>}

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 14 }}>
        {view.can_approve && !rejecting && (
          <button type="button" disabled={busy} onClick={() => decide('approved')} style={btn(C.green)}>
            {t('approval_link.approve')}
          </button>
        )}
        {!rejecting
          ? <button type="button" disabled={busy} onClick={() => { setRejecting(true); setNotice(null) }} style={btn(C.red, true)}>{t('approval_link.reject')}</button>
          : (<>
              <button type="button" disabled={busy || !reasonOk} onClick={() => decide('rejected')} style={btn(C.red)}>{t('approval_link.reject_confirm')}</button>
              <button type="button" disabled={busy} onClick={() => { setRejecting(false); setNotice(null) }} style={btn(C.muted, true)}>{t('approval_link.back')}</button>
            </>)}
      </div>

      <p style={{ margin: '16px 0 0', fontSize: 12, color: C.dim, lineHeight: 1.5 }}>
        {t('approval_link.expires', { date: fmtDate(view.expires_at, lang) })}
      </p>
    </>,
  )
}
