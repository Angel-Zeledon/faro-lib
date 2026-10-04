'use client'
import { useState, useEffect, useCallback, useRef, useMemo, Fragment } from 'react'
import Link from 'next/link'
import {
 getInventoryStatus, upsertInventoryStock, deleteInventoryStock,
 importInventoryCSV, exportInventoryPO, downloadInventoryPDF,
 listInventoryEvents, createInventoryEvent, updateInventoryEvent, deleteInventoryEvent,
 listSuppliers, getDeadCapital, simulateEvent, logPOGeneration, downloadInventoryTemplate,
 createShrinkage,
 getCalendarCatalog, seedCalendarCatalog, toggleCalendarEntry,
 listEventMultipliers, setEventMultiplier, deleteEventMultiplier,
 getSupplierCostInflation, getMarginErosion, getForecastMoney,
 getCostOfIgnoring, getWhyChanged, getSignalThresholds,
 ApiError,
} from '@/lib/api'
import type {
 InventoryStatusItem, InventorySignal,
 InventoryCalcExplanation, InventoryEvent, Supplier, DeadCapitalResponse, ExcludedSku,
 EventSimulationResult, POLineDecision, ShrinkageReason, CalendarCatalogEntry, CoverageUnit,
 EventMultiplier,
 SupplierCostInflationResponse, MarginErosionResponse, ForecastMoneyResponse,
 CostOfIgnoringResponse, WhyChangedResponse, WhyChangedFieldOrigin,
 SignalThresholdsState,
} from '@/lib/types'
import { useAutoSession } from '@/hooks/useAutoSession'
import Pagination, { usePage } from '@/components/table/Pagination'
import { useWarehouses, WarehouseSelector } from '@/components/inventory/WarehouseControls'
import { WarehouseStatusTable } from '@/components/inventory/WarehouseStatusTable'
import DataFreshness from '@/components/ui/DataFreshness'
import Spinner from '@/components/ui/Spinner'
import MenuButton from '@/components/ui/MenuButton'
import { EmptyState, ErrorState, InlineError, LoadingState, SkeletonCards, SkeletonTable, useErrorDetail } from '@/components/ui/States'
import HelpTip from '@/components/ui/HelpTip'
import SharedSignalBadge, { signalColor } from '@/components/ui/SignalBadge'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'
import { useToast } from '@/contexts/ToastContext'
import Tooltip from '@/components/ui/Tooltip'
import { formatMoney, formatMoneyCompact } from '@/lib/currency'
import { csvCell, csvNumber, buildCsv, downloadCsv } from '@/lib/csvWriter'
import { coverageUnitShort } from '@/lib/period'
import {
  DEFAULT_LEAD_TIME_DAYS, DEFAULT_MOQ, DEFAULT_SERVICE_LEVEL,
  isAssumed, sourceLabelKey, type ValueSource,
} from '@/lib/inventoryDefaults'
import { fmtNum } from '@/lib/numberLocale'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import MobileTabs from '@/components/mobile/MobileTabs'
import { BottomSheet, MobileList, MobileCard, StatusBadge, signalTone } from '@/components/mobile'
import {
 MobileMetricGrid, MobileStockCards, MobileProviderGroups, MobileSkuSheet, MobileEditSheet,
 MobileStockEntry, MobileControls, MField, mInput, signalLabel, moneyOr,
} from './InventoryMobile'
import StickyActionBar from '@/components/mobile/StickyActionBar'
import { incomingText } from '@/lib/incomingCopy'
import {
 ShoppingCart, AlertTriangle, CheckCircle2, TrendingDown, TrendingUp,
 ChevronDown, ChevronRight, RefreshCw, MoreHorizontal, Upload, Download, Edit2, Trash2,
 X, Save, Package, Info, Layers, List, FileText, Calendar, Plus, PencilLine, Truck, Sliders,
 Zap, PackageMinus, Search, PackagePlus, DollarSign,
} from 'lucide-react'

// Maps the active UI language to a concrete BCP-47 locale for date formatting,
// so dates follow the language toggle instead of always rendering in Spanish.
function localeFor(lang: string): string {
 return lang === 'en' ? 'en-US' : 'es-CR'
}

// A calendar date, read as the day it says.
//
// `new Date("2026-11-25")` is parsed as midnight UTC by the language spec, so
// `toLocaleDateString` in any negative-offset zone renders the day BEFORE.
// Every country this product sells to is negative-offset: measured in the
// browser, a Black Friday saved for 25–30 Nov was listed as "24 nov → 29 nov".
// Anchoring at noon puts the instant far enough from both midnights that no
// real-world offset can move the date.
function dayOf(iso: string): Date {
 return new Date(`${iso}T12:00:00`)
}

// Why a product the user uploaded is missing from the forecast. The backend
// sends a stable `reason` plus the numbers; `detail` is its English sentence,
// used only when the catalogue has no entry for that reason. It used to be the
// only field and it was Spanish, so this notice stayed Spanish in English mode.
function excludedReasonText(
 e: ExcludedSku,
 t: (k: string, p?: Record<string, unknown>) => string,
): string {
 const key = `inventory.excluded_reason.${e.reason}`
 const text = t(key, { n: e.n_rows, min: e.min_history ?? '' })
 return text === key ? e.detail : text
}

// `undefined` for a box the user left empty, so the field is omitted from the
// request body and the stored value survives. Any number the user actually
// typed goes through, including one the backend will reject — a rejected save
// tells them something; a silent 15 does not.
function leadTimeOrUnset(raw: string): number | undefined {
 const n = parseInt(raw, 10)
 return raw.trim() === '' || Number.isNaN(n) ? undefined : n
}

/** The lead time to send from a bulk-edit row: only when the user changed the
 *  box. The box starts at the RESOLVED value, and the backend stamps whatever
 *  it receives as the user's own choice. */
function changedLeadTime(
 draft: { lead_time_days: string },
 baseline: { lead_time_days: string } | undefined,
): number | undefined {
 if (baseline && draft.lead_time_days === baseline.lead_time_days) return undefined
 return leadTimeOrUnset(draft.lead_time_days)
}

// A dataset with no SKU column trains as ONE series, and the backend labels that
// row with the `__all__` sentinel's English name. Relabel it once here, on the
// way in, so every cell below — table, detail panel, edit form, CSV — shows the
// reader's language without each of them having to know about the sentinel.
//
// Matched against the backend's exact generated label, not against the sentinel
// alone: a user who typed their own name for that row keeps it.
const SINGLE_SERIES_FALLBACK = 'Single series (no SKU column)'

function withSingleSeriesLabel<T extends { items: InventoryStatusItem[] }>(
 data: T,
 t: (k: string) => string,
): T {
 if (!data?.items?.some(i => i.sku === '__all__' && i.display_name === SINGLE_SERIES_FALLBACK)) {
  return data
 }
 return {
  ...data,
  items: data.items.map(i =>
   i.sku === '__all__' && i.display_name === SINGLE_SERIES_FALLBACK
    ? { ...i, display_name: t('inventory.single_series_label') }
    : i),
 }
}

// ── Palette ───────────────────────────────────────────────────────────────────

// A row whose signal says "order" while the quantity is 0 is not a contradiction
// — the stock is still above the reorder point, so the answer is "not yet" — but
// "No pedir" next to "Pedir pronto", or a bare dash, reads like one. Say when it
// WILL be time, using the reorder point the API already sends.
function notYetLabel(
  item: InventoryStatusItem,
  t: (k: string, p?: Record<string, unknown>) => string,
): string {
  const ordering = item.signal === 'PEDIR_YA' || item.signal === 'PEDIR_PRONTO'
  if (!ordering) return t('inventory.dont_order')
  return item.reorder_point != null
    ? t('inventory.not_yet_at', { qty: Math.round(item.reorder_point) })
    : t('inventory.not_yet')
}

const C = {
 surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
 text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
 red: '#C0504D', amber: '#B7791F', green: '#2E8B62', blue: '#4F7FB5', indigo: 'var(--accent)',
}

// ── Signal config ─────────────────────────────────────────────────────────────
// The signal presentation (icon + label + accessible colour) lives in
// components/ui/SignalBadge — it used to be duplicated here and in
// SkuSearchOverlay, with colours that failed contrast in the light theme.
function SignalBadge({ s }: { s: InventorySignal }) {
 return <SharedSignalBadge signal={s} />
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

function ThTip({ label, tip, sortKey, sort, onSort, dataTour }: {
 label: string
 tip: string
 sortKey?: SortKey
 sort?: SortState | null
 onSort?: (key: SortKey) => void
 /** `data-tour` anchor, so a guided tour can point at this column. */
 dataTour?: string
}) {
 const { t } = useLanguage()
 const active = sortKey != null && sort?.key === sortKey
 const ariaSort: 'ascending' | 'descending' | 'none' | undefined =
  sortKey == null ? undefined : active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : 'none'

 const inner = <Tooltip text={tip}><span>{label}</span><Info size={9} color={C.dim} style={{ opacity: 0.5, flexShrink: 0 }} /></Tooltip>

 if (sortKey == null || !onSort) {
  return <th scope="col" data-tour={dataTour} style={thStyle}>{inner}</th>
 }

 // The next state this button produces, spelled out — an arrow glyph alone
 // tells a screen-reader user nothing about what activating it will do.
 const nextDir: SortDir = active && sort!.dir === 'asc' ? 'desc' : 'asc'
 const actionLabel = tOr(t,
  nextDir === 'asc' ? 'table.sort_ascending_by' : 'table.sort_descending_by',
  nextDir === 'asc' ? `Sort ascending by ${label}` : `Sort descending by ${label}`,
  { column: label })

 return (
  <th scope="col" aria-sort={ariaSort} data-tour={dataTour} style={{ ...thStyle, color: active ? 'var(--accent)' : C.dim }}>
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
const ABC_COLOR: Record<string, string> = { A: '#2E8B62', B: '#B7791F', C: '#64748b', '?': '#334155' }
const XYZ_COLOR: Record<string, string> = { X: 'var(--accent)', Y: '#B7791F', Z: '#C0504D', '?': '#334155' }
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

// ── My data, or StockAI's assumption? ────────────────────────────────────
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
        'StockAI assumed this value — you have not given us one yet.')}
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
 // The days the demand is multiplied by: the lead time, plus the supplier's
 // review period when one is declared — the protection interval the backend
 // actually multiplied by, so the step reads as arithmetic that adds up.
 { label: `× ${t('inventory.calc_step_lead_days')} (${exp.protection_interval_days ?? exp.lead_time_days}d${leadOrigin ? ` · ${leadOrigin}` : ''})`, value: `= ${exp.lead_time_demand!.toFixed(0)} ${unitWord}`, op: '×' },
 { label: `+ ${t('inventory.calc_step_safety_stock')}`, value: `+ ${exp.safety_stock!.toFixed(0)} ${unitWord}`, op: '+' },
 { label: `− ${t('inventory.calc_step_current_stock')}`, value: `− ${exp.current_stock!.toFixed(0)} ${unitWord}`, op: '−' },
 ...((exp.incoming ?? 0) > 0
  ? [{ label: `− ${t('inventory.calc_step_incoming')}`, value: `− ${exp.incoming!.toFixed(0)} ${unitWord}`, op: '−' }]
  : []),
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
 {/* A declared event (stability.md 19.5) that overlaps this sku's lead-time
     window right now — a standing fact the semáforo already applied, not a
     what-if. Kept as a short list, never a table: one line per event, and
     the override word only when the tenant's own SKU/category number is
     what actually fired instead of the event's own multiplier. */}
 {exp.events_applied && exp.events_applied.length > 0 && (
 <div style={{ marginTop: 10, paddingTop: 8, borderTop: `1px solid color-mix(in srgb, var(--accent) 15%, transparent)` }}>
 {exp.events_applied.length > 1 && (
 <div style={{ fontSize: 11, color: C.dim, marginBottom: 6 }}>
 {t('inventory.calc_events_intro', { n: exp.events_applied.length })}
 </div>
 )}
 <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
 {exp.events_applied.map(ev => (
 <div key={ev.event_id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: C.text }}>
 <Calendar size={12} color={C.indigo} aria-hidden="true" />
 <span>
 {t('inventory.why_event', {
 event: ev.event_name,
 multiplier: ev.multiplier.toFixed(1),
 overlap: ev.overlap_days,
 window: ev.window_days,
 })}
 </span>
 {ev.multiplier_source !== 'event' && (
 <span style={{ fontSize: 10, fontWeight: 600, color: C.indigo }}>
 ({t(ev.multiplier_source === 'sku' ? 'inventory.mult_origin_sku'
 : ev.multiplier_source === 'family' ? 'inventory.mult_origin_family'
 : 'inventory.mult_origin_category')})
 </span>
 )}
 </div>
 ))}
 </div>
 </div>
 )}
 </div>
 )
}

