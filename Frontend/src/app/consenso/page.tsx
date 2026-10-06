'use client'
/**
 * /consenso — S&OP forecast consensus.
 *
 * Each function (sales, finance, operations) submits an adjustment to the
 * statistical forecast per product and period, with a reason. Every revision
 * stays. The company's rule turns them into one consensus per product and
 * period; an approver publishes a frozen version, and only that version is read
 * by planning. Once real sales arrive, each adjustment and the consensus are
 * graded against the statistical forecast, so nobody claims credit without
 * evidence.
 *
 * The server does every number (the routes are served by the Rust API, the
 * grading by the same rules the engine uses); this page only renders, and says
 * plainly that nothing moves a purchase until a consensus is published. All
 * percentages travel as integer basis points (1 bp = 0.01%).
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Info, Scale, Target } from 'lucide-react'
import {
  createConsensusSubmission, decideConsensus, getConsensusFva, getConsensusPreview, getConsensusSettings,
  getConsensusVersion, getSessionLibrary, listConsensusSubmissions, listConsensusVersions, proposeConsensus,
  refreshConsensusEvidence, saveConsensusSettings,
} from '@/lib/api'
import type {
  ConsensusFunction, ConsensusFva, ConsensusPreview, ConsensusRuleName, ConsensusSettings, ConsensusStatus,
  ConsensusSubmissionList, ConsensusVersionDetail, ConsensusVersionList, SessionSummary,
} from '@/lib/types'
import Card from '@/components/ui/Card'
import Spinner from '@/components/ui/Spinner'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { usePlanning } from '@/contexts/PlanningContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { getUser } from '@/lib/auth'

const C = {
  border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  card: 'var(--surface-2)', red: '#C0504D', amber: '#B7791F', green: '#2E8B62', accent: 'var(--accent)',
}
const STATUS_COLOR: Record<ConsensusStatus, string> = {
  proposed: '#B7791F', approved: '#2E8B62', rejected: '#C0504D', withdrawn: 'var(--muted)', superseded: 'var(--dim)',
}
const FUNCTIONS: ConsensusFunction[] = ['sales', 'finance', 'operations']
type Tab = 'adjustments' | 'consensus' | 'accuracy' | 'rule'

const localeFor = (lang: string) => (lang === 'en' ? 'en-US' : 'es-CR')
/** Basis points as a signed percentage in the reader's number format: 1250 -> "+12.50%". */
const bpFormat = (locale: string) => (n: number | null | undefined) =>
  n == null ? '—' : `${n > 0 ? '+' : ''}${(n / 100).toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`
/** "12", "-5.25", "+3,5" -> basis points, or null when it is not a percentage with at most two decimals. */
function parsePct(raw: string): number | null {
  const s = raw.trim().replace(',', '.')
  if (!/^[+-]?\d+(\.\d{1,2})?$/.test(s)) return null
  return Math.round(parseFloat(s) * 100)
}
const today = () => new Date().toISOString().slice(0, 10)

