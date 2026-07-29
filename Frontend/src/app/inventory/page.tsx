'use client'
import { useState, useEffect, useCallback, useRef, useMemo, Fragment } from 'react'
import Link from 'next/link'
import {
 getInventoryStatus, upsertInventoryStock, deleteInventoryStock,
 importInventoryCSV, exportInventoryPO, downloadInventoryPDF,
 listInventoryEvents, createInventoryEvent, updateInventoryEvent, deleteInventoryEvent,
 listSuppliers, getDeadStock, simulateEvent, logPOGeneration, downloadInventoryTemplate,
 createShrinkage,
 getCalendarCatalog, seedCalendarCatalog, toggleCalendarEntry,
 listEventMultipliers, setEventMultiplier, deleteEventMultiplier,
} from '@/lib/api'
import type {
 InventoryStatusItem, InventorySignal,
 InventoryCalcExplanation, InventoryEvent, Supplier, DeadStockResponse, ExcludedSku,
 EventSimulationResult, POLineDecision, ShrinkageReason, CalendarCatalogEntry, CoverageUnit,
 EventMultiplier,
} from '@/lib/types'
import { useAutoSession } from '@/hooks/useAutoSession'
import Pagination, { usePage } from '@/components/table/Pagination'
import { useWarehouses, WarehouseSelector } from '@/components/inventory/WarehouseControls'
import { WarehouseStatusTable } from '@/components/inventory/WarehouseStatusTable'
import DataFreshness from '@/components/ui/DataFreshness'
import Spinner from '@/components/ui/Spinner'
import { EmptyState, ErrorState, InlineError, LoadingState, SkeletonCards, SkeletonTable } from '@/components/ui/States'
import HelpTip from '@/components/ui/HelpTip'
import SharedSignalBadge, { signalColor } from '@/components/ui/SignalBadge'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'
import { formatMoney, formatMoneyCompact } from '@/lib/currency'
import { coverageUnitShort } from '@/lib/period'
import {
  DEFAULT_LEAD_TIME_DAYS, DEFAULT_MOQ, DEFAULT_SERVICE_LEVEL,
  isAssumed, sourceLabelKey, type ValueSource,
} from '@/lib/inventoryDefaults'
import {
 ShoppingCart, AlertTriangle, CheckCircle2, TrendingDown, TrendingUp,
 ChevronDown, ChevronRight, RefreshCw, Upload, Download, Edit2, Trash2,
 X, Save, Package, Info, Layers, List, FileText, Calendar, Plus, PencilLine, Truck, Sliders,
 Zap, PackageMinus, Search, PackagePlus,
} from 'lucide-react'

// Maps the active UI language to a concrete BCP-47 locale for date formatting,
// so dates follow the language toggle instead of always rendering in Spanish.
function localeFor(lang: string): string {
 return lang === 'en' ? 'en-US' : 'es-CR'
}

// ── Palette ───────────────────────────────────────────────────────────────────
const C = {
 surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
 text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
 red: '#ef4444', amber: '#f59e0b', green: '#22c55e', blue: '#3b82f6', indigo: 'var(--accent)',
}

// ── Signal config ─────────────────────────────────────────────────────────────
// The signal presentation (icon + label + accessible colour) lives in
// components/ui/SignalBadge — it used to be duplicated here and in
// SkuSearchOverlay, with colours that failed contrast in the light theme.
function SignalBadge({ s }: { s: InventorySignal }) {
 return <SharedSignalBadge signal={s} />
}

// ── Tooltip ───────────────────────────────────────────────────────────────────
function Tooltip({ text, children }: { text: string; children: React.ReactNode }) {
 const [pos, setPos] = useState<{ x: number; y: number } | null>(null)

 return (
 <span
 style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 4 }}
 onMouseEnter={e => {
 const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
 setPos({ x: rect.left + rect.width / 2, y: rect.top })
 }}
 onMouseLeave={() => setPos(null)}
 >
 {children}
 {pos && (
 <span style={{
 position: 'fixed',
 left: pos.x, top: pos.y - 8,
 transform: 'translate(-50%, -100%)',
 background: '#1e293b', color: '#e2e8f0', fontSize: 11, lineHeight: 1.55,
 padding: '8px 11px', borderRadius: 7, width: 230, zIndex: 9999,
 border: '1px solid #334155', boxShadow: '0 6px 18px rgba(0,0,0,0.5)',
 pointerEvents: 'none', whiteSpace: 'normal',
 }}>
 {text}
 <span style={{
 position: 'absolute', top: '100%', left: '50%', transform: 'translateX(-50%)',
 borderLeft: '5px solid transparent', borderRight: '5px solid transparent',
 borderTop: '5px solid #1e293b',
 }} />
 </span>
 )}
 </span>
 )
}

// ── Sorting ───────────────────────────────────────────────────────────────────
// The full table now shows one page at a time, so "scroll until I find it" is
// no longer how you reach a row: the columns have to be sortable, and the sort
// has to be announced (aria-sort) rather than left to the arrow glyph.
type SortKey = 'signal' | 'sku' | 'stock' | 'coverage' | 'demand_lt' | 'qty'
             | 'lead_time' | 'moq' | 'abc_xyz' | 'value'
type SortDir = 'asc' | 'desc'
interface SortState { key: SortKey; dir: SortDir }

const SIGNAL_ORDER: Record<string, number> = {
 PEDIR_YA: 0, PEDIR_PRONTO: 1, OK: 2, SOBRESTOCK: 3, SIN_DATOS: 4,
}

function sortValue(item: InventoryStatusItem, key: SortKey): number | string {
 switch (key) {
  case 'signal':    return SIGNAL_ORDER[item.signal] ?? 99
  case 'sku':       return (item.display_name || item.sku).toLowerCase()
  case 'stock':     return item.current_stock ?? -Infinity
  case 'coverage':  return item.coverage_days ?? -Infinity
  case 'demand_lt': return item.lead_time_demand ?? -Infinity
  case 'qty':       return item.recommended_qty ?? -Infinity
  case 'lead_time': return item.lead_time_days ?? -Infinity
  case 'moq':       return item.moq ?? -Infinity
  case 'abc_xyz':   return item.abc_xyz || 'ZZ'
  case 'value':     return item.inventory_value ?? -Infinity
 }
}

function sortItems(items: InventoryStatusItem[], sort: SortState): InventoryStatusItem[] {
 const factor = sort.dir === 'asc' ? 1 : -1
 return [...items].sort((a, b) => {
  const va = sortValue(a, sort.key), vb = sortValue(b, sort.key)
  if (typeof va === 'string' || typeof vb === 'string') {
   return String(va).localeCompare(String(vb)) * factor
  }
  return (va - vb) * factor || a.sku.localeCompare(b.sku)
 })
}

const thStyle: React.CSSProperties = {
 padding: '9px 12px', textAlign: 'left', whiteSpace: 'nowrap', color: C.dim,
 fontWeight: 600, fontSize: 10, borderBottom: `1px solid ${C.border}`,
 textTransform: 'uppercase' as const, letterSpacing: '0.06em',
}

function ThTip({ label, tip, sortKey, sort, onSort }: {
 label: string
 tip: string
 sortKey?: SortKey
 sort?: SortState | null
 onSort?: (key: SortKey) => void
}) {
 const { t } = useLanguage()
 const active = sortKey != null && sort?.key === sortKey
 const ariaSort: 'ascending' | 'descending' | 'none' | undefined =
  sortKey == null ? undefined : active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : 'none'

 const inner = <Tooltip text={tip}><span>{label}</span><Info size={9} color={C.dim} style={{ opacity: 0.5, flexShrink: 0 }} /></Tooltip>

 if (sortKey == null || !onSort) {
  return <th scope="col" style={thStyle}>{inner}</th>
 }

 // The next state this button produces, spelled out — an arrow glyph alone
 // tells a screen-reader user nothing about what activating it will do.
 const nextDir: SortDir = active && sort!.dir === 'asc' ? 'desc' : 'asc'
 const actionLabel = tOr(t,
  nextDir === 'asc' ? 'table.sort_ascending_by' : 'table.sort_descending_by',
  nextDir === 'asc' ? `Sort ascending by ${label}` : `Sort descending by ${label}`,
  { column: label })

 return (
  <th scope="col" aria-sort={ariaSort} style={{ ...thStyle, color: active ? 'var(--accent)' : C.dim }}>
   <button
    type="button"
    onClick={() => onSort(sortKey)}
    aria-label={actionLabel}
    title={actionLabel}
    style={{
     all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
     font: 'inherit', color: 'inherit', letterSpacing: 'inherit', textTransform: 'inherit',
    }}
   >
    {inner}
    <span aria-hidden="true" style={{ fontSize: 9, opacity: active ? 1 : 0.35 }}>
     {active ? (sort!.dir === 'asc' ? '▲' : '▼') : '↕'}
    </span>
   </button>
  </th>
 )
}

// ── ABC-XYZ badge ─────────────────────────────────────────────────────────────
const ABC_COLOR: Record<string, string> = { A: '#22c55e', B: '#f59e0b', C: '#64748b', '?': '#334155' }
const XYZ_COLOR: Record<string, string> = { X: 'var(--accent)', Y: '#f59e0b', Z: '#ef4444', '?': '#334155' }
function AbcXyzBadge({ value }: { value: string }) {
 if (!value || value === '?') return <span style={{ color: C.dim }}>—</span>
 const abc = value[0], xyz = value[1] || ''
 return (
 <span style={{ display: 'inline-flex', gap: 2, fontFamily: 'monospace', fontWeight: 700, fontSize: 11 }}>
 <span style={{ color: ABC_COLOR[abc] ?? C.dim }}>{abc}</span>
 <span style={{ color: XYZ_COLOR[xyz] ?? C.dim }}>{xyz}</span>
 </span>
 )
}

// ── Sparkline ─────────────────────────────────────────────────────────────────
function Sparkline({ data }: { data: { stock: number }[] }) {
 if (data.length < 2) return <span style={{ color: C.dim, fontSize: 10 }}>—</span>
 const W = 60, H = 20
 const vals = data.map(d => d.stock)
 const lo = Math.min(...vals), hi = Math.max(...vals), rng = hi - lo || 1
 const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * W},${H - 2 - ((v - lo) / rng) * (H - 4)}`).join(' ')
 const trend = vals[vals.length - 1] - vals[0]
 const tColor = trend < -rng * 0.1 ? C.red : trend > rng * 0.1 ? C.green : C.indigo
 return (
 <svg width={W} height={H} style={{ display: 'block' }}>
 <polyline points={pts} fill="none" stroke={tColor} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
 </svg>
 )
}

// ── My data, or Faro's assumption? ────────────────────────────────────
// `t` returns the key itself when the catalog has no entry, so a build whose
// copy has not landed yet would print "inventory.source_default" at the buyer.
// Same guard `lib/explanationCopy.ts` uses: probe, and fall back to real words.
type Translate = (key: string, params?: Record<string, unknown>) => string

function tOr(t: Translate, key: string, fallback: string, params?: Record<string, unknown>): string {
  const text = t(key, params)
  return text === key ? fallback : text
}

// English last-resort wording, one per provenance value. Spanish never appears
// here — it lives in the i18n catalog (CLAUDE.md).
const SOURCE_FALLBACK_EN: Record<ValueSource, string> = {
  user:          'you set it',
  file:          'from your file',
  supplier_rule: 'rule',
  learned:       'learned from your deliveries',
  default:       'estimated',
}

/** Names where a value came from, across all five sources. Used in the detail
 *  panel, where the buyer is asking exactly this question. */
function ProvenanceNote({ source, scope }: { source?: ValueSource | null; scope?: string | null }) {
  const { t } = useLanguage()
  const value: ValueSource = source || 'default'
  const label = tOr(t, sourceLabelKey(value), SOURCE_FALLBACK_EN[value])
  // A rule hit names the level that won, so the precedence is visible rather
  // than something the buyer has to reverse-engineer.
  if (value === 'supplier_rule' && scope) {
    return <>{label} · {tOr(t, `explain.scope_${scope}`, scope)}</>
  }
  return <>{label}</>
}

// A muted dotted "estimado" badge on any value we assumed rather than received.
// Only assumed values are badged: the absence of a badge means the number is
// the tenant's own (typed, imported, ruled or learned from their receptions).
// Marking all five sources filled the table with labels without answering the
// one question a buyer actually has — which of these numbers did I never give
// you? Before this, a lead time of 15 days looked identical whether they had
// chosen it or never opened the SKU.
function SourceBadge({ source }: { source?: ValueSource | null }) {
  const { t } = useLanguage()
  if (!isAssumed(source)) return null
  return (
    <span
      title={tOr(t, 'inventory.source_assumed_tip',
        'Faro assumed this value — you have not given us one yet.')}
      style={{
        marginLeft: 6, fontSize: 9, fontWeight: 700, letterSpacing: '0.04em',
        textTransform: 'uppercase', padding: '1px 5px', borderRadius: 4,
        color: 'var(--dim)', border: '1px dashed var(--border)',
        background: 'transparent', whiteSpace: 'nowrap',
      }}
    >{tOr(t, 'inventory.source_assumed_badge', 'estimated')}</span>
  )
}

// ── Lead-time learning state ────────────────────────────────────────────────
// `resolve_lead_time` already replaces the configured lead time with the one
// learned from a supplier's real receptions — but only past
// MIN_LEAD_TIME_OBSERVATIONS of them, a bar a new tenant never clears. So every
// SKU quietly fell back to our assumption and nothing on screen said the
// learning existed at all.
//
// The status payload now carries, per SKU, how many of that supplier's
// deliveries we have recorded and how many we need. The threshold travels with
// the data: a literal here would be free to disagree with the number the
// planner actually applies, which is the same class of bug as the three
// coexisting lead-time defaults this whole change exists to remove.
interface LeadTimeLearningFields {
  lead_time_observations?:        number
  lead_time_observations_needed?: number
  lead_time_learned?:             number | null
}

/** "Todavía no tengo entregas tuyas de este proveedor…" — turns a silent
 *  fallback into something the buyer can wait for. Renders nothing when the
 *  backend did not ship the counters (older build): inventing a threshold is
 *  exactly the kind of unfounded number this change exists to remove. */
function LeadTimeLearning({ item }: { item: InventoryStatusItem & LeadTimeLearningFields }) {
  const { t } = useLanguage()
  const supplier = item.supplier

  if (!supplier) {
    return (
      <div style={learningStyle}>
        {tOr(t, 'inventory.lead_time_learning_no_supplier',
          'This product has no supplier, so we cannot learn its real lead time. Assign one and we start measuring.')}
      </div>
    )
  }

  const needed = item.lead_time_observations_needed
  if (needed == null) return null
  const seen = item.lead_time_observations ?? 0

  // 'learned' is the only state where the average is what the planner used;
  // below the threshold it exists but is deliberately ignored, so showing it
  // would advertise a number nothing acts on.
  if (item.lead_time_source === 'learned' && item.lead_time_learned != null) {
    return (
      <div style={{ ...learningStyle, color: C.green }}>
        {tOr(t, 'inventory.lead_time_learning_active',
          `Learned from ${seen} of your deliveries from ${supplier}: ${item.lead_time_learned} days on average, and that is the number we use.`,
          { supplier, n: seen, days: item.lead_time_learned })}
      </div>
    )
  }

  if (seen === 0) {
    return (
      <div style={learningStyle}>
        {tOr(t, 'inventory.lead_time_learning_none',
          `We have no deliveries from ${supplier} yet. Once you record ${needed}, we adjust the lead time on our own.`,
          { supplier, needed })}
      </div>
    )
  }

  return (
    <div style={learningStyle}>
      {tOr(t, 'inventory.lead_time_learning_partial',
        `${seen} of ${needed} deliveries from ${supplier} recorded. ${needed - seen} more and we adjust the lead time on our own.`,
        { supplier, n: seen, needed, missing: needed - seen })}
    </div>
  )
}

const learningStyle: React.CSSProperties = {
  fontSize: 11, color: C.dim, lineHeight: 1.5, marginTop: 8,
  paddingTop: 8, borderTop: `1px dashed ${C.border}`,
}

// ── The four numbers that decide the order, and who chose each ──────────────
function PlanningValues({ item }: {
  item: InventoryStatusItem & LeadTimeLearningFields
}) {
  const { t } = useLanguage()
  const rows: { label: string; value: string; source?: ValueSource | null; scope?: string | null }[] = [
    {
      label: t('inventory.col_lead_time'),
      value: `${item.lead_time_days} ${tOr(t, 'inventory.planning_days_unit', 'days')}`,
      source: item.lead_time_source, scope: item.lead_time_rule_scope,
    },
    {
      label: tOr(t, 'inventory.planning_service_level_label', 'Service level'),
      value: item.service_level != null ? `${Math.round(item.service_level * 100)}%` : '—',
      source: item.service_level_source, scope: item.service_level_rule_scope,
    },
    {
      label: tOr(t, 'inventory.planning_unit_cost_label', 'Unit cost'),
      value: item.unit_cost != null ? fmtCurrency(item.unit_cost) : '—',
      source: item.unit_cost_source,
    },
    {
      label: 'MOQ',
      value: fmt(item.moq, 0),
      source: item.moq_source, scope: item.moq_rule_scope,
    },
  ]

  return (
    <div style={{
      marginTop: 10, padding: '12px 16px', borderRadius: 9,
      background: C.card, border: `1px solid ${C.border}`,
    }}>
      <div style={{
        fontSize: 11, fontWeight: 700, color: C.dim, marginBottom: 8,
        textTransform: 'uppercase', letterSpacing: '0.06em',
      }}>
        {tOr(t, 'inventory.planning_values_title', 'Values behind this order')}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
        {rows.map(r => (
          <div key={r.label}>
            <div style={{ fontSize: 10, color: C.dim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              {r.label}
            </div>
            <div style={{ fontSize: 13, fontWeight: 700, color: C.text, marginTop: 2 }}>
              {r.value}<SourceBadge source={r.source} />
            </div>
            <div style={{ fontSize: 10, color: C.dim, marginTop: 1 }}>
              <ProvenanceNote source={r.source} scope={r.scope} />
            </div>
          </div>
        ))}
      </div>
      <LeadTimeLearning item={item} />
    </div>
  )
}

// ── Why is this being recommended? ──────────────────────────────────────────
function CalcExplainer({ exp, moq }: { exp: InventoryCalcExplanation; moq: number }) {
 const { t } = useLanguage()

 if (exp.suficiente) {
 return (
 <div style={{ fontSize: 11, color: 'var(--dim)', padding: '4px 0' }}>
 {t('inventory.calc_enough_stock')}
 </div>
 )
 }

 const unitWord = t('inventory.calc_unit_units')
 // Where the lead time came from, across all five real sources. It used to be
 // a learned/configured pair, which called an untouched SKU 'configured' — the
 // one label the product had no right to use.
 const leadSource = (exp.lead_time_source || 'default') as ValueSource
 const leadOrigin = leadSource === 'supplier_rule' && exp.lead_time_rule_scope
 ? `${t(sourceLabelKey(leadSource))} · ${t(`explain.scope_${exp.lead_time_rule_scope}`)}`
 : t(sourceLabelKey(leadSource))
 const steps = [
 { label: t('inventory.calc_step_avg_daily_sales'), value: `${exp.daily_demand!.toFixed(1)} ${t('inventory.calc_unit_per_day')}`, op: null },
 { label: `× ${t('inventory.calc_step_lead_days')} (${exp.lead_time_days}d${leadOrigin ? ` · ${leadOrigin}` : ''})`, value: `= ${exp.lead_time_demand!.toFixed(0)} ${unitWord}`, op: '×' },
 { label: `+ ${t('inventory.calc_step_safety_stock')}`, value: `+ ${exp.safety_stock!.toFixed(0)} ${unitWord}`, op: '+' },
 { label: `− ${t('inventory.calc_step_current_stock')}`, value: `− ${exp.current_stock!.toFixed(0)} ${unitWord}`, op: '−' },
 { label: `= ${t('inventory.calc_step_before_rounding')}`, value: `${exp.antes_moq!.toFixed(0)} ${unitWord}`, op: '=' },
 ...(moq > 1
 ? [{ label: `↑ ${t('inventory.calc_step_rounded_moq')} (${moq})`, value: `→ ${exp.final_qty!.toFixed(0)} ${unitWord}`, op: '↑' }]
 : []),
 ]

 return (
 <div style={{
 background: 'color-mix(in srgb, var(--accent) 4%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 15%, transparent)',
 borderRadius: 8, padding: '12px 16px', marginTop: 2,
 }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: C.indigo, marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
 {t('inventory.calc_title')}
 </div>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
 {steps.map(({ label, value }, i) => (
 <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16 }}>
 <span style={{ fontSize: 12, color: C.muted }}>{label}</span>
 <span style={{
 fontSize: 12, fontFamily: 'monospace', fontWeight: i === steps.length - 1 ? 700 : 500,
 color: i === steps.length - 1 ? C.green : C.text,
 }}>{value}</span>
 </div>
 ))}
 </div>
 <div style={{ marginTop: 10, paddingTop: 8, borderTop: `1px solid color-mix(in srgb, var(--accent) 15%, transparent)`, fontSize: 11, color: C.dim }}>
 {t('inventory.calc_footer_safety_stock')}
 </div>
 </div>
 )
}

// ── Plain-language situation ────────────────────────────────────────────────
function ContextMessage({ summary }: { summary: Record<string, number> }) {
 const { t } = useLanguage()
 const lines: { text: string; color: string }[] = []
 if (summary.order_now > 0)
 lines.push({ text: `${summary.order_now} ${t('inventory.ctx_order_now_suffix')}`, color: '#ef4444' })
 if (summary.order_soon > 0)
 lines.push({ text: `${summary.order_soon} ${t('inventory.ctx_order_soon_suffix')}`, color: '#f59e0b' })
 if (summary.overstock > 0)
 lines.push({ text: `${summary.overstock} ${t('inventory.ctx_overstock_suffix')}`, color: '#3b82f6' })
 if (!lines.length && summary.ok > 0)
 lines.push({ text: t('inventory.ctx_all_covered'), color: '#22c55e' })
 if (!lines.length) return null
 return (
 <div style={{ padding: '10px 16px', borderRadius: 9, background: C.surface, border: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column', gap: 4 }}>
 {lines.map(({ text, color }) => <span key={text} style={{ fontSize: 12, color }}>{text}</span>)}
 </div>
 )
}

// ── KPI card ──────────────────────────────────────────────────────────────────
function KPICard({ label, value, color, sub, onClick, active }: {
 label: string; value: React.ReactNode; color: string; sub?: string; onClick?: () => void; active?: boolean
}) {
 return (
 <div onClick={onClick} style={{ background: C.surface, border: `1px solid ${active ? color + '60' : C.border}`, borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${color}`, cursor: onClick ? 'pointer' : 'default', transition: 'border-color 0.15s' }}>
 <div style={{ fontSize: 22, fontWeight: 800, color, lineHeight: 1.1 }}>{value}</div>
 <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{label}</div>
 {sub && <div style={{ fontSize: 10, color: C.dim, marginTop: 2, opacity: 0.7 }}>{sub}</div>}
 </div>
 )
}

