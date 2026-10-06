'use client'
import { useState, useEffect, useCallback } from 'react'
import {
  Users, Plus, Search, RefreshCw, Trash2, Edit2,
  CheckCircle2, XCircle, AlertTriangle, Clock, ChevronDown,
  X, Eye, EyeOff, Mail,
} from 'lucide-react'
import { getUser } from '@/lib/auth'
import {
  listAdminUsers, createAdminUser, updateAdminUser,
  deleteAdminUser, setUserStatus,
  resendVerification, listWarehouses, listCustomRoles,
  type AdminUser, type CustomRole,
} from '@/lib/api'
import type { Warehouse } from '@/lib/types'
import { WarehouseScope } from '@/components/users/WarehouseScope'
import { SsoSettings } from '@/components/users/SsoSettings'
import { IpAllowlistSettings } from '@/components/users/IpAllowlistSettings'
import { RolesPanel, CustomRoleSelect } from '@/components/users/RolesPanel'
import Card from '@/components/ui/Card'
import { thStyle } from '@/components/ui/Table'
import Input, { Field, Select } from '@/components/ui/Input'
import { useLanguage } from '@/contexts/LanguageContext'
import { roleLabel } from '@/lib/enumLabels'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import UsersMobile from './UsersMobile'
import { EmptyState } from '@/components/ui/States'

// ── Constants ────────────────────────────────────────────────────────────────

const ROLES = ['admin', 'analyst', 'viewer']

// Authorisation is by role only (admin / analyst / viewer). Per-user
// permission checkboxes existed here once and were removed (2026-10-02,
// owner's decision): they were saved but nothing enforced them.

const PERM_LABEL_KEY: Record<string, string> = {
  view_forecasts:      'users.perm_view_forecasts',
  run_training:        'users.perm_run_training',
  manage_sessions:     'users.perm_manage_sessions',
  export_data:         'users.perm_export_data',
  view_inventory:      'users.perm_view_inventory',
  manage_inventory:    'users.perm_manage_inventory',
  view_analysts:       'users.perm_view_analysts',
  run_analysts:        'users.perm_run_analysts',
  view_data_sources:   'users.perm_view_data_sources',
  manage_data_sources: 'users.perm_manage_data_sources',
  view_users:          'users.perm_view_users',
  manage_users:        'users.perm_manage_users',
}

const STATUS_META: Record<string, { labelKey: string; color: string; bg: string }> = {
  active:               { labelKey: 'users.status_active',    color: '#2E8B62', bg: 'rgba(46,139,98,0.1)'  },
  pending_confirmation: { labelKey: 'users.status_pending',   color: '#B7791F', bg: 'rgba(183,121,31,0.1)' },
  inactive:             { labelKey: 'users.status_inactive',  color: 'var(--dim)', bg: 'rgba(100,116,139,0.1)'},
  suspended:            { labelKey: 'users.status_suspended', color: '#C0504D', bg: 'rgba(192,80,77,0.1)'  },
}

// ── Small helpers ────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const { t } = useLanguage()
  const known = STATUS_META[status]
  const m = known ?? {
    labelKey: status.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
    color: 'var(--muted)',
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
      {t(m.labelKey)}
    </span>
  )
}

function RoleBadge({ role }: { role: string }) {
  const { t } = useLanguage()
  const color = role === 'admin' ? 'var(--accent)' : role === 'analyst' ? '#3E8E9B' : 'var(--muted)'
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 99,
      fontSize: 11, fontWeight: 600,
      color, background: color + '18',
    }}>
      {roleLabel(t, role)}
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

