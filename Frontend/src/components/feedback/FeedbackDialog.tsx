'use client'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { getToken, getUser } from '@/lib/auth'
import { sendFeedback } from '@/lib/api'
import { startCapture, type CapturedScreen } from '@/lib/captureScreen'
import { mailFallback, type BugReportFacts } from '@/lib/bugReport'
import { useErrorDetail } from '@/components/ui/States'
import Button from '@/components/ui/Button'
import BottomSheet from '@/components/mobile/BottomSheet'
import ScreenshotEditor, { type ScreenshotEditorHandle } from './ScreenshotEditor'
import { FeedbackContext, type OpenFn } from './context'

/**
 * "Send feedback", the one way a person tells us what happened.
 *
 * Opened from three places and nowhere else: an error (toast, error screen,
 * inline error state), and a plain menu entry for the non-error case. There is
 * deliberately no floating button.
 *
 * Nothing leaves the browser on its own. The flow is compose -> a confirmation
 * step that lists, field by field, exactly what the request will contain ->
 * the person presses "Send". The page picture is taken the moment the dialog is
 * asked for (before it is on screen, so it is a picture of the page and not of
 * the dialog) and its rasterising finishes in the background.
 *
 * Signed out there is no endpoint to send to, so the old behaviour stays: the
 * person's own mail client opens on a prefilled message (`lib/bugReport.ts`).
 */

interface Session {
  facts: BugReportFacts
  capture: Promise<CapturedScreen>
  pagePath: string
  userAgent: string
  appVersion: string
}

const nextFrame = () => new Promise<void>(r => requestAnimationFrame(() => r()))

export function FeedbackProvider({ children }: { children: React.ReactNode }) {
  const { t } = useLanguage()
  const [session, setSession] = useState<Session | null>(null)
  const busy = useRef(false)

  const open = useCallback<OpenFn>((facts = {}) => {
    if (busy.current) return
    // Signed out (an error on the landing or a sign-in screen): no endpoint to
    // call, so the mail client it is.
    if (!getToken()) { mailFallback(t, facts); return }
    busy.current = true
    void (async () => {
      try {
        // Two frames: a menu that was open when the entry was chosen has closed
        // and repainted, so it is not in the picture.
        await nextFrame(); await nextFrame()
        const capture = startCapture()
        // A rejection is handled by the panel; this keeps the runtime from
        // reporting it as unhandled if the panel never mounts.
        capture.catch(() => {})
        setSession({
          facts,
          capture,
          pagePath: window.location.pathname,
          userAgent: navigator.userAgent,
          appVersion: process.env.NEXT_PUBLIC_APP_VERSION || '',
        })
      } finally {
        busy.current = false
      }
    })()
  }, [t])

  return (
    <FeedbackContext.Provider value={open}>
      {children}
      {session && <FeedbackPanel session={session} onClose={() => setSession(null)} />}
    </FeedbackContext.Provider>
  )
}

type Step = 'compose' | 'review' | 'done'
type ShotState = 'capturing' | 'ready' | 'failed'

const MESSAGE_MAX = 4000
const MESSAGE_MIN = 3