// ── Plain-language situation ────────────────────────────────────────────────
function ContextMessage({ summary }: { summary: Record<string, number> }) {
 const { t } = useLanguage()
 // The three signal lines that used to live here are gone: they repeated the
 // PEDIR_YA / PEDIR_PRONTO / SOBRESTOCK cards word for word, one row below.
 // Their sentences now sit under the number they describe, which is where a
 // reader was going to look anyway. What is left is the cases with NO card of
 // their own — the ones about SKUs the semáforo cannot judge.
 const lines: { text: string; color: string }[] = []
 // "Nothing to do today" is gated on the SIGNALS, never on whether this box
 // happens to have printed something. It used to read `!lines.length`, which
 // was only ever true when the three actionable lines above had not fired —
 // and the moment those lines moved into the KPI cards, the guard silently
 // became "always", so the panel wrote "Todo el inventario está bien cubierto"
 // in green over a screen showing four PEDIR_YA. A condition that depends on a
 // sibling's side effect is a trap; this one is the count itself.
 const actionable = (summary.order_now ?? 0) + (summary.order_soon ?? 0) + (summary.overstock ?? 0)
 // SIN_DATOS travels in the payload and was never read here, so five SKUs in
 // OK and five hundred with no stock on file produced a green sentence saying
 // ALL the inventory was covered. It says nothing about those five hundred —
 // they have no signal at all — and "todo" was the word doing the damage.
 const unknown = summary.sin_datos ?? 0
 if (actionable === 0 && summary.ok > 0)
 lines.push({
   text: unknown > 0
     ? `${summary.ok} ${t('inventory.ctx_covered_partial_suffix')} ${unknown} ${t('inventory.ctx_no_signal_suffix')}`
     : t('inventory.ctx_all_covered'),
   color: unknown > 0 ? '#B7791F' : '#2E8B62',
 })
 // Nothing actionable, nothing OK, and SKUs we cannot judge: the panel used to
 // render nothing at all, which reads as "no news is good news".
 if (actionable === 0 && !lines.length && unknown > 0)
 lines.push({ text: `${unknown} ${t('inventory.ctx_only_no_signal_suffix')}`, color: '#B7791F' })
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
// An event multiplier cannot be a number with no provenance: if StockAI
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

 const narrow = useIsNarrow()
 const inputS3: React.CSSProperties = {
  background: C.card, border: `1px solid ${C.border}`, borderRadius: 6,
  color: C.text, fontSize: 11, outline: 'none', padding: '5px 8px',
  ...(narrow ? { fontSize: 16, minHeight: 44, boxSizing: 'border-box', borderRadius: 10 } : {}),
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
     style={{ all: 'unset', cursor: 'pointer', flexShrink: 0, fontSize: 11, fontWeight: 600, color: C.indigo, padding: '2px 4px',
      ...(narrow ? { minHeight: 44, display: 'inline-flex', alignItems: 'center', fontSize: 13, padding: '0 6px' } : {}) }}
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
       style={{ all: 'unset', cursor: busy || !form.value.trim() ? 'not-allowed' : 'pointer', padding: '5px 12px', borderRadius: 6, background: C.indigo, color: '#fff', fontSize: 11, fontWeight: 600, opacity: busy || !form.value.trim() ? 0.5 : 1,
       ...(narrow ? { minHeight: 44, boxSizing: 'border-box', display: 'inline-flex', alignItems: 'center', fontSize: 14, borderRadius: 10, padding: '0 16px' } : {}) }}
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
 const narrow = useIsNarrow()
 const [result, setResult] = useState<EventSimulationResult | null>(null)
 const [error, setError] = useState<string | null>(null)

 // Deliberately does NOT clear `result` first: re-running keeps the numbers on
 // screen until the new ones land, so the per-product editor the user is typing
 // in (rendered only when there is a result) does not vanish under them.
 const runSimulation = useCallback(() => {
 setError(null)
 simulateEvent({ session_id: sessionId, event_id: ev.id })
 .then(setResult)
 .catch(e => setError(e instanceof Error ? e.message : t('inventory.sim_err_failed')))
 // `t` is stable per language and re-running the simulation on a language
 // switch would be a pointless request.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [ev.id, sessionId])

 useEffect(() => { runSimulation() }, [runSimulation])

 const fmtD = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString(localeFor(lang), { day: 'numeric', month: 'long' })

 // What the simulation ACTUALLY ran with, not what the event header says.
 //
 // `ev.multiplier` is the event's own number, and a per-SKU or per-category
 // override replaces it for the products it covers. Measured: with SPIKE-01
 // overridden to x3.0, the headline still announced "+100% de demanda" and the
 // footnote still explained "× 4 días × 2.0" while the only row on screen said
 // x3.0 and carried 466 units. The two sentences that frame the table described
 // a calculation that did not happen.
 //
 // `multipliers_applied` is the breakdown the backend already sends. One entry
 // means every product ran on the same number and the sentences can name it;
 // more than one means there is no single uplift to claim, and saying so is the
 // only honest option.
 const applied = result?.multipliers_applied ?? []
 const uniformMult = applied.length === 1 ? applied[0].multiplier
                   : applied.length === 0 ? ev.multiplier
                   : null
 const pctExtra = uniformMult != null ? Math.round((uniformMult - 1) * 100) : null

 // The date the headline may still ask for.
 //
 // `summary.order_before` is the EARLIEST order_by among the products at risk,
 // past or not, so an event three days away with a nine-day supplier produced
 // "Pide antes del 10 de agosto" on the 16th — an instruction nobody can carry
 // out, printed in the most prominent sentence on the screen, while the row for
 // that same product said "¡hoy mismo!" and the line underneath said it was
 // already too late. Three statements about one product, one of them impossible.
 //
 // So the sentence takes the earliest deadline that has NOT passed: the rows
 // that are late are covered by the red line below, which is the honest thing
 // to say about them. When every deadline is behind us there is no date to ask
 // for and the sentence drops out entirely.
 const orderBefore = result
   ? result.items
       .filter(r => r.en_risk && !r.llega_tarde && r.order_by)
       .map(r => r.order_by)
       .sort()[0] ?? null
   : null

 // A render function, not a component: a component declared here would be a
 // new type every render and remount the multiplier editor under the user.
 const frame = (children: React.ReactNode) => narrow ? (
  <BottomSheet open onClose={onClose} maxHeight="92dvh"
   title={tOr(t, 'inventory.sim_modal_title', `Simulation: ${ev.name}`, { event: ev.name })}>
   <div style={{ minWidth: 0 }}>{children}</div>
  </BottomSheet>
 ) : (
  <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
   <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 640, maxHeight: '85vh', overflowY: 'auto', background: C.surface, border: `1px solid ${C.border}`, borderRadius: 14, padding: 24 }}>
    {children}
   </div>
  </div>
 )

 return frame(<>
 <div style={{ display: narrow ? 'none' : 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
 <Zap size={16} color={C.amber} />
 <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>
 {tOr(t, 'inventory.sim_modal_title', `Simulation: ${ev.name}`, { event: ev.name })}
 </span>
 <button onClick={onClose} aria-label={t('common.close')} style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', color: C.dim }}><X size={16} aria-hidden="true" /></button>
 </div>
 <p style={{ margin: '0 0 16px', fontSize: 12, color: C.dim }}>
 {fmtD(ev.start_date)} → {fmtD(ev.end_date)} · {pctExtra != null
  ? tOr(t, 'inventory.sim_modal_uplift', `estimated demand +${pctExtra}%`, { pct: pctExtra })
  : tOr(t, 'inventory.sim_modal_uplift_mixed', "demand estimated with each product's own multiplier")}
 </p>

 {!result && !error && <div style={{ padding: 24, textAlign: 'center' }}><Spinner size={16} /></div>}
 {error && <div style={{ padding: '10px 14px', borderRadius: 8, background: 'rgba(192,80,77,0.08)', fontSize: 13, color: C.red }}>{error}</div>}

 {result && (
 <>
 {/* Headline — the actionable sentence */}
 <div style={{
 padding: '14px 18px', borderRadius: 10, marginBottom: 16,
 background: result.summary.skus_at_risk > 0 ? 'rgba(183,121,31,0.07)' : 'rgba(46,139,98,0.07)',
 border: `1px solid ${result.summary.skus_at_risk > 0 ? 'rgba(183,121,31,0.3)' : 'rgba(46,139,98,0.3)'}`,
 fontSize: 13, color: C.text, lineHeight: 1.6,
 }}>
 {result.summary.skus_at_risk > 0 ? (
 <>
 <Emphasized text={tOr(
  t,
  pctExtra != null
   ? (result.summary.skus_at_risk === 1 ? 'inventory.sim_headline_risk_one' : 'inventory.sim_headline_risk_many')
   : (result.summary.skus_at_risk === 1 ? 'inventory.sim_headline_risk_one_mixed' : 'inventory.sim_headline_risk_many_mixed'),
  `With *${ev.name}* you would need to order *${result.summary.total_to_order.toLocaleString()} extra units* across *${result.summary.skus_at_risk} ${result.summary.skus_at_risk === 1 ? 'product' : 'products'}*.`,
  {
   event: ev.name,
   pct:   pctExtra ?? 0,
   units: result.summary.total_to_order.toLocaleString(),
   skus:  result.summary.skus_at_risk,
  },
 )} />
 {orderBefore && (
  <Emphasized text={tOr(t, 'inventory.sim_headline_order_before',
   ` Order before *${fmtD(orderBefore)}*.`,
   { date: fmtD(orderBefore) })} />
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
 <Emphasized text={tOr(t,
  pctExtra != null ? 'inventory.sim_headline_safe' : 'inventory.sim_headline_safe_mixed',
  `Your current stock survives *${ev.name}* with no extra orders.`,
  { event: ev.name, pct: pctExtra ?? 0 })} />
 )}
 </div>

 {/* Why this multiplier — never show a x2.2 without justifying it */}
 {/* Editing a multiplier has to re-run the simulation, not just reload the
     event list behind the modal. Measured: setting SPIKE-01 to x3.0 saved the
     override and left the table showing x2.0 and 311 units — the user changes
     the number the whole screen is derived from and the screen keeps the old
     answer, with nothing saying it is stale. */}
 <MultiplierExplainer
 result={result}
 eventId={ev.id}
 onEdited={() => { onReload(); runSimulation() }}
 />

 {narrow ? (
 <MobileList ariaLabel={t('inventory.sim_col_product')}>
 {result.items.map(r => (
  <MobileCard key={r.sku}
   title={r.display_name || r.sku}
   subtitle={`${t('inventory.sim_col_event_demand')} ${r.event_units.toLocaleString()} (+${r.extra_units.toLocaleString()}) · ${t('inventory.sim_col_stock_at_start')} ${r.stock_al_inicio != null ? r.stock_al_inicio.toLocaleString() : '—'}`}
   value={r.qty_to_order ? r.qty_to_order.toLocaleString() : '—'}
   valueCaption={t('inventory.sim_col_order')}
   status={r.en_risk ? { label: r.deficit != null && r.deficit > 0 ? `${t('inventory.sim_col_shortfall')} ${r.deficit.toLocaleString()}` : t('inventory.sim_col_shortfall'), tone: 'danger' } : undefined}>
   <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 12.5 }}>
    <MultiplierChip value={r.multiplier} origin={r.multiplier_source} />
    {r.en_risk && (
     <span style={{ color: r.llega_tarde ? C.red : C.muted, fontWeight: r.llega_tarde ? 700 : 400 }}>
      {t('inventory.sim_col_order_before')}: {r.llega_tarde ? tOr(t, 'inventory.sim_order_today', 'today!') : fmtD(r.order_by)}
     </span>
    )}
   </span>
  </MobileCard>
 ))}
 </MobileList>
 ) : (
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
 <tr key={r.sku} style={{ borderBottom: `1px solid ${C.border}`, background: r.en_risk ? 'rgba(183,121,31,0.04)' : undefined }}>
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
 )}
 <p style={{ margin: '14px 0 0', fontSize: 11, color: C.dim, lineHeight: 1.5 }}>
 {tOr(t, 'inventory.sim_footer_note',
  `Calculation: forecast daily demand × ${result.event_days} days × ${uniformMult != null ? uniformMult.toFixed(1) : "each product's own multiplier"}, against the stock projected at the start of the event. Quantities respect each product's MOQ. Nothing is saved — this is only a simulation.`,
  {
   days: `${result.event_days} ${tOr(t, result.event_days === 1 ? 'inventory.sim_day_one' : 'inventory.sim_day_many', result.event_days === 1 ? 'day' : 'days')}`,
   mult: uniformMult != null
    ? uniformMult.toFixed(1)
    : tOr(t, 'inventory.sim_footer_mult_mixed', "each product's own multiplier"),
  },
 )}
 </p>
 </>
 )}
 </>)
}

// ── Shrinkage modal (record a non-sale stock-out: breakage/expiry/self-consumption/gift) ──
const SHRINKAGE_REASONS: ShrinkageReason[] = ['breakage', 'expiry', 'self_consumption', 'gift']

/**
 * `warehouses` / `defaultWarehouse` exist because the units come off a PLACE.
 *
 * The modal used to ask only for the SKU and the quantity while showing stock
 * SUMMED across warehouses, and `record_shrinkage` resolved `principal`. A
 * crate broken in Norte came off principal: the tenant total still reconciled,
 * so nothing looked wrong, while the per-warehouse semáforo believed Norte held
 * 400 units that did not exist (stability 11.8). The loud variant was worse —
 * a tenant whose stock arrived with a warehouse column has no `principal` row
 * at all, so every shrinkage 404'd blaming the SKU for a warehouse the user
 * never chose.
 */
function ShrinkageModal({ items, warehouses, defaultWarehouse, onClose, onSaved }: {
 items: InventoryStatusItem[]
 warehouses: string[]
 defaultWarehouse: string | null
 onClose: () => void
 onSaved: () => void
}) {
 const { t } = useLanguage()
 const { addToast } = useToast()
 const narrow = useIsNarrow()
 const [sku, setSku] = useState('')
 const [quantity, setQuantity] = useState('')
 const [reason, setReason] = useState<ShrinkageReason>('breakage')
 const [notes, setNotes] = useState('')
 const [warehouse, setWarehouse] = useState<string>(defaultWarehouse ?? warehouses[0] ?? '')
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
   await createShrinkage({
    sku: selected.sku, quantity: qtyNum, reason,
    // Sent whenever the tenant has more than one place to lose stock from.
    warehouse: warehouses.length > 1 ? (warehouse || undefined) : undefined,
    notes: notes || undefined,
   })
   addToast(t('inventory.shrinkage_toast_title'), `${qtyNum} ${t('inventory.calc_unit_units')} ${t('inventory.shrinkage_toast_body')} ${selected.sku}`, 'success')
   onSaved()
   onClose()
  } catch (e) {
   setError(e instanceof Error ? e.message : String(e))
  } finally {
   setSaving(false)
  }
 }

 // Phone: the same form, one field per row, in a sheet with the submit
 // pinned under it. Same fields, same validation, same call.
 if (narrow) return (
  <BottomSheet open onClose={onClose} title={t('inventory.shrinkage_title_register')}
   footer={(
    <div style={{ display: 'flex', gap: 8, width: '100%' }}>
     <button type="button" className="mobile-btn mobile-btn-secondary" onClick={onClose}>{t('common.cancel')}</button>
     <button type="button" className="mobile-btn mobile-btn-danger" onClick={handleSubmit} disabled={saving}>
      {saving && <Spinner size={14} />} {saving ? t('inventory.shrinkage_btn_submitting') : t('inventory.shrinkage_btn_submit')}
     </button>
    </div>
   )}>
   <form onSubmit={e => { e.preventDefault(); void handleSubmit() }} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
    <p style={{ margin: 0, fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('inventory.shrinkage_subtitle')}</p>
    <MField label={t('inventory.shrinkage_field_sku')}
     hint={selected ? <>{t('inventory.shrinkage_current_stock_prefix')} <strong style={{ color: C.text }}>{fmt(selected.current_stock, 0)}</strong></> : undefined}>
     <input name="shrinkage_sku" list="shrinkage-sku-options-m" style={mInput} autoComplete="off"
      placeholder={t('inventory.shrinkage_sku_placeholder')} value={sku} onChange={e => setSku(e.target.value)} />
    </MField>
    <datalist id="shrinkage-sku-options-m">
     {items.map(i => <option key={i.sku} value={i.sku}>{i.display_name || i.sku}</option>)}
    </datalist>
    {warehouses.length > 1 && (
     <MField label={t('inventory.shrinkage_field_warehouse')}>
      <select name="shrinkage_warehouse" style={mInput} value={warehouse} onChange={e => setWarehouse(e.target.value)}>
       {warehouses.map(w => <option key={w} value={w}>{w}</option>)}
      </select>
     </MField>
    )}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
     <MField label={t('inventory.shrinkage_field_quantity')}>
      <input name="shrinkage_quantity" type="number" inputMode="decimal" min={0} step="any" style={mInput} value={quantity} onChange={e => setQuantity(e.target.value)} />
     </MField>
     <MField label={t('inventory.shrinkage_field_reason')}>
      <select name="shrinkage_reason" style={mInput} value={reason} onChange={e => setReason(e.target.value as ShrinkageReason)}>
       {SHRINKAGE_REASONS.map(r => <option key={r} value={r}>{t(`inventory.shrinkage_reason_${r}`)}</option>)}
      </select>
     </MField>
    </div>
    <MField label={t('inventory.shrinkage_field_notes')}>
     <input name="shrinkage_notes" style={mInput} placeholder={t('inventory.shrinkage_notes_placeholder')} value={notes} onChange={e => setNotes(e.target.value)} enterKeyHint="done" />
    </MField>
    {estCost != null && (
     <div style={{ fontSize: 13, color: C.dim }}>
      {t('inventory.shrinkage_estimated_cost_prefix')} <strong style={{ color: C.red }}>{fmtCurrency(estCost)}</strong>
     </div>
    )}
    {error && <div role="alert" style={{ padding: '10px 12px', borderRadius: 10, background: 'rgba(192,80,77,0.08)', fontSize: 13, color: C.red }}>{error}</div>}
    <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
   </form>
  </BottomSheet>
 )

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

     {/* One control, and only when the tenant has more than one place to lose
         stock from. The screen was deliberately simplified to 26 controls
         (stability 1.septies), so this appears for the tenants who need it
         and for nobody else — and without it the write lands on `principal`
         whatever the buyer meant (11.8). */}
     {warehouses.length > 1 && (
      <div style={{ marginBottom: 12 }}>
       <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>
        {t('inventory.shrinkage_field_warehouse')}
       </div>
       <select
        id="shrinkage-warehouse"
        name="shrinkage_warehouse"
        aria-label={t('inventory.shrinkage_field_warehouse')}
        style={inputM}
        value={warehouse}
        onChange={e => setWarehouse(e.target.value)}
       >
        {warehouses.map(w => <option key={w} value={w}>{w}</option>)}
       </select>
      </div>
     )}

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
      <div style={{ padding: '8px 12px', borderRadius: 8, background: 'rgba(192,80,77,0.08)', fontSize: 12, color: C.red }}>{error}</div>
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
 const narrow = useIsNarrow()
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
  // Optimistic: the toggle has to feel immediate.
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
   <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: narrow ? 'wrap' : undefined }}>
    <div style={{ fontSize: narrow ? 13 : 11, color: C.dim, lineHeight: 1.5 }}>
     {t('inventory.calendar_intro')}
     {countries.length > 1 && (
      <>
       {' '}
       <label htmlFor="cal-country" style={{ marginLeft: 4 }}>{t('inventory.calendar_country')}</label>{' '}
       <select
        id="cal-country"
        value={country}
        onChange={e => { setEntries(null); setCountry(e.target.value) }}
        style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 5, color: C.text, fontSize: 11, padding: '2px 5px',
         ...(narrow ? { fontSize: 16, minHeight: 44, borderRadius: 10, marginTop: 6 } : {}) }}
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
      style={{ all: 'unset', cursor: busy ? 'wait' : 'pointer', flexShrink: 0, padding: '6px 12px', borderRadius: 7, background: C.indigo, color: '#fff', fontSize: 12, fontWeight: 600, opacity: busy === '__seed__' ? 0.6 : 1,
       ...(narrow ? { minHeight: 44, boxSizing: 'border-box', display: 'inline-flex', alignItems: 'center', fontSize: 14, borderRadius: 10, padding: '0 16px' } : {}) }}
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
        ...(narrow ? { outline: '12px solid transparent', outlineOffset: 0, margin: '0 4px' } : {}),
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
     style={{ all: 'unset', cursor: 'pointer', fontSize: 11, color: C.dim, padding: '5px 0', textAlign: 'center',
      ...(narrow ? { minHeight: 44, fontSize: 13 } : {}) }}
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
 const narrow = useIsNarrow()
 const [adding, setAdding] = useState(false)
 const [tab, setTab] = useState<'mine' | 'catalog'>('mine')
 const [form, setForm] = useState({ name: '', start_date: '', end_date: '', multiplier: '1.5', notes: '' })

 const visible = events.filter(e => e.active !== false)
 const upcoming = visible.filter(e => dayOf(e.end_date) >= new Date())
 const past = visible.filter(e => dayOf(e.end_date) < new Date())

 function handleAdd() {
 if (!form.name || !form.start_date || !form.end_date) return
 onAdd({ name: form.name, start_date: form.start_date, end_date: form.end_date, multiplier: parseFloat(form.multiplier) || 1.5, notes: form.notes || null })
 setForm({ name: '', start_date: '', end_date: '', multiplier: '1.5', notes: '' })
 setAdding(false)
 }

 const inputS2: React.CSSProperties = { background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, color: C.text, fontSize: 12, outline: 'none', padding: '6px 9px', width: '100%', boxSizing: 'border-box',
  ...(narrow ? { fontSize: 16, minHeight: 44, borderRadius: 10, padding: '8px 10px' } : {}) }
 // Phone: 44px buttons for the event actions.
 const tapS: React.CSSProperties = narrow ? { minHeight: 44, boxSizing: 'border-box', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' } : {}

 const TODAY_LABEL = t('inventory.day_today')
 const TOMORROW_LABEL = t('inventory.day_tomorrow')
 const daysUntil = (date: string) => {
 const d = Math.round((dayOf(date).getTime() - Date.now()) / 86400000)
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
  ...(narrow ? { ...tapS, flex: 1, fontSize: 14, borderRadius: 10 } : {}),
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
 <div key={ev.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderRadius: 8, background: isClose ? 'rgba(183,121,31,0.06)' : C.card, border: `1px solid ${isClose ? 'rgba(183,121,31,0.25)' : C.border}`, ...(narrow ? { flexWrap: 'wrap', gap: 8, padding: '12px', borderRadius: 12 } : {}) }}>
 <Calendar size={14} color={isClose ? C.amber : C.dim} style={{ flexShrink: 0 }} />
 <div style={{ flex: 1, minWidth: narrow ? 'calc(100% - 90px)' : 0 }}>
 <div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{ev.name}</div>
 <div style={{ fontSize: 11, color: C.dim, marginTop: 1 }}>
 {dayOf(ev.start_date).toLocaleDateString(localeFor(lang), { day: 'numeric', month: 'short' })}
 {ev.end_date !== ev.start_date && ` → ${dayOf(ev.end_date).toLocaleDateString(localeFor(lang), { day: 'numeric', month: 'short' })}`}
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
 style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px', borderRadius: 7, border: `1px solid rgba(183,121,31,0.4)`, color: 'var(--signal-order-soon-fg)', fontSize: 11, fontWeight: 600, flexShrink: 0,
  ...(narrow ? { ...tapS, flex: 1, fontSize: 14, borderRadius: 10, gap: 6 } : {}) }}
 >
 <Zap size={11} aria-hidden="true" /> {t('inventory.events_btn_simulate')}
 </button>
 <button
 onClick={() => onDelete(ev.id)}
 aria-label={`${t('inventory.events_btn_delete')}: ${ev.name}`}
 title={t('inventory.events_btn_delete')}
 style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 4,
  ...(narrow ? { ...tapS, minWidth: 44, border: `1px solid ${C.border}`, borderRadius: 10 } : {}) }}
 onMouseEnter={e => (e.currentTarget.style.color = C.red)}
 onMouseLeave={e => (e.currentTarget.style.color = C.dim)}
 >
 <Trash2 size={narrow ? 16 : 12} aria-hidden="true" />
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
 <div style={{ display: 'grid', gridTemplateColumns: narrow ? 'repeat(2, minmax(0, 1fr))' : '1fr 1fr 1fr', gap: 8 }}>
 <div>
 <div style={{ fontSize: 10, color: C.dim, marginBottom: 3 }}>{t('inventory.events_start_date')}</div>
 <input style={inputS2} name="event_start_date" aria-label={t('inventory.events_start_date')} type="date" value={form.start_date} onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))} />
 </div>
 <div>
 <div style={{ fontSize: 10, color: C.dim, marginBottom: 3 }}>{t('inventory.events_end_date')}</div>
 <input style={inputS2} name="event_end_date" aria-label={t('inventory.events_end_date')} type="date" value={form.end_date} onChange={e => setForm(f => ({ ...f, end_date: e.target.value }))} />
 </div>
 <div style={narrow ? { gridColumn: '1 / -1' } : undefined}>
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
 <button onClick={() => setAdding(false)} className={narrow ? 'mobile-btn mobile-btn-secondary' : undefined} style={narrow ? undefined : { all: 'unset', cursor: 'pointer', padding: '6px 12px', borderRadius: 6, border: `1px solid ${C.border}`, color: C.dim, fontSize: 12 }}>{t('common.cancel')}</button>
 <button onClick={handleAdd} disabled={!form.name || !form.start_date || !form.end_date} className={narrow ? 'mobile-btn mobile-btn-primary' : undefined} style={narrow ? undefined : { all: 'unset', cursor: 'pointer', padding: '6px 14px', borderRadius: 6, background: C.indigo, color: '#fff', fontSize: 12, fontWeight: 600, opacity: !form.name || !form.start_date || !form.end_date ? 0.5 : 1 }}>{t('inventory.events_btn_save')}</button>
 </div>
 </div>
 ) : (
 <button onClick={() => setAdding(true)} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, border: `1px dashed ${C.border}`, color: C.dim, fontSize: 12, justifyContent: 'center', ...(narrow ? { ...tapS, fontSize: 14, borderRadius: 12 } : {}) }}>
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
 {critical > 0 && <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: 'rgba(192,80,77,0.1)', color: C.red }}>{critical} {critical !== 1 ? t('inventory.urgent_plural') : t('inventory.urgent_singular')}</span>}
 {warning > 0 && <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: 'rgba(183,121,31,0.1)', color: C.amber }}>{warning} {t('inventory.soon_suffix')}</span>}
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
 ? <span style={{ color: C.dim, fontSize: 11 }}>{notYetLabel(item, t)}</span>
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
// The simulator answers "how would the order MOVE if…", so it is anchored to
// the backend's own recommendation and only adds the difference between two
// runs of this approximation (changed inputs minus unchanged ones). It used to
// print its own approximation as the "with changes" figure, and that
// approximation disagreed with the real one before any slider moved: it read
// a per-WEEK demand as per day (7x on a weekly tenant), rounded up to MOQ
// MULTIPLES (need 520, minimum 500 -> 1000), and ignored stock already on its
// way — a fake delta at the defaults (math audit 2026-10-01).
function simulateRecommendation(
 currentStock: number,
 dailyDemand: number,
 avgStd: number,
 leadTime: number,
 moq: number,
 incoming = 0,
): number {
 const z = 1.645
 const demandLT = dailyDemand * leadTime
 const safetyStock = z * avgStd * Math.sqrt(leadTime)
 const raw = Math.max(0, demandLT + safetyStock - currentStock - incoming)
 // MOQ is a MINIMUM, never a multiple — the backend's _calc_recommended.
 if (raw > 0 && moq > 0) return Math.max(Math.ceil(raw), moq)
 return Math.round(raw)
}

