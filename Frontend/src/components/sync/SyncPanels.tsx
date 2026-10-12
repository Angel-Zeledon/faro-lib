'use client'
/**
 * The diagnosis and developer pieces of /sincronizacion:
 *  - HealthPanel: the profile's health state, why, what to do, and a
 *    copyable diagnostic;
 *  - DoctorPanel: the staged connection doctor, first failure + remedy;
 *  - RunDetailPanel: one run's outcome, rejected rows, CSV download;
 *  - DevPanel: recipes (grants, egress IPs, ERP queries, the stockai_inventory
 *    view, push forwarders), the transform tester, profile-as-code import /
 *    export, and the in-product reference (modes, brake, conflict, idempotency).
 * All copy is `isync.*`; codes render through isync.code / errors.*, remedies
 * through isync.remedy.<code> with the category fallback.
 */
import { useCallback, useEffect, useState } from 'react'
import {
  applySyncDocument, downloadSyncRejectedCsv, exportSyncProfile, getSyncPresets, getSyncRecipes, getSyncRunDetail,
  renderSyncPreset, runSyncDoctor, testSyncTransform, validateSyncDocument,
  type SyncDoctorResult, type SyncPreset, type SyncProfile, type SyncProfileInput, type SyncRecipes,
  type SyncRunDetail,
} from '@/lib/api'
import Card from '@/components/ui/Card'
import Input, { Field, Select, Textarea } from '@/components/ui/Input'
import { useLanguage } from '@/contexts/LanguageContext'

const C = { border: 'var(--border)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D', amber: '#B7791F', green: '#2F855A' }
const btn: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
  padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, border: `1px solid ${C.border}`,
}
const small: React.CSSProperties = { margin: 0, fontSize: 12, color: C.muted, lineHeight: 1.5 }
const pre: React.CSSProperties = {
  margin: '6px 0 0', padding: 10, borderRadius: 8, background: 'var(--surface, rgba(0,0,0,0.04))',
  fontSize: 11.5, fontFamily: 'ui-monospace, monospace', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere',
  maxHeight: 260, overflow: 'auto', border: `1px solid ${C.border}`,
}
const HEALTH_COLOR: Record<string, string> = { healthy: C.green, degraded: C.amber, paused: C.red, blocked: C.red, new: C.muted }

export function useCodeText() {
  const { t } = useLanguage()
  const text = useCallback((code?: string | null, params?: Record<string, unknown> | null) => {
    if (!code) return ''
    const p = (params || {}) as Record<string, unknown>
    for (const key of [`isync.code.${code}`, `errors.${code}`, `isync.reject.${code}`]) {
      const v = t(key, p)
      if (v !== key) return v
    }
    return code
  }, [t])
  const remedy = useCallback((code?: string | null, category?: string | null) => {
    if (!code) return ''
    const own = t(`isync.remedy.${code}`)
    if (own !== `isync.remedy.${code}`) return own
    if (category) {
      const cat = t(`isync.remedy.category.${category}`)
      if (cat !== `isync.remedy.category.${category}`) return cat
    }
    return ''
  }, [t])
  return { text, remedy }
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const { t } = useLanguage()
  const [done, setDone] = useState(false)
  return (
    <button type="button" style={btn} onClick={async () => {
      try { await navigator.clipboard.writeText(value); setDone(true); setTimeout(() => setDone(false), 1500) } catch { /* no clipboard */ }
    }}>{done ? t('isync.copied') : label}</button>
  )
}

const NETWORK = new Set(['data_source_connect_timeout', 'data_source_host_unreachable', 'data_source_host_rejected'])

export function EgressNote({ ips, code }: { ips: string[]; code?: string | null }) {
  const { t } = useLanguage()
  if (code && !NETWORK.has(code)) return null
  return (
    <p style={{ ...small, marginTop: 6 }}>
      {ips.length ? t('isync.egress_ips', { ips: ips.join(', ') }) : t('isync.egress_ips_unknown')}
    </p>
  )
}

// ── Health ───────────────────────────────────────────────────────────────────

