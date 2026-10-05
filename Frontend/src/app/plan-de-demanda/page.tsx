'use client'
/**
 * /plan-de-demanda — demand plan versions with sign-off (an S&OP-lite record).
 *
 * A planner freezes the current plan (statistical forecast + manual adjustments
 * + committed customer orders) as a version, submits it, and an admin or a
 * flagged approver approves or rejects it. Two versions can be compared, and an
 * approved one is measured against real sales once its periods close, next to
 * the plain statistical forecast over the same points.
 *
 * It is a record and a measurement: nothing on this screen changes a purchase
 * recommendation, and the screen says so. The server does every number; this
 * page only renders. One component for desktop and phone (`useIsNarrow` only
 * changes sizes and stacks the rows).
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { FileCheck2, GitCompare, Info, MessageSquare, Target } from 'lucide-react'
import {
  commentDemandPlan, createDemandPlan, decideDemandPlan, diffDemandPlans,
  getDemandPlan, getDemandPlanAccuracy, getDemandPlanLines, listDemandPlans,
} from '@/lib/api'
import type {
  DemandPlanAccuracy, DemandPlanDiff, DemandPlanEvent, DemandPlanLines, DemandPlanList,
  DemandPlanStatus, DemandPlanVersion,
} from '@/lib/types'
import Card from '@/components/ui/Card'
import Spinner from '@/components/ui/Spinner'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { getUser } from '@/lib/auth'

const C = {
  border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  card: 'var(--surface-2)', red: '#C0504D', amber: '#B7791F', green: '#2E8B62', accent: 'var(--accent)',
}

const STATUS_COLOR: Record<DemandPlanStatus, string> = {
  draft: 'var(--muted)', submitted: '#B7791F', approved: '#2E8B62', rejected: '#C0504D', superseded: 'var(--dim)',
}

const localeFor = (lang: string) => (lang === 'en' ? 'en-US' : 'es-CR')

export default function DemandPlanPage() {
  const { t, lang } = useLanguage()
  const narrow = useIsNarrow()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const me = getUser()
  const canWrite = me?.role === 'admin' || me?.role === 'analyst'
  const locale = localeFor(lang)

  const nf = useCallback((n: number | null | undefined) =>
    n == null ? '—' : Math.round(n).toLocaleString(locale), [locale])
  const pf = (x: number | null | undefined, signed = false) =>
    x == null ? '—' : `${signed && x > 0 ? '+' : ''}${(x * 100).toFixed(1)}%`
  const df = useCallback((iso: string | null | undefined) =>
    iso ? new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso).toLocaleDateString(locale, { day: 'numeric', month: 'short', year: 'numeric' }) : '—',
  [locale])

  const [list, setList] = useState<DemandPlanList | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const defaultName = () => t('demand_plan.default_name', {
    month: new Date().toLocaleDateString(locale, { month: 'long', year: 'numeric' }),
  })
  const [form, setForm] = useState({ name: '', horizon: '', note: '' })

  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<DemandPlanVersion | null>(null)
  const [lines, setLines] = useState<DemandPlanLines | null>(null)
  const [search, setSearch] = useState('')
  const [accuracy, setAccuracy] = useState<DemandPlanAccuracy | null>(null)
  const [accuracyLoading, setAccuracyLoading] = useState(false)
  const [comment, setComment] = useState('')

  const [diffA, setDiffA] = useState('')
  const [diffB, setDiffB] = useState('')
  const [diff, setDiff] = useState<DemandPlanDiff | null>(null)

  const load = useCallback(() => {
    listDemandPlans()
      .then(r => { setList(r); setLoadError(null) })
      .catch(e => { setList(null); setLoadError(errorDetail(e)) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { load() }, [load])

  // Default the comparison to "the version before the newest" vs "the newest".
  useEffect(() => {
    const items = list?.items ?? []
    if (items.length >= 2 && !diffA && !diffB) { setDiffA(items[1].id); setDiffB(items[0].id) }
  }, [list, diffA, diffB])

  const loadDetail = useCallback((id: string) => {
    getDemandPlan(id).then(d => {
      setDetail(d)
      setAccuracy(null)
      if (d.status === 'approved' || d.status === 'superseded') {
        setAccuracyLoading(true)
        getDemandPlanAccuracy(id).then(setAccuracy).catch(e => setError(errorDetail(e)))
          .finally(() => setAccuracyLoading(false))
      }
    }).catch(e => setError(errorDetail(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!selected) { setDetail(null); setLines(null); setAccuracy(null); return }
    loadDetail(selected)
  }, [selected, loadDetail])

  useEffect(() => {
    if (!selected) return
    const handle = setTimeout(() => {
      getDemandPlanLines(selected, { q: search.trim() || undefined, limit: 20 })
        .then(setLines).catch(e => setError(errorDetail(e)))
    }, 250)
    return () => clearTimeout(handle)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, search])

  async function save() {
    const name = (form.name.trim() || defaultName()).slice(0, 120)
    const horizon = form.horizon.trim() === '' ? null : Number(form.horizon)
    setBusy(true); setError(null); setNotice(null)
    try {
      const v = await createDemandPlan({ name, horizon_periods: horizon, note: form.note.trim() || null })
      setNotice(t('demand_plan.saved', { name: v.name }))
      setForm({ name: '', horizon: '', note: '' })
      load(); setSelected(v.id)
    } catch (e: unknown) {
      setError(errorDetail(e))
    } finally { setBusy(false) }
  }

  async function decide(action: 'submit' | 'approve' | 'reject') {
    if (!detail) return
    if (action === 'reject' && comment.trim().length < 3) { setError(t('demand_plan.reject_needs_reason')); return }
    const ok = await confirm({
      title: t(`demand_plan.${action}_confirm_title`, { name: detail.name }),
      message: t(`demand_plan.${action}_confirm_message`),
      confirmLabel: t(`demand_plan.${action}`),
      cancelLabel: t('common.cancel'),
      danger: action === 'reject',
    })
    if (!ok) return
    setBusy(true); setError(null); setNotice(null)
    try {
      await decideDemandPlan(detail.id, action, comment.trim() || undefined)
      setComment(''); load(); loadDetail(detail.id)
    } catch (e: unknown) {
      setError(errorDetail(e))
    } finally { setBusy(false) }
  }

  async function sendComment() {
    if (!detail || !comment.trim()) return
    setBusy(true); setError(null)
    try {
      await commentDemandPlan(detail.id, comment.trim())
      setComment(''); loadDetail(detail.id)
    } catch (e: unknown) {
      setError(errorDetail(e))
    } finally { setBusy(false) }
  }

  async function runDiff() {
    if (!diffA || !diffB) return
    setBusy(true); setError(null)
    try { setDiff(await diffDemandPlans(diffA, diffB)) }
    catch (e: unknown) { setError(errorDetail(e)) }
    finally { setBusy(false) }
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

  const chip = (s: DemandPlanStatus) => (
    <span style={{ fontSize: 10.5, fontWeight: 700, color: STATUS_COLOR[s], border: `1px solid ${STATUS_COLOR[s]}`,
      borderRadius: 6, padding: '1px 7px', whiteSpace: 'nowrap' }}>
      {t(`demand_plan.status_${s}`)}
    </span>
  )

  const eventText = (e: DemandPlanEvent) => {
    const name = e.actor_name || '—'
    if (e.kind === 'comment') return t('demand_plan.event_comment', { name })
    return t(`demand_plan.event_${e.to_status}`, { name })
  }

  const items = list?.items ?? []
  const submittedByMe = !!detail && detail.submitted_by === me?.id
  const selfBlocked = submittedByMe && (detail?.approver_count ?? 0) > 1
  const byPeriod = detail?.by_period ?? []
  const showAccuracy = detail && (detail.status === 'approved' || detail.status === 'superseded')

  const verdictText = useMemo(() => {
    const agg = accuracy?.aggregate
    if (!agg) return null
    if (agg.verdict === 'improved') return t('demand_plan.verdict_improved', { pct: (agg.improvement_pct ?? 0).toFixed(1) })
    if (agg.verdict === 'too_little') return t('demand_plan.verdict_too_little', { n: agg.n_points })
    return t(`demand_plan.verdict_${agg.verdict}`)
  }, [accuracy, t])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <p style={{ margin: 0, fontSize: 12.5, color: C.dim, lineHeight: 1.5 }}>{t('demand_plan.intro')}</p>
      <div role="note" style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '10px 14px', borderRadius: 8,
        border: `1px solid ${C.border}`, background: C.card, fontSize: 12.5, color: C.text }}>
        <Info size={14} color="var(--accent)" aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
        <span>{t('demand_plan.no_effect')}</span>
      </div>

      {notice && <p role="status" style={{ margin: 0, fontSize: 12.5, color: C.text }}>{notice}</p>}
      {error && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{error}</p>}
      {loadError && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{loadError}</p>}

      {/* Save the current plan */}
      {canWrite ? (
        <Card padding={narrow ? '14px' : '16px 18px'}>
          <div style={{ ...eyebrow, marginBottom: 10 }}>{t('demand_plan.save_title')}</div>
          <div style={{ display: 'grid', gap: 10, gridTemplateColumns: narrow ? '1fr' : 'repeat(auto-fit, minmax(200px, 1fr))' }}>
            <label style={lbl}>{t('demand_plan.field_name')}
              <input style={field} type="text" maxLength={120} value={form.name} placeholder={defaultName()}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
            </label>
            <label style={lbl}>{t('demand_plan.field_horizon')}
              <input style={field} type="number" inputMode="numeric" min={1} max={520} value={form.horizon}
                onChange={e => setForm(f => ({ ...f, horizon: e.target.value }))} />
            </label>
            <label style={{ ...lbl, gridColumn: '1 / -1' }}>{t('demand_plan.field_note')}
              <input style={field} type="text" maxLength={1000} value={form.note}
                onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
            </label>
          </div>
          <div style={{ marginTop: 12 }}>
            <button type="button" disabled={busy} onClick={save} style={btn(true, busy)}>
              <FileCheck2 size={13} aria-hidden="true" /> {busy ? t('common.saving') : t('demand_plan.save')}
            </button>
          </div>
        </Card>
      ) : (
        <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('demand_plan.view_only')}</p>
      )}

      {/* Versions */}
      <section aria-labelledby="dp-versions" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <h2 id="dp-versions" style={{ ...eyebrow, margin: 0 }}>{t('demand_plan.versions_title')}</h2>
        {!list && !loadError && <div style={{ padding: 32, display: 'flex', justifyContent: 'center' }}><Spinner /></div>}
        {list && items.length === 0 && <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t('demand_plan.empty')}</p>}
        {list && list.approver_count > 0 && items.length > 0 && (
          <p style={{ margin: 0, fontSize: 11.5, color: C.dim }}>{t('demand_plan.approvers_hint', { n: list.approver_count })}</p>
        )}
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {items.map(v => (
            <li key={v.id}>
              <Card padding={narrow ? '12px 14px' : '12px 16px'} highlighted={selected === v.id}
                style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ minWidth: 0, flex: '1 1 260px', overflowWrap: 'anywhere' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 13.5, fontWeight: 650, color: C.text }}>
                    <span>{v.name}</span>{chip(v.status)}
                  </div>
                  <div style={{ fontSize: 12, color: C.dim, marginTop: 3 }}>
                    {t('demand_plan.meta_line', { periods: v.horizon_periods, first: df(v.first_period), last: df(v.last_period), skus: v.sku_count })}
                  </div>
                  <div style={{ fontSize: 12, color: C.dim, marginTop: 1 }}>
                    {t('demand_plan.created_by', { name: v.created_by_name || '—', date: df(v.created_at) })}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'baseline', fontSize: 12, color: C.muted }}>
                  <span><strong style={{ color: C.text, fontSize: 14 }}>{nf(v.totals.plan)}</strong> {t('demand_plan.total_plan')}</span>
                  <button type="button" style={btn()} onClick={() => setSelected(selected === v.id ? null : v.id)}
                    aria-expanded={selected === v.id}>
                    {selected === v.id ? t('demand_plan.close') : t('demand_plan.open')}
                  </button>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      </section>

      {/* Detail of the selected version */}
      {selected && !detail && <div style={{ padding: 24, display: 'flex', justifyContent: 'center' }}><Spinner /></div>}
      {detail && (
        <Card padding={narrow ? '14px' : '18px 20px'} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: C.text, overflowWrap: 'anywhere' }}>
              {t('demand_plan.detail_title', { name: detail.name })}
            </h2>
            {chip(detail.status)}
          </div>
          {detail.note && <p style={{ margin: 0, fontSize: 12.5, color: C.muted }}>{detail.note}</p>}

          <div style={{ display: 'grid', gap: 8, gridTemplateColumns: narrow ? 'repeat(2, minmax(0, 1fr))' : 'repeat(4, minmax(0, 1fr))' }}>
            {([['total_forecast', detail.totals.forecast], ['total_adjustment', detail.totals.adjustment],
               ['total_committed', detail.totals.committed], ['total_plan', detail.totals.plan]] as const).map(([k, v]) => (
              <div key={k} style={{ padding: '10px 12px', background: C.card, borderRadius: 8, minWidth: 0 }}>
                <div style={{ fontSize: 18, fontWeight: 750, color: k === 'total_plan' ? C.accent : C.text }}>{nf(v)}</div>
                <div style={{ fontSize: 11.5, color: C.muted }}>{t(`demand_plan.${k}`)}</div>
              </div>
            ))}
          </div>

          {/* Plan by period */}
          {byPeriod.length > 0 && (
            <div>
              <div style={{ ...eyebrow, marginBottom: 6 }}>{t('demand_plan.by_period_title')}</div>
              <div style={{ overflowX: 'auto', border: `1px solid ${C.border}`, borderRadius: 8 }}>
                <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: '100%' }}>
                  <thead>
                    <tr>
                      {['col_period', 'total_forecast', 'total_adjustment', 'total_committed', 'total_plan'].map(k => (
                        <th key={k} scope="col" style={{ textAlign: k === 'col_period' ? 'left' : 'right', padding: '6px 10px', color: C.muted, fontWeight: 600, whiteSpace: 'nowrap', borderBottom: `1px solid ${C.border}` }}>
                          {t(`demand_plan.${k}`)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {byPeriod.map(p => (
                      <tr key={p.period}>
                        <td style={{ padding: '5px 10px', whiteSpace: 'nowrap', color: C.text }}>{df(p.period)}</td>
                        <td style={{ padding: '5px 10px', textAlign: 'right' }}>{nf(p.forecast)}</td>
                        <td style={{ padding: '5px 10px', textAlign: 'right' }}>{nf(p.adjustment)}</td>
                        <td style={{ padding: '5px 10px', textAlign: 'right' }}>{nf(p.committed)}</td>
                        <td style={{ padding: '5px 10px', textAlign: 'right', fontWeight: 650 }}>{nf(p.plan)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Products */}
          <div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', marginBottom: 6 }}>
              <div style={eyebrow}>{t('demand_plan.lines_title')}</div>
              <input style={{ ...field, width: narrow ? '100%' : 220 }} type="search" maxLength={200} value={search}
                placeholder={t('demand_plan.search_sku')} aria-label={t('demand_plan.search_sku')}
                onChange={e => setSearch(e.target.value)} />
            </div>
            {lines && (
              <>
                <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column' }}>
                  {lines.items.map(l => (
                    <li key={l.sku} style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 14px', padding: '7px 2px', borderBottom: `1px solid ${C.border}`, fontSize: 12.5, alignItems: 'baseline' }}>
                      <span style={{ fontWeight: 600, color: C.text, flex: '1 1 160px', overflowWrap: 'anywhere' }}>
                        {l.sku}
                        {!l.has_forecast && <span style={{ marginLeft: 6, fontSize: 10.5, color: C.amber }}>{t('demand_plan.no_forecast_badge')}</span>}
                      </span>
                      <span style={{ color: C.dim }}>{t('demand_plan.total_forecast')} {nf(l.forecast)}</span>
                      <span style={{ color: C.dim }}>{t('demand_plan.total_adjustment')} {nf(l.adjustment)}</span>
                      <span style={{ color: C.dim }}>{t('demand_plan.total_committed')} {nf(l.committed)}</span>
                      <span style={{ fontWeight: 650, color: C.text }}>{t('demand_plan.total_plan')} {nf(l.plan)}</span>
                    </li>
                  ))}
                </ul>
                {lines.total > lines.items.length && (
                  <p style={{ margin: '6px 0 0', fontSize: 11.5, color: C.dim }}>
                    {t('demand_plan.lines_more', { shown: lines.items.length, total: lines.total })}
                  </p>
                )}
              </>
            )}
          </div>

          {/* Accuracy of an approved (or superseded) version */}
          {showAccuracy && (
            <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: 12 }}>
              <div style={{ ...eyebrow, display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                <Target size={12} aria-hidden="true" /> {t('demand_plan.accuracy_title')}
              </div>
              <p style={{ margin: '0 0 8px', fontSize: 12, color: C.dim }}>{t('demand_plan.accuracy_intro')}</p>
              {accuracyLoading && <Spinner />}
              {accuracy && accuracy.status === 'periods_not_passed' && (
                <p style={{ margin: 0, fontSize: 12.5, color: C.muted }}>{t('demand_plan.accuracy_not_passed', { date: df(accuracy.first_period_end) })}</p>
              )}
              {accuracy && accuracy.status === 'no_actuals' && (
                <p style={{ margin: 0, fontSize: 12.5, color: C.muted }}>{t('demand_plan.accuracy_no_actuals', { n: accuracy.periods_passed })}</p>
              )}
              {accuracy && accuracy.status === 'no_data_yet' && (
                <p style={{ margin: 0, fontSize: 12.5, color: C.muted }}>{t('demand_plan.accuracy_no_data')}</p>
              )}
              {accuracy && !['ok', 'periods_not_passed', 'no_actuals', 'no_data_yet'].includes(accuracy.status) && (
                <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.amber }}>{t('demand_plan.accuracy_error')}</p>
              )}
              {accuracy && accuracy.status === 'ok' && accuracy.aggregate && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div style={{ display: 'grid', gap: 8, gridTemplateColumns: narrow ? 'repeat(2, minmax(0, 1fr))' : 'repeat(4, minmax(0, 1fr))' }}>
                    {([['plan_error_label', pf(accuracy.aggregate.plan_wape)], ['model_error_label', pf(accuracy.aggregate.model_wape)],
                       ['plan_bias_label', pf(accuracy.aggregate.plan_bias, true)], ['model_bias_label', pf(accuracy.aggregate.model_bias, true)]] as const).map(([k, v]) => (
                      <div key={k} style={{ padding: '10px 12px', background: C.card, borderRadius: 8, minWidth: 0 }}>
                        <div style={{ fontSize: 18, fontWeight: 750, color: C.text }}>{v}</div>
                        <div style={{ fontSize: 11.5, color: C.muted }}>{t(`demand_plan.${k}`)}</div>
                      </div>
                    ))}
                  </div>
                  <p style={{ margin: 0, fontSize: 13, fontWeight: 600, color:
                    accuracy.aggregate.verdict === 'improved' ? C.green : accuracy.aggregate.verdict === 'worsened' ? C.red : C.text }}>
                    {verdictText}
                  </p>
                  <p style={{ margin: 0, fontSize: 11.5, color: C.dim }}>
                    {t('demand_plan.accuracy_basis', { points: accuracy.aggregate.n_points, periods: accuracy.periods_compared, source: accuracy.source?.name ?? '—' })}
                  </p>
                  {accuracy.skipped_no_actual > 0 && (
                    <p style={{ margin: 0, fontSize: 11.5, color: C.dim }}>{t('demand_plan.accuracy_skipped', { n: accuracy.skipped_no_actual })}</p>
                  )}
                  {accuracy.skipped_no_forecast > 0 && (
                    <p style={{ margin: 0, fontSize: 11.5, color: C.dim }}>{t('demand_plan.accuracy_skipped_commitments', { n: accuracy.skipped_no_forecast })}</p>
                  )}
                  {accuracy.by_sku.length > 0 && (
                    <div>
                      <div style={{ ...eyebrow, margin: '6px 0 4px' }}>{t('demand_plan.worst_skus')}</div>
                      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                        {accuracy.by_sku.slice(0, 10).map(r => (
                          <li key={r.sku} style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 14px', padding: '6px 2px', borderBottom: `1px solid ${C.border}`, fontSize: 12.5 }}>
                            <span style={{ fontWeight: 600, color: C.text, flex: '1 1 160px', overflowWrap: 'anywhere' }}>{r.sku}</span>
                            <span style={{ color: C.dim }}>{t('demand_plan.col_plan_wape')} {pf(r.plan_wape)}</span>
                            <span style={{ color: C.dim }}>{t('demand_plan.col_model_wape')} {pf(r.model_wape)}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* History */}
          <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: 12 }}>
            <div style={{ ...eyebrow, marginBottom: 6 }}>{t('demand_plan.history_title')}</div>
            <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(detail.events ?? []).map(e => (
                <li key={e.id} style={{ fontSize: 12.5, color: C.text, display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  {e.kind === 'comment'
                    ? <MessageSquare size={12} color="var(--muted)" aria-hidden="true" style={{ marginTop: 3, flexShrink: 0 }} />
                    : <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 4, marginTop: 5, flexShrink: 0, background: STATUS_COLOR[e.to_status ?? 'draft'] }} />}
                  <div style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
                    <span>{eventText(e)}</span>
                    <span style={{ color: C.dim }}> · {df(e.created_at)}</span>
                    {e.details?.self_approved && <div style={{ fontSize: 11.5, color: C.amber }}>{t('demand_plan.self_approved')}</div>}
                    {e.comment && <div style={{ fontSize: 12, color: C.muted }}>«{e.comment}»</div>}
                  </div>
                </li>
              ))}
            </ol>
          </div>

          {/* Sign-off and comments */}
          {canWrite && (
            <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <label style={lbl}>{t('demand_plan.comment_label')}
                <textarea style={{ ...field, minHeight: 64, resize: 'vertical' }} maxLength={1000} value={comment}
                  placeholder={t('demand_plan.comment_placeholder')} onChange={e => setComment(e.target.value)} />
              </label>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button type="button" disabled={busy || !comment.trim()} onClick={sendComment} style={btn(false, busy || !comment.trim())}>
                  <MessageSquare size={12} aria-hidden="true" /> {t('demand_plan.add_comment')}
                </button>
                {detail.status === 'draft' && (
                  <button type="button" disabled={busy} onClick={() => decide('submit')} style={btn(true, busy)}>
                    {t('demand_plan.submit')}
                  </button>
                )}
                {detail.status === 'submitted' && detail.can_approve && (
                  <>
                    <button type="button" disabled={busy || selfBlocked} onClick={() => decide('approve')}
                      style={btn(true, busy || selfBlocked, C.green)}>
                      {t('demand_plan.approve')}
                    </button>
                    <button type="button" disabled={busy} onClick={() => decide('reject')} style={btn(false, busy, C.red)}>
                      {t('demand_plan.reject')}
                    </button>
                  </>
                )}
              </div>
              {detail.status === 'submitted' && detail.can_approve && selfBlocked && (
                <p style={{ margin: 0, fontSize: 11.5, color: C.amber }}>{t('demand_plan.cannot_self_approve')}</p>
              )}
              {detail.status === 'submitted' && !detail.can_approve && (
                <p style={{ margin: 0, fontSize: 11.5, color: C.dim }}>{t('demand_plan.no_approver_rights')}</p>
              )}
            </div>
          )}
        </Card>
      )}

      {/* Compare two versions */}
      <Card padding={narrow ? '14px' : '16px 18px'} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ ...eyebrow, display: 'flex', alignItems: 'center', gap: 6 }}>
          <GitCompare size={12} aria-hidden="true" /> {t('demand_plan.compare_title')}
        </div>
        {items.length < 2 ? (
          <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('demand_plan.compare_need_two')}</p>
        ) : (
          <>
            <div style={{ display: 'grid', gap: 10, gridTemplateColumns: narrow ? '1fr' : '1fr 1fr auto', alignItems: 'end' }}>
              <label style={lbl}>{t('demand_plan.compare_a')}
                <select style={field} value={diffA} onChange={e => { setDiffA(e.target.value); setDiff(null) }}>
                  {items.map(v => <option key={v.id} value={v.id}>{v.name} · {t(`demand_plan.status_${v.status}`)}</option>)}
                </select>
              </label>
              <label style={lbl}>{t('demand_plan.compare_b')}
                <select style={field} value={diffB} onChange={e => { setDiffB(e.target.value); setDiff(null) }}>
                  {items.map(v => <option key={v.id} value={v.id}>{v.name} · {t(`demand_plan.status_${v.status}`)}</option>)}
                </select>
              </label>
              <button type="button" disabled={busy || !diffA || !diffB || diffA === diffB} onClick={runDiff}
                style={btn(true, busy || !diffA || !diffB || diffA === diffB)}>
                {t('demand_plan.compare_run')}
              </button>
            </div>
            {diff && diff.status === 'no_common_periods' && (
              <p style={{ margin: 0, fontSize: 12.5, color: C.muted }}>{t('demand_plan.diff_no_common')}</p>
            )}
            {diff && diff.status === 'ok' && (
              <>
                <p style={{ margin: 0, fontSize: 12.5, color: C.text }}>
                  {diff.n_skus_changed === 0
                    ? t('demand_plan.diff_none')
                    : t('demand_plan.diff_summary', { n: diff.n_skus_changed, periods: diff.n_common_periods, a: nf(diff.total_a), b: nf(diff.total_b) })}
                </p>
                <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                  {diff.items.map(r => (
                    <li key={r.sku} style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 14px', padding: '7px 2px', borderBottom: `1px solid ${C.border}`, fontSize: 12.5, alignItems: 'baseline' }}>
                      <span style={{ fontWeight: 600, color: C.text, flex: '1 1 160px', overflowWrap: 'anywhere' }}>
                        {r.sku}
                        {r.only_in && <span style={{ marginLeft: 6, fontSize: 10.5, color: C.amber }}>{t(`demand_plan.only_in_${r.only_in}`)}</span>}
                      </span>
                      <span style={{ color: C.dim }}>{t('demand_plan.col_plan_a')} {nf(r.plan_a)}</span>
                      <span style={{ color: C.dim }}>{t('demand_plan.col_plan_b')} {nf(r.plan_b)}</span>
                      <span style={{ fontWeight: 650, color: r.change > 0 ? C.green : C.red }}>
                        {r.change > 0 ? '+' : ''}{nf(r.change)}{r.change_pct != null ? ` (${r.change_pct > 0 ? '+' : ''}${r.change_pct.toFixed(1)}%)` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </Card>
    </div>
  )
}
