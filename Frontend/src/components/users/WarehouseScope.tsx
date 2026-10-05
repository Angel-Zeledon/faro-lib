'use client'
/**
 * "Bodegas" - which warehouses one person may see and change.
 *
 * A compact control under the person's e-mail on the users screen, drawn only
 * when the company has two or more warehouses (with one there is nothing to
 * choose). The default is "all" - which is what every existing user has - and
 * the control never changes it unless an administrator saves a different set.
 *
 * What it saves is a list of warehouse ids; an empty list is "none", on purpose
 * and said so, because "no warehouse ticked" must never read as "all".
 */
import { useEffect, useRef, useState } from 'react'
import { MapPin } from 'lucide-react'
import { setUserWarehouseScope, type AdminUser } from '@/lib/api'
import type { Warehouse } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'

export function scopeSummary(
  scope: string[] | null | undefined, warehouses: Warehouse[],
  t: (k: string) => string,
): string {
  if (scope == null) return t('users.wh_all')
  const names = warehouses.filter(w => scope.includes(w.id)).map(w => w.name)
  return names.length ? names.join(', ') : t('users.wh_none')
}

export function WarehouseScope({ user, warehouses, disabled, onChanged }: {
  user: AdminUser
  warehouses: Warehouse[]
  /** A person cannot narrow their own access from here. */
  disabled?: boolean
  onChanged: () => void
}) {
  const { t } = useLanguage()
  const [open, setOpen] = useState(false)
  const [all, setAll] = useState(user.warehouse_scope == null)
  const [picked, setPicked] = useState<string[]>(user.warehouse_scope ?? [])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)

  // Re-seed from the row each time it opens, so a cancelled edit leaves no trace.
  useEffect(() => {
    if (!open) return
    setAll(user.warehouse_scope == null)
    setPicked(user.warehouse_scope ?? [])
    setError(null)
  }, [open, user.warehouse_scope])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await setUserWarehouseScope(user.id, all ? null : picked)
      setOpen(false)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : t('users.operation_failed'))
    } finally { setSaving(false) }
  }

  const label = scopeSummary(user.warehouse_scope, warehouses, t)

  return (
    <div ref={box} style={{ position: 'relative', marginTop: 4 }}>
      <button
        type="button" onClick={() => setOpen(v => !v)} disabled={disabled}
        data-testid="wh-scope-open"
        aria-expanded={open}
        title={t('users.wh_title')}
        style={{
          all: 'unset', cursor: disabled ? 'default' : 'pointer',
          display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11,
          color: user.warehouse_scope == null ? 'var(--dim)' : 'var(--accent)',
          maxWidth: '100%',
        }}
      >
        <MapPin size={11} aria-hidden="true" />
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {t('users.wh_label')}: {label}
        </span>
      </button>
      {open && (
        <div role="dialog" aria-label={t('users.wh_title')} style={{
          position: 'absolute', left: 0, top: '100%', marginTop: 4, zIndex: 30, width: 240,
          background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8,
          padding: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.25)',
        }}>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, color: 'var(--text)', cursor: 'pointer' }}>
            <input type="checkbox" checked={all} onChange={e => setAll(e.target.checked)} />
            {t('users.wh_all_option')}
          </label>
          <div style={{
            margin: '8px 0', display: 'flex', flexDirection: 'column', gap: 6,
            opacity: all ? 0.45 : 1, maxHeight: 180, overflowY: 'auto',
          }}>
            {warehouses.map(w => (
              <label key={w.id} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, color: 'var(--text)', cursor: all ? 'default' : 'pointer' }}>
                <input
                  type="checkbox" disabled={all} checked={all || picked.includes(w.id)}
                  onChange={e => setPicked(p => e.target.checked ? [...p, w.id] : p.filter(x => x !== w.id))}
                />
                {w.name}
              </label>
            ))}
          </div>
          {!all && picked.length === 0 && (
            <p style={{ margin: '0 0 8px', fontSize: 11, color: '#B7791F', lineHeight: 1.4 }}>
              {t('users.wh_none_warning')}
            </p>
          )}
          {error && <p role="alert" style={{ margin: '0 0 8px', fontSize: 11, color: '#C0504D' }}>{error}</p>}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button type="button" onClick={() => setOpen(false)} style={{
              padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border)',
              background: 'transparent', color: 'var(--muted)', fontSize: 12, cursor: 'pointer',
            }}>{t('common.cancel')}</button>
            <button type="button" onClick={save} disabled={saving} style={{
              padding: '5px 12px', borderRadius: 6, border: 'none', background: 'var(--accent)',
              color: '#fff', fontSize: 12, fontWeight: 600, cursor: saving ? 'wait' : 'pointer',
            }}>{saving ? t('users.saving') : t('users.save_changes')}</button>
          </div>
        </div>
      )}
    </div>
  )
}
