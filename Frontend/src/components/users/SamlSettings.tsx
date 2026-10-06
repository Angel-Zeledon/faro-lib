'use client'
/**
 * Company sign-in over SAML 2.0 - the administrator's side.
 *
 * The sibling of `SsoSettings` (OpenID Connect): a collapsed admin-only card at
 * the foot of the users screen. A tenant uses one protocol or the other; the
 * backend refuses the second with `sso_protocol_conflict`, rendered like any
 * other error.
 *
 * It states where things stand before offering a field:
 *
 *   - the installation has company sign-in switched off -> a sentence naming the
 *     switch, and no form;
 *   - otherwise: what the identity provider needs from us (entity ID, ACS URL,
 *     a metadata download), then the provider (pasted metadata XML OR the three
 *     fields), the e-mail domains, the role for new people, the optional
 *     attribute -> role table, "active" and "make it mandatory".
 *
 * Nothing secret exists here: a SAML provider is identified by its PUBLIC
 * signing certificate. An empty provider section on a later save means "keep
 * the one stored"; "mandatory" can only be ticked once an administrator has
 * signed in through this provider (the backend enforces it, the page explains).
 */
import { useCallback, useEffect, useState } from 'react'
import { Building2, ChevronDown, AlertTriangle, CheckCircle2, Trash2, Download } from 'lucide-react'
import {
  getSamlConfig, saveSamlConfig, deleteSamlConfig, downloadSamlSpMetadata, type SamlConfig,
} from '@/lib/api'
import Card from '@/components/ui/Card'
import Input, { Field, Select } from '@/components/ui/Input'
import { useLanguage } from '@/contexts/LanguageContext'
import { roleLabel } from '@/lib/enumLabels'

type Role = 'analyst' | 'viewer'
type Mode = 'metadata' | 'manual'

interface GroupRule { group: string; role: Role }

function rulesFrom(map: Record<string, Role>): GroupRule[] {
  return Object.entries(map).map(([group, role]) => ({ group, role }))
}

/** Certificates pasted as PEM blocks, or as base64 separated by blank lines. */
export function splitCertificates(text: string): string[] {
  const pem = text.match(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/g)
  if (pem) return pem.map(b => b.trim())
  return text.split(/\n\s*\n/).map(b => b.trim()).filter(Boolean)
}

const textareaStyle: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', minHeight: 110, padding: 10, borderRadius: 8,
  border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)',
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 11.5,
}

