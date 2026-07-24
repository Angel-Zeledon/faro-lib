'use client'
import { Suspense, useState } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import { authResetPassword } from '@/lib/api'
import { Zap, Eye, EyeOff, CheckCircle2, AlertTriangle, Loader2 } from 'lucide-react'

function ResetPasswordForm() {
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
    if (pw !== pw2) { setError('Passwords do not match'); return }
    if (pw.length < 8) { setError('Password must be at least 8 characters'); return }
    setError(null)
    setLoading(true)
    try {
      await authResetPassword(token, pw)
      setDone(true)
      setTimeout(() => router.replace('/login'), 2500)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Reset failed')
    } finally {
      setLoading(false)
    }
  }

  const inputStyle = {
    width: '100%', padding: '10px 12px', boxSizing: 'border-box' as const,
    background: '#141520', border: '1px solid #1e2030', borderRadius: 8,
    color: '#e2e8f0', fontSize: 13, outline: 'none',
  }

  return (
    <div style={{ width: '100%', maxWidth: 400, padding: '0 20px' }}>
      <div style={{ textAlign: 'center', marginBottom: 28 }}>
        <div style={{
          width: 44, height: 44, borderRadius: 11, margin: '0 auto 10px',
          background: 'linear-gradient(135deg, #818cf8, #6366f1)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Zap size={20} color="#fff" strokeWidth={2.5} />
        </div>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: '#e2e8f0', margin: '0 0 4px' }}>
          Set a new password
        </h1>
      </div>

      <div style={{ background: '#0f1015', border: '1px solid #1e2030', borderRadius: 14, padding: '24px 28px' }}>
        {done ? (
          <div style={{ textAlign: 'center' }}>
            <CheckCircle2 size={32} color="#22c55e" style={{ margin: '0 auto 12px' }} />
            <p style={{ fontSize: 13, color: '#94a3b8', margin: 0 }}>
              Password updated! Redirecting to login…
            </p>
          </div>
        ) : (
          <>
            {!token && (
              <div style={{ fontSize: 13, color: '#ef4444', marginBottom: 16 }}>
                Invalid or missing reset token. <Link href="/forgot-password" style={{ color: '#818cf8' }}>Request a new link</Link>.
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
                <label htmlFor="reset-new-password" style={{ fontSize: 12, fontWeight: 500, color: '#94a3b8', display: 'block', marginBottom: 6 }}>
                  New password
                </label>
                <div style={{ position: 'relative' }}>
                  <input
                    id="reset-new-password" name="new_password"
                    type={showPw ? 'text' : 'password'} required value={pw}
                    onChange={e => setPw(e.target.value)}
                    placeholder="Min. 8 characters"
                    style={{ ...inputStyle, paddingRight: 36 }}
                    onFocus={e => (e.target.style.borderColor = '#818cf8')}
                    onBlur={e => (e.target.style.borderColor = '#1e2030')}
                  />
                  <button type="button" onClick={() => setShowPw(v => !v)} style={{
                    all: 'unset', position: 'absolute', right: 10, top: '50%',
                    transform: 'translateY(-50%)', cursor: 'pointer', color: '#64748b',
                  }}>
                    {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </div>
              <div>
                <label htmlFor="reset-confirm-password" style={{ fontSize: 12, fontWeight: 500, color: '#94a3b8', display: 'block', marginBottom: 6 }}>
                  Confirm password
                </label>
                <input
                  id="reset-confirm-password" name="confirm_password"
                  type={showPw ? 'text' : 'password'} required value={pw2}
                  onChange={e => setPw2(e.target.value)}
                  placeholder="Repeat password"
                  style={{ ...inputStyle, borderColor: pw2 && pw !== pw2 ? '#ef4444' : '#1e2030' }}
                  onFocus={e => (e.target.style.borderColor = pw2 && pw !== pw2 ? '#ef4444' : '#818cf8')}
                  onBlur={e => (e.target.style.borderColor = pw2 && pw !== pw2 ? '#ef4444' : '#1e2030')}
                />
              </div>
              <button
                type="submit" disabled={loading || !token}
                style={{
                  width: '100%', padding: '11px', borderRadius: 8, border: 'none',
                  background: loading ? '#4f56b0' : '#818cf8', color: '#fff',
                  fontSize: 13, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer', marginTop: 4,
                }}
              >
                {loading ? 'Saving…' : 'Set new password'}
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
        <Loader2 size={24} color="#818cf8" style={{ animation: 'spin 1s linear infinite' }} />
      </div>
    }>
      <ResetPasswordForm />
    </Suspense>
  )
}