export function HealthPanel({ profile, egress, onDoctor }: { profile: SyncProfile; egress: string[]; onDoctor: () => void }) {
  const { t } = useLanguage()
  const { text, remedy } = useCodeText()
  const health = profile.health || 'new'
  const color = HEALTH_COLOR[health] || C.muted
  const reason = profile.health_reason
  const diag = JSON.stringify({ profile_id: profile.id, slug: profile.slug, health, reason,
    params: profile.health_params, last_error_code: profile.last_error_code,
    last_success_at: profile.last_success_at, consecutive_failures: profile.consecutive_failures }, null, 2)
  return (
    <div style={{ marginTop: 12, padding: 12, borderRadius: 10, border: `1px solid ${color}` }} aria-label={t('isync.health_title')}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <strong style={{ color, fontSize: 13 }}>{t('isync.health_title')}: {t(`isync.health.${health}`)}</strong>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <button type="button" data-tour="sync.doctor" style={btn} onClick={onDoctor}>{t('isync.doctor_run')}</button>
          <CopyButton value={diag} label={t('isync.copy_diagnostic')} />
        </div>
      </div>
      <p style={{ ...small, marginTop: 6 }}>{t(`isync.health_hint.${health}`)}</p>
      {reason && (
        <>
          <p style={{ margin: '6px 0 0', fontSize: 12.5 }}>
            <code style={{ fontSize: 11.5 }}>{reason}</code> · {text(reason, profile.health_params)}
          </p>
          {remedy(reason) && <p style={{ margin: '4px 0 0', fontSize: 12.5 }}><strong>{t('isync.what_to_do')}</strong> {remedy(reason)}</p>}
          <EgressNote ips={egress} code={reason} />
        </>
      )}
    </div>
  )
}

// ── Doctor ───────────────────────────────────────────────────────────────────

export function DoctorPanel({ profileId, onClose }: { profileId: string; onClose: () => void }) {
  const { t } = useLanguage()
  const { text, remedy } = useCodeText()
  const [res, setRes] = useState<SyncDoctorResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    runSyncDoctor(profileId).then(r => { if (alive) setRes(r) }).catch(e => { if (alive) setErr(e instanceof Error ? e.message : String(e)) })
    return () => { alive = false }
  }, [profileId])
  const icon = (s: string) => s === 'ok' ? '✓' : s === 'warning' ? '!' : s === 'failed' ? '✗' : '–'
  const col = (s: string) => s === 'ok' ? C.green : s === 'warning' ? C.amber : s === 'failed' ? C.red : C.dim
  return (
    <Card padding={16}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{t('isync.doctor_title')}</h2>
        <button type="button" style={btn} onClick={onClose}>{t('isync.close')}</button>
      </div>
      {!res && !err && <p style={{ ...small, marginTop: 8 }}>{t('isync.doctor_running')}</p>}
      {err && <p role="alert" style={{ ...small, color: C.red, marginTop: 8 }}>{err}</p>}
      {res && (
        <>
          <p role="status" style={{ margin: '8px 0', fontSize: 13, fontWeight: 700, color: res.ok ? C.green : C.red }}>
            {res.ok ? t('isync.doctor_ok', { ms: res.elapsed_ms }) : t('isync.doctor_failed', { stage: t(`isync.stage.${res.first_failure?.stage}`) })}
          </p>
          <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {res.stages.map((s, i) => (
              <li key={i} style={{ fontSize: 12.5 }}>
                <span style={{ color: col(s.status), fontWeight: 700, display: 'inline-block', width: 16 }}>{icon(s.status)}</span>
                {t(`isync.stage.${s.stage}`)}
                {s.duration_ms != null && <span style={{ color: C.dim }}> · {s.duration_ms} ms</span>}
                {s.code && <div style={{ marginLeft: 16, color: C.muted }}>{text(s.code, s.params)}</div>}
                {s.code && s.status !== 'ok' && remedy(s.code, s.status === 'failed' ? s.category : null) && (
                  <div style={{ marginLeft: 16 }}>
                    <strong>{t('isync.what_to_do')}</strong> {remedy(s.code, s.status === 'failed' ? s.category : null)}
                  </div>
                )}
                {s.status === 'failed' && <div style={{ marginLeft: 16 }}><EgressNote ips={res.egress_ips} code={s.code} /></div>}
                {typeof s.detail?.suggestion === 'string' && (
                  <div style={{ marginLeft: 16 }}><pre style={pre}>{String(s.detail.suggestion)}</pre></div>
                )}
                {s.stage === 'capture' && s.detail && (
                  <div style={{ marginLeft: 16, color: C.muted }}>
                    {t('isync.capture_recommended', { strategy: t(`isync.strategy.${String(s.detail.recommended)}`) })}
                  </div>
                )}
              </li>
            ))}
          </ol>
          <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <CopyButton value={JSON.stringify(res.diagnostic, null, 2)} label={t('isync.copy_diagnostic')} />
            <CopyButton value={JSON.stringify(res, null, 2)} label={t('isync.copy_json')} />
          </div>
          <p style={{ ...small, marginTop: 6 }}>{t('isync.diagnostic_no_secrets')}</p>
        </>
      )}
    </Card>
  )
}

