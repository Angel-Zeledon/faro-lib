'use client'
/**
 * What-if scenario builder + comparison (PENDIENTES #7).
 *
 * The user assembles typed rules, simulates them against the session's forecast
 * and reads BASE vs SCENARIO side by side. Nothing is computed in the browser:
 * every number comes from `/sessions/{id}/scenarios/preview|run`, which reuses
 * the same semáforo the rest of the app shows.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { FlaskConical, Plus, Play, Save, Trash2, X } from 'lucide-react'
import {
  createScenario, deleteScenario, listScenarios, previewScenario, runScenario,
} from '@/lib/api'
import type {
  Scenario, ScenarioChangeRow, ScenarioRule, ScenarioRuleType, ScenarioRunResult,
} from '@/lib/types'
import { useAutoSession } from '@/hooks/useAutoSession'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/States'
import SignalBadge from '@/components/ui/SignalBadge'
import { getUser } from '@/lib/auth'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', muted: 'var(--muted)',
  accent: 'var(--accent)',
}

const RULE_TYPES: ScenarioRuleType[] = [
  'demand_multiplier', 'promo', 'supplier_delay', 'safety_stock',
]

/** Fresh rule of a given type, pre-filled with the most common answer. */
function blankRule(type: ScenarioRuleType): ScenarioRule {
  switch (type) {
    case 'demand_multiplier': return { type, multiplier: 1.4 }
    case 'promo':             return { type, multiplier: 1.5, date_from: '', date_to: '' }
    case 'supplier_delay':    return { type, extra_days: 7 }
    case 'safety_stock':      return { type, service_level: 0.99 }
  }
}

/** Strip the empty optional fields so the backend never sees `sku: ''`. */
function cleanRule(rule: ScenarioRule): ScenarioRule {
  const out: ScenarioRule = { type: rule.type }
  if (rule.multiplier    !== undefined) out.multiplier    = rule.multiplier
  if (rule.extra_days    !== undefined) out.extra_days    = rule.extra_days
  if (rule.service_level !== undefined) out.service_level = rule.service_level
  if (rule.sku?.trim())       out.sku       = rule.sku.trim()
  if (rule.category?.trim())  out.category  = rule.category.trim()
  if (rule.supplier?.trim())  out.supplier  = rule.supplier.trim()
  if (rule.date_from)         out.date_from = rule.date_from
  if (rule.date_to)           out.date_to   = rule.date_to
  return out
}

const fmtNum = (n: number) =>
  new Intl.NumberFormat('es', { maximumFractionDigits: 0 }).format(n)
const fmtMoney = (n: number) =>
  new Intl.NumberFormat('es', { maximumFractionDigits: 0 }).format(n)
const fmtDelta = (n: number, money = false) => {
  const body = money ? `$${fmtMoney(Math.abs(n))}` : fmtNum(Math.abs(n))
  if (n === 0) return '—'
  return `${n > 0 ? '+' : '−'}${body}`
}
/** More units / more urgency reads as a warning, less as a relief. */
const deltaColor = (n: number) =>
  n === 0 ? C.dim : n > 0 ? 'var(--signal-order-now-fg)' : 'var(--signal-ok-fg)'

const inputStyle: React.CSSProperties = {
  background: 'var(--bg)', border: `1px solid ${C.border}`, borderRadius: 7,
  padding: '6px 9px', fontSize: 12, color: C.text, width: '100%',
}
const labelStyle: React.CSSProperties = {
  fontSize: 10, fontWeight: 700, color: C.dim,
  textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 4,
  display: 'block',
}
const cardStyle: React.CSSProperties = {
  background: C.surface, border: `1px solid ${C.border}`,
  borderRadius: 12, padding: 18,
}
const btnStyle: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center',
  gap: 7, padding: '8px 14px', borderRadius: 8, fontSize: 13, fontWeight: 600,
}

// ── Rule editor ──────────────────────────────────────────────────────────────

