'use client'
/**
 * What-if scenario builder + comparison (PENDIENTES #7).
 *
 * The user assembles typed rules, simulates them against the session's forecast
 * and reads BASE vs SCENARIO side by side. Nothing is computed in the browser:
 * every number comes from `/sessions/{id}/scenarios/preview|run`, which reuses
 * the same semáforo the rest of the app shows.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FlaskConical, Plus, Play, Save, Trash2, X, ChevronLeft } from 'lucide-react'
import {
  createScenario, deleteScenario, listScenarios, previewScenario, runScenario,
} from '@/lib/api'
import { formatMoney } from '@/lib/currency'
import type {
  Scenario, ScenarioChangeRow, ScenarioRule, ScenarioRuleType, ScenarioRunResult, InventorySignal,
} from '@/lib/types'
import { useAutoSession } from '@/hooks/useAutoSession'
import Card from '@/components/ui/Card'
import Table, { Th, Td } from '@/components/ui/Table'
import Input, { FieldLabel, Select } from '@/components/ui/Input'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/States'
import SignalBadge from '@/components/ui/SignalBadge'
import { getUser } from '@/lib/auth'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import {
  BottomSheet, MobileCard, MobileList, MobileSection, StickyActionBar, signalTone,
} from '@/components/mobile'
import { SIGNAL_STYLES } from '@/components/ui/SignalBadge'

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
// The tenant's currency, like every other money figure in the app. This page
// used to hardcode '$', so a Costa Rican buyer comparing scenarios read
// "$1986" for an amount the inventory screen showed as ₡1 986 — the same
// number, off by an exchange rate, in the panel meant to justify a purchase.
const fmtMoney = (n: number) => formatMoney(n)
/** Daily demand to one decimal — a buyer cannot act on the fourth. */
const fmtDemand = (n: number | null | undefined) =>
  n == null ? '—' : new Intl.NumberFormat('es', { maximumFractionDigits: 1 }).format(n)
const fmtDelta = (n: number, money = false) => {
  const body = money ? fmtMoney(Math.abs(n)) : fmtNum(Math.abs(n))
  if (n === 0) return '—'
  return `${n > 0 ? '+' : '−'}${body}`
}
/** More units / more urgency reads as a warning, less as a relief. */
const deltaColor = (n: number) =>
  n === 0 ? C.dim : n > 0 ? 'var(--signal-order-now-fg)' : 'var(--signal-ok-fg)'

const btnStyle: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center',
  gap: 7, padding: '8px 14px', borderRadius: 8, fontSize: 13, fontWeight: 600,
}

// ── Rule editor ──────────────────────────────────────────────────────────────

