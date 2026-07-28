'use client'
import { Suspense, useState } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { authSignup } from '@/lib/api'
import { Eye, EyeOff, AlertTriangle, CheckCircle2 } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useAuthErrorText } from '@/hooks/useAuthErrorText'

// Composition, deliberately NOT a mirror of /login: this screen carries more
// fields, so the heading is lifted OUT of the card and set as an editorial
// block above it. That gives the taller form a lighter container, and gives
// the two screens a different rhythm while sharing one visual language.
// Ambient canvas, wordmark and fixed shell come from (auth)/layout.tsx.

function PasswordStrength({ password }: { password: string }) {
  const { t } = useLanguage()
  const checks = [
    { label: t('auth.pw_check_length'),    ok: password.length >= 8 },
    { label: t('auth.pw_check_uppercase'), ok: /[A-Z]/.test(password) },
    { label: t('auth.pw_check_number'),    ok: /\d/.test(password) },
    { label: t('auth.pw_check_special'),   ok: /[^A-Za-z0-9]/.test(password) },
  ]
  const score = checks.filter(c => c.ok).length
  const color = score < 2 ? '#dc2626' : score < 4 ? '#d97706' : '#16a34a'

  if (!password) return null
  return (
    <div style={{ marginTop: 11 }}>
      <div style={{ display: 'flex', gap: 3, marginBottom: 9 }}>
        {[0, 1, 2, 3].map(i => (
          <div key={i} style={{
            flex: 1, height: 2.5, borderRadius: 2,
            background: i < score ? color : 'rgba(9,9,11,0.07)',
            transition: 'background 0.35s cubic-bezier(0.16,1,0.3,1)',
          }} />
        ))}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px 14px' }}>
        {checks.map(({ label, ok }) => (
          <div key={label} style={{
            display: 'flex', gap: 5, alignItems: 'center', fontSize: 11,
            color: ok ? '#16a34a' : '#a1a1aa', transition: 'color 0.25s ease',
          }}>
            <CheckCircle2 size={10} />
            {label}
          </div>
        ))}
      </div>
    </div>
  )
}

