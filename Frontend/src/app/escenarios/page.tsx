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
import { useIsNarrow } from '@/hooks/useIsNarrow'
import Card from '@/components/ui/Card'
import Table, { Th, Td } from '@/components/ui/Table'
import Input, { FieldLabel, Select } from '@/components/ui/Input'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'
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

// `useGrouping: 'always'`: the Spanish locale leaves four-digit numbers
// ungrouped ("3585") while its currency figures are grouped ("$22.850"), which
// made columns of the same table look like two different conventions.
const fmtNum = (n: number) =>
  new Intl.NumberFormat('es', { maximumFractionDigits: 0, useGrouping: 'always' }).format(n)
const fmtMoney = (n: number) =>
  new Intl.NumberFormat('es', { maximumFractionDigits: 0, useGrouping: 'always' }).format(n)
const fmtDelta = (n: number, money = false) => {
  const body = money ? `$${fmtMoney(Math.abs(n))}` : fmtNum(Math.abs(n))
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
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
        <Select
          value={rule.type}
          onChange={e => onChange(blankRule(e.target.value as ScenarioRuleType))}
          aria-label={t('scenarios.rule_type')}
          size="sm" tone="bg"
          style={{ flex: 1, fontWeight: 600 }}
        >
          {RULE_TYPES.map(type => (
            <option key={type} value={type}>{t(`scenarios.type_${type}`)}</option>
          ))}
        </Select>
        <button
          onClick={onRemove}
          title={t('scenarios.remove_rule')}
          aria-label={t('scenarios.remove_rule')}
          style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 8 }}
        >
          <X size={15} />
        </button>
      </div>
      <div style={{ fontSize: 12, color: C.dim, lineHeight: 1.5, marginBottom: 10 }}>
        {t(`scenarios.help_${rule.type}`)}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10 }}>
        {isDemand && (
          <>
            <div data-tour={tourAnchor ? 'sc.multiplier' : undefined}>
              <FieldLabel variant="eyebrow" htmlFor={`mult-${rule.type}`}>{t('scenarios.field_multiplier')}</FieldLabel>
              <Input
                id={`mult-${rule.type}`} type="number" step="0.05" min="0.01" max="10"
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
                <Td align="right" divider="top">{money ? `$${fmtMoney(base)}` : fmtNum(base)}</Td>
                <Td align="right" divider="top" style={{ fontWeight: 600 }}>
                  {money ? `$${fmtMoney(scenario)}` : fmtNum(scenario)}
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
  const narrow = useIsNarrow()
  // On a phone a six-column table scrolls sideways and hides the semáforo
  // change, which is the point of the list. One card per product instead.
  if (narrow) {
    return (
      <div>
        {rows.map(row => (
          <div key={row.sku} style={{ padding: '10px 0', borderTop: `1px solid ${C.border}` }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'baseline' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{row.display_name || row.sku}</div>
                {row.display_name && <div style={{ fontSize: 11.5, color: C.dim }}>{row.sku}</div>}
              </div>
              <span style={{ fontSize: 13, fontWeight: 700, color: deltaColor(row.delta_qty), flexShrink: 0 }}>
                {fmtDelta(row.delta_qty)} {t('scenarios.unit_units')}
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
              <SignalBadge signal={row.base_signal} />
              <span style={{ color: C.dim }}>→</span>
              <SignalBadge signal={row.scenario_signal} />
            </div>
            <div style={{ fontSize: 12, color: C.muted, marginTop: 6 }}>
              {t('scenarios.col_qty')}: {fmtNum(row.base_qty)} → <strong>{fmtNum(row.scenario_qty)}</strong>
              {' · '}
              {t('scenarios.col_demand')}: {row.base_daily_demand ?? '—'} → {row.scenario_daily_demand ?? '—'}
            </div>
          </div>
        ))}
      </div>
    )
  }
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
                {row.base_daily_demand ?? '—'} → {row.scenario_daily_demand ?? '—'}
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

// ── Result: reading, comparison tiles, detail ────────────────────────────────

/** Percent for a service level that arrives as a fraction (0.95) or a percent. */
const pctOf = (level: number) => Math.round(level <= 1 ? level * 100 : level)

/** One assumption as a short chip: what changes, by how much, and for what. */
function ruleChip(rule: ScenarioRule, t: (k: string, p?: Record<string, unknown>) => string) {
  const label = t(`scenarios.type_${rule.type}`)
  let detail = ''
  if (rule.type === 'demand_multiplier' || rule.type === 'promo') {
    detail = `×${rule.multiplier ?? 1}`
    if (rule.date_from || rule.date_to) detail += ` (${rule.date_from || '…'} – ${rule.date_to || '…'})`
  } else if (rule.type === 'supplier_delay') {
    detail = `+${rule.extra_days ?? 0} ${t('scenarios.unit_days')}`
  } else if (rule.type === 'safety_stock') {
    detail = `${pctOf(rule.service_level ?? 0.95)}%`
  }
  const scope = rule.sku || rule.category || rule.supplier || t('scenarios.scope_all')
  return { label, detail, scope }
}

/** The result in plain words, written from the deltas the backend returned. */
function Reading({ result }: { result: ScenarioRunResult }) {
  const { t } = useLanguage()
  const d = result.delta
  const lines: string[] = []
  if (d.total_units_to_order !== 0) {
    lines.push(t(d.total_units_to_order > 0 ? 'scenarios.reading_buy_more' : 'scenarios.reading_buy_less', {
      units: fmtNum(Math.abs(d.total_units_to_order)),
      value: `$${fmtMoney(Math.abs(d.estimated_purchase_value))}`,
    }))
  }
  if (d.order_now !== 0) {
    lines.push(t(d.order_now > 0 ? 'scenarios.reading_urgent_more' : 'scenarios.reading_urgent_less', {
      n: fmtNum(Math.abs(d.order_now)),
    }))
  }
  if (d.overstock !== 0) {
    lines.push(t(d.overstock > 0 ? 'scenarios.reading_over_more' : 'scenarios.reading_over_less', {
      n: fmtNum(Math.abs(d.overstock)),
    }))
  }
  // Zero evaluated products is not "no change": there was nothing to compare.
  if (result.base.skus_evaluated === 0) {
    lines.length = 0
    lines.push(t('scenarios.reading_no_stock'))
  } else if (!lines.length) {
    lines.push(t('scenarios.reading_none'))
  }

  return (
    <div style={{
      borderLeft: '3px solid var(--accent)', background: 'var(--accent-dim)',
      borderRadius: 8, padding: '12px 14px',
    }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: C.accent, marginBottom: 6 }}>
        {t('scenarios.reading_title')}
      </div>
      <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 5 }}>
        {lines.map(line => (
          <li key={line} style={{ fontSize: 13.5, lineHeight: 1.55, color: C.text }}>{line}</li>
        ))}
      </ul>
    </div>
  )
}

