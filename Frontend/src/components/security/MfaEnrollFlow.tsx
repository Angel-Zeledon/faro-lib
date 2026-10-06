'use client'
import { useState } from 'react'
import { Copy, Check, Download, ShieldCheck } from 'lucide-react'
import Spinner from '@/components/ui/Spinner'
import { useLanguage } from '@/contexts/LanguageContext'
import { useErrorDetail } from '@/components/ui/States'
import { mfaEnrollBegin, mfaEnrollConfirm, type MfaEnrollStart } from '@/lib/api'
import QrCode from './QrCode'

/**
 * Enrolling an authenticator app: begin (QR + key), confirm with the first
 * code, then show the recovery codes ONCE.
 *
 * Used in two places that look different but do the same thing: Mi cuenta (a
 * signed-in session) and the login screen, for a person the organization
 * requires to enrol, who holds only an `enrollmentToken` and no session.
 * `variant` only picks which set of CSS variables paints it.
 */
export type MfaVariant = 'app' | 'auth'

export const palette = (v: MfaVariant) => v === 'auth'
  ? { text: 'var(--a-ink)', muted: 'var(--a-muted)', border: 'rgba(120,120,120,0.35)',
      accent: 'var(--a-cta-bg)', accentFg: 'var(--a-cta-fg)' }
  : { text: 'var(--text)', muted: 'var(--dim)', border: 'var(--border)',
      accent: 'var(--accent)', accentFg: '#fff' }

export function CodeInput({ value, onChange, id, variant, label, autoFocus, recovery }: {
  value: string; onChange: (v: string) => void; id: string; variant: MfaVariant
  label: string; autoFocus?: boolean; recovery?: boolean
}) {
  const c = palette(variant)
  return (
    <div>
      <label htmlFor={id} style={{ display: 'block', marginBottom: 7, fontSize: 12, fontWeight: 500, color: c.muted }}>
        {label}
      </label>
      <input
        id={id} name="code" autoFocus={autoFocus}
        className={variant === 'auth' ? 'auth-input' : 'form-input'}
        inputMode={recovery ? 'text' : 'numeric'}
        autoComplete="one-time-code"
        value={value}
        onChange={e => onChange(recovery ? e.target.value.toUpperCase() : e.target.value.replace(/\D/g, '').slice(0, 6))}
        placeholder={recovery ? 'XXXXX-XXXXX' : '123456'}
        style={variant === 'app' ? {
          width: 180, padding: '10px 14px', fontSize: recovery ? 15 : 22, fontWeight: 700,
          letterSpacing: recovery ? 2 : 8, fontFamily: 'monospace', textAlign: 'center',
        } : { fontFamily: 'monospace', letterSpacing: recovery ? 2 : 6, textAlign: 'center' }}
      />
    </div>
  )
}

export function PrimaryButton({ children, onClick, disabled, loading, variant, type = 'button' }: {
  children: React.ReactNode; onClick?: () => void; disabled?: boolean; loading?: boolean
  variant: MfaVariant; type?: 'button' | 'submit'
}) {
  const c = palette(variant)
  const off = disabled || loading
  return (
    <button
      type={type} onClick={onClick} disabled={off}
      style={{
        all: 'unset', boxSizing: 'border-box', cursor: off ? 'default' : 'pointer',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 7,
        padding: '9px 18px', minHeight: 40, borderRadius: variant === 'auth' ? 11 : 8,
        fontSize: variant === 'auth' ? 14 : 12, fontWeight: 600,
        background: c.accent, color: c.accentFg, opacity: off ? 0.55 : 1, transition: 'opacity 0.15s',
      }}
    >
      {loading && <Spinner size={12} />}
      {children}
    </button>
  )
}

export function GhostButton({ children, onClick, disabled, variant }: {
  children: React.ReactNode; onClick?: () => void; disabled?: boolean; variant: MfaVariant
}) {
  const c = palette(variant)
  return (
    <button
      type="button" onClick={onClick} disabled={disabled}
      style={{
        all: 'unset', boxSizing: 'border-box', cursor: disabled ? 'default' : 'pointer',
        display: 'inline-flex', alignItems: 'center', gap: 6, padding: '9px 14px', minHeight: 40,
        borderRadius: variant === 'auth' ? 11 : 8, fontSize: variant === 'auth' ? 13 : 12,
        border: `1px solid ${c.border}`, color: c.muted,
      }}
    >
      {children}
    </button>
  )
}

export function ErrorLine({ text }: { text: string }) {
  return (
    <div role="alert" style={{
      padding: '9px 12px', borderRadius: 10, fontSize: 13, color: '#B94A4A',
      background: 'rgba(185,74,74,0.04)', border: '1px solid rgba(185,74,74,0.15)',
    }}>
      {text}
    </div>
  )
}

