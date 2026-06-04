'use client'
import { useState, useRef } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { authForgotPassword, authForgotPasswordVerify, authResetPassword } from '@/lib/api'
import { Zap, CheckCircle2, AlertTriangle, ArrowLeft, KeyRound, Mail, Lock } from 'lucide-react'

const _input: React.CSSProperties = {
  width: '100%', padding: '10px 12px', boxSizing: 'border-box',
  background: '#141520', border: '1px solid #1e2030', borderRadius: 8,
  color: '#e2e8f0', fontSize: 13, outline: 'none',
}

type Step = 'email' | 'code' | 'password' | 'done'

export default function ForgotPasswordPage() {
  const router = useRouter()
  const [step,      setStep]      = useState<Step>('email')
  const [email,     setEmail]     = useState('')
  const [code,      setCode]      = useState(['', '', '', '', '', ''])
  const [pw,        setPw]        = useState('')
  const [pw2,       setPw2]       = useState('')
  const [resetToken, setResetToken] = useState('')
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState<string | null>(null)

  const codeRefs = useRef<(HTMLInputElement | null)[]>([])

  // ── Step 1: request OTP ──────────────────────────────────────────────────

  async function handleEmailSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await authForgotPassword(email)
      setStep('code')
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Request failed')
    } finally {
      setLoading(false)
    }
  }

  // ── Step 2: verify OTP ───────────────────────────────────────────────────

  function handleCodeInput(idx: number, val: string) {
    const digit = val.replace(/\D/g, '').slice(-1)
    const next = [...code]
    next[idx] = digit
    setCode(next)
    if (digit && idx < 5) codeRefs.current[idx + 1]?.focus()
  }

  function handleCodeKeyDown(idx: number, e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Backspace' && !code[idx] && idx > 0) {
      codeRefs.current[idx - 1]?.focus()
    }
  }

  async function handleCodeSubmit(e: React.FormEvent) {
    e.preventDefault()
    const fullCode = code.join('')
    if (fullCode.length < 6) { setError('Enter the complete 6-digit code'); return }
    setError(null)
    setLoading(true)
    try {
      const res = await authForgotPasswordVerify(email, fullCode)
      setResetToken(res.reset_token)
      setStep('password')
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Invalid code')
    } finally {
      setLoading(false)
    }
  }

  // ── Step 3: set new password ─────────────────────────────────────────────

  async function handlePasswordSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (pw !== pw2) { setError('Passwords do not match'); return }
    if (pw.length < 8) { setError('Password must be at least 8 characters'); return }
    setError(null)
    setLoading(true)
    try {
      await authResetPassword(resetToken, pw)
      setStep('done')
      setTimeout(() => router.replace('/login'), 2500)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Reset failed')
    } finally {
      setLoading(false)
    }
  }

  // ── Step indicator ───────────────────────────────────────────────────────

  function StepDot({ n, active, done }: { n: number; active: boolean; done: boolean }) {
    return (
      <div style={{
        width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 11, fontWeight: 700,
        background: done ? '#22c55e' : active ? '#818cf8' : '#1e2030',
        color: done || active ? '#fff' : '#64748b',
        transition: 'all 0.2s',
      }}>
        {done ? '✓' : n}
      </div>
    )
  }

  const stepIdx = step === 'email' ? 0 : step === 'code' ? 1 : step === 'password' ? 2 : 3

  return (
    <div style={{ width: '100%', maxWidth: 420, padding: '0 20px' }}>
      {/* Header */}
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <div style={{
          width: 44, height: 44, borderRadius: 11, margin: '0 auto 10px',
          background: 'linear-gradient(135deg, #818cf8, #6366f1)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Zap size={20} color="#fff" strokeWidth={2.5} />
        </div>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: '#e2e8f0', margin: '0 0 4px', letterSpacing: '-0.03em' }}>
          Recover your password
        </h1>
        <p style={{ fontSize: 12, color: '#64748b', margin: 0 }}>
          We&apos;ll send a verification code to your email
        </p>
      </div>

      {/* Step indicators */}
      {step !== 'done' && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, marginBottom: 20 }}>
          <StepDot n={1} active={stepIdx === 0} done={stepIdx > 0} />
          <div style={{ width: 24, height: 1, background: stepIdx > 0 ? '#22c55e' : '#1e2030', transition: 'background 0.2s' }} />
          <StepDot n={2} active={stepIdx === 1} done={stepIdx > 1} />
          <div style={{ width: 24, height: 1, background: stepIdx > 1 ? '#22c55e' : '#1e2030', transition: 'background 0.2s' }} />
          <StepDot n={3} active={stepIdx === 2} done={stepIdx > 2} />
        </div>
      )}

      <div style={{ background: '#0f1015', border: '1px solid #1e2030', borderRadius: 14, padding: '24px 28px' }}>

        {/* ── Done ── */}
        {step === 'done' && (
          <div style={{ textAlign: 'center' }}>
            <CheckCircle2 size={36} color="#22c55e" style={{ margin: '0 auto 12px' }} />
            <p style={{ fontSize: 14, fontWeight: 600, color: '#e2e8f0', margin: '0 0 6px' }}>Password updated!</p>
            <p style={{ fontSize: 12, color: '#64748b', margin: 0 }}>Redirecting to login…</p>
          </div>
        )}

        {/* ── Step 1: Email ── */}
        {step === 'email' && (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
              <Mail size={14} color="#818cf8" />
              <span style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>Enter your email address</span>
            </div>
            {error && <ErrorBox msg={error} />}
            <form onSubmit={handleEmailSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div>
                <label style={{ fontSize: 12, fontWeight: 500, color: '#94a3b8', display: 'block', marginBottom: 6 }}>
                  Email address
                </label>
                <input
                  type="email" required value={email} onChange={e => setEmail(e.target.value)}
                  placeholder="you@company.com" style={_input}
                  onFocus={e => (e.target.style.borderColor = '#818cf8')}
                  onBlur={e => (e.target.style.borderColor = '#1e2030')}
                />
              </div>
              <PrimaryBtn loading={loading} label="Send verification code" loadingLabel="Sending…" />
            </form>
            <BackToLogin />
          </>
        )}

        {/* ── Step 2: OTP code ── */}
        {step === 'code' && (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <KeyRound size={14} color="#818cf8" />
              <span style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>Enter the verification code</span>
            </div>
            <p style={{ fontSize: 12, color: '#64748b', margin: '0 0 16px' }}>
              We sent a 6-digit code to <strong style={{ color: '#94a3b8' }}>{email}</strong>.
              It expires in 30 hours.
            </p>
            {error && <ErrorBox msg={error} />}
            <form onSubmit={handleCodeSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
                {code.map((digit, i) => (
                  <input
                    key={i}
                    ref={el => { codeRefs.current[i] = el }}
                    type="text" inputMode="numeric" maxLength={1}
                    value={digit}
                    onChange={e => handleCodeInput(i, e.target.value)}
                    onKeyDown={e => handleCodeKeyDown(i, e)}
                    style={{
                      width: 42, height: 48, textAlign: 'center', fontSize: 20,
                      fontWeight: 700, fontFamily: 'monospace',
                      background: '#141520', border: `1px solid ${digit ? '#818cf8' : '#1e2030'}`,
                      borderRadius: 8, color: '#e2e8f0', outline: 'none',
                    }}
                    onFocus={e => (e.target.style.borderColor = '#818cf8')}
                    onBlur={e => (e.target.style.borderColor = digit ? '#818cf8' : '#1e2030')}
                  />
                ))}
              </div>
              <PrimaryBtn loading={loading} label="Verify code" loadingLabel="Verifying…" />
            </form>
            <div style={{ textAlign: 'center', marginTop: 14, fontSize: 12, color: '#64748b' }}>
              Didn&apos;t receive it?{' '}
              <button
                style={{ all: 'unset', cursor: 'pointer', color: '#818cf8' }}
                onClick={() => { setStep('email'); setCode(['','','','','','']); setError(null) }}
              >
                Try again
              </button>
            </div>
          </>
        )}

        {/* ── Step 3: New password ── */}
        {step === 'password' && (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
              <Lock size={14} color="#818cf8" />
              <span style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>Set your new password</span>
            </div>
            {error && <ErrorBox msg={error} />}
            <form onSubmit={handlePasswordSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div>
                <label style={{ fontSize: 12, fontWeight: 500, color: '#94a3b8', display: 'block', marginBottom: 6 }}>
                  New password
                </label>
                <input
                  type="password" required value={pw} onChange={e => setPw(e.target.value)}
                  placeholder="Min. 8 characters" style={_input}
                  onFocus={e => (e.target.style.borderColor = '#818cf8')}
                  onBlur={e => (e.target.style.borderColor = '#1e2030')}
                />
              </div>
              <div>
                <label style={{ fontSize: 12, fontWeight: 500, color: '#94a3b8', display: 'block', marginBottom: 6 }}>
                  Confirm password
                </label>
                <input
                  type="password" required value={pw2} onChange={e => setPw2(e.target.value)}
                  placeholder="Repeat password"
                  style={{ ..._input, borderColor: pw2 && pw !== pw2 ? '#ef4444' : '#1e2030' }}
                  onFocus={e => (e.target.style.borderColor = pw2 && pw !== pw2 ? '#ef4444' : '#818cf8')}
                  onBlur={e => (e.target.style.borderColor = pw2 && pw !== pw2 ? '#ef4444' : '#1e2030')}
                />
              </div>
              <PrimaryBtn loading={loading} label="Reset password" loadingLabel="Saving…" />
            </form>
          </>
        )}
      </div>
    </div>
  )
}

function ErrorBox({ msg }: { msg: string }) {
  return (
    <div style={{
      display: 'flex', gap: 8, alignItems: 'center',
      padding: '10px 14px', borderRadius: 8, marginBottom: 14,
      background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)',
      fontSize: 13, color: '#ef4444',
    }}>
      <AlertTriangle size={14} /> {msg}
    </div>
  )
}

function PrimaryBtn({ loading, label, loadingLabel }: { loading: boolean; label: string; loadingLabel: string }) {
  return (
    <button
      type="submit" disabled={loading}
      style={{
        width: '100%', padding: '11px', borderRadius: 8, border: 'none',
        background: loading ? '#4f56b0' : '#818cf8', color: '#fff',
        fontSize: 13, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer', marginTop: 2,
      }}
    >
      {loading ? loadingLabel : label}
    </button>
  )
}

function BackToLogin() {
  return (
    <p style={{ textAlign: 'center', marginTop: 16, fontSize: 12, color: '#64748b' }}>
      <Link href="/login" style={{ color: '#818cf8', textDecoration: 'none' }}>
        <ArrowLeft size={10} style={{ verticalAlign: 'middle', marginRight: 3 }} />
        Back to login
      </Link>
    </p>
  )
}