/** Plan vs scenario for one metric: two bars on the same scale, then the gap. */
function MetricTile({ label, unit, base, scenario, money }: {
  label: string
  unit: string
  base: number
  scenario: number
  money?: boolean
}) {
  const { t } = useLanguage()
  const delta = scenario - base
  const top = Math.max(base, scenario, 1)
  const show = (n: number) => (money ? `$${fmtMoney(n)}` : fmtNum(n))
  const bar = (value: number, color: string) => (
    <div style={{ height: 6, borderRadius: 3, background: 'var(--surface-3)', overflow: 'hidden' }}>
      <div style={{ width: `${(value / top) * 100}%`, height: '100%', background: color, borderRadius: 3 }} />
    </div>
  )
  return (
    <div style={{
      background: 'var(--surface-2)', border: `1px solid ${C.border}`, borderRadius: 10,
      padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0,
    }}>
      <div style={{ fontSize: 12, color: C.muted }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 18, fontWeight: 700, color: C.text, lineHeight: 1.2 }}>{show(scenario)}</span>
        {unit && <span style={{ fontSize: 11.5, color: C.dim }}>{unit}</span>}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        {bar(base, 'var(--border-strong)')}
        {bar(scenario, 'var(--accent)')}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '2px 8px', flexWrap: 'wrap', fontSize: 11.5, color: C.dim }}>
        <span>{t('scenarios.col_base')}: {show(base)}</span>
        <span style={{ fontWeight: 700, color: deltaColor(delta) }}>
          {delta === 0 ? t('scenarios.no_change') : fmtDelta(delta, money)}
        </span>
      </div>
    </div>
  )
}

