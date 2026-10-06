'use client'
/**
 * Custom roles - the administrator's side.
 *
 * A custom role is a named set of permissions that NARROWS what a person can do
 * inside their built-in role (admin / analyst / viewer); it never grants more.
 * The server enforces it on every request, so a permission removed here is gone
 * on the person's next click. This panel only edits the definitions; who holds
 * which role is chosen per person on the users list (`CustomRoleSelect`).
 *
 * Every server refusal (a name taken, a role in use, "you cannot grant what you
 * do not hold") arrives as a structured code and is rendered through
 * `errors.<code>` by the ApiError message, so nothing here hardcodes a sentence.
 */
import { useCallback, useEffect, useState } from 'react'
import { ShieldCheck, ChevronDown, Plus, Trash2, Edit2 } from 'lucide-react'
import {
  listCustomRoles, listRolePermissions, createCustomRole, updateCustomRole,
  deleteCustomRole, assignCustomRole, type CustomRole, type AdminUser,
} from '@/lib/api'
import Card from '@/components/ui/Card'
import Input, { Field } from '@/components/ui/Input'
import { useLanguage } from '@/contexts/LanguageContext'

/** Label for a permission, falling back to its identifier (never a raw i18n key). */
function permLabel(t: (k: string) => string, name: string): string {
  const key = `users.perm_${name}`
  const v = t(key)
  return v === key ? name : v
}

export function RolesPanel({ onChanged }: { onChanged?: () => void }) {
  const { t } = useLanguage()
  const [open, setOpen] = useState(false)
  const [roles, setRoles] = useState<CustomRole[] | null>(null)
  const [catalogue, setCatalogue] = useState<string[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ id: string | null; name: string; description: string; perms: string[] } | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [r, c] = await Promise.all([listCustomRoles(), listRolePermissions()])
      setRoles(r.roles)
      setCatalogue(c.permissions.map(p => p.name))
      setLoadError(null)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : t('roles.load_failed'))
    }
  }, [t])

  useEffect(() => { if (open && roles === null) void load() }, [open, roles, load])

  async function save() {
    if (!editing) return
    setSaving(true)
    setError(null)
    try {
      const body = { name: editing.name, description: editing.description, permissions: editing.perms }
      if (editing.id) await updateCustomRole(editing.id, body)
      else await createCustomRole(body)
      setEditing(null)
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : t('users.operation_failed'))
    } finally { setSaving(false) }
  }

  async function remove(role: CustomRole) {
    if (!window.confirm(t('roles.delete_confirm', { name: role.name }))) return
    setError(null)
    try {
      await deleteCustomRole(role.id)
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : t('users.operation_failed'))
    }
  }

  return (
    <Card style={{ marginTop: 16 }} data-testid="roles-panel">
      <button
        type="button" onClick={() => setOpen(v => !v)} aria-expanded={open}
        style={{
          all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center',
          gap: 8, width: '100%', fontSize: 14, fontWeight: 600, color: 'var(--text)',
        }}
      >
        <ShieldCheck size={16} aria-hidden="true" />
        <span style={{ flex: 1 }}>{t('roles.title')}</span>
        <ChevronDown size={16} aria-hidden="true" style={{ transform: open ? 'rotate(180deg)' : 'none' }} />
      </button>
      {open && (
        <div style={{ marginTop: 12 }}>
          <p style={{ margin: '0 0 12px', fontSize: 12, color: 'var(--dim)', lineHeight: 1.5 }}>{t('roles.subtitle')}</p>
          {loadError && <p role="alert" style={{ fontSize: 12, color: '#C0504D' }}>{loadError}</p>}
          {roles && roles.length === 0 && !editing && (
            <p style={{ fontSize: 12, color: 'var(--dim)' }}>{t('roles.empty')}</p>
          )}
          {roles?.map(r => (
            <div key={r.id} style={{
              display: 'flex', alignItems: 'flex-start', gap: 12, padding: '10px 0',
              borderTop: '1px solid var(--border)',
            }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                  {r.name}
                  <span style={{ marginLeft: 8, fontSize: 11, fontWeight: 400, color: 'var(--dim)' }}>
                    {t('roles.people', { n: r.user_count })}
                  </span>
                </div>
                {r.description && <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 2 }}>{r.description}</div>}
                <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4, lineHeight: 1.5 }}>
                  {r.permissions.map(p => permLabel(t, p)).join(' · ') || '—'}
                </div>
              </div>
              <button type="button" aria-label={t('roles.edit')} title={t('roles.edit')}
                onClick={() => { setError(null); setEditing({ id: r.id, name: r.name, description: r.description, perms: r.permissions }) }}
                style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)' }}>
                <Edit2 size={14} aria-hidden="true" />
              </button>
              <button type="button" aria-label={t('common.delete')} title={t('common.delete')}
                onClick={() => remove(r)} style={{ all: 'unset', cursor: 'pointer', color: '#C0504D' }}>
                <Trash2 size={14} aria-hidden="true" />
              </button>
            </div>
          ))}

          {editing ? (
            <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
              <Field label={t('roles.name')}>
                <Input value={editing.name} maxLength={60}
                  onChange={e => setEditing({ ...editing, name: e.target.value })} />
              </Field>
              <Field label={t('roles.description')}>
                <Input value={editing.description} maxLength={300}
                  onChange={e => setEditing({ ...editing, description: e.target.value })} />
              </Field>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)', margin: '10px 0 6px' }}>
                {t('roles.permissions')}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 6 }}>
                {catalogue.map(p => (
                  <label key={p} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, color: 'var(--text)', cursor: 'pointer' }}>
                    <input type="checkbox" checked={editing.perms.includes(p)}
                      onChange={e => setEditing({
                        ...editing,
                        perms: e.target.checked ? [...editing.perms, p] : editing.perms.filter(x => x !== p),
                      })} />
                    {permLabel(t, p)}
                  </label>
                ))}
              </div>
              {error && <p role="alert" style={{ margin: '10px 0 0', fontSize: 12, color: '#C0504D' }}>{error}</p>}
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 12 }}>
                <button type="button" onClick={() => setEditing(null)} style={{
                  padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border)',
                  background: 'transparent', color: 'var(--muted)', fontSize: 12, cursor: 'pointer',
                }}>{t('common.cancel')}</button>
                <button type="button" onClick={save} disabled={saving || !editing.name.trim()} style={{
                  padding: '6px 14px', borderRadius: 6, border: 'none', background: 'var(--accent)',
                  color: '#fff', fontSize: 12, fontWeight: 600, cursor: saving ? 'wait' : 'pointer',
                }}>{saving ? t('users.saving') : t('roles.save')}</button>
              </div>
            </div>
          ) : (
            <>
              {error && <p role="alert" style={{ margin: '10px 0 0', fontSize: 12, color: '#C0504D' }}>{error}</p>}
              <button type="button" onClick={() => { setError(null); setEditing({ id: null, name: '', description: '', perms: [] }) }}
                style={{
                  marginTop: 12, display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 12px',
                  borderRadius: 6, border: '1px solid var(--border)', background: 'transparent',
                  color: 'var(--text)', fontSize: 12, cursor: 'pointer',
                }}>
                <Plus size={13} aria-hidden="true" /> {t('roles.new')}
              </button>
            </>
          )}
        </div>
      )}
    </Card>
  )
}