function RuleEditor({ rule, onChange, onRemove, tourAnchor }: {
  rule: ScenarioRule
  onChange: (r: ScenarioRule) => void
  onRemove: () => void
  /** Set on the first row only — a tour anchor has to be unique in the DOM. */
  tourAnchor?: string
}) {
  const { t } = useLanguage()
  const set = (patch: Partial<ScenarioRule>) => onChange({ ...rule, ...patch })
  const isDemand = rule.type === 'demand_multiplier' || rule.type === 'promo'

  return (
    <Card data-tour={tourAnchor} radius={10} padding={12} style={{ marginBottom: 10, background: 'var(--bg)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <Select
          value={rule.type}
          onChange={e => onChange(blankRule(e.target.value as ScenarioRuleType))}
          aria-label={t('scenarios.rule_type')}
          size="sm" tone="bg"
          style={{ width: 'auto', fontWeight: 600 }}
        >
          {RULE_TYPES.map(type => (
            <option key={type} value={type}>{t(`scenarios.type_${type}`)}</option>
          ))}
        </Select>
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
            <div data-tour={tourAnchor ? 'sc.multiplier' : undefined}>
              <FieldLabel variant="eyebrow" htmlFor={`mult-${rule.type}`}>{t('scenarios.field_multiplier')}</FieldLabel>
              {/* step="any": the browser measures steps FROM `min`, so step
                  0.05 with min 0.01 made the valid ladder 0.01, 0.06, 0.11 …
                  and the app's OWN default of 1.4 came up invalid — the browser
                  offered "1.36 y 1.41". A demand multiplier has no natural
                  increment anyway. */}
              <Input
                id={`mult-${rule.type}`} type="number" step="any" min="0.01" max="10"
                value={rule.multiplier ?? ''}
                onChange={e => set({ multiplier: Number(e.target.value) })}
                size="sm" tone="bg"
              />
            </div>
            <div>
              <FieldLabel variant="eyebrow">{t('scenarios.field_sku')}</FieldLabel>
              <Input
                value={rule.sku ?? ''} placeholder={t('scenarios.scope_all')}
                onChange={e => set({ sku: e.target.value, category: '' })}
                size="sm" tone="bg"
              />
            </div>
            {/* The scope/date anchors sit on the SECOND field of each pair, so the
                tour card — which opens below the highlight — never hides the
                other half of the pair the step is talking about. */}
            <div data-tour={tourAnchor ? 'sc.scope' : undefined}>
              <FieldLabel variant="eyebrow">{t('scenarios.field_category')}</FieldLabel>
              <Input
                value={rule.category ?? ''} placeholder={t('scenarios.scope_all')}
                onChange={e => set({ category: e.target.value, sku: '' })}
                size="sm" tone="bg"
              />
            </div>
            <div>
              <FieldLabel variant="eyebrow">{t('scenarios.field_date_from')}</FieldLabel>
              <Input
                type="date" value={rule.date_from ?? ''}
                onChange={e => set({ date_from: e.target.value })}
                size="sm" tone="bg"
              />
            </div>
            <div data-tour={tourAnchor ? 'sc.dates' : undefined}>
              <FieldLabel variant="eyebrow">{t('scenarios.field_date_to')}</FieldLabel>
              <Input
                type="date" value={rule.date_to ?? ''}
                onChange={e => set({ date_to: e.target.value })}
                size="sm" tone="bg"
              />
            </div>
          </>
        )}

        {rule.type === 'supplier_delay' && (
          <>
            <div>
              <FieldLabel variant="eyebrow">{t('scenarios.field_extra_days')}</FieldLabel>
              <Input
                type="number" min="0" max="365" step="1"
                value={rule.extra_days ?? ''}
                onChange={e => set({ extra_days: Number(e.target.value) })}
                size="sm" tone="bg"
              />
            </div>
            <div>
              <FieldLabel variant="eyebrow">{t('scenarios.field_supplier')}</FieldLabel>
              <Input
                value={rule.supplier ?? ''} placeholder={t('scenarios.scope_all')}
                onChange={e => set({ supplier: e.target.value })}
                size="sm" tone="bg"
              />
            </div>
          </>
        )}

        {rule.type === 'safety_stock' && (
          <div>
            <FieldLabel variant="eyebrow">{t('scenarios.field_service_level')}</FieldLabel>
            <Select
              value={String(rule.service_level ?? 0.95)}
              onChange={e => set({ service_level: Number(e.target.value) })}
              size="sm" tone="bg"
            >
              {[0.90, 0.95, 0.97, 0.99].map(level => (
                <option key={level} value={level}>{Math.round(level * 100)}%</option>
              ))}
            </Select>
          </div>
        )}
      </div>

      {isDemand && (
        <div style={{ fontSize: 11, color: C.dim, marginTop: 8 }}>
          {t('scenarios.scope_hint')}
        </div>
      )}
    </Card>
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
  return (
    /* Rows are separated by a rule ABOVE, so the header carries no hairline of
       its own — two rules would stack into a 2px line under it. */
    <Table minWidth={420}>
        <thead>
          <tr>
            <Th divider={false} />
            <Th align="right" divider={false}>{t('scenarios.col_base')}</Th>
            <Th align="right" divider={false}>{t('scenarios.col_scenario')}</Th>
            <Th align="right" divider={false}>{t('scenarios.col_delta')}</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ labelKey, key, money }) => {
            const base = result.base[key]
            const scenario = result.scenario[key]
            const delta = result.delta[key]
            return (
              <tr key={key}>
                <Td divider="top" style={{ color: C.muted }}>{t(labelKey)}</Td>
                <Td align="right" divider="top">{money ? fmtMoney(base) : fmtNum(base)}</Td>
                <Td align="right" divider="top" style={{ fontWeight: 600 }}>
                  {money ? fmtMoney(scenario) : fmtNum(scenario)}
                </Td>
                <Td align="right" divider="top" style={{ fontWeight: 700, color: deltaColor(delta) }}>
                  {fmtDelta(delta, money)}
                </Td>
              </tr>
            )
          })}
        </tbody>
    </Table>
  )
}