// ── Multiplier: explanation and per-product editing (feature 3.4) ───────────
// An event multiplier cannot be a number with no provenance: if Faro
// says "order 3x of this on Black Friday", the buyer has to be able to see
// where that 3x comes from and change it. And it is not a single number —
// electronics and milk behave differently — so it resolves SKU > category > event.

function MultiplierChip({ value, origin }: { value: number; origin: string }) {
 const { t } = useLanguage()
 const label = origin === 'sku' ? t('inventory.mult_origin_sku')
  : origin === 'family' ? t('inventory.mult_origin_family')
  : origin === 'category' ? t('inventory.mult_origin_category')
  : t('inventory.mult_origin_event')
 const custom = origin !== 'event'
 return (
  <span
   title={label}
   style={{
    display: 'inline-flex', alignItems: 'center', gap: 4,
    fontFamily: 'monospace', fontWeight: 700, fontSize: 11,
    padding: '2px 7px', borderRadius: 5,
    background: custom ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : 'transparent',
    color: custom ? C.indigo : C.muted,
   }}
  >
   x{value.toFixed(1)}
   <span style={{ fontWeight: 500, fontSize: 9, opacity: 0.85 }}>{label}</span>
  </span>
 )
}

function MultiplierExplainer({ result, eventId, onEdited }: {
 result: EventSimulationResult
 eventId: string
 onEdited: () => void
}) {
 const { t } = useLanguage()
 const [open, setOpen] = useState(false)
 const [rows, setRows] = useState<EventMultiplier[] | null>(null)
 const [form, setForm] = useState({ scope: 'category' as 'sku' | 'family' | 'category', value: '', mult: '2.0' })
 const [busy, setBusy] = useState(false)
 const [err, setErr] = useState('')
 const exp = result.explanation

 const load = useCallback(() => {
  listEventMultipliers(eventId).then(setRows).catch(() => setRows([]))
 }, [eventId])

 useEffect(() => { if (open && rows === null) load() }, [open, rows, load])

 async function save() {
  if (!form.value.trim()) return
  setBusy(true); setErr('')
  try {
   await setEventMultiplier(eventId, form.scope, form.value.trim(), parseFloat(form.mult) || 1)
   setForm(f => ({ ...f, value: '' }))
   load(); onEdited()
  } catch (e) { setErr(e instanceof Error ? e.message : t('inventory.mult_err_save')) }
  finally { setBusy(false) }
 }

 async function remove(id: string) {
  setBusy(true); setErr('')
  try { await deleteEventMultiplier(eventId, id); load(); onEdited() }
  catch (e) { setErr(e instanceof Error ? e.message : t('inventory.mult_err_delete')) }
  finally { setBusy(false) }
 }

 const inputS3: React.CSSProperties = {
  background: C.card, border: `1px solid ${C.border}`, borderRadius: 6,
  color: C.text, fontSize: 11, outline: 'none', padding: '5px 8px',
 }

 return (
  <div style={{ marginBottom: 16, padding: '12px 14px', borderRadius: 9, background: C.card, border: `1px solid ${C.border}` }}>
   <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
    <Info size={13} color={C.indigo} style={{ flexShrink: 0, marginTop: 2 }} aria-hidden="true" />
    <div style={{ flex: 1, minWidth: 0, fontSize: 11.5, color: C.muted, lineHeight: 1.55 }}>
     <strong style={{ color: C.text }}>
      {t('inventory.mult_base_label')} x{exp.base_multiplier.toFixed(1)}
     </strong>
     {' — '}
     {exp.es_estimacion ? t('inventory.mult_from_catalog') : t('inventory.mult_from_user')}
     {exp.reason && <div style={{ marginTop: 3 }}>{exp.reason}</div>}
     {exp.es_estimacion && (
      <div style={{ marginTop: 3, color: C.dim }}>{t('inventory.mult_estimate_hint')}</div>
     )}
     {result.multipliers_applied.length > 0 && (
      <div style={{ marginTop: 5, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
       {result.multipliers_applied.map(m => (
        <span key={`${m.multiplier}-${m.source}`} style={{ fontSize: 10, color: C.dim }}>
         <MultiplierChip value={m.multiplier} origin={m.source} /> {m.skus} SKU
        </span>
       ))}
      </div>
     )}
    </div>
    <button
     onClick={() => setOpen(v => !v)}
     aria-expanded={open}
     style={{ all: 'unset', cursor: 'pointer', flexShrink: 0, fontSize: 11, fontWeight: 600, color: C.indigo, padding: '2px 4px' }}
    >
     {open ? t('inventory.mult_btn_hide') : t('inventory.mult_btn_adjust')}
    </button>
   </div>

   {open && (
    <div style={{ marginTop: 12, paddingTop: 10, borderTop: `1px solid ${C.border}` }}>
     <div style={{ fontSize: 11, color: C.dim, marginBottom: 8 }}>
      {t('inventory.mult_edit_hint')}
     </div>

     {rows && rows.length > 0 && (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginBottom: 10 }}>
       {rows.map(o => (
        <div key={o.id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11 }}>
         <span style={{ color: C.dim, width: 68 }}>
          {o.scope === 'sku' ? t('inventory.mult_origin_sku') : t('inventory.mult_origin_category')}
         </span>
         <span style={{ flex: 1, color: C.text, fontFamily: 'monospace' }}>{o.scope_value}</span>
         <span style={{ fontFamily: 'monospace', fontWeight: 700, color: C.indigo }}>x{o.multiplier.toFixed(1)}</span>
         <button
          onClick={() => remove(o.id)}
          disabled={busy}
          aria-label={`${t('inventory.mult_btn_remove')}: ${o.scope_value}`}
          title={t('inventory.mult_btn_remove')}
          style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 3 }}
         >
          <Trash2 size={11} aria-hidden="true" />
         </button>
        </div>
       ))}
      </div>
     )}

     <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
      <select
       name="demand_mult_scope"
       value={form.scope}
       onChange={e => setForm(f => ({ ...f, scope: e.target.value as 'sku' | 'family' | 'category' }))}
       aria-label={t('inventory.mult_scope_label')}
       style={{ ...inputS3, width: 110 }}
      >
       <option value="category">{t('inventory.mult_origin_category')}</option>
       <option value="family">{t('inventory.mult_origin_family')}</option>
       <option value="sku">{t('inventory.mult_origin_sku')}</option>
      </select>
      <input
       name="demand_mult_value"
       value={form.value}
       onChange={e => setForm(f => ({ ...f, value: e.target.value }))}
       placeholder={
        form.scope === 'sku'    ? t('inventory.mult_ph_sku')
        : form.scope === 'family' ? t('inventory.mult_ph_family')
        : t('inventory.mult_ph_category')
       }
       aria-label={t('inventory.mult_value_label')}
       style={{ ...inputS3, flex: 1, minWidth: 130 }}
      />
      <input
       name="demand_mult_multiplier"
       type="number" step="0.1" min="0.1" max="10"
       value={form.mult}
       onChange={e => setForm(f => ({ ...f, mult: e.target.value }))}
       aria-label={t('inventory.mult_value_multiplier')}
       style={{ ...inputS3, width: 70 }}
      />
      <button
       onClick={save}
       disabled={busy || !form.value.trim()}
       style={{ all: 'unset', cursor: busy || !form.value.trim() ? 'not-allowed' : 'pointer', padding: '5px 12px', borderRadius: 6, background: C.indigo, color: '#fff', fontSize: 11, fontWeight: 600, opacity: busy || !form.value.trim() ? 0.5 : 1 }}
      >
       {t('inventory.mult_btn_apply')}
      </button>
     </div>
     {err && <div style={{ marginTop: 6, fontSize: 11, color: C.red }}>{err}</div>}
    </div>
   )}
  </div>
 )
}

// Renders *emphasis* markers coming from the i18n catalog as <strong>. Keeps a
// whole sentence — including where the emphasis falls — inside ONE catalog
// entry, instead of splintering it into fragments no translator can reorder.
function Emphasized({ text }: { text: string }) {
 return (
  <>
   {text.split(/\*([^*]+)\*/g).map((part, i) => (
    i % 2 === 1 ? <strong key={i}>{part}</strong> : <span key={i}>{part}</span>
   ))}
  </>
 )
}

