'use client'
import { Suspense, useEffect, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { authVerifyEmail } from '@/lib/api'
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
        background: '#0f1015', border: '1px solid #1e2030',
        borderRadius: 14, padding: '40px 28px',
      }}>
        <div style={{
          width: 44, height: 44, borderRadius: 11, margin: '0 auto 16px',
          background: 'linear-gradient(135deg, #818cf8, #6366f1)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Zap size={20} color="#fff" strokeWidth={2.5} />
        </div>

        {status === 'loading' && (
          <>
            <Loader2 size={28} color="#818cf8" style={{ margin: '0 auto 12px', animation: 'spin 1s linear infinite' }} />
            <p style={{ fontSize: 14, color: '#94a3b8' }}>{t('auth.verify_in_progress')}</p>
          </>
        )}
        {status === 'ok' && (
          <>
            <CheckCircle2 size={36} color="#22c55e" style={{ margin: '0 auto 12px' }} />
            <h2 style={{ fontSize: 18, fontWeight: 700, color: '#e2e8f0', margin: '0 0 8px' }}>{t('auth.verify_ok_title')}</h2>
            <p style={{ fontSize: 13, color: '#64748b', margin: '0 0 24px' }}>{message}</p>
            <Link href="/login" style={{
              display: 'inline-block', padding: '10px 28px',
              background: '#818cf8', color: '#fff', borderRadius: 8,
              fontSize: 13, fontWeight: 600, textDecoration: 'none',
            }}>{t('auth.login_title')}</Link>
          </>
        )}
        {status === 'error' && (
          <>
            <XCircle size={36} color="#ef4444" style={{ margin: '0 auto 12px' }} />
            <h2 style={{ fontSize: 18, fontWeight: 700, color: '#e2e8f0', margin: '0 0 8px' }}>{t('auth.verify_failed_title')}</h2>
            <p style={{ fontSize: 13, color: '#64748b', margin: '0 0 24px' }}>{message}</p>
            <Link href="/signup" style={{
              display: 'inline-block', padding: '10px 28px',
              background: '#1e2030', color: '#e2e8f0', borderRadius: 8,
              fontSize: 13, fontWeight: 600, textDecoration: 'none',
              border: '1px solid #2d3048',
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
        <Loader2 size={24} color="#818cf8" style={{ animation: 'spin 1s linear infinite' }} />
      </div>
    }>
      <VerifyEmailContent />
    </Suspense>
  )
}