function FeedbackPanel({ session, onClose }: { session: Session; onClose: () => void }) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const narrow = useIsNarrow()
  const user = getUser()
  const email = user?.email || ''

  const { facts } = session
  const code = (facts.code || '').trim()
  const [message, setMessage] = useState(() => (facts.detail || facts.code || '').trim())
  const [step, setStep] = useState<Step>('compose')
  const [shotState, setShotState] = useState<ShotState>('capturing')
  const [rawShot, setRawShot] = useState<string | null>(null)
  const [includeShot, setIncludeShot] = useState(true)
  const [finalShot, setFinalShot] = useState<string | null>(null)
  const [consentReply, setConsentReply] = useState(false)
  const [consentNews, setConsentNews] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [notified, setNotified] = useState(false)
  const editor = useRef<ScreenshotEditorHandle>(null)
  const textRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    let alive = true
    session.capture
      .then(c => { if (alive) { setRawShot(c.dataUrl); setShotState('ready') } })
      .catch(() => { if (alive) setShotState('failed') })
    return () => { alive = false }
  }, [session])

  const trimmed = message.trim()
  const canReview = trimmed.length >= MESSAGE_MIN && trimmed.length <= MESSAGE_MAX
  const shotWillBeSent = shotState === 'ready' && includeShot

  const toReview = () => {
    setError('')
    if (shotWillBeSent) {
      const flat = editor.current?.exportImage() ?? null
      if (!flat) {
        // Could not be encoded under the cap: say so instead of silently
        // sending something different from what the person saw.
        setShotState('failed')
        setFinalShot(null)
      } else {
        setFinalShot(flat)
      }
    } else {
      setFinalShot(null)
    }
    setStep('review')
  }

  const send = async () => {
    setSending(true)
    setError('')
    try {
      const res = await sendFeedback({
        message: trimmed,
        error_code: code || null,
        page_path: session.pagePath,
        user_agent: session.userAgent,
        app_version: session.appVersion,
        screenshot: shotWillBeSent ? finalShot : null,
        consent_reply: consentReply,
        consent_news: consentNews,
      })
      setNotified(res.notified)
      setStep('done')
    } catch (e) {
      // Their text stays on screen; a coded refusal (rate limit, bad image)
      // is read in their language, anything else gets the generic line.
      const detail = errorDetail(e)
      setError(detail || t('feedback.send_failed'))
    } finally {
      setSending(false)
    }
  }

  // ---- shared pieces ------------------------------------------------------

  const label: React.CSSProperties = {
    display: 'block', fontSize: 11.5, fontWeight: 600, color: 'var(--muted)',
    marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.3,
  }
  const check: React.CSSProperties = {
    display: 'flex', alignItems: 'flex-start', gap: 10, cursor: 'pointer', fontSize: 13.5,
    color: 'var(--text)', lineHeight: 1.45, minHeight: 28,
  }
  const box: React.CSSProperties = { width: 18, height: 18, marginTop: 1, flexShrink: 0, accentColor: 'var(--accent)' }
  const hint: React.CSSProperties = { display: 'block', fontSize: 12, color: 'var(--dim)', marginTop: 2 }

  const compose = (
    <div style={{ display: step === 'compose' ? 'block' : 'none' }}>
      <p style={{ margin: '0 0 14px', fontSize: 13, lineHeight: 1.55, color: 'var(--dim)' }}>
        {t('feedback.intro')}
      </p>

      {code && (
        <p style={{
          margin: '0 0 12px', fontSize: 12, color: 'var(--muted)', fontFamily: 'ui-monospace, monospace',
          wordBreak: 'break-word',
        }}>
          {t('feedback.error_context', { code })}
        </p>
      )}

      <label htmlFor="feedback-message" style={label}>{t('feedback.message_label')}</label>
      <textarea
        id="feedback-message"
        ref={textRef}
        value={message}
        onChange={e => setMessage(e.target.value)}
        placeholder={t('feedback.message_placeholder')}
        rows={5}
        maxLength={MESSAGE_MAX}
        aria-describedby="feedback-message-help"
        style={{
          width: '100%', padding: '10px 12px', borderRadius: 8, fontSize: 14, resize: 'vertical',
          background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)',
          outline: 'none', fontFamily: 'inherit', lineHeight: 1.5, boxSizing: 'border-box',
        }}
      />
      <div id="feedback-message-help" style={{
        display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--dim)', margin: '4px 0 16px',
      }}>
        <span>{trimmed.length < MESSAGE_MIN ? t('feedback.message_too_short') : ''}</span>
        <span>{trimmed.length} / {MESSAGE_MAX}</span>
      </div>

      <div style={{ marginBottom: 18 }}>
        <span style={label}>{t('feedback.shot_heading')}</span>
        {shotState === 'capturing' && (
          <p role="status" style={{ margin: 0, fontSize: 13, color: 'var(--dim)' }}>{t('feedback.shot_capturing')}</p>
        )}
        {shotState === 'failed' && (
          <p role="status" style={{
            margin: 0, fontSize: 13, color: 'var(--text)', background: 'var(--surface-2)',
            border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', lineHeight: 1.5,
          }}>
            {t('feedback.shot_failed')}
          </p>
        )}
        {shotState === 'ready' && rawShot && (
          <>
            <label style={{ ...check, marginBottom: 10 }}>
              <input
                type="checkbox" checked={includeShot} style={box}
                onChange={e => setIncludeShot(e.target.checked)}
              />
              <span>{t('feedback.shot_include')}</span>
            </label>
            {/* Kept mounted when unticked so the marks survive a change of mind. */}
            <div style={{ display: includeShot ? 'block' : 'none' }}>
              <p style={{ margin: '0 0 10px', fontSize: 12, color: 'var(--dim)', lineHeight: 1.5 }}>
                {t('feedback.shot_hint')}
              </p>
              <ScreenshotEditor ref={editor} src={rawShot} />
            </div>
            {!includeShot && (
              <p style={{ margin: 0, fontSize: 12.5, color: 'var(--dim)' }}>{t('feedback.shot_excluded')}</p>
            )}
          </>
        )}
      </div>

      <fieldset style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <legend style={{ ...label, padding: 0 }}>{t('feedback.consent_heading')}</legend>
        <label style={{ ...check, marginBottom: 10 }}>
          <input type="checkbox" checked={consentReply} style={box} onChange={e => setConsentReply(e.target.checked)} />
          <span>
            {t('feedback.consent_reply')}
            <span style={hint}>{t('feedback.consent_reply_hint', { email: email || '—' })}</span>
          </span>
        </label>
        <label style={check}>
          <input type="checkbox" checked={consentNews} style={box} onChange={e => setConsentNews(e.target.checked)} />
          <span>
            {t('feedback.consent_news')}
            <span style={hint}>{t('feedback.consent_news_hint')}</span>
          </span>
        </label>
      </fieldset>
    </div>
  )

  const row = (k: string, v: React.ReactNode) => (
    <div style={{ padding: '10px 0', borderTop: '1px solid var(--border)' }}>
      <div style={{ ...label, marginBottom: 3 }}>{k}</div>
      <div style={{ fontSize: 13.5, color: 'var(--text)', overflowWrap: 'anywhere', lineHeight: 1.5 }}>{v}</div>
    </div>
  )
  const yn = (b: boolean) => (b ? t('feedback.yes') : t('feedback.no'))

  const review = step === 'review' && (
    <div>
      <p style={{ margin: '0 0 12px', fontSize: 13, lineHeight: 1.55, color: 'var(--text)', fontWeight: 600 }}>
        {t('feedback.review_intro')}
      </p>
      {row(t('feedback.review_message'), <span style={{ whiteSpace: 'pre-wrap' }}>{trimmed}</span>)}
      {row(
        t('feedback.review_screenshot'),
        shotWillBeSent && finalShot
          // eslint-disable-next-line @next/next/no-img-element
          ? <img src={finalShot} alt={t('feedback.shot_alt')}
              style={{ display: 'block', maxWidth: '100%', maxHeight: 220, borderRadius: 6, border: '1px solid var(--border)' }} />
          : t('feedback.review_no_screenshot'),
      )}
      {row(t('feedback.review_page'), session.pagePath || '—')}
      {row(t('feedback.review_browser'), session.userAgent || '—')}
      {row(t('feedback.review_version'), session.appVersion || '—')}
      {row(t('feedback.review_error'), code || t('feedback.none'))}
      {row(t('feedback.review_account'), email || '—')}
      {row(t('feedback.review_reply'), yn(consentReply))}
      {row(t('feedback.review_news'), yn(consentNews))}
      {error && (
        <p role="alert" style={{ margin: '12px 0 0', fontSize: 13, color: '#C0504D', lineHeight: 1.5 }}>{error}</p>
      )}
    </div>
  )

  const done = step === 'done' && (
    <div role="status">
      <p style={{ margin: '0 0 6px', fontSize: 15, fontWeight: 700, color: 'var(--text)' }}>
        {t(notified ? 'feedback.sent_title' : 'feedback.saved_title')}
      </p>
      <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.55, color: 'var(--dim)' }}>
        {t(notified ? 'feedback.sent_body' : 'feedback.saved_body')}
      </p>
    </div>
  )

  const footer = step === 'compose' ? (
    <>
      <Button variant="ghost" onClick={onClose}>{t('feedback.cancel')}</Button>
      <Button variant="primary" disabled={!canReview || shotState === 'capturing'} onClick={toReview}>
        {t('feedback.review_button')}
      </Button>
    </>
  ) : step === 'review' ? (
    <>
      <Button variant="ghost" disabled={sending} onClick={() => setStep('compose')}>{t('feedback.back')}</Button>
      <Button variant="primary" loading={sending} onClick={send}>
        {sending ? t('feedback.sending') : t('feedback.send')}
      </Button>
    </>
  ) : (
    <Button variant="primary" onClick={onClose}>{t('feedback.close')}</Button>
  )

  const title = t(step === 'review' ? 'feedback.title_review' : 'feedback.title')
  const body = (
    <>
      {compose}
      {review}
      {done}
    </>
  )

  if (narrow) {
    return (
      <BottomSheet
        open
        onClose={onClose}
        title={title}
        maxHeight="94dvh"
        initialFocusRef={textRef}
        footer={<div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', width: '100%' }}>{footer}</div>}
      >
        {body}
      </BottomSheet>
    )
  }
  return (
    <DesktopModal title={title} onClose={onClose} footer={footer} initialFocusRef={textRef}>
      {body}
    </DesktopModal>
  )
}