// ── Event impact simulator modal (feature 2.3) ───────────────────────────────
function EventSimModal({ ev, sessionId, onClose, onReload }: {
 ev: InventoryEvent
 sessionId: string
 onClose: () => void
 onReload: () => void
}) {
 const { t, lang } = useLanguage()
 const [result, setResult] = useState<EventSimulationResult | null>(null)
 const [error, setError] = useState<string | null>(null)

 useEffect(() => {
 simulateEvent({ session_id: sessionId, event_id: ev.id })
 .then(setResult)
 .catch(e => setError(e instanceof Error ? e.message : t('inventory.sim_err_failed')))
 // `t` is stable per language and re-running the simulation on a language
 // switch would be a pointless request.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [ev.id, sessionId])

 const fmtD = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString(localeFor(lang), { day: 'numeric', month: 'long' })
 const pctExtra = Math.round((ev.multiplier - 1) * 100)

 return (
 <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
 <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 640, maxHeight: '85vh', overflowY: 'auto', background: C.surface, border: `1px solid ${C.border}`, borderRadius: 14, padding: 24 }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
 <Zap size={16} color={C.amber} />
 <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>
 {tOr(t, 'inventory.sim_modal_title', `Simulation: ${ev.name}`, { event: ev.name })}
 </span>
 <button onClick={onClose} aria-label={t('common.close')} style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', color: C.dim }}><X size={16} aria-hidden="true" /></button>
 </div>
 <p style={{ margin: '0 0 16px', fontSize: 12, color: C.dim }}>
 {fmtD(ev.start_date)} → {fmtD(ev.end_date)} · {tOr(t, 'inventory.sim_modal_uplift', `estimated demand +${pctExtra}%`, { pct: pctExtra })}
 </p>

 {!result && !error && <div style={{ padding: 24, textAlign: 'center' }}><Spinner size={16} /></div>}
 {error && <div style={{ padding: '10px 14px', borderRadius: 8, background: 'rgba(239,68,68,0.08)', fontSize: 13, color: C.red }}>{error}</div>}

 {result && (
 <>
 {/* Headline — the actionable sentence */}
 <div style={{
 padding: '14px 18px', borderRadius: 10, marginBottom: 16,
 background: result.summary.skus_at_risk > 0 ? 'rgba(245,158,11,0.07)' : 'rgba(34,197,94,0.07)',
 border: `1px solid ${result.summary.skus_at_risk > 0 ? 'rgba(245,158,11,0.3)' : 'rgba(34,197,94,0.3)'}`,
 fontSize: 13, color: C.text, lineHeight: 1.6,
 }}>
 {result.summary.skus_at_risk > 0 ? (
 <>
 <Emphasized text={tOr(
  t,
  result.summary.skus_at_risk === 1 ? 'inventory.sim_headline_risk_one' : 'inventory.sim_headline_risk_many',
  `With *${ev.name}* (+${pctExtra}% demand) you would need to order *${result.summary.total_to_order.toLocaleString()} extra units* across *${result.summary.skus_at_risk} ${result.summary.skus_at_risk === 1 ? 'product' : 'products'}*.`,
  {
   event: ev.name,
   pct:   pctExtra,
   units: result.summary.total_to_order.toLocaleString(),
   skus:  result.summary.skus_at_risk,
  },
 )} />
 {result.summary.order_before && (
  <Emphasized text={tOr(t, 'inventory.sim_headline_order_before',
   ` Order before *${fmtD(result.summary.order_before)}*.`,
   { date: fmtD(result.summary.order_before) })} />
 )}
 {result.summary.total_order_value > 0 && (
  <Emphasized text={tOr(t, 'inventory.sim_headline_value',
   ` About *${formatMoney(result.summary.total_order_value)}*.`,
   { value: formatMoney(result.summary.total_order_value) })} />
 )}
 {result.summary.any_order_late && (
 <div style={{ color: C.red, fontWeight: 600, marginTop: 6 }}>
 {tOr(t, 'inventory.sim_headline_late',
  'For some products it is already too late: ordering today, the shipment would arrive with the event under way.')}
 </div>
 )}
 </>
 ) : (
 <Emphasized text={tOr(t, 'inventory.sim_headline_safe',
  `Your current stock survives *${ev.name}* (+${pctExtra}% demand) with no extra orders.`,
  { event: ev.name, pct: pctExtra })} />
 )}
 </div>

 {/* Why this multiplier — never show a x2.2 without justifying it */}
 <MultiplierExplainer result={result} eventId={ev.id} onEdited={onReload} />

 <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
 <thead>
 <tr>
 {[t('inventory.sim_col_product'), t('inventory.sim_col_multiplier'), t('inventory.sim_col_event_demand'), t('inventory.sim_col_stock_at_start'), t('inventory.sim_col_shortfall'), t('inventory.sim_col_order'), t('inventory.sim_col_order_before')].map(h => (
 <th key={h} scope="col" style={{ textAlign: 'left', padding: '6px 8px', color: C.dim, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em', borderBottom: `1px solid ${C.border}` }}>{h}</th>
 ))}
 </tr>
 </thead>
 <tbody>
 {result.items.map(r => (
 <tr key={r.sku} style={{ borderBottom: `1px solid ${C.border}`, background: r.en_risk ? 'rgba(245,158,11,0.04)' : undefined }}>
 <th scope="row" style={{ padding: '8px', textAlign: 'left', fontWeight: 400 }}>
 <div style={{ fontWeight: 600, color: C.text }}>{r.display_name || r.sku}</div>
 <div style={{ fontSize: 10, color: C.dim, fontFamily: 'monospace' }}>{r.sku}</div>
 </th>
 <td style={{ padding: '8px' }}>
 <MultiplierChip value={r.multiplier} origin={r.multiplier_source} />
 </td>
 <td style={{ padding: '8px', fontFamily: 'monospace', color: C.text }}>
 {r.event_units.toLocaleString()}
 <span style={{ color: C.dim, fontSize: 10 }}> (+{r.extra_units.toLocaleString()})</span>
 </td>
 <td style={{ padding: '8px', fontFamily: 'monospace', color: C.muted }}>
 {r.stock_al_inicio != null ? r.stock_al_inicio.toLocaleString() : '—'}
 </td>
 <td style={{ padding: '8px', fontFamily: 'monospace', fontWeight: 700, color: r.en_risk ? C.red : C.green }}>
 {r.deficit != null ? (r.deficit > 0 ? r.deficit.toLocaleString() : <CheckCircle2 size={13} aria-hidden="true" />) : '—'}
 </td>
 <td style={{ padding: '8px', fontFamily: 'monospace', fontWeight: 700, color: r.qty_to_order ? C.text : C.dim }}>
 {r.qty_to_order ? r.qty_to_order.toLocaleString() : '—'}
 </td>
 <td style={{ padding: '8px', fontSize: 11, color: r.llega_tarde && r.en_risk ? C.red : C.muted, whiteSpace: 'nowrap' }}>
 {r.en_risk ? (r.llega_tarde ? tOr(t, 'inventory.sim_order_today', 'today!') : fmtD(r.order_by)) : '—'}
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 <p style={{ margin: '14px 0 0', fontSize: 11, color: C.dim, lineHeight: 1.5 }}>
 {tOr(t, 'inventory.sim_footer_note',
  `Calculation: forecast daily demand × ${result.event_days} days × ${ev.multiplier.toFixed(1)}, against the stock projected at the start of the event. Quantities respect each product's MOQ. Nothing is saved — this is only a simulation.`,
  {
   days: `${result.event_days} ${tOr(t, result.event_days === 1 ? 'inventory.sim_day_one' : 'inventory.sim_day_many', result.event_days === 1 ? 'day' : 'days')}`,
   mult: ev.multiplier.toFixed(1),
  },
 )}
 </p>
 </>
 )}
 </div>
 </div>
 )
}

// ── Shrinkage modal (record a non-sale stock-out: breakage/expiry/self-consumption/gift) ──
const SHRINKAGE_REASONS: ShrinkageReason[] = ['breakage', 'expiry', 'self_consumption', 'gift']

function ShrinkageModal({ items, onClose, onSaved }: {
 items: InventoryStatusItem[]
 onClose: () => void
 onSaved: () => void
}) {
 const { t } = useLanguage()
 const { addToast } = useToast()
 const [sku, setSku] = useState('')
 const [quantity, setQuantity] = useState('')
 const [reason, setReason] = useState<ShrinkageReason>('breakage')
 const [notes, setNotes] = useState('')
 const [saving, setSaving] = useState(false)
 const [error, setError] = useState<string | null>(null)

 const selected = items.find(i => i.sku === sku) || null
 const qtyNum = parseFloat(quantity)
 const estCost = selected?.unit_cost != null && !isNaN(qtyNum) && qtyNum > 0
  ? qtyNum * selected.unit_cost
  : null

 const inputM: React.CSSProperties = { background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, color: C.text, fontSize: 12, outline: 'none', padding: '7px 9px', width: '100%', boxSizing: 'border-box' }

 async function handleSubmit() {
  setError(null)
  if (!selected) { setError(t('inventory.shrinkage_err_select_sku')); return }
  if (!qtyNum || qtyNum <= 0) { setError(t('inventory.shrinkage_err_quantity')); return }
  setSaving(true)
  try {
   await createShrinkage({ sku: selected.sku, quantity: qtyNum, reason, notes: notes || undefined })
   addToast(t('inventory.shrinkage_toast_title'), `${qtyNum} ${t('inventory.calc_unit_units')} ${t('inventory.shrinkage_toast_body')} ${selected.sku}`, 'success')
   onSaved()
   onClose()
  } catch (e) {
   setError(e instanceof Error ? e.message : String(e))
  } finally {
   setSaving(false)
  }
 }

 return (
  <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
   <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 460, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 14, padding: 24 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
     <PackageMinus size={16} color={C.red} />
     <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>{t('inventory.shrinkage_title_register')}</span>
     <button onClick={onClose} aria-label={t('common.close')} style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', color: C.dim }}><X size={16} aria-hidden="true" /></button>
    </div>
    <p style={{ margin: '0 0 16px', fontSize: 12, color: C.dim, lineHeight: 1.5 }}>{t('inventory.shrinkage_subtitle')}</p>

    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
     <div>
      <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>{t('inventory.shrinkage_field_sku')}</div>
      <input
       id="shrinkage-sku"
       name="shrinkage_sku"
       aria-label={t('inventory.shrinkage_field_sku')}
       list="shrinkage-sku-options"
       style={inputM}
       placeholder={t('inventory.shrinkage_sku_placeholder')}
       value={sku}
       onChange={e => setSku(e.target.value)}
       autoFocus
      />
      <datalist id="shrinkage-sku-options">
       {items.map(i => (
        <option key={i.sku} value={i.sku}>{i.display_name || i.sku}</option>
       ))}
      </datalist>
      {selected && (
       <div style={{ marginTop: 4, fontSize: 11, color: C.dim }}>
        {t('inventory.shrinkage_current_stock_prefix')} <strong style={{ color: C.text }}>{fmt(selected.current_stock, 0)}</strong>
       </div>
      )}
     </div>

     <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
      <div>
       <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>{t('inventory.shrinkage_field_quantity')}</div>
       <input id="shrinkage-quantity" name="shrinkage_quantity" aria-label={t('inventory.shrinkage_field_quantity')} type="number" min={0} step="any" style={inputM} value={quantity} onChange={e => setQuantity(e.target.value)} />
      </div>
      <div>
       <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>{t('inventory.shrinkage_field_reason')}</div>
       <select id="shrinkage-reason" name="shrinkage_reason" aria-label={t('inventory.shrinkage_field_reason')} style={inputM} value={reason} onChange={e => setReason(e.target.value as ShrinkageReason)}>
        {SHRINKAGE_REASONS.map(r => <option key={r} value={r}>{t(`inventory.shrinkage_reason_${r}`)}</option>)}
       </select>
      </div>
     </div>

     <div>
      <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>{t('inventory.shrinkage_field_notes')}</div>
      <input id="shrinkage-notes" name="shrinkage_notes" aria-label={t('inventory.shrinkage_field_notes')} style={inputM} placeholder={t('inventory.shrinkage_notes_placeholder')} value={notes} onChange={e => setNotes(e.target.value)} />
     </div>

     {estCost != null && (
      <div style={{ fontSize: 11, color: C.dim }}>
       {t('inventory.shrinkage_estimated_cost_prefix')} <strong style={{ color: C.red }}>{fmtCurrency(estCost)}</strong>
      </div>
     )}

     {error && (
      <div style={{ padding: '8px 12px', borderRadius: 8, background: 'rgba(239,68,68,0.08)', fontSize: 12, color: C.red }}>{error}</div>
     )}

     <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
      <button onClick={onClose} style={{ all: 'unset', cursor: 'pointer', padding: '7px 14px', borderRadius: 8, border: `1px solid ${C.border}`, color: C.dim, fontSize: 12 }}>{t('common.cancel')}</button>
      <button onClick={handleSubmit} disabled={saving} style={{ all: 'unset', cursor: saving ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 16px', borderRadius: 8, background: C.red, color: '#fff', fontSize: 12, fontWeight: 600, opacity: saving ? 0.6 : 1 }}>
       {saving && <Spinner size={12} />} {saving ? t('inventory.shrinkage_btn_submitting') : t('inventory.shrinkage_btn_submit')}
      </button>
     </div>
    </div>
   </div>
  </div>
 )
}

// ── LatAm calendar catalog (feature 3.4) ─────────────────────────────────────
const COUNTRY_NAMES: Record<string, string> = { CR: 'Costa Rica', CO: 'Colombia' }
// The catalog is seeded into the DB (not a frontend array): this only
// shows its state and switches each event on/off.
function CalendarCatalogPanel({ onSeeded }: { onSeeded: () => void }) {
 const { t, lang } = useLanguage()
 const [entries, setEntries] = useState<CalendarCatalogEntry[] | null>(null)
 const [countries, setCountries] = useState<string[]>([])
 const [country, setCountry] = useState('')   // '' = default del backend (CR)
 const [busy, setBusy] = useState<string | null>(null)
 const [err, setErr] = useState('')

 const load = useCallback(() => {
  getCalendarCatalog(country || undefined)
   .then(r => { setEntries(r.entries); setCountries(r.countries); setCountry(c => c || r.country) })
   .catch(e => setErr(e instanceof Error ? e.message : String(e)))
 }, [country])

 useEffect(() => { load() }, [load])

 async function handleSeed() {
  setBusy('__seed__'); setErr('')
  try {
   await seedCalendarCatalog(country || undefined)
   load(); onSeeded()
  } catch (e) { setErr(e instanceof Error ? e.message : t('inventory.calendar_err_seed')) }
  finally { setBusy(null) }
 }

 async function handleToggle(entry: CalendarCatalogEntry) {
  setBusy(entry.key); setErr('')
  const next = !entry.active
  // Optimista: el toggle debe sentirse inmediato.
  setEntries(prev => prev?.map(e => e.key === entry.key ? { ...e, active: next } : e) ?? null)
  try {
   await toggleCalendarEntry(entry.key, next)
   onSeeded()
  } catch (e) {
   setEntries(prev => prev?.map(x => x.key === entry.key ? { ...x, active: entry.active } : x) ?? null)
   setErr(e instanceof Error ? e.message : t('inventory.calendar_err_toggle'))
  } finally { setBusy(null) }
 }

 if (!entries) return <div style={{ fontSize: 12, color: C.dim, padding: '10px 0' }}>{t('common.loading')}</div>

 const anySeeded = entries.some(e => e.seeded)

 return (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
   <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
    <div style={{ fontSize: 11, color: C.dim, lineHeight: 1.5 }}>
     {t('inventory.calendar_intro')}
     {countries.length > 1 && (
      <>
       {' '}
       <label htmlFor="cal-country" style={{ marginLeft: 4 }}>{t('inventory.calendar_country')}</label>{' '}
       <select
        id="cal-country"
        value={country}
        onChange={e => { setEntries(null); setCountry(e.target.value) }}
        style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 5, color: C.text, fontSize: 11, padding: '2px 5px' }}
       >
        {countries.map(c => <option key={c} value={c}>{COUNTRY_NAMES[c] ?? c}</option>)}
       </select>
      </>
     )}
   </div>
    {!anySeeded && (
     <button
      onClick={handleSeed}
      disabled={busy === '__seed__'}
      style={{ all: 'unset', cursor: busy ? 'wait' : 'pointer', flexShrink: 0, padding: '6px 12px', borderRadius: 7, background: C.indigo, color: '#fff', fontSize: 12, fontWeight: 600, opacity: busy === '__seed__' ? 0.6 : 1 }}
     >
      {busy === '__seed__' ? t('inventory.calendar_seeding') : t('inventory.calendar_btn_load')}
     </button>
    )}
   </div>

   {err && <div style={{ fontSize: 11, color: C.red }}>{err}</div>}

   {entries.map(entry => {
    const on = entry.seeded && entry.active
    return (
     <div key={entry.key} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', borderRadius: 8, background: C.card, border: `1px solid ${C.border}`, opacity: entry.seeded ? 1 : 0.55 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
       <div style={{ fontSize: 12.5, fontWeight: 600, color: C.text }}>{entry.name}</div>
       <div style={{ fontSize: 10.5, color: C.dim, marginTop: 2, lineHeight: 1.45 }}>{entry.notes}</div>
       <div style={{ fontSize: 10, color: C.dim, marginTop: 3 }}>
        {entry.seeded
         ? <>×{entry.multiplier.toFixed(1)} · {entry.occurrences} {t('inventory.calendar_occurrences')}
            {entry.next_start && <> · {t('inventory.calendar_next')} {new Date(entry.next_start + 'T00:00:00').toLocaleDateString(localeFor(lang), { day: 'numeric', month: 'short', year: 'numeric' })}</>}
           </>
         : t('inventory.calendar_not_loaded')}
       </div>
      </div>
      <button
       role="switch"
       aria-checked={on}
       aria-label={`${on ? t('inventory.calendar_toggle_off_aria') : t('inventory.calendar_toggle_on_aria')}: ${entry.name}`}
       disabled={!entry.seeded || busy === entry.key}
       onClick={() => handleToggle(entry)}
       style={{
        all: 'unset', flexShrink: 0,
        cursor: !entry.seeded ? 'not-allowed' : busy === entry.key ? 'wait' : 'pointer',
        width: 38, height: 21, borderRadius: 11, position: 'relative',
        background: on ? C.indigo : C.border,
        transition: 'background 0.15s',
       }}
      >
       <span style={{
        position: 'absolute', top: 3, left: on ? 20 : 3,
        width: 15, height: 15, borderRadius: '50%', background: '#fff',
        transition: 'left 0.15s',
       }} />
      </button>
      {/* Estado en texto: el toggle no puede comunicarse sólo por posición/color. */}
      <span style={{ fontSize: 10, fontWeight: 600, width: 52, textAlign: 'right', flexShrink: 0, color: on ? C.indigo : C.dim }}>
       {entry.seeded ? (on ? t('inventory.calendar_state_on') : t('inventory.calendar_state_off')) : '—'}
      </span>
     </div>
    )
   })}

   {anySeeded && (
    <button
     onClick={handleSeed}
     disabled={busy === '__seed__'}
     style={{ all: 'unset', cursor: 'pointer', fontSize: 11, color: C.dim, padding: '5px 0', textAlign: 'center' }}
    >
     {t('inventory.calendar_btn_refresh')}
    </button>
   )}
  </div>
 )
}

// ── Events panel ─────────────────────────────────────────────────────────────
function EventsPanel({ events, onAdd, onDelete, onSimulate, onCatalogChange }: {
 events: InventoryEvent[]
 onAdd: (ev: Omit<InventoryEvent, 'id' | 'tenant_id' | 'created_at'>) => void
 onDelete: (id: string) => void
 onSimulate: (ev: InventoryEvent) => void
 onCatalogChange: () => void
}) {
 const { t, lang } = useLanguage()
 const [adding, setAdding] = useState(false)
 const [tab, setTab] = useState<'mine' | 'catalog'>('mine')
 const [form, setForm] = useState({ name: '', start_date: '', end_date: '', multiplier: '1.5', notes: '' })

 const visible = events.filter(e => e.active !== false)
 const upcoming = visible.filter(e => new Date(e.end_date) >= new Date())
 const past = visible.filter(e => new Date(e.end_date) < new Date())

 function handleAdd() {
 if (!form.name || !form.start_date || !form.end_date) return
 onAdd({ name: form.name, start_date: form.start_date, end_date: form.end_date, multiplier: parseFloat(form.multiplier) || 1.5, notes: form.notes || null })
 setForm({ name: '', start_date: '', end_date: '', multiplier: '1.5', notes: '' })
 setAdding(false)
 }

 const inputS2: React.CSSProperties = { background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, color: C.text, fontSize: 12, outline: 'none', padding: '6px 9px', width: '100%', boxSizing: 'border-box' }

 const TODAY_LABEL = t('inventory.day_today')
 const TOMORROW_LABEL = t('inventory.day_tomorrow')
 const daysUntil = (date: string) => {
 const d = Math.round((new Date(date).getTime() - Date.now()) / 86400000)
 if (d < 0) return null
 if (d === 0) return TODAY_LABEL
 if (d === 1) return TOMORROW_LABEL
 return `${t('inventory.day_in_prefix')} ${d} ${t('inventory.day_in_suffix')}`
 }

 const tabS = (active: boolean): React.CSSProperties => ({
  all: 'unset', cursor: 'pointer', padding: '5px 12px', borderRadius: 7,
  fontSize: 11.5, fontWeight: 600,
  background: active ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : 'transparent',
  color: active ? C.indigo : C.dim,
 })

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
 {/* Mis events vs. calendar LatAm precargado */}
 <div role="tablist" aria-label={t('inventory.events_tablist_aria')} style={{ display: 'flex', gap: 4, marginBottom: 2 }}>
  <button role="tab" aria-selected={tab === 'mine'} onClick={() => setTab('mine')} style={tabS(tab === 'mine')}>
   {t('inventory.events_tab_mine')}
  </button>
  <button role="tab" aria-selected={tab === 'catalog'} onClick={() => setTab('catalog')} style={tabS(tab === 'catalog')}>
   {t('inventory.events_tab_calendar')}
  </button>
 </div>

 {tab === 'catalog' ? <CalendarCatalogPanel onSeeded={onCatalogChange} /> : <>
 {/* Upcoming */}
 {upcoming.map(ev => {
 const until = daysUntil(ev.start_date)
 const isClose = until && ![TODAY_LABEL, TOMORROW_LABEL].includes(until) ? parseInt(until) <= 14 : !!until
 return (
 <div key={ev.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderRadius: 8, background: isClose ? 'rgba(245,158,11,0.06)' : C.card, border: `1px solid ${isClose ? 'rgba(245,158,11,0.25)' : C.border}` }}>
 <Calendar size={14} color={isClose ? C.amber : C.dim} style={{ flexShrink: 0 }} />
 <div style={{ flex: 1, minWidth: 0 }}>
 <div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{ev.name}</div>
 <div style={{ fontSize: 11, color: C.dim, marginTop: 1 }}>
 {new Date(ev.start_date).toLocaleDateString(localeFor(lang), { day: 'numeric', month: 'short' })}
 {ev.end_date !== ev.start_date && ` → ${new Date(ev.end_date).toLocaleDateString(localeFor(lang), { day: 'numeric', month: 'short' })}`}
 {until && <span style={{ marginLeft: 8, color: isClose ? C.amber : C.dim }}>({until})</span>}
 </div>
 </div>
 <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 20, background: 'color-mix(in srgb, var(--accent) 10%, transparent)', color: C.indigo, flexShrink: 0 }}>
 ×{ev.multiplier.toFixed(1)}
 </span>
 <button
 onClick={() => onSimulate(ev)}
 title={t('inventory.events_simulate_tooltip')}
 aria-label={`${t('inventory.events_btn_simulate')}: ${ev.name}`}
 style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px', borderRadius: 7, border: `1px solid rgba(245,158,11,0.4)`, color: 'var(--signal-order-soon-fg)', fontSize: 11, fontWeight: 600, flexShrink: 0 }}
 >
 <Zap size={11} aria-hidden="true" /> {t('inventory.events_btn_simulate')}
 </button>
 <button
 onClick={() => onDelete(ev.id)}
 aria-label={`${t('inventory.events_btn_delete')}: ${ev.name}`}
 title={t('inventory.events_btn_delete')}
 style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 4 }}
 onMouseEnter={e => (e.currentTarget.style.color = C.red)}
 onMouseLeave={e => (e.currentTarget.style.color = C.dim)}
 >
 <Trash2 size={12} aria-hidden="true" />
 </button>
 </div>
 )
 })}

 {upcoming.length === 0 && !adding && (
 <div style={{ fontSize: 12, color: C.dim, textAlign: 'center', padding: '12px 0' }}>
 {t('inventory.events_empty_state')}
 </div>
 )}

 {/* Add form */}
 {adding ? (
 <div style={{ padding: '12px 14px', borderRadius: 8, background: C.card, border: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column', gap: 10 }}>
 <input style={inputS2} name="event_name" aria-label={t('inventory.events_name_placeholder')} placeholder={t('inventory.events_name_placeholder')} value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} autoFocus />
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
 <div>
 <div style={{ fontSize: 10, color: C.dim, marginBottom: 3 }}>{t('inventory.events_start_date')}</div>
 <input style={inputS2} name="event_start_date" aria-label={t('inventory.events_start_date')} type="date" value={form.start_date} onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))} />
 </div>
 <div>
 <div style={{ fontSize: 10, color: C.dim, marginBottom: 3 }}>{t('inventory.events_end_date')}</div>
 <input style={inputS2} name="event_end_date" aria-label={t('inventory.events_end_date')} type="date" value={form.end_date} onChange={e => setForm(f => ({ ...f, end_date: e.target.value }))} />
 </div>
 <div>
 <div style={{ fontSize: 10, color: C.dim, marginBottom: 3 }}>{t('inventory.events_multiplier')}</div>
 <select style={inputS2} name="event_multiplier" aria-label={t('inventory.events_multiplier')} value={form.multiplier} onChange={e => setForm(f => ({ ...f, multiplier: e.target.value }))}>
 <option value="1.2">×1.2 — {t('inventory.events_mult_mild')} (+20%)</option>
 <option value="1.5">×1.5 — {t('inventory.events_mult_moderate')} (+50%)</option>
 <option value="2.0">×2.0 — {t('inventory.events_mult_high')} (+100%)</option>
 <option value="2.5">×2.5 — {t('inventory.events_mult_very_high')} (+150%)</option>
 <option value="3.0">×3.0 — {t('inventory.events_mult_peak')} (+200%)</option>
 </select>
 </div>
 </div>
 <input style={inputS2} name="event_notes" aria-label={t('inventory.events_notes_placeholder')} placeholder={t('inventory.events_notes_placeholder')} value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
 <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
 <button onClick={() => setAdding(false)} style={{ all: 'unset', cursor: 'pointer', padding: '6px 12px', borderRadius: 6, border: `1px solid ${C.border}`, color: C.dim, fontSize: 12 }}>{t('common.cancel')}</button>
 <button onClick={handleAdd} disabled={!form.name || !form.start_date || !form.end_date} style={{ all: 'unset', cursor: 'pointer', padding: '6px 14px', borderRadius: 6, background: C.indigo, color: '#fff', fontSize: 12, fontWeight: 600, opacity: !form.name || !form.start_date || !form.end_date ? 0.5 : 1 }}>{t('inventory.events_btn_save')}</button>
 </div>
 </div>
 ) : (
 <button onClick={() => setAdding(true)} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, border: `1px dashed ${C.border}`, color: C.dim, fontSize: 12, justifyContent: 'center' }}>
 <Plus size={12} /> {t('inventory.events_btn_add')}
 </button>
 )}

 {past.length > 0 && (
 <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>
 {past.length} {past.length > 1 ? t('inventory.events_past_count_suffix_plural') : t('inventory.events_past_count_suffix_singular')}
 </div>
 )}
 </>}
 </div>
 )
}

