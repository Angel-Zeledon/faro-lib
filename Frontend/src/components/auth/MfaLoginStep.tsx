'use client'
import { useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useAuthErrorText } from '@/hooks/useAuthErrorText'
import { authMfaVerify, isApiError, type LoginSession } from '@/lib/api'
import MfaEnrollFlow, {
  CodeInput, PrimaryButton, GhostButton, ErrorLine,
} from '@/components/security/MfaEnrollFlow'

/**
 * The login screen's second step, in place of the password form:
 *
 *  - `challenge`: the password was right and the account has an authenticator.
 *    Ask for the 6-digit code (or a recovery code).
 *  - `enroll`: the organization REQUIRES two-step sign-in and this person has
 *    not set it up, so no session was issued. Set it up here, with the
 *    one-time token login returned, then sign in again.
 *
 * Neither step ever holds an access token until the server issues one.
 */
export type MfaStep =
  | { kind: 'challenge'; token: string; justEnrolled: boolean }
  | { kind: 'enroll'; token: string }

export default function MfaLoginStep({ step, onSession, onBack, onSignInAgain }: {
  step: MfaStep
  onSession: (s: LoginSession) => void
  onBack: () => void
  /** After enrolling: sign in again to get the (now required) challenge. */
  onSignInAgain: () => void
}) {
  const { t } = useLanguage()
  const errorText = useAuthErrorText()
  const [code, setCode] = useState('')
  const [recovery, setRecovery] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dead, setDead] = useState(false)
  const [enrolled, setEnrolled] = useState(false)

  const heading = (title: string, subtitle: string) => (
    <div style={{ marginBottom: 24 }}>
      <h1 style={{
        fontFamily: 'var(--font-brand), system-ui, sans-serif', fontSize: 24, fontWeight: 600,
        color: 'var(--a-ink)', margin: '0 0 10px', letterSpacing: '-0.03em', lineHeight: 1.12,
        display: 'flex', alignItems: 'center', gap: 10,
      }}>
        <ShieldCheck size={22} aria-hidden="true" />{title}
      </h1>
      <p style={{ fontSize: 14, color: 'var(--a-muted)', margin: 0, lineHeight: 1.5 }}>{subtitle}</p>
    </div>
  )

  if (step.kind === 'enroll') {
    return (
      <div data-testid="mfa-enroll-step">
        {heading(t('mfa.enroll_required_title'), t('mfa.enroll_required_subtitle'))}
        {enrolled ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ fontSize: 13.5, color: 'var(--a-ink)', lineHeight: 1.5 }}>{t('mfa.enrolled_sign_in_again')}</div>
            <PrimaryButton variant="auth" onClick={onSignInAgain}>{t('mfa.sign_in_again')}</PrimaryButton>
          </div>
        ) : (
          <MfaEnrollFlow
            variant="auth" enrollmentToken={step.token}
            onCancel={onBack}
            onEnrolled={() => setEnrolled(true)}
          />
        )}
      </div>
    )
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (busy || dead) return
    setBusy(true); setError(null)
    try {
      onSession(await authMfaVerify(step.token, code))
    } catch (err) {
      setError(errorText(err, 'mfa.error_generic'))
      // The challenge is spent (expired, used, or out of guesses): only a new
      // password sign-in can continue, so say so instead of inviting retries.
      if (isApiError(err) && err.code === 'mfa_challenge_invalid') setDead(true)
      setCode('')
    } finally {
      setBusy(false)
    }
  }

  const ready = recovery ? code.trim().length >= 10 : code.length === 6

  return (
    <div data-testid="mfa-challenge-step">
      {heading(t('mfa.challenge_title'), recovery ? t('mfa.recovery_hint') : t('mfa.challenge_subtitle'))}
      {step.justEnrolled && (
        <div style={{ fontSize: 12.5, color: 'var(--a-muted)', marginBottom: 14, lineHeight: 1.5 }}>
          {t('mfa.next_code_hint')}
        </div>
      )}
      <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <CodeInput
          id="mfa-login-code" variant="auth" autoFocus recovery={recovery}
          value={code} onChange={setCode}
          label={recovery ? t('mfa.recovery_label') : t('mfa.code_label')}
        />
        {error && <ErrorLine text={error} />}
        <PrimaryButton type="submit" variant="auth" loading={busy} disabled={!ready || dead}>
          {busy ? t('mfa.verifying') : t('mfa.verify')}
        </PrimaryButton>
        <button
          type="button" disabled={dead}
          onClick={() => { setRecovery(v => !v); setCode(''); setError(null) }}
          style={{
            all: 'unset', cursor: dead ? 'default' : 'pointer', alignSelf: 'flex-start',
            fontSize: 12.5, color: 'var(--a-muted)', textDecoration: 'underline', textUnderlineOffset: 3,
          }}
        >
          {recovery ? t('mfa.use_app') : t('mfa.use_recovery')}
        </button>
        <div><GhostButton variant="auth" onClick={onBack}>{t('mfa.back_to_login')}</GhostButton></div>
      </form>
    </div>
  )
}