function SimulatorPanel({ item }: { item: InventoryStatusItem }) {
 const { t } = useLanguage()
 // Inside the phone's detail sheet the three sliders stack: a third of 328px
 // is not a slider a thumb can hold.
 const narrow = useIsNarrow()
 const exp = item.calc_explanation
 if (!exp || !item.daily_demand || item.daily_demand <= 0) return null

 const [ltDelta,    setLtDelta]    = useState(0)
 const [demandMult, setDemandMult] = useState(100)
 const [stockDelta, setStockDelta] = useState(0)

 // Per DAY, from the breakdown (the status row's `daily_demand` is per bucket
 // of the planning period); the days are the protection interval the
 // backend multiplied by (lead time + review period).
 const baseDemand  = exp.daily_demand ?? 0
 const origLT      = exp.protection_interval_days ?? item.lead_time_days ?? DEFAULT_LEAD_TIME_DAYS
 const simLeadTime = Math.max(1, origLT + ltDelta)
 const simDemand   = baseDemand * demandMult / 100
 const simStock    = Math.max(0, (item.current_stock ?? 0) + stockDelta)
 const incoming    = exp.incoming ?? 0

 // Approximate a per-day sigma from the published safety stock:
 // safety_stock = z * avgStd * sqrt(days) -> avgStd = safety_stock / (z * sqrt(days))
 const origSS = exp.safety_stock ?? 0
 const z      = 1.645
 const avgStd = origLT > 0 ? origSS / (z * Math.sqrt(origLT)) : 0

 const moq            = item.moq ?? 1
 const baseline       = simulateRecommendation(item.current_stock ?? 0, baseDemand, avgStd, origLT, moq, incoming)
 const changed        = simulateRecommendation(simStock, simDemand, avgStd, simLeadTime, moq, incoming)
 const originalRec    = item.recommended_qty ?? 0
 const simRecommended = Math.max(0, originalRec + changed - baseline)
 const delta          = simRecommended - originalRec
 const deltaColor     = delta > 0 ? '#C0504D' : delta < 0 ? '#2E8B62' : C.muted

 const sliderS: React.CSSProperties = { width: '100%', cursor: 'pointer', accentColor: 'var(--accent)', ...(narrow ? { height: 36, margin: 0 } : {}) }

 return (
  <div style={{
   background: 'color-mix(in srgb, var(--accent) 4%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 15%, transparent)',
   borderRadius: 8, padding: '16px 18px', marginTop: 8,
  }}>
   <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent)', marginBottom: 14,
    textTransform: 'uppercase', letterSpacing: '0.07em' }}>
    {t('inventory.sim_title')}
   </div>

   <div style={{ display: 'grid', gridTemplateColumns: narrow ? '1fr' : '1fr 1fr 1fr', gap: narrow ? 20 : 16 }}>
    {/* Lead time slider */}
    <div>
     <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: C.muted, marginBottom: 6 }}>
      <span>{t('inventory.sim_lead_time')}</span>
      <span style={{ fontWeight: 700, color: ltDelta !== 0 ? '#B7791F' : C.text }}>
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
      <span style={{ fontWeight: 700, color: demandMult !== 100 ? '#B7791F' : C.text }}>
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
      <span style={{ fontWeight: 700, color: stockDelta !== 0 ? '#B7791F' : C.text }}>
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
    display: 'flex', alignItems: 'center', gap: 16, flexWrap: narrow ? 'wrap' : undefined,
   }}>
    <div>
     <div style={{ fontSize: 11, color: C.dim, marginBottom: 2 }}>{t('inventory.sim_original_rec')}</div>
     <div style={{ fontSize: 20, fontWeight: 800, color: C.muted }}>{fmtNum(originalRec)} {t('inventory.unit_und')}</div>
    </div>
    <div style={{ fontSize: 20, color: C.dim, display: narrow ? 'none' : undefined }}>→</div>
    <div>
     <div style={{ fontSize: 11, color: C.dim, marginBottom: 2 }}>{t('inventory.sim_with_changes')}</div>
     <div style={{ fontSize: 24, fontWeight: 900, color: delta > 0 ? '#C0504D' : delta < 0 ? '#2E8B62' : C.text }}>
      {fmtNum(simRecommended)} {t('inventory.unit_und')}
     </div>
    </div>
    {delta !== 0 && (
     <div style={{ fontSize: 13, color: deltaColor, fontWeight: 600 }}>
      {delta > 0 ? `+${fmtNum(delta)} ${t('inventory.sim_units_more')}` : `${fmtNum(Math.abs(delta))} ${t('inventory.sim_units_less')}`}
     </div>
    )}
    <button onClick={() => { setLtDelta(0); setDemandMult(100); setStockDelta(0) }}
     style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', fontSize: 11,
      color: C.dim, padding: '4px 10px', border: `1px solid ${C.border}`, borderRadius: 6,
      ...(narrow ? { minHeight: 44, boxSizing: 'border-box', display: 'inline-flex', alignItems: 'center', fontSize: 14, padding: '0 16px', borderRadius: 10 } : {}) }}>
     {t('inventory.btn_reset')}
    </button>
   </div>
  </div>
 )
}

// ── "Why is today's number different" (stability.md 19.7) ───────────────────
// The buyer's actual question is "was it my business or your opinion" — so
// this answers THAT first, in one sentence, before any field-level detail.
// Fetched lazily: it rides the same mount-on-demand row this component sits
// in, one call per SKU the buyer actually opens, never the whole page.
const WHY_CHANGED_FIELD_LABEL_KEY: Record<string, string> = {
 avg_daily_demand: 'inventory.calc_step_avg_daily_sales',
 safety_stock:     'inventory.calc_step_safety_stock',
 reorder_point:    'inventory.why_changed_field_reorder_point',
 lead_time_days:   'inventory.col_lead_time',
 current_stock:    'inventory.calc_step_current_stock',
 recommended_qty:  'inventory.why_changed_field_recommended_qty',
 signal:           'inventory.why_changed_field_signal',
}

function whyChangedOriginKey(origin: WhyChangedFieldOrigin): string {
 if (origin === 'session') return 'inventory.why_changed_origin_session'
 if (origin === 'operational') return 'inventory.why_changed_origin_operational'
 return 'inventory.why_changed_origin_derived'
}

