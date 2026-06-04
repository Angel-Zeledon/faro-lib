'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { authSignup } from '@/lib/api'
import { Zap, Eye, EyeOff, AlertTriangle, CheckCircle2 } from 'lucide-react'

function PasswordStrength({ password }: { password: string }) {
  const checks = [
    { label: 'At least 8 characters', ok: password.length >= 8 },
    { label: 'Uppercase letter',       ok: /[A-Z]/.test(password) },
    { label: 'Number',                 ok: /\d/.test(password) },
    { label: 'Special character',      ok: /[^A-Za-z0-9]/.test(password) },
  ]
  const score = checks.filter(c => c.ok).length
  const color = score < 2 ? '#ef4444' : score < 4 ? '#f59e0b' : '#22c55e'

  if (!password) return null
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: 'flex', gap: 3, marginBottom: 6 }}>
        {[0,1,2,3].map(i => (
          <div key={i} style={{ flex: 1, height: 3, borderRadius: 2, background: i < score ? color : '#1e2030', transition: 'background 0.2s' }} />
        ))}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {checks.map(({ label, ok }) => (
          <div key={label} style={{ display: 'flex', gap: 5, alignItems: 'center', fontSize: 10, color: ok ? '#22c55e' : '#64748b' }}>
            <CheckCircle2 size={9} />
            {label}
          </div>
        ))}
      </div>
    </div>
  )
}

