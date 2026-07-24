'use client'
import { Suspense, useState, useEffect } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { authLogin } from '@/lib/api'
import { setAuth, isAuthenticated } from '@/lib/auth'
import { Eye, EyeOff, AlertTriangle, ArrowRight } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'

// Composition: the card sits left of centre and a touch above the optical
// midline, leaving the open right-hand field for the beam to sweep into and
// the lighthouse to occupy. The ambient canvas, wordmark and fixed shell all
// come from (auth)/layout.tsx — this file renders only the card.

function LoginPageContent() {
  const { t } = useLanguage()
  const router = useRouter()
  const searchParams = useSearchParams()
  const wantsDemo = searchParams.get('demo') === '1'
  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [showPw,   setShowPw]   = useState(false)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)

  // `/login` is a public path, so AuthGuard lets it render even with a live
  // session — a user coming back to a still-valid tab would otherwise be shown
  // a login form for an account they're already in. Send them to the same
  // destination a fresh login would (feature 1.2: post-login lands on /hoy).
  const destination = wantsDemo ? '/quick-start?demo=1' : '/hoy'
  useEffect(() => {
    if (isAuthenticated()) router.replace(destination)
  }, [router, destination])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
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
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box',
    padding: '12px 14px',
    background: 'rgba(250,250,250,0.9)', border: '1px solid rgba(9,9,11,0.085)', borderRadius: 11,
    color: '#0a0a0a', fontSize: 14, outline: 'none',
    transition: 'border-color 0.22s ease, box-shadow 0.22s ease, background 0.22s ease, transform 0.22s ease',
  }
  const focusIn = (e: React.FocusEvent<HTMLInputElement>) => {
    e.target.style.borderColor = 'rgba(9,9,11,0.55)'
    e.target.style.background  = '#fff'
    e.target.style.transform   = 'translateY(-1px)'
    e.target.style.boxShadow   = '0 0 0 3.5px rgba(9,9,11,0.045), 0 6px 14px -8px rgba(9,9,11,0.22)'
  }
  const focusOut = (e: React.FocusEvent<HTMLInputElement>) => {
    e.target.style.borderColor = 'rgba(9,9,11,0.085)'
    e.target.style.background  = 'rgba(250,250,250,0.9)'
    e.target.style.transform   = 'translateY(0)'
    e.target.style.boxShadow   = 'none'
  }

  return (
    <div style={{
      height: '100%', display: 'flex', alignItems: 'center',
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
              display: 'flex', gap: 8, alignItems: 'center',
              padding: '10px 12px', borderRadius: 10, marginBottom: 20,
              background: 'rgba(220,38,38,0.04)', border: '1px solid rgba(220,38,38,0.15)',
              fontSize: 13, color: '#dc2626',
              animation: 'auth-fade-up 0.35s ease-out both',
            }}>
              <AlertTriangle size={13} style={{ flexShrink: 0 }} />
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>

            <div className="auth-field auth-enter" style={{ animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.10s both' }}>
              <label htmlFor="login-email" style={{ display: 'block', marginBottom: 7, fontSize: 12, fontWeight: 500, color: '#52525b' }}>
                {t('auth.email_label')}
              </label>
              <input
                id="login-email" name="email"
                type="email" value={email} required autoComplete="email"
                onChange={e => setEmail(e.target.value)}
                placeholder="you@company.com"
                style={inputStyle} onFocus={focusIn} onBlur={focusOut}
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
                  id="login-password" name="password"
                  type={showPw ? 'text' : 'password'} value={password} required autoComplete="current-password"
                  onChange={e => setPassword(e.target.value)}
                  placeholder="••••••••"
                  style={{ ...inputStyle, padding: '12px 40px 12px 14px' }}
                  onFocus={focusIn} onBlur={focusOut}
                />
                <button
                  type="button" onClick={() => setShowPw(v => !v)}
                  aria-label={showPw ? t('auth.hide_password') : t('auth.show_password')}
                  style={{
                    all: 'unset', position: 'absolute', right: 12, top: '50%',
                    transform: 'translateY(-50%)', cursor: 'pointer', color: '#a1a1aa',
                    display: 'flex', alignItems: 'center', transition: 'color 0.18s ease',
                  }}
                  onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.color = '#0a0a0a' }}
                  onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.color = '#a1a1aa' }}
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
