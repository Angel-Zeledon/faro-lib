'use client'
import { useCallback, useEffect, useState } from 'react'
import { ShieldCheck, KeyRound, RefreshCw } from 'lucide-react'
import Spinner from '@/components/ui/Spinner'
import { Toggle } from './Toggle'
import { useLanguage } from '@/contexts/LanguageContext'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useToast } from '@/contexts/ToastContext'
import { getUser } from '@/lib/auth'
import {
  getMfaStatus, mfaDisable, mfaRegenerateCodes, getMfaPolicy, setMfaPolicy, resetUserMfa,
  type MfaStatus, type MfaPolicy,
} from '@/lib/api'
import MfaEnrollFlow, {
  CodeInput, PrimaryButton, GhostButton, ErrorLine, RecoveryCodes,
} from './MfaEnrollFlow'

/**
 * "Two-step verification" inside Mi cuenta -> Seguridad: the person's own
 * authenticator, and, for an admin, the organization-wide requirement and the
 * reset of a locked-out colleague. Everything here talks to the Rust routes
 * under /mfa (backend-rs/src/routes/mfa/).
 */

type Panel = 'none' | 'enroll' | 'disable' | 'regenerate' | 'codes'

function Row({ children }: { children: React.ReactNode }) {
  return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>{children}</div>
}

export default function TwoStepSignIn() {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const { addToast } = useToast()
  const [status, setStatus] = useState<MfaStatus | null>(null)
  const [failed, setFailed] = useState(false)
  const [panel, setPanel] = useState<Panel>('none')
  const [code, setCode] = useState('')
  const [useRecovery, setUseRecovery] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [newCodes, setNewCodes] = useState<string[]>([])

  const load = useCallback(() => {
    setFailed(false)
    getMfaStatus().then(setStatus).catch(() => setFailed(true))
  }, [])
  useEffect(() => { load() }, [load])

  function closePanel() {
    setPanel('none'); setCode(''); setError(null); setUseRecovery(false)
  }

  async function runDisable() {
    setBusy(true); setError(null)
    try {
      await mfaDisable(code)
      addToast(t('mfa.disabled_ok'), '', 'success')
      closePanel(); load()
    } catch (e) {
      setError(errorDetail(e) || t('mfa.error_generic'))
    } finally { setBusy(false) }
  }

  async function runRegenerate() {
    setBusy(true); setError(null)
    try {
      const r = await mfaRegenerateCodes(code)
      setNewCodes(r.recovery_codes)
      setCode('')
      setPanel('codes')
      load()
    } catch (e) {
      setError(errorDetail(e) || t('mfa.error_generic'))
    } finally { setBusy(false) }
  }

  if (failed) {
    return (
      <div style={sectionStyle}>
        <ErrorLine text={t('mfa.status_unavailable')} />
      </div>
    )
  }
  if (!status) return null

  const codeOk = useRecovery ? code.trim().length >= 10 : code.length === 6
  const me = getUser()

  return (
    <div style={sectionStyle} data-testid="two-step-section">
      <Row>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 13, color: 'var(--text)', fontWeight: 500, display: 'flex', alignItems: 'center', gap: 6 }}>
            <ShieldCheck size={14} aria-hidden="true" />
            {t('mfa.section_title')}
            <span style={{
              fontSize: 10.5, fontWeight: 700, padding: '2px 8px', borderRadius: 99,
              background: status.enrolled ? 'rgba(46,139,98,0.12)' : 'rgba(120,120,120,0.14)',
              color: status.enrolled ? '#2E8B62' : 'var(--dim)',
            }}>
              {status.enrolled ? t('mfa.status_on') : t('mfa.status_off')}
            </span>
          </div>
          <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2, lineHeight: 1.5 }}>{t('mfa.section_desc')}</div>
        </div>
        {!status.enrolled && panel === 'none' && (
          <PrimaryButton variant="app" onClick={() => setPanel('enroll')}>{t('mfa.enable')}</PrimaryButton>
        )}
      </Row>

      {status.enrolled && (
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>
            {t('mfa.codes_left', { n: status.recovery_codes_remaining })}
          </div>
          {status.required_by_tenant && (
            <div style={{ fontSize: 12, color: 'var(--dim)' }}>{t('mfa.required_note')}</div>
          )}
          {panel === 'none' && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <GhostButton variant="app" onClick={() => setPanel('regenerate')}>
                <RefreshCw size={13} aria-hidden="true" />{t('mfa.regenerate')}
              </GhostButton>
              {status.can_disable && (
                <GhostButton variant="app" onClick={() => setPanel('disable')}>{t('mfa.disable')}</GhostButton>
              )}
            </div>
          )}
        </div>
      )}

      {panel === 'enroll' && (
        <div style={{ marginTop: 14 }}>
          <MfaEnrollFlow
            variant="app"
            onCancel={closePanel}
            onEnrolled={() => { addToast(t('mfa.enabled_ok'), '', 'success'); closePanel(); load() }}
          />
        </div>
      )}

      {(panel === 'disable' || panel === 'regenerate') && (
        <form
          onSubmit={e => { e.preventDefault(); if (codeOk) (panel === 'disable' ? runDisable() : runRegenerate()) }}
          style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 12 }}
        >
          <div style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.5 }}>
            {panel === 'disable' ? t('mfa.disable_prompt') : t('mfa.regenerate_prompt')}
          </div>
          <CodeInput
            id="mfa-reauth-code" variant="app" autoFocus recovery={useRecovery}
            value={code} onChange={setCode}
            label={useRecovery ? t('mfa.recovery_label') : t('mfa.code_label')}
          />
          <button
            type="button" onClick={() => { setUseRecovery(v => !v); setCode('') }}
            style={{ all: 'unset', cursor: 'pointer', fontSize: 12, color: 'var(--dim)', textDecoration: 'underline', alignSelf: 'flex-start' }}
          >
            {useRecovery ? t('mfa.use_app') : t('mfa.use_recovery')}
          </button>
          {error && <ErrorLine text={error} />}
          <div style={{ display: 'flex', gap: 8 }}>
            <PrimaryButton type="submit" variant="app" loading={busy} disabled={!codeOk}>
              {panel === 'disable' ? t('mfa.disable') : t('mfa.regenerate')}
            </PrimaryButton>
            <GhostButton variant="app" onClick={closePanel}>{t('mfa.cancel')}</GhostButton>
          </div>
        </form>
      )}

      {panel === 'codes' && (
        <div style={{ marginTop: 14 }}>
          <RecoveryCodes
            codes={newCodes} variant="app"
            onSaved={() => { addToast(t('mfa.codes_regenerated'), '', 'success'); setNewCodes([]); closePanel() }}
          />
        </div>
      )}

      {me?.role === 'admin' && <PolicyBlock myEnrolled={status.enrolled} onChanged={load} />}
    </div>
  )
}

