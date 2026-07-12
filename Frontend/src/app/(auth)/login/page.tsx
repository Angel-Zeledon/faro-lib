'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { authLogin } from '@/lib/api'
import { setAuth } from '@/lib/auth'
import { Eye, EyeOff, AlertTriangle, TrendingUp, ArrowRight } from 'lucide-react'

export default function LoginPage() {
  const router = useRouter()
  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [showPw,   setShowPw]   = useState(false)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const res = await authLogin(email, password)
      setAuth(res.access_token, res.refresh_token, {
        id:        res.user.id,
        email:     res.user.email,
        full_name: res.user.full_name,
        role:      res.user.role,
        tenant_id: res.user.tenant_id,
      })
      router.replace('/hoy')
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box',
    padding: '11px 13px',
    background: '#0b1020', border: '1px solid #1a2540', borderRadius: 8,
    color: '#dde5f5', fontSize: 14, outline: 'none',
    transition: 'border-color 0.15s, box-shadow 0.15s',
  }

  return (
    <>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg) } }
        body { margin: 0; }
      `}</style>

      <div style={{
        minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: '#070b14',
        backgroundImage: 'radial-gradient(ellipse 60% 50% at 50% 0%, rgba(99,102,241,0.07) 0%, transparent 70%)',
        padding: '24px 16px',
      }}>

        <div style={{ width: '100%', maxWidth: 380 }}>

          {/* Logo */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 36, justifyContent: 'center' }}>
            <div style={{
              width: 36, height: 36, borderRadius: 9, flexShrink: 0,
              background: 'linear-gradient(135deg, #818cf8, #6366f1)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <TrendingUp size={17} color="#fff" strokeWidth={2.5} />
            </div>
            <span style={{ fontSize: 17, fontWeight: 700, color: '#dde5f5', letterSpacing: '-0.03em' }}>
              Faro
            </span>
          </div>

          {/* Card */}
          <div style={{
            background: '#0c1120',
            border: '1px solid #17233d',
            borderRadius: 14,
            padding: '32px 28px',
          }}>

            {/* Heading */}
            <div style={{ marginBottom: 24 }}>
              <h1 style={{ fontSize: 20, fontWeight: 700, color: '#dde5f5', margin: '0 0 6px', letterSpacing: '-0.025em' }}>
                Sign in
              </h1>
              <p style={{ fontSize: 13, color: '#3d5280', margin: 0 }}>
                Enter your credentials to access your workspace
              </p>
            </div>

            {/* Error */}
            {error && (
              <div style={{
                display: 'flex', gap: 8, alignItems: 'center',
                padding: '10px 12px', borderRadius: 8, marginBottom: 20,
                background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.18)',
                fontSize: 13, color: '#f87171',
              }}>
                <AlertTriangle size={13} style={{ flexShrink: 0 }} />
                {error}
              </div>
            )}

            {/* Form */}
            <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

              {/* Email */}
              <div>
                <label style={{
                  display: 'block', marginBottom: 6,
                  fontSize: 12, fontWeight: 600, color: '#485d80',
                }}>
                  Email address
                </label>
                <input
                  type="email" value={email} required autoComplete="email"
                  onChange={e => setEmail(e.target.value)}
                  placeholder="you@company.com"
                  style={inputStyle}
                  onFocus={e => { e.target.style.borderColor = '#6366f1'; e.target.style.boxShadow = '0 0 0 3px rgba(99,102,241,0.12)' }}
                  onBlur={e =>  { e.target.style.borderColor = '#1a2540'; e.target.style.boxShadow = 'none' }}
                />
              </div>

              {/* Password */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <label style={{ fontSize: 12, fontWeight: 600, color: '#485d80' }}>
                    Password
                  </label>
                  <Link href="/forgot-password" style={{ fontSize: 12, color: '#6366f1', textDecoration: 'none' }}>
                    Forgot password?
                  </Link>
                </div>
                <div style={{ position: 'relative' }}>
                  <input
                    type={showPw ? 'text' : 'password'} value={password} required autoComplete="current-password"
                    onChange={e => setPassword(e.target.value)}
                    placeholder="••••••••"
                    style={{ ...inputStyle, padding: '11px 38px 11px 13px' }}
                    onFocus={e => { e.target.style.borderColor = '#6366f1'; e.target.style.boxShadow = '0 0 0 3px rgba(99,102,241,0.12)' }}
                    onBlur={e =>  { e.target.style.borderColor = '#1a2540'; e.target.style.boxShadow = 'none' }}
                  />
                  <button
                    type="button" onClick={() => setShowPw(v => !v)}
                    style={{
                      all: 'unset', position: 'absolute', right: 11, top: '50%',
                      transform: 'translateY(-50%)', cursor: 'pointer', color: '#3d5280',
                      display: 'flex', alignItems: 'center',
                    }}
                  >
                    {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </div>

              {/* Submit */}
              <button
                type="submit" disabled={loading}
                style={{
                  width: '100%', padding: '11px', borderRadius: 8, border: 'none',
                  background: loading ? '#3a3d7a' : 'linear-gradient(135deg, #5f5fef, #818cf8)',
                  color: '#fff', fontSize: 14, fontWeight: 600,
                  cursor: loading ? 'not-allowed' : 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
                  marginTop: 6, transition: 'opacity 0.15s',
                }}
                onMouseEnter={e => { if (!loading) (e.currentTarget as HTMLButtonElement).style.opacity = '0.88' }}
                onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.opacity = '1' }}
              >
                {loading ? (
                  <>
                    <span style={{
                      width: 13, height: 13, border: '2px solid rgba(255,255,255,0.3)',
                      borderTopColor: '#fff', borderRadius: '50%',
                      animation: 'spin 0.7s linear infinite', display: 'inline-block',
                    }} />
                    Signing in…
                  </>
                ) : (
                  <>Sign in <ArrowRight size={13} /></>
                )}
              </button>
            </form>

          </div>

          {/* Footer */}
          <p style={{ textAlign: 'center', marginTop: 20, fontSize: 13, color: '#2e3f5c' }}>
            Don&apos;t have an account?{' '}
            <Link href="/signup" style={{ color: '#818cf8', textDecoration: 'none', fontWeight: 600 }}>
              Request access
            </Link>
          </p>

        </div>
      </div>
    </>
  )
}