export default function ConsensusPage() {
  const { t, lang } = useLanguage()
  const narrow = useIsNarrow()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const planning = usePlanning()?.planning ?? null
  const me = getUser()
  const canWrite = me?.role === 'admin' || me?.role === 'analyst'
  const locale = localeFor(lang)
  const df = useCallback((iso: string | null | undefined) =>
    iso ? new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso).toLocaleDateString(locale, { day: 'numeric', month: 'short', year: 'numeric' }) : '—',
  [locale])
  const bp = bpFormat(locale)
  const nf = (n: number | null | undefined, digits = 0) =>
    n == null ? '—' : n.toLocaleString(locale, { maximumFractionDigits: digits })

  const [sessions, setSessions] = useState<SessionSummary[] | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('adjustments')
  const [settings, setSettings] = useState<ConsensusSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [subs, setSubs] = useState<ConsensusSubmissionList | null>(null)
  const [history, setHistory] = useState(false)
  const [form, setForm] = useState({ sku: '', function: '' as ConsensusFunction | '', start: today(), end: today(), pct: '', reason: 'promotion', note: '' })

  const [preview, setPreview] = useState<ConsensusPreview | null>(null)
  const [previewLimit, setPreviewLimit] = useState(50)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [versions, setVersions] = useState<ConsensusVersionList | null>(null)
  const [proposal, setProposal] = useState({ name: '', note: '' })
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<ConsensusVersionDetail | null>(null)
  const [comment, setComment] = useState('')

  const [fva, setFva] = useState<ConsensusFva | null>(null)

  const [draft, setDraft] = useState<{
    rule: ConsensusRuleName; priority: ConsensusFunction[]; weights: Record<ConsensusFunction, string>
    down: string; up: string; members: Record<ConsensusFunction, string[]>
  } | null>(null)

  useEffect(() => {
    getSessionLibrary({ status: ['COMPLETED'], archived: 'active', limit: 500, sort: 'created_at', order: 'desc' })
      .then(r => setSessions(r.items ?? []))
      .catch(e => { setSessions([]); setError(errorDetail(e)) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    if (sessionId || !sessions?.length) return
    const active = planning?.active_session_id
    setSessionId((sessions.find(s => s.session_id === active) ?? sessions[0]).session_id)
  }, [sessions, planning, sessionId])

  const loadSettings = useCallback(() => {
    getConsensusSettings().then(s => {
      setSettings(s)
      setDraft({
        rule: s.settings.rule, priority: s.settings.priority,
        weights: { sales: String(s.settings.weights.sales), finance: String(s.settings.weights.finance), operations: String(s.settings.weights.operations) },
        down: String(s.settings.cap_down_bp / 100), up: String(s.settings.cap_up_bp / 100),
        members: { sales: s.members.sales.map(m => m.user_id), finance: s.members.finance.map(m => m.user_id), operations: s.members.operations.map(m => m.user_id) },
      })
    }).catch(e => setError(errorDetail(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { loadSettings() }, [loadSettings])

  const loadSubs = useCallback(() => {
    if (!sessionId) return
    listConsensusSubmissions(sessionId, { includeSuperseded: history, limit: 300 }).then(setSubs).catch(e => setError(errorDetail(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, history])
  useEffect(() => { setSubs(null); loadSubs() }, [loadSubs])

  const loadConsensus = useCallback(() => {
    if (!sessionId) return
    setPreviewError(null)
    getConsensusPreview(sessionId, { limit: previewLimit }).then(setPreview)
      .catch(e => { setPreview(null); setPreviewError(errorDetail(e)) })
    listConsensusVersions(sessionId).then(setVersions).catch(e => setError(errorDetail(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, previewLimit])
  useEffect(() => { if (tab === 'consensus') loadConsensus() }, [tab, loadConsensus])

  const loadDetail = useCallback((id: string) => {
    getConsensusVersion(id, { limit: 50 }).then(setDetail).catch(e => setError(errorDetail(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { if (selected) loadDetail(selected); else setDetail(null) }, [selected, loadDetail])

  const loadFva = useCallback(() => {
    if (!sessionId) return
    getConsensusFva(sessionId).then(setFva).catch(e => setError(errorDetail(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])
  useEffect(() => { if (tab === 'accuracy') loadFva() }, [tab, loadFva])

  useEffect(() => { setSelected(null); setPreview(null); setFva(null); setNotice(null); setError(null) }, [sessionId])

  const fnLabel = (f: string) => t(`consensus.function.${f}`)
  const reasonLabel = (r: string) => t(`consensus.reason.${r}`)
  const mine = settings?.can_submit ?? { sales: false, finance: false, operations: false }
  const submitFunctions = FUNCTIONS.filter(f => mine[f])

  async function submit() {
    if (!sessionId) return
    const pctBp = parsePct(form.pct)
    const fn = (form.function || submitFunctions[0]) as ConsensusFunction | undefined
    if (pctBp == null) { setError(t('consensus.pct_invalid')); return }
    if (!fn) { setError(t('consensus.not_member_hint')); return }
    setBusy(true); setError(null); setNotice(null)
    try {
      await createConsensusSubmission(sessionId, {
        sku: form.sku.trim(), function: fn, start_date: form.start, end_date: form.end, pct_bp: pctBp,
        reason_code: form.reason, reason_note: form.note.trim() || null,
      })
      setNotice(t('consensus.submitted', { function: fnLabel(fn), sku: form.sku.trim() }))
      setForm(f => ({ ...f, pct: '', note: '' }))
      loadSubs()
    } catch (e: unknown) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  async function propose() {
    if (!sessionId) return
    const name = proposal.name.trim() || t('consensus.default_name', { month: new Date().toLocaleDateString(locale, { month: 'long', year: 'numeric' }) })
    setBusy(true); setError(null); setNotice(null)
    try {
      const v = await proposeConsensus(sessionId, { name: name.slice(0, 120), note: proposal.note.trim() || null })
      setNotice(t('consensus.proposed', { name: v.name }))
      setProposal({ name: '', note: '' })
      loadConsensus(); setSelected(v.id)
    } catch (e: unknown) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  async function decide(action: 'approve' | 'reject' | 'withdraw') {
    if (!detail) return
    if (action !== 'approve' && comment.trim().length < 3) { setError(t('consensus.reason_needed')); return }
    const ok = await confirm({
      title: t(`consensus.${action}_confirm_title`, { name: detail.name }),
      message: t(`consensus.${action}_confirm_message`),
      confirmLabel: t(`consensus.${action}`), cancelLabel: t('common.cancel'),
      danger: action !== 'approve',
    })
    if (!ok) return
    setBusy(true); setError(null); setNotice(null)
    try {
      await decideConsensus(detail.id, action, comment.trim() || undefined)
      setComment(''); loadConsensus(); loadDetail(detail.id)
    } catch (e: unknown) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  async function refreshEvidence() {
    if (!sessionId) return
    setBusy(true); setError(null); setNotice(null)
    try {
      const r = await refreshConsensusEvidence(sessionId)
      const known = ['nothing_to_grade', 'no_forecast', 'no_data_yet', 'dataset_not_found', 'unreadable', 'too_large', 'columns_missing']
      setNotice(r.status === 'ok' ? t('consensus.fva_refreshed', { rows: r.rows })
        : known.includes(r.status) ? t(`consensus.refresh_${r.status}`) : t('consensus.refresh_other', { status: r.status }))
      loadFva()
    } catch (e: unknown) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  async function saveRule() {
    if (!draft) return
    const down = parsePct(draft.down), up = parsePct(draft.up)
    if (down == null || up == null) { setError(t('consensus.pct_invalid')); return }
    const weights = {} as Record<ConsensusFunction, number>
    for (const f of FUNCTIONS) weights[f] = Number(draft.weights[f])
    setBusy(true); setError(null); setNotice(null)
    try {
      const s = await saveConsensusSettings({
        rule: draft.rule, priority: draft.priority, weights, cap_down_bp: down, cap_up_bp: up, members: draft.members,
      })
      setSettings(s); setNotice(t('consensus.rule_saved'))
    } catch (e: unknown) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  const field: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box', fontSize: narrow ? 16 : 12.5, padding: narrow ? '10px 10px' : '6px 8px',
    borderRadius: narrow ? 10 : 7, border: `1px solid ${C.border}`, background: 'var(--surface-2)', color: C.text,
    minHeight: narrow ? 44 : undefined, fontFamily: 'inherit',
  }
  const btn = (primary = false, disabled = false, color?: string): React.CSSProperties => ({
    all: 'unset', cursor: disabled ? 'default' : 'pointer', boxSizing: 'border-box', display: 'inline-flex',
    alignItems: 'center', justifyContent: 'center', gap: 5, padding: narrow ? '0 14px' : '6px 12px',
    minHeight: narrow ? 44 : undefined, borderRadius: narrow ? 10 : 7, fontSize: narrow ? 14 : 12, fontWeight: 600,
    opacity: disabled ? 0.5 : 1, border: `1px solid ${color ?? C.border}`, color: color ?? C.text,
    ...(primary ? { background: 'color-mix(in srgb, var(--accent) 10%, transparent)' } : {}),
  })
  const lbl: React.CSSProperties = { fontSize: narrow ? 13 : 11.5, color: C.muted, display: 'flex', flexDirection: 'column', gap: 4 }
  const eyebrow: React.CSSProperties = { fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }
  const th: React.CSSProperties = { textAlign: 'left', padding: '6px 10px', color: C.muted, fontWeight: 600, whiteSpace: 'nowrap', borderBottom: `1px solid ${C.border}` }
  const td: React.CSSProperties = { padding: '5px 10px', verticalAlign: 'top', color: C.text }
  const grid2: React.CSSProperties = { display: 'grid', gap: 10, gridTemplateColumns: narrow ? '1fr' : 'repeat(auto-fit, minmax(190px, 1fr))' }

  const chip = (s: ConsensusStatus) => (
    <span style={{ fontSize: 10.5, fontWeight: 700, color: STATUS_COLOR[s], border: `1px solid ${STATUS_COLOR[s]}`,
      borderRadius: 6, padding: '1px 7px', whiteSpace: 'nowrap' }}>{t(`consensus.status_${s}`)}</span>
  )
  const periodText = (a: string, b: string) => (a === b ? df(a) : `${df(a)} – ${df(b)}`)

  const verdictColor = (v: string) => (v === 'improved' ? C.green : v === 'worsened' ? C.red : C.muted)
  const fvaTable = (rows: { label: string; fva: ConsensusFva['by_function'][number] }[]) => (
    <div style={{ overflowX: 'auto', border: `1px solid ${C.border}`, borderRadius: 8 }}>
      <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: '100%' }}>
        <thead><tr>
          <th scope="col" style={th}>{t('consensus.col_who')}</th>
          <th scope="col" style={{ ...th, textAlign: 'right' }}>{t('consensus.col_points')}</th>
          <th scope="col" style={{ ...th, textAlign: 'right' }}>{t('consensus.col_stat_error')}</th>
          <th scope="col" style={{ ...th, textAlign: 'right' }}>{t('consensus.col_adj_error')}</th>
          <th scope="col" style={{ ...th, textAlign: 'right' }}>{t('consensus.col_improvement')}</th>
          <th scope="col" style={th}>{t('consensus.col_verdict')}</th>
        </tr></thead>
        <tbody>{rows.map(r => (
          <tr key={r.label}>
            <td style={td}>{r.label}</td>
            <td style={{ ...td, textAlign: 'right' }}>{nf(r.fva.n_points)}</td>
            <td style={{ ...td, textAlign: 'right' }}>{r.fva.base_wape == null ? '—' : `${nf(r.fva.base_wape * 100, 1)}%`}</td>
            <td style={{ ...td, textAlign: 'right' }}>{r.fva.adjusted_wape == null ? '—' : `${nf(r.fva.adjusted_wape * 100, 1)}%`}</td>
            <td style={{ ...td, textAlign: 'right' }}>{r.fva.improvement_pct == null ? '—' : `${r.fva.improvement_pct > 0 ? '+' : ''}${nf(r.fva.improvement_pct, 1)}%`}</td>
            <td style={{ ...td, fontWeight: 650, color: verdictColor(r.fva.verdict) }}>{t(`consensus.verdict_${r.fva.verdict}`)}</td>
          </tr>))}
        </tbody>
      </table>
    </div>
  )

  const published = useMemo(() => versions?.items.find(v => v.status === 'approved') ?? null, [versions])
  const tabs: { id: Tab; label: string }[] = [
    { id: 'adjustments', label: t('consensus.tab_adjustments') }, { id: 'consensus', label: t('consensus.tab_consensus') },
    { id: 'accuracy', label: t('consensus.tab_accuracy') }, { id: 'rule', label: t('consensus.tab_rule') },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <p style={{ margin: 0, fontSize: 12.5, color: C.dim, lineHeight: 1.5 }}>{t('consensus.intro')}</p>
      <div role="note" style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '10px 14px', borderRadius: 8,
        border: `1px solid ${C.border}`, background: C.card, fontSize: 12.5, color: C.text }}>
        <Info size={14} color="var(--accent)" aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
        <span>{t('consensus.effect_note')}</span>
      </div>
      {notice && <p role="status" style={{ margin: 0, fontSize: 12.5, color: C.text }}>{notice}</p>}
      {error && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{error}</p>}

      {sessions && sessions.length === 0 && <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t('consensus.no_sessions')}</p>}
      {sessions && sessions.length > 0 && (
        <label style={{ ...lbl, maxWidth: narrow ? '100%' : 420 }}>{t('consensus.session')}
          <select style={field} value={sessionId ?? ''} onChange={e => setSessionId(e.target.value)}>
            {sessions.map(s => <option key={s.session_id} value={s.session_id}>{s.name}</option>)}
          </select>
        </label>
      )}
      {!sessions && <div style={{ padding: 32, display: 'flex', justifyContent: 'center' }}><Spinner /></div>}

      {sessionId && (
        <div role="tablist" aria-label={t('consensus.tabs_label')} style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {tabs.map(x => (
            <button key={x.id} type="button" role="tab" aria-selected={tab === x.id} onClick={() => setTab(x.id)}
              style={{ ...btn(tab === x.id), fontWeight: tab === x.id ? 700 : 600 }}>{x.label}</button>
          ))}
        </div>
      )}

      {/* ── Adjustments ─────────────────────────────────────────────────── */}
      {sessionId && tab === 'adjustments' && (
        <>
          {canWrite && (
            <Card padding={narrow ? '14px' : '16px 18px'}>
              <div style={{ ...eyebrow, marginBottom: 10 }}>{t('consensus.adjust_title')}</div>
              {settings && submitFunctions.length === 0 ? (
                <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('consensus.not_member_hint')}</p>
              ) : (
                <>
                  <div style={grid2}>
                    <label style={lbl}>{t('consensus.field_sku')}
                      <input style={field} type="text" maxLength={200} value={form.sku} onChange={e => setForm(f => ({ ...f, sku: e.target.value }))} />
                    </label>
                    <label style={lbl}>{t('consensus.field_function')}
                      <select style={field} value={form.function || submitFunctions[0] || ''} onChange={e => setForm(f => ({ ...f, function: e.target.value as ConsensusFunction }))}>
                        {submitFunctions.map(f => <option key={f} value={f}>{fnLabel(f)}</option>)}
                      </select>
                    </label>
                    <label style={lbl}>{t('consensus.field_start')}
                      <input style={field} type="date" value={form.start} onChange={e => setForm(f => ({ ...f, start: e.target.value }))} />
                    </label>
                    <label style={lbl}>{t('consensus.field_end')}
                      <input style={field} type="date" value={form.end} onChange={e => setForm(f => ({ ...f, end: e.target.value }))} />
                    </label>
                    <label style={lbl}>{t('consensus.field_pct')}
                      <input style={field} type="text" inputMode="decimal" value={form.pct} placeholder="+10"
                        onChange={e => setForm(f => ({ ...f, pct: e.target.value }))} />
                    </label>
                    <label style={lbl}>{t('consensus.field_reason')}
                      <select style={field} value={form.reason} onChange={e => setForm(f => ({ ...f, reason: e.target.value }))}>
                        {(settings?.reasons ?? []).map(r => <option key={r} value={r}>{reasonLabel(r)}</option>)}
                      </select>
                    </label>
                    <label style={{ ...lbl, gridColumn: '1 / -1' }}>{t('consensus.field_note')}
                      <input style={field} type="text" maxLength={300} value={form.note} onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
                    </label>
                  </div>
                  <p style={{ margin: '8px 0 0', fontSize: 11.5, color: C.dim }}>{t('consensus.pct_hint')}</p>
                  <div style={{ marginTop: 12 }}>
                    <button type="button" disabled={busy || !form.sku.trim()} onClick={submit} style={btn(true, busy || !form.sku.trim())}>
                      <CheckCircle2 size={13} aria-hidden="true" /> {busy ? t('common.saving') : t('consensus.submit')}
                    </button>
                  </div>
                </>
              )}
            </Card>
          )}
          <section aria-labelledby="cs-list" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <h2 id="cs-list" style={{ ...eyebrow, margin: 0 }}>{t('consensus.list_title')}</h2>
              <label style={{ fontSize: 12, color: C.muted, display: 'flex', gap: 6, alignItems: 'center' }}>
                <input type="checkbox" checked={history} onChange={e => setHistory(e.target.checked)} /> {t('consensus.show_history')}
              </label>
            </div>
            {!subs && <div style={{ padding: 24, display: 'flex', justifyContent: 'center' }}><Spinner /></div>}
            {subs && subs.items.length === 0 && <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t('consensus.list_empty')}</p>}
            {subs && subs.items.length > 0 && (
              <div style={{ overflowX: 'auto', border: `1px solid ${C.border}`, borderRadius: 8 }}>
                <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: '100%' }}>
                  <thead><tr>
                    {['col_sku', 'col_function', 'col_period', 'col_adjustment', 'col_reason', 'col_by', 'col_revision'].map(k => (
                      <th key={k} scope="col" style={th}>{t(`consensus.${k}`)}</th>))}
                  </tr></thead>
                  <tbody>{subs.items.map(s => (
                    <tr key={s.id} style={{ opacity: s.superseded_by ? 0.55 : 1 }}>
                      <td style={{ ...td, fontWeight: 600, overflowWrap: 'anywhere' }}>{s.sku}</td>
                      <td style={td}>{fnLabel(s.function)}</td>
                      <td style={{ ...td, whiteSpace: 'nowrap' }}>{periodText(s.start_date, s.end_date)}</td>
                      <td style={{ ...td, fontWeight: 650 }}>{bp(s.pct_bp)}</td>
                      <td style={td}>{reasonLabel(s.reason_code)}{s.reason_note ? ` — ${s.reason_note}` : ''}</td>
                      <td style={td}>{s.created_by_name || '—'}<div style={{ fontSize: 11, color: C.dim }}>{df(s.created_at)}</div></td>
                      <td style={td}>{s.revision}{s.superseded_by ? ` · ${t('consensus.superseded_tag')}` : ''}</td>
                    </tr>))}
                  </tbody>
                </table>
              </div>
            )}
            {subs && subs.total > subs.items.length && (
              <p style={{ margin: 0, fontSize: 11.5, color: C.dim }}>{t('consensus.list_more', { shown: subs.items.length, total: subs.total })}</p>
            )}
          </section>
        </>
      )}

      {/* ── Consensus ───────────────────────────────────────────────────── */}
      {sessionId && tab === 'consensus' && (
        <>
          {published && (
            <p role="status" style={{ margin: 0, fontSize: 12.5, color: C.green, fontWeight: 600 }}>
              {t('consensus.published_banner', { name: published.name, date: df(published.decided_at) })}
            </p>
          )}
          <Card padding={narrow ? '14px' : '16px 18px'}>
            <div style={{ ...eyebrow, marginBottom: 6 }}>{t('consensus.preview_title')}</div>
            {previewError && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.amber }}>{previewError}</p>}
            {!preview && !previewError && <Spinner />}
            {preview && (
              <>
                <p style={{ margin: '0 0 8px', fontSize: 12, color: C.dim }}>
                  {t('consensus.preview_summary', { lines: preview.line_count, skus: preview.sku_count, n: preview.n_submissions })}
                  {' '}{preview.rule.rule === 'priority' ? t('consensus.rule_priority_short') : t('consensus.rule_weighted_short')}
                </p>
                {preview.lines.items.length === 0 ? (
                  <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t('consensus.preview_empty')}</p>
                ) : (
                  <LinesTable lines={preview.lines.items} total={preview.lines.total} th={th} td={td} df={df} t={t} fnLabel={fnLabel} bp={bp}
                    onMore={() => setPreviewLimit(l => l + 100)} />
                )}
              </>
            )}
            {canWrite && preview && preview.line_count > 0 && (
              <div style={{ marginTop: 14, display: 'grid', gap: 10, gridTemplateColumns: narrow ? '1fr' : '1fr 2fr auto', alignItems: 'end' }}>
                <label style={lbl}>{t('consensus.field_name')}
                  <input style={field} type="text" maxLength={120} value={proposal.name} placeholder={t('consensus.default_name', { month: new Date().toLocaleDateString(locale, { month: 'long', year: 'numeric' }) })}
                    onChange={e => setProposal(p => ({ ...p, name: e.target.value }))} />
                </label>
                <label style={lbl}>{t('consensus.field_version_note')}
                  <input style={field} type="text" maxLength={1000} value={proposal.note} onChange={e => setProposal(p => ({ ...p, note: e.target.value }))} />
                </label>
                <button type="button" disabled={busy} onClick={propose} style={btn(true, busy)}>
                  <Scale size={13} aria-hidden="true" /> {busy ? t('common.saving') : t('consensus.propose')}
                </button>
              </div>
            )}
          </Card>

          <section aria-labelledby="cs-versions" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <h2 id="cs-versions" style={{ ...eyebrow, margin: 0 }}>{t('consensus.versions_title')}</h2>
            {!versions && <Spinner />}
            {versions && versions.items.length === 0 && <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t('consensus.versions_empty')}</p>}
            {versions && versions.items.length > 0 && (
              <p style={{ margin: 0, fontSize: 11.5, color: C.dim }}>{t('consensus.approvers_hint', { n: versions.approver_count })}</p>
            )}
            <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {(versions?.items ?? []).map(v => (
                <li key={v.id}>
                  <Card padding={narrow ? '12px 14px' : '12px 16px'} highlighted={selected === v.id}
                    style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ minWidth: 0, flex: '1 1 260px', overflowWrap: 'anywhere' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 13.5, fontWeight: 650, color: C.text }}>
                        <span>{v.name}</span>{chip(v.status)}
                      </div>
                      <div style={{ fontSize: 12, color: C.dim, marginTop: 3 }}>
                        {t('consensus.version_meta', { lines: v.line_count, skus: v.sku_count })} · {t('consensus.created_by', { name: v.created_by_name || '—', date: df(v.created_at) })}
                      </div>
                    </div>
                    <button type="button" style={btn()} onClick={() => setSelected(selected === v.id ? null : v.id)} aria-expanded={selected === v.id}>
                      {selected === v.id ? t('consensus.close') : t('consensus.open')}
                    </button>
                  </Card>
                </li>
              ))}
            </ul>
          </section>

          {selected && !detail && <div style={{ padding: 24, display: 'flex', justifyContent: 'center' }}><Spinner /></div>}
          {detail && (
            <Card padding={narrow ? '14px' : '18px 20px'} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: C.text, overflowWrap: 'anywhere' }}>{t('consensus.detail_title', { name: detail.name })}</h2>
                {chip(detail.status)}
              </div>
              {detail.note && <p style={{ margin: 0, fontSize: 12.5, color: C.muted }}>{detail.note}</p>}
              <p style={{ margin: 0, fontSize: 12, color: C.dim }}>
                {detail.rule.rule === 'priority'
                  ? t('consensus.rule_frozen_priority', { order: detail.rule.priority.map(fnLabel).join(' > '), down: bp(detail.rule.cap_down_bp), up: bp(detail.rule.cap_up_bp) })
                  : t('consensus.rule_frozen_weighted', { weights: FUNCTIONS.map(f => `${fnLabel(f)} ${detail.rule.weights[f]}`).join(', '), down: bp(detail.rule.cap_down_bp), up: bp(detail.rule.cap_up_bp) })}
              </p>
              {detail.status === 'proposed' && detail.revised_inputs > 0 && (
                <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.amber }}>{t('consensus.stale_warning', { n: detail.revised_inputs })}</p>
              )}
              {detail.status === 'proposed' && detail.expired && (
                <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.amber }}>{t('consensus.expired_warning')}</p>
              )}
              <LinesTable lines={detail.lines.items} total={detail.lines.total} th={th} td={td} df={df} t={t} fnLabel={fnLabel} bp={bp} />
              <div>
                <div style={{ ...eyebrow, marginBottom: 6 }}>{t('consensus.history_title')}</div>
                <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12.5, color: C.text }}>
                  {detail.events.map(e => (
                    <li key={e.id}>
                      {t(`consensus.event_${e.to_status}`, { name: e.actor_name || '—' })} · {df(e.created_at)}
                      {e.comment ? <span style={{ color: C.muted }}> — {e.comment}</span> : null}
                    </li>
                  ))}
                </ul>
                {detail.self_approved && <p style={{ margin: '6px 0 0', fontSize: 11.5, color: C.amber }}>{t('consensus.self_approved_note')}</p>}
              </div>
              {canWrite && detail.can_approve && (detail.status === 'proposed' || detail.status === 'approved') && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, borderTop: `1px solid ${C.border}`, paddingTop: 12 }}>
                  <label style={lbl}>{t('consensus.decision_comment')}
                    <input style={field} type="text" maxLength={1000} value={comment} onChange={e => setComment(e.target.value)} />
                  </label>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {detail.status === 'proposed' && (
                      <>
                        <button type="button" disabled={busy} onClick={() => decide('approve')} style={btn(true, busy, C.green)}>{t('consensus.approve')}</button>
                        <button type="button" disabled={busy} onClick={() => decide('reject')} style={btn(false, busy, C.red)}>{t('consensus.reject')}</button>
                      </>
                    )}
                    {detail.status === 'approved' && (
                      <button type="button" disabled={busy} onClick={() => decide('withdraw')} style={btn(false, busy, C.red)}>{t('consensus.withdraw')}</button>
                    )}
                  </div>
                </div>
              )}
              {detail.status === 'proposed' && !detail.can_approve && (
                <p style={{ margin: 0, fontSize: 12, color: C.dim }}>{t('consensus.cannot_approve')}</p>
              )}
            </Card>
          )}
        </>
      )}

      {/* ── Accuracy ────────────────────────────────────────────────────── */}
      {sessionId && tab === 'accuracy' && (
        <Card padding={narrow ? '14px' : '18px 20px'} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ ...eyebrow, display: 'flex', alignItems: 'center', gap: 6 }}><Target size={12} aria-hidden="true" /> {t('consensus.fva_title')}</div>
          <p style={{ margin: 0, fontSize: 12, color: C.dim }}>{t('consensus.fva_intro')}</p>
          {canWrite && (
            <div><button type="button" disabled={busy} onClick={refreshEvidence} style={btn(true, busy)}>
              {busy ? t('consensus.fva_refreshing') : t('consensus.fva_refresh')}
            </button></div>
          )}
          {!fva && <Spinner />}
          {fva && fva.status === 'no_adjustments' && <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t('consensus.fva_no_adjustments')}</p>}
          {fva && fva.status === 'no_evidence' && <p style={{ margin: 0, fontSize: 13, color: C.muted }}>{t('consensus.fva_no_evidence')}</p>}
          {fva && fva.status === 'ok' && (
            <>
              {fva.evidence && (
                <p style={{ margin: 0, fontSize: 11.5, color: C.dim }}>
                  {t('consensus.fva_evidence', { rows: fva.evidence.rows, first: df(fva.evidence.first_period), last: df(fva.evidence.last_period), at: df(fva.evidence.refreshed_at) })}
                  {fva.n_ungraded > 0 ? ` ${t('consensus.fva_ungraded', { n: fva.n_ungraded })}` : ''}
                </p>
              )}
              {fva.consensus.length > 0 && (
                <div>
                  <div style={{ ...eyebrow, marginBottom: 6 }}>{t('consensus.fva_consensus')}</div>
                  {fvaTable(fva.consensus.map(c => ({ label: `${c.name} (${t(`consensus.status_${c.status}`)})`, fva: { ...c.fva, function: 'sales' } })))}
                </div>
              )}
              {fva.by_function.length > 0 && (
                <div>
                  <div style={{ ...eyebrow, marginBottom: 6 }}>{t('consensus.fva_by_function')}</div>
                  {fvaTable(fva.by_function.map(r => ({ label: fnLabel(r.function), fva: r })))}
                </div>
              )}
              {fva.by_user.length > 0 && (
                <div>
                  <div style={{ ...eyebrow, marginBottom: 6 }}>{t('consensus.fva_by_user')}</div>
                  {fvaTable(fva.by_user.map(r => ({ label: r.name || r.user, fva: { ...r, function: 'sales' } })))}
                </div>
              )}
              {fva.by_reason.length > 0 && (
                <div>
                  <div style={{ ...eyebrow, marginBottom: 6 }}>{t('consensus.fva_by_reason')}</div>
                  {fvaTable(fva.by_reason.map(r => ({ label: reasonLabel(r.reason), fva: { ...r, function: 'sales' } })))}
                </div>
              )}
              {fva.by_submission.length > 0 && (
                <div>
                  <div style={{ ...eyebrow, marginBottom: 6 }}>{t('consensus.fva_by_submission')}</div>
                  {fvaTable(fva.by_submission.map(r => ({
                    label: r.submission ? `${r.submission.sku} · ${fnLabel(r.submission.function)} ${bp(r.submission.pct_bp)}` : r.submission_id,
                    fva: { ...r, function: 'sales' },
                  })))}
                </div>
              )}
            </>
          )}
        </Card>
      )}

      {/* ── Rule ────────────────────────────────────────────────────────── */}
      {sessionId && tab === 'rule' && settings && draft && (
        <Card padding={narrow ? '14px' : '18px 20px'} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={eyebrow}>{t('consensus.rule_title')}</div>
          {!settings.configured && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.amber }}>{t('consensus.rule_not_configured')}</p>}
          {settings.updated && <p style={{ margin: 0, fontSize: 11.5, color: C.dim }}>{t('consensus.rule_updated', { date: df(settings.updated.at) })}</p>}
          {me?.role !== 'admin' && <p style={{ margin: 0, fontSize: 12, color: C.dim }}>{t('consensus.rule_view_only')}</p>}
          <fieldset disabled={me?.role !== 'admin' || busy} style={{ border: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
              {(['priority', 'weighted'] as ConsensusRuleName[]).map(r => (
                <label key={r} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13, color: C.text }}>
                  <input type="radio" name="consensus-rule" checked={draft.rule === r} onChange={() => setDraft(d => d && { ...d, rule: r })} />
                  {t(`consensus.rule_${r}`)}
                </label>
              ))}
            </div>
            {draft.rule === 'priority' ? (
              <div style={grid2}>
                {[0, 1, 2].map(i => (
                  <label key={i} style={lbl}>{t('consensus.priority_position', { n: i + 1 })}
                    <select style={field} value={draft.priority[i]} onChange={e => setDraft(d => {
                      if (!d) return d
                      const next = [...d.priority]; const old = next[i]; const v = e.target.value as ConsensusFunction
                      const j = next.indexOf(v); next[i] = v; if (j >= 0) next[j] = old
                      return { ...d, priority: next }
                    })}>
                      {FUNCTIONS.map(f => <option key={f} value={f}>{fnLabel(f)}</option>)}
                    </select>
                  </label>
                ))}
              </div>
            ) : (
              <div style={grid2}>
                {FUNCTIONS.map(f => (
                  <label key={f} style={lbl}>{t('consensus.weight_of', { function: fnLabel(f) })}
                    <input style={field} type="number" min={0} max={1000} step={1} value={draft.weights[f]}
                      onChange={e => setDraft(d => d && { ...d, weights: { ...d.weights, [f]: e.target.value } })} />
                  </label>
                ))}
              </div>
            )}
            <div style={grid2}>
              <label style={lbl}>{t('consensus.cap_down')}
                <input style={field} type="text" inputMode="decimal" value={draft.down} onChange={e => setDraft(d => d && { ...d, down: e.target.value })} />
              </label>
              <label style={lbl}>{t('consensus.cap_up')}
                <input style={field} type="text" inputMode="decimal" value={draft.up} onChange={e => setDraft(d => d && { ...d, up: e.target.value })} />
              </label>
            </div>
            <p style={{ margin: 0, fontSize: 11.5, color: C.dim }}>{t('consensus.rule_hint')}</p>
            {settings.candidates && (
              <div>
                <div style={{ ...eyebrow, marginBottom: 6 }}>{t('consensus.members_title')}</div>
                <p style={{ margin: '0 0 8px', fontSize: 11.5, color: C.dim }}>{t('consensus.members_hint')}</p>
                <div style={grid2}>
                  {FUNCTIONS.map(f => (
                    <div key={f} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      <strong style={{ fontSize: 12.5, color: C.text }}>{fnLabel(f)}</strong>
                      {settings.candidates?.map(c => (
                        <label key={c.user_id} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12.5, color: C.text }}>
                          <input type="checkbox" checked={draft.members[f].includes(c.user_id)} onChange={e => setDraft(d => d && ({
                            ...d, members: { ...d.members, [f]: e.target.checked ? [...d.members[f], c.user_id] : d.members[f].filter(x => x !== c.user_id) },
                          }))} />
                          {c.name || c.user_id}
                        </label>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </fieldset>
          {me?.role === 'admin' && (
            <div><button type="button" disabled={busy} onClick={saveRule} style={btn(true, busy)}>{busy ? t('common.saving') : t('consensus.save_rule')}</button></div>
          )}
          {me?.role !== 'admin' && (
            <div style={{ fontSize: 12.5, color: C.text }}>
              {FUNCTIONS.map(f => (
                <div key={f}><strong>{fnLabel(f)}</strong>: {settings.members[f].map(m => m.name || m.user_id).join(', ') || '—'}</div>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  )
}

/** The lines of a consensus: period, the agreed figure, who decided it, and what each function said. */
function LinesTable({ lines, total, th, td, df, t, fnLabel, bp, onMore }: {
  lines: ConsensusPreview['lines']['items']; total: number
  th: React.CSSProperties; td: React.CSSProperties
  df: (iso: string | null | undefined) => string
  t: (k: string, p?: Record<string, string | number>) => string
  fnLabel: (f: string) => string
  bp: (n: number | null | undefined) => string
  onMore?: () => void
}) {
  return (
    <div>
      <div style={{ overflowX: 'auto', border: `1px solid ${C.border}`, borderRadius: 8 }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: '100%' }}>
          <thead><tr>
            {['col_sku', 'col_period', 'col_consensus', 'col_source', 'col_inputs'].map(k => (
              <th key={k} scope="col" style={th}>{t(`consensus.${k}`)}</th>))}
          </tr></thead>
          <tbody>{lines.map((l, i) => (
            <tr key={`${l.sku}-${l.start_date}-${i}`}>
              <td style={{ ...td, fontWeight: 600, overflowWrap: 'anywhere' }}>{l.sku}</td>
              <td style={{ ...td, whiteSpace: 'nowrap' }}>{l.start_date === l.end_date ? df(l.start_date) : `${df(l.start_date)} – ${df(l.end_date)}`}</td>
              <td style={{ ...td, fontWeight: 700 }}>{bp(l.pct_bp)}</td>
              <td style={td}>{l.source ? t('consensus.source_priority', { function: fnLabel(l.source) }) : t('consensus.source_weighted')}</td>
              <td style={td}>{l.inputs.map(x => `${fnLabel(x.function)} ${bp(x.pct_bp)}${x.capped ? ` (${t('consensus.capped_tag')})` : ''}`).join(' · ')}</td>
            </tr>))}
          </tbody>
        </table>
      </div>
      {total > lines.length && (
        <p style={{ margin: '6px 0 0', fontSize: 11.5, color: C.dim }}>
          {t('consensus.lines_more', { shown: lines.length, total })}
          {onMore && <button type="button" onClick={onMore} style={{ all: 'unset', cursor: 'pointer', marginLeft: 8, color: C.accent, fontWeight: 600 }}>{t('consensus.show_more')}</button>}
        </p>
      )}
    </div>
  )
}
