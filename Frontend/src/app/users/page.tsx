'use client'
import { useState, useEffect, useCallback } from 'react'
import {
  Users, Plus, Search, RefreshCw, Trash2, Edit2, ShieldCheck,
  CheckCircle2, XCircle, AlertTriangle, Clock, ChevronDown,
  X, Eye, EyeOff, Mail,
} from 'lucide-react'
import { getUser } from '@/lib/auth'
import {
  listAdminUsers, createAdminUser, updateAdminUser,
  deleteAdminUser, setUserStatus,
  getUserPermissions, setUserPermissions,
  resendVerification,
  type AdminUser,
} from '@/lib/api'
import { useLanguage } from '@/contexts/LanguageContext'

// ── Constants ────────────────────────────────────────────────────────────────

const ROLES = ['admin', 'analyst', 'viewer']

const PERMISSION_GROUPS: { label: string; perms: string[] }[] = [
  { label: 'Forecasting', perms: ['view_forecasts', 'run_training', 'manage_sessions', 'export_data'] },
  { label: 'Inventory',   perms: ['view_inventory', 'manage_inventory'] },
  { label: 'AI Analyst',  perms: ['view_analysts', 'run_analysts'] },
  { label: 'Data',        perms: ['view_data_sources', 'manage_data_sources'] },
  { label: 'Admin',       perms: ['view_users', 'manage_users'] },
]

const PERM_LABEL: Record<string, string> = {
  view_forecasts:      'View forecasts',
  run_training:        'Run training',
  manage_sessions:     'Manage sessions',
  export_data:         'Export data',
  view_inventory:      'View inventory',
  manage_inventory:    'Manage inventory',
  view_analysts:       'View AI analyst',
  run_analysts:        'Run AI analyst',
  view_data_sources:   'View data sources',
  manage_data_sources: 'Manage data sources',
  view_users:          'View users',
  manage_users:        'Manage users',
}

const STATUS_META: Record<string, { label: string; color: string; bg: string }> = {
  active:               { label: 'Active',              color: '#22c55e', bg: 'rgba(34,197,94,0.1)'  },
  pending_confirmation: { label: 'Pending',             color: '#f59e0b', bg: 'rgba(245,158,11,0.1)' },
  inactive:             { label: 'Inactive',            color: '#64748b', bg: 'rgba(100,116,139,0.1)'},
  suspended:            { label: 'Suspended',           color: '#ef4444', bg: 'rgba(239,68,68,0.1)'  },
}

// ── Small helpers ────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const m = STATUS_META[status] ?? {
    label: status.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
    color: '#94a3b8',
    bg: 'rgba(148,163,184,0.1)',
  }
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 9px', borderRadius: 99, fontSize: 11, fontWeight: 600,
      color: m.color, background: m.bg,
    }}>
      {status === 'active'   && <CheckCircle2 size={10} />}
      {status === 'suspended'&& <XCircle size={10} />}
      {status === 'pending_confirmation' && <Clock size={10} />}
      {m.label}
    </span>
  )
}

function RoleBadge({ role }: { role: string }) {
  const color = role === 'admin' ? '#818cf8' : role === 'analyst' ? '#06b6d4' : '#94a3b8'
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 99,
      fontSize: 11, fontWeight: 600, textTransform: 'capitalize',
      color, background: color + '18',
    }}>
      {role}
    </span>
  )
}

function Spinner() {
  return (
    <div style={{
      width: 14, height: 14, border: '2px solid rgba(255,255,255,0.2)',
      borderTopColor: '#fff', borderRadius: '50%',
      animation: 'spin 0.7s linear infinite', display: 'inline-block',
    }} />
  )
}

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

// ── Modal wrapper ────────────────────────────────────────────────────────────