function ResultPanel({ result, busy }: { result: ScenarioRunResult; busy: boolean }) {
  const { t } = useLanguage()
  const { base, scenario } = result
  return (
    <>
      <Card padding={18} data-tour="sc.compare" style={{ opacity: busy ? 0.55 : 1, transition: 'opacity .15s' }}>
        <div style={{
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
          gap: 8, marginBottom: 12, flexWrap: 'wrap',
        }}>
          <h2 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: 0 }}>
            {t('scenarios.compare_title')}
          </h2>
          <span style={{ fontSize: 11.5, color: C.dim }}>
            {t('scenarios.series_adjusted', { n: result.applied.series_adjusted })}
            {' · '}
            {t('scenarios.applied_service', { pct: pctOf(result.applied.service_level) })}
          </span>
        </div>

        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 11.5, color: C.dim, marginBottom: 6 }}>{t('scenarios.assumptions_title')}</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {result.rules.map((rule, i) => {
              const chip = ruleChip(rule, t)
              return (
                <span key={i} style={{
                  display: 'inline-flex', flexDirection: 'column', gap: 1, padding: '5px 10px',
                  border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 12, color: C.text,
                }}>
                  <span style={{ fontWeight: 600 }}>{chip.label} {chip.detail}</span>
                  <span style={{ fontSize: 11, color: C.dim }}>{chip.scope}</span>
                </span>
              )
            })}
          </div>
        </div>

        <Reading result={result} />

        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
          gap: 10, margin: '14px 0 4px',
        }}>
          <MetricTile
            label={t('scenarios.metric_value')} unit="" money
            base={base.estimated_purchase_value} scenario={scenario.estimated_purchase_value}
          />
          <MetricTile
            label={t('scenarios.metric_units')} unit={t('scenarios.unit_units')}
            base={base.total_units_to_order} scenario={scenario.total_units_to_order}
          />
          <MetricTile
            label={t('scenarios.tile_urgent')} unit={t('scenarios.unit_products')}
            base={base.order_now} scenario={scenario.order_now}
          />
          <MetricTile
            label={t('scenarios.tile_overstock')} unit={t('scenarios.unit_products')}
            base={base.overstock} scenario={scenario.overstock}
          />
        </div>
        <div style={{ display: 'flex', gap: 14, fontSize: 11.5, color: C.dim, margin: '8px 0 2px' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <i style={{ width: 10, height: 6, borderRadius: 3, background: 'var(--border-strong)' }} />
            {t('scenarios.col_base')}
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <i style={{ width: 10, height: 6, borderRadius: 3, background: 'var(--accent)' }} />
            {t('scenarios.col_scenario')}
          </span>
        </div>

        <details style={{ marginTop: 12 }}>
          <summary style={{ fontSize: 12.5, color: C.muted, cursor: 'pointer', padding: '6px 0' }}>
            {t('scenarios.detail_toggle')}
          </summary>
          <div style={{ marginTop: 6 }}>
            <CompareTable result={result} />
          </div>
        </details>
      </Card>

      <Card padding={18} data-tour="sc.changes" style={{ opacity: busy ? 0.55 : 1, transition: 'opacity .15s' }}>
        <h2 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: '0 0 8px' }}>
          {t('scenarios.changes_title')}
        </h2>
        {result.changes.length === 0 ? (
          <p style={{ fontSize: 13, color: C.dim, margin: 0 }}>
            {t('scenarios.changes_empty')}
          </p>
        ) : (
          <ChangesTable rows={result.changes} />
        )}
      </Card>
    </>
  )
}