function ChangesTable({ rows }: { rows: ScenarioChangeRow[] }) {
  const { t } = useLanguage()
  // 12.5px body type is this table's own: six columns of "before to after"
  // pairs need the half pixel back to stay on one line.
  const CELL: React.CSSProperties = { fontSize: 12.5 }
  return (
    <Table minWidth={720}>
        <thead>
          <tr>
            <Th divider={false}>{t('scenarios.col_sku')}</Th>
            <Th divider={false}>{t('scenarios.col_signal')}</Th>
            <Th align="right" divider={false}>{t('scenarios.col_qty')}</Th>
            <Th align="right" divider={false}>{t('scenarios.col_delta')}</Th>
            <Th align="right" divider={false}>{t('scenarios.col_demand')}</Th>
            <Th align="right" divider={false}>{t('scenarios.col_lead_time')}</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.sku}>
              <Td divider="top" nowrap style={CELL}>
                <div style={{ fontWeight: 600 }}>{row.display_name || row.sku}</div>
                {row.display_name && (
                  <div style={{ fontSize: 11, color: C.dim }}>{row.sku}</div>
                )}
              </Td>
              <Td divider="top" nowrap style={CELL}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <SignalBadge signal={row.base_signal} />
                  <span style={{ color: C.dim }}>→</span>
                  <SignalBadge signal={row.scenario_signal} />
                </span>
              </Td>
              <Td align="right" divider="top" nowrap style={CELL}>
                {fmtNum(row.base_qty)} → <strong>{fmtNum(row.scenario_qty)}</strong>
              </Td>
              <Td align="right" divider="top" nowrap style={{ ...CELL, fontWeight: 700, color: deltaColor(row.delta_qty) }}>
                {fmtDelta(row.delta_qty)}
              </Td>
              <Td align="right" divider="top" nowrap style={{ ...CELL, color: C.muted }}>
                {/* One decimal, like every other demand figure in the app. The
                    raw value carries four ("78.8147 → 110.3406"), which sat next
                    to whole-unit quantities in the same row. */}
                {fmtDemand(row.base_daily_demand)} → {fmtDemand(row.scenario_daily_demand)}
              </Td>
              <Td align="right" divider="top" nowrap style={{ ...CELL, color: C.muted }}>
                {row.base_lead_time_days ?? '—'} → {row.scenario_lead_time_days ?? '—'}
              </Td>
            </tr>
          ))}
        </tbody>
    </Table>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function ScenariosPage() {
  const { t }       = useLanguage()
  const { addToast, undoable } = useToast()
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
  const narrow = useIsNarrow()

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

  /** Resolves true when the scenario was stored (the phone's save sheet
   *  closes on it; the desktop ignores the value). */
  const save = async (): Promise<boolean> => {
    if (!sessionId) return false
    if (!name.trim()) { addToast(t('scenarios.save_title'), t('scenarios.name_required'), 'error'); return false }
    setSaving(true)
    try {
      await createScenario(sessionId, name.trim(), cleanRules)
      addToast(t('scenarios.saved'), t('scenarios.saved_body'), 'success')
      setName('')
      reloadSaved(sessionId)
      return true
    } catch { /* the interceptor already toasted the reason */ return false }
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

  // A saved scenario is a handful of parameters the user can retype, and the
  // list is right there — a modal asking "are you sure?" bought nothing. The
  // row goes, the DELETE waits out the undo window, and "Deshacer" simply
  // cancels it.
  const remove = (scenario: Scenario) => {
    const index = saved.findIndex(s => s.id === scenario.id)
    undoable({
      title:     t('scenarios.deleted'),
      message:   scenario.name,
      undoLabel: t('common.undo'),
      apply:  () => setSaved(prev => prev.filter(s => s.id !== scenario.id)),
      revert: () => setSaved(prev => {
        if (prev.some(s => s.id === scenario.id)) return prev
        const next = [...prev]
        next.splice(index < 0 ? next.length : index, 0, scenario)
        return next
      }),
      commit: () => deleteScenario(scenario.id),
      onCommitError: () => { addToast(t('scenarios.delete_failed'), scenario.name, 'error'); reloadSaved(sessionId) },
    })
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

  if (narrow) {
    return (
      <ScenariosMobile
        sessionId={sessionId}
        onSession={setSessionId}
        sessions={completedSessions.map(s => ({ id: s.session_id, name: s.name }))}
        canEdit={canEdit}
        rules={rules}
        onRules={setRules}
        result={result}
        running={running}
        error={error}
        onSimulate={simulate}
        saved={saved}
        name={name}
        onName={setName}
        saving={saving}
        onSave={save}
        onLoad={load}
        onRemove={remove}
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
        <div data-tour="sc.session">
          <FieldLabel variant="eyebrow" htmlFor="scenario-session">{t('scenarios.session_label')}</FieldLabel>
          <Select
            id="scenario-session"
            value={sessionId}
            onChange={e => setSessionId(e.target.value)}
            size="sm" tone="bg"
            style={{ width: 'auto', minWidth: 220 }}
          >
            {completedSessions.map(s => (
              <option key={s.session_id} value={s.session_id}>{s.name}</option>
            ))}
          </Select>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 420px) 1fr', gap: 18, alignItems: 'start' }}>
        {/* Builder */}
        <div data-tour="sc.builder" style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <Card padding={18}>
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
                tourAnchor={i === 0 ? 'sc.rule' : undefined}
                rule={rule}
                onChange={next => setRules(rules.map((r, j) => (j === i ? next : r)))}
                onRemove={() => setRules(rules.filter((_, j) => j !== i))}
              />
            ))}

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
              <button
                data-tour="sc.add_rule"
                onClick={() => setRules([...rules, blankRule('demand_multiplier')])}
                style={{ ...btnStyle, border: `1px solid ${C.border}`, color: C.muted }}
              >
                <Plus size={14} /> {t('scenarios.add_rule')}
              </button>
              <button
                data-tour="sc.run"
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
          </Card>

          {/* Save */}
          {canEdit && (
            <Card padding={18} data-tour="sc.save">
              <h2 style={{ fontSize: 14, fontWeight: 700, color: C.text, margin: '0 0 12px' }}>
                {t('scenarios.save_title')}
              </h2>
              <div style={{ display: 'flex', gap: 8 }}>
                <Input
                  value={name}
                  onChange={e => setName(e.target.value)}
                  placeholder={t('scenarios.save_name_placeholder')}
                  aria-label={t('scenarios.save_title')}
                  size="sm" tone="bg"
                />
                <button
                  onClick={() => { void save() }}
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
            </Card>
          )}

          {/* Saved list */}
          <Card padding={18} data-tour="sc.saved">
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
          </Card>
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
              <Card padding={18} data-tour="sc.compare">
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
              </Card>

              <Card padding={18} data-tour="sc.changes">
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
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Phone layout ─────────────────────────────────────────────────────────────
//
// The desktop builder is a 420px column of inline rule editors next to two
// tables (420px and 720px minimum). On a 360px phone that measured 536px wide.
// Here the same state is laid out as an app screen:
//
//   · each rule is a card with a one-line summary ("×1.4 · Todos los
//     productos"); tapping it opens the rule in a sheet
//   · adding a rule is a two-step sheet — first what to test (the four types,
//     each with its explanation), then its values — so a buyer never faces a
//     type dropdown and six fields at once
//   · "Simular" is pinned above the tab bar, with "Guardar" beside it
//   · the comparison and the most-affected products are cards; a product's
//     full before/after row opens in a sheet
//   · a saved scenario opens a sheet to load or delete it
//
// Every number still comes from the same preview/run endpoints, through the
// same handlers as the desktop; nothing here computes a result.

/** Inputs at 16px (iOS does not zoom into them) and a 44px target. */
const TOUCH_FIELD: React.CSSProperties = { fontSize: 16, minHeight: 44, boxSizing: 'border-box', width: '100%' }

function ruleSummary(rule: ScenarioRule, t: (k: string, p?: Record<string, string | number>) => string): string {
  const scope = rule.sku?.trim() || rule.category?.trim() || t('scenarios.scope_all')
  switch (rule.type) {
    case 'demand_multiplier':
    case 'promo': {
      const parts = [`×${rule.multiplier ?? '—'}`, scope]
      if (rule.date_from || rule.date_to) parts.push(`${rule.date_from || '…'} → ${rule.date_to || '…'}`)
      return parts.join(' · ')
    }
    case 'supplier_delay':
      return `${t('scenarios.mobile_extra_days', { n: rule.extra_days ?? 0 })} · ${rule.supplier?.trim() || t('scenarios.scope_all')}`
    case 'safety_stock':
      return `${t('scenarios.field_service_level')} ${Math.round((rule.service_level ?? 0.95) * 100)}%`
  }
}

function RuleFieldsMobile({ rule, onChange }: { rule: ScenarioRule; onChange: (r: ScenarioRule) => void }) {
  const { t } = useLanguage()
  const set = (patch: Partial<ScenarioRule>) => onChange({ ...rule, ...patch })
  const isDemand = rule.type === 'demand_multiplier' || rule.type === 'promo'
  const field = (id: string, label: string, control: React.ReactNode) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      {control}
    </div>
  )
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <p style={{ margin: 0, fontSize: 14, color: C.muted, lineHeight: 1.5 }}>{t(`scenarios.help_${rule.type}`)}</p>
      {isDemand && (
        <>
          {field('m-mult', t('scenarios.field_multiplier'), (
            <Input id="m-mult" type="number" inputMode="decimal" step="any" min="0.01" max="10"
              value={rule.multiplier ?? ''} onChange={e => set({ multiplier: Number(e.target.value) })}
              tone="bg" style={TOUCH_FIELD} />
          ))}
          {field('m-sku', t('scenarios.field_sku'), (
            <Input id="m-sku" value={rule.sku ?? ''} placeholder={t('scenarios.scope_all')}
              onChange={e => set({ sku: e.target.value, category: '' })} tone="bg" style={TOUCH_FIELD} />
          ))}
          {field('m-cat', t('scenarios.field_category'), (
            <Input id="m-cat" value={rule.category ?? ''} placeholder={t('scenarios.scope_all')}
              onChange={e => set({ category: e.target.value, sku: '' })} tone="bg" style={TOUCH_FIELD} />
          ))}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
            {field('m-from', t('scenarios.field_date_from'), (
              <Input id="m-from" type="date" value={rule.date_from ?? ''}
                onChange={e => set({ date_from: e.target.value })} tone="bg" style={TOUCH_FIELD} />
            ))}
            {field('m-to', t('scenarios.field_date_to'), (
              <Input id="m-to" type="date" value={rule.date_to ?? ''}
                onChange={e => set({ date_to: e.target.value })} tone="bg" style={TOUCH_FIELD} />
            ))}
          </div>
          <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t('scenarios.scope_hint')}</p>
        </>
      )}
      {rule.type === 'supplier_delay' && (
        <>
          {field('m-days', t('scenarios.field_extra_days'), (
            <Input id="m-days" type="number" inputMode="numeric" min="0" max="365" step="1"
              value={rule.extra_days ?? ''} onChange={e => set({ extra_days: Number(e.target.value) })}
              tone="bg" style={TOUCH_FIELD} />
          ))}
          {field('m-sup', t('scenarios.field_supplier'), (
            <Input id="m-sup" value={rule.supplier ?? ''} placeholder={t('scenarios.scope_all')}
              onChange={e => set({ supplier: e.target.value })} tone="bg" style={TOUCH_FIELD} />
          ))}
        </>
      )}
      {rule.type === 'safety_stock' && field('m-sl', t('scenarios.field_service_level'), (
        <Select id="m-sl" value={String(rule.service_level ?? 0.95)}
          onChange={e => set({ service_level: Number(e.target.value) })} tone="bg" style={TOUCH_FIELD}>
          {[0.90, 0.95, 0.97, 0.99].map(level => (
            <option key={level} value={level}>{Math.round(level * 100)}%</option>
          ))}
        </Select>
      ))}
    </div>
  )
}

