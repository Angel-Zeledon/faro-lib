'use client'
import { Suspense, useState, useEffect } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import {
  authLogin, authResendVerification, isApiError,
  isLoginSession, isMfaChallenge, isMfaEnrollment, type LoginSession,
} from '@/lib/api'
import { setAuth, isAuthenticated } from '@/lib/auth'
import { INTRO_SEEN_KEY } from '@/components/layout/AppIntro'
import { Eye, EyeOff, AlertTriangle, MailCheck } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useAuthErrorText } from '@/hooks/useAuthErrorText'
import { SocialButtons, socialErrorText } from '@/components/auth/SocialButtons'
import { SsoSignIn } from '@/components/auth/SsoSignIn'
import MfaLoginStep, { type MfaStep } from '@/components/auth/MfaLoginStep'

// The split stage (wordmark, form column, the morning-list panel) comes from
// (auth)/layout.tsx — this file renders only the form, centred in its column.

function LoginPageContent() {
  const { t } = useLanguage()
  const authErrorText = useAuthErrorText()
  const router = useRouter()
  const searchParams = useSearchParams()
  const wantsDemo = searchParams.get('demo') === '1'
  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [showPw,   setShowPw]   = useState(false)
  const [loading,  setLoading]  = useState(false)
  // A provider sign-in that was refused comes back here as `?oauth_error=<code>`
  // (never with a token). Shown in the same box as a password error.
  const oauthError = searchParams.get('oauth_error')
  const [error,    setError]    = useState<string | null>(
    oauthError ? socialErrorText(t, oauthError) : null,
  )
  // A login refused for a verification reason is the one error the user cannot
  // fix by retyping something, so it gets an action instead of just a message.
  const [canResend,  setCanResend]  = useState(false)
  // Their company made single sign-on mandatory: the password form refused, so
  // the company option opens by itself with the address already typed.
  const [ssoRequired, setSsoRequired] = useState(false)
  const [resending,  setResending]  = useState(false)
  const [resentNote, setResentNote] = useState<string | null>(null)
  // The second step (a code, or enrolling when the organization requires it)
  // replaces the password form; no token exists until the server issues one.
  const [mfaStep, setMfaStep] = useState<MfaStep | null>(null)

  // `/login` is a public path, so AuthGuard lets it render even with a live
  // session — a user coming back to a still-valid tab would otherwise be shown
  // a login form for an account they're already in. Send them to the same
  // destination a fresh login would (feature 1.2: post-login lands on /hoy).
  const destination = wantsDemo ? '/quick-start?demo=1' : '/hoy'
  useEffect(() => {
    if (isAuthenticated()) router.replace(destination)
  }, [router, destination])

  const VERIFICATION_CODES = ['email_not_verified', 'account_pending_confirmation']

  async function handleResend() {
    setResending(true)
    setResentNote(null)
    try {
      await authResendVerification(email)
    } catch {
      // The endpoint answers the same for every address by design, so there is
      // nothing useful to distinguish here — say it was requested either way
      // rather than inventing a failure the user cannot act on.
    } finally {
      setResentNote(t('auth.resend_verification_done'))
      setResending(false)
    }
  }

  function finishLogin(res: LoginSession) {
    setAuth(res.access_token, res.refresh_token, {
      id:        res.user.id,
      email:     res.user.email,
      full_name: res.user.full_name,
      role:      res.user.role,
      tenant_id: res.user.tenant_id,
    })
    // Every sign-in opens the app with its entrance (AppIntro).
    try { sessionStorage.removeItem(INTRO_SEEN_KEY) } catch { /* storage blocked */ }
    router.replace(destination)
  }

  // Right after enrolling: the password is still in the form, so ask again and
  // the server answers with the challenge the new factor now answers.
  async function signInAgainAfterEnroll() {
    setLoading(true)
    try {
      const res = await authLogin(email, password)
      if (isLoginSession(res)) finishLogin(res)
      else if (isMfaChallenge(res)) setMfaStep({ kind: 'challenge', token: res.mfa_token, justEnrolled: true })
      else setMfaStep(null)
    } catch (err: unknown) {
      setMfaStep(null)
      setError(authErrorText(err, 'auth.login_failed'))
    } finally {
      setLoading(false)
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setCanResend(false)
    setSsoRequired(false)
    setResentNote(null)
    setLoading(true)
    try {
      const res = await authLogin(email, password)
      if (isLoginSession(res)) finishLogin(res)
      else if (isMfaChallenge(res)) setMfaStep({ kind: 'challenge', token: res.mfa_token, justEnrolled: false })
      else if (isMfaEnrollment(res)) setMfaStep({ kind: 'enroll', token: res.enrollment_token })
    } catch (err: unknown) {
      setError(authErrorText(err, 'auth.login_failed'))
      setCanResend(isApiError(err) && VERIFICATION_CODES.includes(err.code))
      setSsoRequired(isApiError(err) && err.code === 'sso_required')
    } finally {
      setLoading(false)
    }
  }

  // The field's resting/hover/focus/autofill styling lives in globals.css
  // under `.auth-field input.auth-input`, NOT in inline onFocus/onBlur
  // handlers. It used to be inline, and that was the contrast bug: an inline
  // `box-shadow` outranks any stylesheet rule, so focusing a field wiped the
  // `0 0 0 1000px … inset` that masks Chrome's autofill background, leaving
  // the autofill text colour stranded on a white field. Styling states in CSS
  // keeps the browser's own pseudo-classes in the same cascade as ours.

  return (
    <div className="auth-shell">
      <div style={{ width: '100%', maxWidth: 380 }}>

        {/* No card: on a plain white column the form itself is the surface. */}
        <div className="auth-enter" style={{ animation: 'auth-fade-in 0.5s ease-out both' }}>

          {!mfaStep && (
            <div style={{ marginBottom: 30 }}>
              <h1 style={{ fontFamily: 'var(--font-brand), system-ui, sans-serif', fontSize: 24, fontWeight: 600, color: 'var(--a-ink)', margin: '0 0 10px', letterSpacing: '-0.03em', lineHeight: 1.12 }}>
                {t('auth.login_title')}
              </h1>
              <p style={{ fontSize: 14, color: 'var(--a-muted)', margin: 0, lineHeight: 1.5 }}>
                {t('auth.login_subtitle')}
              </p>
            </div>
          )}

          {mfaStep && (
            <MfaLoginStep
              step={mfaStep}
              onSession={finishLogin}
              onBack={() => { setMfaStep(null); setPassword('') }}
              onSignInAgain={signInAgainAfterEnroll}
            />
          )}
          {!mfaStep && <SocialButtons intent="login" />}

          {!mfaStep && error && (
            <div role="alert" style={{
              display: 'flex', flexDirection: 'column', gap: 8,
              padding: '10px 12px', borderRadius: 10, marginBottom: 20,
              background: 'rgba(185,74,74,0.04)', border: '1px solid rgba(185,74,74,0.15)',
              fontSize: 13, color: '#B94A4A',
              animation: 'auth-fade-up 0.35s ease-out both',
            }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <AlertTriangle size={13} style={{ flexShrink: 0 }} />
                {error}
              </div>
              {canResend && !resentNote && (
                <button
                  type="button" onClick={handleResend} disabled={resending || !email}
                  style={{
                    all: 'unset', alignSelf: 'flex-start', cursor: resending ? 'wait' : 'pointer',
                    display: 'flex', alignItems: 'center', gap: 6,
                    fontSize: 12.5, fontWeight: 600, color: 'var(--a-ink)',
                    textDecoration: 'underline', textUnderlineOffset: 3,
                  }}
                >
                  <MailCheck size={13} />
                  {resending ? t('auth.resend_verification_sending') : t('auth.resend_verification')}
                </button>
              )}
              {resentNote && (
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12.5, color: 'var(--a-muted)' }}>
                  <MailCheck size={13} style={{ flexShrink: 0 }} />
                  {resentNote}
                </div>
              )}
            </div>
          )}

          {!mfaStep && (
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>

            <div className="auth-field auth-enter" style={{ animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.10s both' }}>
              <label htmlFor="login-email" style={{ display: 'block', marginBottom: 7, fontSize: 12, fontWeight: 500, color: 'var(--a-muted)' }}>
                {t('auth.email_label')}
              </label>
              <input
                id="login-email" name="email" className="auth-input"
                type="email" value={email} required autoComplete="email"
                onChange={e => setEmail(e.target.value)}
                placeholder={t('auth.ph_email')}
              />
            </div>

            <div className="auth-field auth-enter" style={{ animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.16s both' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 7 }}>
                <label htmlFor="login-password" style={{ fontSize: 12, fontWeight: 500, color: 'var(--a-muted)' }}>{t('auth.password_label')}</label>
                <Link href="/forgot-password" className="auth-link" style={{ fontSize: 12, color: 'var(--a-muted)', textDecoration: 'none' }}>
                  {t('auth.forgot_password')}
                </Link>
              </div>
              <div style={{ position: 'relative' }}>
                <input
                  id="login-password" name="password" className="auth-input auth-input-affix"
                  type={showPw ? 'text' : 'password'} value={password} required autoComplete="current-password"
                  onChange={e => setPassword(e.target.value)}
                  placeholder="••••••••"
                />
                <button
                  type="button" onClick={() => setShowPw(v => !v)}
                  aria-label={showPw ? t('auth.hide_password') : t('auth.show_password')}
                  className="auth-eye"
                >
                  {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>

            <button
              type="submit" disabled={loading} className="auth-submit auth-enter"
              style={{
                width: '100%', padding: '12.5px', borderRadius: 11, border: 'none',
                background: loading ? 'var(--a-dim)' : 'var(--a-cta-bg)',
                color: 'var(--a-cta-fg)', fontSize: 14, fontWeight: 600,
                cursor: loading ? 'not-allowed' : 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
                marginTop: 8,
                transition: 'transform 0.22s cubic-bezier(0.16,1,0.3,1), box-shadow 0.22s ease',
                animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.22s both',
              }}
            >
              {loading ? (
                <>
                  <span style={{
                    width: 13, height: 13, border: '2px solid rgba(255,255,255,0.35)',
                    borderTopColor: '#fff', borderRadius: '50%',
                    animation: 'spin 0.7s linear infinite', display: 'inline-block',
                  }} />
                  {t('auth.signing_in')}
                </>
              ) : (
                t('auth.login_title')
              )}
            </button>
          </form>
          )}

          {!mfaStep && <SsoSignIn initialEmail={email} forceOpen={ssoRequired} />}
        </div>

        {!mfaStep && <p className="auth-enter" style={{
          marginTop: 28, fontSize: 13.5, color: 'var(--a-muted)',
          animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.3s both',
        }}>
          {t('auth.no_account')}{' '}
          <Link href="/signup" className="auth-link" style={{ color: 'var(--a-ink)', textDecoration: 'none', fontWeight: 600 }}>
            {t('auth.request_access')}
          </Link>
        </p>}
      </div>
    </div>
  )
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginPageContent />
    </Suspense>
  )
}
