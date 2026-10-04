'use client'
import { Suspense, useState } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { authSignup } from '@/lib/api'
import { Eye, EyeOff, AlertTriangle, CheckCircle2 } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useAuthErrorText } from '@/hooks/useAuthErrorText'
import TermsSentence from '@/components/legal/TermsSentence'
import PhoneInput from '@/components/ui/PhoneInput'
import { SocialButtons, socialErrorText } from '@/components/auth/SocialButtons'

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
  const color = score < 2 ? '#B94A4A' : score < 4 ? '#A8701C' : '#2F855A'

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
            color: ok ? '#2F855A' : 'var(--a-dim)', transition: 'color 0.25s ease',
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
  // Unticked by default, and never ticked for the person: acceptance only
  // counts if they gave it. The backend refuses a signup without it.
  const [accepted, setAccepted] = useState(false)
  const [termsMissing, setTermsMissing] = useState(false)
  const [showPw,  setShowPw]  = useState(false)
  const [loading, setLoading] = useState(false)
  // A refused provider sign-in comes back as `?oauth_error=<code>`.
  const oauthError = searchParams.get('oauth_error')
  const [error,   setError]   = useState<string | null>(
    oauthError ? socialErrorText(t, oauthError) : null,
  )
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
    if (!accepted) {
      // Said next to the box, not in the banner at the top of the form: on a
      // phone that banner is a screen away from the button just tapped.
      setTermsMissing(true)
      document.getElementById('signup-terms')?.focus()
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
        accept_terms:    accepted,
      })
      setVerifyUrl(res.email_sent ? null : (res.verify_url ?? null))
      setDone(true)
    } catch (err: unknown) {
      setError(authErrorText(err, 'auth.signup_failed'))
    } finally {
      setLoading(false)
    }
  }

  // Resting, hover and focus for these fields live in globals.css as
  // `.auth-field input.auth-input`, shared with /login. They used to be an
  // inline style plus onFocus/onBlur handlers that set boxShadow directly —
  // and an inline style outranks every stylesheet rule, including the
  // `:-webkit-autofill` mask that paints the field background. Focusing an
  // autofilled field therefore erased its own background and left Chrome's
  // wash showing through. Letting the cascade decide is the fix.

  // No card: the split layout's white column is the surface (see /login).
  const cardStyle: React.CSSProperties = {}

  return (
    <div className="auth-shell">
      <div style={{ width: '100%', maxWidth: 440 }}>

        {done ? (
          <div className="auth-enter" style={{ ...cardStyle, animation: 'auth-fade-up 0.7s cubic-bezier(0.16,1,0.3,1) both' }}>
            <div style={{
              width: 42, height: 42, borderRadius: '50%', background: 'var(--a-cta-bg)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 20,
            }}>
              <CheckCircle2 size={21} color="var(--a-cta-fg)" strokeWidth={2} />
            </div>
            <h1 style={{ fontSize: 23, fontWeight: 600, color: 'var(--a-ink)', margin: '0 0 9px', letterSpacing: '-0.03em' }}>
              {verifyUrl ? t('auth.verify_link_onscreen_title') : t('auth.check_email_title')}
            </h1>
            {verifyUrl ? (
              <>
                <p style={{ fontSize: 14, color: 'var(--a-muted)', margin: '0 0 16px', lineHeight: 1.6 }}>
                  {t('auth.verify_link_onscreen_body')}
                </p>
                <a
                  href={verifyUrl}
                  style={{
                    display: 'block', wordBreak: 'break-all', marginBottom: 22,
                    padding: '11px 13px', borderRadius: 10,
                    background: 'rgba(9,9,11,0.035)', border: '1px solid rgba(9,9,11,0.09)',
                    fontSize: 12.5, color: 'var(--a-ink)', fontFamily: 'ui-monospace, monospace',
                    textDecoration: 'none', lineHeight: 1.45,
                  }}
                >
                  {verifyUrl}
                </a>
              </>
            ) : (
              <p style={{ fontSize: 14, color: 'var(--a-muted)', margin: '0 0 26px', lineHeight: 1.6 }}>
                {t('auth.check_email_sent_to')} <strong style={{ color: 'var(--a-ink)', fontWeight: 600 }}>{form.email}</strong>.
                {' '}{t('auth.check_email_click')}
                {wantsDemo && ` ${t('auth.check_email_demo_hint')}`}
              </p>
            )}
            <Link href={loginHref} className="auth-submit" style={{
              display: 'inline-flex', alignItems: 'center', padding: '11.5px 24px',
              background: 'var(--a-cta-bg)', color: 'var(--a-cta-fg)', borderRadius: 11,
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
              <h1 style={{ fontFamily: 'var(--font-brand), system-ui, sans-serif', fontSize: 30, fontWeight: 600, color: 'var(--a-ink)', margin: '0 0 10px', letterSpacing: '-0.038em', lineHeight: 1.08 }}>
                {t('auth.signup_title')}
              </h1>
              <p style={{ fontSize: 14.5, color: 'var(--a-muted)', margin: 0, lineHeight: 1.55 }}>
                {t('auth.signup_tagline')}
              </p>
            </div>

            <div className="auth-enter" style={{
              ...cardStyle,
              animation: 'auth-fade-up 0.7s cubic-bezier(0.16,1,0.3,1) 0.08s both',
            }}>
              {/* Social sign-in: renders nothing unless the installation
                  enabled a provider. Above the form, additive. */}
              <SocialButtons intent="signup" />

              {error && (
                <div style={{
                  display: 'flex', gap: 8, alignItems: 'center',
                  padding: '10px 12px', borderRadius: 10, marginBottom: 20,
                  background: 'rgba(185,74,74,0.04)', border: '1px solid rgba(185,74,74,0.15)',
                  fontSize: 13, color: '#B94A4A',
                  animation: 'auth-fade-up 0.35s ease-out both',
                }}>
                  <AlertTriangle size={13} style={{ flexShrink: 0 }} />
                  {error}
                </div>
              )}

              <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div className="auth-row-2" style={{
                  display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12,
                  animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.14s both',
                }}>
                  <div className="auth-field">
                    <label htmlFor="signup-full-name" style={{ fontSize: 12, fontWeight: 500, color: 'var(--a-muted)', display: 'block', marginBottom: 6 }}>
                      {t('auth.full_name_label')}
                    </label>
                    <input
                      id="signup-full-name" name="full_name"
                      type="text" value={form.full_name}
                      onChange={e => set('full_name', e.target.value)}
                      placeholder="Jane Smith"
                      className="auth-input"
                    />
                  </div>
                  <div className="auth-field">
                    <label htmlFor="signup-company" style={{ fontSize: 12, fontWeight: 500, color: 'var(--a-muted)', display: 'block', marginBottom: 6 }}>
                      {t('auth.company_label')} <span style={{ color: '#B94A4A' }}>*</span>
                    </label>
                    <input
                      id="signup-company" name="tenant_name"
                      type="text" value={form.tenant_name} required
                      onChange={e => set('tenant_name', e.target.value)}
                      placeholder="Acme Corp"
                      className="auth-input"
                    />
                  </div>
                </div>

                <div className="auth-field" style={{ animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.19s both' }}>
                  <label htmlFor="signup-email" style={{ fontSize: 12, fontWeight: 500, color: 'var(--a-muted)', display: 'block', marginBottom: 6 }}>
                    {t('auth.email_label')} <span style={{ color: '#B94A4A' }}>*</span>
                  </label>
                  <input
                    id="signup-email" name="email"
                    type="email" value={form.email} required
                    onChange={e => set('email', e.target.value)}
                    placeholder="you@company.com"
                    className="auth-input"
                  />
                </div>

                <div className="auth-field" style={{ animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.215s both' }}>
                  <label htmlFor="signup-whatsapp" style={{ fontSize: 12, fontWeight: 500, color: 'var(--a-muted)', display: 'block', marginBottom: 6 }}>
                    {t('auth.whatsapp_label')} <span style={{ color: '#B94A4A' }}>*</span>
                  </label>
                  <PhoneInput
                    id="signup-whatsapp" name="whatsapp_number" variant="auth"
                    value={form.whatsapp_number} required
                    onChange={v => set('whatsapp_number', v)}
                  />
                  <p style={{ margin: '6px 0 0', fontSize: 11.5, color: 'var(--a-muted)', lineHeight: 1.45 }}>
                    {t('auth.whatsapp_hint')}
                  </p>
                </div>

                <div className="auth-field" style={{ animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.24s both' }}>
                  <label htmlFor="signup-password" style={{ fontSize: 12, fontWeight: 500, color: 'var(--a-muted)', display: 'block', marginBottom: 6 }}>
                    {t('auth.password_label')} <span style={{ color: '#B94A4A' }}>*</span>
                  </label>
                  <div style={{ position: 'relative' }}>
                    <input
                      id="signup-password" name="password"
                      type={showPw ? 'text' : 'password'} value={form.password} required
                      onChange={e => set('password', e.target.value)}
                      placeholder={t('auth.password_placeholder')}
                      className="auth-input auth-input-affix"
                    />
                    <button
                      type="button" onClick={() => setShowPw(v => !v)}
                      aria-label={showPw ? t('auth.hide_password') : t('auth.show_password')}
                      className="auth-eye"
                    >
                      {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                  <PasswordStrength password={form.password} />
                </div>

                <label
                  htmlFor="signup-terms"
                  style={{
                    display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer',
                    fontSize: 13, color: 'var(--a-muted)', lineHeight: 1.5, minHeight: 44,
                    animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.265s both',
                  }}
                >
                  <input
                    id="signup-terms" name="accept_terms" type="checkbox"
                    checked={accepted}
                    onChange={e => { setAccepted(e.target.checked); if (e.target.checked) setTermsMissing(false) }}
                    aria-required="true"
                    aria-invalid={termsMissing || undefined}
                    aria-describedby={termsMissing ? 'signup-terms-error' : undefined}
                    style={{ width: 18, height: 18, marginTop: 1, flexShrink: 0, accentColor: '#0F766E', cursor: 'pointer' }}
                  />
                  <span>
                    <TermsSentence
                      templateKey="auth.terms_accept"
                      linkStyle={{ color: 'var(--a-ink)', fontWeight: 600, textDecoration: 'underline', textUnderlineOffset: 3 }}
                    />
                  </span>
                </label>
                {termsMissing && (
                  <p id="signup-terms-error" role="alert" style={{
                    display: 'flex', gap: 6, alignItems: 'flex-start', margin: '-8px 0 0',
                    fontSize: 12.5, color: '#B94A4A', lineHeight: 1.45,
                  }}>
                    <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2 }} aria-hidden="true" />
                    {t('errors.terms_not_accepted')}
                  </p>
                )}

                <button
                  type="submit" disabled={loading} className="auth-submit"
                  style={{
                    width: '100%', padding: '12.5px', borderRadius: 11, border: 'none',
                    background: loading ? 'var(--a-dim)' : 'var(--a-cta-bg)', color: 'var(--a-cta-fg)',
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
              marginTop: 20, marginLeft: 2, fontSize: 13, color: 'var(--a-dim)',
              animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.35s both',
            }}>
              {t('auth.have_account')}{' '}
              <Link href="/login" className="auth-link" style={{ color: 'var(--a-ink)', textDecoration: 'none', fontWeight: 600 }}>
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