// ── Inline edit state ─────────────────────────────────────────────────────────
interface EditState { current_stock: string; lead_time_days: string; unit_cost: string; moq: string; supplier: string; display_name: string; service_level: string; sale_price: string; category: string; family: string; brand: string; unit_of_measure: string; barcode: string }
function rowToEdit(item: InventoryStatusItem): EditState {
 return { current_stock: String(item.current_stock ?? ''), lead_time_days: String(item.lead_time_days ?? DEFAULT_LEAD_TIME_DAYS), unit_cost: String(item.unit_cost ?? ''), moq: String(item.moq ?? DEFAULT_MOQ), supplier: item.supplier ?? '', display_name: item.display_name ?? '', service_level: String(item.service_level ?? DEFAULT_SERVICE_LEVEL), sale_price: String(item.sale_price ?? ''), category: item.category ?? '', family: item.family ?? '', brand: item.brand ?? '', unit_of_measure: item.unit_of_measure ?? '', barcode: item.barcode ?? '' }
}
const inputS: React.CSSProperties = { background: 'var(--surface-2)', border: `1px solid var(--border)`, borderRadius: 5, color: 'var(--text)', fontSize: 12, outline: 'none', padding: '3px 7px', width: '100%', boxSizing: 'border-box' }

// ── Provider group ────────────────────────────────────────────────────────────
function ProviderGroup({ name, items, onEdit, editedQty, editingQtySku, setEditedQty, setEditingQtySku, effectiveQty, coverageUnit }: {
 name: string; items: InventoryStatusItem[]; onEdit: (item: InventoryStatusItem) => void
 editedQty: Record<string, number>
 editingQtySku: string | null
 setEditedQty: React.Dispatch<React.SetStateAction<Record<string, number>>>
 setEditingQtySku: React.Dispatch<React.SetStateAction<string | null>>
 effectiveQty: (item: InventoryStatusItem) => number
 coverageUnit?: CoverageUnit
}) {
 const { t } = useLanguage()
 const [open, setOpen] = useState(true)
 const critical = items.filter(i => i.signal === 'PEDIR_YA').length
 const warning = items.filter(i => i.signal === 'PEDIR_PRONTO').length
 return (
 <div style={{ border: `1px solid ${C.border}`, borderRadius: 10, overflow: 'hidden', marginBottom: 10 }}>
 <div onClick={() => setOpen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', background: C.card, cursor: 'pointer', borderBottom: open ? `1px solid ${C.border}` : 'none' }}>
 <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{name || t('inventory.no_provider')}</span>
 <span style={{ fontSize: 11, color: C.dim }}>{items.length} SKUs</span>
 {critical > 0 && <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: 'rgba(239,68,68,0.1)', color: C.red }}>{critical} {critical !== 1 ? t('inventory.urgent_plural') : t('inventory.urgent_singular')}</span>}
 {warning > 0 && <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: 'rgba(245,158,11,0.1)', color: C.amber }}>{warning} {t('inventory.soon_suffix')}</span>}
 <ChevronDown size={13} color={C.dim} style={{ transform: open ? 'rotate(180deg)' : undefined, transition: 'transform 0.2s' }} />
 </div>
 {open && items.map((item, idx) => (
 <div key={item.sku} style={{ display: 'grid', gridTemplateColumns: '160px 100px 90px 90px 80px 60px auto', gap: 12, padding: '10px 16px', alignItems: 'center', fontSize: 12, background: idx % 2 === 0 ? C.surface : C.card, borderBottom: idx < items.length - 1 ? `1px solid ${C.border}` : 'none' }}>
 <div><div style={{ fontFamily: 'monospace', fontWeight: 600, fontSize: 11 }}>{item.sku}</div>{item.display_name && <div style={{ color: C.dim, fontSize: 10 }}>{item.display_name}</div>}</div>
 <SignalBadge s={item.signal} />
 <span style={{ color: signalColor(item.signal), fontWeight: 600 }}>{item.coverage_days != null ? `${item.coverage_days.toFixed(0)} ${coverageUnitShort(coverageUnit, t)}` : '—'}</span>
 <span>
 {item.recommended_qty != null && item.recommended_qty > 0 ? (
 editingQtySku === item.sku ? (
 <input
 type="number" min={1} autoFocus
 name={`order-qty-${item.sku}`} aria-label={t('inventory.edit_qty_title')}
 defaultValue={effectiveQty(item)}
 onClick={e => e.stopPropagation()}
 onBlur={e => {
 const n = parseInt(e.target.value, 10)
 setEditedQty(prev => (!isNaN(n) && n > 0 ? { ...prev, [item.sku]: n } : prev))
 setEditingQtySku(null)
 }}
 onKeyDown={e => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
 style={{ width: 70, background: C.card, border: `1px solid ${C.indigo}`, borderRadius: 5, color: C.text, fontSize: 13, fontWeight: 700, padding: '3px 6px', outline: 'none' }}
 />
 ) : (
 <button
 onClick={e => { e.stopPropagation(); setEditingQtySku(item.sku) }}
 title={t('inventory.edit_qty_title')}
 style={{ all: 'unset', cursor: 'pointer', fontWeight: 700, fontSize: 12,
 color: editedQty[item.sku] != null ? C.indigo : C.green,
 borderBottom: `2px dashed ${(editedQty[item.sku] != null ? C.indigo : C.green)}60`, lineHeight: 1 }}
 >
 {fmt(effectiveQty(item), 0)}
 </button>
 )
 ) : item.recommended_qty === 0
 ? <span style={{ color: C.dim, fontSize: 11 }}>{t('inventory.dont_order')}</span>
 : <span style={{ color: C.dim }}>—</span>}
 </span>
 <span style={{ color: C.dim }}>{item.current_stock?.toFixed(0) ?? '—'}</span>
 <AbcXyzBadge value={item.abc_xyz} />
 <button onClick={() => onEdit(item)} aria-label={t('inventory.title_edit')} title={t('inventory.title_edit')} style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 4 }} onMouseEnter={e => (e.currentTarget.style.color = C.indigo)} onMouseLeave={e => (e.currentTarget.style.color = C.dim)}><Edit2 size={12} aria-hidden="true" /></button>
 </div>
 ))}
 </div>
 )
}

function fmt(n: number | null | undefined, d = 1) { if (n == null) return '—'; return n.toLocaleString(undefined, { maximumFractionDigits: d }) }
function fmtCurrency(n: number | null | undefined) { return formatMoney(n) }

// ── Simulator helpers ─────────────────────────────────────────────────────────
function simulateRecommendation(
 currentStock: number,
 dailyDemand: number,
 avgStd: number,
 leadTime: number,
 moq: number,
 serviceLevel = 0.95,
): number {
 const Z_MAP: Record<number, number> = { 0.90: 1.282, 0.95: 1.645, 0.97: 1.881, 0.99: 2.326 }
 const z = Z_MAP[serviceLevel] ?? 1.645
 const demandLT = dailyDemand * leadTime
 const safetyStock = z * avgStd * Math.sqrt(leadTime)
 const raw = Math.max(0, demandLT + safetyStock - currentStock)
 if (moq > 0) return Math.ceil(raw / moq) * moq
 return Math.round(raw)
}

function SimulatorPanel({ item }: { item: InventoryStatusItem }) {
 const { t } = useLanguage()
 const exp = item.calc_explanation
 if (!exp || !item.daily_demand || item.daily_demand <= 0) return null

 const [ltDelta,    setLtDelta]    = useState(0)
 const [demandMult, setDemandMult] = useState(100)
 const [stockDelta, setStockDelta] = useState(0)

 const simLeadTime = Math.max(1, (item.lead_time_days ?? DEFAULT_LEAD_TIME_DAYS) + ltDelta)
 const simDemand   = (item.daily_demand ?? 0) * demandMult / 100
 const simStock    = Math.max(0, (item.current_stock ?? 0) + stockDelta)

 // Approximate avgStd from safety stock and original lead_time
 // safety_stock = z * avgStd * sqrt(lead_time) → avgStd ≈ safety_stock / (z * sqrt(lead_time))
 const origLT = item.lead_time_days ?? DEFAULT_LEAD_TIME_DAYS
 const origSS = exp.safety_stock ?? 0
 const z      = 1.645
 const avgStd = origLT > 0 ? origSS / (z * Math.sqrt(origLT)) : 0

 const simRecommended = simulateRecommendation(simStock, simDemand, avgStd, simLeadTime, item.moq ?? 1)
 const originalRec    = item.recommended_qty ?? 0
 const delta          = simRecommended - originalRec
 const deltaColor     = delta > 0 ? '#ef4444' : delta < 0 ? '#22c55e' : C.muted

 const sliderS: React.CSSProperties = { width: '100%', cursor: 'pointer', accentColor: 'var(--accent)' }

 return (
  <div style={{
   background: 'color-mix(in srgb, var(--accent) 4%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 15%, transparent)',
   borderRadius: 8, padding: '16px 18px', marginTop: 8,
  }}>
   <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent)', marginBottom: 14,
    textTransform: 'uppercase', letterSpacing: '0.07em' }}>
    {t('inventory.sim_title')}
   </div>

   <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
    {/* Lead time slider */}
    <div>
     <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: C.muted, marginBottom: 6 }}>
      <span>{t('inventory.sim_lead_time')}</span>
      <span style={{ fontWeight: 700, color: ltDelta !== 0 ? '#f59e0b' : C.text }}>
       {simLeadTime}d {ltDelta > 0 ? `(+${ltDelta})` : ltDelta < 0 ? `(${ltDelta})` : ''}
      </span>
     </div>
     <input type="range" min={-10} max={30} value={ltDelta}
      name="sim_lead_time" aria-label={t('inventory.sim_lead_time')}
      onChange={e => setLtDelta(Number(e.target.value))} style={sliderS} />
     <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: C.dim }}>
      <span>-10d</span><span>+30d</span>
     </div>
    </div>

    {/* Demand slider */}
    <div>
     <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: C.muted, marginBottom: 6 }}>
      <span>{t('inventory.sim_demand_variation')}</span>
      <span style={{ fontWeight: 700, color: demandMult !== 100 ? '#f59e0b' : C.text }}>
       {demandMult}% {demandMult !== 100 ? `(${simDemand.toFixed(1)} ${t('inventory.calc_unit_per_day')})` : ''}
      </span>
     </div>
     <input type="range" min={50} max={200} step={5} value={demandMult}
      name="sim_demand_variation" aria-label={t('inventory.sim_demand_variation')}
      onChange={e => setDemandMult(Number(e.target.value))} style={sliderS} />
     <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: C.dim }}>
      <span>-50%</span><span>+100%</span>
     </div>
    </div>

    {/* Stock slider */}
    <div>
     <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: C.muted, marginBottom: 6 }}>
      <span>{t('inventory.sim_extra_stock')}</span>
      <span style={{ fontWeight: 700, color: stockDelta !== 0 ? '#f59e0b' : C.text }}>
       {stockDelta > 0 ? `+${stockDelta}` : stockDelta} {t('inventory.unit_und')}
      </span>
     </div>
     <input type="range" min={-(item.current_stock ?? 0)} max={(item.current_stock ?? 0) * 2}
      step={Math.max(1, Math.floor((item.current_stock ?? 50) / 10))}
      value={stockDelta}
      name="sim_extra_stock" aria-label={t('inventory.sim_extra_stock')}
      onChange={e => setStockDelta(Number(e.target.value))} style={sliderS} />
     <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: C.dim }}>
      <span>-{item.current_stock ?? 0}</span><span>+{(item.current_stock ?? 0) * 2}</span>
     </div>
    </div>
   </div>

   {/* Result */}
   <div style={{
    marginTop: 16, padding: '12px 16px', borderRadius: 8,
    background: 'var(--surface)', border: `1px solid ${C.border}`,
    display: 'flex', alignItems: 'center', gap: 16,
   }}>
    <div>
     <div style={{ fontSize: 11, color: C.dim, marginBottom: 2 }}>{t('inventory.sim_original_rec')}</div>
     <div style={{ fontSize: 20, fontWeight: 800, color: C.muted }}>{originalRec.toLocaleString('es')} {t('inventory.unit_und')}</div>
    </div>
    <div style={{ fontSize: 20, color: C.dim }}>→</div>
    <div>
     <div style={{ fontSize: 11, color: C.dim, marginBottom: 2 }}>{t('inventory.sim_with_changes')}</div>
     <div style={{ fontSize: 24, fontWeight: 900, color: delta > 0 ? '#ef4444' : delta < 0 ? '#22c55e' : C.text }}>
      {simRecommended.toLocaleString('es')} {t('inventory.unit_und')}
     </div>
    </div>
    {delta !== 0 && (
     <div style={{ fontSize: 13, color: deltaColor, fontWeight: 600 }}>
      {delta > 0 ? `+${delta.toLocaleString('es')} ${t('inventory.sim_units_more')}` : `${Math.abs(delta).toLocaleString('es')} ${t('inventory.sim_units_less')}`}
     </div>
    )}
    <button onClick={() => { setLtDelta(0); setDemandMult(100); setStockDelta(0) }}
     style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', fontSize: 11,
      color: C.dim, padding: '4px 10px', border: `1px solid ${C.border}`, borderRadius: 6 }}>
     {t('inventory.btn_reset')}
    </button>
   </div>
  </div>
 )
}