// ── Run detail ───────────────────────────────────────────────────────────────

export function RunDetailPanel({ profileId, runId, onClose }: { profileId: string; runId: string; onClose: () => void }) {
  const { t } = useLanguage()
  const { text, remedy } = useCodeText()
  const [run, setRun] = useState<SyncRunDetail | null>(null)
  useEffect(() => { getSyncRunDetail(profileId, runId).then(setRun).catch(() => {}) }, [profileId, runId])
  if (!run) return <Card padding={16}><p style={small}>{t('common.loading')}</p></Card>
  return (
    <Card padding={16}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{t('isync.run_detail_title')}</h2>
        <button type="button" style={btn} onClick={onClose}>{t('isync.close')}</button>
      </div>
      <p style={{ ...small, marginTop: 6 }}>
        {t(`isync.run_status.${run.status}`)} · {t(`isync.run_kind.${run.run_kind}`)} · {t('isync.col.read')}: {run.rows_read}
        {' · '}{t('isync.col.created')}: {run.created} · {t('isync.col.updated')}: {run.updated}
        {' · '}{t('isync.col.unchanged')}: {run.unchanged} · {t('isync.col.rejected')}: {run.rejected}
      </p>
      {run.outcome && (
        <div role="alert" style={{ marginTop: 8, fontSize: 12.5 }}>
          <code style={{ fontSize: 11.5 }}>{run.outcome.code}</code> · {t(`isync.stage.${run.outcome.stage}`)} · {t(`isync.category.${run.outcome.category}`)}
          <div>{text(run.error_code, run.error_params)}</div>
          {remedy(run.outcome.code, run.outcome.category) && <div><strong>{t('isync.what_to_do')}</strong> {remedy(run.outcome.code, run.outcome.category)}</div>}
          <div style={{ color: C.muted }}>{run.outcome.retries ? t('isync.retries_yes') : t('isync.retries_no')}</div>
        </div>
      )}
      {run.warnings?.codes?.length ? (
        <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12.5, color: C.amber }}>
          {run.warnings.codes.map(c => <li key={c}>{text(c, { refused: run.refused_ceiling })}</li>)}
        </ul>
      ) : null}
      {(run.rejected_sample?.length ?? 0) > 0 && (
        <>
          <h3 style={{ margin: '12px 0 6px', fontSize: 12.5, fontWeight: 700 }}>{t('isync.rejected_title')}</h3>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: C.muted }}>
            {run.rejected_sample!.slice(0, 50).map((r, i) => (
              <li key={i}>{r.row != null ? t('isync.row_n', { n: r.row }) + ' · ' : ''}{r.sku ?? '—'} · {t(`isync.reject.${r.code}`)}</li>
            ))}
          </ul>
          <button type="button" style={{ ...btn, marginTop: 8 }} onClick={() => void downloadSyncRejectedCsv(profileId, runId)}>
            {t('isync.download_csv')}
          </button>
        </>
      )}
    </Card>
  )
}

// ── Developer panel ──────────────────────────────────────────────────────────

type DevTab = 'recipes' | 'tester' | 'code' | 'reference'

export function DevPanel({ profile, sourceId }: { profile: SyncProfile | null; sourceId?: string }) {
  const { t } = useLanguage()
  const [tab, setTab] = useState<DevTab>('recipes')
  const [open, setOpen] = useState(false)
  const tabs: DevTab[] = ['recipes', 'tester', 'code', 'reference']
  if (!open) {
    return (
      <Card padding={16}>
        <button type="button" data-tour="sync.dev" style={{ ...btn, border: 'none', padding: 0, fontSize: 14, fontWeight: 700 }}
                aria-expanded={false} onClick={() => setOpen(true)}>{t('isync.dev_title')} ▸</button>
        <p style={{ ...small, marginTop: 4 }}>{t('isync.dev_hint')}</p>
      </Card>
    )
  }
  return (
    <Card padding={16}>
      <button type="button" style={{ ...btn, border: 'none', padding: 0, fontSize: 14, fontWeight: 700, marginBottom: 8 }}
              aria-expanded onClick={() => setOpen(false)}>{t('isync.dev_title')} ▾</button>
      <div role="tablist" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
        {tabs.map(k => (
          <button key={k} role="tab" aria-selected={tab === k} type="button" onClick={() => setTab(k)}
                  style={{ ...btn, background: tab === k ? 'var(--surface, rgba(0,0,0,0.05))' : 'transparent' }}>
            {t(`isync.dev_tab.${k}`)}
          </button>
        ))}
      </div>
      {tab === 'recipes' && <Recipes sourceId={sourceId ?? profile?.data_source_id ?? undefined} />}
      {tab === 'tester' && <Tester />}
      {tab === 'code' && <AsCode profile={profile} />}
      {tab === 'reference' && <Reference />}
    </Card>
  )
}

