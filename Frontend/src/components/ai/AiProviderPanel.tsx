'use client'
/**
 * The company's AI model: StockAI's default, or its own provider account.
 *
 * One rule the whole panel exists to keep honest: the provider is a choice the
 * company makes HERE and sees HERE. When its provider fails the assistant does
 * not quietly answer from StockAI's model — it answers from the data and says
 * which problem the provider had, and this panel shows that same problem with
 * its remedy (the sentences are `errors.llm_<code>`).
 *
 * The key is write-only. The API never returns it; after saving, the field
 * shows the mask the server sends (`••••abcd`) as a placeholder, and typing
 * replaces it. "Probar conexión" tests what is in the form WITHOUT saving it.
 *
 * Admin edits; anyone else who can open it (analysts) reads it.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { CheckCircle2, AlertTriangle, Eye, EyeOff, ExternalLink, PlugZap, RotateCcw, Save, ShieldCheck } from 'lucide-react'
import {
  getAiProviderCatalogue, getAiProviderConfig, getAiProviderStatus,
  saveAiProvider, testAiProvider, resetAiProvider,
  type AiProviderCatalogue, type AiProviderConfig, type AiProviderDraft,
  type AiProviderSpec, type AiProviderStatus, type AiProviderTestResult, type AiProviderUsage,
} from '@/lib/api'
import { getUser, type AuthUser } from '@/lib/auth'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useErrorDetail } from '@/components/ui/States'
import { useCapabilities } from '@/lib/capabilities'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'

type Busy = null | 'save' | 'test' | 'reset'

const box: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12,
  padding: 16, display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0,
}
const label: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: 'var(--muted)' }
const input: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', background: 'var(--surface-2)',
  border: '1px solid var(--border)', borderRadius: 7, padding: '9px 12px',
  fontSize: 14, color: 'var(--text)', minHeight: 40,
}
const hint: React.CSSProperties = { fontSize: 12, color: 'var(--muted)', lineHeight: 1.5, margin: 0 }

export default function AiProviderPanel() {
  const { t, lang } = useLanguage()
  const { addToast } = useToast()
  const confirm = useConfirm()
  const errorDetail = useErrorDetail()
  const { refresh: refreshCaps } = useCapabilities()

  const [user, setUser] = useState<AuthUser | null>(null)
  useEffect(() => { setUser(getUser()) }, [])
  const isAdmin = user?.role === 'admin'

  const [cat, setCat] = useState<AiProviderCatalogue | null>(null)
  const [cfg, setCfg] = useState<AiProviderConfig | null>(null)
  const [status, setStatus] = useState<AiProviderStatus | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)

  // The form. `apiKey` is only ever what the person typed in THIS visit.
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [org, setOrg] = useState('')
  const [project, setProject] = useState('')
  const [temperature, setTemperature] = useState('')
  const [maxTokens, setMaxTokens] = useState('')
  const [priceIn, setPriceIn] = useState('')
  const [priceOut, setPriceOut] = useState('')
  const [enabled, setEnabled] = useState(true)

  const [test, setTest] = useState<AiProviderTestResult | null>(null)
  const [liveModels, setLiveModels] = useState<{ id: string; free: boolean }[] | null>(null)
  const [busy, setBusy] = useState<Busy>(null)
  const [formErr, setFormErr] = useState<string | null>(null)

  const fillForm = useCallback((c: AiProviderConfig) => {
    if (!c.configured) return
    setProvider(c.provider ?? '')
    setModel(c.model ?? '')
    setBaseUrl(c.base_url ?? '')
    setOrg(c.organization ?? '')
    setProject(c.project ?? '')
    setTemperature(c.temperature == null ? '' : String(c.temperature))
    setMaxTokens(c.max_tokens == null ? '' : String(c.max_tokens))
    setPriceIn(c.price_input_per_mtok == null ? '' : String(c.price_input_per_mtok))
    setPriceOut(c.price_output_per_mtok == null ? '' : String(c.price_output_per_mtok))
    setEnabled(c.enabled !== false)
    setApiKey('')
  }, [])

  const load = useCallback(async () => {
    try {
      const [c, conf, st] = await Promise.all([
        getAiProviderCatalogue(), getAiProviderConfig(), getAiProviderStatus(),
      ])
      setCat(c); setCfg(conf); setStatus(st); setLoadErr(null)
      fillForm(conf)
    } catch (e) {
      setLoadErr(errorDetail(e) || t('aiProvider.load_failed'))
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fillForm, t])
  useEffect(() => { void load() }, [load])

  const spec: AiProviderSpec | undefined = useMemo(
    () => cat?.providers.find(p => p.key === provider), [cat, provider])

  const labelOf = (key: string | null | undefined) =>
    key === 'custom' ? t('aiProvider.custom_name')
      : cat?.providers.find(p => p.key === key)?.label ?? key ?? ''

  const when = (iso: string | null | undefined) =>
    iso ? new Date(iso).toLocaleString(lang === 'es' ? 'es' : 'en', { dateStyle: 'short', timeStyle: 'short' })
      : t('aiProvider.never')

  const num = (n: number) => n.toLocaleString(lang === 'es' ? 'es' : 'en')
  const usd = (n: number) => n.toLocaleString(lang === 'es' ? 'es' : 'en',
    { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 4 })

  const pick = (key: string) => {
    if (!isAdmin || key === provider) return
    const next = cat?.providers.find(p => p.key === key)
    setProvider(key)
    setModel(cfg?.configured && cfg.provider === key ? (cfg.model ?? '') : (next?.default_model ?? ''))
    setBaseUrl(cfg?.configured && cfg.provider === key ? (cfg.base_url ?? '') : '')
    setApiKey(''); setTest(null); setLiveModels(null); setFormErr(null)
  }

  const draft = (): AiProviderDraft => ({
    provider, model: model.trim(),
    base_url: spec?.custom_base_url ? baseUrl.trim() : undefined,
    api_key: apiKey.trim() || undefined,
    organization: spec?.supports_org ? org.trim() : undefined,
    project: spec?.supports_org ? project.trim() : undefined,
    temperature: temperature.trim() === '' ? null : Number(temperature),
    max_tokens: maxTokens.trim() === '' ? null : Number(maxTokens),
    price_input_per_mtok: priceIn.trim() === '' ? null : Number(priceIn),
    price_output_per_mtok: priceOut.trim() === '' ? null : Number(priceOut),
    enabled,
  })

  const runTest = async () => {
    setBusy('test'); setFormErr(null)
    try {
      const r = await testAiProvider(draft(), { silent: true })
      setTest(r)
      if (r.models.length) setLiveModels(r.models)
      // A test of the saved settings updates the status (and can resume it).
      void getAiProviderStatus({ silent: true }).then(setStatus).catch(() => {})
    } catch (e) {
      setFormErr(errorDetail(e) || t('aiProvider.action_failed'))
    } finally { setBusy(null) }
  }

  const save = async () => {
    setBusy('save'); setFormErr(null)
    try {
      const r = await saveAiProvider(draft(), false, { silent: true })
      setCfg(r.config); setStatus(r.status); setApiKey(''); setShowKey(false)
      addToast(t('aiProvider.saved'), '', 'success')
      void refreshCaps()
    } catch (e) {
      setFormErr(errorDetail(e) || t('aiProvider.action_failed'))
    } finally { setBusy(null) }
  }

  const reset = async () => {
    if (!(await confirm({
      title: t('aiProvider.reset_confirm_title'), message: t('aiProvider.reset_confirm_body'),
      confirmLabel: t('aiProvider.reset_confirm_cta'), danger: true,
    }))) return
    setBusy('reset')
    try {
      await resetAiProvider({ silent: true })
      setProvider(''); setModel(''); setBaseUrl(''); setApiKey(''); setTest(null); setLiveModels(null)
      setOrg(''); setProject(''); setTemperature(''); setMaxTokens(''); setEnabled(true)
      addToast(t('aiProvider.reset_done'), '', 'success')
      await load()
      void refreshCaps()
    } catch (e) {
      addToast(t('aiProvider.action_failed'), errorDetail(e), 'error')
    } finally { setBusy(null) }
  }

  if (loadErr && !cat) {
    return <div role="alert" style={{ ...box, color: '#C0504D', fontSize: 13 }}>{loadErr}</div>
  }
  if (!cat || !cfg || !status) {
    return <div style={{ padding: 24, display: 'flex', justifyContent: 'center' }}><Spinner size={18} /></div>
  }

  const companyActive = status.source === 'company'
  const failing = companyActive && (status.consecutive_failures ?? 0) > 0
  const stateBadge = !companyActive ? null
    : status.breaker === 'open' ? <Badge variant="danger" dot>{t('aiProvider.state_open')}</Badge>
    : status.breaker === 'half_open' ? <Badge variant="warning" dot>{t('aiProvider.state_half_open')}</Badge>
    : failing ? <Badge variant="warning" dot>{t('aiProvider.state_failing')}</Badge>
    : <Badge variant="success" dot>{t('aiProvider.state_closed')}</Badge>
  const models = liveModels ?? (spec?.suggested_models ?? []).map(id => ({ id, free: id.endsWith(':free') }))
  const savedHere = cfg.configured && cfg.provider === provider
  const privacyName = provider ? labelOf(provider) : ''

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
      <p style={{ ...hint, fontSize: 13 }}>{t('aiProvider.intro')}</p>

      {/* ── What is in use right now ─────────────────────────────────── */}
      <section aria-labelledby="ai-status" style={box} data-testid="ai-provider-status">
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
          <h2 id="ai-status" style={{ margin: 0, fontSize: 14, fontWeight: 700, flex: '1 1 220px', overflowWrap: 'anywhere' }}>
            {companyActive
              ? t('aiProvider.in_use_company', { provider: labelOf(status.provider), model: status.model ?? '' })
              : status.instance_available === false ? t('aiProvider.in_use_stockai_off') : t('aiProvider.in_use_stockai')}
          </h2>
          {stateBadge}
        </div>
        {!companyActive && cfg.configured && !cfg.enabled && <p style={hint}>{t('aiProvider.disabled_note')}</p>}
        {companyActive && (
          <>
            <p style={hint}>{t('aiProvider.last_success', { when: when(status.last_success_at) })}</p>
            {status.last_failure && (failing || status.breaker !== 'closed') && (
              <div role="status" style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13, lineHeight: 1.5 }}>
                <AlertTriangle size={14} color="#B7791F" style={{ flexShrink: 0, marginTop: 3 }} aria-hidden="true" />
                <span>
                  {t('aiProvider.last_failure', { when: when(status.last_failure.at) })}{' '}
                  {t(`errors.llm_${status.last_failure.code}`)}
                  {status.breaker === 'open' && (
                    <> {t('aiProvider.paused_detail', { n: status.consecutive_failures ?? 0, s: status.breaker_cooldown_s ?? 0 })}</>
                  )}
                </span>
              </div>
            )}
          </>
        )}
        {([status.usage_7d, status.usage_30d] as (AiProviderUsage | undefined)[]).map(u => u && (
          <p key={u.days} style={hint} data-testid={`ai-usage-${u.days}d`}>
            {t('aiProvider.usage', {
              days: u.days, calls: num(u.calls), failures: num(u.failures),
              input: num(u.input_tokens), output: num(u.output_tokens),
            })}{' '}
            {u.estimated_cost_usd == null
              ? t('aiProvider.cost_unknown')
              : t('aiProvider.cost_estimate', { cost: usd(u.estimated_cost_usd) })}
          </p>
        ))}
      </section>

      {!isAdmin && user && (
        <p role="note" style={{ ...hint, display: 'flex', gap: 6, alignItems: 'center' }}>
          <ShieldCheck size={14} aria-hidden="true" /> {t('aiProvider.read_only')}
        </p>
      )}

      {/* ── Choose a provider ────────────────────────────────────────── */}
      <section aria-labelledby="ai-choose" style={box}>
        <h2 id="ai-choose" style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{t('aiProvider.choose_title')}</h2>
        <div role="radiogroup" aria-labelledby="ai-choose" style={{
          display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 8,
        }}>
          {cat.providers.map(p => {
            const on = p.key === provider
            return (
              <button
                key={p.key} type="button" role="radio" aria-checked={on}
                data-testid={`ai-provider-${p.key}`}
                disabled={!isAdmin && !on}
                onClick={() => pick(p.key)}
                style={{
                  all: 'unset', boxSizing: 'border-box', cursor: isAdmin ? 'pointer' : 'default',
                  padding: '10px 12px', borderRadius: 9, minHeight: 56,
                  border: `1px solid ${on ? 'var(--accent)' : 'var(--border)'}`,
                  background: on ? 'color-mix(in srgb, var(--accent) 8%, transparent)' : 'var(--surface-2)',
                  display: 'flex', flexDirection: 'column', gap: 4, justifyContent: 'center',
                  opacity: !isAdmin && !on ? 0.55 : 1,
                }}
              >
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', overflowWrap: 'anywhere' }}>
                  {p.key === 'custom' ? t('aiProvider.field_base_url') : p.label}
                </span>
                {p.free && <Badge variant="success" style={{ alignSelf: 'flex-start' }}>{t('aiProvider.free_badge')}</Badge>}
              </button>
            )
          })}
        </div>

        {spec && (
          <form
            onSubmit={e => { e.preventDefault(); if (isAdmin) void save() }}
            style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}
          >
            <p style={hint}>
              {t(`aiProvider.hint.${spec.key}`)}{' '}
              {spec.docs_url && (
                <a href={spec.docs_url} target="_blank" rel="noopener noreferrer"
                   style={{ color: 'var(--accent)', display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                  {t('aiProvider.get_key')} <ExternalLink size={11} aria-hidden="true" />
                </a>
              )}
            </p>
            {spec.free && <p style={hint}>{t('aiProvider.free_note', { provider: spec.label })}</p>}

            {spec.custom_base_url && (
              <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={label}>{t('aiProvider.field_base_url')}</span>
                <input
                  name="ai_base_url" type="url" inputMode="url" autoComplete="off" spellCheck={false}
                  value={baseUrl} onChange={e => setBaseUrl(e.target.value)} disabled={!isAdmin}
                  placeholder="https://api.example.com/v1" style={input}
                />
                <span style={hint}>
                  {t('aiProvider.base_url_hint')}
                  {!cat.allow_private_hosts && <> {t('aiProvider.base_url_private_off')}</>}
                </span>
              </label>
            )}

            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={label}>{t('aiProvider.field_key')}</span>
              <span style={{ display: 'flex', gap: 6 }}>
                <input
                  name="ai_api_key" type={showKey ? 'text' : 'password'} autoComplete="new-password"
                  spellCheck={false} value={apiKey} onChange={e => setApiKey(e.target.value)}
                  disabled={!isAdmin} data-testid="ai-api-key"
                  placeholder={savedHere && cfg.api_key_mask
                    ? t('aiProvider.key_saved_placeholder', { mask: cfg.api_key_mask })
                    : t('aiProvider.key_placeholder')}
                  style={{ ...input, flex: 1, minWidth: 0 }}
                />
                <button
                  type="button" onClick={() => setShowKey(v => !v)} disabled={!isAdmin}
                  aria-label={showKey ? t('aiProvider.hide_key') : t('aiProvider.show_key')}
                  title={showKey ? t('aiProvider.hide_key') : t('aiProvider.show_key')}
                  style={{
                    all: 'unset', cursor: 'pointer', width: 40, height: 40, borderRadius: 7, flexShrink: 0,
                    border: '1px solid var(--border)', background: 'var(--surface-2)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)',
                  }}
                >{showKey ? <EyeOff size={15} /> : <Eye size={15} />}</button>
              </span>
              {!spec.key_required && <span style={hint}>{t('aiProvider.key_optional')}</span>}
            </label>

            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={label}>{t('aiProvider.field_model')}</span>
              <input
                name="ai_model" list="ai-model-options" autoComplete="off" spellCheck={false}
                value={model} onChange={e => setModel(e.target.value)} disabled={!isAdmin}
                data-testid="ai-model" style={input}
              />
              <datalist id="ai-model-options">
                {models.map(m => <option key={m.id} value={m.id}>{m.free ? t('aiProvider.free_badge') : ''}</option>)}
              </datalist>
              <span style={hint}>{liveModels ? t('aiProvider.model_hint_live') : t('aiProvider.model_hint_suggested')}</span>
            </label>

            {spec.supports_org && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 8 }}>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <span style={label}>{t('aiProvider.field_org')}</span>
                  <input name="ai_org" value={org} onChange={e => setOrg(e.target.value)} disabled={!isAdmin}
                         autoComplete="off" spellCheck={false} style={input} />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <span style={label}>{t('aiProvider.field_project')}</span>
                  <input name="ai_project" value={project} onChange={e => setProject(e.target.value)} disabled={!isAdmin}
                         autoComplete="off" spellCheck={false} style={input} />
                </label>
              </div>
            )}

            <details>
              <summary style={{ ...label, cursor: 'pointer', padding: '4px 0' }}>{t('aiProvider.advanced')}</summary>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 8, marginTop: 8 }}>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <span style={label}>{t('aiProvider.field_temperature')}</span>
                  <input name="ai_temperature" type="number" inputMode="decimal" min={0} max={2} step={0.1}
                         value={temperature} onChange={e => setTemperature(e.target.value)} disabled={!isAdmin} style={input} />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <span style={label}>{t('aiProvider.field_max_tokens', { max: cat.max_output_tokens })}</span>
                  <input name="ai_max_tokens" type="number" inputMode="numeric" min={16} max={cat.max_output_tokens}
                         value={maxTokens} onChange={e => setMaxTokens(e.target.value)} disabled={!isAdmin} style={input} />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <span style={label}>{t('aiProvider.field_price_in')}</span>
                  <input name="ai_price_in" type="number" inputMode="decimal" min={0} step="any"
                         value={priceIn} onChange={e => setPriceIn(e.target.value)} disabled={!isAdmin} style={input} />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <span style={label}>{t('aiProvider.field_price_out')}</span>
                  <input name="ai_price_out" type="number" inputMode="decimal" min={0} step="any"
                         value={priceOut} onChange={e => setPriceOut(e.target.value)} disabled={!isAdmin} style={input} />
                </label>
              </div>
              <p style={hint}>{t('aiProvider.price_hint')}</p>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, fontSize: 13 }}>
                <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} disabled={!isAdmin} />
                {t('aiProvider.field_enabled')}
              </label>
            </details>

            {/* The result of "Probar conexión", in the reader's language. */}
            <div role="status" aria-live="polite" data-testid="ai-test-result">
              {test && test.ok && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13, color: '#2E8B62', lineHeight: 1.5 }}>
                  <CheckCircle2 size={15} style={{ flexShrink: 0, marginTop: 2 }} aria-hidden="true" />
                  <span>
                    {t('aiProvider.test_ok', { model: test.model, ms: test.latency_ms ?? 0 })}
                    {test.models_error && <> {t('aiProvider.test_models_unavailable')}</>}
                  </span>
                </div>
              )}
              {test && !test.ok && test.error && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13, color: '#C0504D', lineHeight: 1.5 }}>
                  <AlertTriangle size={15} style={{ flexShrink: 0, marginTop: 2 }} aria-hidden="true" />
                  <span>{t('aiProvider.test_failed')} {t(`errors.llm_${test.error.code}`)}</span>
                </div>
              )}
              {formErr && (
                <div role="alert" style={{ fontSize: 13, color: '#C0504D', lineHeight: 1.5 }}>{formErr}</div>
              )}
            </div>

            {isAdmin && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                <Button type="button" variant="secondary" onClick={() => void runTest()}
                        loading={busy === 'test'} disabled={busy !== null || !model.trim()}
                        icon={<PlugZap size={14} />} data-testid="ai-test">
                  {t('aiProvider.test')}
                </Button>
                <Button type="submit" loading={busy === 'save'} disabled={busy !== null || !model.trim()}
                        icon={<Save size={14} />} data-testid="ai-save">
                  {t('aiProvider.save')}
                </Button>
              </div>
            )}

            <p style={{ ...hint, display: 'flex', gap: 6, alignItems: 'flex-start' }}>
              <ShieldCheck size={13} style={{ flexShrink: 0, marginTop: 2 }} aria-hidden="true" />
              <span>{t('aiProvider.privacy', { provider: privacyName })}</span>
            </p>
          </form>
        )}
      </section>

      {isAdmin && cfg.configured && (
        <div>
          <Button type="button" variant="ghost" onClick={() => void reset()} loading={busy === 'reset'}
                  disabled={busy !== null} icon={<RotateCcw size={14} />} data-testid="ai-reset">
            {t('aiProvider.reset')}
          </Button>
        </div>
      )}
    </div>
  )
}