/** Recovery codes, shown once, with copy and download. */
export function RecoveryCodes({ codes, variant, onSaved }: {
  codes: string[]; variant: MfaVariant; onSaved: () => void
}) {
  const { t } = useLanguage()
  const c = palette(variant)
  const [copied, setCopied] = useState(false)
  const text = codes.join('\n')

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* clipboard blocked: the codes are on screen and downloadable */ }
  }

  function download() {
    const blob = new Blob([`StockAI\n\n${text}\n`], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'stockai-recovery-codes.txt'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontSize: 14, fontWeight: 600, color: c.text }}>{t('mfa.codes_title')}</div>
      <div style={{ fontSize: 12.5, color: c.muted, lineHeight: 1.5 }}>{t('mfa.codes_body')}</div>
      <div
        data-testid="recovery-codes"
        style={{
          display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 6,
          padding: 12, borderRadius: 10, border: `1px solid ${c.border}`,
          fontFamily: 'monospace', fontSize: 14, color: c.text, letterSpacing: 1,
        }}
      >
        {codes.map(code => <span key={code}>{code}</span>)}
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <GhostButton variant={variant} onClick={copy}>
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {copied ? t('mfa.copied') : t('mfa.copy')}
        </GhostButton>
        <GhostButton variant={variant} onClick={download}>
          <Download size={13} />
          {t('mfa.codes_download')}
        </GhostButton>
        <PrimaryButton variant={variant} onClick={onSaved}>
          <ShieldCheck size={13} />
          {t('mfa.codes_saved')}
        </PrimaryButton>
      </div>
    </div>
  )
}

export default function MfaEnrollFlow({ variant, enrollmentToken, onEnrolled, onCancel }: {
  variant: MfaVariant
  /** Set on the login screen: the person holds no session yet. */
  enrollmentToken?: string
  /** Called once the person confirmed they saved their recovery codes. */
  onEnrolled: (info: { signInAgain: boolean }) => void
  onCancel?: () => void
}) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const c = palette(variant)
  const [start, setStart] = useState<MfaEnrollStart | null>(null)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [codes, setCodes] = useState<string[] | null>(null)
  const [signInAgain, setSignInAgain] = useState(false)

  async function begin() {
    setBusy(true); setError(null)
    try {
      setStart(await mfaEnrollBegin(enrollmentToken))
    } catch (e) {
      setError(errorDetail(e) || t('mfa.error_generic'))
    } finally { setBusy(false) }
  }

  async function confirm() {
    if (code.length !== 6) return
    setBusy(true); setError(null)
    try {
      const r = await mfaEnrollConfirm(code, enrollmentToken)
      setCodes(r.recovery_codes)
      setSignInAgain(r.sign_in_again)
    } catch (e) {
      setError(errorDetail(e) || t('mfa.error_generic'))
    } finally { setBusy(false) }
  }

  async function copyKey() {
    if (!start) return
    try {
      await navigator.clipboard.writeText(start.secret)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* the key is on screen */ }
  }

  if (codes) {
    return <RecoveryCodes codes={codes} variant={variant} onSaved={() => onEnrolled({ signInAgain })} />
  }

  if (!start) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {error && <ErrorLine text={error} />}
        <div style={{ display: 'flex', gap: 8 }}>
          <PrimaryButton variant={variant} onClick={begin} loading={busy}>{t('mfa.begin')}</PrimaryButton>
          {onCancel && <GhostButton variant={variant} onClick={onCancel}>{t('mfa.cancel')}</GhostButton>}
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ fontSize: 13, color: c.text, lineHeight: 1.5 }}>{t('mfa.enroll_step1')}</div>
      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <QrCode value={start.otpauth_uri} label={t('mfa.qr_label')} />
        <div style={{ minWidth: 0, flex: '1 1 200px' }}>
          <div style={{ fontSize: 11.5, color: c.muted, marginBottom: 6 }}>{t('mfa.manual_key')}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <code data-testid="mfa-secret" style={{
              fontFamily: 'monospace', fontSize: 13, color: c.text, letterSpacing: 1,
              overflowWrap: 'anywhere', wordBreak: 'break-all',
            }}>{start.secret.match(/.{1,4}/g)?.join(' ')}</code>
            <GhostButton variant={variant} onClick={copyKey}>
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? t('mfa.copied') : t('mfa.copy')}
            </GhostButton>
          </div>
          <div style={{ fontSize: 11, color: c.muted, marginTop: 10, lineHeight: 1.5 }}>{t('mfa.secret_stays')}</div>
        </div>
      </div>
      <div style={{ fontSize: 13, color: c.text, lineHeight: 1.5 }}>{t('mfa.enroll_step2')}</div>
      <form onSubmit={e => { e.preventDefault(); confirm() }} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <CodeInput id="mfa-enroll-code" variant={variant} value={code} onChange={setCode} label={t('mfa.code_label')} autoFocus />
        {error && <ErrorLine text={error} />}
        <div style={{ display: 'flex', gap: 8 }}>
          <PrimaryButton type="submit" variant={variant} loading={busy} disabled={code.length !== 6}>
            {t('mfa.confirm')}
          </PrimaryButton>
          {onCancel && <GhostButton variant={variant} onClick={onCancel}>{t('mfa.cancel')}</GhostButton>}
        </div>
      </form>
    </div>
  )
}