function Recipes({ sourceId }: { sourceId?: string }) {
  const { t } = useLanguage()
  const [r, setR] = useState<SyncRecipes | null>(null)
  useEffect(() => { getSyncRecipes(sourceId).then(setR).catch(() => {}) }, [sourceId])
  if (!r) return <p style={small}>{t('common.loading')}</p>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <p style={small}>{t('isync.recipes_intro')}</p>
      <EgressNote ips={r.egress_ips} />
      {Object.entries(r.grants).map(([engine, lines]) => (
        <div key={engine}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
            <strong style={{ fontSize: 12.5 }}>{t('isync.recipe_grants', { engine: t(`data.conn.engine_${engine}`) })}</strong>
            <CopyButton value={lines.join('\n')} label={t('isync.copy')} />
          </div>
          <pre style={pre}>{lines.join('\n')}</pre>
        </div>
      ))}
      <div>
        <strong style={{ fontSize: 12.5 }}>{t('isync.recipe_view')}</strong>
        <p style={small}>{t('isync.recipe_view_hint')}</p>
        {Object.entries(r.view_template).map(([engine, v]) => (
          <div key={engine}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginTop: 6 }}>
              <span style={{ fontSize: 12 }}>{t(`data.conn.engine_${engine}`)}</span>
              <CopyButton value={typeof v === 'string' ? v : JSON.stringify(v, null, 2)} label={t('isync.copy')} />
            </div>
            <pre style={pre}>{typeof v === 'string' ? v : JSON.stringify(v, null, 2)}</pre>
          </div>
        ))}
      </div>
      <div>
        <strong style={{ fontSize: 12.5 }}>{t('isync.recipe_examples')}</strong>
        {Object.entries(r.examples).map(([k, ex]) => (
          <div key={k} style={{ marginTop: 6 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 12 }}>{ex.title}</span>
              <CopyButton value={ex.sql} label={t('isync.copy')} />
            </div>
            <pre style={pre}>{ex.sql}</pre>
          </div>
        ))}
      </div>
      <div>
        <strong style={{ fontSize: 12.5 }}>{t('isync.recipe_forwarders')}</strong>
        <p style={small}>{t('isync.recipe_forwarders_hint')}</p>
        {Object.entries(r.forwarders).map(([k, code]) => (
          <div key={k} style={{ marginTop: 6 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 12 }}>{t(`isync.forwarder.${k}`)}</span>
              <CopyButton value={code} label={t('isync.copy')} />
            </div>
            <pre style={pre}>{code}</pre>
          </div>
        ))}
      </div>
    </div>
  )
}