function WhyChangedPanel({ sku }: { sku: string }) {
 const { t, lang } = useLanguage()
 const [data, setData] = useState<WhyChangedResponse | null>(null)
 const [loading, setLoading] = useState(true)

 useEffect(() => {
  let alive = true
  setLoading(true)
  getWhyChanged(sku)
   .then(r => { if (alive) setData(r) })
   .catch(() => { if (alive) setData(null) })
   .finally(() => { if (alive) setLoading(false) })
  return () => { alive = false }
 }, [sku])

 const boxS: React.CSSProperties = {
  background: 'var(--surface)', border: `1px solid ${C.border}`,
  borderRadius: 8, padding: '12px 16px', marginTop: 8,
 }
 const titleS: React.CSSProperties = {
  fontSize: 11, fontWeight: 700, color: C.muted, marginBottom: 8,
  textTransform: 'uppercase', letterSpacing: '0.06em',
 }

 if (loading) return null // No spinner: this is a secondary panel, not the row itself.
 if (!data) return null   // The fetch failed — degrade to nothing rather than an error box beside a working row.

 if (!data.available) {
  const reasonKey = data.reason === 'no_recorded_recommendations'
   ? 'inventory.why_changed_no_history' : 'inventory.why_changed_one_day'
  return (
   <div style={boxS}>
    <div style={titleS}>{t('inventory.why_changed_title')}</div>
    <div style={{ fontSize: 12, color: C.dim }}>{t(reasonKey)}</div>
   </div>
  )
 }

 const fields = data.fields || {}
 // Only the inputs that actually moved — a row with delta 0 (or, for
 // `signal`, an unchanged string) is not part of "why", it is noise next to it.
 const changed = Object.entries(fields).filter(([, f]) => {
  if (f.delta != null) return f.delta !== 0
  return f.previous !== f.current
 })

 return (
  <div style={boxS}>
   <div style={titleS}>{t('inventory.why_changed_title')}</div>
   <div style={{ fontSize: 13, fontWeight: 600, color: C.text, marginBottom: 4 }}>
    {t(data.session_changed ? 'inventory.why_changed_new_session' : 'inventory.why_changed_same_session')}
   </div>
   {data.previous_recorded_on && data.latest_recorded_on && (
    <div style={{ fontSize: 11, color: C.dim, marginBottom: 10 }}>
     {t('inventory.why_changed_dates', {
      previous: new Date(`${data.previous_recorded_on}T12:00:00`).toLocaleDateString(lang === 'en' ? 'en-US' : 'es-CR'),
      latest: new Date(`${data.latest_recorded_on}T12:00:00`).toLocaleDateString(lang === 'en' ? 'en-US' : 'es-CR'),
     })}
    </div>
   )}
   {changed.length === 0 ? (
    <div style={{ fontSize: 12, color: C.dim }}>{t('inventory.why_changed_no_change_fields')}</div>
   ) : (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
     {changed.map(([field, f]) => (
      <div key={field} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
       <span style={{
        fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em',
        padding: '2px 6px', borderRadius: 4, color: f.origin === 'operational' ? C.amber : f.origin === 'session' ? C.indigo : C.dim,
        background: f.origin === 'operational' ? 'color-mix(in srgb, var(--warning, #B7791F) 14%, transparent)'
         : f.origin === 'session' ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : 'transparent',
       }}>
        {t(whyChangedOriginKey(f.origin))}
       </span>
       <span style={{ color: C.muted, flex: 1 }}>{t(WHY_CHANGED_FIELD_LABEL_KEY[field] || field)}</span>
       <span style={{ fontFamily: 'monospace', color: C.dim }}>{String(f.previous ?? '—')}</span>
       <span style={{ color: C.dim }}>→</span>
       <span style={{ fontFamily: 'monospace', fontWeight: 700, color: C.text }}>{String(f.current ?? '—')}</span>
      </div>
     ))}
    </div>
   )}
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
       {/* "Was it my business or your opinion" (stability.md 19.7) — sits right
           under the calculation it explains the CHANGE to, not on its own
           screen: the buyer is already looking at this sku. */}
       <WhyChangedPanel sku={item.sku} />
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
 const { t, lang } = useLanguage()
 const narrow = useIsNarrow()
 const { addToast } = useToast()
 // A viewer was offered the whole write toolbar — stock editor, shrinkage,
 // "add warehouse" — could type a value, and only met the refusal at save
 // time. The backend always held (403, nothing written), so this is honesty,
 // not security: do not offer what the role cannot do. Same shape as
 // /escenarios and /historial.
 const canEdit = ((): boolean => {
 const role = getUser()?.role
 return role === 'admin' || role === 'analyst'
 })()
 // The semáforo's configured multipliers, so the legend prints the rule this
 // tenant actually runs on instead of a sentence that could drift from it.
 const [signalRules, setSignalRules] = useState<SignalThresholdsState | null>(null)
 useEffect(() => {
 getSignalThresholds({ silent: true }).then(setSignalRules).catch(() => setSignalRules(null))
 }, [])
 const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
 const { sessionId, setSessionId, currentSession, completedSessions, error: sessionsError, refresh: refreshSessions } = useAutoSession()
 // Translates an ApiError's `error_code` + `params` into the user's language.
 const errorDetail = useErrorDetail()
 const [data, setData] = useState<{ items: InventoryStatusItem[]; summary: Record<string, number>; excluded_skus?: ExcludedSku[]; coverage_unit?: CoverageUnit } | null>(null)
 const [loading, setLoading] = useState(false)
 // Raw error, so ErrorState can classify by kind instead of showing a
 // pre-flattened string.
 const [error, setError] = useState<unknown>(null)
 const [signalFilter, setSignalFilter] = useState<InventorySignal | ''>('')
 const [search, setSearch] = useState('')
 const [sort, setSort] = useState<SortState | null>(null)
 const [page, setPage] = useState(1)
 const [viewMode, setViewMode] = useState<'table' | 'simple' | 'provider' | 'update' | 'capital' | 'inflation' | 'erosion' | 'money' | 'ignored'>(() =>
 typeof window !== 'undefined' && localStorage.getItem('adv') === '1' ? 'table' : 'simple'
 )
 const [expandedSku, setExpandedSku] = useState<string | null>(null)
 // Phone only: the SKU whose detail sheet is open (the desktop expands a row).
 const [detailSku, setDetailSku] = useState<string | null>(null)
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
 // "Plata parada" (capital parado): the one view of money that is not
 // moving — needs no session, reads real stock-level history. See
 // getDeadCapital. The session-bound "dead stock" view it used to sit beside
 // was retired 2026-09-30 (stability.md 19.2).
 const [deadCapital, setDeadCapital] = useState<DeadCapitalResponse | null>(null)
 const [loadingDeadCapital, setLoadingDeadCapital] = useState(false)
 const [deadCapitalWindow, setDeadCapitalWindow] = useState(90)
 const [deadCapitalPage, setDeadCapitalPage] = useState(1)
 // Supplier cost inflation / margin erosion (stability.md #20, items 5-6):
 // neither needs a session, both read received-PO cost history. See
 // getSupplierCostInflation / getMarginErosion.
 const [costInflation, setCostInflation] = useState<SupplierCostInflationResponse | null>(null)
 const [loadingCostInflation, setLoadingCostInflation] = useState(false)
 const [costInflationWindow, setCostInflationWindow] = useState(365)
 const [costInflationPage, setCostInflationPage] = useState(1)
 const [marginErosion, setMarginErosion] = useState<MarginErosionResponse | null>(null)
 const [loadingMarginErosion, setLoadingMarginErosion] = useState(false)
 const [marginErosionWindow, setMarginErosionWindow] = useState(365)
 const [marginErosionMinPts, setMarginErosionMinPts] = useState(0.5)
 const [marginErosionPage, setMarginErosionPage] = useState(1)
 // The forecast in money (stability.md #20, item 1): needs the active
 // session's forecast, same as the main status view. See getForecastMoney.
 const [forecastMoney, setForecastMoney] = useState<ForecastMoneyResponse | null>(null)
 const [loadingForecastMoney, setLoadingForecastMoney] = useState(false)
 const [forecastMoneyPage, setForecastMoneyPage] = useState(1)
 // "What did it cost me to ignore you" (stability.md 19.4): needs no
 // session, reads the recommendation log itself. See getCostOfIgnoring.
 const [costOfIgnoring, setCostOfIgnoring] = useState<CostOfIgnoringResponse | null>(null)
 const [loadingCostOfIgnoring, setLoadingCostOfIgnoring] = useState(false)
 const todayIso = new Date().toISOString().slice(0, 10)
 const [ignoringFromDate, setIgnoringFromDate] = useState(
  new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10))
 const [ignoringToDate, setIgnoringToDate] = useState(todayIso)
 const [ignoringPoWindow, setIgnoringPoWindow] = useState(14)
 const [ignoringPage, setIgnoringPage] = useState(1)
 const [editedQty, setEditedQty] = useState<Record<string, number>>({})
 const [editingQtySku, setEditingQtySku] = useState<string | null>(null)
 const [showShrinkageModal, setShowShrinkageModal] = useState(false)
 // Multi-warehouse (feature 5.4): selector + per-warehouse view. null = all.
 const { warehouses, multi: multiWarehouse } = useWarehouses()
 const [selectedWarehouse, setSelectedWarehouse] = useState<string | null>(null)

 // "Todas" with several warehouses shows the NETWORK TOTAL per SKU, but a row
 // save posts no warehouse and lands on one of them. So a buyer who read 615
 // (575 + 40), typed 600 and pressed Enter left the network holding 640: the
 // figure they typed became principal's stock and the other warehouse was
 // added on top. Editing an aggregate has no correct destination — the fields
 // are read-only here and the hint says which tab to use instead.
 const isNetworkStockView = multiWarehouse && !selectedWarehouse

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
 try { setData(withSingleSeriesLabel(await getInventoryStatus(sid, 0.95, { silent: true }), t)) }
 catch (e: unknown) { setError(e) }
 finally { setLoading(false) }
 }, [t])

 useEffect(() => { if (sessionId) load(sessionId) }, [sessionId, load])

 // ── Dead capital ("plata parada") load ─────────────────────────────────────
 // No session dependency: it works off real stock-level history alone, so a
 // tenant with no completed session yet can still see it.
 useEffect(() => {
 if (viewMode !== 'capital') return
 setLoadingDeadCapital(true)
 getDeadCapital(deadCapitalWindow)
  .then(setDeadCapital)
  .catch((e: unknown) => setError(e))
  .finally(() => setLoadingDeadCapital(false))
 }, [viewMode, deadCapitalWindow])

 // ── Supplier cost inflation load ────────────────────────────────────────────
 useEffect(() => {
 if (viewMode !== 'inflation') return
 setLoadingCostInflation(true)
 getSupplierCostInflation(costInflationWindow)
  .then(setCostInflation)
  .catch((e: unknown) => setError(e))
  .finally(() => setLoadingCostInflation(false))
 }, [viewMode, costInflationWindow])

 // ── Margin erosion load ─────────────────────────────────────────────────────
 useEffect(() => {
 if (viewMode !== 'erosion') return
 setLoadingMarginErosion(true)
 getMarginErosion(marginErosionWindow, marginErosionMinPts)
  .then(setMarginErosion)
  .catch((e: unknown) => setError(e))
  .finally(() => setLoadingMarginErosion(false))
 }, [viewMode, marginErosionWindow, marginErosionMinPts])

 // ── The forecast in money load ──────────────────────────────────────────────
 useEffect(() => {
 if (viewMode !== 'money' || !sessionId) return
 setLoadingForecastMoney(true)
 getForecastMoney(sessionId)
  .then(setForecastMoney)
  .catch((e: unknown) => setError(e))
  .finally(() => setLoadingForecastMoney(false))
 }, [viewMode, sessionId])

 // ── Cost of ignoring load ───────────────────────────────────────────────────
 useEffect(() => {
 if (viewMode !== 'ignored') return
 setLoadingCostOfIgnoring(true)
 getCostOfIgnoring(ignoringFromDate, ignoringToDate, ignoringPoWindow)
  .then(setCostOfIgnoring)
  .catch((e: unknown) => setError(e))
  .finally(() => setLoadingCostOfIgnoring(false))
 }, [viewMode, ignoringFromDate, ignoringToDate, ignoringPoWindow])

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
 // A blank box means "leave it as it is", not "use 15 days". Sending the
 // default wrote it over whatever the user had configured, and the app then
 // reported it back as their own choice. And an UNCHANGED box is the same:
 // it is pre-filled with the RESOLVED lead time (a supplier rule, a learned
 // value, the assumed 15), and sending it back stamped it as typed by the
 // user — counting stock pinned the lead time (math audit 2026-10-01).
 lead_time_days: changedLeadTime(draft, rowBaseline[sku]),
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
 // Tracked apart from `failed` because the cause changes what we can honestly
 // tell the user: a rejected value is worth reviewing, a rejected ROLE is not.
 let denied = false
 for (const [sku, draft] of toSave) {
 try {
 await upsertInventoryStock(sku, {
 current_stock: parseFloat(draft.current_stock) || 0,
 // Only a lead time the user actually changed — see saveRow.
 lead_time_days: changedLeadTime(draft, rowBaseline[sku]),
 supplier: draft.supplier || undefined,
 })
 saved++
 } catch (e) {
 console.error(`Error saving ${sku}:`, e)
 failed.push(sku)
 if (e instanceof ApiError && e.kind === 'permission') denied = true
 }
 }
 setUpdateSaving(false)
 if (failed.length === 0) {
 addToast(t('inventory.toast_saved_title'), `${saved} SKUs`, 'success')
 setUpdateDraft({})
 setUpdatedSkus(new Set())
 setViewMode('table')
 } else if (denied) {
 // "Review and try again" is a lie when the role was the refusal: the values
 // are fine and every retry fails identically. Say what happened and what
 // would actually resolve it, instead of sending the user in a circle.
 addToast(t('inventory.toast_save_denied_title'),
 t('inventory.toast_save_denied_body'), 'error')
 setUpdatedSkus(new Set(failed))
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
 const deadCapitalItems = useMemo(() => deadCapital?.items ?? [], [deadCapital])
 const deadCapitalPaged = usePage(deadCapitalItems, deadCapitalPage, setDeadCapitalPage)
 const forecastMoneyItems = useMemo(() => forecastMoney?.items ?? [], [forecastMoney])
 const forecastMoneyPaged = usePage(forecastMoneyItems, forecastMoneyPage, setForecastMoneyPage)
 const costInflationSuppliers = useMemo(() => costInflation?.suppliers ?? [], [costInflation])
 const costInflationPaged = usePage(costInflationSuppliers, costInflationPage, setCostInflationPage)
 const marginErosionItems = useMemo(() => marginErosion?.items ?? [], [marginErosion])
 const marginErosionPaged = usePage(marginErosionItems, marginErosionPage, setMarginErosionPage)
 const costOfIgnoringSkus = useMemo(() => costOfIgnoring?.skus ?? [], [costOfIgnoring])
 const costOfIgnoringPaged = usePage(costOfIgnoringSkus, ignoringPage, setIgnoringPage)
 // The report itself only carries the sku code (it is a read over the
 // recommendation log, which does not persist a display name) — the main
 // status list already loaded for this tenant does, so this joins the two
 // client-side rather than asking the backend to duplicate that lookup.
 const skuDisplayName = useMemo(() => {
 const m: Record<string, string> = {}
 for (const i of data?.items ?? []) if (i.display_name) m[i.sku] = i.display_name
 return m
 }, [data])

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
 const d = Math.round((dayOf(e.start_date).getTime() - Date.now()) / 86400000)
 return d >= 0 && d <= 30 && dayOf(e.end_date) >= new Date()
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
 // The form is pre-filled with RESOLVED values — a supplier rule's lead time
 // or minimum, a learned lead time, the assumed defaults. Sending them back
 // unchanged stamped each one as typed by the user, so fixing a product's
 // name pinned its lead time and MOQ against every later rule change
 // (stability 1.9's defect through the edit form; math audit 2026-10-01).
 const shown = data?.items.find(i => i.sku === sku)
 const before = shown ? rowToEdit(shown) : null
 const leadChanged = !before || editState.lead_time_days !== before.lead_time_days
 const moqChanged = !before || editState.moq !== before.moq
 await upsertInventoryStock(sku, { display_name: editState.display_name || undefined, current_stock: parseFloat(editState.current_stock) || 0, lead_time_days: leadChanged ? leadTimeOrUnset(editState.lead_time_days) : undefined, unit_cost: editState.unit_cost ? parseFloat(editState.unit_cost) : undefined, moq: moqChanged ? (parseFloat(editState.moq) || DEFAULT_MOQ) : undefined, supplier: editState.supplier || undefined, service_level: parseFloat(editState.service_level) || DEFAULT_SERVICE_LEVEL, sale_price: editState.sale_price ? parseFloat(editState.sale_price) : undefined, category: editState.category || undefined, family: editState.family || undefined, brand: editState.brand || undefined, unit_of_measure: editState.unit_of_measure || undefined, barcode: editState.barcode || undefined })
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
 // Whichever warehouse tab is open is the destination for rows that name
 // none. In "Todas" nothing is passed and the backend resolves the default,
 // exactly as before.
 const res = await importInventoryCSV(file, selectedWarehouse ?? undefined)
 await load(sessionId)
 addToast(t('inventory.toast_import_title'), `${t('inventory.alert_imported_prefix')} ${res.imported} ${t('inventory.alert_imported_of')} ${res.total_rows} SKUs`, 'success')
 }
 catch (err: unknown) { setError(err instanceof Error ? err.message : t('inventory.err_importing')) }
 finally { setImporting(false) }
 }

 // The file follows the tab that is open.
 //
 // `GET /inventory/status/export-po` had no warehouse parameter at all and
 // re-derived the list at network level, while the download menu sits in the
 // page header ABOVE the warehouse selector and stayed enabled with a tab
 // open. The buyer read "Norte needs 40", downloaded a file saying 150, and
 // `logPOGeneration` wrote that into /pedidos as an order they never saw
 // (stability 11.7 — §3.1 again, on the warehouse axis).
 async function handleExport() {
 if (!sessionId) return; setExporting(true)
 try {
  const { logged } = await exportInventoryPO(sessionId, 0.95, selectedWarehouse ?? undefined)
  if (!logged) warnPONotLogged()
 }
 catch (e: unknown) { setError(e instanceof Error ? e.message : t('inventory.err_exporting')) }
 finally { setExporting(false) }
 }

 // The download succeeded and the order did NOT get recorded — two different
 // facts, and the buyer needs both. Without this they walk away believing the
 // order is in Pedidos, where it will never arrive and never be received.
 function warnPONotLogged() {
  addToast(t('inventory.toast_po_not_logged_title'),
      t('inventory.toast_po_not_logged_body'), 'error')
 }

 // Exports a PO CSV built from the buyer's edited quantities (instead of the
 // server re-deriving them) and logs the decisions via logPOGeneration so
 // edited amounts are reflected in adoption tracking.
 function exportEditedPO() {
 if (!sessionId || !data) return
 // While a warehouse tab is open, `data.items` is still the NETWORK list and
 // the edits the buyer made live in the per-warehouse table, so this export
 // would emit quantities from a view nobody is looking at. The server-side
 // export knows how to scope itself now, so that is what runs instead.
 if (selectedWarehouse) { void handleExport(); return }
 // Every actionable line StockAI put in front of the buyer, whatever they did
 // with it. Splitting here rather than filtering once is what makes
 // 'rejected' reachable at all: a line the buyer zeroed out used to be
 // dropped before the decisions were built, so this export could only ever
 // emit 'approved' or 'modified'. Adoption was structurally incapable of
 // recording a refusal, and a tenant working from this screen saw a green
 // 100% forever — with every urgent SKU also counted as a risk acted on.
 const actionable = data.items
  .filter(i => i.signal === 'PEDIR_YA' || i.signal === 'PEDIR_PRONTO')
 const orderItems = actionable.filter(i => effectiveQty(i) > 0)
 const declined = actionable.filter(i => effectiveQty(i) <= 0)
 if (orderItems.length === 0) return
 // Same artifact as the export on /hoy — same filename, same columns — so it
 // must use the same header keys. This one hardcoded Spanish, so an English
 // user got Spanish headers here and English ones there, from one product.
 // Same writer as /compras and as the backend endpoint — see lib/csvWriter for
 // what raw interpolation was costing this file.
 const header = ['SKU', t('hoy.csv_col_product'), t('hoy.csv_col_quantity'),
                 t('hoy.csv_col_supplier'), t('hoy.csv_col_estimated_value')]
 const rows = orderItems.map(i => {
  const qty = effectiveQty(i)
  return [
   csvCell(i.sku),
   csvCell(i.display_name ?? ''),
   csvNumber(qty),
   csvCell(i.supplier ?? ''),
   csvNumber(i.unit_cost == null ? null : qty * i.unit_cost),
  ]
 })
 downloadCsv('purchase_order.csv', buildCsv(header, rows))
 // Log decisions: ordered (edited => 'modified', otherwise 'approved') AND
 // the actionable lines the buyer zeroed out, which are refusals and have to
 // be recorded as such — they are the only thing that can move adoption off
 // 100% from this screen.
 const decisions: POLineDecision[] = [
  ...orderItems.map(i => ({
   sku: i.sku,
   display_name: i.display_name ?? undefined,
   supplier: i.supplier ?? undefined,
   signal: i.signal,
   recommended_qty: i.recommended_qty ?? 0,
   final_qty: effectiveQty(i),
   status: (editedQty[i.sku] != null ? 'modified' : 'approved') as 'approved' | 'modified',
   unit_cost: i.unit_cost ?? undefined,
  })),
  ...declined.map(i => ({
   sku: i.sku,
   display_name: i.display_name ?? undefined,
   supplier: i.supplier ?? undefined,
   signal: i.signal,
   recommended_qty: i.recommended_qty ?? 0,
   final_qty: 0,
   status: 'rejected' as const,
   unit_cost: i.unit_cost ?? undefined,
  })),
 ]
 logPOGeneration(sessionId, decisions, undefined, { silent: true })
  .catch(() => warnPONotLogged())
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
 {/* On a phone the compact header already names the screen. */}
 <div style={{ display: narrow ? 'none' : 'flex', alignItems: 'center', gap: 10 }}>
 <div style={{ width: 36, height: 36, borderRadius: 9, background: '#2E8B62', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
 <ShoppingCart size={17} color="#fff" strokeWidth={2.5} />
 </div>
 <div>
 <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>{t('inventory.title')}</h1>
 <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('inventory.subtitle')}</p>
 </div>
 </div>

 <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', ...(narrow ? { width: '100%', minWidth: 0 } : {}) }}>
 {/* Session freshness */}
 <DataFreshness currentSession={currentSession} />

 {/* View toggle. On a phone the nine views measured 719px inside a
     clipping 336px box, so five of them could not be reached at all: there
     it is a strip that scrolls with a finger and keeps the active view in
     sight (components/mobile/MobileTabs). Desktop is unchanged. */}
 {(() => {
 const views = ([
 ['table', <List size={13} />, t('inventory.view_table')],
 ['simple', <Package size={13} />, t('inventory.view_simple')],
 ['provider', <Layers size={13} />, t('inventory.view_provider')],
 // "Actualizar stock" is the editor, not a view: a viewer who opens it can
 // type and reach an enabled Save that can only ever be refused.
 ...(canEdit ? [['update', <PencilLine size={13} />, t('inventory.view_update')]] : []),
 ['capital', <TrendingDown size={13} />, t('inventory.view_dead_capital')],
 ['inflation', <TrendingUp size={13} />, t('inventory.view_cost_inflation')],
 ['erosion', <TrendingDown size={13} />, t('inventory.view_margin_erosion')],
 ['money', <DollarSign size={13} />, t('inventory.view_forecast_money')],
 ['ignored', <AlertTriangle size={13} />, t('inventory.view_cost_of_ignoring')],
 ] as [string, React.ReactNode, string][])
 type ViewMode = typeof viewMode
 if (narrow) return (
 <div data-tour="inv.views" style={{ width: '100%', minWidth: 0 }}>
 <MobileTabs
  ariaLabel={t('inventory.views_aria')}
  value={viewMode}
  onChange={mode => setViewMode(mode as ViewMode)}
  tabs={views.map(([mode, icon, label]) => ({ id: mode as ViewMode, label, icon }))}
 />
 </div>
 )
 return (
 <div data-tour="inv.views" style={{ display: 'flex', border: `1px solid ${C.border}`, borderRadius: 8, overflow: 'hidden' }}>
 {views.map(([mode, icon, label]) => (
 <button key={mode} onClick={() => setViewMode(mode as ViewMode)} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, padding: '6px 11px', fontSize: 11, fontWeight: 500, background: viewMode === mode ? 'var(--accent-dim)' : 'transparent', color: viewMode === mode ? 'var(--accent)' : C.dim }}>
 {icon}{label}
 </button>
 ))}
 </div>
 )
 })()}

 {/* Getting data in and out used to be eleven loose controls in this bar:
     five of them exports, two of them links to screens already sitting in the
     sidebar two inches to the left. Counted on the running app it was 26
     controls and 7 colours before the buyer reached the first row, which is
     why the owner could not read his own screen. They collapse into two
     menus. Nothing was removed except the duplicated navigation — every
     action still exists, one click deeper. */}
 <input ref={importRef} type="file" name="inventory_csv_import" aria-label={t('inventory.btn_import_csv_arrow')} accept=".csv" style={{ display: 'none' }} onChange={handleImport} />

 <div data-tour="inv.export">
  <MenuButton
   label={t('inventory.menu_download')}
   icon={<Download size={12} />}
   items={[
    { label: t('inventory.btn_template'),      icon: <Download size={12} />,  onSelect: () => downloadInventoryTemplate().catch(err => setError(err instanceof Error ? err.message : String(err))) },
    { label: t('inventory.btn_export_po'),     icon: <Download size={12} />,  onSelect: handleExport,   disabled: exporting || !sessionId },
    { label: t('inventory.btn_export_edited'), icon: <Download size={12} />,  onSelect: exportEditedPO, disabled: !sessionId },
    { label: t('inventory.menu_pdf'),          icon: <FileText size={12} />,  onSelect: handlePDF,      disabled: pdfLoading || !sessionId },
   ]}
  />
 </div>

 <MenuButton
  icon={<MoreHorizontal size={14} />}
  title={t('inventory.menu_more')}
  items={[
   { label: t('inventory.btn_refresh'), icon: <RefreshCw size={12} />, onSelect: () => sessionId && load(sessionId), disabled: loading },
   ...(canEdit ? [
    { label: t('inventory.menu_import_csv'),        icon: <Upload size={12} />,       onSelect: () => importRef.current?.click(), disabled: importing },
    { label: t('inventory.shrinkage_btn_register'), icon: <PackageMinus size={12} />, onSelect: () => setShowShrinkageModal(true), danger: true },
   ] : []),
  ]}
 />

 {/* Configurar inventario is no longer a sidebar entry; it lives under
     this screen (the sidebar keeps Inventario lit while it is open). */}
 <Link
  href="/configurar-inventario"
  style={{
   display: 'flex', alignItems: 'center', gap: 6, textDecoration: 'none',
   padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600,
   border: `1px solid ${C.border}`, color: 'var(--muted)',
   ...(narrow ? { minHeight: 44, boxSizing: 'border-box', fontSize: 14, borderRadius: 10 } : {}),
  }}
 >
  <Sliders size={narrow ? 14 : 12} aria-hidden="true" />
  {t('nav.inventory_setup')}
 </Link>
 </div>
 </div>

 {/* Secondary failure over an already-rendered screen (e.g. the money-not-moving view). */}
 {error != null && data != null && (
 <InlineError error={error} onRetry={() => sessionId && load(sessionId)} onDismiss={() => setError(null)} />
 )}

 {/* Excluded SKUs notice — products uploaded but left out of the forecast */}
 {(data?.excluded_skus?.length ?? 0) > 0 && (
 <div style={{ padding: '12px 16px', borderRadius: 8, background: 'rgba(183,121,31,0.07)', border: '1px solid rgba(183,121,31,0.25)', display: 'flex', alignItems: 'flex-start', gap: 10 }}>
 <Info size={14} color={C.amber} style={{ flexShrink: 0, marginTop: 1 }} />
 <div style={{ flex: 1, fontSize: 12.5, color: C.text }}>
 <span style={{ fontWeight: 700, color: C.amber }}>
 {data!.excluded_skus!.length} {data!.excluded_skus!.length !== 1 ? t('inventory.excluded_skus_suffix_plural') : t('inventory.excluded_skus_suffix_singular')}
 </span>
 <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 3 }}>
 {data!.excluded_skus!.map(e => (
 <div key={e.sku} style={{ color: C.muted }}>
 <span style={{ fontFamily: 'monospace', fontWeight: 600, color: C.text }}>{e.sku}</span>
 {' — '}{excludedReasonText(e, t)}
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
 <div style={{ padding: '10px 16px', borderRadius: 8, background: 'rgba(183,121,31,0.07)', border: '1px solid rgba(183,121,31,0.25)', display: 'flex', alignItems: 'flex-start', gap: 10 }}>
 <Calendar size={14} color={C.amber} style={{ flexShrink: 0, marginTop: 1 }} />
 <div style={{ flex: 1, fontSize: 12 }}>
 <span style={{ fontWeight: 600, color: C.amber }}>{t('inventory.upcoming_events_prefix')}</span>
 {upcomingAlerts.map(ev => {
 const d = Math.round((dayOf(ev.start_date).getTime() - Date.now()) / 86400000)
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
 {loading && !summary && <SkeletonCards count={narrow ? 4 : 6} height={74} />}
 {summary && narrow && (
 <div data-tour="inv.filters" className="page-enter">
 <MobileMetricGrid ariaLabel={t('inventory.m_filters_aria')} metrics={[
  { label: t('inventory.kpi_total_skus'), value: summary.total_skus, color: C.indigo, onClick: () => setSignalFilter(''), active: !signalFilter },
  { label: t('inventory.signal_order_now'), value: summary.order_now, color: C.red, onClick: () => setSignalFilter(signalFilter === 'PEDIR_YA' ? '' : 'PEDIR_YA'), active: signalFilter === 'PEDIR_YA', sub: summary.order_now > 0 ? t('inventory.kpi_sub_order_now') : undefined },
  { label: t('inventory.signal_order_soon'), value: summary.order_soon, color: C.amber, onClick: () => setSignalFilter(signalFilter === 'PEDIR_PRONTO' ? '' : 'PEDIR_PRONTO'), active: signalFilter === 'PEDIR_PRONTO', sub: summary.order_soon > 0 ? t('inventory.kpi_sub_order_soon') : undefined },
  { label: t('inventory.signal_ok'), value: summary.ok, color: C.green, onClick: () => setSignalFilter(signalFilter === 'OK' ? '' : 'OK'), active: signalFilter === 'OK' },
  { label: t('inventory.signal_overstock'), value: summary.overstock, color: C.blue, onClick: () => setSignalFilter(signalFilter === 'SOBRESTOCK' ? '' : 'SOBRESTOCK'), active: signalFilter === 'SOBRESTOCK', sub: summary.overstock > 0 ? t('inventory.kpi_sub_overstock') : undefined },
  // Compact money: the full figure of a real inventory does not fit half a phone.
  { label: t('inventory.kpi_inventory_value'), value: summary.total_inventory_value > 0 ? formatMoneyCompact(summary.total_inventory_value) : '—', color: C.indigo, sub: t('inventory.kpi_skus_with_cost') },
 ]} />
 </div>
 )}
 {summary && !narrow && (
 // Fades in over the skeleton cards it replaces: same shape, so the
 // transition reads as the placeholders resolving into numbers.
 <div data-tour="inv.filters" className="page-enter" style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12 }}>
 <KPICard label={t('inventory.kpi_total_skus')} value={summary.total_skus} color={C.indigo} onClick={() => setSignalFilter('')} active={!signalFilter} />
 <KPICard label={t('inventory.signal_order_now')} value={summary.order_now} color={C.red} onClick={() => setSignalFilter(signalFilter === 'PEDIR_YA' ? '' : 'PEDIR_YA')} active={signalFilter === 'PEDIR_YA'} sub={summary.order_now > 0 ? t('inventory.kpi_sub_order_now') : undefined} />
 <KPICard label={t('inventory.signal_order_soon')} value={summary.order_soon} color={C.amber} onClick={() => setSignalFilter(signalFilter === 'PEDIR_PRONTO' ? '' : 'PEDIR_PRONTO')} active={signalFilter === 'PEDIR_PRONTO'} sub={summary.order_soon > 0 ? t('inventory.kpi_sub_order_soon') : undefined} />
 <KPICard label={t('inventory.signal_ok')} value={summary.ok} color={C.green} onClick={() => setSignalFilter(signalFilter === 'OK' ? '' : 'OK')} active={signalFilter === 'OK'} />
 <KPICard label={t('inventory.signal_overstock')} value={summary.overstock} color={C.blue} onClick={() => setSignalFilter(signalFilter === 'SOBRESTOCK' ? '' : 'SOBRESTOCK')} active={signalFilter === 'SOBRESTOCK'} sub={summary.overstock > 0 ? t('inventory.kpi_sub_overstock') : undefined} />
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
 <>
 {/* How stock gets INTO this warehouse. A newly created location showed
     "Sin datos en esta bodega" and offered nothing: the only route was a
     `warehouse` column in a CSV that the UI never mentioned. The import
     button lives here too now, and rows that name no warehouse land in
     the tab that is open. */}
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
               padding: '8px 12px', borderRadius: 8, background: C.surface,
               border: `1px solid ${C.border}`, marginBottom: 10 }}>
 <span style={{ fontSize: 11.5, color: C.dim, flex: 1 }}>
 {t('inventory.wh_import_hint', { warehouse: selectedWarehouse })}
 </span>
 <button onClick={() => importRef.current?.click()} disabled={importing}
         style={{ all: 'unset', cursor: importing ? 'default' : 'pointer',
                  fontSize: 12, fontWeight: 600, color: C.indigo,
                  ...(narrow ? { minHeight: 44, display: 'inline-flex', alignItems: 'center', fontSize: 14 } : {}) }}>
 {importing ? t('common.saving') : t('inventory.btn_import_csv_arrow')}
 </button>
 </div>
 <WarehouseStatusTable
 sessionId={sessionId}
 warehouse={selectedWarehouse}
 onTransferCreated={() => load(sessionId)}
 />
 </>
 ) : (
 <>
 {/* Main table / view. On a phone the cards bring their own surface, so the
     framing box and the toolbar band are dropped. */}
 <div style={narrow
  ? { minWidth: 0 }
  : { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>

 {/* Toolbar */}
 <div style={narrow
  ? { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, minWidth: 0 }
  : { padding: '12px 16px', borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', gap: 10, background: C.card }}>
 <input data-tour="inv.search" type="search" name="inventory_search" aria-label={t('inventory.search_placeholder')} value={search} onChange={e => setSearch(e.target.value)} placeholder={t('inventory.search_placeholder')} style={{ flex: 1, minWidth: 0, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 7, padding: '6px 12px', fontSize: 12, color: C.text, outline: 'none', ...(narrow ? { fontSize: 16, minHeight: 44, borderRadius: 10, boxSizing: 'border-box' } : {}) }} />
 {search && <button onClick={() => setSearch('')} aria-label={t('inventory.search_clear')} title={t('inventory.search_clear')} style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', ...(narrow ? { minWidth: 44, minHeight: 44, alignItems: 'center', justifyContent: 'center' } : {}) }}><X size={narrow ? 18 : 13} aria-hidden="true" /></button>}
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
 <div style={{ fontSize: 14, color: C.text, marginBottom: 16, maxWidth: 420, margin: '0 auto 16px' }}>{errorDetail(sessionsError)}</div>
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
 ) : viewMode === 'provider' && narrow ? (
 <>
 <MobileProviderGroups groups={byProvider} render={provItems => (
  <MobileStockCards items={provItems} coverageUnit={data?.coverage_unit} mode="simple"
   effectiveQty={effectiveQty} editedQty={editedQty} notYet={i => notYetLabel(i, t)}
   onOpen={i => setDetailSku(i.sku)} ariaLabel={t('inventory.view_provider')} />
 )} />
 <Pagination page={paged.page} pageCount={paged.pageCount} offset={paged.offset} total={paged.total} rowsOnPage={pageItems.length} onPage={setPage} label="SKU" />
 </>
 ) : viewMode === 'provider' ? (
 <>
 <div style={{ padding: 16 }}>{byProvider.map(([provider, provItems]) => <ProviderGroup key={provider || '__none__'} name={provider} items={provItems} onEdit={startEdit} editedQty={editedQty} editingQtySku={editingQtySku} setEditedQty={setEditedQty} setEditingQtySku={setEditingQtySku} effectiveQty={effectiveQty} coverageUnit={data?.coverage_unit} />)}</div>
 <Pagination page={paged.page} pageCount={paged.pageCount} offset={paged.offset} total={paged.total} rowsOnPage={pageItems.length} onPage={setPage} label="SKU" />
 </>

 ) : viewMode === 'update' && narrow ? (
 /* ── Quick update view, phone: one card per product, the save pinned
    above the tab bar. Same draft, same per-row Enter, same save-all. */
 <div>
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
  <span style={{ fontSize: 13, color: C.dim, flex: 1, minWidth: 180, lineHeight: 1.45 }}>
   {isNetworkStockView
    ? tOr(t, 'inventory.bulk_network_readonly', 'These figures add up every warehouse. Pick one above to edit its stock.')
    : t('inventory.m_update_hint')}
  </span>
  <button onClick={() => importRef.current?.click()} disabled={importing} className="mobile-btn mobile-btn-secondary" style={{ flex: '0 0 auto', fontSize: 14 }}>
   <Upload size={15} aria-hidden="true" /> {t('inventory.btn_import_csv_arrow')}
  </button>
 </div>
 <div aria-live="polite" className="sr-only">
 {rowStatus && (
  rowStatus.kind === 'saved'
   ? tOr(t, 'inventory.bulk_row_saved', `Row ${rowStatus.sku} saved`, { sku: rowStatus.sku })
   : rowStatus.kind === 'discarded'
    ? tOr(t, 'inventory.bulk_row_discarded', `Changes to row ${rowStatus.sku} discarded`, { sku: rowStatus.sku })
    : tOr(t, 'inventory.bulk_row_error', `Row ${rowStatus.sku} could not be saved`, { sku: rowStatus.sku })
 )}
 </div>
 <MobileStockEntry
  items={pageItems}
  draft={updateDraft}
  modified={updatedSkus}
  readOnly={isNetworkStockView}
  savingRow={savingRow}
  onChange={(sku, field, value) => handleDraftChange(sku, field, value)}
  onKeyDown={handleRowKeyDown}
 />
 <Pagination page={paged.page} pageCount={paged.pageCount} offset={paged.offset} total={paged.total} rowsOnPage={pageItems.length} onPage={setPage} label="SKU" />
 <StickyActionBar hidden={updatedSkus.size === 0}>
  <button type="button" className="mobile-btn mobile-btn-secondary" style={{ flex: '0 0 auto' }}
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
   }}>
   {t('inventory.btn_discard')}
  </button>
  <button type="button" data-tour="inv.save" className="mobile-btn mobile-btn-primary" onClick={handleSaveAll} disabled={updateSaving}>
   {updateSaving ? <Spinner size={14} /> : <Save size={16} aria-hidden="true" />}
   {updateSaving ? t('inventory.saving_ellipsis') : `${t('inventory.btn_save_prefix')} ${updatedSkus.size} ${updatedSkus.size !== 1 ? t('inventory.changes_plural') : t('inventory.changes_singular')}`}
  </button>
 </StickyActionBar>
 </div>

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
 style={{ all: 'unset', cursor: updatedSkus.size === 0 ? 'default' : 'pointer', padding: '6px 14px', borderRadius: 7, border: `1px solid ${C.border}`, fontSize: 12, color: C.dim, opacity: updatedSkus.size === 0 ? 0.4 : 1, ...(narrow ? { minHeight: 44, boxSizing: 'border-box', display: 'flex', alignItems: 'center' } : {}) }}
 >
 {t('inventory.btn_discard')}
 </button>
 <button
 data-tour="inv.save"
 onClick={handleSaveAll}
 disabled={updatedSkus.size === 0 || updateSaving}
 style={{ all: 'unset', cursor: updatedSkus.size === 0 || updateSaving ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '6px 16px', borderRadius: 7, fontSize: 12, fontWeight: 600, background: updatedSkus.size > 0 ? C.green : 'rgba(46,139,98,0.2)', color: '#fff', opacity: updatedSkus.size === 0 || updateSaving ? 0.5 : 1, ...(narrow ? { minHeight: 44, boxSizing: 'border-box', fontSize: 14 } : {}) }}
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
 {isNetworkStockView
  ? tOr(t, 'inventory.bulk_network_readonly',
   'These figures add up every warehouse. Pick one above to edit its stock.')
  : tOr(t, 'inventory.bulk_keyboard_hint',
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
 // A thumb on a warehouse floor, not a mouse: 44px tall and 16px text on a
 // phone (16px also stops iOS zooming the page on focus). Desktop unchanged.
 ...(narrow ? { minHeight: 44, fontSize: 16, padding: '8px 10px' } : {}),
 }
 // Every cell in the row names its SKU: 100 identically-labelled
 // "Stock actual" fields tell a screen-reader user nothing about
 // which product they are typing into.
 const fieldLabel = (col: string) => `${col} — ${item.display_name || item.sku}`
 return (
 <tr
 key={item.sku}
 style={{ background: isModified ? 'rgba(183,121,31,0.04)' : rowBg }}
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
 disabled={isNetworkStockView}
 value={draft?.current_stock ?? ''}
 onChange={e => handleDraftChange(item.sku, 'current_stock', e.target.value)}
 onFocus={e => { e.target.style.borderColor = 'var(--accent)' }}
 onBlur={e => { e.target.style.borderColor = C.border }}
 onKeyDown={e => handleRowKeyDown(e, item.sku, 'current_stock')}
 data-bulk-field="current_stock"
 data-stock-input=""
 /* First row only: a tour anchor has to be unique to be findable. */
 data-tour={idx === 0 ? 'inv.update_stock' : undefined}
 />
 </td>
 <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, minWidth: 130 }}>
 <input
 style={inputUpd}
 name={`bulk-lead-time-${item.sku}`} aria-label={fieldLabel(t('inventory.col_lead_time_days'))}
 aria-describedby="bulk-edit-keys" aria-keyshortcuts="Enter Escape"
 type="number" min={1} max={365}
 disabled={isNetworkStockView}
 value={draft?.lead_time_days ?? ''}
 onChange={e => handleDraftChange(item.sku, 'lead_time_days', e.target.value)}
 onFocus={e => { e.target.style.borderColor = 'var(--accent)' }}
 onBlur={e => { e.target.style.borderColor = C.border }}
 onKeyDown={e => handleRowKeyDown(e, item.sku, 'lead_time_days')}
 data-bulk-field="lead_time_days"
 data-tour={idx === 0 ? 'inv.update_lead' : undefined}
 />
 </td>
 <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, minWidth: 150 }}>
 <input
 style={inputUpd}
 name={`bulk-supplier-${item.sku}`} aria-label={fieldLabel(t('inventory.col_provider'))}
 aria-describedby="bulk-edit-keys" aria-keyshortcuts="Enter Escape"
 type="text"
 disabled={isNetworkStockView}
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

 ) : (viewMode === 'simple' || viewMode === 'table') && narrow ? (
 /* ── Simple / full table, phone: one card per SKU, the rest of the
    columns and the calculation in a sheet. The full view keeps its sort. */
 <div className="page-enter">
 {viewMode === 'table' && (
  <div style={{ marginBottom: 10 }}>
   <MField label={t('inventory.m_sort_label')}>
    <select style={mInput} name="inventory_mobile_sort" value={sort ? `${sort.key}:${sort.dir}` : ''}
     onChange={e => {
      const v = e.target.value
      if (!v) { setSort(null); return }
      const [key, dir] = v.split(':') as [SortKey, SortDir]
      setSort({ key, dir })
     }}>
     <option value="">{t('inventory.m_sort_default')}</option>
     {([
      ['signal', t('inventory.col_signal')], ['sku', t('inventory.col_sku_name')], ['stock', t('inventory.col_stock')],
      ['coverage', t('inventory.wh_col_coverage')], ['qty', t('inventory.col_qty_to_order')],
      ['lead_time', t('inventory.col_lead_time')], ['value', t('inventory.col_warehouse_value')],
     ] as [SortKey, string][]).flatMap(([k, label]) => [
      <option key={`${k}:asc`} value={`${k}:asc`}>{label} ↑</option>,
      <option key={`${k}:desc`} value={`${k}:desc`}>{label} ↓</option>,
     ])}
    </select>
   </MField>
  </div>
 )}
 <MobileStockCards items={pageItems} coverageUnit={data?.coverage_unit} mode={viewMode === 'table' ? 'table' : 'simple'}
  effectiveQty={effectiveQty} editedQty={editedQty} notYet={i => notYetLabel(i, t)}
  onOpen={i => setDetailSku(i.sku)} ariaLabel={t('inventory.title')} />
 <Pagination page={paged.page} pageCount={paged.pageCount} offset={paged.offset} total={paged.total} rowsOnPage={pageItems.length} onPage={setPage} label="SKU" />
 </div>

 ) : viewMode === 'simple' ? (
 /* ── Simple view ──────────────────────────────────────────── */
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
 : <span style={{ fontSize: 12, color: C.dim }}>{item.recommended_qty === 0 ? notYetLabel(item, t) : '—'}</span>}
 {/* Why the number is low (or a dash) when the signal is red: the units
     are already on a truck. Without this the drop reads as a bug. */}
 {(item.incoming_qty ?? 0) > 0 && (
 <div style={{ fontSize: 10.5, color: C.muted, marginTop: 2 }}>
 {incomingText(t, item.incoming_qty, item.incoming_sources)}
 </div>
 )}
 </div>
 <span style={{ fontSize: 12, color: C.muted }}>{item.supplier || '—'}</span>
 </div>
 ))}
 <Pagination page={paged.page} pageCount={paged.pageCount} offset={paged.offset} total={paged.total} rowsOnPage={pageItems.length} onPage={setPage} label="SKU" />
 </div>

 ) : viewMode === 'capital' && narrow ? (
 /* ── Money not moving, phone ── */
 <div>
  <p style={{ margin: '0 0 12px', fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('inventory.deadcap_intro', { days: deadCapitalWindow })}</p>
  <MobileControls>
   <MField label={t('inventory.deadcap_window_label')}>
    <input type="number" inputMode="numeric" min={7} max={365} value={deadCapitalWindow} name="m_deadcap_window" style={mInput}
     onChange={e => { const v = Number(e.target.value); if (Number.isFinite(v) && v >= 7 && v <= 365) setDeadCapitalWindow(v) }} />
   </MField>
  </MobileControls>
  {loadingDeadCapital ? (
   <div style={{ padding: 40, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
  ) : !deadCapital ? null : deadCapital.sku_count === 0 ? (
   <div style={{ padding: '32px 8px', textAlign: 'center' }}>
    <div style={{ fontSize: 15, fontWeight: 600, color: C.green, marginBottom: 8 }}>{t('inventory.deadcap_none')}</div>
    <div style={{ fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('inventory.deadcap_none_desc', { days: deadCapital.window_days })}</div>
   </div>
  ) : (
   <>
    <div style={{ marginBottom: 12 }}>
     <MobileMetricGrid metrics={[
      { label: t('inventory.deadcap_kpi_total'), value: formatMoneyCompact(deadCapital.total_value), color: C.red },
      { label: t('inventory.deadcap_kpi_skus'), value: deadCapital.sku_count, color: C.amber },
      ...(deadCapital.unpriced_sku_count > 0 ? [{ label: t('inventory.deadcap_kpi_unpriced'), value: deadCapital.unpriced_sku_count, color: C.dim }] : []),
     ]} />
    </div>
    {deadCapital.unpriced_sku_count > 0 && (
     <div style={{ padding: '10px 12px', borderRadius: 10, fontSize: 13, lineHeight: 1.45, background: 'color-mix(in srgb, var(--dim) 10%, transparent)', color: C.dim, marginBottom: 12 }}>{t('inventory.deadcap_unpriced_banner', { count: deadCapital.unpriced_sku_count })}</div>
    )}
    <MobileList ariaLabel={t('inventory.view_dead_capital')}>
     {deadCapitalPaged.rows.map(item => (
      <MobileCard key={item.sku}
       title={item.display_name || item.sku}
       subtitle={[item.display_name ? item.sku : null, item.category, item.supplier].filter(Boolean).join(' · ') || undefined}
       status={item.signal ? { label: signalLabel(t, item.signal), tone: signalTone(item.signal) } : undefined}
       value={<span style={{ color: item.value === null ? C.dim : C.red }}>{item.value === null ? t('inventory.deadcap_value_unknown_short') : formatMoneyCompact(item.value)}</span>}
       valueCaption={`${fmtNum(item.current_stock)} ${t('inventory.unit_und')}`}>
       <span style={{ fontSize: 12.5, fontWeight: 600, color: C.red }}>
        {t(item.days_still_exact ? 'inventory.deadcap_days_still_exact' : 'inventory.deadcap_days_still_approx', { days: item.days_still })}
       </span>
       {item.value === null && <span style={{ display: 'block', fontSize: 12, color: C.dim, marginTop: 2 }}>{t('inventory.deadcap_value_unknown')}</span>}
      </MobileCard>
     ))}
    </MobileList>
    <Pagination page={deadCapitalPaged.page} pageCount={deadCapitalPaged.pageCount} offset={deadCapitalPaged.offset} total={deadCapitalPaged.total} rowsOnPage={deadCapitalPaged.rows.length} onPage={setDeadCapitalPage} label="SKU" />
    {(deadCapital.excluded_no_history > 0 || deadCapital.excluded_too_recent > 0) && (
     <div style={{ marginTop: 12, fontSize: 12, color: C.dim, lineHeight: 1.45 }}>
      {t('inventory.deadcap_footer_excluded', { noHistory: deadCapital.excluded_no_history, tooRecent: deadCapital.excluded_too_recent, days: deadCapital.window_days })}
     </div>
    )}
   </>
  )}
 </div>

 ) : viewMode === 'capital' ? (
 /* ── Capital parado / "plata parada" ─────────────────────────
    The one view of money that is not moving: needs no session, ranks every
    SKU by money that has not moved (real stock-level history), worst first,
    with the tenant's total at the top. See getDeadCapital / dead_capital.py. */
 <div style={{ padding: 16 }}>
  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
   <p style={{ margin: 0, fontSize: 12, color: C.dim, maxWidth: 560 }}>
    {t('inventory.deadcap_intro', { days: deadCapitalWindow })}
   </p>
   <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: C.dim }}>
    {t('inventory.deadcap_window_label')}
    <input
     type="number" min={7} max={365} value={deadCapitalWindow}
     onChange={e => {
      const v = Number(e.target.value)
      if (Number.isFinite(v) && v >= 7 && v <= 365) setDeadCapitalWindow(v)
     }}
     style={{ ...inputS, width: 64 }}
     aria-label={t('inventory.deadcap_window_label')}
    />
   </label>
  </div>

  {loadingDeadCapital ? (
   <div style={{ padding: 48, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
  ) : !deadCapital ? null : deadCapital.sku_count === 0 ? (
   <div style={{ padding: '40px 0', textAlign: 'center' }}>
    <div style={{ fontSize: 14, fontWeight: 600, color: C.green, marginBottom: 8 }}>
     {t('inventory.deadcap_none')}
    </div>
    <div style={{ fontSize: 13, color: C.dim }}>
     {t('inventory.deadcap_none_desc', { days: deadCapital.window_days })}
    </div>
   </div>
  ) : (
   <>
    {/* Total in money, first — the number the owner asked to see at the top. */}
    <div style={{
     display: 'grid',
     gridTemplateColumns: deadCapital.unpriced_sku_count > 0 ? 'repeat(3,1fr)' : 'repeat(2,1fr)',
     gap: 12, marginBottom: 20,
    }}>
     <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${C.red}`,
     }}>
      <div style={{ fontSize: 22, fontWeight: 800, color: C.red }}>{formatMoneyCompact(deadCapital.total_value)}</div>
      <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{t('inventory.deadcap_kpi_total')}</div>
     </div>
     <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${C.amber}`,
     }}>
      <div style={{ fontSize: 20, fontWeight: 800, color: C.amber }}>{deadCapital.sku_count}</div>
      <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{t('inventory.deadcap_kpi_skus')}</div>
     </div>
     {deadCapital.unpriced_sku_count > 0 && (
      <div style={{
       background: C.surface, border: `1px solid ${C.border}`,
       borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${C.dim}`,
      }}>
       <div style={{ fontSize: 20, fontWeight: 800, color: C.text }}>{deadCapital.unpriced_sku_count}</div>
       <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{t('inventory.deadcap_kpi_unpriced')}</div>
      </div>
     )}
    </div>

    {deadCapital.unpriced_sku_count > 0 && (
     <div style={{
      marginBottom: 14, padding: '10px 14px', borderRadius: 8, fontSize: 11.5,
      background: 'color-mix(in srgb, var(--dim) 10%, transparent)', color: C.dim,
     }}>
      {t('inventory.deadcap_unpriced_banner', { count: deadCapital.unpriced_sku_count })}
     </div>
    )}

    <div style={{ borderRadius: 10, border: `1px solid ${C.border}`, overflow: 'hidden' }}>
     <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
      <thead>
       <tr style={{ background: C.card }}>
        {[
         t('inventory.deadcap_col_product'), t('inventory.deadcap_col_days_still'),
         t('inventory.deadcap_col_stock'), t('inventory.deadcap_col_value'),
         t('inventory.deadcap_col_category'), t('inventory.deadcap_col_signal'),
        ].map(h => (
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
       {deadCapitalPaged.rows.map((item, i: number) => (
        <tr key={item.sku} style={{
         background: (deadCapitalPaged.offset + i) % 2 === 0 ? C.surface : C.card,
         borderBottom: `1px solid ${C.border}`,
        }}>
         <th scope="row" style={{ padding: '10px 12px', textAlign: 'left', fontWeight: 400, color: C.text }}>
          <div style={{ fontWeight: 600 }}>{item.display_name || item.sku}</div>
          <div style={{ fontSize: 10, color: C.dim, fontFamily: 'monospace' }}>{item.sku}</div>
          {item.supplier && <div style={{ fontSize: 10, color: C.muted }}>{item.supplier}</div>}
         </th>
         <td style={{ padding: '10px 12px', color: C.red, fontWeight: 700 }}>
          {t(item.days_still_exact ? 'inventory.deadcap_days_still_exact' : 'inventory.deadcap_days_still_approx', { days: item.days_still })}
         </td>
         <td style={{ padding: '10px 12px', color: C.text }}>
          {item.current_stock.toLocaleString()}
         </td>
         <td style={{ padding: '10px 12px', fontWeight: 700, color: item.value === null ? C.dim : C.red }}>
          {item.value === null
           ? <Tooltip text={t('inventory.deadcap_value_unknown')}><span>{t('inventory.deadcap_value_unknown_short')}</span></Tooltip>
           : formatMoneyCompact(item.value)}
         </td>
         <td style={{ padding: '10px 12px', fontSize: 11, color: C.muted }}>
          {item.category || '—'}
         </td>
         <td style={{ padding: '10px 12px' }}>
          {item.signal
           ? <SignalBadge s={item.signal} />
           : <span style={{ fontSize: 10, color: C.dim }}>{t('inventory.deadcap_signal_none')}</span>}
         </td>
        </tr>
       ))}
      </tbody>
     </table>
    </div>

    <Pagination page={deadCapitalPaged.page} pageCount={deadCapitalPaged.pageCount} offset={deadCapitalPaged.offset} total={deadCapitalPaged.total} rowsOnPage={deadCapitalPaged.rows.length} onPage={setDeadCapitalPage} label="SKU" />

    {(deadCapital.excluded_no_history > 0 || deadCapital.excluded_too_recent > 0) && (
     <div style={{ marginTop: 12, fontSize: 11, color: C.dim }}>
      {t('inventory.deadcap_footer_excluded', {
       noHistory: deadCapital.excluded_no_history,
       tooRecent: deadCapital.excluded_too_recent,
       days: deadCapital.window_days,
      })}
     </div>
    )}
   </>
  )}
 </div>

 ) : viewMode === 'inflation' && narrow ? (
 /* ── Supplier cost inflation, phone ── */
 <div>
  <p style={{ margin: '0 0 12px', fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('inventory.inflation_intro', { days: costInflationWindow })}</p>
  <MobileControls>
   <MField label={t('inventory.inflation_window_label')}>
    <input type="number" inputMode="numeric" min={30} max={1095} value={costInflationWindow} name="m_inflation_window" style={mInput}
     onChange={e => { const v = Number(e.target.value); if (Number.isFinite(v) && v >= 30 && v <= 1095) setCostInflationWindow(v) }} />
   </MField>
  </MobileControls>
  {loadingCostInflation ? (
   <div style={{ padding: 40, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
  ) : !costInflation ? null : costInflation.supplier_count === 0 ? (
   <div style={{ padding: '32px 8px', textAlign: 'center' }}>
    <div style={{ fontSize: 15, fontWeight: 600, color: C.green, marginBottom: 8 }}>{t('inventory.inflation_none')}</div>
    <div style={{ fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('inventory.inflation_none_desc', { days: costInflation.window_days })}</div>
   </div>
  ) : (
   <>
    <div style={{ marginBottom: 12 }}>
     <MobileMetricGrid metrics={[{ label: t('inventory.inflation_kpi_suppliers'), value: costInflation.supplier_count, color: C.red }]} />
    </div>
    <MobileList ariaLabel={t('inventory.view_cost_inflation')}>
     {costInflationPaged.rows.map(sp => (
      <MobileCard key={sp.supplier}
       title={sp.supplier}
       subtitle={`${t('inventory.inflation_col_increases')}: ${sp.increases_count}`}
       value={<span style={{ color: C.red }}>+{sp.cumulative_pct}%</span>}
       valueCaption={t('inventory.inflation_col_cumulative')}>
       {sp.worst_products.map(p => (
        <span key={p.sku} style={{ display: 'block', fontSize: 12.5, color: C.muted, lineHeight: 1.45, marginTop: 2, whiteSpace: 'normal' }}>
         <strong style={{ color: C.text }}>{p.display_name || p.sku}</strong>{' — '}
         {t('inventory.inflation_product_change', { first: p.first_cost, last: p.last_cost, pct: p.cumulative_pct })}
        </span>
       ))}
      </MobileCard>
     ))}
    </MobileList>
    <Pagination page={costInflationPaged.page} pageCount={costInflationPaged.pageCount} offset={costInflationPaged.offset} total={costInflationPaged.total} rowsOnPage={costInflationPaged.rows.length} onPage={setCostInflationPage} label={t('inventory.inflation_col_supplier')} />
    <div style={{ marginTop: 12, fontSize: 12, color: C.dim, lineHeight: 1.45 }}>{t('inventory.inflation_footer_note')}</div>
    {(costInflation.skus_single_observation > 0 || costInflation.lines_excluded_no_supplier > 0) && (
     <div style={{ marginTop: 4, fontSize: 12, color: C.dim, lineHeight: 1.45 }}>
      {t('inventory.inflation_excluded_note', { single: costInflation.skus_single_observation, noSupplier: costInflation.lines_excluded_no_supplier })}
     </div>
    )}
   </>
  )}
 </div>

 ) : viewMode === 'inflation' ? (
 /* ── Supplier cost inflation (stability.md #20 item 5) ───────────
    Suppliers who raised a SKU's cost at least once in the window, from
    only received orders — see getSupplierCostInflation / cost_alerts.py. */
 <div style={{ padding: 16 }}>
  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
   <p style={{ margin: 0, fontSize: 12, color: C.dim, maxWidth: 560 }}>
    {t('inventory.inflation_intro', { days: costInflationWindow })}
   </p>
   <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: C.dim }}>
    {t('inventory.inflation_window_label')}
    <input
     type="number" min={30} max={1095} value={costInflationWindow}
     onChange={e => {
      const v = Number(e.target.value)
      if (Number.isFinite(v) && v >= 30 && v <= 1095) setCostInflationWindow(v)
     }}
     style={{ ...inputS, width: 72 }}
     aria-label={t('inventory.inflation_window_label')}
    />
   </label>
  </div>

  {loadingCostInflation ? (
   <div style={{ padding: 48, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
  ) : !costInflation ? null : costInflation.supplier_count === 0 ? (
   <div style={{ padding: '40px 0', textAlign: 'center' }}>
    <div style={{ fontSize: 14, fontWeight: 600, color: C.green, marginBottom: 8 }}>
     {t('inventory.inflation_none')}
    </div>
    <div style={{ fontSize: 13, color: C.dim }}>
     {t('inventory.inflation_none_desc', { days: costInflation.window_days })}
    </div>
   </div>
  ) : (
   <>
    <div style={{
     background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10,
     padding: '14px 18px', borderTop: `3px solid ${C.red}`, marginBottom: 20, maxWidth: 260,
    }}>
     <div style={{ fontSize: 22, fontWeight: 800, color: C.red }}>{costInflation.supplier_count}</div>
     <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{t('inventory.inflation_kpi_suppliers')}</div>
    </div>

    <div style={{ borderRadius: 10, border: `1px solid ${C.border}`, overflow: 'hidden' }}>
     <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
      <thead>
       <tr style={{ background: C.card }}>
        {[
         t('inventory.inflation_col_supplier'), t('inventory.inflation_col_increases'),
         t('inventory.inflation_col_cumulative'), t('inventory.inflation_col_products'),
        ].map(h => (
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
       {costInflationPaged.rows.map((sp, i: number) => (
        <tr key={sp.supplier} style={{
         background: (costInflationPaged.offset + i) % 2 === 0 ? C.surface : C.card,
         borderBottom: `1px solid ${C.border}`,
        }}>
         <th scope="row" style={{ padding: '10px 12px', textAlign: 'left', fontWeight: 600, color: C.text }}>
          {sp.supplier}
         </th>
         <td style={{ padding: '10px 12px', color: C.text }}>{sp.increases_count}</td>
         <td style={{ padding: '10px 12px', fontWeight: 700, color: C.red }}>+{sp.cumulative_pct}%</td>
         <td style={{ padding: '10px 12px', fontSize: 11, color: C.muted }}>
          {sp.worst_products.map(p => (
           <div key={p.sku} style={{ marginBottom: 2 }}>
            <span style={{ fontWeight: 600, color: C.text }}>{p.display_name || p.sku}</span>
            {' — '}
            {t('inventory.inflation_product_change', { first: p.first_cost, last: p.last_cost, pct: p.cumulative_pct })}
           </div>
          ))}
         </td>
        </tr>
       ))}
      </tbody>
     </table>
    </div>

    <Pagination page={costInflationPaged.page} pageCount={costInflationPaged.pageCount} offset={costInflationPaged.offset} total={costInflationPaged.total} rowsOnPage={costInflationPaged.rows.length} onPage={setCostInflationPage} label={t('inventory.inflation_col_supplier')} />

    <div style={{ marginTop: 12, fontSize: 11, color: C.dim }}>{t('inventory.inflation_footer_note')}</div>
    {(costInflation.skus_single_observation > 0 || costInflation.lines_excluded_no_supplier > 0) && (
     <div style={{ marginTop: 4, fontSize: 11, color: C.dim }}>
      {t('inventory.inflation_excluded_note', {
       single: costInflation.skus_single_observation,
       noSupplier: costInflation.lines_excluded_no_supplier,
      })}
     </div>
    )}
   </>
  )}
 </div>

 ) : viewMode === 'erosion' && narrow ? (
 /* ── Margin erosion, phone ── */
 <div>
  <p style={{ margin: '0 0 12px', fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('inventory.erosion_intro')}</p>
  <MobileControls>
   <MField label={t('inventory.erosion_window_label')}>
    <input type="number" inputMode="numeric" min={30} max={1095} value={marginErosionWindow} name="m_erosion_window" style={mInput}
     onChange={e => { const v = Number(e.target.value); if (Number.isFinite(v) && v >= 30 && v <= 1095) setMarginErosionWindow(v) }} />
   </MField>
   <MField label={t('inventory.erosion_min_pts_label')}>
    <input type="number" inputMode="decimal" min={0} max={100} step={0.5} value={marginErosionMinPts} name="m_erosion_min_pts" style={mInput}
     onChange={e => { const v = Number(e.target.value); if (Number.isFinite(v) && v >= 0 && v <= 100) setMarginErosionMinPts(v) }} />
   </MField>
  </MobileControls>
  <div style={{ padding: '10px 12px', borderRadius: 10, fontSize: 13, lineHeight: 1.45, background: 'color-mix(in srgb, var(--dim) 10%, transparent)', color: C.dim, marginBottom: 12 }}>{t('inventory.erosion_disclaimer')}</div>
  {loadingMarginErosion ? (
   <div style={{ padding: 40, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
  ) : !marginErosion ? null : marginErosion.sku_count === 0 ? (
   <div style={{ padding: '32px 8px', textAlign: 'center' }}>
    <div style={{ fontSize: 15, fontWeight: 600, color: C.green, marginBottom: 8 }}>{t('inventory.erosion_none')}</div>
    <div style={{ fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('inventory.erosion_none_desc', { pts: marginErosionMinPts, days: marginErosion.window_days })}</div>
   </div>
  ) : (
   <>
    <div style={{ marginBottom: 12 }}>
     <MobileMetricGrid metrics={[{ label: t('inventory.erosion_kpi_count'), value: marginErosion.sku_count, color: C.red }]} />
    </div>
    <MobileList ariaLabel={t('inventory.view_margin_erosion')}>
     {marginErosionPaged.rows.map(item => (
      <MobileCard key={item.sku}
       title={item.display_name || item.sku}
       subtitle={`${t('inventory.erosion_col_cost_then')} ${formatMoneyCompact(item.cost_then)} → ${formatMoneyCompact(item.cost_now)}`}
       value={<span style={{ color: C.red }}>-{item.erosion_pts} pts</span>}
       valueCaption={t('inventory.erosion_col_erosion')}>
       <span style={{ fontSize: 12.5, color: C.muted }}>
        {t('inventory.erosion_col_margin_then')} {item.margin_pct_then}% → <strong style={{ color: item.margin_pct_now < 0 ? C.red : C.text }}>{item.margin_pct_now}%</strong>
       </span>
       {item.margin_pct_now < 0 && <span style={{ display: 'block', fontSize: 12, color: C.red, marginTop: 2 }}>{t('inventory.erosion_margin_negative')}</span>}
      </MobileCard>
     ))}
    </MobileList>
    <Pagination page={marginErosionPaged.page} pageCount={marginErosionPaged.pageCount} offset={marginErosionPaged.offset} total={marginErosionPaged.total} rowsOnPage={marginErosionPaged.rows.length} onPage={setMarginErosionPage} label="SKU" />
    {(marginErosion.excluded_no_sale_price > 0 || marginErosion.excluded_no_cost_history > 0) && (
     <div style={{ marginTop: 12, fontSize: 12, color: C.dim, lineHeight: 1.45 }}>
      {t('inventory.erosion_excluded_note', { noPrice: marginErosion.excluded_no_sale_price, noHistory: marginErosion.excluded_no_cost_history })}
     </div>
    )}
   </>
  )}
 </div>

 ) : viewMode === 'erosion' ? (
 /* ── Margin erosion (stability.md #20 item 6) ─────────────────────
    Cost history crossed with the SKU's CURRENT sale_price — the only one
    StockAI stores. See getMarginErosion / cost_alerts.get_margin_erosion for
    why margin_pct_then is a hypothetical, not a historical fact. */
 <div style={{ padding: 16 }}>
  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
   <p style={{ margin: 0, fontSize: 12, color: C.dim, maxWidth: 560 }}>
    {t('inventory.erosion_intro')}
   </p>
   <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: C.dim }}>
     {t('inventory.erosion_window_label')}
     <input
      type="number" min={30} max={1095} value={marginErosionWindow}
      onChange={e => {
       const v = Number(e.target.value)
       if (Number.isFinite(v) && v >= 30 && v <= 1095) setMarginErosionWindow(v)
      }}
      style={{ ...inputS, width: 72 }}
      aria-label={t('inventory.erosion_window_label')}
     />
    </label>
    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: C.dim }}>
     {t('inventory.erosion_min_pts_label')}
     <input
      type="number" min={0} max={100} step={0.5} value={marginErosionMinPts}
      onChange={e => {
       const v = Number(e.target.value)
       if (Number.isFinite(v) && v >= 0 && v <= 100) setMarginErosionMinPts(v)
      }}
      style={{ ...inputS, width: 64 }}
      aria-label={t('inventory.erosion_min_pts_label')}
     />
    </label>
   </div>
  </div>

  <div style={{
   marginBottom: 16, padding: '10px 14px', borderRadius: 8, fontSize: 11.5,
   background: 'color-mix(in srgb, var(--dim) 10%, transparent)', color: C.dim,
  }}>
   {t('inventory.erosion_disclaimer')}
  </div>

  {loadingMarginErosion ? (
   <div style={{ padding: 48, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
  ) : !marginErosion ? null : marginErosion.sku_count === 0 ? (
   <div style={{ padding: '40px 0', textAlign: 'center' }}>
    <div style={{ fontSize: 14, fontWeight: 600, color: C.green, marginBottom: 8 }}>
     {t('inventory.erosion_none')}
    </div>
    <div style={{ fontSize: 13, color: C.dim }}>
     {t('inventory.erosion_none_desc', { pts: marginErosionMinPts, days: marginErosion.window_days })}
    </div>
   </div>
  ) : (
   <>
    <div style={{
     background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10,
     padding: '14px 18px', borderTop: `3px solid ${C.red}`, marginBottom: 20, maxWidth: 260,
    }}>
     <div style={{ fontSize: 22, fontWeight: 800, color: C.red }}>{marginErosion.sku_count}</div>
     <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{t('inventory.erosion_kpi_count')}</div>
    </div>

    <div style={{ borderRadius: 10, border: `1px solid ${C.border}`, overflow: 'hidden' }}>
     <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
      <thead>
       <tr style={{ background: C.card }}>
        {[
         t('inventory.erosion_col_product'), t('inventory.erosion_col_cost_then'),
         t('inventory.erosion_col_cost_now'), t('inventory.erosion_col_margin_then'),
         t('inventory.erosion_col_margin_now'), t('inventory.erosion_col_erosion'),
        ].map(h => (
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
       {marginErosionPaged.rows.map((item, i: number) => (
        <tr key={item.sku} style={{
         background: (marginErosionPaged.offset + i) % 2 === 0 ? C.surface : C.card,
         borderBottom: `1px solid ${C.border}`,
        }}>
         <th scope="row" style={{ padding: '10px 12px', textAlign: 'left', fontWeight: 400, color: C.text }}>
          <div style={{ fontWeight: 600 }}>{item.display_name || item.sku}</div>
          <div style={{ fontSize: 10, color: C.dim, fontFamily: 'monospace' }}>{item.sku}</div>
         </th>
         <td style={{ padding: '10px 12px', color: C.text }}>{formatMoneyCompact(item.cost_then)}</td>
         <td style={{ padding: '10px 12px', color: C.text }}>{formatMoneyCompact(item.cost_now)}</td>
         <td style={{ padding: '10px 12px', color: C.dim }}>{item.margin_pct_then}%</td>
         <td style={{ padding: '10px 12px', fontWeight: 700, color: item.margin_pct_now < 0 ? C.red : C.text }}>
          {item.margin_pct_now < 0
           ? <Tooltip text={t('inventory.erosion_margin_negative')}><span>{item.margin_pct_now}%</span></Tooltip>
           : `${item.margin_pct_now}%`}
         </td>
         <td style={{ padding: '10px 12px', fontWeight: 700, color: C.red }}>-{item.erosion_pts} pts</td>
        </tr>
       ))}
      </tbody>
     </table>
    </div>

    <Pagination page={marginErosionPaged.page} pageCount={marginErosionPaged.pageCount} offset={marginErosionPaged.offset} total={marginErosionPaged.total} rowsOnPage={marginErosionPaged.rows.length} onPage={setMarginErosionPage} label="SKU" />

    {(marginErosion.excluded_no_sale_price > 0 || marginErosion.excluded_no_cost_history > 0) && (
     <div style={{ marginTop: 12, fontSize: 11, color: C.dim }}>
      {t('inventory.erosion_excluded_note', {
       noPrice: marginErosion.excluded_no_sale_price,
       noHistory: marginErosion.excluded_no_cost_history,
      })}
     </div>
    )}
   </>
  )}
 </div>

 ) : viewMode === 'money' && narrow ? (
 /* ── The forecast in money, phone ── */
 <div>
  {loadingForecastMoney ? (
   <div style={{ padding: 40, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
  ) : !forecastMoney ? (
   <div style={{ padding: 32, textAlign: 'center', color: C.dim, fontSize: 14 }}>{t('inventory.money_select_session')}</div>
  ) : forecastMoney.sku_count === 0 ? (
   <div style={{ padding: '32px 8px', textAlign: 'center' }}>
    <div style={{ fontSize: 15, fontWeight: 600, color: C.dim, marginBottom: 8 }}>{t('inventory.money_none')}</div>
    <div style={{ fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('inventory.money_none_desc')}</div>
   </div>
  ) : (
   <>
    <p style={{ margin: '0 0 12px', fontSize: 13, color: C.dim, lineHeight: 1.5 }}>
     {t('inventory.money_intro', { days: forecastMoney.horizon_days, start: forecastMoney.horizon_start ?? '—', end: forecastMoney.horizon_end ?? '—' })}
    </p>
    <div style={{ padding: '10px 12px', borderRadius: 10, fontSize: 13, lineHeight: 1.45, background: 'color-mix(in srgb, var(--dim) 10%, transparent)', color: C.dim, marginBottom: 12 }}>{t('inventory.money_price_caveat')}</div>
    <div style={{ marginBottom: 12 }}>
     <MobileMetricGrid metrics={[
      { label: t('inventory.money_kpi_revenue'), value: formatMoneyCompact(forecastMoney.total_revenue), color: C.text },
      { label: `${t('inventory.money_kpi_margin')}${forecastMoney.total_margin_pct != null ? ` (${forecastMoney.total_margin_pct}%)` : ''}`, value: formatMoneyCompact(forecastMoney.total_margin), color: forecastMoney.total_margin < 0 ? C.red : C.green },
      { label: t('inventory.money_kpi_skus'), value: forecastMoney.sku_count, color: C.amber },
     ]} />
    </div>
    {forecastMoney.top10_margin_share_pct != null && (
     <div style={{ marginBottom: 12, padding: '10px 12px', borderRadius: 10, fontSize: 13, lineHeight: 1.45, background: 'color-mix(in srgb, var(--accent) 8%, transparent)', color: C.text }}>
      {t('inventory.money_top_contributors_note', { n: forecastMoney.top_contributors, pct: forecastMoney.top10_margin_share_pct })}
     </div>
    )}
    {(forecastMoney.excluded_no_price_count > 0 || forecastMoney.excluded_no_cost_count > 0) && (
     <div style={{ padding: '10px 12px', borderRadius: 10, fontSize: 13, lineHeight: 1.45, background: 'color-mix(in srgb, var(--dim) 10%, transparent)', color: C.dim, marginBottom: 12 }}>{t('inventory.money_excluded_note', { noPrice: forecastMoney.excluded_no_price_count, noCost: forecastMoney.excluded_no_cost_count })}</div>
    )}
    <MobileList ariaLabel={t('inventory.view_forecast_money')}>
     {forecastMoneyPaged.rows.map(item => (
      <MobileCard key={item.sku}
       title={item.display_name || item.sku}
       subtitle={`${fmtNum(item.units_forecast)} ${t('inventory.unit_und')} · ${t('inventory.money_col_revenue')} ${moneyOr(item.revenue, t('inventory.money_value_unknown_short'))}`}
       value={<span style={{ color: item.margin === null ? C.dim : item.margin < 0 ? C.red : C.green }}>{moneyOr(item.margin, t('inventory.money_value_unknown_short'))}</span>}
       valueCaption={item.margin_pct === null ? t('inventory.money_col_margin') : `${t('inventory.money_col_margin')} ${item.margin_pct}%`}>
       <span style={{ fontSize: 12.5, color: C.muted }}>
        {t('inventory.money_col_cost')} {moneyOr(item.cost, t('inventory.money_value_unknown_short'))}
        {item.contribution_pct !== null && ` · ${t('inventory.money_col_contribution')} ${item.contribution_pct}%`}
       </span>
       {(item.revenue === null || item.cost === null) && (
        <span style={{ display: 'block', fontSize: 12, color: C.dim, marginTop: 2, whiteSpace: 'normal' }}>
         {t('inventory.money_value_unknown', { reason: t(`inventory.money_reason_${item.revenue === null ? item.revenue_unknown_reason : item.cost_unknown_reason}`) })}
        </span>
       )}
      </MobileCard>
     ))}
    </MobileList>
    <Pagination page={forecastMoneyPaged.page} pageCount={forecastMoneyPaged.pageCount} offset={forecastMoneyPaged.offset} total={forecastMoneyPaged.total} rowsOnPage={forecastMoneyPaged.rows.length} onPage={setForecastMoneyPage} label="SKU" />
   </>
  )}
 </div>

 ) : viewMode === 'money' ? (
 /* ── The forecast in money (stability.md #20 item 1) ─────────────
    The engine predicts units, the product knows sale_price/unit_cost per
    SKU — this multiplies them over the active session's forecast horizon.
    See getForecastMoney / forecast_money.get_forecast_money. */
 <div style={{ padding: 16 }}>
  {loadingForecastMoney ? (
   <div style={{ padding: 48, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
  ) : !forecastMoney ? (
   <div style={{ padding: 48, textAlign: 'center', color: C.dim, fontSize: 13 }}>
    {t('inventory.money_select_session')}
   </div>
  ) : forecastMoney.sku_count === 0 ? (
   <div style={{ padding: '40px 0', textAlign: 'center' }}>
    <div style={{ fontSize: 14, fontWeight: 600, color: C.dim, marginBottom: 8 }}>
     {t('inventory.money_none')}
    </div>
    <div style={{ fontSize: 13, color: C.dim }}>
     {t('inventory.money_none_desc')}
    </div>
   </div>
  ) : (
   <>
    <p style={{ margin: '0 0 12px', fontSize: 12, color: C.dim, maxWidth: 620 }}>
     {t('inventory.money_intro', {
      days: forecastMoney.horizon_days,
      start: forecastMoney.horizon_start ?? '—',
      end: forecastMoney.horizon_end ?? '—',
     })}
    </p>

    {/* StockAI stores no price history — the whole projection is built on
        today's sale_price, so this says so once instead of implying the
        product knows tomorrow's price. */}
    <div style={{
     marginBottom: 16, padding: '10px 14px', borderRadius: 8, fontSize: 11.5,
     background: 'color-mix(in srgb, var(--dim) 10%, transparent)', color: C.dim,
    }}>
     {t('inventory.money_price_caveat')}
    </div>

    {/* Totals, first — the number the owner asked to see at the top. */}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginBottom: 12 }}>
     <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${C.text}`,
     }}>
      <div style={{ fontSize: 22, fontWeight: 800, color: C.text }}>{formatMoneyCompact(forecastMoney.total_revenue)}</div>
      <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{t('inventory.money_kpi_revenue')}</div>
     </div>
     <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${forecastMoney.total_margin < 0 ? C.red : C.green}`,
     }}>
      <div style={{ fontSize: 22, fontWeight: 800, color: forecastMoney.total_margin < 0 ? C.red : C.green }}>
       {formatMoneyCompact(forecastMoney.total_margin)}
      </div>
      <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>
       {t('inventory.money_kpi_margin')}
       {forecastMoney.total_margin_pct != null && ` (${forecastMoney.total_margin_pct}%)`}
      </div>
     </div>
     <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${C.amber}`,
     }}>
      <div style={{ fontSize: 20, fontWeight: 800, color: C.amber }}>{forecastMoney.sku_count}</div>
      <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{t('inventory.money_kpi_skus')}</div>
     </div>
    </div>

    {forecastMoney.top10_margin_share_pct != null && (
     <div style={{
      marginBottom: 16, padding: '10px 14px', borderRadius: 8, fontSize: 12,
      background: 'color-mix(in srgb, var(--accent) 8%, transparent)', color: C.text,
     }}>
      {t('inventory.money_top_contributors_note', {
       n: forecastMoney.top_contributors,
       pct: forecastMoney.top10_margin_share_pct,
      })}
     </div>
    )}

    {(forecastMoney.excluded_no_price_count > 0 || forecastMoney.excluded_no_cost_count > 0) && (
     <div style={{
      marginBottom: 14, padding: '10px 14px', borderRadius: 8, fontSize: 11.5,
      background: 'color-mix(in srgb, var(--dim) 10%, transparent)', color: C.dim,
     }}>
      {t('inventory.money_excluded_note', {
       noPrice: forecastMoney.excluded_no_price_count,
       noCost: forecastMoney.excluded_no_cost_count,
      })}
     </div>
    )}

    <div style={{ borderRadius: 10, border: `1px solid ${C.border}`, overflow: 'hidden' }}>
     <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
      <thead>
       <tr style={{ background: C.card }}>
        {[
         t('inventory.money_col_product'), t('inventory.money_col_units'),
         t('inventory.money_col_revenue'), t('inventory.money_col_cost'),
         t('inventory.money_col_margin'), t('inventory.money_col_margin_pct'),
         t('inventory.money_col_contribution'),
        ].map(h => (
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
       {forecastMoneyPaged.rows.map((item, i: number) => (
        <tr key={item.sku} style={{
         background: (forecastMoneyPaged.offset + i) % 2 === 0 ? C.surface : C.card,
         borderBottom: `1px solid ${C.border}`,
        }}>
         <th scope="row" style={{ padding: '10px 12px', textAlign: 'left', fontWeight: 400, color: C.text }}>
          <div style={{ fontWeight: 600 }}>{item.display_name || item.sku}</div>
          <div style={{ fontSize: 10, color: C.dim, fontFamily: 'monospace' }}>{item.sku}</div>
          {item.supplier && <div style={{ fontSize: 10, color: C.muted }}>{item.supplier}</div>}
         </th>
         <td style={{ padding: '10px 12px', color: C.text }}>
          {item.units_forecast.toLocaleString()}
         </td>
         <td style={{ padding: '10px 12px', color: C.text }}>
          {item.revenue === null
           ? <Tooltip text={t('inventory.money_value_unknown', { reason: t(`inventory.money_reason_${item.revenue_unknown_reason}`) })}>
              <span style={{ color: C.dim }}>{t('inventory.money_value_unknown_short')}</span>
             </Tooltip>
           : formatMoneyCompact(item.revenue)}
         </td>
         <td style={{ padding: '10px 12px', color: C.text }}>
          {item.cost === null
           ? <Tooltip text={t('inventory.money_value_unknown', { reason: t(`inventory.money_reason_${item.cost_unknown_reason}`) })}>
              <span style={{ color: C.dim }}>{t('inventory.money_value_unknown_short')}</span>
             </Tooltip>
           : formatMoneyCompact(item.cost)}
         </td>
         <td style={{ padding: '10px 12px', fontWeight: 700, color: item.margin === null ? C.dim : item.margin < 0 ? C.red : C.green }}>
          {item.margin === null
           ? <span>{t('inventory.money_value_unknown_short')}</span>
           : formatMoneyCompact(item.margin)}
         </td>
         <td style={{ padding: '10px 12px', color: item.margin_pct !== null && item.margin_pct < 0 ? C.red : C.dim }}>
          {item.margin_pct === null ? '—' : `${item.margin_pct}%`}
         </td>
         <td style={{ padding: '10px 12px', color: C.dim }}>
          {item.contribution_pct === null ? '—' : `${item.contribution_pct}%`}
         </td>
        </tr>
       ))}
      </tbody>
     </table>
    </div>

    <Pagination page={forecastMoneyPaged.page} pageCount={forecastMoneyPaged.pageCount} offset={forecastMoneyPaged.offset} total={forecastMoneyPaged.total} rowsOnPage={forecastMoneyPaged.rows.length} onPage={setForecastMoneyPage} label="SKU" />
   </>
  )}
 </div>

 ) : viewMode === 'ignored' && narrow ? (
 /* ── Cost of ignoring, phone ── */
 <div>
  <p style={{ margin: '0 0 12px', fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('inventory.ignoring_intro')}</p>
  <MobileControls>
   <MField label={t('inventory.ignoring_from_label')}>
    <input type="date" value={ignoringFromDate} max={ignoringToDate} name="m_ignoring_from" style={mInput}
     onChange={e => e.target.value && setIgnoringFromDate(e.target.value)} />
   </MField>
   <MField label={t('inventory.ignoring_to_label')}>
    <input type="date" value={ignoringToDate} min={ignoringFromDate} max={todayIso} name="m_ignoring_to" style={mInput}
     onChange={e => e.target.value && setIgnoringToDate(e.target.value)} />
   </MField>
   <MField label={t('inventory.ignoring_po_window_label')}>
    <input type="number" inputMode="numeric" min={1} max={90} value={ignoringPoWindow} name="m_ignoring_po_window" style={mInput}
     onChange={e => { const v = Number(e.target.value); if (Number.isFinite(v) && v >= 1 && v <= 90) setIgnoringPoWindow(v) }} />
   </MField>
  </MobileControls>
  {loadingCostOfIgnoring ? (
   <div style={{ padding: 40, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
  ) : !costOfIgnoring ? null : costOfIgnoring.summary.skus_flagged === 0 ? (
   <div style={{ padding: '32px 8px', textAlign: 'center' }}>
    <div style={{ fontSize: 15, fontWeight: 600, color: C.green, marginBottom: 8 }}>{t('inventory.ignoring_none')}</div>
    <div style={{ fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('inventory.ignoring_none_desc', { from: costOfIgnoring.from_date, to: costOfIgnoring.to_date })}</div>
   </div>
  ) : (
   <>
    <div style={{ marginBottom: 12 }}>
     <MobileMetricGrid metrics={[
      { label: t('inventory.ignoring_kpi_flagged'), value: costOfIgnoring.summary.skus_flagged, color: C.text },
      { label: t('inventory.ignoring_kpi_ordered'), value: costOfIgnoring.summary.skus_ordered, color: C.green },
      { label: t('inventory.ignoring_kpi_stockout'), value: costOfIgnoring.summary.skus_likely_stockout, color: C.red },
      { label: t('inventory.ignoring_kpi_unclear'), value: costOfIgnoring.summary.skus_unclear, color: C.dim },
      { label: t('inventory.ignoring_kpi_lost_units'), value: costOfIgnoring.summary.total_estimated_lost_units == null ? '—' : fmtNum(costOfIgnoring.summary.total_estimated_lost_units), color: C.amber },
      { label: t('inventory.ignoring_kpi_lost_value'), value: costOfIgnoring.summary.total_estimated_lost_value == null ? t('inventory.ignoring_kpi_lost_value_none') : formatMoneyCompact(costOfIgnoring.summary.total_estimated_lost_value), color: costOfIgnoring.summary.total_estimated_lost_value == null ? C.dim : C.red },
     ]} />
    </div>
    {costOfIgnoring.summary.skus_with_lost_units_but_unknown_value > 0 && (
     <div style={{ padding: '10px 12px', borderRadius: 10, fontSize: 13, lineHeight: 1.45, background: 'color-mix(in srgb, var(--dim) 10%, transparent)', color: C.dim, marginBottom: 12 }}>{t('inventory.ignoring_unknown_value_note', { n: costOfIgnoring.summary.skus_with_lost_units_but_unknown_value })}</div>
    )}
    <MobileList ariaLabel={t('inventory.view_cost_of_ignoring')}>
     {costOfIgnoringPaged.rows.map(row => {
      const outcome = row.outcome === 'ordered'
       ? <span style={{ color: C.green, fontWeight: 600 }}>{t('inventory.ignoring_outcome_ordered')}{row.po_generated_at ? ` · ${new Date(row.po_generated_at).toLocaleDateString(lang === 'en' ? 'en-US' : 'es-CR')}` : ''}</span>
       : row.outcome === 'likely_stockout'
        ? <span style={{ color: C.red, fontWeight: 600 }}>{t('inventory.ignoring_outcome_stockout')}{row.partial_window ? ' *' : ''}</span>
        : <span style={{ color: C.dim, fontWeight: 600 }}>{t('inventory.ignoring_outcome_unclear')}</span>
      return (
       <MobileCard key={row.sku}
        title={skuDisplayName[row.sku] || row.sku}
        subtitle={`${t('inventory.ignoring_col_flagged_since')} ${row.first_flagged_on}`}
        status={{ label: signalLabel(t, row.latest_signal), tone: signalTone(row.latest_signal) }}
        value={row.lost_value != null
         ? <span style={{ color: C.red }}>{formatMoneyCompact(row.lost_value)}</span>
         : <span style={{ fontSize: 12, fontWeight: 500, color: C.dim }}>{row.lost_value_reason === 'sale_price_unknown' || row.lost_value_reason === 'no_demand_rate_recorded' ? t('inventory.ignoring_value_unknown_short') : '—'}</span>}
        valueCaption={row.lost_units != null ? `${row.lost_units} ${t('inventory.unit_und')}` : undefined}>
        <span style={{ fontSize: 12.5 }}>{outcome}</span>
        {row.days_out_of_stock != null && (
         <span style={{ display: 'block', fontSize: 12, color: C.muted, marginTop: 2 }}>{t('inventory.ignoring_col_days_out')}: {row.days_out_of_stock}</span>
        )}
        {row.partial_window && row.outcome === 'likely_stockout' && (
         <span style={{ display: 'block', fontSize: 12, color: C.dim, marginTop: 2, whiteSpace: 'normal' }}>* {t('inventory.ignoring_partial_window')}</span>
        )}
       </MobileCard>
      )
     })}
    </MobileList>
    <Pagination page={costOfIgnoringPaged.page} pageCount={costOfIgnoringPaged.pageCount} offset={costOfIgnoringPaged.offset} total={costOfIgnoringPaged.total} rowsOnPage={costOfIgnoringPaged.rows.length} onPage={setIgnoringPage} label="SKU" />
   </>
  )}
 </div>

 ) : viewMode === 'ignored' ? (
 /* ── Cost of ignoring (stability.md 19.4) ─────────────────────────
    Every SKU that carried an ordering signal in the window: did a PO
    follow, and — only when it did not AND a snapshot actually recorded
    stock at or below zero — what the missed units were worth. See
    getCostOfIgnoring / recommendation_reports.cost_of_ignoring. The
    conservatism in that endpoint is the whole point: `no_po_no_stockout_
    observed` reads as "we cannot show a cost", never as a silent zero. */
 <div style={{ padding: 16 }}>
  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
   <p style={{ margin: 0, fontSize: 12, color: C.dim, maxWidth: 620 }}>
    {t('inventory.ignoring_intro')}
   </p>
   <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: C.dim }}>
     {t('inventory.ignoring_from_label')}
     <input
      type="date" value={ignoringFromDate} max={ignoringToDate}
      onChange={e => e.target.value && setIgnoringFromDate(e.target.value)}
      style={{ ...inputS, width: 130 }}
      aria-label={t('inventory.ignoring_from_label')}
     />
    </label>
    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: C.dim }}>
     {t('inventory.ignoring_to_label')}
     <input
      type="date" value={ignoringToDate} min={ignoringFromDate} max={todayIso}
      onChange={e => e.target.value && setIgnoringToDate(e.target.value)}
      style={{ ...inputS, width: 130 }}
      aria-label={t('inventory.ignoring_to_label')}
     />
    </label>
    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: C.dim }}>
     {t('inventory.ignoring_po_window_label')}
     <input
      type="number" min={1} max={90} value={ignoringPoWindow}
      onChange={e => {
       const v = Number(e.target.value)
       if (Number.isFinite(v) && v >= 1 && v <= 90) setIgnoringPoWindow(v)
      }}
      style={{ ...inputS, width: 60 }}
      aria-label={t('inventory.ignoring_po_window_label')}
     />
    </label>
   </div>
  </div>

  {loadingCostOfIgnoring ? (
   <div style={{ padding: 48, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
  ) : !costOfIgnoring ? null : costOfIgnoring.summary.skus_flagged === 0 ? (
   <div style={{ padding: '40px 0', textAlign: 'center' }}>
    <div style={{ fontSize: 14, fontWeight: 600, color: C.green, marginBottom: 8 }}>
     {t('inventory.ignoring_none')}
    </div>
    <div style={{ fontSize: 13, color: C.dim }}>
     {t('inventory.ignoring_none_desc', { from: costOfIgnoring.from_date, to: costOfIgnoring.to_date })}
    </div>
   </div>
  ) : (
   <>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12, marginBottom: 12 }}>
     <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${C.text}` }}>
      <div style={{ fontSize: 22, fontWeight: 800, color: C.text }}>{costOfIgnoring.summary.skus_flagged}</div>
      <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{t('inventory.ignoring_kpi_flagged')}</div>
     </div>
     <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${C.green}` }}>
      <div style={{ fontSize: 22, fontWeight: 800, color: C.green }}>{costOfIgnoring.summary.skus_ordered}</div>
      <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{t('inventory.ignoring_kpi_ordered')}</div>
     </div>
     <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${C.red}` }}>
      <div style={{ fontSize: 22, fontWeight: 800, color: C.red }}>{costOfIgnoring.summary.skus_likely_stockout}</div>
      <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{t('inventory.ignoring_kpi_stockout')}</div>
     </div>
     <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${C.dim}` }}>
      <div style={{ fontSize: 22, fontWeight: 800, color: C.dim }}>{costOfIgnoring.summary.skus_unclear}</div>
      <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{t('inventory.ignoring_kpi_unclear')}</div>
     </div>
    </div>

    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16, maxWidth: 520 }}>
     <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${C.amber}` }}>
      <div style={{ fontSize: 20, fontWeight: 800, color: C.amber }}>
       {costOfIgnoring.summary.total_estimated_lost_units == null ? '—' : costOfIgnoring.summary.total_estimated_lost_units.toLocaleString()}
      </div>
      <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{t('inventory.ignoring_kpi_lost_units')}</div>
     </div>
     <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${C.red}` }}>
      <div style={{ fontSize: 20, fontWeight: 800, color: costOfIgnoring.summary.total_estimated_lost_value == null ? C.dim : C.red }}>
       {costOfIgnoring.summary.total_estimated_lost_value == null
        ? t('inventory.ignoring_kpi_lost_value_none')
        : formatMoneyCompact(costOfIgnoring.summary.total_estimated_lost_value)}
      </div>
      <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{t('inventory.ignoring_kpi_lost_value')}</div>
     </div>
    </div>

    {costOfIgnoring.summary.skus_with_lost_units_but_unknown_value > 0 && (
     <div style={{
      marginBottom: 16, padding: '10px 14px', borderRadius: 8, fontSize: 11.5,
      background: 'color-mix(in srgb, var(--dim) 10%, transparent)', color: C.dim,
     }}>
      {t('inventory.ignoring_unknown_value_note', { n: costOfIgnoring.summary.skus_with_lost_units_but_unknown_value })}
     </div>
    )}

    <div style={{ borderRadius: 10, border: `1px solid ${C.border}`, overflow: 'hidden' }}>
     <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
      <thead>
       <tr style={{ background: C.card }}>
        {[
         t('inventory.ignoring_col_product'), t('inventory.col_signal'),
         t('inventory.ignoring_col_flagged_since'), t('inventory.ignoring_col_outcome'),
         t('inventory.ignoring_col_days_out'), t('inventory.ignoring_col_lost_units'),
         t('inventory.ignoring_col_lost_value'),
        ].map(h => (
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
       {costOfIgnoringPaged.rows.map((row, i: number) => (
        <tr key={row.sku} style={{
         background: (costOfIgnoringPaged.offset + i) % 2 === 0 ? C.surface : C.card,
         borderBottom: `1px solid ${C.border}`,
        }}>
         <th scope="row" style={{ padding: '10px 12px', textAlign: 'left', fontWeight: 400, color: C.text }}>
          <div style={{ fontWeight: 600 }}>{skuDisplayName[row.sku] || row.sku}</div>
          <div style={{ fontSize: 10, color: C.dim, fontFamily: 'monospace' }}>{row.sku}</div>
         </th>
         <td style={{ padding: '10px 12px' }}><SignalBadge s={row.latest_signal as InventorySignal} /></td>
         <td style={{ padding: '10px 12px', color: C.muted, fontSize: 11 }}>{row.first_flagged_on}</td>
         <td style={{ padding: '10px 12px' }}>
          {row.outcome === 'ordered' ? (
           <Tooltip text={t('inventory.ignoring_outcome_ordered_detail', { date: row.po_generated_at ? new Date(row.po_generated_at).toLocaleDateString(lang === 'en' ? 'en-US' : 'es-CR') : '—' })}>
            <span style={{ color: C.green, fontWeight: 600 }}>{t('inventory.ignoring_outcome_ordered')}</span>
           </Tooltip>
          ) : row.outcome === 'likely_stockout' ? (
           <span style={{ color: C.red, fontWeight: 600 }}>
            {t('inventory.ignoring_outcome_stockout')}
            {row.partial_window && (
             <Tooltip text={t('inventory.ignoring_partial_window')}>
              <span style={{ marginLeft: 4, color: C.dim, fontWeight: 400 }}>*</span>
             </Tooltip>
            )}
           </span>
          ) : (
           <Tooltip text={t('inventory.ignoring_outcome_unclear_tooltip')}>
            <span style={{ color: C.dim, fontWeight: 600 }}>{t('inventory.ignoring_outcome_unclear')}</span>
           </Tooltip>
          )}
         </td>
         <td style={{ padding: '10px 12px', color: C.text }}>{row.days_out_of_stock ?? '—'}</td>
         <td style={{ padding: '10px 12px', color: C.text }}>{row.lost_units ?? '—'}</td>
         <td style={{ padding: '10px 12px', fontWeight: 700, color: row.lost_value != null ? C.red : C.dim }}>
          {row.lost_value != null
           ? formatMoneyCompact(row.lost_value)
           : row.lost_value_reason === 'sale_price_unknown' || row.lost_value_reason === 'no_demand_rate_recorded'
           ? <Tooltip text={t(`inventory.ignoring_value_unknown_reason_${row.lost_value_reason}`, { units: row.lost_units ?? 0 })}>
              <span>{t('inventory.ignoring_value_unknown_short')}</span>
             </Tooltip>
           : '—'}
         </td>
        </tr>
       ))}
      </tbody>
     </table>
    </div>

    <Pagination page={costOfIgnoringPaged.page} pageCount={costOfIgnoringPaged.pageCount} offset={costOfIgnoringPaged.offset} total={costOfIgnoringPaged.total} rowsOnPage={costOfIgnoringPaged.rows.length} onPage={setIgnoringPage} label="SKU" />
   </>
  )}
 </div>

 ) : (
 /* ── Full table ───────────────────────────────────────────── */
 /* The other landing view, and the one the skeleton table is shaped after:
    it fades in so the placeholder appears to turn into the rows. */
 <div className="page-enter" style={{ overflowX: 'auto' }}>
 <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
 <thead>
 <tr style={{ background: C.card }}>
 <th scope="col" style={{ padding: '9px 12px', width: 28, borderBottom: `1px solid ${C.border}` }}>
 <span className="sr-only">{tOr(t, 'inventory.col_expand', 'Show calculation')}</span>
 </th>
 <ThTip label={t('inventory.col_signal')} tip={t('inventory.tip_signal')} sortKey="signal" sort={sort} onSort={toggleSort} dataTour="inv.signal" />
 <ThTip label={t('inventory.col_sku_name')} tip={t('inventory.tip_sku_name')} sortKey="sku" sort={sort} onSort={toggleSort} />
 <ThTip label={t('inventory.col_stock')} tip={t('inventory.tip_stock')} sortKey="stock" sort={sort} onSort={toggleSort} />
 <ThTip label={t('inventory.col_trend')} tip={t('inventory.tip_trend')} />
 <ThTip label={`${t('inventory.wh_col_coverage')} (${coverageUnitShort(data?.coverage_unit, t)})`} tip={t('inventory.tip_days_coverage')} sortKey="coverage" sort={sort} onSort={toggleSort} dataTour="inv.coverage" />
 <ThTip label={t('inventory.col_demand_lt')} tip={t('inventory.tip_demand_lt')} sortKey="demand_lt" sort={sort} onSort={toggleSort} />
 <ThTip label={t('inventory.col_qty_to_order')} tip={t('inventory.tip_qty_to_order')} sortKey="qty" sort={sort} onSort={toggleSort} dataTour="inv.suggest" />
 <ThTip label={t('inventory.col_lead_time')} tip={t('inventory.tip_lead_time')} sortKey="lead_time" sort={sort} onSort={toggleSort} dataTour="inv.leadtime" />
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
 style={{ background: crit ? 'rgba(192,80,77,0.02)' : rowBg, borderLeft: `3px solid ${crit ? C.red : 'transparent'}`, transition: 'background 0.1s' }}
 onMouseEnter={e => (e.currentTarget.style.background = 'color-mix(in srgb, var(--accent) 4%, transparent)')}
 onMouseLeave={e => (e.currentTarget.style.background = crit ? 'rgba(192,80,77,0.02)' : rowBg)}
 >
 {/* Expand button */}
 <td style={{ padding: '10px 6px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 {item.calc_explanation && (
 <button
 /* First row only: a tour anchor has to be unique to be findable. */
 data-tour={idx === 0 ? 'inv.expand' : undefined}
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
 ? <span style={{ color: C.dim, fontSize: 11 }}>{notYetLabel(item, t)}</span>
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
  <button data-tour={idx === 0 ? 'inv.simulate' : undefined} onClick={() => setExpandedSku(isExpanded ? null : item.sku)} title={t('inventory.title_simulate_scenarios')}
   style={{ all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5, color: isExpanded ? C.indigo : C.dim, display: 'flex' }}
   onMouseEnter={e => (e.currentTarget.style.color = C.indigo)}
   onMouseLeave={e => (e.currentTarget.style.color = isExpanded ? C.indigo : C.dim)}>
   <Sliders size={13} />
  </button>
 )}
 <button data-tour={idx === 0 ? 'inv.edit' : undefined} onClick={() => startEdit(item)} title={t('inventory.title_edit')} style={{ all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5, color: C.dim, display: 'flex' }} onMouseEnter={e => (e.currentTarget.style.color = C.indigo)} onMouseLeave={e => (e.currentTarget.style.color = C.dim)}><Edit2 size={13} /></button>
 {item.has_stock && <button onClick={() => handleDelete(item.sku)} title={t('inventory.title_delete')} style={{ all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5, color: C.dim, display: 'flex' }} onMouseEnter={e => (e.currentTarget.style.color = C.red)} onMouseLeave={e => (e.currentTarget.style.color = C.dim)}><Trash2 size={13} /></button>}
 </div>
 </td>
 </tr>
 {/* Expanded explanation row — grows in place (see ExpandedCalcRow).
     Only the arrival is animated; collapsing is immediate, because the user
     already decided to close it. */}
 {isExpanded && item.calc_explanation && (
 <ExpandedCalcRow item={item} background={crit ? 'rgba(192,80,77,0.01)' : rowBg} />
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
 aria-expanded={showEvents}
 style={{ all: 'unset', cursor: 'pointer', width: '100%', display: 'flex', alignItems: 'center', gap: 10, padding: narrow ? '12px 14px' : '14px 20px', boxSizing: 'border-box', minHeight: narrow ? 52 : undefined }}
 >
 <Calendar size={14} color={C.indigo} />
 <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{t('inventory.events_section_title')}</span>
 {upcomingAlerts.length > 0 && <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: 'rgba(183,121,31,0.1)', color: C.amber }}>{upcomingAlerts.length} {upcomingAlerts.length > 1 ? t('inventory.upcoming_plural') : t('inventory.upcoming_singular')}</span>}
 <ChevronDown size={13} color={C.dim} style={{ transform: showEvents ? 'rotate(180deg)' : undefined, transition: 'transform 0.2s' }} />
 </button>
 {showEvents && (
 <div style={{ padding: narrow ? '0 12px 14px' : '0 20px 20px', borderTop: `1px solid ${C.border}` }}>
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
  warehouses={warehouses.map(w => w.name)}
  // The warehouse tab that is open is the one the buyer is looking at, so it
  // is the one the modal opens on. On "Todas" it falls back to the default.
  defaultWarehouse={selectedWarehouse}
  onClose={() => setShowShrinkageModal(false)}
  onSaved={() => { if (sessionId) load(sessionId) }}
 />
 )}

 {/* Phone: the SKU detail sheet and the product editor. The desktop table
     expands a row and edits in place; neither fits a 360px screen. */}
 {narrow && (() => {
  const detail = detailSku ? data?.items.find(i => i.sku === detailSku) ?? null : null
  return (
   <MobileSkuSheet
    item={detail && !editId ? detail : null}
    coverageUnit={data?.coverage_unit}
    onClose={() => setDetailSku(null)}
    canEdit={canEdit}
    onEdit={i => startEdit(i)}
    // One sheet at a time: the confirmation replaces the detail.
    onDelete={sku => { setDetailSku(null); handleDelete(sku) }}
    qty={detail ? effectiveQty(detail) : 0}
    onQtyChange={(sku, n) => setEditedQty(prev => ({ ...prev, [sku]: n }))}
    notYet={i => notYetLabel(i, t)}
    calc={detail?.calc_explanation ? (
     <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      <CalcExplainer exp={detail.calc_explanation} moq={detail.moq} />
      <WhyChangedPanel sku={detail.sku} />
      <PlanningValues item={detail} />
      <SimulatorPanel item={detail} />
     </div>
    ) : undefined}
   />
  )
 })()}
 {narrow && (
  <MobileEditSheet
   sku={editId}
   state={editState}
   setState={fn => setEditState(prev => fn(prev))}
   suppliers={suppliers}
   onSave={() => editId && commitEdit(editId)}
   onCancel={cancelEdit}
   saving={saving}
  />
 )}

 {deleteTarget && narrow && (
  <BottomSheet open onClose={() => setDeleteTarget(null)}
   title={`${t('inventory.confirm_delete_prefix')} ${deleteTarget}?`}
   footer={(
    <div style={{ display: 'flex', gap: 8, width: '100%' }}>
     <button type="button" className="mobile-btn mobile-btn-secondary" onClick={() => setDeleteTarget(null)}>{t('common.cancel')}</button>
     <button type="button" className="mobile-btn mobile-btn-danger" onClick={() => { void confirmDelete() }}>{t('inventory.btn_delete_confirm')}</button>
    </div>
   )}>
   <p style={{ margin: 0, fontSize: 14, color: C.dim, lineHeight: 1.5 }}>{t('inventory.confirm_delete_hint')}</p>
  </BottomSheet>
 )}

 {deleteTarget && !narrow && (
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
 { signal: t('inventory.signal_order_now'), desc: signalRules
 ? `${t('inventory.legend_order_now')} (${t('inventory.thresholds_legend_order_now', { x: signalRules.effective.order_now_factor.toLocaleString(localeFor(lang)) })})`
 : t('inventory.legend_order_now') },
 { signal: t('inventory.signal_order_soon'), desc: t('inventory.legend_order_soon') },
 { signal: t('inventory.signal_ok'), desc: t('inventory.legend_ok') },
 { signal: t('inventory.signal_overstock'), desc: signalRules
 ? `${t('inventory.legend_overstock')} (${t('inventory.thresholds_legend_overstock', { x: signalRules.effective.overstock_factor.toLocaleString(localeFor(lang)) })})`
 : t('inventory.legend_overstock') },
 { signal: t('inventory.signal_sin_datos'), desc: t('inventory.legend_sin_datos') },
 ].map(({ signal, desc }) => <span key={signal}><strong>{signal}</strong> — {desc}</span>)}
 {signalRules && (
 <Link href="/configurar-inventario#reglas-semaforo" style={{ color: C.indigo, textDecoration: 'none', ...(narrow ? { minHeight: 44, display: 'inline-flex', alignItems: 'center', fontSize: 13 } : {}) }}>
 {signalRules.overrides.length === 0
 ? t('inventory.thresholds_legend_adjust')
 : signalRules.overrides.length === 1
 ? t('inventory.thresholds_legend_adjust_with_override_one')
 : t('inventory.thresholds_legend_adjust_with_overrides', { count: signalRules.overrides.length })}
 </Link>
 )}
 </div>
 )}
 </div>
 )
}
