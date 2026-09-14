'use client'
/**
 * /instalacion — what this deployment has, what is on, and what is off.
 *
 * Two tabs, because they answer to two different people:
 *
 *  - **This installation** belongs to whoever runs the deployment — the buyer
 *    of the source. It is gated by `INSTANCE_ADMIN_EMAILS`, NOT by the `admin`
 *    role, which exists inside a tenant and would otherwise let anybody who
 *    signs up rewrite the owner's credentials.
 *  - **My channels** belongs to a company inside that deployment: its own
 *    WhatsApp sender, its own mail transport. Scope comes from the token, so
 *    there is nothing here to point at somebody else's tenant.
 *
 * The rule the whole screen exists to make visible: a service without its
 * credential is OFF and says what that costs. Not an error, not a blank
 * screen, and never an answer from somewhere else.
 *
 * A stored secret never comes back from the API — a save takes one, and the
 * screen afterwards shows at most four trailing characters. The input is
 * therefore always empty on load; typing in it replaces, and clearing it
 * (with Save) returns the field to the environment.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  getServices, saveService, resetService, probeService,
  getTenantServices, saveTenantService, resetTenantService, probeTenantService,
  type ServiceView, type ServicesReport, type TenantServicesReport,
  type ConfigFieldView, type ProbeView,
} from '@/lib/api'
import { SERVICE_CONFIG, type FieldKey, type ServiceKey } from '@/i18n/serviceConfig'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { useToast } from '@/contexts/ToastContext'
import Card from '@/components/ui/Card'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Spinner from '@/components/ui/Spinner'
import {
  AlertTriangle, CheckCircle2, CircleSlash, Lock, PlugZap, ServerCog, XCircle,
} from 'lucide-react'

type Tab = 'instance' | 'tenant'

// ── State pill ───────────────────────────────────────────────────────────────
// Four states, four colours, and `degraded` is the one that earns this screen
// its keep: "configured" and "working" are different claims, and a key that was
// revoked at the provider looks exactly like a key that is fine until somebody
// asks.

const STATE_STYLE: Record<string, { color: string; bg: string; Icon: React.ElementType }> = {
  ready:           { color: '#22c55e', bg: 'rgba(34,197,94,0.10)',  Icon: CheckCircle2 },
  on:              { color: '#22c55e', bg: 'rgba(34,197,94,0.10)',  Icon: CheckCircle2 },
  not_configured:  { color: 'var(--dim)', bg: 'var(--surface-2)',   Icon: CircleSlash },
  off:             { color: 'var(--dim)', bg: 'var(--surface-2)',   Icon: CircleSlash },
  degraded:        { color: '#ef4444', bg: 'rgba(239,68,68,0.10)',  Icon: XCircle },
}

function StatePill({ state }: { state: string }) {
  const { lang } = useLanguage()
  const ui = SERVICE_CONFIG[lang].ui
  const label: Record<string, string> = {
    ready: ui.stateReady, on: ui.stateOn, not_configured: ui.stateNotConfigured,
    off: ui.stateOff, degraded: ui.stateDegraded,
  }
  const style = STATE_STYLE[state] ?? STATE_STYLE.not_configured
  const Icon = style.Icon
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 9px', borderRadius: 999, fontSize: 11, fontWeight: 600,
      color: style.color, background: style.bg,
    }}>
      <Icon size={12} aria-hidden="true" />
      {label[state] ?? state}
    </span>
  )
}

function probeMessage(ui: typeof SERVICE_CONFIG['es']['ui'], code: string): string {
  const byCode: Record<string, string> = {
    ok: ui.probeOk,
    not_configured: ui.probeNotConfigured,
    auth_failed: ui.probeAuthFailed,
    unreachable: ui.probeUnreachable,
    timeout: ui.probeTimeout,
    rejected: ui.probeRejected,
    missing_dependency: ui.probeMissingDependency,
  }
  return byCode[code] ?? ui.probeUnexpected
}

// ── One field row ────────────────────────────────────────────────────────────

function FieldRow({
  field, draft, onChange, disabled,
}: {
  field: ConfigFieldView
  draft: string | undefined
  onChange: (key: string, value: string) => void
  disabled: boolean
}) {
  const { lang } = useLanguage()
  const narrow = useIsNarrow()
  const copy = SERVICE_CONFIG[lang]
  const ui = copy.ui
  const doc = copy.fields[field.key as FieldKey] ?? field.doc

  const sourceLabel: Record<string, string> = {
    tenant: ui.sourceTenant, instance: ui.sourceInstance,
    env: ui.sourceEnv, default: ui.sourceDefault,
  }

  const readOnly = disabled || !field.editable
  const shown = field.secret
    ? (draft ?? '')
    : (draft ?? (field.value === null || field.value === undefined ? '' : String(field.value)))

  return (
    <div style={{
      display: 'grid',
      // Two columns on a laptop; stacked on a phone. Side by side at 400px the
      // input collapsed to the width of one word — the field was on screen and
      // impossible to type into, which is worse than being absent.
      gridTemplateColumns: narrow ? 'minmax(0, 1fr)' : 'minmax(0, 260px) minmax(0, 1fr)',
      gap: narrow ? 8 : 16, alignItems: 'start', padding: '14px 0',
      borderTop: '1px solid var(--border)',
    }}>
      <div style={{ minWidth: 0 }}>
        <div style={{
          fontFamily: 'monospace', fontSize: 12, fontWeight: 600,
          color: 'var(--text)', wordBreak: 'break-all',
        }}>
          {field.env}
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 5 }}>
          {field.required && (
            <span style={{ fontSize: 10, color: '#f59e0b', fontWeight: 600 }}>
              {ui.required}
            </span>
          )}
          {!field.editable && (
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 3,
              fontSize: 10, color: 'var(--dim)',
            }} title={ui.envOnlyHelp}>
              <Lock size={9} aria-hidden="true" />{ui.envOnly}
            </span>
          )}
          <span style={{ fontSize: 10, color: 'var(--dim)' }}>
            {ui.sourceLabel}: {sourceLabel[field.source] ?? field.source}
          </span>
        </div>
        <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 6, lineHeight: 1.5 }}>
          {doc}
        </div>
      </div>

      <div style={{ minWidth: 0 }}>
        {field.secret ? (
          <>
            <Input
              type="password"
              autoComplete="new-password"
              name={field.key}
              aria-label={field.env}
              disabled={readOnly}
              placeholder={readOnly ? '' : ui.secretPlaceholder}
              value={shown}
              onChange={e => onChange(field.key, e.target.value)}
              style={{ width: '100%', fontSize: 12, fontFamily: 'monospace' }}
            />
            <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 5 }}>
              {!field.has_value
                ? ui.secretEmpty
                : field.inherited
                  // Set, but by the installation — so there is no hint to show,
                  // and saying "Guardada ·" with nothing after it would read as
                  // a rendering bug rather than a boundary.
                  ? ui.secretInherited
                  : `${ui.secretSet} · ${field.hint ?? ''}`}
            </div>
          </>
        ) : (
          <Input
            name={field.key}
            aria-label={field.env}
            disabled={readOnly}
            value={shown}
            onChange={e => onChange(field.key, e.target.value)}
            style={{ width: '100%', fontSize: 12, fontFamily: 'monospace' }}
          />
        )}
        {!readOnly && (
          <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 4 }}>
            {ui.clearHint}
          </div>
        )}
      </div>
    </div>
  )
}

// ── One service card ─────────────────────────────────────────────────────────

function ServiceCard({
  service, scope, onChanged,
}: {
  service: ServiceView
  scope: Tab
  onChanged: () => void
}) {
  const { lang } = useLanguage()
  const copy = SERVICE_CONFIG[lang]
  const ui = copy.ui
  const { addToast } = useToast()
  const confirm = useConfirm()

  const text = copy.services[service.key as ServiceKey]
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [probe, setProbe] = useState<ProbeView | null>(service.last_check)

  useEffect(() => { setProbe(service.last_check) }, [service.last_check])

  const editable = service.editable && service.editable_fields.length > 0
  const dirty = Object.keys(draft).length > 0

  // A field this scope may write. In the tenant tab that is only the service's
  // own editable fields — a borrowed Twilio SID belongs to the installation.
  const canEdit = useCallback(
    (f: ConfigFieldView) => editable && service.editable_fields.includes(f.key),
    [editable, service.editable_fields],
  )

  const onChange = (key: string, value: string) =>
    setDraft(d => ({ ...d, [key]: value }))

  const save = async () => {
    setSaving(true)
    try {
      const call = scope === 'tenant' ? saveTenantService : saveService
      await call(service.key, draft)
      setDraft({})
      addToast(text?.name ?? service.key, ui.saved, 'success')
      onChanged()
    } catch {
      // The API client already surfaced the reason — a refused secret, an
      // unparseable number, a store that could not be written. Nothing was
      // saved in any of those cases, so the draft stays on screen.
    } finally {
      setSaving(false)
    }
  }

  const reset = async () => {
    if (!(await confirm({ title: ui.reset, message: ui.resetConfirm, danger: true }))) return
    setSaving(true)
    try {
      const call = scope === 'tenant' ? resetTenantService : resetService
      await call(service.key)
      setDraft({})
      onChanged()
    } catch { /* reported by the API client */ }
    finally { setSaving(false) }
  }

  const test = async () => {
    setTesting(true)
    try {
      const call = scope === 'tenant' ? probeTenantService : probeService
      setProbe(await call(service.key))
    } catch { /* reported by the API client */ }
    finally { setTesting(false) }
  }

  return (
    <Card padding="18px 20px" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ fontSize: 14, fontWeight: 700 }}>{text?.name ?? service.key}</div>
        <StatePill state={service.state} />
        <div style={{ flex: 1 }} />
        {service.has_probe && service.state !== 'not_configured' && (
          <Button variant="secondary" size="sm" loading={testing}
                  icon={<PlugZap size={12} />} onClick={test}>
            {testing ? ui.testing : ui.test}
          </Button>
        )}
      </div>

      <div style={{ fontSize: 12, color: 'var(--muted)' }}>{text?.summary ?? service.summary}</div>

      {/* What is lost while it is off — the sentence this whole panel is for.
          Shown only when it IS off: on a healthy service it would read as a
          warning about nothing. */}
      {(service.state === 'not_configured' || service.state === 'off') && (
        <div style={{
          display: 'flex', gap: 8, alignItems: 'flex-start',
          padding: '10px 12px', borderRadius: 8,
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          fontSize: 12, color: 'var(--text)', lineHeight: 1.55,
        }}>
          <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2, color: '#f59e0b' }} aria-hidden="true" />
          <div>
            <div>{text?.whatBreaks ?? service.what_breaks}</div>
            {service.missing.length > 0 && (
              <div style={{ marginTop: 6, fontSize: 11, color: 'var(--dim)' }}>
                {ui.missingLabel}: <span style={{ fontFamily: 'monospace' }}>
                  {service.missing.join(', ')}
                </span>
                {/* Some services are an OR: email runs on Resend or on SMTP.
                    Naming one path without the other reads as the only path. */}
                {service.missing_alternatives?.map((group, i) => (
                  <span key={i}>
                    {' '}{ui.orElse}{' '}
                    <span style={{ fontFamily: 'monospace' }}>{group.join(' + ')}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {probe && (
        <div style={{
          fontSize: 11, color: probe.ok ? '#22c55e' : '#ef4444',
          display: 'flex', gap: 6, alignItems: 'flex-start',
        }}>
          {probe.ok ? <CheckCircle2 size={12} style={{ marginTop: 1 }} /> : <XCircle size={12} style={{ marginTop: 1 }} />}
          <span>
            {probeMessage(ui, probe.code)}
            {probe.detail ? ` — ${probe.detail}` : ''}
          </span>
        </div>
      )}

      {text?.note && (
        <div style={{ fontSize: 11, color: 'var(--dim)', lineHeight: 1.55, whiteSpace: 'pre-line' }}>
          {text.note}
        </div>
      )}

      <div>
        {service.fields.map(f => (
          <FieldRow
            key={f.key}
            field={f}
            draft={draft[f.key]}
            onChange={onChange}
            disabled={!canEdit(f)}
          />
        ))}
      </div>

      {editable ? (
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <Button variant="ghost" size="sm" onClick={reset} disabled={saving}>
            {ui.reset}
          </Button>
          <Button variant="primary" size="sm" loading={saving} disabled={!dirty}
                  onClick={save}>
            {saving ? ui.saving : ui.save}
          </Button>
        </div>
      ) : (
        <div style={{ fontSize: 11, color: 'var(--dim)' }}>{ui.noneEditable}</div>
      )}
    </Card>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function InstallationPage() {
  const { lang } = useLanguage()
  const user = getUser()
  const copy = SERVICE_CONFIG[lang]
  const ui = copy.ui

  const [tab, setTab] = useState<Tab>('instance')
  const [instance, setInstance] = useState<ServicesReport | null>(null)
  const [tenant, setTenant] = useState<TenantServicesReport | null>(null)
  const [loading, setLoading] = useState(true)
  // Why the instance tab is unavailable, when it is: two different fixes, so
  // two different messages, and neither of them is "something went wrong".
  const [denied, setDenied] = useState<'disabled' | 'not_operator' | null>(null)

  const isAdmin = user?.role === 'admin'

  const load = useCallback(async () => {
    // A non-admin has nothing to fetch: both endpoints answer 403, and an error
    // toast on top of a screen that already explains itself is noise blaming
    // the user for opening a page.
    if (!isAdmin) { setLoading(false); return }
    setLoading(true)
    // Both silent. A deployment with no operator, and a tenant admin who is not
    // one, are expected states this screen renders in words — not failures.
    const [inst, ten] = await Promise.allSettled([
      getServices({ silent: true }),
      getTenantServices({ silent: true }),
    ])

    if (inst.status === 'fulfilled') {
      setInstance(inst.value)
      setDenied(null)
    } else {
      setInstance(null)
      const code = (inst.reason as { code?: string })?.code
      setDenied(code === 'instance_config_disabled' ? 'disabled' : 'not_operator')
    }
    if (ten.status === 'fulfilled') setTenant(ten.value)
    setLoading(false)
  }, [isAdmin])

  useEffect(() => { load() }, [load])

  // Land on the tab the reader can actually use.
  useEffect(() => {
    if (!loading && denied) setTab('tenant')
  }, [loading, denied])

  const tenantServices = tenant?.services ?? []

  const tabs = useMemo(() => ([
    { id: 'instance' as Tab, label: ui.tabInstance, Icon: ServerCog },
    { id: 'tenant' as Tab, label: ui.tabTenant, Icon: PlugZap },
  ]), [ui])

  if (!isAdmin) {
    return (
      <Card padding="24px">
        <div style={{ fontSize: 14, fontWeight: 600 }}>{ui.notOperatorTitle}</div>
        <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 6 }}>
          {ui.notOperatorBody}
        </div>
      </Card>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <div style={{ fontSize: 20, fontWeight: 700 }}>{ui.title}</div>
        <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 2 }}>{ui.lead}</div>
      </div>

      <div style={{ display: 'flex', gap: 4, borderBottom: '1px solid var(--border)' }}>
        {tabs.map(({ id, label, Icon }) => {
          const active = tab === id
          return (
            <button
              key={id}
              onClick={() => setTab(id)}
              style={{
                all: 'unset', cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 7,
                padding: '8px 16px', fontSize: 13, fontWeight: active ? 600 : 400,
                color: active ? 'var(--accent)' : 'var(--muted)',
                borderBottom: `2px solid ${active ? 'var(--accent)' : 'transparent'}`,
                marginBottom: -1,
              }}
            >
              <Icon size={13} aria-hidden="true" />{label}
            </button>
          )
        })}
      </div>

      {/* Why the reader was put on the other tab. Landing them there silently
          answers "what can I do" and leaves "why can't I do the other thing"
          hanging — and an unexplained missing screen reads as a broken one.
          The tab stays clickable: the full explanation is behind it. */}
      {!loading && denied && tab === 'tenant' && (
        <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: -8 }}>
          {denied === 'disabled' ? ui.operatorDisabledBody : ui.notOperatorBody}
        </div>
      )}

      {loading ? (
        <div style={{ textAlign: 'center', padding: 40 }}><Spinner /></div>
      ) : tab === 'instance' ? (
        denied ? (
          <Card padding="24px">
            <div style={{ fontSize: 14, fontWeight: 600 }}>
              {denied === 'disabled' ? ui.operatorDisabledTitle : ui.notOperatorTitle}
            </div>
            <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 6, lineHeight: 1.6 }}>
              {denied === 'disabled' ? ui.operatorDisabledBody : ui.notOperatorBody}
            </div>
          </Card>
        ) : instance && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {!instance.overrides.store_available && (
              <Banner tone="warn" text={ui.storeUnavailable} />
            )}
            {!instance.overrides.encryption_available && (
              <Banner tone="warn" text={ui.encryptionUnavailable} />
            )}
            {instance.overrides.encryption_source === 'generated' && (
              <Banner tone="warn" text={ui.encryptionGenerated} />
            )}
            {/* Operating by being the only company here is real access with an
                expiry date. Saying so while there is still one tenant is the
                difference between naming an operator calmly and discovering on
                the day a customer signs up that nobody can reach the panel. */}
            {instance.operator?.bootstrap && (
              <Banner tone="warn" text={ui.bootstrapNotice} />
            )}
            {instance.undocumented_settings.length > 0 && (
              <Banner
                tone="warn"
                text={`${ui.undocumented}: ${instance.undocumented_settings.join(', ')}`}
              />
            )}
            {instance.services.map(s => (
              <ServiceCard key={s.key} service={s} scope="instance" onChanged={load} />
            ))}
          </div>
        )
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ fontSize: 12, color: 'var(--dim)' }}>{ui.tenantLead}</div>
          {tenantServices.length === 0 ? (
            <Card padding="24px">
              <div style={{ fontSize: 12, color: 'var(--dim)' }}>{ui.tenantEmpty}</div>
            </Card>
          ) : (
            tenantServices.map(s => (
              <ServiceCard key={s.key} service={s} scope="tenant" onChanged={load} />
            ))
          )}
        </div>
      )}
    </div>
  )
}

function Banner({ tone, text }: { tone: 'warn'; text: string }) {
  return (
    <div role="status" style={{
      display: 'flex', gap: 8, alignItems: 'flex-start',
      padding: '12px 16px', borderRadius: 8,
      background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)',
      fontSize: 12, color: 'var(--text)', lineHeight: 1.55,
    }}>
      <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1, color: '#f59e0b' }} aria-hidden="true" />
      <span>{text}</span>
    </div>
  )
}