function Tester() {
  const { t } = useLanguage()
  const { text } = useCodeText()
  const [field, setField] = useState('current_stock')
  const [value, setValue] = useState('1.250')
  const [multiplier, setMultiplier] = useState('')
  const [decimal, setDecimal] = useState<'auto' | 'dot' | 'comma'>('auto')
  const [caseOpt, setCaseOpt] = useState<'none' | 'upper' | 'lower'>('none')
  const [valueMap, setValueMap] = useState('')
  const [out, setOut] = useState<string | null>(null)
  async function run() {
    const vm: Record<string, string> = {}
    for (const line of valueMap.split('\n')) { const i = line.indexOf('='); if (i > 0) vm[line.slice(0, i).trim()] = line.slice(i + 1).trim() }
    try {
      const r = await testSyncTransform({ field, value, spec: {
        decimal, case: caseOpt, multiplier: multiplier ? Number(multiplier) : null,
        value_map: Object.keys(vm).length ? vm : null } })
      setOut(r.ok ? t('isync.tester_ok', { value: String(r.output) }) : t('isync.tester_rejected', { reason: text(r.code, r.params) || t(`isync.reject.${r.code}`) }))
    } catch (e) { setOut(e instanceof Error ? e.message : String(e)) }
  }
  const fields = ['sku', 'warehouse', 'current_stock', 'min_stock', 'unit_cost', 'lead_time_days', 'supplier', 'category', 'sale_price']
  return (
    <div>
      <p style={{ ...small, marginBottom: 8 }}>{t('isync.tester_intro')}</p>
      <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))' }}>
        <Field label={t('isync.tester_field')}>
          <Select value={field} onChange={e => setField(e.target.value)}>
            {fields.map(f => <option key={f} value={f}>{t(`isync.field.${f}`)}</option>)}
          </Select>
        </Field>
        <Field label={t('isync.tester_value')}><Input value={value} onChange={e => setValue(e.target.value)} /></Field>
        <Field label={t('isync.multiplier')}><Input type="number" value={multiplier} onChange={e => setMultiplier(e.target.value)} /></Field>
        <Field label={t('isync.decimal')}>
          <Select value={decimal} onChange={e => setDecimal(e.target.value as 'auto')}>
            {(['auto', 'dot', 'comma'] as const).map(x => <option key={x} value={x}>{t(`isync.decimal_opt.${x}`)}</option>)}
          </Select>
        </Field>
        <Field label={t('isync.case')}>
          <Select value={caseOpt} onChange={e => setCaseOpt(e.target.value as 'none')}>
            {(['none', 'upper', 'lower'] as const).map(x => <option key={x} value={x}>{t(`isync.case_opt.${x}`)}</option>)}
          </Select>
        </Field>
        <Field label={t('isync.value_map')}><Textarea rows={2} value={valueMap} placeholder="01=Norte" onChange={e => setValueMap(e.target.value)} /></Field>
      </div>
      <button type="button" style={{ ...btn, marginTop: 8 }} onClick={() => void run()}>{t('isync.tester_run')}</button>
      {out && <p role="status" style={{ margin: '8px 0 0', fontSize: 13 }}>{out}</p>}
    </div>
  )
}

function AsCode({ profile }: { profile: SyncProfile | null }) {
  const { t } = useLanguage()
  const [doc, setDoc] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [errors, setErrors] = useState<{ path: string; message: string }[]>([])
  useEffect(() => {
    if (profile) exportSyncProfile(profile.id).then(d => setDoc(JSON.stringify(d, null, 2))).catch(() => {})
  }, [profile])
  async function act(kind: 'validate' | 'dry' | 'apply') {
    setMsg(null); setErrors([])
    let parsed: unknown
    try { parsed = JSON.parse(doc) } catch { setErrors([{ path: '$', message: t('isync.code_not_json') }]); return }
    try {
      if (kind === 'validate') {
        const r = await validateSyncDocument(parsed)
        setErrors(r.errors); setMsg(r.valid ? t('isync.code_valid') : null)
      } else {
        const r = await applySyncDocument(parsed, kind === 'dry')
        setMsg(kind === 'dry' ? t('isync.code_dry_ok', { action: t(`isync.code_action.${r.action}`) })
                              : t('isync.code_applied', { action: t(`isync.code_action.${r.action}`) }))
      }
    } catch (e: unknown) {
      const params = (e as { params?: { errors?: { path: string; message: string }[] } })?.params
      if (params?.errors?.length) setErrors(params.errors)
      else setMsg(e instanceof Error ? e.message : String(e))
    }
  }
  return (
    <div>
      <p style={{ ...small, marginBottom: 8 }}>{t('isync.code_intro')}</p>
      <Textarea rows={14} value={doc} onChange={e => setDoc(e.target.value)} style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11.5 }}
                aria-label={t('isync.code_document')} />
      <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
        <button type="button" style={btn} onClick={() => void act('validate')}>{t('isync.code_validate')}</button>
        <button type="button" style={btn} onClick={() => void act('dry')}>{t('isync.code_dry')}</button>
        <button type="button" style={btn} onClick={() => void act('apply')}>{t('isync.code_apply')}</button>
        <CopyButton value={doc} label={t('isync.copy')} />
        <a href="/api/inventory-sync/schema/profile.json" target="_blank" rel="noreferrer" style={{ ...btn, textDecoration: 'none', color: 'inherit' }}>
          {t('isync.code_schema')}
        </a>
      </div>
      {msg && <p role="status" style={{ margin: '8px 0 0', fontSize: 12.5 }}>{msg}</p>}
      {errors.length > 0 && (
        <ul role="alert" style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12, color: C.red }}>
          {errors.map((e, i) => <li key={i}><code>{e.path}</code>: {e.message.replace(`${e.path}: `, '')}</li>)}
        </ul>
      )}
    </div>
  )
}

