'use client'
/**
 * /usuarios on a phone. The desktop screen is a six-column grid with icon-only
 * actions 24px wide; at 360px it ran 20px off the edge and every action was a
 * miss-tap. Here each person is a card (name, email, status, role), and every
 * action — status, resend, edit, delete — lives in the person's sheet.
 *
 * Data and filters stay owned by the page (`app/usuarios/page.tsx`); this file
 * only renders them and runs the same API calls the desktop modals run.
 */
import { useEffect, useRef, useState } from 'react'
import { Plus, Search, RefreshCw, Mail, AlertTriangle, CheckCircle2, Users, ChevronLeft, ChevronRight } from 'lucide-react'
import {
  createAdminUser, updateAdminUser, deleteAdminUser, setUserStatus, resendVerification,
  type AdminUser,
} from '@/lib/api'
import type { getUser } from '@/lib/auth'
import { useLanguage } from '@/contexts/LanguageContext'
import { roleLabel } from '@/lib/enumLabels'
import {
  BottomSheet, MobileList, MobileCard, StickyActionBar, type StatusTone,
} from '@/components/mobile'
import MobileFormScope from '@/components/mobile/MobileFormScope'
import Input, { Field, Select } from '@/components/ui/Input'

const ROLES = ['admin', 'analyst', 'viewer']

const STATUS: Record<string, { labelKey: string; tone: StatusTone }> = {
  active:               { labelKey: 'users.status_active',    tone: 'success' },
  pending_confirmation: { labelKey: 'users.status_pending',   tone: 'warning' },
  inactive:             { labelKey: 'users.status_inactive',  tone: 'neutral' },
  suspended:            { labelKey: 'users.status_suspended', tone: 'danger' },
}
const SETTABLE_STATUSES = ['active', 'inactive', 'suspended']

function initials(u: AdminUser): string {
  return (u.full_name || u.email || '?')
    .split(/[\s@.]+/).filter(Boolean).map(w => w[0]).slice(0, 2).join('').toUpperCase()
}

function fmtDate(iso: string | null, lang: 'es' | 'en') {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(lang === 'en' ? 'en-US' : 'es-CR', { month: 'short', day: 'numeric', year: 'numeric' })
}

function ErrorLine({ text }: { text: string }) {
  return (
    <div role="alert" style={{
      display: 'flex', gap: 8, alignItems: 'flex-start',
      padding: '10px 12px', borderRadius: 10, marginBottom: 14,
      background: 'rgba(192,80,77,0.08)', border: '1px solid rgba(192,80,77,0.2)',
      fontSize: 13, color: '#C0504D', lineHeight: 1.45,
    }}>
      <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 2 }} aria-hidden="true" /> {text}
    </div>
  )
}

export interface UsersMobileProps {
  users: AdminUser[]
  total: number
  loading: boolean
  loadError: string | null
  search: string
  setSearch: (v: string) => void
  filterStatus: string
  setFilterStatus: (v: string) => void
  filterRole: string
  setFilterRole: (v: string) => void
  offset: number
  setOffset: (v: number) => void
  limit: number
  load: () => void
  currentUser: ReturnType<typeof getUser>
}

type SheetState =
  | { kind: 'none' }
  | { kind: 'detail'; user: AdminUser }
  | { kind: 'form'; user: AdminUser | null }
  | { kind: 'delete'; user: AdminUser }

