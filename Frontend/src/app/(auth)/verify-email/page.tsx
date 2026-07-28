'use client'
import { Suspense, useEffect, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { authResendVerification, authVerifyEmail } from '@/lib/api'
import { useLanguage } from '@/contexts/LanguageContext'
import { useAuthErrorText } from '@/hooks/useAuthErrorText'
import { CheckCircle2, XCircle, Loader2, Zap } from 'lucide-react'

function VerifyEmailContent() {
  const { t } = useLanguage()
  const authErrorText = useAuthErrorText()
  const params = useSearchParams()
  const token  = params.get('token')
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading')
  const [message, setMessage] = useState('')
  // Every way of landing here in error — expired link, mangled link, or no
  // link at all — has the same fix, so the page carries it rather than
  // dead-ending at "back to signup".
  const [resendEmail, setResendEmail] = useState('')
  const [resending,   setResending]   = useState(false)
  const [resentNote,  setResentNote]  = useState<string | null>(null)

  async function handleResend(e: React.FormEvent) {
    e.preventDefault()
    setResending(true)
    try {
      await authResendVerification(resendEmail)
    } catch {
      // Generic by design (anti-enumeration): the same answer for an unknown
      // address, an already-verified one and a real resend.
    } finally {
      setResentNote(t('auth.resend_verification_done'))
      setResending(false)
    }
  }

  useEffect(() => {
    if (!token) { setStatus('error'); setMessage(t('auth.verify_no_token')); return }
    // The endpoint's own `message` is English prose; the outcome here is binary,
    // so the localized copy says it instead. Failures carry a stable
    // `error_code`, which `authErrorText` renders in the user's language.
    authVerifyEmail(token)
      .then(() => { setStatus('ok'); setMessage(t('auth.verify_ok_body')) })
      .catch(e => { setStatus('error'); setMessage(authErrorText(e, 'errors.verification_token_invalid')) })
    // Deliberately keyed on the token alone: `t` and `authErrorText` are new
    // identities on every render, and the verification is a one-shot call.
  }, [token])   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div style={{ width: '100%', maxWidth: 400, padding: '0 20px', textAlign: 'center' }}>
      <div style={{
        background: 'var(--surface)', border: '1px solid var(--surface)',
        borderRadius: 14, padding: '40px 28px',
      }}>
        <div style={{
          width: 44, height: 44, borderRadius: 11, margin: '0 auto 16px',
          background: 'linear-gradient(135deg, var(--accent), var(--accent))',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Zap size={20} color="#fff" strokeWidth={2.5} />
        </div>

        {status === 'loading' && (
          <>
            <Loader2 size={28} color="var(--accent)" style={{ margin: '0 auto 12px', animation: 'spin 1s linear infinite' }} />
            <p style={{ fontSize: 14, color: 'var(--muted)' }}>{t('auth.verify_in_progress')}</p>
          </>
        )}
        {status === 'ok' && (
          <>
            <CheckCircle2 size={36} color="#22c55e" style={{ margin: '0 auto 12px' }} />
            <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: '0 0 8px' }}>{t('auth.verify_ok_title')}</h2>
            <p style={{ fontSize: 13, color: 'var(--dim)', margin: '0 0 24px' }}>{message}</p>
            <Link href="/login" style={{
              display: 'inline-block', padding: '10px 28px',
              background: 'var(--accent)', color: '#fff', borderRadius: 8,
              fontSize: 13, fontWeight: 600, textDecoration: 'none',
            }}>{t('auth.login_title')}</Link>
          </>
        )}
        {status === 'error' && (
          <>
            <XCircle size={36} color="#ef4444" style={{ margin: '0 auto 12px' }} />
            <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: '0 0 8px' }}>{t('auth.verify_failed_title')}</h2>
            <p style={{ fontSize: 13, color: 'var(--dim)', margin: '0 0 20px' }}>{message}</p>

            {resentNote ? (
              <p style={{ fontSize: 13, color: '#22c55e', margin: '0 0 22px' }}>{resentNote}</p>
            ) : (
              <form onSubmit={handleResend} style={{ margin: '0 0 22px', textAlign: 'left' }}>
                <label htmlFor="resend-email" style={{ display: 'block', fontSize: 12, color: 'var(--muted)', marginBottom: 7 }}>
                  {t('auth.resend_verification_prompt')}
                </label>
                <div style={{ display: 'flex', gap: 8 }}>
                  <input
                    id="resend-email" name="email" type="email" required
                    value={resendEmail} onChange={e => setResendEmail(e.target.value)}
                    placeholder="you@company.com"
                    style={{
                      flex: 1, minWidth: 0, boxSizing: 'border-box', padding: '9px 11px',
                      background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 8,
                      color: 'var(--text)', fontSize: 13, outline: 'none',
                    }}
                  />
                  <button
                    type="submit" disabled={resending}
                    style={{
                      padding: '9px 16px', borderRadius: 8, border: 'none',
                      background: resending ? 'var(--border-strong)' : 'var(--accent)', color: '#fff',
                      fontSize: 12.5, fontWeight: 600,
                      cursor: resending ? 'wait' : 'pointer', whiteSpace: 'nowrap',
                    }}
                  >
                    {resending ? t('auth.resend_verification_sending') : t('auth.resend_verification')}
                  </button>
                </div>
              </form>
            )}

            <Link href="/signup" style={{
              display: 'inline-block', padding: '10px 28px',
              background: 'var(--surface)', color: 'var(--text)', borderRadius: 8,
              fontSize: 13, fontWeight: 600, textDecoration: 'none',
              border: '1px solid var(--border)',
            }}>{t('auth.verify_back_to_signup')}</Link>
          </>
        )}
      </div>
    </div>
  )
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={
      <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
        <Loader2 size={24} color="var(--accent)" style={{ animation: 'spin 1s linear infinite' }} />
      </div>
    }>
      <VerifyEmailContent />
    </Suspense>
  )
}