const sectionStyle: React.CSSProperties = {
  marginTop: 18, paddingTop: 16, borderTop: '1px solid var(--border)',
}

// ── Admin: the organization's requirement and the reset of a colleague ───────

function PolicyBlock({ myEnrolled, onChanged }: { myEnrolled: boolean; onChanged: () => void }) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const { addToast } = useToast()
  const [policy, setPolicy] = useState<MfaPolicy | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const me = getUser()

  const load = useCallback(() => { getMfaPolicy().then(setPolicy).catch(() => setPolicy(null)) }, [])
  useEffect(() => { load() }, [load, myEnrolled])

  if (!policy) return null

  async function toggle() {
    if (!policy) return
    const next = !policy.required
    if (next) {
      if (!myEnrolled) { setError(t('errors.mfa_admin_not_enrolled')); return }
      const pending = policy.users.filter(u => !u.enrolled).length
      const ok = await confirm({
        title: t('mfa.confirm_require_title'),
        message: t('mfa.confirm_require_body', { n: pending }),
        confirmLabel: t('mfa.confirm_require'),
        danger: true,
      })
      if (!ok) return
    }
    setBusy(true); setError(null)
    try {
      await setMfaPolicy(next)
      addToast(t(next ? 'mfa.policy_saved_on' : 'mfa.policy_saved_off'), '', 'success')
      load(); onChanged()
    } catch (e) {
      setError(errorDetail(e) || t('mfa.error_generic'))
    } finally { setBusy(false) }
  }

  async function reset(u: MfaPolicy['users'][number]) {
    const ok = await confirm({
      title: t('mfa.reset_confirm_title'),
      message: t('mfa.reset_confirm_body', { email: u.email }),
      confirmLabel: t('mfa.reset_user'),
      danger: true,
    })
    if (!ok) return
    try {
      await resetUserMfa(u.id)
      addToast(t('mfa.reset_ok', { email: u.email }), '', 'success')
      load()
    } catch (e) {
      addToast(errorDetail(e) || t('mfa.error_generic'), '', 'error')
    }
  }

  return (
    <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px dashed var(--border)' }} data-testid="mfa-policy">
      <Row>
        <div style={{ minWidth: 0, flex: '1 1 240px' }}>
          <div style={{ fontSize: 13, color: 'var(--text)', fontWeight: 500, display: 'flex', alignItems: 'center', gap: 6 }}>
            <KeyRound size={14} aria-hidden="true" />{t('mfa.policy_title')}
          </div>
          <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2, lineHeight: 1.5 }}>{t('mfa.policy_desc')}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>
            {t('mfa.policy_enrolled_count', { enrolled: policy.enrolled_users, total: policy.total_users })}
          </div>
        </div>
        {busy ? <Spinner size={14} /> : (
          <Toggle on={policy.required} onChange={toggle} label={t('mfa.policy_title')} />
        )}
      </Row>
      {!myEnrolled && !policy.required && (
        <div style={{ fontSize: 11.5, color: '#b45309', marginTop: 8 }}>{t('mfa.policy_enroll_first')}</div>
      )}
      {policy.required && (
        <div style={{ fontSize: 11.5, color: 'var(--dim)', marginTop: 8, lineHeight: 1.5 }}>{t('mfa.policy_on_note')}</div>
      )}
      {error && <div style={{ marginTop: 8 }}><ErrorLine text={error} /></div>}

      <div style={{ marginTop: 12, fontSize: 11.5, fontWeight: 600, color: 'var(--muted)' }}>{t('mfa.users_title')}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }}>
        {policy.users.map(u => (
          <div key={u.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 12.5, color: 'var(--text)', overflowWrap: 'anywhere' }}>{u.full_name || u.email}</div>
              {u.full_name && <div style={{ fontSize: 11, color: 'var(--dim)', overflowWrap: 'anywhere' }}>{u.email}</div>}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 11, color: u.enrolled ? '#2E8B62' : 'var(--dim)', fontWeight: 600 }}>
                {u.enrolled ? t('mfa.user_enrolled') : t('mfa.user_not')}
              </span>
              {u.enrolled && u.id !== me?.id && (
                <GhostButton variant="app" onClick={() => reset(u)}>{t('mfa.reset_user')}</GhostButton>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