function RuleEditor({ rule, onChange, onRemove }: {
  rule: ScenarioRule
  onChange: (r: ScenarioRule) => void
  onRemove: () => void
}) {
  const { t } = useLanguage()
  const set = (patch: Partial<ScenarioRule>) => onChange({ ...rule, ...patch })
  const isDemand = rule.type === 'demand_multiplier' || rule.type === 'promo'

  return (
    <div style={{
      border: `1px solid ${C.border}`, borderRadius: 10, padding: 12,
      marginBottom: 10, background: 'var(--bg)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <select
          value={rule.type}
          onChange={e => onChange(blankRule(e.target.value as ScenarioRuleType))}
          aria-label={t('scenarios.rule_type')}
          style={{ ...inputStyle, width: 'auto', fontWeight: 600 }}
        >
          {RULE_TYPES.map(type => (
            <option key={type} value={type}>{t(`scenarios.type_${type}`)}</option>
          ))}
        </select>
        <span style={{ fontSize: 11, color: C.dim, flex: 1 }}>
          {t(`scenarios.help_${rule.type}`)}
        </span>
        <button
          onClick={onRemove}
          title={t('scenarios.remove_rule')}
          aria-label={t('scenarios.remove_rule')}
          style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex' }}
        >
          <X size={15} />
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10 }}>
        {isDemand && (
          <>
            <div>
              <label style={labelStyle} htmlFor={`mult-${rule.type}`}>{t('scenarios.field_multiplier')}</label>
              <input
                id={`mult-${rule.type}`} type="number" step="0.05" min="0.01" max="10"
                value={rule.multiplier ?? ''}
                onChange={e => set({ multiplier: Number(e.target.value) })}
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>{t('scenarios.field_sku')}</label>
              <input
                value={rule.sku ?? ''} placeholder={t('scenarios.scope_all')}
                onChange={e => set({ sku: e.target.value, category: '' })}
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>{t('scenarios.field_category')}</label>
              <input
                value={rule.category ?? ''} placeholder={t('scenarios.scope_all')}
                onChange={e => set({ category: e.target.value, sku: '' })}
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>{t('scenarios.field_date_from')}</label>
              <input
                type="date" value={rule.date_from ?? ''}
                onChange={e => set({ date_from: e.target.value })}
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>{t('scenarios.field_date_to')}</label>
              <input
                type="date" value={rule.date_to ?? ''}
                onChange={e => set({ date_to: e.target.value })}
                style={inputStyle}
              />
            </div>
          </>
        )}

        {rule.type === 'supplier_delay' && (
          <>
            <div>
              <label style={labelStyle}>{t('scenarios.field_extra_days')}</label>
              <input
                type="number" min="0" max="365" step="1"
                value={rule.extra_days ?? ''}
                onChange={e => set({ extra_days: Number(e.target.value) })}
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>{t('scenarios.field_supplier')}</label>
              <input
                value={rule.supplier ?? ''} placeholder={t('scenarios.scope_all')}
                onChange={e => set({ supplier: e.target.value })}
                style={inputStyle}
              />
            </div>
          </>
        )}

        {rule.type === 'safety_stock' && (
          <div>
            <label style={labelStyle}>{t('scenarios.field_service_level')}</label>
            <select
              value={String(rule.service_level ?? 0.95)}
              onChange={e => set({ service_level: Number(e.target.value) })}
              style={inputStyle}
            >
              {[0.90, 0.95, 0.97, 0.99].map(level => (
                <option key={level} value={level}>{Math.round(level * 100)}%</option>
              ))}
            </select>
          </div>
        )}
      </div>

      {isDemand && (
        <div style={{ fontSize: 11, color: C.dim, marginTop: 8 }}>
          {t('scenarios.scope_hint')}
        </div>
      )}
    </div>
  )
}

// ── Comparison ───────────────────────────────────────────────────────────────