function SignupPageContent() {
  const { t } = useLanguage()
  // Pydantic rejects a malformed email with its own English prose.
  // `authErrorText` rebuilds the sentence from the machine-readable `type` +
  // field, so the user reads Spanish instead of "value is not a valid email
  // address: …".
  const authErrorText = useAuthErrorText()
  const searchParams = useSearchParams()
  const wantsDemo = searchParams.get('demo') === '1'
  const loginHref = wantsDemo ? '/login?demo=1' : '/login'

  const [form, setForm] = useState({
    email: '', password: '', full_name: '', tenant_name: '', whatsapp_number: '',
  })
  const [showPw,  setShowPw]  = useState(false)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  const [done,    setDone]    = useState(false)
  // Set only when the backend tells us the verification mail did NOT leave.
  // Then the link goes on screen — sending someone to check an inbox we know
  // received nothing is how accounts end up permanently unactivatable.
  const [verifyUrl, setVerifyUrl] = useState<string | null>(null)

  function set(field: string, value: string) {
    setForm(f => ({ ...f, [field]: value }))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    const phone = form.whatsapp_number.trim()
    // Same E.164 rule the backend enforces — catch it before the round trip.
    if (!/^\+[1-9]\d{7,14}$/.test(phone)) {
      setError(t('auth.whatsapp_invalid'))
      return
    }
    setLoading(true)
    try {
      const res = await authSignup({
        email:           form.email,
        password:        form.password,
        full_name:       form.full_name || undefined,
        tenant_name:     form.tenant_name,
        whatsapp_number: phone,
      })
      setVerifyUrl(res.email_sent ? null : (res.verify_url ?? null))
      setDone(true)
    } catch (err: unknown) {
      setError(authErrorText(err, 'auth.signup_failed'))
    } finally {
      setLoading(false)
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box', padding: '11px 13px',
    background: 'rgba(250,250,250,0.9)', border: '1px solid rgba(9,9,11,0.085)', borderRadius: 11,
    color: '#0a0a0a', fontSize: 13.5, outline: 'none',
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

  const cardStyle: React.CSSProperties = {
    background: '#fff',
    border: '1px solid rgba(9,9,11,0.09)',
    borderRadius: 20,
    padding: '32px 36px',
    boxShadow:
      '0 1px 2px rgba(9,9,11,0.04),' +
      '0 12px 28px -14px rgba(9,9,11,0.14),' +
      '0 44px 80px -36px rgba(9,9,11,0.16)',
  }

  return (
    <div style={{
      height: '100%', display: 'flex', alignItems: 'center',
      paddingLeft: 'clamp(28px, 13vw, 200px)', paddingRight: 'clamp(28px, 6vw, 64px)',
      paddingBottom: '2vh',
    }}>
      <div style={{ width: '100%', maxWidth: 452 }}>

        {done ? (
          <div className="auth-enter" style={{ ...cardStyle, animation: 'auth-fade-up 0.7s cubic-bezier(0.16,1,0.3,1) both' }}>
            <div style={{
              width: 42, height: 42, borderRadius: '50%', background: '#0a0a0a',
              display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 20,
            }}>
              <CheckCircle2 size={21} color="#fff" strokeWidth={2} />
            </div>
            <h1 style={{ fontSize: 23, fontWeight: 600, color: '#0a0a0a', margin: '0 0 9px', letterSpacing: '-0.03em' }}>
              {verifyUrl ? t('auth.verify_link_onscreen_title') : t('auth.check_email_title')}
            </h1>
            {verifyUrl ? (
              <>
                <p style={{ fontSize: 14, color: '#71717a', margin: '0 0 16px', lineHeight: 1.6 }}>
                  {t('auth.verify_link_onscreen_body')}
                </p>
                <a
                  href={verifyUrl}
                  style={{
                    display: 'block', wordBreak: 'break-all', marginBottom: 22,
                    padding: '11px 13px', borderRadius: 10,
                    background: 'rgba(9,9,11,0.035)', border: '1px solid rgba(9,9,11,0.09)',
                    fontSize: 12.5, color: '#0a0a0a', fontFamily: 'ui-monospace, monospace',
                    textDecoration: 'none', lineHeight: 1.45,
                  }}
                >
                  {verifyUrl}
                </a>
              </>
            ) : (
              <p style={{ fontSize: 14, color: '#71717a', margin: '0 0 26px', lineHeight: 1.6 }}>
                {t('auth.check_email_sent_to')} <strong style={{ color: '#0a0a0a', fontWeight: 600 }}>{form.email}</strong>.
                {' '}{t('auth.check_email_click')}
                {wantsDemo && ` ${t('auth.check_email_demo_hint')}`}
              </p>
            )}
            <Link href={loginHref} className="auth-submit" style={{
              display: 'inline-flex', alignItems: 'center', padding: '11.5px 24px',
              background: '#0a0a0a', color: '#fff', borderRadius: 11,
              fontSize: 13.5, fontWeight: 600, textDecoration: 'none',
            }}>
              {t('auth.go_to_login')}
            </Link>
          </div>
        ) : (
          <>
            {/* Editorial heading — outside the card, unlike /login */}
            <div className="auth-enter" style={{
              marginBottom: 26, paddingLeft: 2,
              animation: 'auth-fade-up 0.7s cubic-bezier(0.16,1,0.3,1) both',
            }}>
              <h1 style={{ fontSize: 34, fontWeight: 600, color: '#0a0a0a', margin: '0 0 10px', letterSpacing: '-0.038em', lineHeight: 1.08 }}>
                {t('auth.signup_title')}
              </h1>
              <p style={{ fontSize: 14.5, color: '#71717a', margin: 0, lineHeight: 1.55 }}>
                Faro — Inventario Inteligente
              </p>
            </div>

            <div className="auth-enter" style={{
              ...cardStyle,
              animation: 'auth-fade-up 0.7s cubic-bezier(0.16,1,0.3,1) 0.08s both',
            }}>
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

              <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div style={{
                  display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12,
                  animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.14s both',
                }}>
                  <div className="auth-field">
                    <label htmlFor="signup-full-name" style={{ fontSize: 12, fontWeight: 500, color: '#52525b', display: 'block', marginBottom: 6 }}>
                      {t('auth.full_name_label')}
                    </label>
                    <input
                      id="signup-full-name" name="full_name"
                      type="text" value={form.full_name}
                      onChange={e => set('full_name', e.target.value)}
                      placeholder="Jane Smith"
                      style={inputStyle} onFocus={focusIn} onBlur={focusOut}
                    />
                  </div>
                  <div className="auth-field">
                    <label htmlFor="signup-company" style={{ fontSize: 12, fontWeight: 500, color: '#52525b', display: 'block', marginBottom: 6 }}>
                      {t('auth.company_label')} <span style={{ color: '#dc2626' }}>*</span>
                    </label>
                    <input
                      id="signup-company" name="tenant_name"
                      type="text" value={form.tenant_name} required
                      onChange={e => set('tenant_name', e.target.value)}
                      placeholder="Acme Corp"
                      style={inputStyle} onFocus={focusIn} onBlur={focusOut}
                    />
                  </div>
                </div>

                <div className="auth-field" style={{ animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.19s both' }}>
                  <label htmlFor="signup-email" style={{ fontSize: 12, fontWeight: 500, color: '#52525b', display: 'block', marginBottom: 6 }}>
                    {t('auth.email_label')} <span style={{ color: '#dc2626' }}>*</span>
                  </label>
                  <input
                    id="signup-email" name="email"
                    type="email" value={form.email} required
                    onChange={e => set('email', e.target.value)}
                    placeholder="you@company.com"
                    style={inputStyle} onFocus={focusIn} onBlur={focusOut}
                  />
                </div>

                <div className="auth-field" style={{ animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.215s both' }}>
                  <label htmlFor="signup-whatsapp" style={{ fontSize: 12, fontWeight: 500, color: '#52525b', display: 'block', marginBottom: 6 }}>
                    {t('auth.whatsapp_label')} <span style={{ color: '#dc2626' }}>*</span>
                  </label>
                  <input
                    id="signup-whatsapp" name="whatsapp_number"
                    type="tel" value={form.whatsapp_number} required
                    onChange={e => set('whatsapp_number', e.target.value)}
                    placeholder="+50688887777"
                    style={inputStyle} onFocus={focusIn} onBlur={focusOut}
                  />
                  <p style={{ margin: '6px 0 0', fontSize: 11.5, color: '#71717a', lineHeight: 1.45 }}>
                    {t('auth.whatsapp_hint')}
                  </p>
                </div>

                <div className="auth-field" style={{ animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.24s both' }}>
                  <label htmlFor="signup-password" style={{ fontSize: 12, fontWeight: 500, color: '#52525b', display: 'block', marginBottom: 6 }}>
                    {t('auth.password_label')} <span style={{ color: '#dc2626' }}>*</span>
                  </label>
                  <div style={{ position: 'relative' }}>
                    <input
                      id="signup-password" name="password"
                      type={showPw ? 'text' : 'password'} value={form.password} required
                      onChange={e => set('password', e.target.value)}
                      placeholder={t('auth.password_placeholder')}
                      style={{ ...inputStyle, paddingRight: 38 }}
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
                  <PasswordStrength password={form.password} />
                </div>

                <button
                  type="submit" disabled={loading} className="auth-submit"
                  style={{
                    width: '100%', padding: '12.5px', borderRadius: 11, border: 'none',
                    background: loading ? '#a1a1aa' : '#0a0a0a', color: '#fff',
                    fontSize: 14, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer',
                    marginTop: 6,
                    transition: 'transform 0.22s cubic-bezier(0.16,1,0.3,1), box-shadow 0.22s ease',
                    animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.29s both',
                  }}
                  onMouseEnter={e => { if (!loading) { const b = e.currentTarget as HTMLButtonElement; b.style.transform = 'translateY(-1.5px)'; b.style.boxShadow = '0 10px 22px -10px rgba(9,9,11,0.45)' } }}
                  onMouseLeave={e => { const b = e.currentTarget as HTMLButtonElement; b.style.transform = 'translateY(0)'; b.style.boxShadow = 'none' }}
                >
                  {loading ? t('auth.creating_workspace') : t('auth.create_workspace')}
                </button>
              </form>
            </div>

            <p className="auth-enter" style={{
              marginTop: 20, marginLeft: 2, fontSize: 13, color: '#a1a1aa',
              animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.35s both',
            }}>
              {t('auth.have_account')}{' '}
              <Link href="/login" className="auth-link" style={{ color: '#0a0a0a', textDecoration: 'none', fontWeight: 600 }}>
                {t('auth.login_title')}
              </Link>
            </p>
          </>
        )}
      </div>
    </div>
  )
}

export default function SignupPage() {
  return (
    <Suspense fallback={null}>
      <SignupPageContent />
    </Suspense>
  )
}