export default function UsersMobile(p: UsersMobileProps) {
  const { t, lang } = useLanguage()
  const [sheet, setSheet] = useState<SheetState>({ kind: 'none' })
  const close = () => setSheet({ kind: 'none' })

  // The detail sheet shows the row as it is NOW: after a status change the
  // list reloads, and the sheet must not keep showing the old badge.
  const detailUser = sheet.kind === 'detail'
    ? (p.users.find(u => u.id === sheet.user.id) ?? sheet.user)
    : null

  const pages = Math.ceil(p.total / p.limit)
  const page = Math.floor(p.offset / p.limit) + 1

  return (
    <MobileFormScope>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ fontSize: 13, color: 'var(--muted)', padding: '0 4px' }}>
          {p.total} {p.total !== 1 ? t('users.user_plural') : t('users.user_singular')} {t('users.in_workspace')}
        </div>

        {/* Search + refresh */}
        <div style={{ display: 'flex', gap: 8 }}>
          <div style={{ position: 'relative', flex: 1, minWidth: 0 }}>
            <Search size={16} color="var(--dim)" aria-hidden="true"
                    style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
            <input
              type="search" name="user_search" enterKeyHint="search"
              value={p.search}
              onChange={e => { p.setSearch(e.target.value); p.setOffset(0) }}
              placeholder={t('users.search_placeholder')}
              aria-label={t('users.search_placeholder')}
              style={{
                width: '100%', padding: '10px 12px 10px 36px', boxSizing: 'border-box',
                background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12,
                color: 'var(--text)', outline: 'none',
              }}
            />
          </div>
          <button
            type="button" onClick={p.load} aria-label={t('common.refresh')}
            className="tap-feedback"
            style={{
              all: 'unset', boxSizing: 'border-box', cursor: 'pointer', flexShrink: 0,
              width: 44, height: 44, borderRadius: 12, border: '1px solid var(--border)',
              background: 'var(--surface)', color: 'var(--muted)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            <RefreshCw size={16} aria-hidden="true" />
          </button>
        </div>

        {/* Filters: native pickers — the phone's own wheel/list is the right control. */}
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', gap: 8 }}>
          <Select
            name="filter_status" chevron
            value={p.filterStatus}
            onChange={e => { p.setFilterStatus(e.target.value); p.setOffset(0) }}
            aria-label={t('users.col_status')}
            style={{ width: '100%', borderRadius: 12, background: 'var(--surface)' }}
          >
            <option value="">{t('users.all_statuses')}</option>
            {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{t(v.labelKey)}</option>)}
          </Select>
          <Select
            name="filter_role" chevron
            value={p.filterRole}
            onChange={e => { p.setFilterRole(e.target.value); p.setOffset(0) }}
            aria-label={t('users.col_role')}
            style={{ width: '100%', borderRadius: 12, background: 'var(--surface)' }}
          >
            <option value="">{t('users.all_roles')}</option>
            {ROLES.map(r => <option key={r} value={r}>{roleLabel(t, r)}</option>)}
          </Select>
        </div>

        {p.loadError && (
          <div role="alert" style={{
            padding: '12px 14px', borderRadius: 12,
            background: 'rgba(192,80,77,0.08)', border: '1px solid rgba(192,80,77,0.2)',
            color: '#C0504D', fontSize: 14, display: 'flex', flexDirection: 'column', gap: 10,
          }}>
            <span style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
              <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 1 }} aria-hidden="true" />{p.loadError}
            </span>
            <button type="button" className="mobile-btn mobile-btn-secondary" onClick={p.load}>{t('common.retry')}</button>
          </div>
        )}

        {p.loading && p.users.length === 0 ? (
          <MobileList ariaLabel={t('users.loading')}>
            {[0, 1, 2, 3].map(i => (
              <li key={i} className="mobile-list-item" aria-hidden="true"
                  style={{ display: 'flex', alignItems: 'center', gap: 12, minHeight: 64, padding: '10px 14px' }}>
                <span className="skeleton" style={{ width: 40, height: 40, borderRadius: 12, flexShrink: 0 }} />
                <span style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <span className="skeleton" style={{ height: 12, width: '55%', borderRadius: 6 }} />
                  <span className="skeleton" style={{ height: 10, width: '75%', borderRadius: 6 }} />
                </span>
              </li>
            ))}
          </MobileList>
        ) : p.users.length === 0 && !p.loadError ? (
          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10,
            padding: '40px 16px', textAlign: 'center', color: 'var(--dim)', fontSize: 14,
          }}>
            <Users size={28} aria-hidden="true" style={{ opacity: 0.5 }} />
            {t('users.no_users_found')}
          </div>
        ) : (
          <MobileList ariaLabel={t('users.title')} style={{ opacity: p.loading ? 0.6 : 1, transition: 'opacity var(--dur-1) var(--ease-out)' }}>
            {p.users.map(u => {
              const st = STATUS[u.status]
              const isSelf = u.id === p.currentUser?.id
              return (
                <MobileCard
                  key={u.id}
                  leading={
                    <span aria-hidden="true" style={{
                      width: 40, height: 40, borderRadius: 12,
                      background: 'color-mix(in srgb, var(--accent) 14%, transparent)', color: 'var(--accent)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 14, fontWeight: 700,
                    }}>{initials(u)}</span>
                  }
                  title={<>{u.full_name || u.email}{isSelf && <span style={{ fontSize: 12, color: 'var(--accent)', fontWeight: 500, marginLeft: 6 }}>{t('users.you')}</span>}</>}
                  subtitle={u.full_name ? `${u.email} · ${roleLabel(t, u.role)}` : roleLabel(t, u.role)}
                  status={{ label: st ? t(st.labelKey) : u.status, tone: st?.tone ?? 'neutral' }}
                  onClick={() => setSheet({ kind: 'detail', user: u })}
                />
              )
            })}
          </MobileList>
        )}

        {pages > 1 && (
          <nav aria-label={t('users.m_pagination')} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button type="button" className="mobile-btn mobile-btn-secondary"
                    disabled={page <= 1} onClick={() => p.setOffset(Math.max(0, p.offset - p.limit))}
                    aria-label={t('users.m_prev')} style={{ flex: '0 0 48px', padding: 0 }}>
              <ChevronLeft size={18} aria-hidden="true" />
            </button>
            <span style={{ flex: 1, textAlign: 'center', fontSize: 13, color: 'var(--muted)' }}>
              {t('users.showing')} {p.offset + 1}–{Math.min(p.offset + p.limit, p.total)} {t('users.of')} {p.total}
            </span>
            <button type="button" className="mobile-btn mobile-btn-secondary"
                    disabled={page >= pages} onClick={() => p.setOffset(p.offset + p.limit)}
                    aria-label={t('users.m_next')} style={{ flex: '0 0 48px', padding: 0 }}>
              <ChevronRight size={18} aria-hidden="true" />
            </button>
          </nav>
        )}
      </div>

      <StickyActionBar>
        <button type="button" className="mobile-btn mobile-btn-primary" data-tour="users.invite"
                onClick={() => setSheet({ kind: 'form', user: null })}>
          <Plus size={18} aria-hidden="true" />{t('users.create_user')}
        </button>
      </StickyActionBar>

      <DetailSheet
        user={detailUser}
        currentUser={p.currentUser}
        onClose={close}
        onEdit={u => setSheet({ kind: 'form', user: u })}
        onDelete={u => setSheet({ kind: 'delete', user: u })}
        onChanged={p.load}
        lang={lang}
      />
      <FormSheet
        open={sheet.kind === 'form'}
        user={sheet.kind === 'form' ? sheet.user : null}
        onClose={close}
        onSaved={p.load}
      />
      <DeleteSheet
        user={sheet.kind === 'delete' ? sheet.user : null}
        onClose={close}
        onDeleted={p.load}
      />
    </MobileFormScope>
  )
}

