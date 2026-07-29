'use client'
import { Suspense, useState, useEffect } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { authLogin, authResendVerification, isApiError } from '@/lib/api'
import { setAuth, isAuthenticated } from '@/lib/auth'
import { Eye, EyeOff, AlertTriangle, ArrowRight, MailCheck } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useAuthErrorText } from '@/hooks/useAuthErrorText'

// Composition: the card sits left of centre and a touch above the optical
// midline, leaving the open right-hand field for the beam to sweep into and
// the lighthouse to occupy. The ambient canvas, wordmark and fixed shell all
// come from (auth)/layout.tsx — this file renders only the card.

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
  const [error,    setError]    = useState<string | null>(null)
  // A login refused for a verification reason is the one error the user cannot
  // fix by retyping something, so it gets an action instead of just a message.
  const [canResend,  setCanResend]  = useState(false)
  const [resending,  setResending]  = useState(false)
  const [resentNote, setResentNote] = useState<string | null>(null)

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

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setCanResend(false)
    setResentNote(null)
    setLoading(true)
    try {
      const res = await authLogin(email, password)
      setAuth(res.access_token, res.refresh_token, {
        id:        res.user.id,
        email:     res.user.email,
        full_name: res.user.full_name,
        role:      res.user.role,
        tenant_id: res.user.tenant_id,
      })
      router.replace(destination)
    } catch (err: unknown) {
      setError(authErrorText(err, 'auth.login_failed'))
      setCanResend(isApiError(err) && VERIFICATION_CODES.includes(err.code))
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
    <div className="auth-shell" style={{
      height: '100%', display: 'flex', alignItems: 'center',
      // Deliberately asymmetric on desktop: the card sits left of centre so the
      // illustrated half of the layout reads as the other half of a composition.
      // On a phone there is no other half, so globals.css evens this out — an
      // off-centre card on a 390px screen just looks like a mistake.
      paddingLeft: "clamp(28px, 10vw, 150px)", paddingRight: "clamp(28px, 6vw, 64px)",
      paddingBottom: '3vh',   // optical centring — sits a touch above true middle
    }}>
      <div style={{ width: '100%', maxWidth: 392 }}>

        <div className="auth-enter" style={{
          // Solid white with a real border. A translucent card over a pale
          // background just looks washed out — the crispness IS the premium
          // signal here, not the transparency.
          background: '#fff',
          border: '1px solid rgba(9,9,11,0.09)',
          borderRadius: 20,
          padding: '38px 36px',
          // Contact hairline, close ambient pool, wide soft cast.
          boxShadow:
            '0 1px 2px rgba(9,9,11,0.04),' +
            '0 12px 28px -14px rgba(9,9,11,0.14),' +
            '0 44px 80px -36px rgba(9,9,11,0.16)',
          animation: 'auth-fade-up 0.7s cubic-bezier(0.16, 1, 0.3, 1) both',
        }}>

          <div style={{ marginBottom: 30 }}>
            <h1 style={{ fontSize: 26, fontWeight: 600, color: '#0a0a0a', margin: '0 0 9px', letterSpacing: '-0.032em', lineHeight: 1.15 }}>
              {t('auth.login_title')}
            </h1>
            <p style={{ fontSize: 14, color: '#71717a', margin: 0, lineHeight: 1.5 }}>
              {t('auth.login_subtitle')}
            </p>
          </div>

          {error && (
            <div style={{
              display: 'flex', flexDirection: 'column', gap: 8,
              padding: '10px 12px', borderRadius: 10, marginBottom: 20,
              background: 'rgba(220,38,38,0.04)', border: '1px solid rgba(220,38,38,0.15)',
              fontSize: 13, color: '#dc2626',
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
                    fontSize: 12.5, fontWeight: 600, color: '#0a0a0a',
                    textDecoration: 'underline', textUnderlineOffset: 3,
                  }}
                >
                  <MailCheck size={13} />
                  {resending ? t('auth.resend_verification_sending') : t('auth.resend_verification')}
                </button>
              )}
              {resentNote && (
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12.5, color: '#52525b' }}>
                  <MailCheck size={13} style={{ flexShrink: 0 }} />
                  {resentNote}
                </div>
              )}
            </div>
          )}

          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>

            <div className="auth-field auth-enter" style={{ animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.10s both' }}>
              <label htmlFor="login-email" style={{ display: 'block', marginBottom: 7, fontSize: 12, fontWeight: 500, color: '#52525b' }}>
                {t('auth.email_label')}
              </label>
              <input
                id="login-email" name="email" className="auth-input"
                type="email" value={email} required autoComplete="email"
                onChange={e => setEmail(e.target.value)}
                placeholder="you@company.com"
              />
            </div>

            <div className="auth-field auth-enter" style={{ animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.16s both' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 7 }}>
                <label htmlFor="login-password" style={{ fontSize: 12, fontWeight: 500, color: '#52525b' }}>{t('auth.password_label')}</label>
                <Link href="/forgot-password" className="auth-link" style={{ fontSize: 12, color: '#71717a', textDecoration: 'none' }}>
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
                background: loading ? '#a1a1aa' : '#0a0a0a',
                color: '#fff', fontSize: 14, fontWeight: 600,
                cursor: loading ? 'not-allowed' : 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
                marginTop: 8,
                transition: 'transform 0.22s cubic-bezier(0.16,1,0.3,1), box-shadow 0.22s ease',
                animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.22s both',
              }}
              onMouseEnter={e => { if (!loading) { const b = e.currentTarget as HTMLButtonElement; b.style.transform = 'translateY(-1.5px)'; b.style.boxShadow = '0 10px 22px -10px rgba(9,9,11,0.45)' } }}
              onMouseLeave={e => { const b = e.currentTarget as HTMLButtonElement; b.style.transform = 'translateY(0)'; b.style.boxShadow = 'none' }}
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
                <>{t('auth.login_title')} <ArrowRight size={13} /></>
              )}
            </button>
          </form>
        </div>

        <p className="auth-enter" style={{
          marginTop: 22, marginLeft: 2, fontSize: 13, color: '#a1a1aa',
          animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.3s both',
        }}>
          {t('auth.no_account')}{' '}
          <Link href="/signup" className="auth-link" style={{ color: '#0a0a0a', textDecoration: 'none', fontWeight: 600 }}>
            {t('auth.request_access')}
          </Link>
        </p>
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