interface RuleSheetState {
  /** Index in `rules` being edited; null while adding a new one. */
  index: number | null
  step: 1 | 2
  draft: ScenarioRule
}

function ScenariosMobile({
  sessionId, onSession, sessions, canEdit, rules, onRules, result, running, error, onSimulate,
  saved, name, onName, saving, onSave, onLoad, onRemove,
}: {
  sessionId: string
  onSession: (id: string) => void
  sessions: { id: string; name: string }[]
  canEdit: boolean
  rules: ScenarioRule[]
  onRules: (r: ScenarioRule[]) => void
  result: ScenarioRunResult | null
  running: boolean
  error: unknown
  onSimulate: () => Promise<void>
  saved: Scenario[]
  name: string
  onName: (v: string) => void
  saving: boolean
  onSave: () => Promise<boolean>
  onLoad: (s: Scenario) => Promise<void>
  onRemove: (s: Scenario) => void
}) {
  const { t } = useLanguage()
  const [sheet,     setSheet]     = useState<RuleSheetState | null>(null)
  const [saveOpen,  setSaveOpen]  = useState(false)
  const [savedPick, setSavedPick] = useState<Scenario | null>(null)
  const [change,    setChange]    = useState<ScenarioChangeRow | null>(null)
  const resultsRef = useRef<HTMLDivElement>(null)

  // A simulation answers below the fold; bring it into view when it lands,
  // the way a native screen scrolls to what just changed.
  const showResults = () => {
    window.setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60)
  }
  const simulate = async () => { await onSimulate(); showResults() }
  const load = async (s: Scenario) => { setSavedPick(null); await onLoad(s); showResults() }

  const commitRule = () => {
    if (!sheet) return
    onRules(sheet.index === null
      ? [...rules, sheet.draft]
      : rules.map((r, j) => (j === sheet.index ? sheet.draft : r)))
    setSheet(null)
  }
  const removeRule = () => {
    if (sheet?.index == null) return
    onRules(rules.filter((_, j) => j !== sheet.index))
    setSheet(null)
  }

  const metricRows: { labelKey: string; key: keyof ScenarioRunResult['delta']; money?: boolean }[] = [
    { labelKey: 'scenarios.metric_units',      key: 'total_units_to_order' },
    { labelKey: 'scenarios.metric_value',      key: 'estimated_purchase_value', money: true },
    { labelKey: 'scenarios.metric_skus',       key: 'skus_to_order' },
    { labelKey: 'scenarios.metric_order_now',  key: 'order_now' },
    { labelKey: 'scenarios.metric_order_soon', key: 'order_soon' },
    { labelKey: 'scenarios.metric_overstock',  key: 'overstock' },
  ]

  const detailLine = (label: string, value: React.ReactNode, color?: string) => (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12,
      padding: '11px 0', borderBottom: `1px solid ${C.border}`, fontSize: 14,
    }}>
      <span style={{ color: C.dim }}>{label}</span>
      <span style={{ color: color ?? C.text, fontWeight: 600, textAlign: 'right', minWidth: 0 }}>{value}</span>
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18, minWidth: 0 }}>
      <div>
        <h1 style={{ fontSize: 18, fontWeight: 700, color: C.text, margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
          <FlaskConical size={17} color={C.accent} aria-hidden="true" /> {t('scenarios.title')}
        </h1>
        <p style={{ fontSize: 13.5, color: C.dim, margin: '6px 0 0', lineHeight: 1.5 }}>{t('scenarios.subtitle')}</p>
      </div>

      <div data-tour="sc.session" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <FieldLabel htmlFor="scenario-session-m">{t('scenarios.session_label')}</FieldLabel>
        <Select id="scenario-session-m" value={sessionId} onChange={e => onSession(e.target.value)} tone="bg" style={TOUCH_FIELD}>
          {sessions.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </Select>
      </div>

      {/* Rules */}
      <MobileSection title={t('scenarios.builder_title')}>
        <div data-tour="sc.builder" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {rules.length === 0 ? (
            <p style={{ fontSize: 14, color: C.dim, margin: 0 }}>{t('scenarios.builder_empty')}</p>
          ) : (
            <MobileList ariaLabel={t('scenarios.builder_title')}>
              {rules.map((rule, i) => (
                <MobileCard
                  key={i}
                  title={t(`scenarios.type_${rule.type}`)}
                  subtitle={ruleSummary(rule, t)}
                  onClick={() => setSheet({ index: i, step: 2, draft: rule })}
                />
              ))}
            </MobileList>
          )}
          <button
            data-tour="sc.add_rule"
            className="mobile-btn mobile-btn-secondary"
            style={{ flex: 'none', width: '100%' }}
            onClick={() => setSheet({ index: null, step: 1, draft: blankRule('demand_multiplier') })}
          >
            <Plus size={16} aria-hidden="true" /> {t('scenarios.add_rule')}
          </button>
        </div>
      </MobileSection>

      {/* Results */}
      <div ref={resultsRef} style={{ scrollMarginTop: 12, display: 'flex', flexDirection: 'column', gap: 18 }}>
        {error ? (
          <ErrorState error={error} onRetry={simulate} />
        ) : running && !result ? (
          <LoadingState label={t('scenarios.running')} />
        ) : !result ? (
          <EmptyState icon={<FlaskConical size={22} />} title={t('scenarios.compare_title')} body={t('scenarios.builder_empty')} compact />
        ) : (
          <>
            <MobileSection
              title={t('scenarios.compare_title')}
              description={t('scenarios.series_adjusted', { n: result.applied.series_adjusted })}
            >
              <div data-tour="sc.compare">
                <MobileList ariaLabel={t('scenarios.compare_title')}>
                  {metricRows.map(({ labelKey, key, money }) => {
                    const base = result.base[key], scenario = result.scenario[key], delta = result.delta[key]
                    const f = (n: number) => (money ? fmtMoney(n) : fmtNum(n))
                    return (
                      <MobileCard
                        key={key}
                        title={t(labelKey)}
                        subtitle={`${t('scenarios.col_base')} ${f(base)} → ${f(scenario)}`}
                        value={<span style={{ color: deltaColor(delta) }}>{fmtDelta(delta, money)}</span>}
                        valueCaption={t('scenarios.col_delta')}
                      />
                    )
                  })}
                </MobileList>
              </div>
            </MobileSection>

            <MobileSection title={t('scenarios.changes_title')}>
              <div data-tour="sc.changes">
                {result.changes.length === 0 ? (
                  <p style={{ fontSize: 14, color: C.dim, margin: 0 }}>{t('scenarios.changes_empty')}</p>
                ) : (
                  <MobileList ariaLabel={t('scenarios.changes_title')}>
                    {result.changes.map(row => (
                      <MobileCard
                        key={row.sku}
                        title={row.display_name || row.sku}
                        subtitle={`${row.display_name ? `${row.sku} · ` : ''}${fmtNum(row.base_qty)} → ${fmtNum(row.scenario_qty)}`}
                        status={{ label: t(SIGNAL_STYLES[(row.scenario_signal ?? 'SIN_DATOS') as InventorySignal]?.labelKey ?? 'inventory.signal_sin_datos'), tone: signalTone(row.scenario_signal) }}
                        value={<span style={{ color: deltaColor(row.delta_qty) }}>{fmtDelta(row.delta_qty)}</span>}
                        onClick={() => setChange(row)}
                      />
                    ))}
                  </MobileList>
                )}
              </div>
            </MobileSection>
          </>
        )}
      </div>

      {/* Saved */}
      <MobileSection title={t('scenarios.saved_list_title')}>
        <div data-tour="sc.saved">
          {saved.length === 0 ? (
            <p style={{ fontSize: 14, color: C.dim, margin: 0 }}>{t('scenarios.saved_list_empty')}</p>
          ) : (
            <MobileList ariaLabel={t('scenarios.saved_list_title')}>
              {saved.map(s => (
                <MobileCard
                  key={s.id}
                  title={s.name}
                  subtitle={t('scenarios.rules_count', { n: s.rules.length })}
                  onClick={() => setSavedPick(s)}
                />
              ))}
            </MobileList>
          )}
        </div>
      </MobileSection>

      <StickyActionBar>
        {canEdit && (
          <button
            data-tour="sc.save"
            className="mobile-btn mobile-btn-secondary"
            style={{ flex: '0 0 auto' }}
            onClick={() => setSaveOpen(true)}
            disabled={!rules.length}
          >
            <Save size={16} aria-hidden="true" /> {t('scenarios.save')}
          </button>
        )}
        <button
          data-tour="sc.run"
          className="mobile-btn mobile-btn-primary"
          onClick={simulate}
          disabled={running || !sessionId}
        >
          <Play size={16} aria-hidden="true" />
          {running ? t('scenarios.running') : result ? t('scenarios.rerun') : t('scenarios.run')}
        </button>
      </StickyActionBar>

      {/* Rule sheet: step 1 picks what to test, step 2 sets its values. */}
      <BottomSheet
        open={!!sheet}
        onClose={() => setSheet(null)}
        title={sheet?.step === 1 ? t('scenarios.mobile_pick_type') : sheet ? t(`scenarios.type_${sheet.draft.type}`) : ''}
        footer={sheet?.step === 2 ? (
          <div style={{ display: 'flex', gap: 8, width: '100%' }}>
            {sheet.index === null ? (
              <button className="mobile-btn mobile-btn-secondary" style={{ flex: '0 0 auto' }}
                      onClick={() => setSheet({ ...sheet, step: 1 })}>
                <ChevronLeft size={16} aria-hidden="true" /> {t('mobile.back')}
              </button>
            ) : (
              <button className="mobile-btn mobile-btn-secondary" style={{ flex: '0 0 auto', color: 'var(--signal-order-now-fg)' }}
                      onClick={removeRule}>
                <Trash2 size={16} aria-hidden="true" /> {t('scenarios.remove_rule')}
              </button>
            )}
            <button className="mobile-btn mobile-btn-primary" onClick={commitRule}>
              {sheet.index === null ? t('scenarios.add_rule') : t('scenarios.mobile_done')}
            </button>
          </div>
        ) : undefined}
      >
        {sheet && (
          <div style={{ paddingBottom: 8 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.dim, letterSpacing: '0.04em', textTransform: 'uppercase', marginBottom: 10 }}>
              {t('scenarios.mobile_step', { n: sheet.step })}
            </div>
            {sheet.step === 1 ? (
              <MobileList ariaLabel={t('scenarios.rule_type')}>
                {RULE_TYPES.map(type => (
                  <MobileCard
                    key={type}
                    title={t(`scenarios.type_${type}`)}
                    subtitle={t(`scenarios.help_${type}`)}
                    selected={sheet.draft.type === type}
                    onClick={() => setSheet({
                      ...sheet, step: 2,
                      draft: sheet.draft.type === type ? sheet.draft : blankRule(type),
                    })}
                  />
                ))}
              </MobileList>
            ) : (
              <>
                <RuleFieldsMobile rule={sheet.draft} onChange={draft => setSheet({ ...sheet, draft })} />
                {sheet.index !== null && (
                  <button
                    onClick={() => setSheet({ ...sheet, step: 1 })}
                    style={{
                      all: 'unset', cursor: 'pointer', marginTop: 12, minHeight: 44,
                      display: 'flex', alignItems: 'center', fontSize: 14, fontWeight: 600, color: C.accent,
                    }}
                  >
                    {t('scenarios.mobile_change_type')}
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </BottomSheet>

      {/* Save */}
      <BottomSheet
        open={saveOpen}
        onClose={() => setSaveOpen(false)}
        title={t('scenarios.save_title')}
        footer={
          <button
            className="mobile-btn mobile-btn-primary"
            style={{ width: '100%' }}
            disabled={saving || !rules.length}
            onClick={async () => { if (await onSave()) setSaveOpen(false) }}
          >
            <Save size={16} aria-hidden="true" /> {saving ? t('scenarios.saving') : t('scenarios.save')}
          </button>
        }
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingBottom: 8 }}>
          <Input
            value={name}
            onChange={e => onName(e.target.value)}
            placeholder={t('scenarios.save_name_placeholder')}
            aria-label={t('scenarios.save_title')}
            enterKeyHint="done"
            tone="bg"
            style={TOUCH_FIELD}
          />
          <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t('scenarios.rules_count', { n: rules.length })}</p>
        </div>
      </BottomSheet>

      {/* A saved scenario: load it or delete it. */}
      <BottomSheet
        open={!!savedPick}
        onClose={() => setSavedPick(null)}
        title={savedPick?.name ?? ''}
        footer={savedPick ? (
          <div style={{ display: 'flex', gap: 8, width: '100%' }}>
            {canEdit && (
              <button className="mobile-btn mobile-btn-secondary" style={{ flex: '0 0 auto', color: 'var(--signal-order-now-fg)' }}
                      onClick={() => { const s = savedPick; setSavedPick(null); onRemove(s) }}>
                <Trash2 size={16} aria-hidden="true" /> {t('scenarios.delete')}
              </button>
            )}
            <button className="mobile-btn mobile-btn-primary" onClick={() => load(savedPick)}>
              <Play size={16} aria-hidden="true" /> {t('scenarios.load')}
            </button>
          </div>
        ) : undefined}
      >
        {savedPick && (
          <MobileList ariaLabel={t('scenarios.builder_title')}>
            {savedPick.rules.map((rule, i) => (
              <MobileCard key={i} title={t(`scenarios.type_${rule.type}`)} subtitle={ruleSummary(rule, t)} />
            ))}
          </MobileList>
        )}
      </BottomSheet>

      {/* One product's full before/after row. */}
      <BottomSheet open={!!change} onClose={() => setChange(null)} title={change ? (change.display_name || change.sku) : ''}>
        {change && (
          <div style={{ paddingBottom: 8 }}>
            {change.display_name && detailLine(t('scenarios.col_sku'), change.sku)}
            {detailLine(t('scenarios.col_signal'), (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                <SignalBadge signal={change.base_signal} />
                <span style={{ color: C.dim }}>→</span>
                <SignalBadge signal={change.scenario_signal} />
              </span>
            ))}
            {detailLine(t('scenarios.col_qty'), `${fmtNum(change.base_qty)} → ${fmtNum(change.scenario_qty)}`)}
            {detailLine(t('scenarios.col_delta'), fmtDelta(change.delta_qty), deltaColor(change.delta_qty))}
            {detailLine(t('scenarios.col_demand'), `${fmtDemand(change.base_daily_demand)} → ${fmtDemand(change.scenario_daily_demand)}`)}
            {detailLine(t('scenarios.col_lead_time'), `${change.base_lead_time_days ?? '—'} → ${change.scenario_lead_time_days ?? '—'}`)}
          </div>
        )}
      </BottomSheet>
    </div>
  )
}
