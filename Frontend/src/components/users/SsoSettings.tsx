'use client'
/**
 * Company sign-in (OpenID Connect) - the administrator's side.
 *
 * A collapsed card at the foot of the users screen, admin-only like the screen.
 * It says plainly where things stand before offering a single field:
 *
 *   - the installation has it switched off      -> a sentence naming the switch,
 *     and no form (there would be nothing to save into);
 *   - secrets cannot be stored on this server    -> a sentence, and no form;
 *   - otherwise the form: issuer, client id and secret, the e-mail domains that
 *     belong to the company, the role a new person gets, an optional groups
 *     claim with a group -> role table, and "make it mandatory".
 *
 * The client secret is write-only: it is never shown again, not even masked. An
 * empty field on a later save means "keep the one stored".
 */
import { useCallback, useEffect, useState } from 'react'
import { Building2, ChevronDown, AlertTriangle, CheckCircle2, Trash2 } from 'lucide-react'
import {
  getSsoConfig, saveSsoConfig, deleteSsoConfig, type SsoConfig,
} from '@/lib/api'
import Card from '@/components/ui/Card'
import Input, { Field, Select } from '@/components/ui/Input'
import { useLanguage } from '@/contexts/LanguageContext'
import { roleLabel } from '@/lib/enumLabels'

type Role = 'analyst' | 'viewer'

interface GroupRule { group: string; role: Role }

function rulesFrom(map: Record<string, Role>): GroupRule[] {
  return Object.entries(map).map(([group, role]) => ({ group, role }))
}

