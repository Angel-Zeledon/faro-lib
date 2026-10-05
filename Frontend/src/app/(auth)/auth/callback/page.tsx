'use client'
/**
 * Landing page after a Google / Microsoft / Apple / Facebook sign-in.
 *
 * The backend redirects here with `#code=<one-time code>` — a fragment, so it
 * never reaches a server log — and this page trades it for the session with a
 * POST. No token ever travels in a URL. The code lives 60 seconds and works
 * once; the fragment is wiped from the address bar before the exchange, so a
 * Back button or a copied URL cannot replay it.
 */
import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Loader2, XCircle } from 'lucide-react'
import { exchangeSocialCode } from '@/lib/api'
import { setAuth } from '@/lib/auth'
import { INTRO_SEEN_KEY } from '@/components/layout/AppIntro'
import { BrandMark } from '@/components/brand/BrandMark'
import { useLanguage } from '@/contexts/LanguageContext'
import { useAuthErrorText } from '@/hooks/useAuthErrorText'

export default function SocialCallbackPage() {
  const { t } = useLanguage()
  const authErrorText = useAuthErrorText()
  const router = useRouter()
  const [error, setError] = useState<string | null>(null)
  // React strict mode runs effects twice in development. The code is single-
  // use, so a second exchange would fail and show an error over a sign-in
  // that actually worked.
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    const code = new URLSearchParams(window.location.hash.slice(1)).get('code') || ''
    window.history.replaceState(null, '', window.location.pathname)
    if (!code) {
      setError(t('errors.oauth_exchange_invalid'))
      return
    }
    exchangeSocialCode(code)
      .then(res => {
        setAuth(res.access_token, res.refresh_token, {
          id:        res.user.id,
          email:     res.user.email,
          full_name: res.user.full_name,
          role:      res.user.role,
          tenant_id: res.user.tenant_id,
        })
        try { sessionStorage.removeItem(INTRO_SEEN_KEY) } catch { /* storage blocked */ }
        router.replace('/hoy')
      })
      .catch(e => setError(authErrorText(e, 'errors.oauth_exchange_invalid')))
    // One-shot by design; `t`/`authErrorText` change identity every render.
  }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div style={{ width: '100%', maxWidth: 400, padding: '0 20px', textAlign: 'center' }}>
      <div style={{
        background: 'var(--surface)', border: '1px solid var(--surface)',
        borderRadius: 14, padding: '40px 28px',
      }}>
        <div style={{ marginBottom: 18 }}>
          <BrandMark size={40} />
        </div>
        {error ? (
          <>
            <XCircle size={30} color="#B94A4A" style={{ marginBottom: 12 }} aria-hidden="true" />
            <h1 style={{ fontSize: 17, fontWeight: 600, color: 'var(--text)', margin: '0 0 8px' }}>
              {t('auth.social_callback_failed_title')}
            </h1>
            <p role="alert" style={{ fontSize: 13.5, color: 'var(--muted)', margin: '0 0 20px', lineHeight: 1.55 }}>
              {error}
            </p>
            <Link href="/login" style={{
              display: 'inline-flex', padding: '10px 20px', borderRadius: 10,
              background: 'var(--accent)', color: '#fff', fontSize: 13.5, fontWeight: 600,
              textDecoration: 'none',
            }}>
              {t('auth.social_callback_back')}
            </Link>
          </>
        ) : (
          <>
            <Loader2 size={26} style={{ animation: 'spin 0.8s linear infinite', marginBottom: 12, color: 'var(--accent)' }} aria-hidden="true" />
            <p style={{ fontSize: 14, color: 'var(--muted)', margin: 0 }}>{t('auth.social_callback_title')}</p>
          </>
        )}
      </div>
    </div>
  )
}