// ── Detail ───────────────────────────────────────────────────────────────────

function DetailSheet({ user, currentUser, onClose, onEdit, onDelete, onChanged, lang }: {
  user: AdminUser | null
  currentUser: ReturnType<typeof getUser>
  onClose: () => void
  onEdit: (u: AdminUser) => void
  onDelete: (u: AdminUser) => void
  onChanged: () => void
  lang: 'es' | 'en'
}) {
  const { t } = useLanguage()
  const [statusBusy, setStatusBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [resend, setResend] = useState<'idle' | 'loading' | 'sent' | 'error'>('idle')
  // Keep the last user while the sheet animates out, so it does not go blank.
  const last = useRef<AdminUser | null>(null)
  if (user) last.current = user
  const u = user ?? last.current

  useEffect(() => { setError(null); setResend('idle') }, [user?.id])

  const isSelf = !!u && u.id === currentUser?.id

  async function pickStatus(s: string) {
    if (!u || s === u.status) return
    setStatusBusy(s); setError(null)
    try {
      await setUserStatus(u.id, s)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : t('users.update_status_failed'))
    } finally { setStatusBusy(null) }
  }

  async function doResend() {
    if (!u || resend === 'loading' || resend === 'sent') return
    setResend('loading')
    try { await resendVerification(u.id); setResend('sent') }
    catch { setResend('error') }
  }

  const st = u ? STATUS[u.status] : undefined

  return (
    <BottomSheet
      open={!!user}
      onClose={onClose}
      title={u ? (u.full_name || u.email) : ''}
      footer={u && (
        <div style={{ display: 'flex', gap: 8, flex: 1, minWidth: 0 }}>
          {!isSelf && (
            <button type="button" className="mobile-btn mobile-btn-secondary" style={{ color: 'var(--danger)' }}
                    onClick={() => onDelete(u)}>
              {t('users.delete_title')}
            </button>
          )}
          <button type="button" className="mobile-btn mobile-btn-primary" onClick={() => onEdit(u)}>
            {t('users.edit_title')}
          </button>
        </div>
      )}
    >
      {u && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {error && <ErrorLine text={error} />}
          <dl style={{
            margin: 0, display: 'grid', gridTemplateColumns: 'auto minmax(0,1fr)', gap: '10px 16px',
            fontSize: 14,
          }}>
            <dt style={{ color: 'var(--dim)' }}>{t('users.email_address')}</dt>
            <dd style={{ margin: 0, color: 'var(--text)', overflowWrap: 'anywhere', textAlign: 'right' }}>{u.email}</dd>
            <dt style={{ color: 'var(--dim)' }}>{t('users.col_status')}</dt>
            <dd style={{ margin: 0, textAlign: 'right', color: 'var(--text)' }}>{st ? t(st.labelKey) : u.status}</dd>
            <dt style={{ color: 'var(--dim)' }}>{t('users.col_role')}</dt>
            <dd style={{ margin: 0, textAlign: 'right', color: 'var(--text)' }}>{roleLabel(t, u.role)}</dd>
            <dt style={{ color: 'var(--dim)' }}>{t('users.col_created')}</dt>
            <dd style={{ margin: 0, textAlign: 'right', color: 'var(--text)' }}>{fmtDate(u.created_at, lang)}</dd>
            <dt style={{ color: 'var(--dim)' }}>{t('users.col_last_login')}</dt>
            <dd style={{ margin: 0, textAlign: 'right', color: 'var(--text)' }}>{fmtDate(u.last_login_at, lang)}</dd>
          </dl>

          {u.status === 'pending_confirmation' && (
            <button type="button" className="mobile-btn mobile-btn-secondary" onClick={doResend}
                    disabled={resend === 'loading' || resend === 'sent'}
                    style={{ color: resend === 'sent' ? 'var(--success)' : resend === 'error' ? 'var(--danger)' : undefined }}>
              {resend === 'sent' ? <CheckCircle2 size={16} aria-hidden="true" /> : <Mail size={16} aria-hidden="true" />}
              {resend === 'sent' ? `${t('users.resend_sent_prefix')} ${u.email}`
                : resend === 'error' ? t('users.resend_failed')
                : t('users.resend_title')}
            </button>
          )}

          {/* Your own status is not yours to change — same rule as desktop. */}
          {!isSelf && (
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--dim)', marginBottom: 8 }}>
                {t('users.change_status')}
              </div>
              <div role="radiogroup" aria-label={t('users.change_status')}
                   style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0,1fr))', gap: 6 }}>
                {SETTABLE_STATUSES.map(s => {
                  const active = u.status === s
                  return (
                    <button
                      key={s} type="button" role="radio" aria-checked={active}
                      onClick={() => pickStatus(s)}
                      disabled={statusBusy !== null}
                      className="tap-feedback"
                      style={{
                        all: 'unset', boxSizing: 'border-box', cursor: 'pointer', textAlign: 'center',
                        minHeight: 44, padding: '0 6px', borderRadius: 10, fontSize: 13, fontWeight: 600,
                        border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
                        background: active ? 'var(--accent-dim)' : 'var(--surface)',
                        color: active ? 'var(--accent)' : 'var(--muted)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        opacity: statusBusy && statusBusy !== s ? 0.5 : 1,
                      }}
                    >
                      {statusBusy === s ? t('users.saving') : t(STATUS[s].labelKey)}
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </BottomSheet>
  )
}

// ── Create / edit ────────────────────────────────────────────────────────────

function FormSheet({ open, user: target, onClose, onSaved }: {
  open: boolean
  user: AdminUser | null
  onClose: () => void
  onSaved: () => void
}) {
  const { t } = useLanguage()
  const isCreate = !target
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('analyst')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const formId = 'user-form-mobile'

  // Reset the fields every time the sheet opens on a (possibly different) user.
  useEffect(() => {
    if (!open) return
    setFullName(target?.full_name ?? '')
    setEmail(target?.email ?? '')
    setRole(target?.role ?? 'analyst')
    setError(null)
  }, [open, target])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null); setLoading(true)
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
    <BottomSheet
      open={open}
      onClose={onClose}
      title={isCreate ? t('users.create_user') : t('users.edit_user_title')}
      footer={
        <div style={{ display: 'flex', gap: 8, flex: 1, minWidth: 0 }}>
          <button type="button" className="mobile-btn mobile-btn-secondary" onClick={onClose}>{t('common.cancel')}</button>
          <button type="submit" form={formId} className="mobile-btn mobile-btn-primary" disabled={loading || !email.trim()}>
            {loading ? t('users.saving') : isCreate ? t('users.create_user') : t('users.save_changes')}
          </button>
        </div>
      }
    >
      <MobileFormScope>
        {error && <ErrorLine text={error} />}
        <form id={formId} onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Field label={t('users.full_name_optional')} htmlFor="m-user-full-name">
            <Input
              size="lg" id="m-user-full-name" name="full_name" autoComplete="name"
              value={fullName} onChange={e => setFullName(e.target.value)}
              placeholder={t('users.full_name_placeholder')}
              style={{ width: '100%' }}
            />
          </Field>
          <Field
            label={<>{t('users.email_address')} {isCreate && <span style={{ color: '#C0504D' }}>*</span>}</>}
            htmlFor="m-user-email"
          >
            <Input
              size="lg" id="m-user-email" name="email" autoComplete="email" inputMode="email"
              autoCapitalize="none" spellCheck={false}
              type="email" required value={email} onChange={e => setEmail(e.target.value)}
              placeholder={t('users.email_placeholder')}
              style={{ width: '100%' }}
            />
            {!isCreate && email !== target?.email && (
              <p style={{ fontSize: 12, color: '#B7791F', margin: '6px 0 0' }}>{t('users.email_reverify')}</p>
            )}
          </Field>
          <Field label={t('users.role')} htmlFor="m-user-role">
            <Select size="lg" id="m-user-role" name="role" chevron value={role} onChange={e => setRole(e.target.value)} style={{ width: '100%' }}>
              {ROLES.map(r => <option key={r} value={r}>{roleLabel(t, r)}</option>)}
            </Select>
          </Field>
          {isCreate && (
            <div style={{
              display: 'flex', gap: 8, padding: '10px 12px', borderRadius: 10,
              background: 'color-mix(in srgb, var(--accent) 8%, transparent)',
              border: '1px solid color-mix(in srgb, var(--accent) 20%, transparent)',
              fontSize: 13, color: 'var(--text)', lineHeight: 1.45,
            }}>
              <Mail size={14} style={{ marginTop: 2, flexShrink: 0, color: 'var(--accent)' }} aria-hidden="true" />
              {t('users.setup_email_note')}
            </div>
          )}
        </form>
      </MobileFormScope>
    </BottomSheet>
  )
}

// ── Delete ───────────────────────────────────────────────────────────────────

function DeleteSheet({ user, onClose, onDeleted }: {
  user: AdminUser | null
  onClose: () => void
  onDeleted: () => void
}) {
  const { t } = useLanguage()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const last = useRef<AdminUser | null>(null)
  if (user) last.current = user
  const u = user ?? last.current

  useEffect(() => { setError(null); setLoading(false) }, [user?.id])

  async function handleDelete() {
    if (!u) return
    setLoading(true)
    try {
      await deleteAdminUser(u.id)
      onDeleted()
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('users.delete_failed'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <BottomSheet
      open={!!user}
      onClose={onClose}
      title={t('users.delete_user_q')}
      footer={
        <div style={{ display: 'flex', gap: 8, flex: 1, minWidth: 0 }}>
          <button type="button" className="mobile-btn mobile-btn-secondary" onClick={onClose}>{t('common.cancel')}</button>
          <button type="button" className="mobile-btn mobile-btn-danger" onClick={handleDelete} disabled={loading}>
            {loading ? t('users.deleting') : t('users.delete_user')}
          </button>
        </div>
      }
    >
      {u && (
        <div style={{ fontSize: 14, lineHeight: 1.55, color: 'var(--muted)' }}>
          {error && <ErrorLine text={error} />}
          <p style={{ margin: '0 0 8px' }}>
            {t('users.delete_perm_prefix')}{' '}
            <strong style={{ color: 'var(--text)', overflowWrap: 'anywhere' }}>{u.full_name || u.email}</strong>.
          </p>
          <p style={{ margin: 0, fontSize: 13 }}>{t('users.delete_perm_detail')}</p>
        </div>
      )}
    </BottomSheet>
  )
}
