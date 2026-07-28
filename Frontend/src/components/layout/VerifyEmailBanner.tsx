'use client'
/**
 * An unverified account is no longer refused at login — it can explore, upload
 * and look around; only the outward actions (inviting people, integrations,
 * sending notifications) demand a verified address. That left one gap: the user
 * had no way of knowing they were unverified until they walked into one of
 * those walls, halfway through doing something.
 *
 * This says it up front, on every screen, with the way out attached. Dismissal
 * lasts the browser session (sessionStorage) — long enough not to nag inside one
 * sitting, short enough that it comes back tomorrow if the account is still
 * unverified.
 *
 * Source of truth is the server (`GET /users/me`), not the cached login payload:
 * a session that started before verification would otherwise keep showing the
 * banner after the user verified in another tab.
 */
import { useEffect, useState } from 'react'
import { MailWarning, MailCheck, X } from 'lucide-react'
import { getMe, authResendVerification } from '@/lib/api'
import { getUser } from '@/lib/auth'
import { useLanguage } from '@/contexts/LanguageContext'

const AMBER = '#f59e0b'
const DISMISS_KEY = 'faro_verify_banner_dismissed'

export default function VerifyEmailBanner() {
  const { t } = useLanguage()
  const [unverified, setUnverified] = useState(false)
  const [dismissed,  setDismissed]  = useState(true)   // hidden until we know
  const [email,      setEmail]      = useState('')
  const [resending,  setResending]  = useState(false)
  const [sentNote,   setSentNote]   = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    if (sessionStorage.getItem(DISMISS_KEY) === '1') return
    getMe()
      .then(me => {
        if (!alive) return
        // `email_verified` is optional on the type; only an explicit false is a
        // reason to nag. An older backend that omits it must not produce a
        // banner nobody can act on.
        if (me.email_verified === false) {
          setEmail(me.email)
          setUnverified(true)
          setDismissed(false)
        }
      })
      .catch(() => { /* a failed /users/me is the auth layer's problem, not ours */ })
    return () => { alive = false }
  }, [])

  async function handleResend() {
    setResending(true)
    try {
      await authResendVerification(email || getUser()?.email || '')
    } catch {
      // The endpoint answers identically for every address by design, so there
      // is nothing here the user could act on differently.
    } finally {
      setSentNote(t('auth.resend_verification_done'))
      setResending(false)
    }
  }

  function handleDismiss() {
    sessionStorage.setItem(DISMISS_KEY, '1')
    setDismissed(true)
  }

  if (!unverified || dismissed) return null

  return (
    <div
      role="status"
      style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        padding: '10px 14px', borderRadius: 10, margin: '0 0 14px',
        background: `${AMBER}10`, border: `1px solid ${AMBER}40`,
      }}
    >
      <MailWarning size={14} color={AMBER} style={{ flexShrink: 0 }} />
      <span style={{ fontSize: 12, color: 'var(--text)', flex: 1, minWidth: 220 }}>
        {t('errors.email_not_verified')}
      </span>

      {sentNote ? (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--muted)' }}>
          <MailCheck size={13} style={{ flexShrink: 0 }} />
          {sentNote}
        </span>
      ) : (
        <button
          type="button"
          onClick={handleResend}
          disabled={resending}
          style={{
            all: 'unset', cursor: resending ? 'wait' : 'pointer',
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '5px 11px', borderRadius: 7,
            border: `1px solid ${AMBER}66`, color: AMBER,
            fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap',
          }}
        >
          <MailCheck size={13} />
          {resending ? t('auth.resend_verification_sending') : t('auth.resend_verification')}
        </button>
      )}

      <button
        type="button"
        onClick={handleDismiss}
        title={t('common.close')}
        aria-label={t('common.close')}
        style={{
          all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5,
          color: 'var(--dim)', display: 'flex', alignItems: 'center',
        }}
      >
        <X size={13} />
      </button>
    </div>
  )
}
