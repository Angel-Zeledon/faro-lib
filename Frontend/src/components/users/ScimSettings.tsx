'use client'
/**
 * SCIM provisioning - the administrator's side, inside the company sign-in card.
 *
 * What it says, in order:
 *   - SSO not saved/enabled yet      -> one sentence, no button (the backend
 *                                       refuses a token without SSO anyway);
 *   - the base URL to paste in the identity provider;
 *   - no token                       -> "turn on", with the admin opt-in;
 *   - a token                        -> its non-secret id, last sync, last
 *                                       change, the opt-in, rotate and revoke;
 *   - a token just minted            -> shown ONCE, with a copy button and the
 *                                       warning that it will not be shown again;
 *   - the provisioning log, refusals included, each with its reason.
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Copy, KeyRound, CheckCircle2, XCircle } from 'lucide-react'
import {
  getScimStatus, mintScimToken, updateScimToken, revokeScimToken,
  type ScimEvent, type ScimStatus,
} from '@/lib/api'
import { useLanguage } from '@/contexts/LanguageContext'
import { localeFor } from '@/lib/numberLocale'

const btn = {
  padding: '7px 14px', borderRadius: 7, border: '1px solid var(--border)',
  background: 'transparent', color: 'var(--text)', fontSize: 12.5, cursor: 'pointer',
} as const

const codeBox = {
  display: 'block', padding: '8px 10px', borderRadius: 6, background: 'var(--surface-2, var(--bg))',
  border: '1px solid var(--border)', fontSize: 12, wordBreak: 'break-all', userSelect: 'all',
} as const

function CopyButton({ value }: { value: string }) {
  const { t } = useLanguage()
  const [done, setDone] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setDone(true)
      setTimeout(() => setDone(false), 1800)
    } catch {
      // Clipboard blocked (insecure origin, permissions): the value is still
      // selectable on screen, which is why the box uses `user-select: all`.
    }
  }
  return (
    <button type="button" onClick={copy} style={{ ...btn, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <Copy size={12} aria-hidden="true" /> {done ? t('scim.copied') : t('scim.copy')}
    </button>
  )
}

export function ScimSettings({ ssoSaved }: { ssoSaved: boolean }) {
  const { t, lang } = useLanguage()
  const [state, setState] = useState<ScimStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [freshToken, setFreshToken] = useState<string | null>(null)
  const [manageAdmins, setManageAdmins] = useState(false)

  const load = useCallback(async () => {
    setError(null)
    try {
      const s = await getScimStatus()
      setState(s)
      setManageAdmins(s.token?.manage_admins ?? false)
    } catch (e) {
      setError(e instanceof Error ? e.message : t('users.operation_failed'))
    }
  }, [t])

  // Re-read when the SSO form is saved: SCIM's availability follows it.
  useEffect(() => { load() }, [load, ssoSaved])

  const when = (iso: string | null) => iso
    ? new Date(iso).toLocaleString(localeFor(lang), { dateStyle: 'short', timeStyle: 'short' })
    : t('scim.never')

  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(null)
    try { await fn() } catch (e) {
      setError(e instanceof Error ? e.message : t('users.operation_failed'))
    } finally { setBusy(false) }
  }

  const mint = (confirmRotate: boolean) => run(async () => {
    if (confirmRotate && !window.confirm(t('scim.confirm_rotate'))) return
    const r = await mintScimToken(manageAdmins)
    setFreshToken(r.token)
    await load()
  })

  const revoke = () => run(async () => {
    if (!window.confirm(t('scim.confirm_revoke'))) return
    await revokeScimToken()
    setFreshToken(null)
    await load()
  })

  const toggleAdmins = (value: boolean) => {
    setManageAdmins(value)
    if (!state?.token) return  // applied when the token is minted
    run(async () => {
      try {
        await updateScimToken(value)
      } catch (e) {
        setManageAdmins(!value)  // the switch must not claim what the server refused
        throw e
      }
      await load()
    })
  }

  const eventText = (e: ScimEvent) => {
    if (e.outcome === 'success') return t('scim.outcome_success')
    const key = `scim.error.${e.error_code ?? ''}`
    const known = t(key)
    return known !== key ? `${t('scim.outcome_error')}: ${known}`
      : t('scim.refused_generic', { code: e.error_code ?? String(e.http_status) })
  }
  const opText = (op: string) => {
    const key = `scim.op.${op}`
    const label = t(key)
    return label !== key ? label : op
  }

  return (
    <section data-testid="scim-settings" style={{
      marginTop: 18, paddingTop: 16, borderTop: '1px solid var(--border)',
      display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0,
    }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
        <KeyRound size={14} color="var(--dim)" aria-hidden="true" style={{ marginTop: 2, flexShrink: 0 }} />
        <div style={{ minWidth: 0 }}>
          <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{t('scim.title')}</h3>
          <p style={{ margin: '2px 0 0', fontSize: 11.5, color: 'var(--dim)', lineHeight: 1.5 }}>{t('scim.subtitle')}</p>
        </div>
      </div>

      {error && (
        <p role="alert" style={{ margin: 0, display: 'flex', gap: 6, alignItems: 'center', fontSize: 12.5, color: '#C0504D' }}>
          <AlertTriangle size={13} aria-hidden="true" /> {error}
        </p>
      )}
      {!state && !error && <p style={{ margin: 0, fontSize: 12.5, color: 'var(--dim)' }}>{t('users.loading')}</p>}

      {state && !state.sso_ready && !state.token && (
        <p style={{ margin: 0, fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.55 }}>{t('scim.needs_sso')}</p>
      )}

      {state && (state.sso_ready || state.token) && (
        <>
          {!state.sso_ready && (
            <p role="alert" style={{ margin: 0, fontSize: 12.5, color: '#C0504D', lineHeight: 1.55 }}>{t('scim.needs_sso')}</p>
          )}
          <div>
            <div style={{ fontSize: 11.5, color: 'var(--dim)', marginBottom: 4 }}>{t('scim.base_url')}</div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <code style={{ ...codeBox, flex: '1 1 220px', minWidth: 0 }}>{state.base_url}</code>
              <CopyButton value={state.base_url} />
            </div>
          </div>

          {freshToken && (
            <div role="status" style={{
              padding: 12, borderRadius: 8, border: '1px solid #D9A441',
              background: 'rgba(217,164,65,0.08)', display: 'flex', flexDirection: 'column', gap: 8,
            }}>
              <p style={{ margin: 0, fontSize: 12.5, color: 'var(--text)', lineHeight: 1.5 }}>
                <AlertTriangle size={13} aria-hidden="true" style={{ verticalAlign: '-2px', marginRight: 6 }} />
                {t('scim.token_once')}
              </p>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <code aria-label={t('scim.token_label')} style={{ ...codeBox, flex: '1 1 220px', minWidth: 0 }}>{freshToken}</code>
                <CopyButton value={freshToken} />
              </div>
            </div>
          )}

          <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 12.5, color: 'var(--text)' }}>
            <input type="checkbox" checked={manageAdmins} disabled={busy}
              onChange={e => toggleAdmins(e.target.checked)} style={{ marginTop: 2 }} />
            <span>
              {t('scim.manage_admins')}
              <span style={{ display: 'block', fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>{t('scim.manage_admins_hint')}</span>
            </span>
          </label>

          {state.token ? (
            <>
              <dl style={{
                margin: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
                gap: '8px 16px', fontSize: 12,
              }}>
                <div style={{ minWidth: 0 }}>
                  <dt style={{ color: 'var(--dim)' }}>{t('scim.token_label')}</dt>
                  <dd style={{ margin: 0, wordBreak: 'break-all' }}><code>{state.token.hint}</code></dd>
                </div>
                <div><dt style={{ color: 'var(--dim)' }}>{t('scim.created_at')}</dt><dd style={{ margin: 0 }}>{when(state.token.created_at)}</dd></div>
                <div><dt style={{ color: 'var(--dim)' }}>{t('scim.last_used')}</dt><dd style={{ margin: 0 }}>{when(state.last_used_at)}</dd></div>
                <div><dt style={{ color: 'var(--dim)' }}>{t('scim.last_change')}</dt><dd style={{ margin: 0 }}>{when(state.last_change_at)}</dd></div>
              </dl>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                <button type="button" onClick={revoke} disabled={busy} style={{ ...btn, color: 'var(--muted)' }}>{t('scim.revoke')}</button>
                <button type="button" onClick={() => mint(true)} disabled={busy || !state.sso_ready} style={btn}>{t('scim.rotate')}</button>
              </div>
            </>
          ) : (
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button type="button" onClick={() => mint(false)} disabled={busy || !state.sso_ready} style={{
                padding: '8px 18px', borderRadius: 7, border: 'none', background: 'var(--accent)',
                color: '#fff', fontSize: 13, fontWeight: 600, cursor: busy ? 'wait' : 'pointer',
              }}>{busy ? t('users.saving') : t('scim.enable')}</button>
            </div>
          )}

          <p style={{ margin: 0, fontSize: 11, color: 'var(--dim)', lineHeight: 1.5 }}>{t('scim.groups_hint')}</p>

          <div>
            <h4 style={{ margin: '4px 0 6px', fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>{t('scim.log_title')}</h4>
            {state.events.length === 0 ? (
              <p style={{ margin: 0, fontSize: 12, color: 'var(--dim)' }}>{t('scim.log_empty')}</p>
            ) : (
              <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
                {state.events.map(e => (
                  <li key={e.id} style={{
                    display: 'flex', flexWrap: 'wrap', gap: '2px 10px', alignItems: 'baseline',
                    fontSize: 12, padding: '6px 0', borderBottom: '1px solid var(--border)', minWidth: 0,
                  }}>
                    {e.outcome === 'success'
                      ? <CheckCircle2 size={12} color="#2E8B62" aria-hidden="true" style={{ alignSelf: 'center' }} />
                      : <XCircle size={12} color="#C0504D" aria-hidden="true" style={{ alignSelf: 'center' }} />}
                    <span style={{ color: 'var(--dim)', whiteSpace: 'nowrap' }}>{when(e.created_at)}</span>
                    <span style={{ fontWeight: 600 }}>{opText(e.operation)}</span>
                    {e.email && <span style={{ wordBreak: 'break-all', minWidth: 0 }}>{e.email}</span>}
                    <span style={{ flexBasis: '100%', color: e.outcome === 'success' ? 'var(--muted)' : '#C0504D' }}>
                      {eventText(e)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </section>
  )
}
