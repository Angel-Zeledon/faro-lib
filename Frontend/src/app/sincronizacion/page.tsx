'use client'
/**
 * /sincronizacion — keep inventory in step with the customer's own database.
 *
 * A profile says where the stock lives (a table, a view, a read-only query, a
 * MongoDB collection or pipeline) and which column is what. The flow on this
 * screen is the order a person needs: pick the database -> pick what to read ->
 * confirm the proposed mapping (confidence badges, sample values) -> preview it
 * (dry run, nothing written) -> schedule. The status view then says, out loud,
 * when the last success was, whether the safety brake stopped a run, and why a
 * run failed. All copy is `isync.*` in translations.ts.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import {
  createSyncProfile, deleteSyncProfile, disableSyncProfile, discoverSyncSource, dryRunSyncDraft,
  dryRunSyncProfile, enableSyncProfile, getSyncFields, getSyncTableColumns, inspectSyncSource,
  listSyncProfiles, listSyncRuns, listSyncSources, runSyncProfile, updateSyncProfile,
  type SyncColumn, type SyncDatabaseSource, type SyncDryRun, type SyncFieldMapping,
  type SyncFieldsCatalog, type SyncFilter, type SyncProfile, type SyncProfileInput, type SyncRun,
  type SyncSourceKind, type SyncSuggestion, type SyncTable, type SyncMasterSource,
} from '@/lib/api'
import Card from '@/components/ui/Card'
import Input, { Field, Select, Textarea } from '@/components/ui/Input'
import { LoadingState, useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'
import { translateErrorParts } from '@/lib/errorMessage'
import { DevPanel, DoctorPanel, HealthPanel, PresetPicker, RunDetailPanel } from '@/components/sync/SyncPanels'

const C = {
  border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  red: '#C0504D', amber: '#B7791F', green: '#2F855A',
}

const btn: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
  padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600,
  border: `1px solid ${C.border}`, color: C.text,
}
const primary: React.CSSProperties = { ...btn, background: 'var(--accent, #0C3A40)', color: '#fff', border: 'none' }
const h2: React.CSSProperties = { margin: 0, fontSize: 14, fontWeight: 700 }
const small: React.CSSProperties = { margin: 0, fontSize: 12, color: C.muted, lineHeight: 1.5 }
const grid: React.CSSProperties = { display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }

interface MappingRow extends SyncFieldMapping { valueMapText?: string }

interface Draft {
  id: string | null
  name: string
  data_source_id: string
  source_kind: SyncSourceKind
  source_schema: string | null
  source_object: string | null
  source_query: string
  mapping: Record<string, MappingRow>
  filters: SyncFilter[]
  default_warehouse: string
  mode: 'full' | 'incremental'
  watermark_column: string
  overlap_minutes: number
  interval_minutes: number
  missing_policy: 'ignore' | 'mark_inactive' | 'zero'
  negative_policy: 'reject' | 'zero'
  duplicate_policy: 'reject' | 'sum'
  allow_new_warehouses: boolean
  brake_max_vanished_pct: number
  brake_min_row_ratio: number
  brake_max_rejected_pct: number
  brake_max_jump_pct: number
  freshness: 'scheduled' | 'continuous' | 'on_demand'
  poll_seconds: number
  reconcile_minutes: number
  strategy: string
  stock_model: 'snapshot' | 'aggregate' | 'ledger'
  model_config_text: string
  priority: number
  slug: string
  unknown_warehouse: '' | 'create' | 'reject' | 'default'
  wh_master: SyncMasterSource | null
  sup_master: SyncMasterSource | null
}

const LEDGER_TEMPLATE = JSON.stringify({
  id_column: 'movement_id', cursor_column: 'movement_id',
  sign: { mode: 'type_map', type_column: 'movement_type', type_map: { IN: 1, OUT: -1, ADJ: 1 }, unknown: 'reject' },
}, null, 2)

const emptyDraft = (sourceId: string, mongo: boolean, minInterval: number): Draft => ({
  id: null, name: '', data_source_id: sourceId, source_kind: mongo ? 'collection' : 'table',
  source_schema: null, source_object: null, source_query: '', mapping: {}, filters: [],
  default_warehouse: '', mode: 'full', watermark_column: '', overlap_minutes: 10,
  interval_minutes: Math.max(60, minInterval), missing_policy: 'ignore', negative_policy: 'reject',
  duplicate_policy: 'reject', allow_new_warehouses: false, brake_max_vanished_pct: 20,
  brake_min_row_ratio: 0.5, brake_max_rejected_pct: 50, brake_max_jump_pct: 0,
  freshness: 'scheduled', poll_seconds: 60, reconcile_minutes: 360, strategy: 'auto',
  stock_model: 'snapshot', model_config_text: '', priority: 100, slug: '',
  unknown_warehouse: '', wh_master: null, sup_master: null,
})

function draftFrom(p: SyncProfile): Draft {
  const mapping: Record<string, MappingRow> = {}
  for (const [f, m] of Object.entries(p.mapping || {})) {
    mapping[f] = {
      ...m, valueMapText: m.value_map ? Object.entries(m.value_map).map(([k, v]) => `${k}=${v}`).join('\n') : '',
    }
  }
  return {
    id: p.id, name: p.name, data_source_id: p.data_source_id, source_kind: p.source_kind,
    source_schema: p.source_schema, source_object: p.source_object, source_query: p.source_query ?? '',
    mapping, filters: p.filters || [], default_warehouse: p.default_warehouse ?? '', mode: p.mode,
    watermark_column: p.watermark_column ?? '', overlap_minutes: p.overlap_minutes,
    interval_minutes: p.interval_minutes, missing_policy: p.missing_policy,
    negative_policy: p.negative_policy, duplicate_policy: p.duplicate_policy,
    allow_new_warehouses: p.allow_new_warehouses, brake_max_vanished_pct: p.brake_max_vanished_pct,
    brake_min_row_ratio: p.brake_min_row_ratio, brake_max_rejected_pct: p.brake_max_rejected_pct,
    brake_max_jump_pct: p.brake_max_jump_pct ?? 0, freshness: p.freshness ?? 'scheduled',
    poll_seconds: p.poll_seconds ?? 60, reconcile_minutes: p.reconcile_minutes ?? 360,
    strategy: p.strategy ?? 'auto', stock_model: p.stock_model ?? 'snapshot',
    model_config_text: p.model_config && Object.keys(p.model_config).length ? JSON.stringify(p.model_config, null, 2) : '',
    priority: p.priority ?? 100, slug: p.slug ?? '',
    unknown_warehouse: p.unknown_warehouse ?? '',
    wh_master: p.masters?.warehouses ?? null, sup_master: p.masters?.suppliers ?? null,
  }
}

function parseModelConfig(text: string): Record<string, unknown> {
  if (!text.trim()) return {}
  try { const v = JSON.parse(text); return v && typeof v === 'object' ? v : {} } catch { return { __invalid_json__: true } }
}

function parseValueMap(text: string | undefined): Record<string, string> | null {
  const out: Record<string, string> = {}
  for (const line of (text || '').split('\n')) {
    const i = line.indexOf('=')
    if (i <= 0) continue
    out[line.slice(0, i).trim()] = line.slice(i + 1).trim()
  }
  return Object.keys(out).length ? out : null
}

function payload(d: Draft): SyncProfileInput {
  const mapping: Record<string, SyncFieldMapping | null> = {}
  for (const [f, m] of Object.entries(d.mapping)) {
    if (!m.column && !m.default) continue
    mapping[f] = {
      column: m.column || null, cast: m.cast || 'auto', trim: m.trim ?? true, case: m.case || 'none',
      decimal: m.decimal || 'auto', multiplier: m.multiplier ? Number(m.multiplier) : null,
      default: m.default || null, value_map: parseValueMap(m.valueMapText),
    }
  }
  return {
    name: d.name.trim(), data_source_id: d.source_kind === 'push' ? (null as unknown as string) : d.data_source_id,
    source_kind: d.source_kind,
    source_schema: d.source_schema, source_object: d.source_object,
    source_query: d.source_query.trim() || null, mapping,
    filters: d.filters.filter(f => f.column.trim()), default_warehouse: d.default_warehouse.trim() || null,
    mode: d.mode, watermark_column: d.watermark_column.trim() || null,
    overlap_minutes: d.overlap_minutes, interval_minutes: d.interval_minutes,
    missing_policy: d.mode === 'incremental' ? 'ignore' : d.missing_policy,
    negative_policy: d.negative_policy, duplicate_policy: d.duplicate_policy,
    allow_new_warehouses: d.allow_new_warehouses, brake_max_vanished_pct: d.brake_max_vanished_pct,
    brake_min_row_ratio: d.brake_min_row_ratio, brake_max_rejected_pct: d.brake_max_rejected_pct,
    brake_max_jump_pct: d.brake_max_jump_pct, freshness: d.freshness, poll_seconds: d.poll_seconds,
    reconcile_minutes: d.reconcile_minutes, strategy: d.strategy, stock_model: d.stock_model,
    model_config: d.stock_model === 'snapshot' ? {} : parseModelConfig(d.model_config_text),
    priority: d.priority, slug: d.slug.trim() || null,
    unknown_warehouse: d.unknown_warehouse || null,
    masters: {
      ...(d.wh_master?.source_object?.trim() ? { warehouses: d.wh_master } : {}),
      ...(d.sup_master?.source_object?.trim() ? { suppliers: d.sup_master } : {}),
    },
  }
}

const masterKey = (sourceId: string, m: SyncMasterSource) =>
  `${sourceId}|${m.source_schema ?? ''}|${m.source_object ?? ''}`
const MASTER_WH_COLS:(keyof SyncMasterSource)[] = ['code_column', 'name_column', 'active_column', 'kind_column', 'city_column']
const MASTER_SUP_COLS: (keyof SyncMasterSource)[] = ['code_column', 'name_column', 'active_column', 'lead_time_column',
  'payment_terms_column', 'email_column']
const MASTER_LABEL: Record<string, string> = {
  code_column: 'isync.master_code', name_column: 'isync.master_name', active_column: 'isync.master_active',
  kind_column: 'isync.master_kind', city_column: 'isync.master_city', lead_time_column: 'isync.master_lead',
  payment_terms_column: 'isync.master_terms', email_column: 'isync.master_email',
}

export default function InventorySyncPage() {
  const { t, lang } = useLanguage()
  // useErrorDetail returns a new function on every render: kept in a ref so
  // the loaders below are stable (as a dependency it re-ran them forever).
  const errorDetailRaw = useErrorDetail()
  const errorRef = useRef(errorDetailRaw)
  errorRef.current = errorDetailRaw
  const errorDetail = useCallback((e: unknown) => errorRef.current(e), [])
  const confirm = useConfirm()
  const role = getUser()?.role
  const canEdit = role === 'admin' || role === 'analyst'

  const [catalog, setCatalog] = useState<SyncFieldsCatalog | null>(null)
  const [profiles, setProfiles] = useState<SyncProfile[] | null>(null)
  const [sources, setSources] = useState<SyncDatabaseSource[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [runs, setRuns] = useState<SyncRun[]>([])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [tables, setTables] = useState<{ tables: SyncTable[]; candidates: SyncTable[] } | null>(null)
  const [columns, setColumns] = useState<SyncColumn[]>([])
  const [suggestion, setSuggestion] = useState<SyncSuggestion | null>(null)
  const [dry, setDry] = useState<SyncDryRun | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [doctorFor, setDoctorFor] = useState<string | null>(null)
  const [runDetail, setRunDetail] = useState<string | null>(null)
  // The preview and the error render above the form, but the buttons that
  // produce them sit at the bottom of a long form: without scrolling to the
  // outcome, "Preview" looked like it did nothing.
  // Column names of each master table, keyed by masterKey().
  const [masterCols, setMasterCols] = useState<Record<string, string[]>>({})
  const whMaster = draft?.wh_master, supMaster = draft?.sup_master, draftSourceId = draft?.data_source_id
  useEffect(() => {
    if (!draftSourceId) return
    for (const m of [whMaster, supMaster]) {
      if (!m?.source_object || (m.source_kind !== 'table' && m.source_kind !== 'collection')) continue
      const k = masterKey(draftSourceId, m)
      if (masterCols[k]) continue
      getSyncTableColumns(draftSourceId, m.source_schema ?? null, m.source_object)
        .then(r => setMasterCols(prev => ({ ...prev, [k]: r.columns.map(c => c.name) })))
        .catch(() => { /* the free-text field stays; the dry run names a wrong column */ })
    }
  }, [draftSourceId, whMaster, supMaster, masterCols])
  const tablesFor = useRef<string | null>(null)
  const errorBox = useRef<HTMLParagraphElement>(null)
  const dryRef = useRef<HTMLDivElement>(null)
  useEffect(() => { if (error) errorBox.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }, [error])
  useEffect(() => { if (dry) dryRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }, [dry])

  const codeText = useCallback((code: string | null | undefined, params?: Record<string, unknown> | null) => {
    if (!code) return ''
    const key = `isync.code.${code}`
    const own = t(key, params || {})
    if (own !== key) return own
    return translateErrorParts({ status: 400, code, params: params || {}, fieldErrors: [] }, lang) || code
  }, [t, lang])

  const when = useCallback((iso: string | null) =>
    iso ? new Date(iso).toLocaleString(lang === 'en' ? 'en-US' : 'es-CR', { dateStyle: 'medium', timeStyle: 'short' }) : '', [lang])

  const age = useCallback((s: number | null) => {
    if (s == null) return t('isync.never')
    if (s < 90) return t('isync.age_minutes', { n: 1 })
    if (s < 5400) return t('isync.age_minutes', { n: Math.round(s / 60) })
    if (s < 172800) return t('isync.age_hours', { n: Math.round(s / 3600) })
    return t('isync.age_days', { n: Math.round(s / 86400) })
  }, [t])

  const load = useCallback(() => {
    listSyncProfiles().then(setProfiles).catch(e => setError(errorDetail(e)))
  }, [errorDetail])

  useEffect(() => {
    getSyncFields().then(setCatalog).catch(e => setError(errorDetail(e)))
    load()
    if (canEdit) listSyncSources().then(setSources).catch(e => setError(errorDetail(e)))
  }, [load, canEdit, errorDetail])

  // While a run is going, refresh on its own so the outcome shows up.
  const anyRunning = (profiles || []).some(p => p.running) || runs.some(r => r.status === 'running')
  useEffect(() => {
    if (!anyRunning) return
    const id = setInterval(() => {
      load()
      if (selected) listSyncRuns(selected).then(setRuns).catch(() => {})
    }, 3000)
    return () => clearInterval(id)
  }, [anyRunning, load, selected])

  useEffect(() => {
    setDoctorFor(null); setRunDetail(null)
    if (!selected) { setRuns([]); return }
    listSyncRuns(selected).then(setRuns).catch(e => setError(errorDetail(e)))
  }, [selected, errorDetail])

  const current = useMemo(() => profiles?.find(p => p.id === selected) ?? null, [profiles, selected])
  const sourceOf = useCallback((id: string) => sources.find(s => s.id === id), [sources])
  const draftSource = draft ? sourceOf(draft.data_source_id) : undefined
  const isMongo = draftSource?.engine === 'mongodb'
  const isPush = draft?.source_kind === 'push'
  const egress = catalog?.egress_ips ?? []

  async function act(fn: () => Promise<unknown>, ok?: string) {
    // A preview on screen describes the moment it was taken; once something
    // runs or changes it is stale, so it goes (a new preview sets it again).
    setBusy(true); setError(null); setNotice(null); setDry(null)
    try { await fn(); if (ok) setNotice(ok); load(); if (selected) setRuns(await listSyncRuns(selected)) }
    catch (e: unknown) { setError(errorDetail(e)) }
    finally { setBusy(false) }
  }

  // ── Wizard helpers ─────────────────────────────────────────────────────────
  function applySuggestion(cols: SyncColumn[], sug: SyncSuggestion, keepExisting: boolean) {
    setColumns(cols); setSuggestion(sug)
    setDraft(d => {
      if (!d) return d
      const mapping: Record<string, MappingRow> = keepExisting ? { ...d.mapping } : {}
      for (const [field, s] of Object.entries(sug.mapping)) {
        if (!mapping[field]?.column) mapping[field] = { ...(mapping[field] || {}), column: s.column }
      }
      return { ...d, mapping }
    })
  }

  async function loadTables(sourceId: string, refresh = false) {
    // Discovery can take seconds and the person may switch database meanwhile:
    // only the answer for the database now selected may land, or the slower,
    // older one would list another database's tables under this one.
    tablesFor.current = sourceId
    setTables(null)
    try {
      const r = await discoverSyncSource(sourceId, refresh)
      if (tablesFor.current === sourceId) setTables(r)
    } catch (e) { if (tablesFor.current === sourceId) setError(errorDetail(e)) }
  }

  async function pickTable(tb: SyncTable) {
    if (!draft) return
    setDraft(d => d && { ...d, source_schema: tb.schema, source_object: tb.name })
    setBusy(true); setError(null); setDry(null)
    try {
      if (draft.source_kind === 'collection' || draft.source_kind === 'table') {
        const r = await getSyncTableColumns(draft.data_source_id, tb.schema, tb.name)
        applySuggestion(r.columns, r.suggestion, false)
      }
    } catch (e) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  async function inspectQuery() {
    if (!draft) return
    setBusy(true); setError(null); setDry(null)
    try {
      const r = await inspectSyncSource(draft.data_source_id, {
        source_kind: draft.source_kind, source_object: draft.source_object, source_query: draft.source_query || null,
      })
      applySuggestion(r.columns, r.suggestion, true)
    } catch (e) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  function startNew() {
    const first = sources.find(s => s.connection_status === 'connected') ?? sources[0]
    if (!first || !catalog) return
    const d = emptyDraft(first.id, first.engine === 'mongodb', catalog.limits.min_interval_minutes)
    setDraft(d); setSelected(null); setColumns([]); setSuggestion(null); setDry(null)
    void loadTables(first.id)
  }

  async function startEdit(p: SyncProfile) {
    setDraft(draftFrom(p)); setDry(null); setSuggestion(null); setColumns([])
    void loadTables(p.data_source_id)
    try {
      if ((p.source_kind === 'table' || p.source_kind === 'collection') && p.source_object) {
        const r = await getSyncTableColumns(p.data_source_id, p.source_schema, p.source_object)
        setColumns(r.columns); setSuggestion(r.suggestion)
      } else {
        const r = await inspectSyncSource(p.data_source_id, { source_kind: p.source_kind, source_object: p.source_object, source_query: p.source_query })
        setColumns(r.columns); setSuggestion(r.suggestion)
      }
    } catch (e) { setError(errorDetail(e)) }
  }

  async function previewDraft() {
    if (!draft) return
    setBusy(true); setError(null); setDry(null)
    try { setDry(await dryRunSyncDraft(payload(draft))) }
    catch (e) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  async function saveDraft() {
    if (!draft) return
    setBusy(true); setError(null); setNotice(null)
    try {
      let saved: SyncProfile
      if (draft.id) {
        const r = await updateSyncProfile(draft.id, payload(draft))
        saved = r
        if (r.disabled_by_edit) setNotice(t('isync.disabled_by_edit'))
      } else {
        saved = await createSyncProfile(payload(draft))
      }
      // The preview that allows turning it on is of the SAVED configuration.
      setDry(await dryRunSyncProfile(saved.id))
      setDraft(null); setSelected(saved.id); load()
    } catch (e) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  if (!profiles || !catalog) {
    return error ? <p role="alert" style={{ color: C.red, fontSize: 13 }}>{error}</p> : <LoadingState label={t('common.loading')} />
  }

  const statusBadge = (p: SyncProfile) => {
    if (p.running) return <span style={{ color: C.muted }}>{t('isync.status_running')}</span>
    if (p.braked) return <span style={{ color: C.red, fontWeight: 700 }}>{t('isync.status_braked')}</span>
    if (p.health === 'paused' || p.health === 'blocked')
      return <span style={{ color: C.red, fontWeight: 700 }}>{t(`isync.health.${p.health}`)}</span>
    if (p.stale) return <span style={{ color: C.red, fontWeight: 700 }}>{t('isync.status_stale')}</span>
    if (p.last_status === 'failed') return <span style={{ color: C.amber, fontWeight: 700 }}>{t('isync.status_failed')}</span>
    if (!p.enabled) return <span style={{ color: C.dim }}>{t('isync.status_off')}</span>
    return <span style={{ color: C.green, fontWeight: 700 }}>{t('isync.status_ok')}</span>
  }

  const fieldLabel = (f: string) => t(`isync.field.${f}`)
  const required = new Set(catalog.required)

  return (
    <div style={{ width: '100%', maxWidth: 980, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 18 }}>
      <p style={{ margin: 0, fontSize: 13, color: C.muted, lineHeight: 1.5 }}>{t('isync.intro')}</p>
      {error && <p ref={errorBox} role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{error}</p>}
      {notice && <p role="status" style={{ margin: 0, fontSize: 12.5, color: C.green }}>{notice}</p>}

      {/* ── Profile list ─────────────────────────────────────────────────── */}
      {!draft && (
        <Card padding={16}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            <h2 style={h2}>{t('isync.list_title')}</h2>
            {canEdit && (
              <button type="button" data-tour="sync.new" style={{ ...primary, opacity: sources.length && !busy ? 1 : 0.5 }}
                      disabled={!sources.length || busy || profiles.length >= catalog.limits.max_profiles}
                      onClick={startNew}>{t('isync.new')}</button>
            )}
          </div>
          {canEdit && sources.length === 0 && (
            <p style={{ ...small, marginTop: 10 }}>
              {t('isync.no_sources')} <Link href="/archivos" style={{ color: 'inherit', fontWeight: 600 }}>{t('isync.go_sources')}</Link>
            </p>
          )}
          {profiles.length === 0 && <p style={{ ...small, marginTop: 10 }}>{t('isync.empty')}</p>}
          <ul data-tour="sync.list" style={{ listStyle: 'none', margin: '10px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {profiles.map(p => (
              <li key={p.id}>
                <button type="button" onClick={() => { setSelected(p.id); setDry(null) }}
                        style={{ ...btn, width: '100%', boxSizing: 'border-box', justifyContent: 'space-between',
                                 padding: '10px 12px', fontWeight: 500,
                                 background: selected === p.id ? 'var(--surface, rgba(0,0,0,0.03))' : 'transparent' }}>
                  <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
                    <strong style={{ fontSize: 13, overflowWrap: 'anywhere' }}>{p.name}</strong>
                    <span style={{ fontSize: 12, color: C.muted }}>
                      {(p.source_object || t(`isync.kind.${p.source_kind}`))}
                      {' · '}{t('isync.last_success', { when: age(p.last_success_age_s) })}
                    </span>
                  </span>
                  <span style={{ fontSize: 12 }}>{statusBadge(p)}</span>
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* ── Detail / status ──────────────────────────────────────────────── */}
      {!draft && current && (
        <Card padding={16}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ minWidth: 0 }}>
              <h2 style={{ ...h2, overflowWrap: 'anywhere' }}>{current.name}</h2>
              <p style={small}>
                {sourceOf(current.data_source_id)?.name ?? current.data_source_id}
                {' · '}{current.source_object || t(`isync.kind.${current.source_kind}`)}
                {' · '}{t(`isync.mode.${current.mode}`)}
                {' · '}{current.freshness === 'continuous'
                  ? t('isync.every_seconds', { n: current.poll_seconds ?? 60 })
                  : current.freshness === 'on_demand' ? t('isync.freshness.on_demand') : t('isync.every', { n: current.interval_minutes })}
                {' · '}{t(`isync.model.${current.stock_model ?? 'snapshot'}`)}
                {current.strategy && current.strategy !== 'auto' ? ` · ${t(`isync.strategy.${current.strategy}`)}` : ''}
              </p>
              {current.source_kind === 'push' && (
                <p style={{ ...small, marginTop: 4, overflowWrap: 'anywhere' }}>
                  {t('isync.push_url')}: <code>POST /api/inventory-sync/profiles/{current.id}/ingest</code>
                </p>
              )}
            </div>
            <span style={{ fontSize: 12.5 }}>{statusBadge(current)}</span>
          </div>

          <div style={{ ...grid, marginTop: 12 }}>
            <div><div style={small}>{t('isync.last_success_label')}</div>
              <strong style={{ fontSize: 13 }}>{current.last_success_at ? `${age(current.last_success_age_s)} · ${when(current.last_success_at)}` : t('isync.never')}</strong></div>
            <div><div style={small}>{t('isync.next_run_label')}</div>
              <strong style={{ fontSize: 13 }}>{current.enabled && current.next_run_at ? when(current.next_run_at) : t('isync.not_scheduled')}</strong></div>
            <div><div style={small}>{t('isync.owned_label')}</div>
              <strong style={{ fontSize: 13 }}>{current.owned_fields.map(fieldLabel).join(', ') || '—'}</strong></div>
          </div>
          <p style={{ ...small, marginTop: 8 }}>{t('isync.conflict_rule')}</p>

          {current.stale && (
            <p role="alert" style={{ margin: '10px 0 0', fontSize: 12.5, color: C.red }}>
              {t('isync.stale_alert', { n: current.interval_minutes * 2 })}
            </p>
          )}
          {current.braked && (
            <div role="alert" style={{ marginTop: 10, padding: 10, borderRadius: 8, border: `1px solid ${C.red}` }}>
              <p style={{ margin: 0, fontSize: 12.5, color: C.red, fontWeight: 700 }}>{t('isync.braked_title')}</p>
              <p style={{ margin: '4px 0 0', fontSize: 12.5 }}>
                {codeText(current.last_error_code, runs.find(r => r.status === 'braked')?.error_params)}
              </p>
              <p style={{ ...small, marginTop: 4 }}>{t('isync.braked_body')}</p>
              {canEdit && (
                <button type="button" style={{ ...btn, marginTop: 8, borderColor: C.red, color: C.red }} disabled={busy}
                        onClick={async () => {
                          if (await confirm({ title: t('isync.override_title'), message: t('isync.override_body'),
                                              confirmLabel: t('isync.override_confirm'), danger: true }))
                            void act(() => runSyncProfile(current.id, true), t('isync.run_started'))
                        }}>{t('isync.override')}</button>
              )}
            </div>
          )}
          {!current.braked && current.last_status === 'failed' && (
            <p role="alert" style={{ margin: '10px 0 0', fontSize: 12.5, color: C.amber }}>
              {t('isync.failed_alert', { n: current.consecutive_failures })} {codeText(current.last_error_code,
                runs.find(r => r.status === 'failed')?.error_params)}
            </p>
          )}
          {current.needs_dry_run && (
            <p style={{ margin: '10px 0 0', fontSize: 12.5, color: C.amber }}>{t('isync.needs_dry_run')}</p>
          )}
          <div data-tour="sync.health"><HealthPanel profile={current} egress={egress} onDoctor={() => setDoctorFor(current.id)} /></div>

          {canEdit && (
            <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
              <button type="button" style={btn} disabled={busy}
                      onClick={() => act(async () => setDry(await dryRunSyncProfile(current.id)))}>{t('isync.dry_run')}</button>
              {current.enabled
                ? <button type="button" style={btn} disabled={busy} onClick={() => act(() => disableSyncProfile(current.id), t('isync.disabled'))}>{t('isync.disable')}</button>
                : <button type="button" style={{ ...primary, opacity: current.needs_dry_run ? 0.5 : 1 }} disabled={busy || current.needs_dry_run}
                          onClick={() => act(() => enableSyncProfile(current.id), t('isync.enabled'))}>{t('isync.enable')}</button>}
              <button type="button" style={{ ...btn, opacity: current.needs_dry_run || current.running ? 0.5 : 1 }}
                      disabled={busy || current.needs_dry_run || current.running}
                      onClick={() => act(() => runSyncProfile(current.id), t('isync.run_started'))}>{t('isync.run_now')}</button>
              <button type="button" style={btn} disabled={busy} onClick={() => void startEdit(current)}>{t('isync.edit')}</button>
              <button type="button" style={btn} disabled={busy} onClick={async () => {
                if (await confirm({ title: t('isync.delete_title'), message: t('isync.delete_body', { name: current.name }),
                                    confirmLabel: t('isync.delete'), danger: true }))
                  void act(async () => { await deleteSyncProfile(current.id); setSelected(null) })
              }}>{t('isync.delete')}</button>
            </div>
          )}
        </Card>
      )}

      {!draft && current && doctorFor === current.id && <DoctorPanel profileId={current.id} onClose={() => setDoctorFor(null)} />}

      {dry && <div ref={dryRef} style={{ scrollMarginTop: 16 }}><DryRunCard dry={dry} fieldLabel={fieldLabel} /></div>}

      {!draft && current && (
        <Card padding={16}>
          <h2 data-tour="sync.history" style={{ ...h2, marginBottom: 10 }}>{t('isync.history_title')}</h2>
          {runs.length === 0 && <p style={small}>{t('isync.history_empty')}</p>}
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
              {runs.length > 0 && (
                <thead><tr style={{ textAlign: 'left', color: C.muted }}>
                  {['started', 'status', 'read', 'created', 'updated', 'unchanged', 'rejected', 'duration'].map(h =>
                    <th key={h} style={{ padding: '4px 6px', fontWeight: 600 }}>{t(`isync.col.${h}`)}</th>)}
                </tr></thead>
              )}
              <tbody>
                {runs.map(r => (
                  <tr key={r.id} style={{ borderTop: `1px solid ${C.border}`, verticalAlign: 'top' }}>
                    <td style={{ padding: '6px' }}>
                      <button type="button" onClick={() => setRunDetail(r.id)} aria-label={t('isync.run_detail_open')}
                              style={{ all: 'unset', cursor: 'pointer', textDecoration: 'underline', textUnderlineOffset: 2 }}>
                        {when(r.started_at)}
                      </button>
                      <div style={{ color: C.dim }}>{t(`isync.trigger.${r.trigger_kind}`)}</div></td>
                    <td style={{ padding: '6px', color: r.status === 'success' ? C.green : r.status === 'running' ? C.muted : C.red }}>
                      {t(`isync.run_status.${r.status}`)}
                      {r.error_code && <div style={{ color: C.muted, maxWidth: 280 }}>{codeText(r.error_code, r.error_params)}</div>}
                      {r.manual_overwritten > 0 && <div style={{ color: C.amber }}>{t('isync.overwritten_n', { n: r.manual_overwritten })}</div>}
                      {r.missing > 0 && <div style={{ color: C.muted }}>{t('isync.missing_n', { n: r.missing, applied: r.missing_applied })}</div>}
                      {r.brake_overridden && <div style={{ color: C.amber }}>{t('isync.brake_was_overridden')}</div>}
                    </td>
                    <td style={{ padding: '6px' }}>{r.rows_read}</td>
                    <td style={{ padding: '6px' }}>{r.created}</td>
                    <td style={{ padding: '6px' }}>{r.updated}</td>
                    <td style={{ padding: '6px' }}>{r.unchanged}</td>
                    <td style={{ padding: '6px', color: r.rejected ? C.amber : undefined }}>{r.rejected}</td>
                    <td style={{ padding: '6px' }}>{r.duration_ms != null ? `${(r.duration_ms / 1000).toFixed(1)} s` : '…'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {!draft && current && runDetail && (
        <RunDetailPanel key={runDetail} profileId={current.id} runId={runDetail} onClose={() => setRunDetail(null)} />
      )}

      {!draft && canEdit && <DevPanel profile={current} />}

      {!draft && (catalog.documented_limits?.length ?? 0) > 0 && (
        <Card padding={16}>
          <details>
            <summary style={{ cursor: 'pointer', fontSize: 13, fontWeight: 700 }}>{t('isync.limits_title')}</summary>
            <p style={{ ...small, marginTop: 6 }}>{t('isync.limits_intro')}</p>
            <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12.5, display: 'flex', flexDirection: 'column', gap: 6 }}>
              {catalog.documented_limits!.map(k => <li key={k}>{t(`isync.limit.${k}`)}</li>)}
              <li>{t('isync.limit.mongodb_change_stream')}</li>
            </ul>
          </details>
        </Card>
      )}

      {/* ── Wizard ───────────────────────────────────────────────────────── */}
      {draft && (
        <>
          <Card padding={16}>
            <h2 data-tour="sync.source" style={{ ...h2, marginBottom: 10 }}>{t('isync.step1')}</h2>
            <div style={grid}>
              <Field label={t('isync.name')}>
                <Input value={draft.name} maxLength={120} onChange={e => setDraft(d => d && { ...d, name: e.target.value })} />
              </Field>
              <Field label={t('isync.database')}>
                <Select value={draft.data_source_id} disabled={!!draft.id}
                        onChange={e => {
                          const s = sourceOf(e.target.value)
                          setDraft(d => d && { ...emptyDraft(e.target.value, s?.engine === 'mongodb', catalog.limits.min_interval_minutes), name: d.name })
                          setColumns([]); setSuggestion(null); setDry(null)
                          void loadTables(e.target.value)
                        }}>
                  {sources.map(s => <option key={s.id} value={s.id}>{s.name} · {s.engine}{s.connection_status !== 'connected' ? ` (${t('isync.not_connected')})` : ''}</option>)}
                </Select>
              </Field>
            </div>
            {draftSource && draftSource.connection_status !== 'connected' && (
              <p role="alert" style={{ ...small, color: C.red, marginTop: 8 }}>{t('isync.source_not_connected')}</p>
            )}
          </Card>

          <Card padding={16}>
            <h2 data-tour="sync.what" style={{ ...h2, marginBottom: 10 }}>{t('isync.step2')}</h2>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              {(isMongo ? ['collection', 'pipeline', 'push'] : ['table', 'query', 'push']).map(k => (
                <label key={k} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13, cursor: 'pointer' }}>
                  <input type="radio" name="kind" checked={draft.source_kind === k}
                         onChange={() => setDraft(d => d && { ...d, source_kind: k as SyncSourceKind })} />
                  {t(`isync.kind.${k}`)}
                </label>
              ))}
            </div>
            {isPush && <p style={small}>{t('isync.push_hint')}</p>}
            {!isPush && draftSource && (
              <PresetPicker engine={draftSource.engine} onApply={body => {
                setDraft(d => {
                  if (!d) return d
                  const mapping: Record<string, MappingRow> = {}
                  for (const [f, m] of Object.entries(body.mapping || {})) if (m) mapping[f] = { ...m }
                  return { ...d, name: d.name || body.name || '', source_kind: 'query', source_object: null,
                           source_schema: null, source_query: body.source_query || '', mapping,
                           stock_model: (body.stock_model as Draft['stock_model']) || 'snapshot',
                           model_config_text: body.model_config && Object.keys(body.model_config).length
                             ? JSON.stringify(body.model_config, null, 2) : '',
                           strategy: body.strategy || 'auto', mode: 'full',
                           wh_master: body.masters?.warehouses ?? d.wh_master }
                })
                setColumns([]); setSuggestion(null); setDry(null); setNotice(t('isync.preset_applied'))
              }} />
            )}
            {(draft.source_kind === 'table' || draft.source_kind === 'collection' || draft.source_kind === 'pipeline') && (
              <>
                {!tables && <LoadingState label={t('isync.discovering')} />}
                {tables && tables.candidates.length > 0 && (
                  <>
                    <p style={{ ...small, marginBottom: 6 }}>{t('isync.candidates')}</p>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
                      {tables.candidates.map(tb => (
                        <button key={`${tb.schema}.${tb.name}`} type="button" disabled={busy} onClick={() => void pickTable(tb)}
                                style={{ ...btn, borderColor: draft.source_object === tb.name ? 'var(--accent, #0C3A40)' : C.border }}>
                          {tb.schema && !isMongo ? `${tb.schema}.` : ''}{tb.name}
                          <Badge label={tb.label as string} t={t} />
                        </button>
                      ))}
                    </div>
                  </>
                )}
                {tables && (
                  <Field label={t(isMongo ? 'isync.pick_collection' : 'isync.pick_table')}>
                    <Select value={draft.source_object ? `${draft.source_schema ?? ''}|${draft.source_object}` : ''}
                            onChange={e => {
                              const tb = tables.tables.find(x => `${x.schema ?? ''}|${x.name}` === e.target.value)
                              if (tb) void pickTable(tb)
                            }}>
                      <option value="">{t('isync.choose')}</option>
                      {tables.tables.map(tb => (
                        <option key={`${tb.schema}|${tb.name}`} value={`${tb.schema ?? ''}|${tb.name}`}>
                          {tb.schema && !isMongo ? `${tb.schema}.` : ''}{tb.name}{tb.row_estimate != null ? ` (${tb.row_estimate})` : ''}
                        </option>
                      ))}
                    </Select>
                  </Field>
                )}
              </>
            )}
            {(draft.source_kind === 'query' || draft.source_kind === 'pipeline' || draft.source_kind === 'collection') && (
              <div style={{ marginTop: 10 }}>
                <Field label={t(`isync.query_label.${draft.source_kind}`)} hint={t(`isync.query_hint.${draft.source_kind}`)}>
                  <Textarea rows={draft.source_kind === 'query' ? 6 : 4} value={draft.source_query}
                            style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}
                            onChange={e => setDraft(d => d && { ...d, source_query: e.target.value })} />
                </Field>
                <button type="button" style={{ ...btn, marginTop: 8 }} disabled={busy || (draft.source_kind !== 'query' && !draft.source_object)}
                        onClick={() => void inspectQuery()}>{t('isync.inspect')}</button>
              </div>
            )}
          </Card>

          <Card padding={16}>
            <h2 data-tour="sync.mapping" style={{ ...h2, marginBottom: 4 }}>{t('isync.step3')}</h2>
            <p style={{ ...small, marginBottom: 10 }}>{t('isync.mapping_hint')}</p>
            {columns.length === 0 && !isPush ? <p style={small}>{t('isync.mapping_needs_source')}</p> : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {catalog.fields.map(f => {
                  const m = draft.mapping[f] || {}
                  const sug = suggestion?.mapping[f]
                  const col = columns.find(c => c.name === m.column)
                  const numeric = catalog.number_fields.includes(f)
                  const set = (patch: Partial<MappingRow>) =>
                    setDraft(d => d && { ...d, mapping: { ...d.mapping, [f]: { ...(d.mapping[f] || {}), ...patch } } })
                  return (
                    <div key={f} style={{ borderTop: `1px solid ${C.border}`, paddingTop: 8 }}>
                      <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', alignItems: 'end' }}>
                        <div style={{ fontSize: 13, fontWeight: 600 }}>
                          {fieldLabel(f)}{required.has(f) ? ' *' : ''}
                          {sug && sug.column === m.column && <Badge label={sug.label} t={t} />}
                          <div style={{ fontSize: 11.5, color: C.dim, fontWeight: 400 }}>{t(`isync.field_hint.${f}`)}</div>
                        </div>
                        <Field label={t('isync.column')}>
                          {isPush
                            ? <Input value={m.column || ''} aria-label={`${fieldLabel(f)} — ${t('isync.column')}`} placeholder={f}
                                     onChange={e => set({ column: e.target.value || null })} />
                            : <Select value={m.column || ''} aria-label={`${fieldLabel(f)} — ${t('isync.column')}`}
                                      onChange={e => set({ column: e.target.value || null })}>
                                <option value="">{t('isync.not_mapped')}</option>
                                {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
                              </Select>}
                        </Field>
                        <div style={{ fontSize: 12, color: C.muted, overflowWrap: 'anywhere' }}>
                          {col?.samples?.length ? `${t('isync.samples')}: ${col.samples.join(', ')}` : ''}
                        </div>
                      </div>
                      {(m.column || m.default) && (
                        <details style={{ marginTop: 6 }}>
                          <summary style={{ fontSize: 12, color: C.muted, cursor: 'pointer' }}>{t('isync.transforms')}</summary>
                          <div style={{ ...grid, marginTop: 8 }}>
                            {numeric && (
                              <Field label={t('isync.multiplier')} hint={t('isync.multiplier_hint')}>
                                <Input type="number" min={0} value={m.multiplier ?? ''} onChange={e => set({ multiplier: e.target.value ? Number(e.target.value) : null })} />
                              </Field>
                            )}
                            {numeric && (
                              <Field label={t('isync.decimal')}>
                                <Select value={m.decimal || 'auto'} onChange={e => set({ decimal: e.target.value as SyncFieldMapping['decimal'] })}>
                                  {catalog.decimals.map(x => <option key={x} value={x}>{t(`isync.decimal_opt.${x}`)}</option>)}
                                </Select>
                              </Field>
                            )}
                            {!numeric && (
                              <Field label={t('isync.case')}>
                                <Select value={m.case || 'none'} onChange={e => set({ case: e.target.value as SyncFieldMapping['case'] })}>
                                  {catalog.cases.map(x => <option key={x} value={x}>{t(`isync.case_opt.${x}`)}</option>)}
                                </Select>
                              </Field>
                            )}
                            <Field label={t('isync.default')} hint={t('isync.default_hint')}>
                              <Input value={m.default ?? ''} onChange={e => set({ default: e.target.value || null })} />
                            </Field>
                            {!numeric && (
                              <Field label={t('isync.value_map')} hint={t('isync.value_map_hint')}>
                                <Textarea rows={3} value={m.valueMapText ?? ''} placeholder="01=Norte"
                                          onChange={e => set({ valueMapText: e.target.value })} />
                              </Field>
                            )}
                          </div>
                        </details>
                      )}
                    </div>
                  )
                })}

                <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: 8 }}>
                  <p style={{ fontSize: 13, fontWeight: 600, margin: '0 0 4px' }}>{t('isync.filters')}</p>
                  <p style={{ ...small, marginBottom: 6 }}>{t('isync.filters_hint')}</p>
                  {draft.filters.map((fl, i) => (
                    <div key={i} style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
                      <Select value={fl.column} aria-label={t('isync.column')} onChange={e => setDraft(d => d && { ...d, filters: d.filters.map((x, j) => j === i ? { ...x, column: e.target.value } : x) })}>
                        <option value="">{t('isync.choose')}</option>
                        {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
                      </Select>
                      <Select value={fl.op} aria-label={t('isync.operator')} onChange={e => setDraft(d => d && { ...d, filters: d.filters.map((x, j) => j === i ? { ...x, op: e.target.value } : x) })}>
                        {catalog.filter_ops.map(o => <option key={o} value={o}>{t(`isync.op.${o}`)}</option>)}
                      </Select>
                      {!['is_null', 'not_null'].includes(fl.op) && (
                        <Input value={Array.isArray(fl.value) ? fl.value.join(',') : (fl.value ?? '')} aria-label={t('isync.value')}
                               onChange={e => setDraft(d => d && { ...d, filters: d.filters.map((x, j) => j === i
                                 ? { ...x, value: ['in', 'not_in'].includes(x.op) ? e.target.value.split(',').map(s => s.trim()) : e.target.value } : x) })} />
                      )}
                      <button type="button" style={btn} onClick={() => setDraft(d => d && { ...d, filters: d.filters.filter((_, j) => j !== i) })}>{t('isync.remove')}</button>
                    </div>
                  ))}
                  <button type="button" style={btn} onClick={() => setDraft(d => d && { ...d, filters: [...d.filters, { column: '', op: 'eq', value: '' }] })}>{t('isync.add_filter')}</button>
                </div>
              </div>
            )}
          </Card>

          {!isPush && (
            <Card padding={16}>
              <h2 data-tour="sync.masters" style={{ ...h2, marginBottom: 4 }}>{t('isync.masters_title')}</h2>
              <p style={{ ...small, marginBottom: 10 }}>{t('isync.masters_hint')}</p>
              <Field label={t('isync.unknown_warehouse_label')} hint={t('isync.unknown_warehouse_hint')}>
                <Select value={draft.unknown_warehouse}
                        onChange={e => setDraft(d => d && { ...d, unknown_warehouse: e.target.value as Draft['unknown_warehouse'] })}>
                  {(['', 'create', 'reject', 'default'] as const).map(v =>
                    <option key={v} value={v}>{t(`isync.unknown_warehouse.${v || 'auto'}`)}</option>)}
                </Select>
              </Field>
              {([['wh_master', 'isync.master_wh_title', MASTER_WH_COLS], ['sup_master', 'isync.master_sup_title', MASTER_SUP_COLS]] as const).map(([key, title, cols]) => {
                const m = draft[key]
                const set = (patch: Partial<SyncMasterSource>) => setDraft(d => d && {
                  ...d, [key]: { source_kind: 'table', code_column: '', name_column: '', ...(d[key] || {}), ...patch } })
                return (
                  <details key={key} open={!!m?.source_object} style={{ marginTop: 10 }}>
                    <summary style={{ cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>{t(title)}</summary>
                    <div style={{ ...grid, marginTop: 8 }}>
                      <Field label={t('isync.master_object')}>
                        <Select value={m?.source_object ? `${m.source_schema ?? ''}|${m.source_object}` : ''}
                                onChange={e => {
                                  const tb = tables?.tables.find(x => `${x.schema ?? ''}|${x.name}` === e.target.value)
                                  if (!tb) { setDraft(d => d && { ...d, [key]: null }); return }
                                  set({ source_kind: isMongo ? 'collection' : 'table', source_schema: tb.schema, source_object: tb.name })
                                }}>
                          <option value="">{t('isync.choose')}</option>
                          {(tables?.tables ?? []).map(tb => (
                            <option key={`${tb.schema}|${tb.name}`} value={`${tb.schema ?? ''}|${tb.name}`}>
                              {tb.schema && !isMongo ? `${tb.schema}.` : ''}{tb.name}
                            </option>
                          ))}
                        </Select>
                      </Field>
                      {m?.source_object && cols.map(c => {
                        const known = masterCols[masterKey(draft.data_source_id, m)]
                        const value = String(m[c] ?? '')
                        return (
                          <Field key={c} label={t(MASTER_LABEL[c])}>
                            {/* The master's own columns, read from the source: typed by
                                hand, a typo silently emptied the whole master. Free
                                text stays only while the column list is unavailable. */}
                            {known ? (
                              <Select value={value} onChange={e => set({ [c]: e.target.value || null } as Partial<SyncMasterSource>)}>
                                <option value="">{t('isync.not_mapped')}</option>
                                {value && !known.includes(value) && <option value={value}>{value}</option>}
                                {known.map(col => <option key={col} value={col}>{col}</option>)}
                              </Select>
                            ) : (
                              <Input value={value} onChange={e => set({ [c]: e.target.value || null } as Partial<SyncMasterSource>)} />
                            )}
                          </Field>
                        )
                      })}
                    </div>
                    {key === 'sup_master' && m?.source_object && (
                      <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13, marginTop: 8, cursor: 'pointer' }}>
                        <input type="checkbox" checked={!!m.source_wins} onChange={e => set({ source_wins: e.target.checked })} />
                        <span>{t('isync.master_source_wins')}
                          <span style={{ display: 'block', fontSize: 12, color: C.dim }}>{t('isync.master_source_wins_hint')}</span></span>
                      </label>
                    )}
                  </details>
                )
              })}
            </Card>
          )}

          <Card padding={16}>
            <h2 style={{ ...h2, marginBottom: 10 }}>{t('isync.step4')}</h2>
            <div style={{ ...grid, marginBottom: 12 }}>
              <div data-tour="sync.model"><Field label={t('isync.model_label')} hint={t(`isync.model_hint.${draft.stock_model}`)}>
                <Select value={draft.stock_model} onChange={e => {
                  const v = e.target.value as Draft['stock_model']
                  setDraft(d => d && { ...d, stock_model: v,
                    model_config_text: v === 'ledger' && !d.model_config_text ? LEDGER_TEMPLATE
                      : v === 'aggregate' && !d.model_config_text ? JSON.stringify({ aggregate: 'sum' }, null, 2) : d.model_config_text })
                }}>
                  {(catalog.stock_models ?? ['snapshot']).map(m => <option key={m} value={m}>{t(`isync.model.${m}`)}</option>)}
                </Select>
              </Field></div>
              <div data-tour="sync.freshness"><Field label={t('isync.freshness_label')} hint={t(`isync.freshness_hint.${draft.freshness}`)}>
                <Select value={draft.freshness} onChange={e => setDraft(d => d && { ...d, freshness: e.target.value as Draft['freshness'] })}>
                  {(catalog.freshness_modes ?? ['scheduled']).map(m => <option key={m} value={m}>{t(`isync.freshness.${m}`)}</option>)}
                </Select>
              </Field></div>
              {draft.freshness === 'continuous' && (
                <Field label={t('isync.poll_seconds')} hint={t('isync.poll_seconds_hint', { n: catalog.limits.min_poll_seconds ?? 15 })}>
                  <Input type="number" min={catalog.limits.min_poll_seconds ?? 15} value={draft.poll_seconds}
                         onChange={e => setDraft(d => d && { ...d, poll_seconds: Number(e.target.value) })} />
                </Field>
              )}
              {draft.freshness === 'continuous' && (
                <Field label={t('isync.reconcile_minutes')} hint={t('isync.reconcile_minutes_hint')}>
                  <Input type="number" min={5} value={draft.reconcile_minutes}
                         onChange={e => setDraft(d => d && { ...d, reconcile_minutes: Number(e.target.value) })} />
                </Field>
              )}
              {!isPush && (
                <Field label={t('isync.strategy_label')} hint={t(`isync.strategy_hint.${draft.strategy}`)}>
                  <Select value={draft.strategy} onChange={e => setDraft(d => d && { ...d, strategy: e.target.value })}>
                    {(catalog.strategies ?? ['auto']).map(s => <option key={s} value={s}>{t(`isync.strategy.${s}`)}</option>)}
                  </Select>
                </Field>
              )}
              <Field label={t('isync.priority')} hint={t('isync.priority_hint')}>
                <Input type="number" min={0} max={1000} value={draft.priority}
                       onChange={e => setDraft(d => d && { ...d, priority: Number(e.target.value) })} />
              </Field>
              <Field label={t('isync.slug')} hint={t('isync.slug_hint')}>
                <Input value={draft.slug} maxLength={64} placeholder="erp-main-stock"
                       onChange={e => setDraft(d => d && { ...d, slug: e.target.value })} />
              </Field>
            </div>
            {draft.stock_model !== 'snapshot' && (
              <div style={{ marginBottom: 12 }}>
                <Field label={t('isync.model_config')} hint={t(`isync.model_config_hint.${draft.stock_model}`)}>
                  <Textarea rows={draft.stock_model === 'ledger' ? 10 : 3} value={draft.model_config_text}
                            style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}
                            onChange={e => setDraft(d => d && { ...d, model_config_text: e.target.value })} />
                </Field>
              </div>
            )}
            <div style={grid}>
              <Field label={t('isync.mode_label')}>
                <Select value={draft.mode} onChange={e => setDraft(d => d && { ...d, mode: e.target.value as Draft['mode'] })}>
                  {catalog.modes.map(m => <option key={m} value={m}>{t(`isync.mode.${m}`)}</option>)}
                </Select>
              </Field>
              {draft.mode === 'incremental' && (
                <Field label={t('isync.watermark')} hint={t('isync.watermark_hint')}>
                  <Select value={draft.watermark_column} onChange={e => setDraft(d => d && { ...d, watermark_column: e.target.value })}>
                    <option value="">{t('isync.choose')}</option>
                    {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
                  </Select>
                </Field>
              )}
              {draft.mode === 'incremental' && (
                <Field label={t('isync.overlap')}>
                  <Input type="number" min={0} value={draft.overlap_minutes} onChange={e => setDraft(d => d && { ...d, overlap_minutes: Number(e.target.value) })} />
                </Field>
              )}
              <Field label={t('isync.interval')} hint={t('isync.interval_hint', { n: catalog.limits.min_interval_minutes })}>
                <Input type="number" min={catalog.limits.min_interval_minutes} value={draft.interval_minutes}
                       onChange={e => setDraft(d => d && { ...d, interval_minutes: Number(e.target.value) })} />
              </Field>
              {draft.mode === 'full' && (
                <Field label={t('isync.missing_policy')} hint={t(`isync.missing_hint.${draft.missing_policy}`)}>
                  <Select value={draft.missing_policy} onChange={e => setDraft(d => d && { ...d, missing_policy: e.target.value as Draft['missing_policy'] })}>
                    {catalog.missing_policies.map(m => <option key={m} value={m}>{t(`isync.missing.${m}`)}</option>)}
                  </Select>
                </Field>
              )}
              <Field label={t('isync.negative_policy')}>
                <Select value={draft.negative_policy} onChange={e => setDraft(d => d && { ...d, negative_policy: e.target.value as Draft['negative_policy'] })}>
                  {catalog.negative_policies.map(m => <option key={m} value={m}>{t(`isync.negative.${m}`)}</option>)}
                </Select>
              </Field>
              <Field label={t('isync.duplicate_policy')}>
                <Select value={draft.duplicate_policy} onChange={e => setDraft(d => d && { ...d, duplicate_policy: e.target.value as Draft['duplicate_policy'] })}>
                  {catalog.duplicate_policies.map(m => <option key={m} value={m}>{t(`isync.duplicate.${m}`)}</option>)}
                </Select>
              </Field>
              <Field label={t('isync.default_warehouse')} hint={t('isync.default_warehouse_hint')}>
                <Input value={draft.default_warehouse} placeholder="principal" onChange={e => setDraft(d => d && { ...d, default_warehouse: e.target.value })} />
              </Field>
            </div>
            <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13, marginTop: 10, cursor: 'pointer' }}>
              <input type="checkbox" checked={draft.allow_new_warehouses} onChange={e => setDraft(d => d && { ...d, allow_new_warehouses: e.target.checked })} />
              <span>{t('isync.allow_new_warehouses')}<span style={{ display: 'block', fontSize: 12, color: C.dim }}>{t('isync.allow_new_warehouses_hint')}</span></span>
            </label>
            <h3 data-tour="sync.brake" style={{ margin: '14px 0 6px', fontSize: 12.5, fontWeight: 700, color: C.muted }}>{t('isync.brake_title')}</h3>
            <p style={{ ...small, marginBottom: 8 }}>{t('isync.brake_hint')}</p>
            <div style={grid}>
              <Field label={t('isync.brake_vanished')}>
                <Input type="number" min={0} max={100} value={draft.brake_max_vanished_pct} onChange={e => setDraft(d => d && { ...d, brake_max_vanished_pct: Number(e.target.value) })} />
              </Field>
              <Field label={t('isync.brake_row_ratio')}>
                <Input type="number" min={0} max={1} step={0.05} value={draft.brake_min_row_ratio} onChange={e => setDraft(d => d && { ...d, brake_min_row_ratio: Number(e.target.value) })} />
              </Field>
              <Field label={t('isync.brake_rejected')}>
                <Input type="number" min={0} max={100} value={draft.brake_max_rejected_pct} onChange={e => setDraft(d => d && { ...d, brake_max_rejected_pct: Number(e.target.value) })} />
              </Field>
              <Field label={t('isync.brake_jump')} hint={t('isync.brake_jump_hint')}>
                <Input type="number" min={0} value={draft.brake_max_jump_pct} onChange={e => setDraft(d => d && { ...d, brake_max_jump_pct: Number(e.target.value) })} />
              </Field>
            </div>
          </Card>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" data-tour="sync.preview" style={btn} disabled={busy || (columns.length === 0 && !isPush)} onClick={() => void previewDraft()}>{t('isync.dry_run')}</button>
            <button type="button" style={{ ...primary, opacity: draft.name.trim() && !busy ? 1 : 0.5 }} disabled={!draft.name.trim() || busy}
                    onClick={() => void saveDraft()}>{draft.id ? t('isync.save_changes') : t('isync.save')}</button>
            <button type="button" style={btn} disabled={busy} onClick={() => { setDraft(null); setDry(null) }}>{t('isync.cancel')}</button>
          </div>
          <p style={small}>{t('isync.save_hint')}</p>
        </>
      )}
    </div>
  )
}

function Badge({ label, t }: { label: string; t: (k: string) => string }) {
  if (!label) return null
  const color = label === 'high' ? C.green : label === 'medium' ? C.amber : C.red
  return (
    <span style={{ marginLeft: 6, fontSize: 10.5, fontWeight: 700, color, border: `1px solid ${color}`,
                   borderRadius: 999, padding: '1px 6px', verticalAlign: 'middle' }}>
      {t(`isync.confidence.${label}`)}
    </span>
  )
}

function DryRunCard({ dry, fieldLabel }: {
  dry: SyncDryRun
  fieldLabel: (f: string) => string
}) {
  const { t } = useLanguage()
  return (
    <Card padding={16}>
      <h2 data-tour="sync.dry" style={{ ...h2, marginBottom: 4 }}>{t('isync.dry_title')}</h2>
      <p style={{ ...small, marginBottom: 10 }}>
        {t('isync.dry_intro', { n: dry.rows_read })}{dry.sample_truncated ? ` ${t('isync.dry_truncated', { n: dry.sample_limit })}` : ''}
      </p>
      <div style={grid}>
        {([['would_create', C.green], ['would_update', C.amber], ['would_leave_unchanged', C.muted],
           ['rejected', C.red], ['filtered_out', C.dim]] as const).map(([k, color]) => (
          <div key={k}><div style={small}>{t(`isync.dry.${k}`)}</div>
            <strong style={{ fontSize: 18, color }}>{dry[k]}</strong></div>
        ))}
      </div>
      {dry.masters && (dry.masters.warehouses.master_rows > 0 || dry.masters.suppliers.master_rows > 0
        || dry.masters.warehouses.new.length > 0 || dry.masters.warehouses.defaulted > 0) && (
        <div style={{ marginTop: 10 }}>
          <h3 style={{ margin: '0 0 6px', fontSize: 12.5, fontWeight: 700 }}>{t('isync.dry_masters_title')}</h3>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5 }}>
            {dry.masters.warehouses.new.length > 0 && <li>{t('isync.dry_wh_new', { list: dry.masters.warehouses.new.join(', ') })}</li>}
            {dry.masters.warehouses.renamed_in_source.length > 0 && <li>{t('isync.dry_wh_renamed', {
              list: dry.masters.warehouses.renamed_in_source.map(r => `${r.stockai} ← ${r.source}`).join(', ') })}</li>}
            {dry.masters.warehouses.inactive.length > 0 && <li>{t('isync.dry_wh_inactive', { list: dry.masters.warehouses.inactive.join(', ') })}</li>}
            {dry.masters.warehouses.defaulted > 0 && <li>{t('isync.dry_wh_defaulted', { n: dry.masters.warehouses.defaulted })}</li>}
            {[...dry.masters.warehouses.collisions, ...dry.masters.suppliers.collisions].length > 0 && <li>{t('isync.dry_collisions', {
              list: [...dry.masters.warehouses.collisions, ...dry.masters.suppliers.collisions].map(c => `${c.code}: ${c.kept} / ${c.dropped}`).join('; ') })}</li>}
            {(dry.masters.suppliers.create?.length ?? 0) > 0 && <li>{t('isync.dry_sup_create', { list: dry.masters.suppliers.create!.join(', ') })}</li>}
            {(dry.masters.suppliers.lead_time_kept ?? 0) > 0 && <li>{t('isync.dry_sup_kept', { n: dry.masters.suppliers.lead_time_kept! })}</li>}
          </ul>
          {dry.masters.warehouses.totals.length > 0 && (
            <details style={{ marginTop: 6 }}>
              <summary style={{ cursor: 'pointer', fontSize: 12.5 }}>{t('isync.dry_wh_totals')}</summary>
              <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12, color: C.muted }}>
                {dry.masters.warehouses.totals.map(w => <li key={w.warehouse}>{w.warehouse}: {w.rows} · {w.quantity}</li>)}
              </ul>
            </details>
          )}
        </div>
      )}
      {(dry.advice?.length ?? 0) > 0 && (
        <div role="alert" style={{ marginTop: 10, padding: 10, borderRadius: 8, border: `1px solid ${C.amber}` }}>
          <p style={{ margin: 0, fontSize: 12.5, fontWeight: 700, color: C.amber }}>{t('isync.advice_title')}</p>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12.5 }}>
            {dry.advice!.map((a, i) => <li key={i}>{t(`isync.advice.${a.code}`, a.params)}</li>)}
          </ul>
        </div>
      )}
      {dry.unknown_warehouses.length > 0 && (
        <p role="alert" style={{ margin: '10px 0 0', fontSize: 12.5, color: C.red }}>
          {t('isync.unknown_warehouses', { list: dry.unknown_warehouses.join(', ') })}
        </p>
      )}
      {Object.keys(dry.rejected_by_code).length > 0 && (
        <>
          <h3 style={{ margin: '12px 0 6px', fontSize: 12.5, fontWeight: 700 }}>{t('isync.rejected_title')}</h3>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5 }}>
            {Object.entries(dry.rejected_by_code).map(([code, n]) => <li key={code}>{t(`isync.reject.${code}`)}: {n}</li>)}
          </ul>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12, color: C.muted }}>
            {dry.rejected_sample.slice(0, 15).map((r, i) => (
              <li key={i}>
                {r.row != null ? t('isync.row_n', { n: r.row }) + ' · ' : ''}{r.sku ?? '—'} · {t(`isync.reject.${r.code}`)}
                {r.params.field ? ` (${fieldLabel(String(r.params.field))}${r.params.value != null ? `: ${String(r.params.value)}` : ''})` : ''}
                {r.params.warehouse ? ` (${String(r.params.warehouse)})` : ''}
              </li>
            ))}
          </ul>
        </>
      )}
      {dry.changes_sample.length > 0 && (
        <>
          <h3 style={{ margin: '12px 0 6px', fontSize: 12.5, fontWeight: 700 }}>{t('isync.changes_title')}</h3>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5 }}>
            {dry.changes_sample.slice(0, 20).map((c, i) => (
              <li key={i}>
                <strong>{c.sku}</strong> · {c.warehouse} · {t(`isync.action.${c.action}`)}
                {': '}{Object.entries(c.fields).map(([f, v]) =>
                  `${fieldLabel(f)} ${v.before == null ? '' : `${String(v.before)} → `}${String(v.after)}`).join('; ')}
              </li>
            ))}
          </ul>
        </>
      )}
      {(dry.examples?.length ?? 0) > 0 && (
        <>
          <h3 style={{ margin: '12px 0 6px', fontSize: 12.5, fontWeight: 700 }}>{t(`isync.examples_title.${dry.stock_model}`)}</h3>
          {dry.examples!.map((ex, i) => (
            <div key={i} style={{ fontSize: 12.5, marginBottom: 8 }}>
              <strong>{ex.sku}</strong> · {ex.warehouse}
              <div style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11.5, color: C.muted, overflowWrap: 'anywhere' }}>
                {ex.movements
                  ? ex.movements.slice(0, 20).map(m => m.rejected ? `#${m.id} ${t(`isync.reject.${m.rejected}`)}`
                      : `${m.counted === false ? '(' : ''}${(m.signed ?? 0) >= 0 ? '+' : ''}${m.signed}${m.type ? ` ${m.type}` : ''}${m.counted === false ? ')' : ''}`).join('  ')
                  : (ex.rows ?? []).map(r => r.rejected ? t(`isync.reject.${r.rejected}`) : String(r.quantity)).join(ex.aggregate === 'sum' ? ' + ' : ', ')}
                {' = '}<strong style={{ color: 'var(--text)' }}>{ex.total ?? '—'}</strong>
                {ex.aggregate && ex.aggregate !== 'sum' ? ` (${ex.aggregate})` : ''}
              </div>
              {(ex.movement_count ?? ex.row_count ?? 0) > 20 && (
                <div style={{ fontSize: 11.5, color: C.dim }}>{t('isync.examples_more', { n: ex.movement_count ?? ex.row_count ?? 0 })}</div>
              )}
            </div>
          ))}
          <p style={small}>{t('isync.examples_hint')}</p>
        </>
      )}
      <p style={{ ...small, marginTop: 10 }}>{t('isync.dry_nothing_written')}</p>
    </Card>
  )
}