function Reference() {
  const { t } = useLanguage()
  const sections = ['modes', 'freshness', 'models', 'ledger', 'brake', 'conflict', 'idempotency', 'transforms', 'push', 'limits']
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {sections.map(s => (
        <details key={s}>
          <summary style={{ cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>{t(`isync.ref.${s}.title`)}</summary>
          <p style={{ ...small, marginTop: 6, whiteSpace: 'pre-line' }}>{t(`isync.ref.${s}.body`)}</p>
        </details>
      ))}
    </div>
  )
}

// ── ERP recipe presets (wizard step 2) ───────────────────────────────────────

export function PresetPicker({ engine, onApply }: {
  engine: string
  onApply: (body: SyncProfileInput & { strategy?: string }) => void
}) {
  const { t } = useLanguage()
  const { text } = useCodeText()
  const [list, setList] = useState<SyncPreset[] | null>(null)
  const [others, setOthers] = useState<Record<string, { vendor: string; path: string }>>({})
  const [pick, setPick] = useState('')
  const [params, setParams] = useState<Record<string, string>>({})
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    getSyncPresets().then(r => { setList(r.presets); setOthers(r.not_direct) }).catch(() => setList([]))
  }, [])
  const usable = (list ?? []).filter(p => p.engines.includes(engine))
  const chosen = usable.find(p => p.id === pick)
  useEffect(() => {
    if (!chosen) return
    const next: Record<string, string> = {}
    for (const p of chosen.params) next[p.name] = p.default ?? ''
    setParams(next); setErr(null)
  }, [chosen])
  async function apply() {
    if (!chosen) return
    setErr(null)
    try {
      const clean: Record<string, string> = {}
      for (const [k, v] of Object.entries(params)) if (v.trim()) clean[k] = v.trim()
      const r = await renderSyncPreset(chosen.id, engine, clean)
      onApply(r.profile)
    } catch (e: unknown) {
      const ae = e as { code?: string; params?: Record<string, unknown> }
      setErr(ae?.code ? text(ae.code, ae.params) : (e instanceof Error ? e.message : String(e)))
    }
  }
  if (list === null) return null
  return (
    <details style={{ marginBottom: 12 }}>
      <summary data-tour="sync.preset" style={{ cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>{t('isync.preset_title')}</summary>
      <p style={{ ...small, marginTop: 6 }}>{t('isync.preset_hint')}</p>
      {usable.length === 0 ? <p style={{ ...small, marginTop: 6 }}>{t('isync.preset_none_for_engine')}</p> : (
        <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', marginTop: 8 }}>
          <Field label={t('isync.preset_pick')}>
            <Select value={pick} onChange={e => setPick(e.target.value)}>
              <option value="">{t('isync.choose')}</option>
              {usable.map(p => <option key={p.id} value={p.id}>{p.product}</option>)}
            </Select>
          </Field>
          {chosen?.params.map(p => (
            <Field key={p.name} label={`${t(`isync.preset_param.${p.name}`)}${p.required ? ' *' : ''}`}>
              <Input value={params[p.name] ?? ''} onChange={e => setParams(s => ({ ...s, [p.name]: e.target.value }))} />
            </Field>
          ))}
        </div>
      )}
      {chosen && (
        <>
          <p style={{ margin: '8px 0 0', fontSize: 12, color: chosen.verified ? C.green : C.amber }}>
            {chosen.verified ? t('isync.preset_verified') : t('isync.preset_unverified', { vendor: chosen.vendor })}
          </p>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12, color: C.muted }}>
            {chosen.gotchas.map(g => <li key={g}>{t(`isync.gotcha.${g}`)}</li>)}
          </ul>
          <button type="button" style={{ ...btn, marginTop: 8 }} onClick={() => void apply()}>{t('isync.preset_apply')}</button>
        </>
      )}
      {err && <p role="alert" style={{ ...small, color: C.red, marginTop: 6 }}>{err}</p>}
      {Object.keys(others).length > 0 && (
        <>
          <p style={{ margin: '10px 0 4px', fontSize: 12, fontWeight: 600 }}>{t('isync.preset_other_systems')}</p>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: C.muted }}>
            {Object.keys(others).map(k => <li key={k}>{t(`isync.not_direct.${k}`)}</li>)}
          </ul>
        </>
      )}
    </details>
  )
}
