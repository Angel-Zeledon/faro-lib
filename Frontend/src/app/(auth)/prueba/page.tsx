'use client'
import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { AlertTriangle, Check, Copy, Clock, Sparkles } from 'lucide-react'
import { authLogin, createTrialAccount, type TrialAccount } from '@/lib/api'
import { setAuth } from '@/lib/auth'
import { useLanguage } from '@/contexts/LanguageContext'
import { useAuthErrorText } from '@/hooks/useAuthErrorText'

// The landing's "try it without signing up". Arriving here creates a throwaway
// account (backend/trial/) and shows its user and password, which the visitor
// can copy to come back later in the same 24 hours, or use straight away.
// Entering goes to the one-click demo (/ventas?demo=1), which loads the sample
// history and shows the training progress until there is a semáforo to see.
//
// The credentials are kept in sessionStorage so a reload shows the same account
// instead of minting another one — the backend allows three per address a day,
// and a refresh must not spend them. The password lives nowhere else: the
// server stores only its hash.

const STORE_KEY = 'stockai_trial_account'

function readStored(): TrialAccount | null {
  try {
    const raw = sessionStorage.getItem(STORE_KEY)
    if (!raw) return null
    const acct = JSON.parse(raw) as TrialAccount
    return new Date(acct.expires_at).getTime() > Date.now() ? acct : null
  } catch {
    return null
  }
}

function store(acct: TrialAccount) {
  try { sessionStorage.setItem(STORE_KEY, JSON.stringify(acct)) } catch { /* private mode */ }
}

function CopyField({ id, label, value }: { id: string; label: string; value: string }) {
  const { t } = useLanguage()
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      // Clipboard refused (insecure origin, permissions): the value is on
      // screen and selectable, which is the fallback.
    }
  }

  return (
    <div className="auth-field">
      <label htmlFor={id} style={{ display: 'block', marginBottom: 7, fontSize: 12, fontWeight: 500, color: 'var(--a-muted)' }}>
        {label}
      </label>
      <div style={{ position: 'relative' }}>
        <input
          id={id} readOnly value={value} className="auth-input auth-input-affix"
          onFocus={e => e.currentTarget.select()}
          style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 14 }}
        />
        <button
          type="button" onClick={copy} className="auth-eye"
          aria-label={copied ? t('trial.copied') : t('trial.copy')}
          title={copied ? t('trial.copied') : t('trial.copy')}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
      </div>
    </div>
  )
}

export default function TrialPage() {
  const { t, lang } = useLanguage()
  const authErrorText = useAuthErrorText()
  const router = useRouter()
  const [acct, setAcct] = useState<TrialAccount | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [entering, setEntering] = useState(false)
  // React runs effects twice in development; without this a reload would
  // create two accounts and show one.
  const started = useRef(false)

  function create() {
    setError(null)
    createTrialAccount()
      .then(a => { store(a); setAcct(a) })
      .catch(err => setError(authErrorText(err, 'trial.create_failed')))
  }

  useEffect(() => {
    if (started.current) return
    started.current = true
    const stored = readStored()
    if (stored) setAcct(stored)
    else create()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function enter() {
    if (!acct) return
    setEntering(true)
    setError(null)
    try {
      const res = await authLogin(acct.email, acct.password)
      setAuth(res.access_token, res.refresh_token, {
        id:        res.user.id,
        email:     res.user.email,
        full_name: res.user.full_name,
        role:      res.user.role,
        tenant_id: res.user.tenant_id,
      })
      router.replace('/ventas?demo=1')
    } catch (err) {
      setError(authErrorText(err, 'auth.login_failed'))
      setEntering(false)
    }
  }

  const expires = acct
    ? new Date(acct.expires_at).toLocaleString(lang === 'es' ? 'es-CR' : 'en-US', {
        weekday: 'long', hour: '2-digit', minute: '2-digit',
      })
    : ''

  return (
    <div className="auth-shell">
      <div style={{ width: '100%', maxWidth: 380 }}>
        <div className="auth-enter" style={{ animation: 'auth-fade-in 0.5s ease-out both' }}>

          <div style={{ marginBottom: 26 }}>
            <h1 style={{ fontFamily: 'var(--font-brand), system-ui, sans-serif', fontSize: 30, fontWeight: 600, color: 'var(--a-ink)', margin: '0 0 10px', letterSpacing: '-0.03em', lineHeight: 1.12 }}>
              {t('trial.title')}
            </h1>
            <p style={{ fontSize: 14, color: 'var(--a-muted)', margin: 0, lineHeight: 1.5 }}>
              {t('trial.subtitle')}
            </p>
          </div>

          {error && (
            <div role="alert" style={{
              display: 'flex', flexDirection: 'column', gap: 10,
              padding: '10px 12px', borderRadius: 10, marginBottom: 20,
              background: 'rgba(220,38,38,0.04)', border: '1px solid rgba(220,38,38,0.15)',
              fontSize: 13, color: '#dc2626',
            }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <AlertTriangle size={13} style={{ flexShrink: 0 }} />
                {error}
              </div>
              {!acct && (
                <button
                  type="button" onClick={create}
                  style={{ all: 'unset', cursor: 'pointer', alignSelf: 'flex-start', fontSize: 12.5, fontWeight: 600, color: 'var(--a-ink)', textDecoration: 'underline', textUnderlineOffset: 3 }}
                >
                  {t('trial.retry')}
                </button>
              )}
            </div>
          )}

          {!acct && !error && (
            <div role="status" style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 14, color: 'var(--a-muted)', padding: '18px 0' }}>
              <span style={{
                width: 14, height: 14, border: '2px solid var(--a-dim)',
                borderTopColor: 'var(--a-ink)', borderRadius: '50%',
                animation: 'spin 0.7s linear infinite', display: 'inline-block',
              }} />
              {t('trial.creating')}
            </div>
          )}

          {acct && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <CopyField id="trial-user" label={t('trial.user_label')} value={acct.email} />
              <CopyField id="trial-password" label={t('auth.password_label')} value={acct.password} />

              <div style={{ display: 'flex', flexDirection: 'column', gap: 9, fontSize: 12.5, color: 'var(--a-muted)', lineHeight: 1.5 }}>
                <div style={{ display: 'flex', gap: 8 }}>
                  <Clock size={14} style={{ flexShrink: 0, marginTop: 2 }} />
                  <span>{t('trial.expires_note', { when: expires })}</span>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <Sparkles size={14} style={{ flexShrink: 0, marginTop: 2 }} />
                  <span>{t('trial.data_note')}</span>
                </div>
              </div>

              <button
                type="button" onClick={enter} disabled={entering} className="auth-submit"
                style={{
                  width: '100%', padding: '12.5px', borderRadius: 11, border: 'none',
                  background: entering ? 'var(--a-dim)' : 'var(--a-cta-bg)',
                  color: 'var(--a-cta-fg)', fontSize: 14, fontWeight: 600,
                  cursor: entering ? 'not-allowed' : 'pointer', marginTop: 4,
                }}
              >
                {entering ? t('auth.signing_in') : t('trial.enter')}
              </button>
            </div>
          )}
        </div>

        <p style={{ marginTop: 28, fontSize: 13.5, color: 'var(--a-muted)' }}>
          {t('trial.prefer_signup')}{' '}
          <Link href="/signup" className="auth-link" style={{ color: 'var(--a-ink)', textDecoration: 'none', fontWeight: 600 }}>
            {t('auth.request_access')}
          </Link>
        </p>
      </div>
    </div>
  )
}