export default function SignupPage() {
  const router = useRouter()
  const [form, setForm] = useState({
    email: '', password: '', full_name: '', tenant_name: '',
  })
  const [showPw,   setShowPw]   = useState(false)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const [done,     setDone]     = useState(false)

  function set(field: string, value: string) {
    setForm(f => ({ ...f, [field]: value }))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await authSignup({
        email:       form.email,
        password:    form.password,
        full_name:   form.full_name || undefined,
        tenant_name: form.tenant_name,
      })
      setDone(true)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Signup failed')
    } finally {
      setLoading(false)
    }
  }

  if (done) {
    return (
      <div style={{ width: '100%', maxWidth: 400, padding: '0 20px', textAlign: 'center' }}>
        <div style={{
          background: '#0f1015', border: '1px solid #1e2030',
          borderRadius: 14, padding: '40px 28px',
        }}>
          <CheckCircle2 size={40} color="#22c55e" style={{ margin: '0 auto 16px' }} />
          <h2 style={{ fontSize: 18, fontWeight: 700, color: '#e2e8f0', margin: '0 0 8px' }}>Check your email</h2>
          <p style={{ fontSize: 13, color: '#64748b', margin: '0 0 20px', lineHeight: 1.6 }}>
            We sent a verification link to <strong style={{ color: '#e2e8f0' }}>{form.email}</strong>.
            Click it to activate your account.
          </p>
          <Link href="/login" style={{
            display: 'inline-block', padding: '10px 24px',
            background: '#818cf8', color: '#fff', borderRadius: 8,
            fontSize: 13, fontWeight: 600, textDecoration: 'none',
          }}>
            Go to login
          </Link>
        </div>
      </div>
    )
  }

  const inputStyle = {
    width: '100%', padding: '10px 12px', boxSizing: 'border-box' as const,
    background: '#141520', border: '1px solid #1e2030', borderRadius: 8,
    color: '#e2e8f0', fontSize: 13, outline: 'none',
  }

  return (
    <div style={{ width: '100%', maxWidth: 420, padding: '0 20px' }}>
      {/* Logo */}
      <div style={{ textAlign: 'center', marginBottom: 28 }}>
        <div style={{
          width: 44, height: 44, borderRadius: 11, margin: '0 auto 10px',
          background: 'linear-gradient(135deg, #818cf8, #6366f1)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Zap size={20} color="#fff" strokeWidth={2.5} />
        </div>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: '#e2e8f0', margin: '0 0 4px', letterSpacing: '-0.03em' }}>
          Create your workspace
        </h1>
        <p style={{ fontSize: 12, color: '#64748b', margin: 0 }}>Faro — Inventario Inteligente</p>
      </div>

      <div style={{ background: '#0f1015', border: '1px solid #1e2030', borderRadius: 14, padding: '24px 28px' }}>
        {error && (
          <div style={{
            display: 'flex', gap: 8, alignItems: 'center',
            padding: '10px 14px', borderRadius: 8, marginBottom: 18,
            background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)',
            fontSize: 13, color: '#ef4444',
          }}>
            <AlertTriangle size={14} />
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={{ fontSize: 11, fontWeight: 500, color: '#94a3b8', display: 'block', marginBottom: 5 }}>
                Full name
              </label>
              <input
                type="text"
                value={form.full_name}
                onChange={e => set('full_name', e.target.value)}
                placeholder="Jane Smith"
                style={inputStyle}
                onFocus={e => (e.target.style.borderColor = '#818cf8')}
                onBlur={e => (e.target.style.borderColor = '#1e2030')}
              />
            </div>
            <div>
              <label style={{ fontSize: 11, fontWeight: 500, color: '#94a3b8', display: 'block', marginBottom: 5 }}>
                Company name <span style={{ color: '#ef4444' }}>*</span>
              </label>
              <input
                type="text"
                value={form.tenant_name}
                onChange={e => set('tenant_name', e.target.value)}
                required
                placeholder="Acme Corp"
                style={inputStyle}
                onFocus={e => (e.target.style.borderColor = '#818cf8')}
                onBlur={e => (e.target.style.borderColor = '#1e2030')}
              />
            </div>
          </div>

          <div>
            <label style={{ fontSize: 11, fontWeight: 500, color: '#94a3b8', display: 'block', marginBottom: 5 }}>
              Email address <span style={{ color: '#ef4444' }}>*</span>
            </label>
            <input
              type="email"
              value={form.email}
              onChange={e => set('email', e.target.value)}
              required
              placeholder="you@company.com"
              style={inputStyle}
              onFocus={e => (e.target.style.borderColor = '#818cf8')}
              onBlur={e => (e.target.style.borderColor = '#1e2030')}
            />
          </div>

          <div>
            <label style={{ fontSize: 11, fontWeight: 500, color: '#94a3b8', display: 'block', marginBottom: 5 }}>
              Password <span style={{ color: '#ef4444' }}>*</span>
            </label>
            <div style={{ position: 'relative' }}>
              <input
                type={showPw ? 'text' : 'password'}
                value={form.password}
                onChange={e => set('password', e.target.value)}
                required
                placeholder="Min. 8 characters"
                style={{ ...inputStyle, paddingRight: 36 }}
                onFocus={e => (e.target.style.borderColor = '#818cf8')}
                onBlur={e => (e.target.style.borderColor = '#1e2030')}
              />
              <button
                type="button"
                onClick={() => setShowPw(v => !v)}
                style={{
                  all: 'unset', position: 'absolute', right: 10, top: '50%',
                  transform: 'translateY(-50%)', cursor: 'pointer', color: '#64748b',
                }}
              >
                {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
            <PasswordStrength password={form.password} />
          </div>

          <button
            type="submit"
            disabled={loading}
            style={{
              width: '100%', padding: '11px', borderRadius: 8, border: 'none',
              background: loading ? '#4f56b0' : '#818cf8', color: '#fff',
              fontSize: 13, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer',
              transition: 'background 0.15s', marginTop: 4,
            }}
          >
            {loading ? 'Creating workspace…' : 'Create workspace'}
          </button>
        </form>

        <p style={{ textAlign: 'center', marginTop: 18, fontSize: 12, color: '#64748b' }}>
          Already have an account?{' '}
          <Link href="/login" style={{ color: '#818cf8', textDecoration: 'none', fontWeight: 500 }}>
            Sign in
          </Link>
        </p>
      </div>
    </div>
  )
}
