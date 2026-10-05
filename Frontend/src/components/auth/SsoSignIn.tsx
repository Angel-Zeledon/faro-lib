'use client'
/**
 * "Sign in with your company" - enterprise single sign-on.
 *
 * Renders NOTHING unless the installation turned the feature on
 * (`/auth/sso/availability`), so a source install shows exactly the e-mail +
 * password form it always did. When it is on, the person types their WORK
 * e-mail; the backend resolves which company provider that domain belongs to
 * and, if there is one, the whole window goes to that provider's page - a
 * navigation, not a fetch, because the provider has to take over.
 *
 * An address with no company provider is told so plainly (never a blank
 * failure), and the password form stays where it was.
 *
 * `forceOpen` is for the one moment the person has no choice: their company made
 * single sign-on mandatory and the password form said so.
 */
import { useEffect, useState } from 'react'
import { Building2 } from 'lucide-react'
import { getSsoAvailability, ssoDiscover, ssoStartUrl } from '@/lib/api'
import { useLanguage } from '@/contexts/LanguageContext'

export function SsoSignIn({ initialEmail = '', forceOpen = false }: {
  initialEmail?: string
  forceOpen?: boolean
}) {
  const { t } = useLanguage()
  const [enabled, setEnabled] = useState(false)
  const [open, setOpen] = useState(false)
  const [email, setEmail] = useState(initialEmail)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    getSsoAvailability()
      .then(r => { if (alive) setEnabled(Boolean(r.enabled)) })
      // Unreachable or old backend: no option, which is the pre-feature page.
      .catch(() => { if (alive) setEnabled(false) })
    return () => { alive = false }
  }, [])

  useEffect(() => { if (forceOpen) setOpen(true) }, [forceOpen])
  useEffect(() => { if (initialEmail) setEmail(initialEmail) }, [initialEmail])

  // Back button restores the page from the bfcache with the spinner still on.
  useEffect(() => {
    const onShow = () => setBusy(false)
    window.addEventListener('pageshow', onShow)
    return () => window.removeEventListener('pageshow', onShow)
  }, [])

  if (!enabled) return null

  async function go(e: React.FormEvent) {
    e.preventDefault()
    const value = email.trim()
    if (!value) return
    setNote(null)
    setBusy(true)
    try {
      const r = await ssoDiscover(value)
      if (r.available) {
        window.location.href = ssoStartUrl(value)
        return
      }
      setNote(t('auth.sso_not_available_for_email'))
    } catch {
      setNote(t('auth.sso_check_failed'))
    }
    setBusy(false)
  }

  return (
    <div style={{ marginTop: 18 }}>
      {!open ? (
        <button
          type="button" onClick={() => setOpen(true)} data-testid="sso-open"
          style={{
            all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center',
            justifyContent: 'center', gap: 8, width: '100%', boxSizing: 'border-box',
            padding: '11px 12px', borderRadius: 11, border: '1px solid var(--a-line)',
            fontSize: 13.5, fontWeight: 500, color: 'var(--a-ink)',
          }}
        >
          <Building2 size={15} aria-hidden="true" />
          {t('auth.sso_open')}
        </button>
      ) : (
        <form onSubmit={go} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <label htmlFor="sso-email" style={{ fontSize: 12, fontWeight: 500, color: 'var(--a-muted)' }}>
            {t('auth.sso_email_label')}
          </label>
          <div className="auth-field">
            <input
              id="sso-email" name="sso_email" className="auth-input" type="email"
              value={email} required autoComplete="email" autoFocus
              onChange={e => setEmail(e.target.value)}
              placeholder={t('auth.ph_email')}
            />
          </div>
          {note && (
            <p role="alert" style={{ margin: 0, fontSize: 12.5, color: '#B94A4A', lineHeight: 1.45 }}>{note}</p>
          )}
          <button
            type="submit" disabled={busy || !email.trim()}
            style={{
              width: '100%', padding: '11px', borderRadius: 11, border: '1px solid var(--a-line)',
              background: 'transparent', color: 'var(--a-ink)', fontSize: 13.5, fontWeight: 600,
              cursor: busy ? 'wait' : 'pointer',
            }}
          >
            {busy ? t('auth.sso_going') : t('auth.sso_continue')}
          </button>
        </form>
      )}
    </div>
  )
}
