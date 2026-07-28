'use client'
import { Suspense, useState } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import { authResetPassword } from '@/lib/api'
import { useLanguage } from '@/contexts/LanguageContext'
import { useAuthErrorText } from '@/hooks/useAuthErrorText'
import { Zap, Eye, EyeOff, CheckCircle2, AlertTriangle, Loader2 } from 'lucide-react'

function ResetPasswordForm() {
  const { t }    = useLanguage()
  const authErrorText = useAuthErrorText()
  const params   = useSearchParams()
  const router   = useRouter()
  const token    = params.get('token') ?? ''
  const [pw,     setPw]     = useState('')
  const [pw2,    setPw2]    = useState('')
  const [showPw, setShowPw] = useState(false)
  const [loading, setLoading] = useState(false)
  const [done,   setDone]   = useState(false)
  const [error,  setError]  = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (pw !== pw2) { setError(t('auth.pw_mismatch')); return }
    if (pw.length < 8) { setError(t('auth.pw_too_short')); return }
    setError(null)
    setLoading(true)
    try {
      await authResetPassword(token, pw)
      setDone(true)
      setTimeout(() => router.replace('/login'), 2500)
    } catch (err: unknown) {
      setError(authErrorText(err, 'auth.reset_failed'))
    } finally {
      setLoading(false)
    }
  }

  const inputStyle = {
    width: '100%', padding: '10px 12px', boxSizing: 'border-box' as const,
    background: 'var(--bg)', border: '1px solid var(--surface)', borderRadius: 8,
    color: 'var(--text)', fontSize: 13, outline: 'none',
  }

  return (
    <div style={{ width: '100%', maxWidth: 400, padding: '0 20px' }}>
      <div style={{ textAlign: 'center', marginBottom: 28 }}>
        <div style={{
          width: 44, height: 44, borderRadius: 11, margin: '0 auto 10px',
          background: 'linear-gradient(135deg, var(--accent), var(--accent))',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Zap size={20} color="#fff" strokeWidth={2.5} />
        </div>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--text)', margin: '0 0 4px' }}>
          {t('auth.set_new_password_title')}
        </h1>
      </div>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--surface)', borderRadius: 14, padding: '24px 28px' }}>
        {done ? (
          <div style={{ textAlign: 'center' }}>
            <CheckCircle2 size={32} color="#22c55e" style={{ margin: '0 auto 12px' }} />
            <p style={{ fontSize: 13, color: 'var(--muted)', margin: 0 }}>
              {t('auth.pw_updated')} {t('auth.redirecting_login')}
            </p>
          </div>
        ) : (
          <>
            {!token && (
              <div style={{ fontSize: 13, color: '#ef4444', marginBottom: 16 }}>
                {t('auth.reset_token_missing')}{' '}
                <Link href="/forgot-password" style={{ color: 'var(--accent)' }}>{t('auth.reset_request_new_link')}</Link>.
              </div>
            )}
            {error && (
              <div style={{
                display: 'flex', gap: 8, alignItems: 'center',
                padding: '10px 14px', borderRadius: 8, marginBottom: 16,
                background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)',
                fontSize: 13, color: '#ef4444',
              }}>
                <AlertTriangle size={14} /> {error}
              </div>
            )}
            <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div>
                <label htmlFor="reset-new-password" style={{ fontSize: 12, fontWeight: 500, color: 'var(--muted)', display: 'block', marginBottom: 6 }}>
                  {t('auth.new_password_label')}
                </label>
                <div style={{ position: 'relative' }}>
                  <input
                    id="reset-new-password" name="new_password"
                    type={showPw ? 'text' : 'password'} required value={pw}
                    onChange={e => setPw(e.target.value)}
                    placeholder={t('auth.password_placeholder')}
                    style={{ ...inputStyle, paddingRight: 36 }}
                    onFocus={e => (e.target.style.borderColor = 'var(--accent)')}
                    onBlur={e => (e.target.style.borderColor = 'var(--surface)')}
                  />
                  <button type="button" onClick={() => setShowPw(v => !v)} style={{
                    all: 'unset', position: 'absolute', right: 10, top: '50%',
                    transform: 'translateY(-50%)', cursor: 'pointer', color: 'var(--dim)',
                  }}>
                    {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </div>
              <div>
                <label htmlFor="reset-confirm-password" style={{ fontSize: 12, fontWeight: 500, color: 'var(--muted)', display: 'block', marginBottom: 6 }}>
                  {t('auth.confirm_password_label')}
                </label>
                <input
                  id="reset-confirm-password" name="confirm_password"
                  type={showPw ? 'text' : 'password'} required value={pw2}
                  onChange={e => setPw2(e.target.value)}
                  placeholder={t('auth.confirm_password_placeholder')}
                  style={{ ...inputStyle, borderColor: pw2 && pw !== pw2 ? '#ef4444' : 'var(--surface)' }}
                  onFocus={e => (e.target.style.borderColor = pw2 && pw !== pw2 ? '#ef4444' : 'var(--accent)')}
                  onBlur={e => (e.target.style.borderColor = pw2 && pw !== pw2 ? '#ef4444' : 'var(--surface)')}
                />
              </div>
              <button
                type="submit" disabled={loading || !token}
                style={{
                  width: '100%', padding: '11px', borderRadius: 8, border: 'none',
                  background: loading ? 'color-mix(in srgb, var(--accent) 70%, black)' : 'var(--accent)', color: '#fff',
                  fontSize: 13, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer', marginTop: 4,
                }}
              >
                {loading ? t('auth.saving') : t('auth.set_new_password_btn')}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  )
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={
      <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
        <Loader2 size={24} color="var(--accent)" style={{ animation: 'spin 1s linear infinite' }} />
      </div>
    }>
      <ResetPasswordForm />
    </Suspense>
  )
}