/** Before anything has been simulated: what this screen gives you, and a way in. */
function Starter({ onPick, disabled }: { onPick: (rules: ScenarioRule[]) => void; disabled: boolean }) {
  const { t } = useLanguage()
  const presets: { key: string; rules: ScenarioRule[] }[] = [
    { key: 'demand',  rules: [{ type: 'demand_multiplier', multiplier: 1.4 }] },
    { key: 'delay',   rules: [{ type: 'supplier_delay', extra_days: 7 }] },
    { key: 'service', rules: [{ type: 'safety_stock', service_level: 0.99 }] },
  ]
  return (
    <Card padding="28px 24px" style={{ textAlign: 'center' }}>
      <div style={{
        width: 44, height: 44, borderRadius: 12, margin: '0 auto 12px',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'color-mix(in srgb, var(--accent) 12%, transparent)', color: C.accent,
      }}>
        <FlaskConical size={20} />
      </div>
      <h2 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: '0 0 6px' }}>
        {t('scenarios.empty_title')}
      </h2>
      <p style={{ fontSize: 13, color: C.muted, lineHeight: 1.6, margin: '0 auto 16px', maxWidth: 440 }}>
        {t('scenarios.empty_body')}
      </p>
      <div style={{ fontSize: 11.5, color: C.dim, marginBottom: 8 }}>{t('scenarios.presets_title')}</div>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
        {presets.map(p => (
          <button
            key={p.key}
            className="sim-btn"
            disabled={disabled}
            onClick={() => onPick(p.rules)}
            style={{
              ...btnStyle, border: `1px solid ${C.border}`, color: C.text, background: 'var(--surface-2)',
              opacity: disabled ? 0.6 : 1, cursor: disabled ? 'not-allowed' : 'pointer',
            }}
          >
            {t(`scenarios.preset_${p.key}`)}
          </button>
        ))}
      </div>
    </Card>
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

  const reloadSaved = useCallback((sid: string) => {
    if (!sid) { setSaved([]); return }
    listScenarios(sid, { silent: true }).then(setSaved).catch(() => setSaved([]))
  }, [])

  useEffect(() => { reloadSaved(sessionId) }, [sessionId, reloadSaved])
  // A different session means a different forecast: the old comparison would be
  // stale numbers under a new title.
  useEffect(() => { setResult(null) }, [sessionId])

  const cleanRules = useMemo(() => rules.map(cleanRule), [rules])

  const simulate = async (toRun: ScenarioRule[] = cleanRules) => {
    if (!sessionId) return
    setRunning(true); setError(null)
    try {
      setResult(await previewScenario(sessionId, toRun, { silent: true }))
    } catch (e: unknown) {
      setError(e)
      setResult(null)
    } finally {
      setRunning(false)
    }
  }

  const usePreset = (preset: ScenarioRule[]) => {
    setRules(preset)
    void simulate(preset.map(cleanRule))
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

  return (
    <div className="sim-page" style={{ padding: '22px 28px', maxWidth: 1240, margin: '0 auto' }}>
      {/* Header + session picker */}
      <div style={{
        display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
        gap: 16, flexWrap: 'wrap', marginBottom: 18,
      }}>
        <div style={{ minWidth: 0, flex: '1 1 320px' }}>
          <h1 style={{
            fontSize: 18, fontWeight: 700, color: C.text, margin: 0,
            display: 'flex', alignItems: 'center', gap: 9,
          }}>
            <FlaskConical size={18} color={C.accent} />
            {t('scenarios.title')}
          </h1>
          <p style={{ fontSize: 13, color: C.dim, margin: '6px 0 0', maxWidth: 640, lineHeight: 1.55 }}>
            {t('scenarios.subtitle')}
          </p>
        </div>
        <div data-tour="sc.session" className="sim-session">
          <FieldLabel variant="eyebrow" htmlFor="scenario-session">{t('scenarios.session_label')}</FieldLabel>
          <Select
            id="scenario-session"
            value={sessionId}
            onChange={e => setSessionId(e.target.value)}
            size="sm" tone="bg"
            style={{ width: '100%', minWidth: 220 }}
          >
            {completedSessions.map(s => (
              <option key={s.session_id} value={s.session_id}>{s.name}</option>
            ))}
          </Select>
        </div>
      </div>

      <div className="sim-grid">
        {/* Assumptions */}
        <div className="sim-build" data-tour="sc.builder">
          <Card padding={18}>
            <h2 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: '0 0 12px' }}>
              {t('scenarios.builder_title')}
            </h2>

            {rules.length === 0 && (
              <p style={{ fontSize: 13, color: C.dim, margin: '0 0 12px' }}>
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
                className="sim-btn"
                onClick={() => setRules([...rules, blankRule('demand_multiplier')])}
                style={{ ...btnStyle, border: `1px solid ${C.border}`, color: C.muted }}
              >
                <Plus size={14} /> {t('scenarios.add_rule')}
              </button>
              <button
                data-tour="sc.run"
                className="sim-btn"
                onClick={() => void simulate()}
                disabled={running || !sessionId || !rules.length}
                style={{
                  ...btnStyle, background: C.accent, color: '#fff', flex: '1 1 auto', justifyContent: 'center',
                  opacity: running || !sessionId || !rules.length ? 0.6 : 1,
                  cursor: running || !sessionId || !rules.length ? 'not-allowed' : 'pointer',
                }}
              >
                <Play size={14} />
                {running ? t('scenarios.running') : result ? t('scenarios.rerun') : t('scenarios.run')}
              </button>
            </div>
          </Card>
        </div>

        {/* Result */}
        <div className="sim-result" style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
          {error ? (
            <ErrorState error={error} onRetry={() => void simulate()} />
          ) : running && !result ? (
            <LoadingState label={t('scenarios.running')} />
          ) : !result ? (
            <Starter onPick={usePreset} disabled={running || !sessionId} />
          ) : (
            <ResultPanel result={result} busy={running} />
          )}
        </div>

        {/* Saved scenarios */}
        <div className="sim-extras" style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
          {canEdit && (
            <Card padding={18} data-tour="sc.save">
              <h2 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: '0 0 12px' }}>
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
                  className="sim-btn"
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
                <p style={{ fontSize: 12, color: C.dim, margin: '8px 0 0' }}>
                  {t('scenarios.no_rules_hint')}
                </p>
              )}
            </Card>
          )}

          <Card padding={18} data-tour="sc.saved">
            <h2 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: '0 0 12px' }}>
              {t('scenarios.saved_list_title')}
            </h2>
            {saved.length === 0 ? (
              <p style={{ fontSize: 13, color: C.dim, margin: 0 }}>
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
                  <div style={{ fontSize: 11.5, color: C.dim }}>
                    {t('scenarios.rules_count', { n: scenario.rules.length })}
                  </div>
                </div>
                <button
                  className="sim-btn"
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
                    style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 10, margin: -6 }}
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            ))}
          </Card>
        </div>
      </div>
    </div>
  )
}