// ── Grow-in-place detail row ─────────────────────────────────────────────────
// The panel is mounted closed and opened one painted frame later, so the
// `grid-template-rows: 0fr -> 1fr` transition of `.reveal-panel` has a starting
// value to animate from. Mounting it already open (the naive
// `{isExpanded && <panel data-open="true"/>}`) gives the browser nothing to
// interpolate and the panel snaps.
//
// Mount-on-demand rather than "render every row and toggle data-open": only one
// row is ever expanded, and pre-rendering all of them would put a CalcExplainer,
// a PlanningValues and a stateful SimulatorPanel inside every one of the 100
// rows on the page.
function useOpenAfterMount(): boolean {
 const [open, setOpen] = useState(false)
 useEffect(() => {
  // Two animation frames, not one, and not a forced reflow in a layout effect:
  // a freshly inserted element has no before-change style, so until the
  // browser has actually painted it once there is nothing for the transition
  // to start from and the panel snaps open. (Measured: the layout-effect
  // variant jumped 0 -> 603px in a single frame.)
  let inner = 0
  const outer = requestAnimationFrame(() => { inner = requestAnimationFrame(() => setOpen(true)) })
  return () => { cancelAnimationFrame(outer); cancelAnimationFrame(inner) }
 }, [])
 return open
}

function ExpandedCalcRow({ item, background }: {
 item: InventoryStatusItem & LeadTimeLearningFields
 background: string
}) {
 const open = useOpenAfterMount()
 // The cell carries no padding of its own: padding applied at mount would jump
 // into place before the growth starts. It lives on the clipped content instead
 // so it grows with everything else.
 return (
  <tr>
   <td colSpan={13} style={{ padding: 0, borderBottom: `1px solid ${C.border}`, background }}>
    <div className="reveal-panel" data-open={open ? 'true' : 'false'}>
     <div>
      <div style={{ padding: '0 16px 12px 48px' }}>
       <CalcExplainer exp={item.calc_explanation!} moq={item.moq} />
       {/* Which of the four planning numbers are the buyer's and which are ours,
           plus what the lead-time learning is waiting for. */}
       <PlanningValues item={item} />
       <SimulatorPanel item={item} />
      </div>
     </div>
    </div>
   </td>
  </tr>
 )
}