/**
 * One person's custom role, under their e-mail on the users list. "No custom
 * role" is the default (the built-in role alone decides). A person cannot
 * change their own role from here, and the server refuses it anyway.
 */
export function CustomRoleSelect({ user, roles, disabled, onChanged }: {
  user: AdminUser
  roles: CustomRole[]
  disabled?: boolean
  onChanged: () => void
}) {
  const { t } = useLanguage()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function change(value: string) {
    setBusy(true)
    setError(null)
    try {
      await assignCustomRole(user.id, value === '' ? null : value)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : t('users.operation_failed'))
    } finally { setBusy(false) }
  }

  return (
    <div style={{ marginTop: 4, display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--dim)' }}>
      <ShieldCheck size={11} aria-hidden="true" />
      <label title={t('roles.assign_title')}>{t('roles.assign_label')}:</label>
      <select
        data-testid="custom-role-select" value={user.custom_role_id ?? ''} disabled={disabled || busy}
        onChange={e => change(e.target.value)}
        style={{ fontSize: 11, background: 'transparent', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 4, maxWidth: 160 }}
      >
        <option value="">{t('roles.none')}</option>
        {roles.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
        {user.custom_role_id && !roles.some(r => r.id === user.custom_role_id) && (
          <option value={user.custom_role_id}>{user.custom_role_id}</option>
        )}
      </select>
      {error && <span role="alert" style={{ color: '#C0504D' }}>{error}</span>}
    </div>
  )
}