/** Centered dialog: Esc closes, Tab cannot leave, focus returns to the opener. */
function DesktopModal({ title, onClose, footer, children, initialFocusRef }: {
  title: string
  onClose: () => void
  footer: React.ReactNode
  children: React.ReactNode
  initialFocusRef: React.RefObject<HTMLElement>
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  const titleId = useMemo(() => `feedback-title-${Math.random().toString(36).slice(2, 8)}`, [])

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    initialFocusRef.current?.focus({ preventScroll: true })
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); closeRef.current(); return }
      if (e.key !== 'Tab' || !panelRef.current) return
      const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select, textarea, canvas[tabindex], [tabindex]:not([tabindex="-1"])',
      )).filter(el => el.offsetParent !== null)
      if (!items.length) return
      const first = items[0], last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      opener?.focus?.({ preventScroll: true })
    }
  }, [initialFocusRef])

  return (
    <div
      className="modal-backdrop-enter"
      onClick={() => closeRef.current()}
      style={{
        position: 'fixed', inset: 0, zIndex: 10000, background: 'rgba(0,0,0,0.55)',
        backdropFilter: 'blur(3px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={e => e.stopPropagation()}
        className="modal-panel-enter"
        style={{
          background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14,
          width: '100%', maxWidth: 640, maxHeight: '90vh', display: 'flex', flexDirection: 'column',
          boxShadow: '0 24px 60px -20px rgba(0,0,0,0.5)',
        }}
      >
        <h2 id={titleId} style={{ margin: 0, padding: '20px 24px 8px', fontSize: 17, fontWeight: 700, color: 'var(--text)' }}>
          {title}
        </h2>
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '8px 24px 16px' }}>{children}</div>
        <div style={{
          display: 'flex', gap: 10, justifyContent: 'flex-end', padding: '12px 24px',
          borderTop: '1px solid var(--border)',
        }}>
          {footer}
        </div>
      </div>
    </div>
  )
}