// ── Main ─────────────────────────────────────────────────────────────────────
export default function InventoryPage() {
 const { t } = useLanguage()
 const { addToast } = useToast()
 const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
 const { sessionId, setSessionId, currentSession, completedSessions, error: sessionsError, refresh: refreshSessions } = useAutoSession()
 const [data, setData] = useState<{ items: InventoryStatusItem[]; summary: Record<string, number>; excluded_skus?: ExcludedSku[]; coverage_unit?: CoverageUnit } | null>(null)
 const [loading, setLoading] = useState(false)
 // Raw error, so ErrorState can classify by kind instead of showing a
 // pre-flattened string.
 const [error, setError] = useState<unknown>(null)
 const [signalFilter, setSignalFilter] = useState<InventorySignal | ''>('')
 const [search, setSearch] = useState('')
 const [sort, setSort] = useState<SortState | null>(null)
 const [page, setPage] = useState(1)
 const [deadPage, setDeadPage] = useState(1)
 const [viewMode, setViewMode] = useState<'table' | 'simple' | 'provider' | 'update' | 'dead'>(() =>
 typeof window !== 'undefined' && localStorage.getItem('adv') === '1' ? 'table' : 'simple'
 )
 const [expandedSku, setExpandedSku] = useState<string | null>(null)
 const [editId, setEditId] = useState<string | null>(null)
 const [editState, setEditState] = useState<EditState | null>(null)
 const [saving, setSaving] = useState(false)
 const [importing, setImporting] = useState(false)
 const [exporting, setExporting] = useState(false)
 const [pdfLoading, setPdfLoading] = useState(false)
 const [events, setEvents] = useState<InventoryEvent[]>([])
 const [showEvents, setShowEvents] = useState(false)
 const [simEvent, setSimEvent] = useState<InventoryEvent | null>(null)
 const [updateDraft, setUpdateDraft] = useState<Record<string, { current_stock: string; lead_time_days: string; supplier: string }>>({})
 const [rowBaseline, setRowBaseline] = useState<Record<string, { current_stock: string; lead_time_days: string; supplier: string }>>({})
 const [updateSaving, setUpdateSaving] = useState(false)
 const [savingRow, setSavingRow] = useState<string | null>(null)
 const [rowStatus, setRowStatus] = useState<{ sku: string; kind: 'saved' | 'discarded' | 'error' } | null>(null)
 const savingRowRef = useRef<string | null>(null)
 const [updatedSkus, setUpdatedSkus] = useState<Set<string>>(new Set())
 const [suppliers, setSuppliers] = useState<Supplier[]>([])
 const importRef = useRef<HTMLInputElement>(null)
 const savingRef = useRef(false)
 const [deadStock,   setDeadStock]   = useState<DeadStockResponse | null>(null)
 const [loadingDead, setLoadingDead] = useState(false)
 const [editedQty, setEditedQty] = useState<Record<string, number>>({})
 const [editingQtySku, setEditingQtySku] = useState<string | null>(null)
 const [showShrinkageModal, setShowShrinkageModal] = useState(false)
 // Multi-warehouse (feature 5.4): selector + per-warehouse view. null = all.
 const { warehouses, multi: multiWarehouse } = useWarehouses()
 const [selectedWarehouse, setSelectedWarehouse] = useState<string | null>(null)

 // Effective order quantity for an item: the buyer's edit if present, else the recommendation.
 const effectiveQty = useCallback((item: InventoryStatusItem): number =>
  editedQty[item.sku] ?? item.recommended_qty ?? 0, [editedQty])

 const reloadEvents = useCallback(() => {
 listInventoryEvents().then(setEvents).catch(() => {})
 }, [])

 useEffect(() => {
 reloadEvents()
 listSuppliers().then(setSuppliers).catch(() => {})
 }, [reloadEvents])

 const load = useCallback(async (sid: string) => {
 if (!sid) return
 setLoading(true); setError(null)
 setEditedQty({})
 setEditingQtySku(null)
 // `silent: true` — the failure is rendered as a full ErrorState below, so the
 // interceptor's toast would say the same thing twice.
 try { setData(await getInventoryStatus(sid, 0.95, { silent: true })) }
 catch (e: unknown) { setError(e) }
 finally { setLoading(false) }
 }, [])

 useEffect(() => { if (sessionId) load(sessionId) }, [sessionId, load])

 // ── Dead stock load ────────────────────────────────────────────────────────
 useEffect(() => {
 if (viewMode !== 'dead' || !sessionId) return
 setLoadingDead(true)
 getDeadStock(sessionId)
  .then(setDeadStock)
  .catch((e: unknown) => setError(e))
  .finally(() => setLoadingDead(false))
 }, [viewMode, sessionId])

 // ── Update-draft initialization ────────────────────────────────────────────
 useEffect(() => {
 if (viewMode === 'update' && data) {
 const draft: Record<string, { current_stock: string; lead_time_days: string; supplier: string }> = {}
 data.items.forEach(item => {
 draft[item.sku] = {
 current_stock: String(item.current_stock ?? ''),
 lead_time_days: String(item.lead_time_days ?? DEFAULT_LEAD_TIME_DAYS),
 supplier: item.supplier ?? '',
 }
 })
 setUpdateDraft(draft)
 // What each row looked like the last time it was in sync with the server.
 // Esc restores from here and a per-row save refreshes it, so discarding
 // after saving one row does not resurrect the pre-save value.
 setRowBaseline(draft)
 setUpdatedSkus(new Set())
 }
 }, [viewMode, data])

 function handleDraftChange(sku: string, field: string, value: string) {
 setUpdateDraft(prev => ({ ...prev, [sku]: { ...prev[sku], [field]: value } }))
 setUpdatedSkus(prev => { const next = new Set(prev); next.add(sku); return next })
 }

 // ── Per-row keyboard commit / discard (bulk-edit view) ─────────────────────
 // Tab already reached the inputs, but there was no way to commit or abandon a
 // single row without leaving the keyboard for the "save all" button, which
 // saves every row at once. Enter saves this row and moves to the same field
 // one row down (spreadsheet behaviour); Esc puts the row back.

 function discardRow(sku: string) {
 const base = rowBaseline[sku]
 if (!base) return
 setUpdateDraft(prev => ({ ...prev, [sku]: { ...base } }))
 setUpdatedSkus(prev => { const next = new Set(prev); next.delete(sku); return next })
 setRowStatus({ sku, kind: 'discarded' })
 }

 async function saveRow(sku: string) {
 const draft = updateDraft[sku]
 if (!draft || savingRowRef.current) return
 savingRowRef.current = sku
 setSavingRow(sku)
 setRowStatus(null)
 try {
 await upsertInventoryStock(sku, {
 current_stock: parseFloat(draft.current_stock) || 0,
 lead_time_days: parseInt(draft.lead_time_days) || DEFAULT_LEAD_TIME_DAYS,
 supplier: draft.supplier || undefined,
 })
 setRowBaseline(prev => ({ ...prev, [sku]: { ...draft } }))
 setUpdatedSkus(prev => { const next = new Set(prev); next.delete(sku); return next })
 setRowStatus({ sku, kind: 'saved' })
 } catch (e) {
 console.error(`Error saving ${sku}:`, e)
 setRowStatus({ sku, kind: 'error' })
 } finally {
 savingRowRef.current = null
 setSavingRow(null)
 }
 }

 /** Enter → save this row, then land on the same column of the next row.
  *  Esc → restore this row. Anything else is left to the input. */
 function handleRowKeyDown(e: React.KeyboardEvent<HTMLInputElement>, sku: string, field: string) {
 if (e.key === 'Enter') {
 e.preventDefault()
 void saveRow(sku)
 const inputs = Array.from(document.querySelectorAll<HTMLInputElement>(`[data-bulk-field="${field}"]`))
 const at = inputs.indexOf(e.target as HTMLInputElement)
 if (at >= 0 && at < inputs.length - 1) inputs[at + 1].focus()
 } else if (e.key === 'Escape') {
 e.preventDefault()
 discardRow(sku)
 }
 }

 async function handleSaveAll() {
 if (!sessionId) return
 setUpdateSaving(true)
 const toSave = Object.entries(updateDraft).filter(([sku, draft]) => {
 const original = rowBaseline[sku]
 if (!original) return true
 return (
 draft.current_stock !== original.current_stock ||
 draft.lead_time_days !== original.lead_time_days ||
 draft.supplier !== original.supplier
 )
 })
 let saved = 0
 const failed: string[] = []
 for (const [sku, draft] of toSave) {
 try {
 await upsertInventoryStock(sku, {
 current_stock: parseFloat(draft.current_stock) || 0,
 lead_time_days: parseInt(draft.lead_time_days) || DEFAULT_LEAD_TIME_DAYS,
 supplier: draft.supplier || undefined,
 })
 saved++
 } catch (e) {
 console.error(`Error saving ${sku}:`, e)
 failed.push(sku)
 }
 }
 setUpdateSaving(false)
 if (failed.length === 0) {
 addToast(t('inventory.toast_saved_title'), `${saved} SKUs`, 'success')
 setUpdateDraft({})
 setUpdatedSkus(new Set())
 setViewMode('table')
 } else {
 addToast(t('inventory.toast_save_partial'), `${failed.join(', ')} ${t('inventory.toast_save_failed_sufx')}`, 'error')
 setUpdatedSkus(new Set(failed))
 }
 await load(sessionId)
 }

 const items = useMemo(() => (data?.items ?? []).filter(item => {
 if (signalFilter && item.signal !== signalFilter) return false
 if (search) { const q = search.toLowerCase(); return item.sku.toLowerCase().includes(q) || (item.display_name ?? '').toLowerCase().includes(q) || (item.supplier ?? '').toLowerCase().includes(q) }
 return true
 }), [data, signalFilter, search])

 // The order the rows are paged in. It differs per view — the simple view puts
 // everything that needs a decision first — so it has to be resolved before
 // slicing, or page 1 would be an arbitrary window of the wrong sequence.
 const orderedItems = useMemo(() => {
 if (viewMode === 'simple') {
 return [
 ...items.filter(i => i.signal !== 'OK' && i.signal !== 'SIN_DATOS'),
 ...items.filter(i => i.signal === 'OK' || i.signal === 'SIN_DATOS'),
 ]
 }
 if (viewMode === 'table' && sort) return sortItems(items, sort)
 return items
 }, [items, viewMode, sort])

 const paged = usePage(orderedItems, page, setPage)
 const pageItems = paged.rows
 const deadItems = useMemo(() => deadStock?.items ?? [], [deadStock])
 const deadPaged = usePage(deadItems, deadPage, setDeadPage)
 useEffect(() => { setDeadPage(1) }, [deadStock])

 // Any change to what is being listed sends you back to the first page:
 // staying on page 14 of a list that now has 2 pages is never what you meant.
 useEffect(() => { setPage(1) }, [search, signalFilter, viewMode, sessionId, sort])

 function toggleSort(key: SortKey) {
 setSort(prev => prev?.key === key
 ? (prev.dir === 'asc' ? { key, dir: 'desc' } : null)
 : { key, dir: 'asc' })
 }

 const byProvider = useMemo(() => {
 const groups: Record<string, InventoryStatusItem[]> = {}
 for (const item of pageItems) { const k = item.supplier || ''; if (!groups[k]) groups[k] = []; groups[k].push(item) }
 const PRIO = ['PEDIR_YA', 'PEDIR_PRONTO', 'OK', 'SOBRESTOCK', 'SIN_DATOS']
 return Object.entries(groups).sort((a, b) => {
 const sa = Math.min(...a[1].map(i => PRIO.indexOf(i.signal)))
 const sb = Math.min(...b[1].map(i => PRIO.indexOf(i.signal)))
 return sa - sb || a[0].localeCompare(b[0])
 })
 }, [pageItems])

 // Upcoming events within 30 days
 const upcomingAlerts = useMemo(() => events.filter(e => {
 if (e.active === false) return false   // un event apagado no debe alertar
 const d = Math.round((new Date(e.start_date).getTime() - Date.now()) / 86400000)
 return d >= 0 && d <= 30 && new Date(e.end_date) >= new Date()
 }), [events])

 function startEdit(item: InventoryStatusItem) { setEditId(item.sku); setEditState(rowToEdit(item)) }
 function cancelEdit() { setEditId(null); setEditState(null) }

 async function commitEdit(sku: string) {
 // Guard with a ref, not just the `saving` state: a fast double-click fires
 // this handler twice before React re-renders the disabled button, and both
 // calls would otherwise close over the same stale saving=false and both
 // PUT to the backend.
 if (!editState || savingRef.current) return
 savingRef.current = true; setSaving(true)
 try {
 await upsertInventoryStock(sku, { display_name: editState.display_name || undefined, current_stock: parseFloat(editState.current_stock) || 0, lead_time_days: parseInt(editState.lead_time_days) || DEFAULT_LEAD_TIME_DAYS, unit_cost: editState.unit_cost ? parseFloat(editState.unit_cost) : undefined, moq: parseFloat(editState.moq) || DEFAULT_MOQ, supplier: editState.supplier || undefined, service_level: parseFloat(editState.service_level) || DEFAULT_SERVICE_LEVEL, sale_price: editState.sale_price ? parseFloat(editState.sale_price) : undefined, category: editState.category || undefined, family: editState.family || undefined, brand: editState.brand || undefined, unit_of_measure: editState.unit_of_measure || undefined, barcode: editState.barcode || undefined })
 setEditId(null); setEditState(null); await load(sessionId)
 } catch (e: unknown) { setError(e instanceof Error ? e.message : t('inventory.err_saving')) }
 finally { savingRef.current = false; setSaving(false) }
 }

 function handleDelete(sku: string) { setDeleteTarget(sku) }

 async function confirmDelete() {
 if (!deleteTarget) return
 const sku = deleteTarget
 setDeleteTarget(null)
 try { await deleteInventoryStock(sku); await load(sessionId) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : t('inventory.err_deleting')) }
 }

 async function handleImport(e: React.ChangeEvent<HTMLInputElement>) {
 const file = e.target.files?.[0]; if (!file) return; e.target.value = ''; setImporting(true)
 try {
 const res = await importInventoryCSV(file)
 await load(sessionId)
 addToast(t('inventory.toast_import_title'), `${t('inventory.alert_imported_prefix')} ${res.imported} ${t('inventory.alert_imported_of')} ${res.total_rows} SKUs`, 'success')
 }
 catch (err: unknown) { setError(err instanceof Error ? err.message : t('inventory.err_importing')) }
 finally { setImporting(false) }
 }

 async function handleExport() {
 if (!sessionId) return; setExporting(true)
 try { await exportInventoryPO(sessionId) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : t('inventory.err_exporting')) }
 finally { setExporting(false) }
 }

 // Exports a PO CSV built from the buyer's edited quantities (instead of the
 // server re-deriving them) and logs the decisions via logPOGeneration so
 // edited amounts are reflected in adoption tracking.
 function exportEditedPO() {
 if (!sessionId || !data) return
 const orderItems = data.items
  .filter(i => (i.signal === 'PEDIR_YA' || i.signal === 'PEDIR_PRONTO') && effectiveQty(i) > 0)
 if (orderItems.length === 0) return
 // Same artifact as the export on /hoy — same filename, same columns — so it
 // must use the same header keys. This one hardcoded Spanish, so an English
 // user got Spanish headers here and English ones there, from one product.
 const rows = [`SKU,${t('hoy.csv_col_product')},${t('hoy.csv_col_quantity')},${t('hoy.csv_col_supplier')},${t('hoy.csv_col_estimated_value')}`]
 for (const i of orderItems) {
  const qty = effectiveQty(i)
  const val = qty * (i.unit_cost ?? 0)
  rows.push(`${i.sku},"${i.display_name ?? ''}",${qty},"${i.supplier ?? ''}",${val}`)
 }
 const blob = new Blob([rows.join('\n')], { type: 'text/csv' })
 const url = URL.createObjectURL(blob)
 const a = document.createElement('a'); a.href = url; a.download = 'purchase_order.csv'; a.click()
 URL.revokeObjectURL(url)
 // Log decisions (edited => 'modified', otherwise 'approved')
 const decisions: POLineDecision[] = orderItems.map(i => ({
  sku: i.sku,
  display_name: i.display_name ?? undefined,
  supplier: i.supplier ?? undefined,
  signal: i.signal,
  recommended_qty: i.recommended_qty ?? 0,
  final_qty: effectiveQty(i),
  status: (editedQty[i.sku] != null ? 'modified' : 'approved') as 'approved' | 'modified',
  unit_cost: i.unit_cost ?? undefined,
 }))
 logPOGeneration(sessionId, decisions).catch(() => {})
 }

 async function handlePDF() {
 if (!sessionId) return; setPdfLoading(true)
 try { await downloadInventoryPDF(sessionId) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : t('inventory.err_generating_pdf')) }
 finally { setPdfLoading(false) }
 }

 async function handleAddEvent(ev: Omit<InventoryEvent, 'id' | 'tenant_id' | 'created_at'>) {
 try { const created = await createInventoryEvent(ev); setEvents(prev => [...prev, created]) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : t('inventory.err_saving_event')) }
 }

 async function handleDeleteEvent(id: string) {
 try { await deleteInventoryEvent(id); setEvents(prev => prev.filter(e => e.id !== id)) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : t('inventory.err_deleting_event')) }
 }

 const summary = data?.summary
 const skusWithoutStock = data ? data.items.filter(i => !i.has_stock).length : 0
 const skusWithForecast = data ? data.items.filter(i => i.has_forecast).length : 0

 // No page-level entrance on this root: the route fade is applied once by
 // AppShell, and a second one here would double-animate the same screen.
 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

 {/* Header */}
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
 <div style={{ width: 36, height: 36, borderRadius: 9, background: '#22c55e', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
 <ShoppingCart size={17} color="#fff" strokeWidth={2.5} />
 </div>
 <div>
 <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>{t('inventory.title')}</h1>
 <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('inventory.subtitle')}</p>
 </div>
 </div>

 <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
 {/* Session freshness */}
 <DataFreshness currentSession={currentSession} />

 {/* View toggle */}
 <div style={{ display: 'flex', border: `1px solid ${C.border}`, borderRadius: 8, overflow: 'hidden' }}>
 {([
 ['table', <List size={13} />, t('inventory.view_table')],
 ['simple', <Package size={13} />, t('inventory.view_simple')],
 ['provider', <Layers size={13} />, t('inventory.view_provider')],
 ['update', <PencilLine size={13} />, t('inventory.view_update')],
 ['dead', <Package size={13} />, t('inventory.view_dead')],
 ] as [string, React.ReactNode, string][]).map(([mode, icon, label]) => (
 <button key={mode} onClick={() => setViewMode(mode as 'table' | 'simple' | 'provider' | 'update' | 'dead')} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, padding: '6px 11px', fontSize: 11, fontWeight: 500, background: viewMode === mode ? 'var(--accent-dim)' : 'transparent', color: viewMode === mode ? 'var(--accent)' : C.dim }}>
 {icon}{label}
 </button>
 ))}
 </div>

 <button onClick={() => sessionId && load(sessionId)} disabled={loading} title={t('inventory.btn_refresh')} style={{ all: 'unset', cursor: loading ? 'default' : 'pointer', display: 'flex', alignItems: 'center', padding: '7px 10px', border: `1px solid ${C.border}`, borderRadius: 8, color: C.dim, opacity: loading ? 0.5 : 1 }}><RefreshCw size={13} /></button>
 <input ref={importRef} type="file" name="inventory_csv_import" aria-label={t('inventory.btn_import_csv_arrow')} accept=".csv" style={{ display: 'none' }} onChange={handleImport} />
 <button onClick={() => importRef.current?.click()} disabled={importing} style={{ all: 'unset', cursor: importing ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, border: `1px solid ${C.border}`, color: C.muted, opacity: importing ? 0.6 : 1 }}>
 {importing ? <Spinner size={12} /> : <Upload size={12} />} CSV
 </button>
 <button onClick={() => downloadInventoryTemplate().catch(err => setError(err instanceof Error ? err.message : String(err)))} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, border: `1px solid ${C.border}`, color: C.muted }}>
 <Download size={12} /> {t('inventory.btn_template')}
 </button>
 <button onClick={handleExport} disabled={exporting || !sessionId} style={{ all: 'unset', cursor: exporting || !sessionId ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.3)', color: C.green, opacity: exporting || !sessionId ? 0.5 : 1 }}>
 {exporting ? <Spinner size={12} /> : <Download size={12} />} {t('inventory.btn_export_po')}
 </button>
 <button onClick={exportEditedPO} disabled={!sessionId} style={{ all: 'unset', cursor: !sessionId ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, background: 'color-mix(in srgb, var(--accent) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)', color: C.indigo, opacity: !sessionId ? 0.5 : 1 }}>
 <Download size={12} /> {t('inventory.btn_export_edited')}
 </button>
 <button onClick={handlePDF} disabled={pdfLoading || !sessionId} title={t('inventory.title_download_pdf')} style={{ all: 'unset', cursor: pdfLoading || !sessionId ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, background: 'color-mix(in srgb, var(--accent) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)', color: C.indigo, opacity: pdfLoading || !sessionId ? 0.5 : 1 }}>
 {pdfLoading ? <Spinner size={12} /> : <FileText size={12} />} PDF
 </button>
 <Link href="/inventory/roi" style={{
 display: 'flex', alignItems: 'center', gap: 5,
 fontSize: 11, color: C.dim, textDecoration: 'none',
 padding: '7px 10px', border: `1px solid ${C.border}`,
 borderRadius: 8,
 }} title={t('inventory.title_view_impact')}>
 <TrendingUp size={12} /> {t('inventory.btn_impact')}
 </Link>
 <Link href="/inventory/suppliers" style={{
 display: 'flex', alignItems: 'center', gap: 5,
 fontSize: 11, color: C.dim, textDecoration: 'none',
 padding: '7px 10px', border: `1px solid ${C.border}`,
 borderRadius: 8,
 }} title={t('inventory.title_manage_suppliers')}>
 <Truck size={12} /> {t('inventory.btn_suppliers')}
 </Link>
 <button onClick={() => setShowShrinkageModal(true)} title={t('inventory.shrinkage_title_register')} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', color: C.red }}>
 <PackageMinus size={12} /> {t('inventory.shrinkage_btn_register')}
 </button>
 </div>
 </div>

 {/* Secondary failure over an already-rendered screen (e.g. dead-stock view). */}
 {error != null && data != null && (
 <InlineError error={error} onRetry={() => sessionId && load(sessionId)} onDismiss={() => setError(null)} />
 )}

 {/* Excluded SKUs notice — products uploaded but left out of the forecast */}
 {(data?.excluded_skus?.length ?? 0) > 0 && (
 <div style={{ padding: '12px 16px', borderRadius: 8, background: 'rgba(245,158,11,0.07)', border: '1px solid rgba(245,158,11,0.25)', display: 'flex', alignItems: 'flex-start', gap: 10 }}>
 <Info size={14} color={C.amber} style={{ flexShrink: 0, marginTop: 1 }} />
 <div style={{ flex: 1, fontSize: 12.5, color: C.text }}>
 <span style={{ fontWeight: 700, color: C.amber }}>
 {data!.excluded_skus!.length} {data!.excluded_skus!.length !== 1 ? t('inventory.excluded_skus_suffix_plural') : t('inventory.excluded_skus_suffix_singular')}
 </span>
 <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 3 }}>
 {data!.excluded_skus!.map(e => (
 <div key={e.sku} style={{ color: C.muted }}>
 <span style={{ fontFamily: 'monospace', fontWeight: 600, color: C.text }}>{e.sku}</span>
 {' — '}{e.detail}
 </div>
 ))}
 </div>
 <div style={{ marginTop: 6, fontSize: 11, color: C.dim }}>
 {t('inventory.excluded_skus_hint')}
 </div>
 </div>
 </div>
 )}

 {/* Upcoming events alert */}
 {upcomingAlerts.length > 0 && (
 <div style={{ padding: '10px 16px', borderRadius: 8, background: 'rgba(245,158,11,0.07)', border: '1px solid rgba(245,158,11,0.25)', display: 'flex', alignItems: 'flex-start', gap: 10 }}>
 <Calendar size={14} color={C.amber} style={{ flexShrink: 0, marginTop: 1 }} />
 <div style={{ flex: 1, fontSize: 12 }}>
 <span style={{ fontWeight: 600, color: C.amber }}>{t('inventory.upcoming_events_prefix')}</span>
 {upcomingAlerts.map(ev => {
 const d = Math.round((new Date(ev.start_date).getTime() - Date.now()) / 86400000)
 // Clickable: the multiplier must not stay a bare number — opening the
 // simulator shows where it comes from and lets you tune it per product.
 return (
 <button
 key={ev.id}
 onClick={() => sessionId && setSimEvent(ev)}
 disabled={!sessionId}
 title={ev.notes || t('inventory.events_simulate_tooltip')}
 aria-label={`${t('inventory.events_btn_simulate')}: ${ev.name}`}
 style={{ all: 'unset', cursor: sessionId ? 'pointer' : 'default', marginLeft: 8, color: C.text, borderBottom: sessionId ? `1px dotted ${C.dim}` : 'none' }}
 >
 {ev.name} <span style={{ color: C.dim }}>({d === 0 ? t('inventory.day_today') : `${t('inventory.day_in_prefix')} ${d}d`}, ×{ev.multiplier.toFixed(1)})</span>
 </button>
 )
 })}
 <span style={{ marginLeft: 8, color: C.dim, fontSize: 11 }}>{t('inventory.events_multiplier_hint')}</span>
 </div>
 </div>
 )}

 {/* Situación */}
 {summary && <ContextMessage summary={summary} />}

 {/* SKUs sin stock banner */}
 {!loading && sessionId && skusWithoutStock > 0 && (
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderRadius: 8, background: 'color-mix(in srgb, var(--accent) 6%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 20%, transparent)', color: C.indigo, fontSize: 13 }}>
 <Info size={14} style={{ flexShrink: 0 }} />
 <span><strong>{skusWithoutStock} {t('inventory.skus_of_label')} {skusWithForecast} SKUs</strong> {t('inventory.skus_no_stock_hint')} <code style={{ fontSize: 11, background: 'color-mix(in srgb, var(--accent) 10%, transparent)', padding: '1px 5px', borderRadius: 4 }}>sku, current_stock, lead_time_days</code></span>
 </div>
 )}

 {/* KPIs — skeleton first so the row does not pop in. */}
 {loading && !summary && <SkeletonCards count={6} height={74} />}
 {summary && (
 // Fades in over the skeleton cards it replaces: same shape, so the
 // transition reads as the placeholders resolving into numbers.
 <div className="page-enter" style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12 }}>
 <KPICard label={t('inventory.kpi_total_skus')} value={summary.total_skus} color={C.indigo} onClick={() => setSignalFilter('')} active={!signalFilter} />
 <KPICard label={t('inventory.signal_order_now')} value={summary.order_now} color={C.red} onClick={() => setSignalFilter(signalFilter === 'PEDIR_YA' ? '' : 'PEDIR_YA')} active={signalFilter === 'PEDIR_YA'} sub={summary.order_now > 0 ? t('inventory.kpi_immediate_risk') : undefined} />
 <KPICard label={t('inventory.signal_order_soon')} value={summary.order_soon} color={C.amber} onClick={() => setSignalFilter(signalFilter === 'PEDIR_PRONTO' ? '' : 'PEDIR_PRONTO')} active={signalFilter === 'PEDIR_PRONTO'} />
 <KPICard label={t('inventory.signal_ok')} value={summary.ok} color={C.green} onClick={() => setSignalFilter(signalFilter === 'OK' ? '' : 'OK')} active={signalFilter === 'OK'} />
 <KPICard label={t('inventory.signal_overstock')} value={summary.overstock} color={C.blue} onClick={() => setSignalFilter(signalFilter === 'SOBRESTOCK' ? '' : 'SOBRESTOCK')} active={signalFilter === 'SOBRESTOCK'} />
 <KPICard label={t('inventory.kpi_inventory_value')} value={summary.total_inventory_value > 0 ? fmtCurrency(summary.total_inventory_value) : '—'} color={C.indigo} sub={t('inventory.kpi_skus_with_cost')} />
 </div>
 )}

 {/* Warehouse selector (feature 5.4). With 2+ warehouses: full selector;
     mono-warehouse: only the discreet add-warehouse entry point. */}
 {sessionId && (
 <WarehouseSelector
 value={selectedWarehouse}
 onChange={setSelectedWarehouse}
 warehouses={warehouses}
 onSharesChanged={() => { if (sessionId) load(sessionId) }}
 />
 )}

 {/* Per-warehouse semáforo replaces the main table while a warehouse is selected */}
 {selectedWarehouse && sessionId ? (
 <WarehouseStatusTable
 sessionId={sessionId}
 warehouse={selectedWarehouse}
 onTransferCreated={() => load(sessionId)}
 />
 ) : (
 <>
 {/* Main table / view */}
 <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>

 {/* Toolbar */}
 <div style={{ padding: '12px 16px', borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', gap: 10, background: C.card }}>
 <input type="search" name="inventory_search" aria-label={t('inventory.search_placeholder')} value={search} onChange={e => setSearch(e.target.value)} placeholder={t('inventory.search_placeholder')} style={{ flex: 1, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 7, padding: '6px 12px', fontSize: 12, color: C.text, outline: 'none' }} />
 {search && <button onClick={() => setSearch('')} aria-label={t('inventory.search_clear')} title={t('inventory.search_clear')} style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex' }}><X size={13} aria-hidden="true" /></button>}
 <span style={{ fontSize: 11, color: C.dim, whiteSpace: 'nowrap' }}>{items.length} SKU{items.length !== 1 ? 's' : ''}</span>
 </div>

 {loading ? (
 <LoadingState label={t('inventory.loading_label')}>
 <SkeletonTable rows={8} columns={6} />
 </LoadingState>
 ) : error && !data ? (
 /* ── Status request failed outright ───────────────────────── */
 <div style={{ padding: '32px 24px' }}>
 <ErrorState error={error} onRetry={() => sessionId && load(sessionId)} />
 </div>
 ) : sessionsError ? (
 /* ── Session list failed to load ──────────────────────────── */
 <div style={{ padding: '40px 32px', textAlign: 'center' }}>
 <AlertTriangle size={32} color={C.red} style={{ margin: '0 auto 12px', opacity: 0.7 }} />
 <div style={{ fontSize: 14, color: C.text, marginBottom: 16, maxWidth: 420, margin: '0 auto 16px' }}>{sessionsError}</div>
 <button onClick={refreshSessions} style={{ all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 18px', borderRadius: 8, background: C.indigo, color: '#fff', fontSize: 13, fontWeight: 600 }}>
 <RefreshCw size={12} /> {t('inventory.btn_retry')}
 </button>
 </div>
 ) : !sessionId ? (
 /* ── Onboarding empty state ───────────────────────────────── */
 <div style={{ padding: '40px 48px', maxWidth: 520, margin: '0 auto', textAlign: 'center' }}>
 <ShoppingCart size={36} strokeWidth={1} color={C.indigo} style={{ margin: '0 auto 16px', opacity: 0.4 }} />
 <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginBottom: 8 }}>{t('inventory.onboarding_welcome')}</div>
 <div style={{ fontSize: 13, color: C.dim, marginBottom: 24, lineHeight: 1.7 }}>{t('inventory.onboarding_desc')}</div>
 <div style={{ textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 14 }}>
 {[
 { n: '1', title: t('inventory.onboarding_step1_title'), desc: t('inventory.onboarding_step1_desc') },
 { n: '2', title: t('inventory.onboarding_step2_title'), desc: t('inventory.onboarding_step2_desc') },
 { n: '3', title: t('inventory.onboarding_step3_title'), desc: t('inventory.onboarding_step3_desc') },
 ].map(({ n, title, desc }) => (
 <div key={n} style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
 <span style={{ width: 24, height: 24, borderRadius: '50%', flexShrink: 0, background: 'color-mix(in srgb, var(--accent) 12%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)', color: C.indigo, fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{n}</span>
 <div><div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{title}</div><div style={{ fontSize: 12, color: C.dim, marginTop: 3, lineHeight: 1.6 }}>{desc}</div></div>
 </div>
 ))}
 </div>
 </div>
 ) : items.length === 0 ? (
 /* Two very different emptinesses: a filter that matched nothing (clear it)
    versus a session with no stock loaded (go load it). */
 <div style={{ padding: '32px 24px' }}>
 {signalFilter || search ? (
 <EmptyState
 compact
 icon={<Search size={20} />}
 title={t('inventory.empty_filtered_title')}
 body={t('inventory.empty_no_filtered_skus')}
 actions={[{
 label: t('inventory.empty_filtered_cta'),
 variant: 'secondary',
 onClick: () => { setSignalFilter(''); setSearch('') },
 }]}
 />
 ) : (
 <EmptyState
 icon={<PackagePlus size={22} />}
 title={t('inventory.empty_stock_title')}
 body={t('inventory.empty_stock_body')}
 actions={[{
 label: t('inventory.empty_stock_cta'),
 icon: <Upload size={14} />,
 onClick: () => setViewMode('update'),
 }]}
 />
 )}
 </div>
 ) : viewMode === 'provider' ? (
 <>
 <div style={{ padding: 16 }}>{byProvider.map(([provider, provItems]) => <ProviderGroup key={provider || '__none__'} name={provider} items={provItems} onEdit={startEdit} editedQty={editedQty} editingQtySku={editingQtySku} setEditedQty={setEditedQty} setEditingQtySku={setEditingQtySku} effectiveQty={effectiveQty} coverageUnit={data?.coverage_unit} />)}</div>
 <Pagination page={paged.page} pageCount={paged.pageCount} offset={paged.offset} total={paged.total} rowsOnPage={pageItems.length} onPage={setPage} label="SKU" />
 </>

 ) : viewMode === 'update' ? (
 /* ── Quick update view ────────────────────────────────────── */
 <div>
 {/* CSV import hint */}
 <div style={{ padding: '10px 16px', background: 'color-mix(in srgb, var(--accent) 4%, transparent)', borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
 <span style={{ fontSize: 12, color: C.dim, flex: 1 }}>
 {t('inventory.csv_hint_prefix')} <code style={{ fontSize: 11, background: 'color-mix(in srgb, var(--accent) 10%, transparent)', padding: '1px 5px', borderRadius: 4 }}>sku, current_stock, lead_time_days</code>
 </span>
 <button onClick={() => importRef.current?.click()} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '5px 12px', borderRadius: 7, fontSize: 12, fontWeight: 600, border: `1px solid ${C.border}`, color: C.muted }}>
 <Upload size={11} /> {t('inventory.btn_import_csv_arrow')}
 </button>
 </div>
 {/* Action bar */}
 <div style={{ padding: '10px 16px', background: C.card, borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', gap: 10 }}>
 <span style={{ fontSize: 12, color: updatedSkus.size > 0 ? C.amber : C.dim, fontWeight: updatedSkus.size > 0 ? 600 : 400, flex: 1 }}>
 {updatedSkus.size === 0 ? t('inventory.no_changes') : `${updatedSkus.size} ${updatedSkus.size !== 1 ? t('inventory.rows_modified_plural') : t('inventory.rows_modified_singular')}`}
 </span>
 <button
 onClick={() => {
 if (data) {
 const draft: Record<string, { current_stock: string; lead_time_days: string; supplier: string }> = {}
 data.items.forEach(item => {
 draft[item.sku] = { current_stock: String(item.current_stock ?? ''), lead_time_days: String(item.lead_time_days ?? DEFAULT_LEAD_TIME_DAYS), supplier: item.supplier ?? '' }
 })
 setUpdateDraft(draft)
 setRowBaseline(draft)
 setUpdatedSkus(new Set())
 }
 }}
 disabled={updatedSkus.size === 0}
 style={{ all: 'unset', cursor: updatedSkus.size === 0 ? 'default' : 'pointer', padding: '6px 14px', borderRadius: 7, border: `1px solid ${C.border}`, fontSize: 12, color: C.dim, opacity: updatedSkus.size === 0 ? 0.4 : 1 }}
 >
 {t('inventory.btn_discard')}
 </button>
 <button
 onClick={handleSaveAll}
 disabled={updatedSkus.size === 0 || updateSaving}
 style={{ all: 'unset', cursor: updatedSkus.size === 0 || updateSaving ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '6px 16px', borderRadius: 7, fontSize: 12, fontWeight: 600, background: updatedSkus.size > 0 ? C.green : 'rgba(34,197,94,0.2)', color: '#fff', opacity: updatedSkus.size === 0 || updateSaving ? 0.5 : 1 }}
 >
 {updateSaving ? <Spinner size={11} /> : <Save size={11} />}
 {updateSaving ? t('inventory.saving_ellipsis') : `${t('inventory.btn_save_prefix')} ${updatedSkus.size > 0 ? updatedSkus.size : ''} ${updatedSkus.size !== 1 ? t('inventory.changes_plural') : t('inventory.changes_singular')}`}
 </button>
 </div>
 {/* Per-row keyboard contract, stated where the typing happens. Also the
     accessible description every editable cell points at, so it is read
     out the first time focus lands in the row. */}
 <div
 id="bulk-edit-keys"
 style={{ padding: '7px 16px', background: C.surface, borderBottom: `1px solid ${C.border}`, fontSize: 11, color: C.dim }}
 >
 {tOr(t, 'inventory.bulk_keyboard_hint',
  'Enter saves this row and moves to the next · Esc discards this row')}
 </div>
 {/* Row-level outcome, announced. Without it a keyboard user pressing Enter
     had no way to know whether the row was saved. */}
 <div aria-live="polite" className="sr-only">
 {rowStatus && (
  rowStatus.kind === 'saved'
   ? tOr(t, 'inventory.bulk_row_saved', `Row ${rowStatus.sku} saved`, { sku: rowStatus.sku })
   : rowStatus.kind === 'discarded'
    ? tOr(t, 'inventory.bulk_row_discarded', `Changes to row ${rowStatus.sku} discarded`, { sku: rowStatus.sku })
    : tOr(t, 'inventory.bulk_row_error', `Row ${rowStatus.sku} could not be saved`, { sku: rowStatus.sku })
 )}
 </div>
 {/* Table */}
 <div style={{ overflowY: 'auto', maxHeight: 500 }}>
 <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
 <caption className="sr-only">
 {tOr(t, 'inventory.bulk_table_caption',
  'Stock, lead time and supplier per product. Enter saves the row, Esc discards it.')}
 </caption>
 <thead style={{ position: 'sticky', top: 0, zIndex: 2 }}>
 <tr style={{ background: C.card }}>
 <th scope="col" style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em', width: 8 }}>
 <span className="sr-only">{tOr(t, 'inventory.bulk_col_modified', 'Modified')}</span>
 </th>
 <th scope="col" style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>SKU</th>
 <th scope="col" style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>{t('inventory.col_name')}</th>
 <th scope="col" style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>{t('inventory.col_current_stock')}</th>
 <th scope="col" style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>{t('inventory.col_lead_time_days')}</th>
 <th scope="col" style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>{t('inventory.col_provider')}</th>
 </tr>
 </thead>
 <tbody>
 {pageItems.map((item, idx) => {
 const draft = updateDraft[item.sku]
 const isModified = updatedSkus.has(item.sku)
 const rowBg = (paged.offset + idx) % 2 === 0 ? C.surface : C.card
 const isSavingThis = savingRow === item.sku
 const inputUpd: React.CSSProperties = {
 background: C.surface, border: `1px solid ${C.border}`, borderRadius: 5,
 color: C.text, fontSize: 12, outline: 'none',
 padding: '5px 8px', width: '100%', boxSizing: 'border-box' as const,
 transition: 'border-color 0.15s',
 opacity: isSavingThis ? 0.6 : 1,
 }
 // Every cell in the row names its SKU: 100 identically-labelled
 // "Stock actual" fields tell a screen-reader user nothing about
 // which product they are typing into.
 const fieldLabel = (col: string) => `${col} — ${item.display_name || item.sku}`
 return (
 <tr
 key={item.sku}
 style={{ background: isModified ? 'rgba(245,158,11,0.04)' : rowBg }}
 >
 {/* Modified indicator */}
 <td style={{ padding: '0 0 0 8px', borderBottom: `1px solid ${C.border}`, width: 8 }}>
 {isModified && (
 <span
 title={tOr(t, 'inventory.bulk_row_unsaved', 'Unsaved changes')}
 style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: C.amber }}
 >
 <span className="sr-only">{tOr(t, 'inventory.bulk_row_unsaved', 'Unsaved changes')}</span>
 </span>
 )}
 </td>
 {/* The SKU is this row's header — announcing it before each cell is
     the whole point of a row header. */}
 <th scope="row" style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, fontFamily: 'monospace', fontWeight: 600, fontSize: 11, whiteSpace: 'nowrap', textAlign: 'left', color: C.text }}>
 {item.sku}
 </th>
 <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, color: C.muted, maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
 {item.display_name || <span style={{ color: C.dim }}>—</span>}
 </td>
 <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, minWidth: 110 }}>
 <input
 style={inputUpd}
 name={`bulk-current-stock-${item.sku}`} aria-label={fieldLabel(t('inventory.col_current_stock'))}
 aria-describedby="bulk-edit-keys" aria-keyshortcuts="Enter Escape"
 type="number" min={0}
 value={draft?.current_stock ?? ''}
 onChange={e => handleDraftChange(item.sku, 'current_stock', e.target.value)}
 onFocus={e => { e.target.style.borderColor = 'var(--accent)' }}
 onBlur={e => { e.target.style.borderColor = C.border }}
 onKeyDown={e => handleRowKeyDown(e, item.sku, 'current_stock')}
 data-bulk-field="current_stock"
 data-stock-input=""
 />
 </td>
 <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, minWidth: 130 }}>
 <input
 style={inputUpd}
 name={`bulk-lead-time-${item.sku}`} aria-label={fieldLabel(t('inventory.col_lead_time_days'))}
 aria-describedby="bulk-edit-keys" aria-keyshortcuts="Enter Escape"
 type="number" min={1} max={365}
 value={draft?.lead_time_days ?? ''}
 onChange={e => handleDraftChange(item.sku, 'lead_time_days', e.target.value)}
 onFocus={e => { e.target.style.borderColor = 'var(--accent)' }}
 onBlur={e => { e.target.style.borderColor = C.border }}
 onKeyDown={e => handleRowKeyDown(e, item.sku, 'lead_time_days')}
 data-bulk-field="lead_time_days"
 />
 </td>
 <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, minWidth: 150 }}>
 <input
 style={inputUpd}
 name={`bulk-supplier-${item.sku}`} aria-label={fieldLabel(t('inventory.col_provider'))}
 aria-describedby="bulk-edit-keys" aria-keyshortcuts="Enter Escape"
 type="text"
 value={draft?.supplier ?? ''}
 onChange={e => handleDraftChange(item.sku, 'supplier', e.target.value)}
 onFocus={e => { e.target.style.borderColor = 'var(--accent)' }}
 onBlur={e => { e.target.style.borderColor = C.border }}
 onKeyDown={e => handleRowKeyDown(e, item.sku, 'supplier')}
 data-bulk-field="supplier"
 />
 </td>
 </tr>
 )
 })}
 </tbody>
 </table>
 </div>
 <Pagination page={paged.page} pageCount={paged.pageCount} offset={paged.offset} total={paged.total} rowsOnPage={pageItems.length} onPage={setPage} label="SKU" />
 </div>

 ) : viewMode === 'simple' ? (
 /* ── Vista simple ─────────────────────────────────────────── */
 /* One of the two views the page can land on, so it is what replaces the
    skeleton table: 140 ms of fade instead of a same-frame swap. */
 <div className="page-enter">
 <div style={{ padding: '10px 16px', background: C.card, borderBottom: `1px solid ${C.border}`, display: 'grid', gridTemplateColumns: '1fr 160px 120px 160px', gap: 16, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
 <span>{t('inventory.col_sku_product')}</span><span>{t('inventory.col_signal')}</span><span style={{ textAlign: 'right' }}>{t('inventory.col_qty_to_order')}</span><span>{t('inventory.col_provider')}</span>
 </div>
 {/* Already ordered "needs a decision first" by `orderedItems`, before paging. */}
 {pageItems.map((item, idx) => (
 <div key={item.sku} style={{ display: 'grid', gridTemplateColumns: '1fr 160px 120px 160px', gap: 16, padding: '14px 16px', alignItems: 'center', borderBottom: `1px solid ${C.border}`, background: (paged.offset + idx) % 2 === 0 ? C.surface : C.card, borderLeft: `3px solid ${item.signal === 'PEDIR_YA' || item.signal === 'PEDIR_PRONTO' ? signalColor(item.signal) : 'transparent'}` }}>
 <div>
 <div style={{ fontWeight: 600, fontSize: 13 }}>{item.display_name || item.sku}</div>
 {item.display_name && <div style={{ fontSize: 11, color: C.dim, fontFamily: 'monospace' }}>{item.sku}</div>}
 </div>
 <SignalBadge s={item.signal} />
 <div style={{ textAlign: 'right' }}>
 {item.recommended_qty != null && item.recommended_qty > 0
 ? <span style={{ fontSize: 18, fontWeight: 800, color: signalColor(item.signal) }}>{fmt(item.recommended_qty, 0)}</span>
 : <span style={{ fontSize: 13, color: C.dim }}>—</span>}
 </div>
 <span style={{ fontSize: 12, color: C.muted }}>{item.supplier || '—'}</span>
 </div>
 ))}
 <Pagination page={paged.page} pageCount={paged.pageCount} offset={paged.offset} total={paged.total} rowsOnPage={pageItems.length} onPage={setPage} label="SKU" />
 </div>

 ) : viewMode === 'dead' ? (
 /* ── Inventario inmovilizado ──────────────────────────────── */
 <div style={{ padding: 16 }}>
  {loadingDead ? (
   <div style={{ padding: 48, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
  ) : !deadStock ? (
   <div style={{ padding: 48, textAlign: 'center', color: C.dim, fontSize: 13 }}>
    {t('inventory.dead_select_session')}
   </div>
  ) : deadStock.sku_count === 0 ? (
   <div style={{ padding: '40px 0', textAlign: 'center' }}>
    <div style={{ fontSize: 14, fontWeight: 600, color: C.green, marginBottom: 8 }}>
     {t('inventory.dead_none_detected')}
    </div>
    <div style={{ fontSize: 13, color: C.dim }}>
     {t('inventory.dead_none_detected_desc')}
    </div>
   </div>
  ) : (
   <>
    {/* Summary bar */}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginBottom: 20 }}>
     {[
      { label: t('inventory.dead_kpi_skus'), value: deadStock.sku_count, color: C.amber },
      { label: t('inventory.dead_kpi_capital_trapped'),
       value: formatMoneyCompact(deadStock.total_capital_trapped),
       color: C.red },
      { label: t('inventory.dead_kpi_holding_cost'),
       value: formatMoneyCompact(deadStock.total_holding_cost_monthly),
       color: C.amber },
     ].map(({ label, value, color }) => (
      <div key={label} style={{
       background: C.surface, border: `1px solid ${C.border}`,
       borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${color}`,
      }}>
       <div style={{ fontSize: 20, fontWeight: 800, color }}>{value}</div>
       <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{label}</div>
      </div>
     ))}
    </div>

    {/* Items table */}
    <div style={{ borderRadius: 10, border: `1px solid ${C.border}`, overflow: 'hidden' }}>
     <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
      <thead>
       <tr style={{ background: C.card }}>
        {[t('inventory.dead_col_product'), t('inventory.dead_col_days_stalled'), t('inventory.dead_col_stock'), t('inventory.dead_col_capital_trapped'), t('inventory.dead_col_cost_per_month'), t('inventory.dead_col_category'), t('inventory.dead_col_suggested_action')].map(h => (
         <th key={h} scope="col" style={{
          padding: '9px 12px', textAlign: 'left',
          color: C.dim, fontWeight: 600, fontSize: 10,
          borderBottom: `1px solid ${C.border}`, textTransform: 'uppercase' as const,
          letterSpacing: '0.06em',
         }}>{h}</th>
        ))}
       </tr>
      </thead>
      <tbody>
       {deadPaged.rows.map((item, i: number) => (
        <tr key={item.sku} style={{
         background: (deadPaged.offset + i) % 2 === 0 ? C.surface : C.card,
         borderBottom: `1px solid ${C.border}`,
        }}>
         <th scope="row" style={{ padding: '10px 12px', textAlign: 'left', fontWeight: 400, color: C.text }}>
          <div style={{ fontWeight: 600 }}>{item.display_name || item.sku}</div>
          <div style={{ fontSize: 10, color: C.dim, fontFamily: 'monospace' }}>{item.sku}</div>
          {item.supplier && <div style={{ fontSize: 10, color: C.muted }}>{item.supplier}</div>}
         </th>
         <td style={{ padding: '10px 12px', color: C.red, fontWeight: 700 }}>
          {item.days_without_movement}d
         </td>
         <td style={{ padding: '10px 12px', color: C.text }}>
          {item.current_stock?.toLocaleString()}
         </td>
         <td style={{ padding: '10px 12px', fontWeight: 700, color: C.red }}>
          {formatMoneyCompact(item.capital_trapped)}
         </td>
         <td style={{ padding: '10px 12px', color: C.amber, fontSize: 11 }}>
          {formatMoneyCompact(item.holding_cost_monthly)}{t('inventory.unit_per_month_suffix')}
         </td>
         <td style={{ padding: '10px 12px' }}>
          <span style={{
           fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 20,
           background: item.abc === 'A' ? 'rgba(34,197,94,0.1)' : item.abc === 'B' ? 'rgba(245,158,11,0.1)' : 'rgba(100,116,139,0.1)',
           color: item.abc === 'A' ? '#22c55e' : item.abc === 'B' ? '#f59e0b' : '#64748b',
          }}>{item.abc}</span>
         </td>
         <td style={{ padding: '10px 12px', fontSize: 11, color: C.muted }}>
          {item.action_suggested}
         </td>
        </tr>
       ))}
      </tbody>
     </table>
    </div>

    <Pagination page={deadPaged.page} pageCount={deadPaged.pageCount} offset={deadPaged.offset} total={deadPaged.total} rowsOnPage={deadPaged.rows.length} onPage={setDeadPage} label="SKU" />

    <div style={{ marginTop: 12, fontSize: 11, color: C.dim }}>
     {t('inventory.dead_footer_note_1')}
     {t('inventory.dead_footer_note_2')}
    </div>
   </>
  )}
 </div>

 ) : (
 /* ── Tabla completa ───────────────────────────────────────── */
 /* The other landing view, and the one the skeleton table is shaped after:
    it fades in so the placeholder appears to turn into the rows. */
 <div className="page-enter" style={{ overflowX: 'auto' }}>
 <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
 <thead>
 <tr style={{ background: C.card }}>
 <th scope="col" style={{ padding: '9px 12px', width: 28, borderBottom: `1px solid ${C.border}` }}>
 <span className="sr-only">{tOr(t, 'inventory.col_expand', 'Show calculation')}</span>
 </th>
 <ThTip label={t('inventory.col_signal')} tip={t('inventory.tip_signal')} sortKey="signal" sort={sort} onSort={toggleSort} />
 <ThTip label={t('inventory.col_sku_name')} tip={t('inventory.tip_sku_name')} sortKey="sku" sort={sort} onSort={toggleSort} />
 <ThTip label={t('inventory.col_stock')} tip={t('inventory.tip_stock')} sortKey="stock" sort={sort} onSort={toggleSort} />
 <ThTip label={t('inventory.col_trend')} tip={t('inventory.tip_trend')} />
 <ThTip label={`${t('inventory.wh_col_coverage')} (${coverageUnitShort(data?.coverage_unit, t)})`} tip={t('inventory.tip_days_coverage')} sortKey="coverage" sort={sort} onSort={toggleSort} />
 <ThTip label={t('inventory.col_demand_lt')} tip={t('inventory.tip_demand_lt')} sortKey="demand_lt" sort={sort} onSort={toggleSort} />
 <ThTip label={t('inventory.col_qty_to_order')} tip={t('inventory.tip_qty_to_order')} sortKey="qty" sort={sort} onSort={toggleSort} />
 <ThTip label={t('inventory.col_lead_time')} tip={t('inventory.tip_lead_time')} sortKey="lead_time" sort={sort} onSort={toggleSort} />
 <ThTip label="MOQ" tip={t('inventory.tip_moq')} sortKey="moq" sort={sort} onSort={toggleSort} />
 <ThTip label="ABC-XYZ" tip={t('inventory.tip_abc_xyz')} sortKey="abc_xyz" sort={sort} onSort={toggleSort} />
 <ThTip label={t('inventory.col_warehouse_value')} tip={t('inventory.tip_warehouse_value')} sortKey="value" sort={sort} onSort={toggleSort} />
 <th scope="col" style={{ padding: '9px 12px', borderBottom: `1px solid ${C.border}` }}>
 <span className="sr-only">{tOr(t, 'inventory.col_actions', 'Actions')}</span>
 </th>
 </tr>
 </thead>
 <tbody>
 {pageItems.map((item, idx) => {
 const isEditing = editId === item.sku
 const isExpanded = expandedSku === item.sku && !isEditing
 const rowBg = (paged.offset + idx) % 2 === 0 ? C.surface : C.card
 const crit = item.signal === 'PEDIR_YA'

 if (isEditing && editState) return (
 <tr key={item.sku} style={{ background: 'color-mix(in srgb, var(--accent) 4%, transparent)' }}>
 <td style={{ padding: '8px 6px', borderBottom: `1px solid ${C.border}` }} />
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}><SignalBadge s={item.signal} /></td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <div style={{ fontWeight: 600, fontFamily: 'monospace', marginBottom: 4, fontSize: 11 }}>{item.sku}</div>
 <input style={inputS} name={`edit-display-name-${item.sku}`} aria-label={t('inventory.edit_display_name_placeholder')} placeholder={t('inventory.edit_display_name_placeholder')} value={editState.display_name} onChange={e => setEditState(s => s ? { ...s, display_name: e.target.value } : s)} />
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>{t('inventory.edit_display_name_hint')}</div>
 <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
 <input style={{ ...inputS, width: 90 }} name={`edit-category-${item.sku}`} aria-label={t('inventory.edit_category')} placeholder={t('inventory.edit_category')} value={editState.category} onChange={e => setEditState(s => s ? { ...s, category: e.target.value } : s)} />
 <input style={{ ...inputS, width: 90 }} name={`edit-family-${item.sku}`} aria-label={t('inventory.edit_family')} placeholder={t('inventory.edit_family')} value={editState.family} onChange={e => setEditState(s => s ? { ...s, family: e.target.value } : s)} />
 <input style={{ ...inputS, width: 90 }} name={`edit-brand-${item.sku}`} aria-label={t('inventory.edit_brand')} placeholder={t('inventory.edit_brand')} value={editState.brand} onChange={e => setEditState(s => s ? { ...s, brand: e.target.value } : s)} />
 <input style={{ ...inputS, width: 70 }} name={`edit-unit-${item.sku}`} aria-label={t('inventory.edit_unit')} placeholder={t('inventory.edit_unit')} value={editState.unit_of_measure} onChange={e => setEditState(s => s ? { ...s, unit_of_measure: e.target.value } : s)} />
 <input style={{ ...inputS, width: 120 }} name={`edit-barcode-${item.sku}`} aria-label={t('inventory.edit_barcode')} placeholder={t('inventory.edit_barcode')} value={editState.barcode} onChange={e => setEditState(s => s ? { ...s, barcode: e.target.value } : s)} />
 </div>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <input style={{ ...inputS, width: 80 }} name={`edit-current-stock-${item.sku}`} aria-label={t('inventory.col_current_stock')} type="number" min={0} value={editState.current_stock} onChange={e => setEditState(s => s ? { ...s, current_stock: e.target.value } : s)} />
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>{t('inventory.edit_stock_hint')}</div>
 </td>
 <td colSpan={2} style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}`, color: C.dim, fontSize: 11 }}>{t('inventory.edit_recalculated_on_save')}</td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <select
 style={{ ...inputS, width: 160 }}
 name={`edit-supplier-${item.sku}`} aria-label={t('inventory.col_provider')}
 value={editState.supplier}
 onChange={e => setEditState(s => s ? { ...s, supplier: e.target.value } : s)}
 >
 <option value="">{t('inventory.edit_no_provider_option')}</option>
 {suppliers.map(s => (
 <option key={s.id} value={s.name}>{s.name}</option>
 ))}
 </select>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <input style={{ ...inputS, width: 60 }} name={`edit-lead-time-${item.sku}`} aria-label={t('inventory.col_lead_time_days')} type="number" min={1} max={365} value={editState.lead_time_days} onChange={e => setEditState(s => s ? { ...s, lead_time_days: e.target.value } : s)} />
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>{t('inventory.edit_provider_days_hint')}</div>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <input style={{ ...inputS, width: 70 }} name={`edit-moq-${item.sku}`} aria-label={t('inventory.edit_min_per_order')} type="number" min={0} value={editState.moq} onChange={e => setEditState(s => s ? { ...s, moq: e.target.value } : s)} />
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
 {t('inventory.edit_min_per_order')}
 <HelpTip text={t('inventory.help_moq')} width={240} />
 </div>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}><AbcXyzBadge value={item.abc_xyz} /></td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
 <span style={{ fontSize: 11, color: C.dim }}>$</span>
 <input style={{ ...inputS, width: 80 }} name={`edit-unit-cost-${item.sku}`} aria-label={t('inventory.edit_provider_price_hint')} type="number" min={0} placeholder="0" value={editState.unit_cost} onChange={e => setEditState(s => s ? { ...s, unit_cost: e.target.value } : s)} />
 </div>
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>{t('inventory.edit_provider_price_hint')}</div>
 <div style={{ display: 'flex', gap: 4, alignItems: 'center', marginTop: 4 }}>
 <span style={{ fontSize: 11, color: C.dim }}>$</span>
 <input style={{ ...inputS, width: 80 }} name={`edit-sale-price-${item.sku}`} aria-label={t('inventory.edit_sale_price')} type="number" min={0} placeholder={t('inventory.edit_sale_price')} value={editState.sale_price} onChange={e => setEditState(s => s ? { ...s, sale_price: e.target.value } : s)} />
 </div>
 <div style={{ fontSize: 10, color: C.dim, marginTop: 4, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
 {t('inventory.edit_service_level_label')}
 <HelpTip text={t('inventory.help_service_level')} width={260} />
 </div>
 <select style={{ ...inputS, width: 90 }} name={`edit-service-level-${item.sku}`} aria-label={t('inventory.edit_service_level_label')} value={editState.service_level}
 onChange={e => setEditState(s => s ? { ...s, service_level: e.target.value } : s)}>
 <option value="0.90">90% — {t('inventory.service_level_low')}</option>
 <option value="0.95">95% — {t('inventory.service_level_normal')}</option>
 <option value="0.97">97% — {t('inventory.service_level_high')}</option>
 <option value="0.99">99% — {t('inventory.service_level_max')}</option>
 </select>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', gap: 4 }}>
 <button onClick={() => commitEdit(item.sku)} disabled={saving} style={{ all: 'unset', cursor: saving ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: '5px 12px', borderRadius: 6, fontSize: 11, fontWeight: 600, background: C.green, color: '#fff', opacity: saving ? 0.6 : 1 }}>
 {saving ? <Spinner size={10} /> : <Save size={10} />} {t('inventory.btn_save')}
 </button>
 <button onClick={cancelEdit} aria-label={t('common.cancel')} title={t('common.cancel')} style={{ all: 'unset', cursor: 'pointer', padding: '5px 8px', borderRadius: 6, border: `1px solid ${C.border}`, color: C.dim, fontSize: 11 }}><X size={11} aria-hidden="true" /></button>
 </div>
 </td>
 </tr>
 )

 return (
 // The row and its expanded explanation are two <tr>s from one iteration:
 // the key belongs on the fragment, not on the first child, or React sees an
 // unkeyed list item and re-creates both on every reorder.
 <Fragment key={item.sku}>
 <tr
 style={{ background: crit ? 'rgba(239,68,68,0.02)' : rowBg, borderLeft: `3px solid ${crit ? C.red : 'transparent'}`, transition: 'background 0.1s' }}
 onMouseEnter={e => (e.currentTarget.style.background = 'color-mix(in srgb, var(--accent) 4%, transparent)')}
 onMouseLeave={e => (e.currentTarget.style.background = crit ? 'rgba(239,68,68,0.02)' : rowBg)}
 >
 {/* Expand button */}
 <td style={{ padding: '10px 6px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 {item.calc_explanation && (
 <button
 onClick={() => setExpandedSku(isExpanded ? null : item.sku)}
 title={t('inventory.title_see_calculation')}
 style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 4 }}
 onMouseEnter={e => (e.currentTarget.style.color = C.indigo)}
 onMouseLeave={e => (e.currentTarget.style.color = C.dim)}
 >
 <ChevronRight size={12} style={{ transform: isExpanded ? 'rotate(90deg)' : undefined, transition: 'transform 0.15s' }} />
 </button>
 )}
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}><SignalBadge s={item.signal} /></td>
 {/* The product names the row: every other cell is only meaningful once you
     know which SKU it belongs to. */}
 <th scope="row" style={{ padding: '10px 12px', textAlign: 'left', fontWeight: 400, color: C.text, borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 <div style={{ fontWeight: 600, fontFamily: 'monospace', fontSize: 11 }}>{item.sku}</div>
 {item.display_name && <div style={{ fontSize: 11, color: C.muted, marginTop: 1 }}>{item.display_name}</div>}
 {item.supplier && <div style={{ fontSize: 10, color: C.dim, marginTop: 1 }}>{item.supplier}</div>}
 </th>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 {item.has_stock ? <span style={{ fontWeight: 600 }}>{fmt(item.current_stock, 0)}</span> : <span style={{ color: C.dim, fontSize: 11 }}>{t('inventory.no_record')}</span>}
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 <Sparkline data={item.stock_history} />
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 {item.coverage_days != null ? (
 <span style={{ fontWeight: 600, color: signalColor(item.signal) }}>
 {fmt(item.coverage_days, 0)} {coverageUnitShort(data?.coverage_unit, t)}
 </span>
 ) : '—'}
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, color: C.muted, fontFamily: 'monospace', fontSize: 11 }}>{fmt(item.lead_time_demand, 0)}</td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 {item.recommended_qty != null && item.recommended_qty > 0 ? (
 editingQtySku === item.sku ? (
 <input
 type="number" min={1} autoFocus
 name={`order-qty-${item.sku}`} aria-label={t('inventory.edit_qty_title')}
 defaultValue={effectiveQty(item)}
 onBlur={e => {
 const n = parseInt(e.target.value, 10)
 setEditedQty(prev => (!isNaN(n) && n > 0 ? { ...prev, [item.sku]: n } : prev))
 setEditingQtySku(null)
 }}
 onKeyDown={e => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
 style={{ width: 70, background: C.card, border: `1px solid ${C.indigo}`, borderRadius: 5, color: C.text, fontSize: 13, fontWeight: 700, padding: '3px 6px', outline: 'none' }}
 />
 ) : (
 <button
 onClick={() => setEditingQtySku(item.sku)}
 title={t('inventory.edit_qty_title')}
 style={{ all: 'unset', cursor: 'pointer', fontWeight: 700, fontSize: 13,
 color: editedQty[item.sku] != null ? C.indigo : C.green,
 borderBottom: `2px dashed ${(editedQty[item.sku] != null ? C.indigo : C.green)}60`, lineHeight: 1 }}
 >
 {fmt(effectiveQty(item), 0)}
 </button>
 )
 ) : item.recommended_qty === 0
 ? <span style={{ color: C.dim, fontSize: 11 }}>{t('inventory.dont_order')}</span>
 : '—'}
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, color: C.muted }}>{item.lead_time_days}d
 <SourceBadge source={item.lead_time_source} /></td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, color: C.muted }}>{fmt(item.moq, 0)}
 <SourceBadge source={item.moq_source} /></td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}><AbcXyzBadge value={item.abc_xyz} /></td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, fontFamily: 'monospace', fontSize: 11, color: C.muted }}>{item.inventory_value != null ? fmtCurrency(item.inventory_value) : '—'}</td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', gap: 4 }}>
 {item.calc_explanation && item.daily_demand && (
  <button onClick={() => setExpandedSku(isExpanded ? null : item.sku)} title={t('inventory.title_simulate_scenarios')}
   style={{ all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5, color: isExpanded ? C.indigo : C.dim, display: 'flex' }}
   onMouseEnter={e => (e.currentTarget.style.color = C.indigo)}
   onMouseLeave={e => (e.currentTarget.style.color = isExpanded ? C.indigo : C.dim)}>
   <Sliders size={13} />
  </button>
 )}
 <button onClick={() => startEdit(item)} title={t('inventory.title_edit')} style={{ all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5, color: C.dim, display: 'flex' }} onMouseEnter={e => (e.currentTarget.style.color = C.indigo)} onMouseLeave={e => (e.currentTarget.style.color = C.dim)}><Edit2 size={13} /></button>
 {item.has_stock && <button onClick={() => handleDelete(item.sku)} title={t('inventory.title_delete')} style={{ all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5, color: C.dim, display: 'flex' }} onMouseEnter={e => (e.currentTarget.style.color = C.red)} onMouseLeave={e => (e.currentTarget.style.color = C.dim)}><Trash2 size={13} /></button>}
 </div>
 </td>
 </tr>
 {/* Expanded explanation row — grows in place (see ExpandedCalcRow).
     Only the arrival is animated; collapsing is immediate, because the user
     already decided to close it. */}
 {isExpanded && item.calc_explanation && (
 <ExpandedCalcRow item={item} background={crit ? 'rgba(239,68,68,0.01)' : rowBg} />
 )}
 </Fragment>
 )
 })}
 </tbody>
 </table>
 <Pagination page={paged.page} pageCount={paged.pageCount} offset={paged.offset} total={paged.total} rowsOnPage={pageItems.length} onPage={setPage} label="SKU" />
 </div>
 )}
 </div>
 </>
 )}

 {/* Events panel */}
 <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>
 <button
 onClick={() => setShowEvents(v => !v)}
 style={{ all: 'unset', cursor: 'pointer', width: '100%', display: 'flex', alignItems: 'center', gap: 10, padding: '14px 20px', boxSizing: 'border-box' }}
 >
 <Calendar size={14} color={C.indigo} />
 <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{t('inventory.events_section_title')}</span>
 {upcomingAlerts.length > 0 && <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: 'rgba(245,158,11,0.1)', color: C.amber }}>{upcomingAlerts.length} {upcomingAlerts.length > 1 ? t('inventory.upcoming_plural') : t('inventory.upcoming_singular')}</span>}
 <ChevronDown size={13} color={C.dim} style={{ transform: showEvents ? 'rotate(180deg)' : undefined, transition: 'transform 0.2s' }} />
 </button>
 {showEvents && (
 <div style={{ padding: '0 20px 20px', borderTop: `1px solid ${C.border}` }}>
 <div style={{ fontSize: 12, color: C.dim, marginBottom: 14, marginTop: 12, lineHeight: 1.6 }}>
 {t('inventory.events_section_desc')}
 </div>
 <EventsPanel events={events} onAdd={handleAddEvent} onDelete={handleDeleteEvent} onSimulate={setSimEvent} onCatalogChange={reloadEvents} />
 </div>
 )}
 </div>

 {simEvent && sessionId && (
 <EventSimModal ev={simEvent} sessionId={sessionId} onClose={() => setSimEvent(null)} onReload={reloadEvents} />
 )}

 {showShrinkageModal && (
 <ShrinkageModal
  items={data?.items ?? []}
  onClose={() => setShowShrinkageModal(false)}
  onSaved={() => { if (sessionId) load(sessionId) }}
 />
 )}

 {deleteTarget && (
 <div onClick={() => setDeleteTarget(null)} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
 <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 400, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 14, padding: 24 }}>
 <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginBottom: 8 }}>
 {t('inventory.confirm_delete_prefix')} {deleteTarget}?
 </div>
 <p style={{ fontSize: 12, color: C.dim, margin: '0 0 18px', lineHeight: 1.5 }}>{t('inventory.confirm_delete_hint')}</p>
 <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
 <button onClick={() => setDeleteTarget(null)} style={{ all: 'unset', cursor: 'pointer', padding: '8px 16px', borderRadius: 8, border: `1px solid ${C.border}`, color: C.dim, fontSize: 13 }}>{t('common.cancel')}</button>
 <button onClick={confirmDelete} style={{ all: 'unset', cursor: 'pointer', padding: '8px 16px', borderRadius: 8, background: C.red, color: '#fff', fontSize: 13, fontWeight: 700 }}>{t('inventory.btn_delete_confirm')}</button>
 </div>
 </div>
 </div>
 )}

 {/* Legend */}
 {!loading && sessionId && (
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 20, fontSize: 11, color: C.dim, paddingBottom: 8, flexWrap: 'wrap' }}>
 {[
 { signal: t('inventory.signal_order_now'), desc: t('inventory.legend_order_now') },
 { signal: t('inventory.signal_order_soon'), desc: t('inventory.legend_order_soon') },
 { signal: t('inventory.signal_ok'), desc: t('inventory.legend_ok') },
 { signal: t('inventory.signal_overstock'), desc: t('inventory.legend_overstock') },
 { signal: t('inventory.signal_sin_datos'), desc: t('inventory.legend_sin_datos') },
 ].map(({ signal, desc }) => <span key={signal}><strong>{signal}</strong> — {desc}</span>)}
 </div>
 )}
 </div>
 )
}