function CompareTable({ result }: { result: ScenarioRunResult }) {
  const { t } = useLanguage()
  const rows: { labelKey: string; key: keyof ScenarioRunResult['delta']; money?: boolean }[] = [
    { labelKey: 'scenarios.metric_units',      key: 'total_units_to_order' },
    { labelKey: 'scenarios.metric_value',      key: 'estimated_purchase_value', money: true },
    { labelKey: 'scenarios.metric_skus',       key: 'skus_to_order' },
    { labelKey: 'scenarios.metric_order_now',  key: 'order_now' },
    { labelKey: 'scenarios.metric_order_soon', key: 'order_soon' },
    { labelKey: 'scenarios.metric_overstock',  key: 'overstock' },
  ]
  const th: React.CSSProperties = {
    textAlign: 'right', padding: '8px 10px', fontSize: 11, fontWeight: 700,
    color: C.dim, textTransform: 'uppercase', letterSpacing: '0.05em',
  }
  const td: React.CSSProperties = {
    textAlign: 'right', padding: '9px 10px', fontSize: 13, color: C.text,
    borderTop: `1px solid ${C.border}`,
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 420 }}>
        <thead>
          <tr>
            <th style={{ ...th, textAlign: 'left' }} />
            <th style={th}>{t('scenarios.col_base')}</th>
            <th style={th}>{t('scenarios.col_scenario')}</th>
            <th style={th}>{t('scenarios.col_delta')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ labelKey, key, money }) => {
            const base = result.base[key]
            const scenario = result.scenario[key]
            const delta = result.delta[key]
            return (
              <tr key={key}>
                <td style={{ ...td, textAlign: 'left', color: C.muted }}>{t(labelKey)}</td>
                <td style={td}>{money ? `$${fmtMoney(base)}` : fmtNum(base)}</td>
                <td style={{ ...td, fontWeight: 600 }}>
                  {money ? `$${fmtMoney(scenario)}` : fmtNum(scenario)}
                </td>
                <td style={{ ...td, fontWeight: 700, color: deltaColor(delta) }}>
                  {fmtDelta(delta, money)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function ChangesTable({ rows }: { rows: ScenarioChangeRow[] }) {
  const { t } = useLanguage()
  const th: React.CSSProperties = {
    textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 700,
    color: C.dim, textTransform: 'uppercase', letterSpacing: '0.05em',
    whiteSpace: 'nowrap',
  }
  const td: React.CSSProperties = {
    padding: '9px 10px', fontSize: 12.5, color: C.text,
    borderTop: `1px solid ${C.border}`, whiteSpace: 'nowrap',
  }
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 720 }}>
        <thead>
          <tr>
            <th style={th}>{t('scenarios.col_sku')}</th>
            <th style={th}>{t('scenarios.col_signal')}</th>
            <th style={{ ...th, textAlign: 'right' }}>{t('scenarios.col_qty')}</th>
            <th style={{ ...th, textAlign: 'right' }}>{t('scenarios.col_delta')}</th>
            <th style={{ ...th, textAlign: 'right' }}>{t('scenarios.col_demand')}</th>
            <th style={{ ...th, textAlign: 'right' }}>{t('scenarios.col_lead_time')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.sku}>
              <td style={td}>
                <div style={{ fontWeight: 600 }}>{row.display_name || row.sku}</div>
                {row.display_name && (
                  <div style={{ fontSize: 11, color: C.dim }}>{row.sku}</div>
                )}
              </td>
              <td style={td}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <SignalBadge signal={row.base_signal} />
                  <span style={{ color: C.dim }}>→</span>
                  <SignalBadge signal={row.scenario_signal} />
                </span>
              </td>
              <td style={{ ...td, textAlign: 'right' }}>
                {fmtNum(row.base_qty)} → <strong>{fmtNum(row.scenario_qty)}</strong>
              </td>
              <td style={{ ...td, textAlign: 'right', fontWeight: 700, color: deltaColor(row.delta_qty) }}>
                {fmtDelta(row.delta_qty)}
              </td>
              <td style={{ ...td, textAlign: 'right', color: C.muted }}>
                {row.base_daily_demand ?? '—'} → {row.scenario_daily_demand ?? '—'}
              </td>
              <td style={{ ...td, textAlign: 'right', color: C.muted }}>
                {row.base_lead_time_days ?? '—'} → {row.scenario_lead_time_days ?? '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function ScenariosPage() {
  const { t }       = useLanguage()
  const { addToast } = useToast()
  const confirm     = useConfirm()
  const user        = getUser()
  const canEdit     = user?.role === 'admin' || user?.role === 'analyst'
  const {
    sessionId, setSessionId, completedSessions,
    loading: sessionsLoading, error: sessionsError,
  } = useAutoSession()

  const [rules,   setRules]   = useState<ScenarioRule[]>([blankRule('demand_multiplier')])
  const [result,  setResult]  = useState<ScenarioRunResult | null>(null)
  const [running, setRunning] = useState(false)
  const [error,   setError]   = useState<unknown>(null)

  const [saved,    setSaved]    = useState<Scenario[]>([])
  const [name,     setName]     = useState('')
  const [saving,   setSaving]   = useState(false)

  const reloadSaved = useCallback((sid: string) => {
    if (!sid) { setSaved([]); return }
    listScenarios(sid, { silent: true }).then(setSaved).catch(() => setSaved([]))
  }, [])

  useEffect(() => { reloadSaved(sessionId) }, [sessionId, reloadSaved])
  // A different session means a different forecast: the old comparison would be
  // stale numbers under a new title.
  useEffect(() => { setResult(null) }, [sessionId])

  const cleanRules = useMemo(() => rules.map(cleanRule), [rules])

  const simulate = async () => {
    if (!sessionId) return
    setRunning(true); setError(null)
    try {
      setResult(await previewScenario(sessionId, cleanRules, { silent: true }))
    } catch (e: unknown) {
      setError(e)
      setResult(null)
    } finally {
      setRunning(false)
    }
  }

  const save = async () => {
    if (!sessionId) return
    if (!name.trim()) { addToast(t('scenarios.save_title'), t('scenarios.name_required'), 'error'); return }
    setSaving(true)
    try {
      await createScenario(sessionId, name.trim(), cleanRules)
      addToast(t('scenarios.saved'), t('scenarios.saved_body'), 'success')
      setName('')
      reloadSaved(sessionId)
    } catch { /* the interceptor already toasted the reason */ }
    finally { setSaving(false) }
  }

  const load = async (scenario: Scenario) => {
    setRules(scenario.rules.length ? scenario.rules : [blankRule('demand_multiplier')])
    setName(scenario.name)
    setRunning(true); setError(null)
    try {
      setResult(await runScenario(scenario.session_id, scenario.id, { silent: true }))
    } catch (e: unknown) {
      setError(e); setResult(null)
    } finally {
      setRunning(false)
    }
  }

  const remove = async (scenario: Scenario) => {
    const okToDelete = await confirm({
      title:   t('scenarios.delete_confirm_title'),
      message: t('scenarios.delete_confirm_body', { name: scenario.name }),
      danger:  true,
    })
    if (!okToDelete) return
    try {
      await deleteScenario(scenario.id)
      addToast(t('scenarios.deleted'), scenario.name, 'success')
      reloadSaved(sessionId)
    } catch { /* interceptor toasts */ }
  }

  if (sessionsError) return <ErrorState error={sessionsError} />
  if (sessionsLoading && !completedSessions.length) return <LoadingState />
  if (!sessionsLoading && !completedSessions.length) {
    return (
      <EmptyState
        icon={<FlaskConical size={22} />}
        title={t('scenarios.title')}
        body={t('scenarios.no_sessions')}
      />
    )
  }

  return (
    <div style={{ padding: '24px 28px', maxWidth: 1240, margin: '0 auto' }}>
      {/* Header + session picker */}
      <div style={{
        display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
        gap: 16, flexWrap: 'wrap', marginBottom: 20,
      }}>
        <div>
          <h1 style={{
            fontSize: 22, fontWeight: 700, color: C.text, margin: 0,
            display: 'flex', alignItems: 'center', gap: 9,
          }}>
            <FlaskConical size={19} color={C.accent} />
            {t('scenarios.title')}
          </h1>
          <p style={{ fontSize: 13, color: C.dim, margin: '6px 0 0', maxWidth: 640 }}>
            {t('scenarios.subtitle')}
          </p>
        </div>
        <div>
          <label style={labelStyle} htmlFor="scenario-session">{t('scenarios.session_label')}</label>
          <select
            id="scenario-session"
            value={sessionId}
            onChange={e => setSessionId(e.target.value)}
            style={{ ...inputStyle, width: 'auto', minWidth: 220 }}
          >
            {completedSessions.map(s => (
              <option key={s.session_id} value={s.session_id}>{s.name}</option>
            ))}
          </select>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 420px) 1fr', gap: 18, alignItems: 'start' }}>
        {/* Builder */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div style={cardStyle}>
            <h2 style={{ fontSize: 14, fontWeight: 700, color: C.text, margin: '0 0 12px' }}>
              {t('scenarios.builder_title')}
            </h2>

            {rules.length === 0 && (
              <p style={{ fontSize: 12.5, color: C.dim, margin: '0 0 12px' }}>
                {t('scenarios.builder_empty')}
              </p>
            )}

            {rules.map((rule, i) => (
              <RuleEditor
                key={i}
                rule={rule}
                onChange={next => setRules(rules.map((r, j) => (j === i ? next : r)))}
                onRemove={() => setRules(rules.filter((_, j) => j !== i))}
              />
            ))}

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
              <button
                onClick={() => setRules([...rules, blankRule('demand_multiplier')])}
                style={{ ...btnStyle, border: `1px solid ${C.border}`, color: C.muted }}
              >
                <Plus size={14} /> {t('scenarios.add_rule')}
              </button>
              <button
                onClick={simulate}
                disabled={running || !sessionId}
                style={{
                  ...btnStyle, background: C.accent, color: '#fff',
                  opacity: running || !sessionId ? 0.6 : 1,
                  cursor: running || !sessionId ? 'not-allowed' : 'pointer',
                }}
              >
                <Play size={14} />
                {running ? t('scenarios.running') : result ? t('scenarios.rerun') : t('scenarios.run')}
              </button>
            </div>
          </div>

          {/* Save */}
          {canEdit && (
            <div style={cardStyle}>
              <h2 style={{ fontSize: 14, fontWeight: 700, color: C.text, margin: '0 0 12px' }}>
                {t('scenarios.save_title')}
              </h2>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  value={name}
                  onChange={e => setName(e.target.value)}
                  placeholder={t('scenarios.save_name_placeholder')}
                  aria-label={t('scenarios.save_title')}
                  style={inputStyle}
                />
                <button
                  onClick={save}
                  disabled={saving || !rules.length}
                  style={{
                    ...btnStyle, border: `1px solid ${C.border}`, color: C.text,
                    opacity: saving || !rules.length ? 0.6 : 1,
                    cursor: saving || !rules.length ? 'not-allowed' : 'pointer',
                  }}
                >
                  <Save size={14} /> {saving ? t('scenarios.saving') : t('scenarios.save')}
                </button>
              </div>
              {!rules.length && (
                <p style={{ fontSize: 11.5, color: C.dim, margin: '8px 0 0' }}>
                  {t('scenarios.no_rules_hint')}
                </p>
              )}
            </div>
          )}

          {/* Saved list */}
          <div style={cardStyle}>
            <h2 style={{ fontSize: 14, fontWeight: 700, color: C.text, margin: '0 0 12px' }}>
              {t('scenarios.saved_list_title')}
            </h2>
            {saved.length === 0 ? (
              <p style={{ fontSize: 12.5, color: C.dim, margin: 0 }}>
                {t('scenarios.saved_list_empty')}
              </p>
            ) : saved.map(scenario => (
              <div
                key={scenario.id}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '8px 0', borderTop: `1px solid ${C.border}`,
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: 13, fontWeight: 600, color: C.text,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {scenario.name}
                  </div>
                  <div style={{ fontSize: 11, color: C.dim }}>
                    {t('scenarios.rules_count', { n: scenario.rules.length })}
                  </div>
                </div>
                <button
                  onClick={() => load(scenario)}
                  style={{ ...btnStyle, padding: '5px 10px', fontSize: 12, border: `1px solid ${C.border}`, color: C.muted }}
                >
                  {t('scenarios.load')}
                </button>
                {canEdit && (
                  <button
                    onClick={() => remove(scenario)}
                    title={t('scenarios.delete')}
                    aria-label={t('scenarios.delete')}
                    style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 4 }}
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Comparison */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {error ? (
            <ErrorState error={error} onRetry={simulate} />
          ) : running && !result ? (
            <LoadingState label={t('scenarios.running')} />
          ) : !result ? (
            <EmptyState
              icon={<FlaskConical size={22} />}
              title={t('scenarios.compare_title')}
              body={t('scenarios.builder_empty')}
            />
          ) : (
            <>
              <div style={cardStyle}>
                <div style={{
                  display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
                  gap: 10, marginBottom: 8, flexWrap: 'wrap',
                }}>
                  <h2 style={{ fontSize: 14, fontWeight: 700, color: C.text, margin: 0 }}>
                    {t('scenarios.compare_title')}
                  </h2>
                  <span style={{ fontSize: 11.5, color: C.dim }}>
                    {t('scenarios.series_adjusted', { n: result.applied.series_adjusted })}
                  </span>
                </div>
                <CompareTable result={result} />
              </div>

              <div style={cardStyle}>
                <h2 style={{ fontSize: 14, fontWeight: 700, color: C.text, margin: '0 0 8px' }}>
                  {t('scenarios.changes_title')}
                </h2>
                {result.changes.length === 0 ? (
                  <p style={{ fontSize: 12.5, color: C.dim, margin: 0 }}>
                    {t('scenarios.changes_empty')}
                  </p>
                ) : (
                  <ChangesTable rows={result.changes} />
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