function Modal({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 50,
      background: 'rgba(0,0,0,0.65)', backdropFilter: 'blur(3px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }} onClick={onClose}>
      <div
        style={{
          background: '#0f1015', border: '1px solid #1e2030',
          borderRadius: 14, padding: '28px', width: '100%', maxWidth: 480,
          maxHeight: '90vh', overflowY: 'auto',
        }}
        onClick={e => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  width: '100%', padding: '9px 12px', boxSizing: 'border-box',
  background: '#141520', border: '1px solid #1e2030', borderRadius: 8,
  color: '#e2e8f0', fontSize: 13, outline: 'none',
}

// ── Create/Edit Modal ────────────────────────────────────────────────────────

function UserFormModal({
  user: target,
  onClose,
  onSaved,
}: {
  user: AdminUser | null
  onClose: () => void
  onSaved: () => void
}) {
  const { t } = useLanguage()
  const isCreate = !target
  const [fullName, setFullName] = useState(target?.full_name ?? '')
  const [email,    setEmail]    = useState(target?.email ?? '')
  const [role,     setRole]     = useState(target?.role ?? 'analyst')
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      if (isCreate) {
        await createAdminUser({ email, role, full_name: fullName || undefined })
      } else {
        await updateAdminUser(target.id, {
          full_name: fullName || undefined,
          role,
          email: email !== target.email ? email : undefined,
        })
      }
      onSaved()
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Operation failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal onClose={onClose}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, color: '#e2e8f0', margin: 0 }}>
          {isCreate ? 'Create user' : 'Edit user'}
        </h2>
        <button onClick={onClose} aria-label={t('common.close')} style={{ all: 'unset', cursor: 'pointer', color: '#64748b' }}>
          <X size={16} aria-hidden="true" />
        </button>
      </div>

      {error && (
        <div style={{
          display: 'flex', gap: 8, alignItems: 'center',
          padding: '9px 12px', borderRadius: 8, marginBottom: 16,
          background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)',
          fontSize: 12, color: '#ef4444',
        }}>
          <AlertTriangle size={12} /> {error}
        </div>
      )}

      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div>
          <label style={{ fontSize: 11, fontWeight: 500, color: '#94a3b8', display: 'block', marginBottom: 5 }}>
            Full name (optional)
          </label>
          <input
            value={fullName} onChange={e => setFullName(e.target.value)}
            placeholder="Jane Doe" style={inputStyle}
            onFocus={e => (e.target.style.borderColor = '#818cf8')}
            onBlur={e => (e.target.style.borderColor = '#1e2030')}
          />
        </div>
        <div>
          <label style={{ fontSize: 11, fontWeight: 500, color: '#94a3b8', display: 'block', marginBottom: 5 }}>
            Email address {isCreate && <span style={{ color: '#ef4444' }}>*</span>}
          </label>
          <input
            type="email" required value={email} onChange={e => setEmail(e.target.value)}
            placeholder="user@company.com" style={inputStyle}
            onFocus={e => (e.target.style.borderColor = '#818cf8')}
            onBlur={e => (e.target.style.borderColor = '#1e2030')}
          />
          {!isCreate && email !== target?.email && (
            <p style={{ fontSize: 11, color: '#f59e0b', marginTop: 4 }}>
              Changing the email will require re-verification.
            </p>
          )}
        </div>
        <div>
          <label style={{ fontSize: 11, fontWeight: 500, color: '#94a3b8', display: 'block', marginBottom: 5 }}>
            Role
          </label>
          <select
            value={role} onChange={e => setRole(e.target.value)}
            style={{ ...inputStyle, cursor: 'pointer' }}
            onFocus={e => (e.target.style.borderColor = '#818cf8')}
            onBlur={e => (e.target.style.borderColor = '#1e2030')}
          >
            {ROLES.map(r => <option key={r} value={r} style={{ background: '#141520', textTransform: 'capitalize' }}>{r}</option>)}
          </select>
        </div>

        {isCreate && (
          <div style={{
            display: 'flex', gap: 8, padding: '10px 12px', borderRadius: 8,
            background: 'rgba(129,140,248,0.08)', border: '1px solid rgba(129,140,248,0.2)',
            fontSize: 12, color: '#a5b4fc',
          }}>
            <Mail size={12} style={{ marginTop: 1, flexShrink: 0 }} />
            An account setup email with a verification link will be sent to the user.
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 4 }}>
          <button type="button" onClick={onClose} style={{
            padding: '8px 16px', borderRadius: 7, border: '1px solid #1e2030',
            background: 'transparent', color: '#94a3b8', fontSize: 13, cursor: 'pointer',
          }}>
            Cancel
          </button>
          <button type="submit" disabled={loading} style={{
            padding: '8px 20px', borderRadius: 7, border: 'none',
            background: loading ? '#4f56b0' : '#818cf8', color: '#fff',
            fontSize: 13, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer',
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            {loading && <Spinner />}
            {loading ? 'Saving…' : isCreate ? 'Create user' : 'Save changes'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

// ── Delete confirmation modal ─────────────────────────────────────────────────

function DeleteModal({
  user: target,
  onClose,
  onDeleted,
}: {
  user: AdminUser
  onClose: () => void
  onDeleted: () => void
}) {
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState<string | null>(null)

  async function handleDelete() {
    setLoading(true)
    try {
      await deleteAdminUser(target.id)
      onDeleted()
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Delete failed')
      setLoading(false)
    }
  }

  return (
    <Modal onClose={onClose}>
      <div style={{ textAlign: 'center' }}>
        <div style={{
          width: 48, height: 48, borderRadius: 12, margin: '0 auto 16px',
          background: 'rgba(239,68,68,0.1)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Trash2 size={20} color="#ef4444" />
        </div>
        <h2 style={{ fontSize: 16, fontWeight: 700, color: '#e2e8f0', margin: '0 0 8px' }}>
          Delete user?
        </h2>
        <p style={{ fontSize: 13, color: '#64748b', margin: '0 0 6px' }}>
          This will permanently delete{' '}
          <strong style={{ color: '#e2e8f0' }}>{target.full_name || target.email}</strong>.
        </p>
        <p style={{ fontSize: 12, color: '#64748b', margin: '0 0 20px' }}>
          All their sessions, tokens, and permissions will be removed. This cannot be undone.
        </p>
        {error && (
          <div style={{
            padding: '9px 12px', borderRadius: 8, marginBottom: 14,
            background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)',
            fontSize: 12, color: '#ef4444',
          }}>
            {error}
          </div>
        )}
        <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
          <button onClick={onClose} style={{
            padding: '8px 20px', borderRadius: 7, border: '1px solid #1e2030',
            background: 'transparent', color: '#94a3b8', fontSize: 13, cursor: 'pointer',
          }}>
            Cancel
          </button>
          <button onClick={handleDelete} disabled={loading} style={{
            padding: '8px 20px', borderRadius: 7, border: 'none',
            background: loading ? '#7f1d1d' : '#ef4444', color: '#fff',
            fontSize: 13, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer',
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            {loading && <Spinner />}
            {loading ? 'Deleting…' : 'Delete user'}
          </button>
        </div>
      </div>
    </Modal>
  )
}

// ── Permissions Modal ─────────────────────────────────────────────────────────

function PermissionsModal({
  user: target,
  onClose,
}: {
  user: AdminUser
  onClose: () => void
}) {
  const { t } = useLanguage()
  const [perms,   setPerms]   = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [saving,  setSaving]  = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  useEffect(() => {
    getUserPermissions(target.id)
      .then(res => { setPerms(res.permissions); setLoading(false) })
      .catch(() => { setError('Failed to load permissions'); setLoading(false) })
  }, [target.id])

  function toggle(perm: string) {
    setPerms(prev => prev.includes(perm) ? prev.filter(p => p !== perm) : [...prev, perm])
  }

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      await setUserPermissions(target.id, perms)
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal onClose={onClose}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 15, fontWeight: 700, color: '#e2e8f0', margin: '0 0 2px' }}>
            Permissions
          </h2>
          <p style={{ fontSize: 11, color: '#64748b', margin: 0 }}>
            {target.full_name || target.email} · <span style={{ textTransform: 'capitalize' }}>{target.role}</span>
          </p>
        </div>
        <button onClick={onClose} aria-label={t('common.close')} style={{ all: 'unset', cursor: 'pointer', color: '#64748b' }}>
          <X size={16} aria-hidden="true" />
        </button>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 24, color: '#64748b', fontSize: 13 }}>Loading…</div>
      ) : (
        <>
          {error && (
            <div style={{
              padding: '9px 12px', borderRadius: 8, marginBottom: 12,
              background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)',
              fontSize: 12, color: '#ef4444',
            }}>
              {error}
            </div>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {PERMISSION_GROUPS.map(group => (
              <div key={group.label}>
                <div style={{ fontSize: 10, fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8 }}>
                  {group.label}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {group.perms.map(perm => (
                    <label key={perm} style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        checked={perms.includes(perm)}
                        onChange={() => toggle(perm)}
                        style={{ accentColor: '#818cf8', width: 14, height: 14, cursor: 'pointer' }}
                      />
                      <span style={{ fontSize: 13, color: '#e2e8f0' }}>{PERM_LABEL[perm] ?? perm}</span>
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 20 }}>
            <button onClick={onClose} style={{
              padding: '8px 16px', borderRadius: 7, border: '1px solid #1e2030',
              background: 'transparent', color: '#94a3b8', fontSize: 13, cursor: 'pointer',
            }}>
              Cancel
            </button>
            <button onClick={handleSave} disabled={saving} style={{
              padding: '8px 20px', borderRadius: 7, border: 'none',
              background: saving ? '#4f56b0' : '#818cf8', color: '#fff',
              fontSize: 13, fontWeight: 600, cursor: saving ? 'not-allowed' : 'pointer',
              display: 'flex', alignItems: 'center', gap: 6,
            }}>
              {saving && <Spinner />}
              {saving ? 'Saving…' : 'Save permissions'}
            </button>
          </div>
        </>
      )}
    </Modal>
  )
}

// ── Resend verification button ───────────────────────────────────────────────

function ResendButton({ userId, email }: { userId: string; email: string }) {
  const [state, setState] = useState<'idle' | 'loading' | 'sent' | 'error'>('idle')

  async function handleResend() {
    if (state === 'loading' || state === 'sent') return
    setState('loading')
    try {
      await resendVerification(userId)
      setState('sent')
      setTimeout(() => setState('idle'), 3000)
    } catch {
      setState('error')
      setTimeout(() => setState('idle'), 3000)
    }
  }

  const color = state === 'sent' ? '#22c55e' : state === 'error' ? '#ef4444' : '#f59e0b'
  const title = state === 'sent' ? `Sent to ${email}` : state === 'error' ? 'Failed — check SMTP config' : 'Resend verification email'

  return (
    <button
      onClick={handleResend}
      title={title}
      disabled={state === 'loading' || state === 'sent'}
      style={{ all: 'unset', cursor: state === 'loading' ? 'wait' : state === 'sent' ? 'default' : 'pointer', color, padding: 5 }}
    >
      {state === 'loading' ? <Spinner /> : <Mail size={14} />}
    </button>
  )
}

// ── Status change dropdown ────────────────────────────────────────────────────

function StatusDropdown({
  user: target,
  currentUser,
  onChanged,
}: {
  user: AdminUser
  currentUser: ReturnType<typeof getUser>
  onChanged: () => void
}) {
  const [open,    setOpen]    = useState(false)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  const isSelf = target.id === currentUser?.id

  const OPTIONS = [
    { status: 'active',    label: 'Active' },
    { status: 'inactive',  label: 'Inactive' },
    { status: 'suspended', label: 'Suspended' },
  ]

  async function pick(s: string) {
    setOpen(false)
    if (s === target.status) return
    setLoading(true)
    setError(null)
    try {
      await setUserStatus(target.id, s)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update status')
      setTimeout(() => setError(null), 4000)
    } finally { setLoading(false) }
  }

  if (isSelf) return null

  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(v => !v)}
        disabled={loading}
        style={{
          display: 'flex', alignItems: 'center', gap: 4, padding: '5px 9px',
          borderRadius: 6, border: '1px solid #1e2030', background: '#141520',
          color: '#94a3b8', fontSize: 11, cursor: loading ? 'wait' : 'pointer',
        }}
        title="Change status"
      >
        {loading ? <Spinner /> : <ChevronDown size={11} />}
      </button>
      {error && (
        <div style={{
          position: 'absolute', top: '100%', right: 0, marginTop: 4,
          background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)',
          borderRadius: 6, padding: '4px 8px', fontSize: 11, color: '#ef4444',
          whiteSpace: 'nowrap', zIndex: 10,
        }}>
          {error}
        </div>
      )}
      {open && (
        <div style={{
          position: 'absolute', right: 0, top: 28, zIndex: 20,
          background: '#0f1015', border: '1px solid #1e2030', borderRadius: 8,
          minWidth: 130, boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
        }}>
          {OPTIONS.map(o => {
            const m = STATUS_META[o.status]
            return (
              <button
                key={o.status}
                onClick={() => pick(o.status)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  width: '100%', padding: '8px 12px', border: 'none',
                  background: target.status === o.status ? m.bg : 'transparent',
                  color: target.status === o.status ? m.color : '#94a3b8',
                  fontSize: 12, cursor: 'pointer', textAlign: 'left',
                }}
              >
                {target.status === o.status && <CheckCircle2 size={10} color={m.color} />}
                {o.label}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Main Page ────────────────────────────────────────────────────────────────

export default function UsersPage() {
  const { t } = useLanguage()
  const currentUser = getUser()

  const [users,        setUsers]        = useState<AdminUser[]>([])
  const [total,        setTotal]        = useState(0)
  const [loading,      setLoading]      = useState(true)
  const [search,       setSearch]       = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [filterRole,   setFilterRole]   = useState('')
  const [offset,       setOffset]       = useState(0)
  const limit = 20

  const [showCreate, setShowCreate]     = useState(false)
  const [editUser,   setEditUser]       = useState<AdminUser | null>(null)
  const [deleteUser, setDeleteUser]     = useState<AdminUser | null>(null)
  const [permsUser,  setPermsUser]      = useState<AdminUser | null>(null)
  const [loadError,  setLoadError]      = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const res = await listAdminUsers({ search: search || undefined, status: filterStatus || undefined, role: filterRole || undefined, limit, offset })
      setUsers(res.items)
      setTotal(res.total)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : 'Failed to load users')
    } finally { setLoading(false) }
  }, [search, filterStatus, filterRole, limit, offset])

  useEffect(() => { load() }, [load])

  // Redirect non-admins
  if (currentUser?.role !== 'admin') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '60vh', gap: 12 }}>
        <XCircle size={32} color="#ef4444" />
        <p style={{ fontSize: 14, color: '#64748b' }}>You don&apos;t have permission to view this page.</p>
      </div>
    )
  }

  const pages = Math.ceil(total / limit)
  const page  = Math.floor(offset / limit) + 1

  return (
    <div style={{ padding: '28px 32px', maxWidth: 1100, margin: '0 auto' }}>
      <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{
            width: 40, height: 40, borderRadius: 10,
            background: 'rgba(129,140,248,0.12)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <Users size={18} color="#818cf8" />
          </div>
          <div>
            <h1 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: 0, letterSpacing: '-0.02em' }}>
              Users
            </h1>
            <p style={{ fontSize: 12, color: 'var(--dim)', margin: 0 }}>
              {total} user{total !== 1 ? 's' : ''} in this workspace
            </p>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={load}
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '8px 14px', borderRadius: 7, border: '1px solid var(--border)',
              background: 'transparent', color: 'var(--dim)', fontSize: 12, cursor: 'pointer',
            }}
          >
            <RefreshCw size={12} />
            Refresh
          </button>
          <button
            onClick={() => setShowCreate(true)}
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '8px 16px', borderRadius: 7, border: 'none',
              background: '#818cf8', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer',
            }}
          >
            <Plus size={14} />
            Create user
          </button>
        </div>
      </div>

      {/* Filters */}
      <div style={{
        display: 'flex', gap: 10, marginBottom: 16,
        background: 'var(--surface)', border: '1px solid var(--border)',
        borderRadius: 10, padding: '12px 16px', alignItems: 'center',
      }}>
        <div style={{ position: 'relative', flex: 1 }}>
          <Search size={13} color="#64748b" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)' }} />
          <input
            value={search} onChange={e => { setSearch(e.target.value); setOffset(0) }}
            placeholder="Search by name or email…"
            style={{
              width: '100%', padding: '8px 10px 8px 30px', boxSizing: 'border-box',
              background: '#141520', border: '1px solid var(--border)', borderRadius: 7,
              color: 'var(--text)', fontSize: 12, outline: 'none',
            }}
          />
        </div>
        <select
          value={filterStatus} onChange={e => { setFilterStatus(e.target.value); setOffset(0) }}
          style={{ padding: '8px 10px', background: '#141520', border: '1px solid var(--border)', borderRadius: 7, color: 'var(--dim)', fontSize: 12, cursor: 'pointer', outline: 'none' }}
        >
          <option value="">All statuses</option>
          {Object.entries(STATUS_META).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <select
          value={filterRole} onChange={e => { setFilterRole(e.target.value); setOffset(0) }}
          style={{ padding: '8px 10px', background: '#141520', border: '1px solid var(--border)', borderRadius: 7, color: 'var(--dim)', fontSize: 12, cursor: 'pointer', outline: 'none' }}
        >
          <option value="">All roles</option>
          {ROLES.map(r => <option key={r} value={r} style={{ textTransform: 'capitalize' }}>{r}</option>)}
        </select>
      </div>

      {/* Load error */}
      {loadError && (
        <div style={{
          marginBottom: 12, padding: '10px 16px', borderRadius: 8,
          background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)',
          color: '#ef4444', fontSize: 13,
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <AlertTriangle size={14} />
          {loadError}
        </div>
      )}

      {/* Table */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
        {/* Table header */}
        <div style={{
          display: 'grid', gridTemplateColumns: '2fr 1.2fr 1fr 1fr 1fr auto',
          gap: 0, padding: '10px 16px',
          borderBottom: '1px solid var(--border)',
          fontSize: 11, fontWeight: 600, color: 'var(--dim)',
          textTransform: 'uppercase', letterSpacing: '0.06em',
        }}>
          <div>Name / Email</div>
          <div>Status</div>
          <div>Role</div>
          <div>Created</div>
          <div>Last login</div>
          <div style={{ textAlign: 'right' }}>Actions</div>
        </div>

        {loading ? (
          <div style={{ padding: '32px', textAlign: 'center', color: 'var(--dim)', fontSize: 13 }}>
            Loading users…
          </div>
        ) : users.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', color: 'var(--dim)', fontSize: 13 }}>
            {t('users.no_users_found')}
          </div>
        ) : users.map((u, idx) => (
          <div
            key={u.id}
            style={{
              display: 'grid', gridTemplateColumns: '2fr 1.2fr 1fr 1fr 1fr auto',
              gap: 0, padding: '12px 16px', alignItems: 'center',
              borderBottom: idx < users.length - 1 ? '1px solid var(--border)' : 'none',
              background: u.id === currentUser?.id ? 'rgba(129,140,248,0.03)' : 'transparent',
            }}
          >
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                {u.full_name || '—'}
                {u.id === currentUser?.id && (
                  <span style={{ fontSize: 10, color: '#818cf8', marginLeft: 6, fontWeight: 400 }}>(you)</span>
                )}
              </div>
              <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>{u.email}</div>
            </div>
            <div><StatusBadge status={u.status} /></div>
            <div><RoleBadge role={u.role} /></div>
            <div style={{ fontSize: 12, color: 'var(--dim)' }}>{fmtDate(u.created_at)}</div>
            <div style={{ fontSize: 12, color: 'var(--dim)' }}>{fmtDate(u.last_login_at)}</div>
            <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', alignItems: 'center' }}>
              <StatusDropdown user={u} currentUser={currentUser} onChanged={load} />
              {u.status === 'pending_confirmation' && (
                <ResendButton userId={u.id} email={u.email} />
              )}
              <button
                onClick={() => setPermsUser(u)}
                title="Permissions"
                style={{ all: 'unset', cursor: 'pointer', color: '#64748b', padding: 5 }}
              >
                <ShieldCheck size={14} />
              </button>
              <button
                onClick={() => setEditUser(u)}
                title="Edit"
                style={{ all: 'unset', cursor: 'pointer', color: '#64748b', padding: 5 }}
              >
                <Edit2 size={14} />
              </button>
              {u.id !== currentUser?.id && (
                <button
                  onClick={() => setDeleteUser(u)}
                  title="Delete"
                  style={{ all: 'unset', cursor: 'pointer', color: '#64748b', padding: 5 }}
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 12 }}>
          <span style={{ fontSize: 12, color: 'var(--dim)' }}>
            Showing {offset + 1}–{Math.min(offset + limit, total)} of {total}
          </span>
          <div style={{ display: 'flex', gap: 6 }}>
            {Array.from({ length: pages }, (_, i) => (
              <button
                key={i}
                onClick={() => setOffset(i * limit)}
                style={{
                  padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border)',
                  background: page === i + 1 ? '#818cf8' : 'transparent',
                  color: page === i + 1 ? '#fff' : 'var(--dim)',
                  fontSize: 12, cursor: 'pointer',
                }}
              >
                {i + 1}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Modals */}
      {showCreate && (
        <UserFormModal user={null} onClose={() => setShowCreate(false)} onSaved={load} />
      )}
      {editUser && (
        <UserFormModal user={editUser} onClose={() => setEditUser(null)} onSaved={load} />
      )}
      {deleteUser && (
        <DeleteModal user={deleteUser} onClose={() => setDeleteUser(null)} onDeleted={load} />
      )}
      {permsUser && (
        <PermissionsModal user={permsUser} onClose={() => setPermsUser(null)} />
      )}
    </div>
  )
}
