'use client'
/**
 * A sandbox to build an integration against: a throwaway tenant with the demo
 * history and a key already issued. It cannot reach this company's data and this
 * company's keys cannot reach it. The key and the login are shown once, here.
 */
import { useCallback, useEffect, useState } from 'react'
import { createSandbox, deleteSandbox, listSandboxes } from '@/lib/api'
import type { SandboxCreated, SandboxInfo } from '@/lib/api'
import Button from '@/components/ui/Button'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'

const C = { border: 'var(--border)', dim: 'var(--dim)', red: '#C0504D', green: '#2F855A', amber: '#B7791F' }

export default function SandboxCard() {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const isAdmin = getUser()?.role === 'admin'
  const [items, setItems] = useState<SandboxInfo[] | null>(null)
  const [created, setCreated] = useState<SandboxCreated | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    listSandboxes({ silent: true }).then(setItems).catch(() => setItems([]))
  }, [])
  useEffect(() => { load() }, [load])

  const create = async () => {
    setBusy(true); setError(null)
    try { setCreated(await createSandbox()); load() }
    catch (e: unknown) { setError(errorDetail(e)) }
    finally { setBusy(false) }
  }

  const remove = async (id: string) => {
    if (!(await confirm({ title: t('sandbox.delete_title'), message: t('sandbox.delete_message'), danger: true }))) return
    setBusy(true); setError(null)
    try { await deleteSandbox(id); if (created?.id === id) setCreated(null); load() }
    catch (e: unknown) { setError(errorDetail(e)) }
    finally { setBusy(false) }
  }

  if (items === null) return null

  return (
    <div style={{ border: `1px solid ${C.border}`, borderRadius: 10, padding: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <strong style={{ fontSize: 13 }}>{t('sandbox.title')}</strong>
        {isAdmin && (
          <Button variant="secondary" size="sm" loading={busy} onClick={create}>{t('sandbox.create')}</Button>
        )}
      </div>
      <p style={{ margin: '6px 0 0', fontSize: 12, color: C.dim, lineHeight: 1.5 }}>{t('sandbox.intro')}</p>
      {error && <p role="alert" style={{ margin: '8px 0 0', fontSize: 12, color: C.red }}>{error}</p>}

      {created && (
        <div role="status" style={{ marginTop: 10, padding: 10, borderRadius: 8, border: `1px solid ${C.green}` }}>
          <p style={{ margin: 0, fontSize: 12.5, fontWeight: 700, color: C.green }}>{t('sandbox.created')}</p>
          <p style={{ margin: '6px 0 0', fontSize: 12 }}>{t('sandbox.key_label')}</p>
          <code style={{ display: 'block', fontSize: 12, overflowWrap: 'anywhere', userSelect: 'all' }}>{created.api_key}</code>
          <p style={{ margin: '8px 0 0', fontSize: 12 }}>{t('sandbox.login_label')}</p>
          <code style={{ display: 'block', fontSize: 12, overflowWrap: 'anywhere', userSelect: 'all' }}>
            {created.login.email} / {created.login.password}
          </code>
          <p style={{ margin: '8px 0 0', fontSize: 11.5, color: C.dim }}>{t('sandbox.shown_once')}</p>
          {!created.seeded && <p style={{ margin: '6px 0 0', fontSize: 11.5, color: C.amber }}>{t('sandbox.not_seeded')}</p>}
        </div>
      )}

      {items.length > 0 && (
        <ul style={{ listStyle: 'none', margin: '10px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {items.map(s => (
            <li key={s.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center', fontSize: 12 }}>
              <span>{t('sandbox.item', { created: s.created_at?.slice(0, 10) ?? '', expires: s.expires_at?.slice(0, 10) ?? '' })}</span>
              {isAdmin && (
                <Button variant="danger" size="sm" disabled={busy} onClick={() => remove(s.id)}>{t('sandbox.delete')}</Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
