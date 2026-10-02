'use client'
/**
 * "Continue with Google / Apple / Facebook" — only for the providers this
 * installation enabled.
 *
 * Renders NOTHING until `/auth/providers` answers, and nothing at all when it
 * answers with an empty list. That is the default on every install (the
 * feature is off until the instance operator configures a provider), so the
 * login and signup screens look exactly as they did before this existed.
 *
 * Each button follows its brand's published guidelines: Google's four-colour
 * "G" on white (or #131314 in dark), Apple's logo in black/white, Facebook's
 * "f" on #1877F2. The label is the provider's own wording, "Continue with …".
 *
 * Clicking navigates the whole window to the backend's /start route — the
 * provider's page must take over, and the flow comes back through
 * /auth/callback. The terms notice sits under the buttons because a first
 * sign-in through a provider CREATES an account.
 */
import { useEffect, useState } from 'react'
import { getAuthProviders, socialStartUrl, type SocialProvider } from '@/lib/api'
import { useLanguage } from '@/contexts/LanguageContext'
import { useTheme } from '@/contexts/ThemeContext'

function GoogleLogo() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <path fill="#EA4335" d="M9 3.48c1.69 0 2.83.73 3.48 1.34l2.54-2.48C13.46.89 11.43 0 9 0 5.48 0 2.44 2.02.96 4.96l2.91 2.26C4.6 5.05 6.62 3.48 9 3.48z" />
      <path fill="#4285F4" d="M17.64 9.2c0-.74-.06-1.28-.19-1.84H9v3.34h4.96c-.1.83-.64 2.08-1.84 2.92l2.84 2.2c1.7-1.57 2.68-3.88 2.68-6.62z" />
      <path fill="#FBBC05" d="M3.88 10.78A5.54 5.54 0 0 1 3.58 9c0-.62.11-1.22.29-1.78L.96 4.96A9.008 9.008 0 0 0 0 9c0 1.45.35 2.82.96 4.04l2.92-2.26z" />
      <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.84-2.2c-.76.53-1.78.9-3.12.9-2.38 0-4.4-1.57-5.12-3.74L.97 13.04C2.45 15.98 5.48 18 9 18z" />
    </svg>
  )
}

function AppleLogo({ color }: { color: string }) {
  return (
    <svg width="16" height="19" viewBox="0 0 814 1000" aria-hidden="true">
      <path fill={color} d="M788.1 340.9c-5.8 4.5-108.2 62.2-108.2 190.5 0 148.4 130.3 200.9 134.2 202.2-.6 3.2-20.7 71.9-68.7 141.9-42.8 61.6-87.5 123.1-155.5 123.1s-85.5-39.5-164-39.5c-76.5 0-103.7 40.8-165.9 40.8s-105.6-57-155.5-127C46.7 790.7 0 663 0 541.8c0-194.4 126.4-297.5 250.8-297.5 66.1 0 121.2 43.4 162.7 43.4 39.5 0 101.1-46 176.3-46 28.5 0 130.9 2.6 198.3 99.2zm-234-181.5c31.1-36.9 53.1-88.1 53.1-139.3 0-7.1-.6-14.3-1.9-20.1-50.6 1.9-110.8 33.7-147.1 75.8-28.5 32.4-55.1 83.6-55.1 135.5 0 7.8 1.3 15.6 1.9 18.1 3.2.6 8.4 1.3 13.6 1.3 45.4 0 102.5-30.4 135.5-71.3z" />
    </svg>
  )
}

function FacebookLogo() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#ffffff" d="M24 12.073C24 5.405 18.627 0 12 0S0 5.405 0 12.073C0 18.1 4.388 23.094 10.125 24v-8.437H7.078v-3.49h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.49h-2.796V24C19.612 23.094 24 18.1 24 12.073z" />
    </svg>
  )
}

interface BrandStyle { bg: string; fg: string; border: string; logo: React.ReactNode }