export function SsoSettings() {
  const { t } = useLanguage()
  const [open, setOpen] = useState(false)
  const [state, setState] = useState<{
    instance_enabled: boolean; secret_storage: boolean; redirect_uri: string
    config: SsoConfig | null
  } | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [issuer, setIssuer] = useState('')
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const [domains, setDomains] = useState('')
  const [role, setRole] = useState<Role>('viewer')
  const [claim, setClaim] = useState('')
  const [rules, setRules] = useState<GroupRule[]>([])
  const [enforce, setEnforce] = useState(false)
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const apply = useCallback((cfg: SsoConfig | null) => {
    setIssuer(cfg?.issuer ?? '')
    setClientId(cfg?.client_id ?? '')
    setSecret('')
    setDomains((cfg?.allowed_domains ?? []).join(', '))
    setRole(cfg?.default_role ?? 'viewer')
    setClaim(cfg?.groups_claim ?? '')
    setRules(rulesFrom(cfg?.group_roles ?? {}))
    setEnforce(cfg?.enforce_sso ?? false)
    setEnabled(cfg?.enabled ?? true)
  }, [])

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const r = await getSsoConfig()
      setState(r)
      apply(r.config)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : t('users.operation_failed'))
    }
  }, [apply, t])

  useEffect(() => { if (open && !state) load() }, [open, state, load])

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true); setError(null); setSaved(false)
    try {
      const group_roles: Record<string, Role> = {}
      for (const r of rules) if (r.group.trim()) group_roles[r.group.trim()] = r.role
      const res = await saveSsoConfig({
        issuer: issuer.trim(),
        client_id: clientId.trim(),
        client_secret: secret.trim() || null,
        allowed_domains: domains.split(/[,\s]+/).map(d => d.trim()).filter(Boolean),
        default_role: role,
        enforce_sso: enforce,
        groups_claim: claim.trim() || null,
        group_roles,
        enabled,
      })
      setState(s => s && { ...s, config: res.config })
      apply(res.config)
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('users.operation_failed'))
    } finally { setSaving(false) }
  }

  async function remove() {
    if (!window.confirm(t('sso.confirm_remove'))) return
    setSaving(true); setError(null)
    try {
      await deleteSsoConfig()
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : t('users.operation_failed'))
    } finally { setSaving(false) }
  }

  const status = !state ? null
    : !state.instance_enabled ? 'instance_off'
    : !state.secret_storage ? 'no_secret_storage'
    : 'ready'

  return (
    <div style={{ marginTop: 28 }} data-testid="sso-settings">
      <Card padding={0}>
        <button
          type="button" onClick={() => setOpen(v => !v)} aria-expanded={open}
          style={{
            all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10,
            width: '100%', boxSizing: 'border-box', padding: '14px 16px',
          }}
        >
          <Building2 size={15} color="var(--dim)" aria-hidden="true" />
          <span style={{ flex: 1 }}>
            <span style={{ display: 'block', fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{t('sso.title')}</span>
            <span style={{ display: 'block', fontSize: 11.5, color: 'var(--dim)', marginTop: 2 }}>{t('sso.subtitle')}</span>
          </span>
          <ChevronDown size={14} color="var(--dim)" style={{ transform: open ? 'rotate(180deg)' : undefined }} aria-hidden="true" />
        </button>

        {open && (
          <div style={{ padding: '4px 16px 18px', borderTop: '1px solid var(--border)' }}>
            {loadError && <p role="alert" style={{ fontSize: 12.5, color: '#C0504D' }}>{loadError}</p>}
            {!state && !loadError && <p style={{ fontSize: 12.5, color: 'var(--dim)' }}>{t('users.loading')}</p>}

            {status === 'instance_off' && (
              <p style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.55 }}>{t('sso.instance_off')}</p>
            )}
            {status === 'no_secret_storage' && (
              <p style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.55 }}>{t('sso.no_secret_storage')}</p>
            )}

            {status === 'ready' && state && (
              <form onSubmit={save} style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 14 }}>
                <p style={{ margin: 0, fontSize: 12, color: 'var(--dim)', lineHeight: 1.5 }}>
                  {t('sso.redirect_hint')} <code style={{ userSelect: 'all' }}>{state.redirect_uri}</code>
                </p>

                <Field label={t('sso.issuer')} htmlFor="sso-issuer">
                  <Input id="sso-issuer" name="sso_issuer" type="url" required value={issuer}
                    onChange={e => setIssuer(e.target.value)} placeholder="https://login.example.com" />
                </Field>
                <Field label={t('sso.client_id')} htmlFor="sso-client-id">
                  <Input id="sso-client-id" name="sso_client_id" required value={clientId}
                    onChange={e => setClientId(e.target.value)} autoComplete="off" />
                </Field>
                <Field label={t('sso.client_secret')} htmlFor="sso-client-secret">
                  <Input id="sso-client-secret" name="sso_client_secret" type="password" value={secret}
                    onChange={e => setSecret(e.target.value)} autoComplete="new-password"
                    required={!state.config}
                    placeholder={state.config?.has_client_secret ? t('sso.secret_kept') : ''} />
                </Field>
                <Field label={t('sso.domains')} htmlFor="sso-domains">
                  <Input id="sso-domains" name="sso_domains" required value={domains}
                    onChange={e => setDomains(e.target.value)} placeholder="example.com, example.org" />
                  <p style={{ fontSize: 11, color: 'var(--dim)', margin: '4px 0 0' }}>{t('sso.domains_hint')}</p>
                </Field>
                <Field label={t('sso.default_role')} htmlFor="sso-default-role">
                  <Select id="sso-default-role" name="sso_default_role" value={role}
                    onChange={e => setRole(e.target.value as Role)}>
                    <option value="viewer">{roleLabel(t, 'viewer')}</option>
                    <option value="analyst">{roleLabel(t, 'analyst')}</option>
                  </Select>
                  <p style={{ fontSize: 11, color: 'var(--dim)', margin: '4px 0 0' }}>{t('sso.default_role_hint')}</p>
                </Field>

                <Field label={t('sso.groups_claim')} htmlFor="sso-claim">
                  <Input id="sso-claim" name="sso_claim" value={claim} onChange={e => setClaim(e.target.value)}
                    placeholder="groups" />
                  <p style={{ fontSize: 11, color: 'var(--dim)', margin: '4px 0 0' }}>{t('sso.groups_hint')}</p>
                </Field>
                {(claim.trim() || rules.length > 0) && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {rules.map((r, i) => (
                      <div key={i} style={{ display: 'flex', gap: 8 }}>
                        <Input name={`sso_group_${i}`} aria-label={t('sso.group_name')} value={r.group}
                          onChange={e => setRules(rs => rs.map((x, j) => j === i ? { ...x, group: e.target.value } : x))}
                          placeholder={t('sso.group_name')} />
                        <Select name={`sso_group_role_${i}`} aria-label={t('sso.group_role')} value={r.role}
                          style={{ width: 140 }}
                          onChange={e => setRules(rs => rs.map((x, j) => j === i ? { ...x, role: e.target.value as Role } : x))}>
                          <option value="viewer">{roleLabel(t, 'viewer')}</option>
                          <option value="analyst">{roleLabel(t, 'analyst')}</option>
                        </Select>
                        <button type="button" aria-label={t('common.delete')}
                          onClick={() => setRules(rs => rs.filter((_, j) => j !== i))}
                          style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)', padding: 6 }}>
                          <Trash2 size={13} aria-hidden="true" />
                        </button>
                      </div>
                    ))}
                    <button type="button" onClick={() => setRules(rs => [...rs, { group: '', role: 'viewer' }])}
                      style={{ all: 'unset', cursor: 'pointer', fontSize: 12, color: 'var(--accent)' }}>
                      + {t('sso.add_group')}
                    </button>
                  </div>
                )}

                <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 12.5, color: 'var(--text)' }}>
                  <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} style={{ marginTop: 2 }} />
                  <span>{t('sso.enabled')}</span>
                </label>
                <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 12.5, color: 'var(--text)' }}>
                  <input type="checkbox" checked={enforce} onChange={e => setEnforce(e.target.checked)} style={{ marginTop: 2 }} />
                  <span>
                    {t('sso.enforce')}
                    <span style={{ display: 'block', fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>{t('sso.enforce_hint')}</span>
                  </span>
                </label>

                {error && (
                  <p role="alert" style={{ margin: 0, display: 'flex', gap: 6, alignItems: 'center', fontSize: 12.5, color: '#C0504D' }}>
                    <AlertTriangle size={13} aria-hidden="true" /> {error}
                  </p>
                )}
                {saved && !error && (
                  <p style={{ margin: 0, display: 'flex', gap: 6, alignItems: 'center', fontSize: 12.5, color: '#2E8B62' }}>
                    <CheckCircle2 size={13} aria-hidden="true" /> {t('sso.saved')}
                  </p>
                )}
                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                  {state.config && (
                    <button type="button" onClick={remove} disabled={saving} style={{
                      padding: '8px 14px', borderRadius: 7, border: '1px solid var(--border)',
                      background: 'transparent', color: 'var(--muted)', fontSize: 13, cursor: 'pointer',
                    }}>{t('sso.remove')}</button>
                  )}
                  <button type="submit" disabled={saving} style={{
                    padding: '8px 20px', borderRadius: 7, border: 'none', background: 'var(--accent)',
                    color: '#fff', fontSize: 13, fontWeight: 600, cursor: saving ? 'wait' : 'pointer',
                  }}>{saving ? t('users.saving') : t('users.save_changes')}</button>
                </div>
              </form>
            )}
          </div>
        )}
      </Card>
    </div>
  )
}
