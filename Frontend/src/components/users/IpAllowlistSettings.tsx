'use client'
/**
 * IP allowlist - the administrator's side, a collapsed card at the foot of the
 * users screen (admin-only like the screen).
 *
 * It says where things stand before offering anything: whether the allowlist is
 * on, the address the server sees for THIS browser and whether the entries
 * cover it. The server refuses a change that would lock the caller out (turning
 * it on, or removing the entry that covers them); this screen never decides
 * that itself, it only shows the server's own verdict (`your_ip_covered`) so the
 * refusal is not a surprise.
 */
import { useCallback, useEffect, useState } from 'react'
import { ShieldCheck, ChevronDown, AlertTriangle, Trash2 } from 'lucide-react'
import {
  getIpAllowlist, addIpAllowlistEntry, deleteIpAllowlistEntry, setIpAllowlistEnabled,
  type IpAllowlistState,
} from '@/lib/api'
import Card from '@/components/ui/Card'
import Input, { Field } from '@/components/ui/Input'
import { useLanguage } from '@/contexts/LanguageContext'
import { localeFor } from '@/lib/numberLocale'

const btn = {
  padding: '7px 14px', borderRadius: 7, border: '1px solid var(--border)',
  background: 'transparent', color: 'var(--text)', fontSize: 12.5, cursor: 'pointer',
} as const

export function IpAllowlistSettings() {
  const { t, lang } = useLanguage()
  const [open, setOpen] = useState(false)
  const [state, setState] = useState<IpAllowlistState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [cidr, setCidr] = useState('')
  const [label, setLabel] = useState('')

  const load = useCallback(async () => {
    setError(null)
    try {
      setState(await getIpAllowlist())
    } catch (e) {
      setError(e instanceof Error ? e.message : t('users.operation_failed'))
    }
  }, [t])

  useEffect(() => { if (open && !state) load() }, [open, state, load])

  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(null)
    try { await fn() } catch (e) {
      setError(e instanceof Error ? e.message : t('users.operation_failed'))
    } finally { setBusy(false) }
  }

  const add = (e: React.FormEvent) => {
    e.preventDefault()
    run(async () => {
      setState(await addIpAllowlistEntry(cidr.trim(), label.trim()))
      setCidr(''); setLabel('')
    })
  }

  const remove = (id: string) => run(async () => {
    if (!window.confirm(t('ip_allowlist.confirm_remove'))) return
    await deleteIpAllowlistEntry(id)
    await load()
  })

  const toggle = (enabled: boolean) => run(async () => {
    if (enabled && !window.confirm(t('ip_allowlist.confirm_enable'))) return
    setState(await setIpAllowlistEnabled(enabled))
  })

  const when = (iso: string | null) => iso
    ? new Date(iso).toLocaleString(localeFor(lang), { dateStyle: 'short', timeStyle: 'short' })
    : ''

  return (
    <div style={{ marginTop: 28 }} data-testid="ip-allowlist-settings">
      <Card padding={0}>
        <button
          type="button" onClick={() => setOpen(v => !v)} aria-expanded={open}
          style={{
            all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10,
            width: '100%', boxSizing: 'border-box', padding: '14px 16px',
          }}
        >
          <ShieldCheck size={15} color="var(--dim)" aria-hidden="true" />
          <span style={{ flex: 1 }}>
            <span style={{ display: 'block', fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{t('ip_allowlist.title')}</span>
            <span style={{ display: 'block', fontSize: 11.5, color: 'var(--dim)', marginTop: 2 }}>{t('ip_allowlist.subtitle')}</span>
          </span>
          <ChevronDown size={14} color="var(--dim)" style={{ transform: open ? 'rotate(180deg)' : undefined }} aria-hidden="true" />
        </button>

        {open && (
          <div style={{ padding: '4px 16px 18px', borderTop: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 14 }}>
            {error && (
              <p role="alert" style={{ margin: '10px 0 0', display: 'flex', gap: 6, alignItems: 'center', fontSize: 12.5, color: '#C0504D' }}>
                <AlertTriangle size={13} aria-hidden="true" /> {error}
              </p>
            )}
            {!state && !error && <p style={{ fontSize: 12.5, color: 'var(--dim)' }}>{t('users.loading')}</p>}

            {state && (
              <>
                <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                      {state.enabled ? t('ip_allowlist.status_on') : t('ip_allowlist.status_off')}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>
                      {state.your_ip
                        ? t('ip_allowlist.your_ip', { ip: state.your_ip })
                        : t('ip_allowlist.your_ip_unknown')}
                      {' '}
                      {state.your_ip && (state.your_ip_covered
                        ? t('ip_allowlist.covers_you')
                        : t('ip_allowlist.does_not_cover_you'))}
                    </div>
                  </div>
                  <button type="button" disabled={busy} onClick={() => toggle(!state.enabled)}
                    style={state.enabled ? btn : {
                      padding: '8px 18px', borderRadius: 7, border: 'none', background: 'var(--accent)',
                      color: '#fff', fontSize: 13, fontWeight: 600, cursor: busy ? 'wait' : 'pointer',
                    }}>
                    {state.enabled ? t('ip_allowlist.disable') : t('ip_allowlist.enable')}
                  </button>
                </div>

                <p style={{ margin: 0, fontSize: 11.5, color: 'var(--dim)', lineHeight: 1.5 }}>{t('ip_allowlist.scope_hint')}</p>

                {state.entries.length === 0 ? (
                  <p style={{ margin: 0, fontSize: 12, color: 'var(--dim)' }}>{t('ip_allowlist.empty')}</p>
                ) : (
                  <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column' }}>
                    {state.entries.map(en => (
                      <li key={en.id} style={{
                        display: 'flex', flexWrap: 'wrap', gap: '2px 12px', alignItems: 'center',
                        padding: '8px 0', borderBottom: '1px solid var(--border)', minWidth: 0, fontSize: 12.5,
                      }}>
                        <code style={{ fontWeight: 600 }}>{en.cidr}</code>
                        <span style={{ flex: 1, minWidth: 0, color: 'var(--muted)', wordBreak: 'break-word' }}>{en.label}</span>
                        <span style={{ color: 'var(--dim)', fontSize: 11.5 }}>{when(en.created_at)}</span>
                        <button type="button" disabled={busy} onClick={() => remove(en.id)}
                          aria-label={t('ip_allowlist.remove')} title={t('ip_allowlist.remove')}
                          style={{ ...btn, padding: '4px 8px', display: 'inline-flex', alignItems: 'center' }}>
                          <Trash2 size={13} aria-hidden="true" />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}

                <form onSubmit={add} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <Field label={t('ip_allowlist.cidr_label')} htmlFor="ipa-cidr">
                    <Input id="ipa-cidr" name="ip_allowlist_cidr" required value={cidr} autoComplete="off"
                      onChange={e => setCidr(e.target.value)} placeholder="203.0.113.0/24" />
                  </Field>
                  <Field label={t('ip_allowlist.label_label')} htmlFor="ipa-label">
                    <Input id="ipa-label" name="ip_allowlist_label" value={label} maxLength={100} autoComplete="off"
                      onChange={e => setLabel(e.target.value)} placeholder={t('ip_allowlist.label_placeholder')} />
                  </Field>
                  <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                    <button type="submit" disabled={busy || !cidr.trim() || state.entries.length >= state.max_entries} style={btn}>
                      {t('ip_allowlist.add')}
                    </button>
                  </div>
                </form>
              </>
            )}
          </div>
        )}
      </Card>
    </div>
  )
}