function brand(provider: SocialProvider, dark: boolean): BrandStyle {
  if (provider === 'google') {
    return dark
      ? { bg: '#131314', fg: '#E3E3E3', border: '#8E918F', logo: <GoogleLogo /> }
      : { bg: '#FFFFFF', fg: '#1F1F1F', border: '#747775', logo: <GoogleLogo /> }
  }
  if (provider === 'apple') {
    return dark
      ? { bg: '#FFFFFF', fg: '#000000', border: '#FFFFFF', logo: <AppleLogo color="#000000" /> }
      : { bg: '#000000', fg: '#FFFFFF', border: '#000000', logo: <AppleLogo color="#FFFFFF" /> }
  }
  return { bg: '#1877F2', fg: '#FFFFFF', border: '#1877F2', logo: <FacebookLogo /> }
}

export function SocialButtons({ intent }: { intent: 'login' | 'signup' }) {
  const { t } = useLanguage()
  const { theme } = useTheme()
  const [providers, setProviders] = useState<SocialProvider[]>([])
  const [going, setGoing] = useState<SocialProvider | null>(null)

  useEffect(() => {
    let alive = true
    getAuthProviders()
      .then(r => { if (alive) setProviders(r.providers ?? []) })
      // Unreachable or old backend: no buttons, which is the pre-feature page.
      .catch(() => { if (alive) setProviders([]) })
    return () => { alive = false }
  }, [])

  // Coming back with the browser's Back button restores this page from the
  // bfcache with the spinner still on; reset it.
  useEffect(() => {
    const onShow = () => setGoing(null)
    window.addEventListener('pageshow', onShow)
    return () => window.removeEventListener('pageshow', onShow)
  }, [])

  if (providers.length === 0) return null
  const dark = theme === 'dark'

  return (
    <div className="auth-enter" style={{ marginBottom: 22, animation: 'auth-fade-up 0.6s cubic-bezier(0.16,1,0.3,1) 0.06s both' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {providers.map(p => {
          const b = brand(p, dark)
          const busy = going === p
          return (
            <a
              key={p}
              href={socialStartUrl(p, intent)}
              data-provider={p}
              onClick={e => {
                if (going) { e.preventDefault(); return }
                setGoing(p)
              }}
              aria-busy={busy}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10,
                minHeight: 44, padding: '0 16px', borderRadius: 11,
                background: b.bg, color: b.fg, border: `1px solid ${b.border}`,
                fontFamily: 'Roboto, system-ui, -apple-system, "Segoe UI", sans-serif',
                fontSize: 14, fontWeight: 500, textDecoration: 'none',
                opacity: going && !busy ? 0.55 : 1,
                cursor: going ? 'wait' : 'pointer',
                transition: 'opacity 0.15s ease, filter 0.15s ease',
              }}
            >
              {busy ? (
                <span style={{
                  width: 16, height: 16, borderRadius: '50%',
                  border: `2px solid ${b.fg}`, borderTopColor: 'transparent',
                  animation: 'spin 0.7s linear infinite', display: 'inline-block',
                }} />
              ) : b.logo}
              <span>{t(`auth.social_continue_${p}`)}</span>
            </a>
          )
        })}
      </div>
      <p style={{ margin: '10px 0 0', fontSize: 11.5, lineHeight: 1.5, color: 'var(--a-dim)', textAlign: 'center' }}>
        {t('auth.social_terms_notice')}
      </p>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 20 }}>
        <span style={{ flex: 1, height: 1, background: 'var(--a-line)' }} />
        <span style={{ fontSize: 12, color: 'var(--a-muted)' }}>{t('auth.social_divider')}</span>
        <span style={{ flex: 1, height: 1, background: 'var(--a-line)' }} />
      </div>
    </div>
  )
}

/** Copy for a sign-in refused on the way back from a provider. Never echoes a
 *  raw key: an unknown code falls back to the generic failure. */
export function socialErrorText(t: (k: string) => string, code: string): string {
  const key = `auth.social_error.${code}`
  const text = t(key)
  return text === key ? t('auth.social_error.oauth_failed') : text
}