export function SamlSettings() {
  const { t } = useLanguage()
  const [open, setOpen] = useState(false)
  const [state, setState] = useState<{
    instance_enabled: boolean
    sp: { entity_id: string; acs_url: string }
    config: SamlConfig | null
  } | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [mode, setMode] = useState<Mode>('metadata')
  const [metadata, setMetadata] = useState('')
  const [entityId, setEntityId] = useState('')
  const [ssoUrl, setSsoUrl] = useState('')
  const [certs, setCerts] = useState('')
  const [domains, setDomains] = useState('')
  const [role, setRole] = useState<Role>('viewer')
  const [emailAttr, setEmailAttr] = useState('')
  const [groupsAttr, setGroupsAttr] = useState('')
  const [rules, setRules] = useState<GroupRule[]>([])
  const [enforce, setEnforce] = useState(false)
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const cfg = state?.config ?? null

  const apply = useCallback((cfg: SamlConfig | null) => {
    setMetadata(''); setCerts('')
    setEntityId(cfg?.idp_entity_id ?? '')
    setSsoUrl(cfg?.sso_url ?? '')
    setDomains((cfg?.allowed_domains ?? []).join(', '))
    setRole(cfg?.default_role ?? 'viewer')
    setEmailAttr(cfg?.email_attribute ?? '')
    setGroupsAttr(cfg?.groups_attribute ?? '')
    setRules(rulesFrom(cfg?.group_roles ?? {}))
    setEnforce(cfg?.enforce_sso ?? false)
    setEnabled(cfg?.enabled ?? true)
  }, [])

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const r = await getSamlConfig()
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
      // Metadata mode with the box empty keeps the stored provider. Manual mode
      // keeps it only if the certificate box is empty AND the two fields are
      // untouched: an edited field is never silently dropped, it is sent and the
      // backend names what is missing.
      const fieldsChanged = !cfg
        || entityId.trim() !== cfg.idp_entity_id || ssoUrl.trim() !== cfg.sso_url
      const provider = mode === 'metadata'
        ? { metadata_xml: metadata.trim() || null }
        : (certs.trim() || fieldsChanged
          ? { idp_entity_id: entityId.trim(), sso_url: ssoUrl.trim(), certificates: splitCertificates(certs) }
          : {})
      const res = await saveSamlConfig({
        ...provider,
        allowed_domains: domains.split(/[,\s]+/).map(d => d.trim()).filter(Boolean),
        default_role: role,
        enforce_sso: enforce,
        email_attribute: emailAttr.trim() || null,
        groups_attribute: groupsAttr.trim() || null,
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
    if (!window.confirm(t('saml.confirm_remove'))) return
    setSaving(true); setError(null)
    try {
      await deleteSamlConfig()
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : t('users.operation_failed'))
    } finally { setSaving(false) }
  }

  const status = !state ? null : !state.instance_enabled ? 'instance_off' : 'ready'
  const needsProvider = !cfg

  return (
    <div style={{ marginTop: 16 }} data-testid="saml-settings">
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
            <span style={{ display: 'block', fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{t('saml.title')}</span>
            <span style={{ display: 'block', fontSize: 11.5, color: 'var(--dim)', marginTop: 2 }}>{t('saml.subtitle')}</span>
          </span>
          <ChevronDown size={14} color="var(--dim)" style={{ transform: open ? 'rotate(180deg)' : undefined }} aria-hidden="true" />
        </button>

        {open && (
          <div style={{ padding: '4px 16px 18px', borderTop: '1px solid var(--border)' }}>
            {loadError && <p role="alert" style={{ fontSize: 12.5, color: '#C0504D' }}>{loadError}</p>}
            {!state && !loadError && <p style={{ fontSize: 12.5, color: 'var(--dim)' }}>{t('users.loading')}</p>}

            {status === 'instance_off' && (
              <p style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.55 }}>{t('saml.instance_off')}</p>
            )}

            {status === 'ready' && state && (
              <form onSubmit={save} style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 14 }}>
                <div style={{ fontSize: 12, color: 'var(--dim)', lineHeight: 1.6 }}>
                  <p style={{ margin: '0 0 6px' }}>{t('saml.sp_hint')}</p>
                  <div>{t('saml.sp_entity')}: <code style={{ userSelect: 'all' }}>{state.sp.entity_id}</code></div>
                  <div>{t('saml.sp_acs')}: <code style={{ userSelect: 'all' }}>{state.sp.acs_url}</code></div>
                  <button type="button" onClick={() => { downloadSamlSpMetadata().catch(() => undefined) }}
                    style={{
                      all: 'unset', cursor: 'pointer', display: 'inline-flex', gap: 6, alignItems: 'center',
                      marginTop: 8, fontSize: 12, color: 'var(--accent)',
                    }}>
                    <Download size={13} aria-hidden="true" /> {t('saml.sp_download')}
                  </button>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}>{t('saml.idp_title')}</div>
                  {!needsProvider && (
                    <p style={{ margin: 0, fontSize: 11.5, color: 'var(--dim)', lineHeight: 1.5 }}>{t('saml.idp_keep')}</p>
                  )}
                  <div style={{ display: 'flex', gap: 16, fontSize: 12.5 }}>
                    {(['metadata', 'manual'] as Mode[]).map(m => (
                      <label key={m} style={{ display: 'flex', gap: 6, alignItems: 'center', color: 'var(--text)' }}>
                        <input type="radio" name="saml_mode" checked={mode === m} onChange={() => setMode(m)} />
                        {t(m === 'metadata' ? 'saml.mode_metadata' : 'saml.mode_manual')}
                      </label>
                    ))}
                  </div>

                  {mode === 'metadata' ? (
                    <Field label={t('saml.metadata_label')} htmlFor="saml-metadata">
                      <textarea id="saml-metadata" name="saml_metadata" value={metadata} style={textareaStyle}
                        required={needsProvider} spellCheck={false}
                        onChange={e => setMetadata(e.target.value)} placeholder="<EntityDescriptor …>" />
                      <p style={{ fontSize: 11, color: 'var(--dim)', margin: '4px 0 0' }}>{t('saml.metadata_hint')}</p>
                    </Field>
                  ) : (
                    <>
                      <Field label={t('saml.entity_id')} htmlFor="saml-entity">
                        <Input id="saml-entity" name="saml_entity" value={entityId} required={needsProvider}
                          onChange={e => setEntityId(e.target.value)} autoComplete="off" />
                      </Field>
                      <Field label={t('saml.sso_url')} htmlFor="saml-sso-url">
                        <Input id="saml-sso-url" name="saml_sso_url" type="url" value={ssoUrl} required={needsProvider}
                          onChange={e => setSsoUrl(e.target.value)} placeholder="https://login.example.com/saml" />
                        <p style={{ fontSize: 11, color: 'var(--dim)', margin: '4px 0 0' }}>{t('saml.sso_url_hint')}</p>
                      </Field>
                      <Field label={t('saml.certificates')} htmlFor="saml-certs">
                        <textarea id="saml-certs" name="saml_certs" value={certs} style={textareaStyle}
                          required={needsProvider} spellCheck={false}
                          onChange={e => setCerts(e.target.value)} placeholder="-----BEGIN CERTIFICATE-----" />
                        <p style={{ fontSize: 11, color: 'var(--dim)', margin: '4px 0 0' }}>{t('saml.certificates_hint')}</p>
                      </Field>
                    </>
                  )}

                  {cfg && cfg.certificates.length > 0 && (
                    <div style={{ fontSize: 11.5, color: 'var(--dim)' }} data-testid="saml-certs-stored">
                      <div style={{ fontWeight: 600 }}>{t('saml.certs_stored')}</div>
                      {cfg.certificates.map((c, i) => (
                        <div key={i} style={{ fontFamily: 'ui-monospace, monospace', color: c.expired ? '#C0504D' : undefined }}>
                          {(c.fingerprint_sha256 ?? '').slice(0, 23)}…{' '}
                          {c.expired
                            ? t('saml.cert_expired')
                            : c.not_after ? t('saml.cert_expires', { date: c.not_after.slice(0, 10) }) : ''}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <Field label={t('sso.domains')} htmlFor="saml-domains">
                  <Input id="saml-domains" name="saml_domains" required value={domains}
                    onChange={e => setDomains(e.target.value)} placeholder="example.com, example.org" />
                  <p style={{ fontSize: 11, color: 'var(--dim)', margin: '4px 0 0' }}>{t('sso.domains_hint')}</p>
                </Field>
                <Field label={t('sso.default_role')} htmlFor="saml-default-role">
                  <Select id="saml-default-role" name="saml_default_role" value={role}
                    onChange={e => setRole(e.target.value as Role)}>
                    <option value="viewer">{roleLabel(t, 'viewer')}</option>
                    <option value="analyst">{roleLabel(t, 'analyst')}</option>
                  </Select>
                  <p style={{ fontSize: 11, color: 'var(--dim)', margin: '4px 0 0' }}>{t('sso.default_role_hint')}</p>
                </Field>

                <Field label={t('saml.email_attribute')} htmlFor="saml-email-attr">
                  <Input id="saml-email-attr" name="saml_email_attr" value={emailAttr}
                    onChange={e => setEmailAttr(e.target.value)} placeholder="email" />
                  <p style={{ fontSize: 11, color: 'var(--dim)', margin: '4px 0 0' }}>{t('saml.email_attribute_hint')}</p>
                </Field>
                <Field label={t('saml.groups_attribute')} htmlFor="saml-groups-attr">
                  <Input id="saml-groups-attr" name="saml_groups_attr" value={groupsAttr}
                    onChange={e => setGroupsAttr(e.target.value)} placeholder="groups" />
                  <p style={{ fontSize: 11, color: 'var(--dim)', margin: '4px 0 0' }}>{t('saml.groups_hint')}</p>
                </Field>
                {(groupsAttr.trim() || rules.length > 0) && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {rules.map((r, i) => (
                      <div key={i} style={{ display: 'flex', gap: 8 }}>
                        <Input name={`saml_group_${i}`} aria-label={t('sso.group_name')} value={r.group}
                          onChange={e => setRules(rs => rs.map((x, j) => j === i ? { ...x, group: e.target.value } : x))}
                          placeholder={t('sso.group_name')} />
                        <Select name={`saml_group_role_${i}`} aria-label={t('sso.group_role')} value={r.role}
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
                    <span style={{ display: 'block', fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>
                      {t('sso.enforce_hint')}
                    </span>
                    {cfg && (
                      <span style={{ display: 'block', fontSize: 11, marginTop: 2, color: cfg.admin_signed_in ? '#2E8B62' : 'var(--dim)' }}>
                        {t(cfg.admin_signed_in ? 'saml.enforce_ready' : 'saml.enforce_needs_sign_in')}
                      </span>
                    )}
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
                  {cfg && (
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