function fmtDate(iso: string | null, lang: 'es' | 'en') {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(lang === 'en' ? 'en-US' : 'es-CR', { month: 'short', day: 'numeric', year: 'numeric' })
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
          background: 'var(--surface)', border: '1px solid var(--border)',
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

// The modal's own label rhythm: lighter and one step smaller than the Field
// default, because these sit inside an already-titled dialog.
const MODAL_LABEL_STYLE: React.CSSProperties = {
  fontSize: 11, fontWeight: 500, marginBottom: 5,
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
      setError(err instanceof Error ? err.message : t('users.operation_failed'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal onClose={onClose}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', margin: 0 }}>
          {isCreate ? t('users.create_user') : t('users.edit_user_title')}
        </h2>
        <button onClick={onClose} aria-label={t('common.close')} style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)' }}>
          <X size={16} aria-hidden="true" />
        </button>
      </div>

      {error && (
        <div style={{
          display: 'flex', gap: 8, alignItems: 'center',
          padding: '9px 12px', borderRadius: 8, marginBottom: 16,
          background: 'rgba(192,80,77,0.08)', border: '1px solid rgba(192,80,77,0.2)',
          fontSize: 12, color: '#C0504D',
        }}>
          <AlertTriangle size={12} /> {error}
        </div>
      )}

      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <Field
          label={t('users.full_name_optional')}
          htmlFor="user-full-name"
          labelStyle={MODAL_LABEL_STYLE}
        >
          <Input
            size="lg"
            id="user-full-name" name="full_name"
            value={fullName} onChange={e => setFullName(e.target.value)}
            placeholder={t('users.full_name_placeholder')}
          />
        </Field>
        <Field
          label={<>{t('users.email_address')} {isCreate && <span style={{ color: '#C0504D' }}>*</span>}</>}
          htmlFor="user-email"
          labelStyle={MODAL_LABEL_STYLE}
        >
          <Input
            size="lg"
            id="user-email" name="email"
            type="email" required value={email} onChange={e => setEmail(e.target.value)}
            placeholder={t('users.email_placeholder')}
          />
          {!isCreate && email !== target?.email && (
            <p style={{ fontSize: 11, color: '#B7791F', marginTop: 4 }}>
              {t('users.email_reverify')}
            </p>
          )}
        </Field>
        <Field label={t('users.role')} htmlFor="user-role" labelStyle={MODAL_LABEL_STYLE}>
          <Select
            size="lg"
            id="user-role" name="role"
            value={role} onChange={e => setRole(e.target.value)}
          >
            {ROLES.map(r => (
              <option key={r} value={r} style={{ background: 'var(--surface-2)' }}>{roleLabel(t, r)}</option>
            ))}
          </Select>
        </Field>

        {isCreate && (
          <div style={{
            display: 'flex', gap: 8, padding: '10px 12px', borderRadius: 8,
            background: 'color-mix(in srgb, var(--accent) 8%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 20%, transparent)',
            fontSize: 12, color: '#a5b4fc',
          }}>
            <Mail size={12} style={{ marginTop: 1, flexShrink: 0 }} />
            {t('users.setup_email_note')}
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 4 }}>
          <button type="button" onClick={onClose} style={{
            padding: '8px 16px', borderRadius: 7, border: '1px solid var(--border)',
            background: 'transparent', color: 'var(--muted)', fontSize: 13, cursor: 'pointer',
          }}>
            {t('common.cancel')}
          </button>
          <button type="submit" disabled={loading} style={{
            padding: '8px 20px', borderRadius: 7, border: 'none',
            background: loading ? 'color-mix(in srgb, var(--accent) 70%, black)' : 'var(--accent)', color: '#fff',
            fontSize: 13, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer',
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            {loading && <Spinner />}
            {loading ? t('users.saving') : isCreate ? t('users.create_user') : t('users.save_changes')}
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
  const { t } = useLanguage()
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState<string | null>(null)

  async function handleDelete() {
    setLoading(true)
    try {
      await deleteAdminUser(target.id)
      onDeleted()
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('users.delete_failed'))
      setLoading(false)
    }
  }

  return (
    <Modal onClose={onClose}>
      <div style={{ textAlign: 'center' }}>
        <div style={{
          width: 48, height: 48, borderRadius: 12, margin: '0 auto 16px',
          background: 'rgba(192,80,77,0.1)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Trash2 size={20} color="#C0504D" />
        </div>
        <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', margin: '0 0 8px' }}>
          {t('users.delete_user_q')}
        </h2>
        <p style={{ fontSize: 13, color: 'var(--dim)', margin: '0 0 6px' }}>
          {t('users.delete_perm_prefix')}{' '}
          <strong style={{ color: 'var(--text)' }}>{target.full_name || target.email}</strong>.
        </p>
        <p style={{ fontSize: 12, color: 'var(--dim)', margin: '0 0 20px' }}>
          {t('users.delete_perm_detail')}
        </p>
        {error && (
          <div style={{
            padding: '9px 12px', borderRadius: 8, marginBottom: 14,
            background: 'rgba(192,80,77,0.08)', border: '1px solid rgba(192,80,77,0.2)',
            fontSize: 12, color: '#C0504D',
          }}>
            {error}
          </div>
        )}
        <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
          <button onClick={onClose} style={{
            padding: '8px 20px', borderRadius: 7, border: '1px solid var(--border)',
            background: 'transparent', color: 'var(--muted)', fontSize: 13, cursor: 'pointer',
          }}>
            {t('common.cancel')}
          </button>
          <button onClick={handleDelete} disabled={loading} style={{
            padding: '8px 20px', borderRadius: 7, border: 'none',
            background: loading ? '#7f1d1d' : '#C0504D', color: '#fff',
            fontSize: 13, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer',
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            {loading && <Spinner />}
            {loading ? t('users.deleting') : t('users.delete_user')}
          </button>
        </div>
      </div>
    </Modal>
  )
}

// ── Resend verification button ───────────────────────────────────────────────

function ResendButton({ userId, email }: { userId: string; email: string }) {
  const { t } = useLanguage()
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

  const color = state === 'sent' ? '#2E8B62' : state === 'error' ? '#C0504D' : '#B7791F'
  const title = state === 'sent' ? `${t('users.resend_sent_prefix')} ${email}` : state === 'error' ? t('users.resend_failed') : t('users.resend_title')

  return (
    <button
      onClick={handleResend}
      title={title}
      aria-label={title}
      disabled={state === 'loading' || state === 'sent'}
      style={{ all: 'unset', cursor: state === 'loading' ? 'wait' : state === 'sent' ? 'default' : 'pointer', color, padding: 5 }}
    >
      {state === 'loading' ? <Spinner /> : <Mail size={14} aria-hidden="true" />}
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
  const { t } = useLanguage()
  const [open,    setOpen]    = useState(false)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  const isSelf = target.id === currentUser?.id

  const OPTIONS = [
    { status: 'active',    labelKey: 'users.status_active' },
    { status: 'inactive',  labelKey: 'users.status_inactive' },
    { status: 'suspended', labelKey: 'users.status_suspended' },
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
      setError(e instanceof Error ? e.message : t('users.update_status_failed'))
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
          borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface-2)',
          color: 'var(--muted)', fontSize: 11, cursor: loading ? 'wait' : 'pointer',
        }}
        title={t('users.change_status')}
        aria-label={t('users.change_status')}
      >
        {loading ? <Spinner /> : <ChevronDown size={11} aria-hidden="true" />}
      </button>
      {error && (
        <div style={{
          position: 'absolute', top: '100%', right: 0, marginTop: 4,
          background: 'rgba(192,80,77,0.1)', border: '1px solid rgba(192,80,77,0.3)',
          borderRadius: 6, padding: '4px 8px', fontSize: 11, color: '#C0504D',
          whiteSpace: 'nowrap', zIndex: 10,
        }}>
          {error}
        </div>
      )}
      {open && (
        <div style={{
          position: 'absolute', right: 0, top: 28, zIndex: 20,
          background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8,
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
                  color: target.status === o.status ? m.color : 'var(--muted)',
                  fontSize: 12, cursor: 'pointer', textAlign: 'left',
                }}
              >
                {target.status === o.status && <CheckCircle2 size={10} color={m.color} />}
                {t(o.labelKey)}
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
  const { t, lang } = useLanguage()
  const narrow = useIsNarrow()
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
  const [loadError,  setLoadError]      = useState<string | null>(null)
  // The "Bodegas" control appears only when there is something to choose from.
  const [warehouses, setWarehouses]     = useState<Warehouse[]>([])

  useEffect(() => {
    let alive = true
    listWarehouses()
      .then(w => { if (alive) setWarehouses(w) })
      .catch(() => { if (alive) setWarehouses([]) })
    return () => { alive = false }
  }, [])

  // Custom roles (Rust-only routes). A failure leaves the list empty: the
  // per-person selector then simply does not appear, nothing else changes.
  const [customRoles, setCustomRoles]   = useState<CustomRole[]>([])
  const loadRoles = useCallback(() => {
    listCustomRoles().then(r => setCustomRoles(r.roles)).catch(() => setCustomRoles([]))
  }, [])
  useEffect(() => { loadRoles() }, [loadRoles])

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const res = await listAdminUsers({ search: search || undefined, status: filterStatus || undefined, role: filterRole || undefined, limit, offset })
      setUsers(res.items)
      setTotal(res.total)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : t('users.load_error'))
    } finally { setLoading(false) }
  }, [search, filterStatus, filterRole, limit, offset, t])

  useEffect(() => { load() }, [load])

  // Redirect non-admins
  if (currentUser?.role !== 'admin') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '60vh', gap: 12 }}>
        <XCircle size={32} color="#C0504D" />
        <p style={{ fontSize: 14, color: 'var(--dim)' }}>{t('users.no_permission')}</p>
      </div>
    )
  }

  // Phones get a list of cards with every action in a sheet — see UsersMobile.
  if (narrow) {
    return (
      <UsersMobile
        users={users} total={total} loading={loading} loadError={loadError}
        search={search} setSearch={setSearch}
        filterStatus={filterStatus} setFilterStatus={setFilterStatus}
        filterRole={filterRole} setFilterRole={setFilterRole}
        offset={offset} setOffset={setOffset} limit={limit}
        load={load} currentUser={currentUser}
      />
    )
  }

  const pages = Math.ceil(total / limit)
  const page  = Math.floor(offset / limit) + 1

  return (
    <div style={{ padding: '28px 32px', maxWidth: 1100, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        {/* The top bar already says "Usuarios": here only the count. */}
        <p style={{ fontSize: 12, color: 'var(--dim)', margin: 0 }}>
          {total} {total !== 1 ? t('users.user_plural') : t('users.user_singular')} {t('users.in_workspace')}
        </p>
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
            {t('common.refresh')}
          </button>
          <button
            onClick={() => setShowCreate(true)}
            data-tour="users.invite"
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '8px 16px', borderRadius: 7, border: 'none',
              background: 'var(--accent)', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer',
            }}
          >
            <Plus size={14} />
            {t('users.create_user')}
          </button>
        </div>
      </div>

      {/* Alone in the workspace: one step, not a table with one row. */}
      {!loading && total === 1 && users.length === 1 && users[0].id === currentUser?.id
        && !search && !filterStatus && !filterRole && (
        <div style={{ marginBottom: 16 }}>
          <EmptyState
            compact
            icon={<Users size={22} />}
            title={t('users.alone_title')}
            body={t('users.alone_body')}
            actions={[{ label: t('users.create_user'), icon: <Plus size={14} />, onClick: () => setShowCreate(true) }]}
          />
        </div>
      )}

      {/* Filters */}
      <div data-tour="users.filters" style={{
        display: 'flex', gap: 10, marginBottom: 16,
        background: 'var(--surface)', border: '1px solid var(--border)',
        borderRadius: 10, padding: '12px 16px', alignItems: 'center',
      }}>
        <div style={{ position: 'relative', flex: 1 }}>
          <Search size={13} color="var(--dim)" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)' }} />
          <input
            type="search" name="user_search"
            value={search} onChange={e => { setSearch(e.target.value); setOffset(0) }}
            placeholder={t('users.search_placeholder')}
            aria-label={t('users.search_placeholder')}
            style={{
              width: '100%', padding: '8px 10px 8px 30px', boxSizing: 'border-box',
              background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 7,
              color: 'var(--text)', fontSize: 12, outline: 'none',
            }}
          />
        </div>
        <Select
          size="md"
          name="filter_status"
          value={filterStatus} onChange={e => { setFilterStatus(e.target.value); setOffset(0) }}
          aria-label={t('users.col_status')}
          style={{ padding: '8px 10px', color: 'var(--dim)', fontSize: 12, width: 'auto' }}
        >
          <option value="">{t('users.all_statuses')}</option>
          {Object.entries(STATUS_META).map(([k, v]) => <option key={k} value={k}>{t(v.labelKey)}</option>)}
        </Select>
        <Select
          size="md"
          name="filter_role"
          value={filterRole} onChange={e => { setFilterRole(e.target.value); setOffset(0) }}
          aria-label={t('users.col_role')}
          style={{ padding: '8px 10px', color: 'var(--dim)', fontSize: 12, width: 'auto' }}
        >
          <option value="">{t('users.all_roles')}</option>
          {ROLES.map(r => <option key={r} value={r}>{roleLabel(t, r)}</option>)}
        </Select>
      </div>

      {/* Load error */}
      {loadError && (
        <div style={{
          marginBottom: 12, padding: '10px 16px', borderRadius: 8,
          background: 'rgba(192,80,77,0.08)', border: '1px solid rgba(192,80,77,0.2)',
          color: '#C0504D', fontSize: 13,
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <AlertTriangle size={14} />
          {loadError}
        </div>
      )}

      {/* Table */}
      <Card padding={0} overflow="hidden">
        {/* Table header */}
        {/* A CSS grid, not a <table> — so it borrows the header treatment as a
            style object. Same trick a virtualized row grid needs. */}
        <div style={{
          ...thStyle(),
          display: 'grid', gridTemplateColumns: '2fr 1.2fr 1fr 1fr 1fr auto',
          gap: 0, padding: '10px 16px',
        }}>
          <div>{t('users.col_name_email')}</div>
          <div>{t('users.col_status')}</div>
          <div>{t('users.col_role')}</div>
          <div>{t('users.col_created')}</div>
          <div>{t('users.col_last_login')}</div>
          <div style={{ textAlign: 'right' }}>{t('users.col_actions')}</div>
        </div>

        {loading ? (
          <div style={{ padding: '32px', textAlign: 'center', color: 'var(--dim)', fontSize: 13 }}>
            {t('users.loading')}
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
              background: u.id === currentUser?.id ? 'color-mix(in srgb, var(--accent) 3%, transparent)' : 'transparent',
            }}
          >
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                {u.full_name || '—'}
                {u.id === currentUser?.id && (
                  <span style={{ fontSize: 10, color: 'var(--accent)', marginLeft: 6, fontWeight: 400 }}>{t('users.you')}</span>
                )}
              </div>
              <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>{u.email}</div>
              {warehouses.length >= 2 && (
                <WarehouseScope user={u} warehouses={warehouses} onChanged={load} />
              )}
              {(customRoles.length > 0 || u.custom_role_id) && (
                <CustomRoleSelect user={u} roles={customRoles} disabled={u.id === currentUser?.id}
                  onChanged={() => { load(); loadRoles() }} />
              )}
            </div>
            <div data-tour={idx === 0 ? 'users.status' : undefined}><StatusBadge status={u.status} /></div>
            <div data-tour={idx === 0 ? 'users.role' : undefined}><RoleBadge role={u.role} /></div>
            <div style={{ fontSize: 12, color: 'var(--dim)' }}>{fmtDate(u.created_at, lang)}</div>
            <div style={{ fontSize: 12, color: 'var(--dim)' }}>{fmtDate(u.last_login_at, lang)}</div>
            <div data-tour={idx === 0 ? 'users.actions' : undefined} style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', alignItems: 'center' }}>
              <StatusDropdown user={u} currentUser={currentUser} onChanged={load} />
              {u.status === 'pending_confirmation' && (
                <ResendButton userId={u.id} email={u.email} />
              )}
              <button
                onClick={() => setEditUser(u)}
                title={t('users.edit_title')}
                aria-label={t('users.edit_title')}
                style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)', padding: 5 }}
              >
                <Edit2 size={14} aria-hidden="true" />
              </button>
              {u.id !== currentUser?.id && (
                <button
                  onClick={() => setDeleteUser(u)}
                  title={t('users.delete_title')}
                  aria-label={t('users.delete_title')}
                  style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)', padding: 5 }}
                >
                  <Trash2 size={14} aria-hidden="true" />
                </button>
              )}
            </div>
          </div>
        ))}
      </Card>

      {/* Pagination */}
      {pages > 1 && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 12 }}>
          <span style={{ fontSize: 12, color: 'var(--dim)' }}>
            {t('users.showing')} {offset + 1}–{Math.min(offset + limit, total)} {t('users.of')} {total}
          </span>
          <div style={{ display: 'flex', gap: 6 }}>
            {Array.from({ length: pages }, (_, i) => (
              <button
                key={i}
                onClick={() => setOffset(i * limit)}
                style={{
                  padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border)',
                  background: page === i + 1 ? 'var(--accent)' : 'transparent',
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

      <RolesPanel onChanged={() => { load(); loadRoles() }} />
      <SsoSettings />
      <IpAllowlistSettings />

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
    </div>
  )
}
