'use client'
import { useState, useEffect, useRef, useMemo } from 'react'
import dynamic from 'next/dynamic'
import {
  getSessions, getMetrics, getResults, getQuality,
  exportConfig, detectDrift, generateReport, downloadReportBlob,
} from '@/lib/api'
import type { DriftReport } from '@/lib/api'
import type { SessionInfo, MetricRow, QualityReport } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import { useBusinessProfile } from '@/contexts/BusinessProfileContext'
import {
  FileText, Download, TrendingUp, BarChart2,
  Activity, Package, AlertTriangle,
  ShieldAlert, UploadCloud, CheckCircle2,
  ChevronDown, Award, Layers, Target, Database,
  X,
} from 'lucide-react'

const ReactECharts = dynamic(() => import('echarts-for-react'), { ssr: false })

// ── Palette ───────────────────────────────────────────────────────────────────
const C = {
  bg:      'var(--bg)',
  surface: 'var(--surface)',
  card:    'var(--surface-2)',
  border:  'var(--border)',
  border2: 'var(--border-strong)',
  text:    'var(--text)',
  muted:   'var(--muted)',
  dim:     'var(--dim)',
  accent:  'var(--accent)',
  indigo:  '#818cf8',
  green:   '#22c55e',
  amber:   '#f59e0b',
  red:     '#ef4444',
  orange:  '#f97316',
  cyan:    '#06b6d4',
  slate:   '#64748b',
}

const TYPE_COLOR: Record<string, string> = {
  stable: C.green, seasonal: C.indigo, volatile: C.amber,
  intermittent: C.orange, short: C.cyan,
}
const PSI_COLOR: Record<string, string> = {
  none: C.green, low: C.amber, moderate: C.orange, significant: C.red,
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function pct(n: number) { return `${(n * 100).toFixed(1)}%` }
function fmt(n: number | null, d = 3) { return n === null ? '—' : n.toFixed(d) }

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

// ── KPI card ──────────────────────────────────────────────────────────────────
function KpiCard({ icon: Icon, label, value, sub, color }: {
  icon: React.ElementType; label: string; value: React.ReactNode
  sub?: string; color: string
}) {
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 10, padding: '14px 18px',
      display: 'flex', alignItems: 'flex-start', gap: 12,
    }}>
      <div style={{
        width: 36, height: 36, borderRadius: 9,
        background: color + '18', display: 'flex',
        alignItems: 'center', justifyContent: 'center', flexShrink: 0,
      }}>
        <Icon size={16} color={color} />
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 20, fontWeight: 700, color: C.text, lineHeight: 1.1 }}>{value}</div>
        <div style={{ fontSize: 11, color: C.dim, marginTop: 3 }}>{label}</div>
        {sub && <div style={{ fontSize: 10, color: C.dim, marginTop: 1, opacity: 0.7 }}>{sub}</div>}
      </div>
    </div>
  )
}

// ── Section card ──────────────────────────────────────────────────────────────
function Card({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 12, overflow: 'hidden',
    }}>
      <div style={{
        padding: '14px 20px', borderBottom: `1px solid ${C.border}`,
        display: 'flex', flexDirection: 'column', gap: 2,
      }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{title}</div>
        {sub && <div style={{ fontSize: 11, color: C.dim }}>{sub}</div>}
      </div>
      {children}
    </div>
  )
}

// ── Tab bar ───────────────────────────────────────────────────────────────────
function TabBar({ tabs, active, onChange }: {
  tabs: { id: string; label: string; icon: React.ElementType }[]
  active: string
  onChange: (t: string) => void
}) {
  return (
    <div style={{ display: 'flex', gap: 0, borderBottom: `1px solid ${C.border}` }}>
      {tabs.map(({ id, label, icon: Icon }) => {
        const isActive = id === active
        return (
          <button key={id} onClick={() => onChange(id)} style={{
            all: 'unset', cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: 7,
            padding: '12px 18px', fontSize: 12, fontWeight: isActive ? 600 : 500,
            color: isActive ? C.indigo : C.dim,
            borderBottom: `2px solid ${isActive ? C.indigo : 'transparent'}`,
            marginBottom: -1, transition: 'all 0.15s',
            background: isActive ? C.indigo + '08' : 'transparent',
          }}>
            <Icon size={13} />
            {label}
          </button>
        )
      })}
    </div>
  )
}

// ── Mini bar (horizontal) ─────────────────────────────────────────────────────
function Bar({ value, max, color }: { value: number; max: number; color: string }) {
  const w = max > 0 ? Math.min((value / max) * 100, 100) : 0
  return (
    <div style={{ height: 5, background: C.border, borderRadius: 3, flex: 1 }}>
      <div style={{ height: '100%', width: `${w}%`, background: color, borderRadius: 3, transition: 'width 0.5s' }} />
    </div>
  )
}

// ── Model comparison EChart ───────────────────────────────────────────────────
function ModelBarChart({ rows }: { rows: MetricRow[] }) {
  const data = useMemo(() => {
    const byModel: Record<string, number[]> = {}
    rows.filter(r => r.mae !== null).forEach(r => {
      if (!byModel[r.model]) byModel[r.model] = []
      byModel[r.model].push(r.mae!)
    })
    return Object.entries(byModel)
      .map(([model, maes]) => ({
        model,
        avgMAE: maes.reduce((a, b) => a + b, 0) / maes.length,
        n: maes.length,
      }))
      .sort((a, b) => a.avgMAE - b.avgMAE)
  }, [rows])

  if (!data.length) return (
    <div style={{ padding: '40px 20px', textAlign: 'center', color: C.dim, fontSize: 13 }}>
      No model metrics available.
    </div>
  )

  const models  = data.map(d => d.model)
  const values  = data.map(d => parseFloat(d.avgMAE.toFixed(3)))
  const colors  = data.map((_, i) => i === 0 ? C.indigo : C.slate)

  const option = {
    backgroundColor: 'transparent',
    grid: { left: 110, right: 30, top: 16, bottom: 28 },
    xAxis: {
      type: 'value',
      axisLabel: { color: C.slate, fontSize: 10 },
      splitLine: { lineStyle: { color: '#334155', type: 'dashed' } },
    },
    yAxis: {
      type: 'category',
      data: models,
      inverse: false,
      axisLabel: {
        color: C.muted, fontSize: 11, fontFamily: 'monospace',
        formatter: (v: string) => v.length > 12 ? v.slice(0, 11) + '…' : v,
      },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    series: [{
      type: 'bar',
      data: values.map((v, i) => ({ value: v, itemStyle: { color: colors[i], borderRadius: [0, 4, 4, 0] } })),
      barMaxWidth: 32,
      label: {
        show: true, position: 'right',
        color: C.muted, fontSize: 10, fontFamily: 'monospace',
        formatter: (p: { value: number }) => p.value.toFixed(3),
      },
    }],
    tooltip: {
      trigger: 'axis',
      formatter: (p: { name: string; value: number }[]) => {
        const d = data.find(x => x.model === p[0]?.name)
        return `<b>${p[0]?.name}</b><br/>Avg MAE: <b>${p[0]?.value.toFixed(3)}</b><br/>SKUs: ${d?.n ?? '?'}`
      },
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
    },
  }

  return (
    <div style={{ padding: '8px 0 0' }}>
      <ReactECharts
        option={option}
        style={{ height: Math.max(180, data.length * 46 + 60) }}
        theme="dark"
        opts={{ renderer: 'canvas' }}
        notMerge
      />
    </div>
  )
}

// ── Quality donut EChart ──────────────────────────────────────────────────────
function QualityDonut({ byType, total }: { byType: Record<string, number>; total: number }) {
  const pieData = Object.entries(byType).sort((a, b) => b[1] - a[1]).map(([type, count]) => ({
    name: type, value: count,
    itemStyle: { color: TYPE_COLOR[type] ?? C.slate },
  }))

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      formatter: (p: { name: string; value: number; percent: number }) =>
        `<b>${p.name}</b>: ${p.value} SKUs (${p.percent.toFixed(1)}%)`,
      backgroundColor: '#1e293b', borderColor: '#334155',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
    },
    legend: {
      orient: 'vertical', right: 16, top: 'center',
      textStyle: { color: C.muted, fontSize: 11 },
      itemWidth: 10, itemHeight: 10,
    },
    series: [{
      type: 'pie', radius: ['44%', '70%'],
      center: ['35%', '50%'],
      avoidLabelOverlap: true,
      label: {
        show: true, position: 'inside',
        formatter: (p: { percent: number }) => p.percent >= 10 ? `${p.percent.toFixed(0)}%` : '',
        fontSize: 10, fontWeight: 600, color: '#fff',
      },
      data: pieData,
    }],
    graphic: [{
      type: 'text', left: 'center', top: 'center',
      style: { text: `${total}\nSKUs`, textAlign: 'center', fill: C.muted, fontSize: 12, lineHeight: 18 },
    }],
  }

  return (
    <ReactECharts
      option={option}
      style={{ height: 220 }}
      theme="dark"
      opts={{ renderer: 'canvas' }}
      notMerge
    />
  )
}

// ── Business summary card ─────────────────────────────────────────────────────
function ForecastSummaryCard({ rows }: { rows: MetricRow[] }) {
  const wapeRows  = rows.filter(r => r.wape !== null)
  const avgWape   = wapeRows.length
    ? wapeRows.reduce((acc, r) => acc + r.wape!, 0) / wapeRows.length
    : 0
  const accuracyPct = Math.round((1 - avgWape) * 100)

  const skuWape: Record<string, number[]> = {}
  wapeRows.forEach(r => {
    if (!r.sku) return
    if (!skuWape[r.sku]) skuWape[r.sku] = []
    skuWape[r.sku].push(r.wape!)
  })
  const skuAvgWape = Object.entries(skuWape).map(([sku, wapes]) => ({
    sku,
    avg: wapes.reduce((a, b) => a + b, 0) / wapes.length,
  }))

  const goodSkus = skuAvgWape.filter(s => s.avg < 0.20).length
  const fairSkus = skuAvgWape.filter(s => s.avg >= 0.20 && s.avg <= 0.35).length
  const poorSkus = skuAvgWape.filter(s => s.avg > 0.35).length
  const totalSkus = new Set(rows.filter(r => r.sku).map(r => r.sku)).size

  const accentColor = accuracyPct >= 85 ? C.green : accuracyPct >= 70 ? C.amber : C.red

  const summaryText = poorSkus > 0
    ? `${poorSkus} producto${poorSkus !== 1 ? 's' : ''} muestran alta variabilidad — evita basar pedidos importantes solo en el forecast de ${poorSkus !== 1 ? 'estos SKUs' : 'este SKU'}.`
    : 'El sistema tiene suficiente precisión para tomar decisiones de compra e inventario con confianza.'

  if (!rows.length) return null

  return (
    <div style={{
      margin: '16px 16px 0',
      padding: '16px 20px',
      borderRadius: 10,
      background: accentColor + '08',
      border: `1px solid ${accentColor}28`,
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: C.text, marginBottom: 14 }}>
        Precisión general del forecast
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 14 }}>
        {[
          { value: `${accuracyPct}%`, label: 'Precisión promedio', color: accentColor },
          { value: goodSkus,          label: 'Pronóstico confiable', color: C.green },
          { value: fairSkus,          label: 'Con margen de mejora', color: C.amber },
          { value: poorSkus,          label: 'Revisar manualmente',   color: C.red   },
        ].map(({ value, label, color }) => (
          <div key={label} style={{
            background: C.surface, borderRadius: 8,
            padding: '10px 12px', border: `1px solid ${C.border}`,
            textAlign: 'center',
          }}>
            <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
            <div style={{ fontSize: 10, color: C.dim, marginTop: 3, lineHeight: 1.4 }}>{label}</div>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 12, color: C.muted, lineHeight: 1.7 }}>
        {wapeRows.length > 0
          ? `El forecast tiene ${accuracyPct}% de precisión promedio. ${goodSkus > 0 ? `${goodSkus} producto${goodSkus !== 1 ? 's tienen' : ' tiene'} pronósticos muy confiables.` : ''} ${summaryText}`
          : 'No hay métricas de precisión disponibles para esta sesión.'
        }
      </div>
      {totalSkus > 0 && (
        <div style={{ fontSize: 10, color: C.dim, marginTop: 6, opacity: 0.7 }}>
          Basado en {totalSkus} SKU{totalSkus !== 1 ? 's' : ''} · umbral confiable: WAPE &lt; 20%
        </div>
      )}
    </div>
  )
}

// ── Model performance tab ─────────────────────────────────────────────────────
function ModelPerformanceTab({ metrics }: { metrics: MetricRow[] }) {
  const [sortCol, setSortCol] = useState<keyof MetricRow>('wape')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [modelFilter, setModelFilter] = useState('')
  const [showTechnical, setShowTechnical] = useState(false)
  const { advancedMode } = useBusinessProfile()

  const byModel = useMemo(() => {
    const map: Record<string, { maes: number[]; wapes: number[] }> = {}
    metrics.filter(r => r.mae !== null).forEach(r => {
      if (!map[r.model]) map[r.model] = { maes: [], wapes: [] }
      map[r.model].maes.push(r.mae!)
      if (r.wape !== null) map[r.model].wapes.push(r.wape)
    })
    return Object.entries(map).map(([model, { maes, wapes }]) => ({
      model,
      avgMAE:  maes.reduce((a, b) => a + b, 0) / maes.length,
      avgWAPE: wapes.length ? wapes.reduce((a, b) => a + b, 0) / wapes.length : null,
      n: maes.length,
    })).sort((a, b) => a.avgMAE - b.avgMAE)
  }, [metrics])

  const models   = Array.from(new Set(metrics.map(r => r.model))).sort()
  const filtered = metrics.filter(r => !modelFilter || r.model === modelFilter)
  const sorted   = [...filtered].sort((a, b) => {
    const av = a[sortCol], bv = b[sortCol]
    if (av == null && bv == null) return 0
    if (av == null) return 1
    if (bv == null) return -1
    const cmp = typeof av === 'number' && typeof bv === 'number'
      ? av - bv : String(av).localeCompare(String(bv))
    return sortDir === 'asc' ? cmp : -cmp
  })

  function toggle(col: keyof MetricRow) {
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortCol(col); setSortDir('asc') }
  }

  const Hdr = ({ col, label }: { col: keyof MetricRow; label: string }) => (
    <th onClick={() => toggle(col)} style={{ cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}>
      {label} {sortCol === col ? (sortDir === 'asc' ? '↑' : '↓') : ''}
    </th>
  )

  if (!metrics.length) return (
    <div style={{ padding: '48px 20px', textAlign: 'center', color: C.dim, fontSize: 13 }}>
      No training metrics available.
    </div>
  )

  const technicalSection = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      {/* Chart */}
      <div style={{ padding: '0 16px 8px', borderBottom: `1px solid ${C.border}` }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: C.dim, padding: '12px 0 4px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Average MAE by Model
        </div>
        <ModelBarChart rows={metrics} />
      </div>

      {/* Leaderboard strip */}
      <div style={{ padding: '12px 16px', borderBottom: `1px solid ${C.border}` }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: C.dim, marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Model Leaderboard
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '140px 1fr 80px 80px 50px', gap: 10, padding: '6px 0', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 600, color: C.dim, textTransform: 'uppercase' }}>
          <span>Model</span><span>Avg MAE</span><span>Avg WAPE</span><span>Rank</span><span>SKUs</span>
        </div>
        {byModel.map((s, i) => {
          const maxMAE = byModel[byModel.length - 1]?.avgMAE ?? 1
          return (
            <div key={s.model} style={{
              display: 'grid', gridTemplateColumns: '140px 1fr 80px 80px 50px',
              gap: 10, padding: '9px 0', alignItems: 'center',
              borderBottom: `1px solid ${C.border}`,
              background: i === 0 ? C.indigo + '08' : 'transparent',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {i === 0 && (
                  <span style={{ fontSize: 9, color: C.indigo, background: C.indigo + '20', borderRadius: 3, padding: '1px 5px', fontWeight: 700 }}>
                    BEST
                  </span>
                )}
                <span style={{ fontSize: 12, fontWeight: 500, fontFamily: 'monospace', color: C.text }}>{s.model}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Bar value={s.avgMAE} max={maxMAE} color={i === 0 ? C.indigo : C.slate} />
                <span style={{ fontSize: 12, fontFamily: 'monospace', flexShrink: 0, color: C.muted }}>{fmt(s.avgMAE)}</span>
              </div>
              <span style={{ fontSize: 12, color: C.muted }}>{s.avgWAPE !== null ? pct(s.avgWAPE) : '—'}</span>
              <span style={{ fontSize: 12, color: C.dim }}>#{i + 1}</span>
              <span style={{ fontSize: 12, color: C.dim }}>{s.n}</span>
            </div>
          )
        })}
      </div>

      {/* Detailed table */}
      <div style={{ padding: '10px 16px', borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 11, color: C.dim }}>Filter:</span>
        <select
          value={modelFilter}
          onChange={e => setModelFilter(e.target.value)}
          style={{
            background: C.card, border: `1px solid ${C.border}`,
            borderRadius: 6, color: C.text, fontSize: 12,
            padding: '4px 8px', outline: 'none', cursor: 'pointer',
          }}
        >
          <option value="">All models ({metrics.length} rows)</option>
          {models.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>
      <div style={{ overflowX: 'auto', maxHeight: 360, overflowY: 'auto' }}>
        <table className="data-table">
          <thead>
            <tr>
              <Hdr col="model" label="Model" />
              <Hdr col="sku"   label="SKU" />
              <Hdr col="mae"   label="MAE" />
              <Hdr col="rmse"  label="RMSE" />
              <Hdr col="wape"  label="WAPE" />
              <Hdr col="bias"  label="Bias" />
              <th>Folds</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => (
              <tr key={i} style={{ background: i === 0 && !modelFilter ? C.green + '06' : undefined }}>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    {i === 0 && !modelFilter && (
                      <span style={{ fontSize: 9, color: C.green, fontWeight: 700 }}>BEST</span>
                    )}
                    <span style={{ fontFamily: 'monospace', fontWeight: 500 }}>{r.model}</span>
                  </div>
                </td>
                <td style={{ color: C.muted, fontFamily: 'monospace', fontSize: 11 }}>{r.sku ?? '—'}</td>
                <td style={{ fontFamily: 'monospace' }}>{fmt(r.mae)}</td>
                <td style={{ fontFamily: 'monospace' }}>{fmt(r.rmse)}</td>
                <td style={{
                  color: r.wape !== null && r.wape < 0.2 ? C.green : r.wape !== null && r.wape < 0.4 ? C.amber : C.text,
                  fontFamily: 'monospace',
                }}>
                  {r.wape !== null ? pct(r.wape) : '—'}
                </td>
                <td style={{ fontFamily: 'monospace', color: C.muted }}>{fmt(r.bias)}</td>
                <td style={{ color: C.dim }}>{r.n_folds ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      {/* Business summary card */}
      <ForecastSummaryCard rows={metrics} />

      {/* Technical toggle / section */}
      <div style={{ margin: '12px 16px 0' }}>
        {advancedMode ? (
          technicalSection
        ) : (
          <>
            <button
              onClick={() => setShowTechnical(v => !v)}
              style={{
                all: 'unset', cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 6,
                fontSize: 12, color: C.dim,
                padding: '8px 0', width: '100%',
                borderTop: `1px solid ${C.border}`,
              }}
            >
              <span style={{ fontSize: 10 }}>{showTechnical ? '▲' : '▼'}</span>
              {showTechnical ? 'Ocultar' : 'Ver'} comparativa técnica de modelos
            </button>
            {showTechnical && technicalSection}
          </>
        )}
      </div>
    </div>
  )
}

// ── Data quality tab ──────────────────────────────────────────────────────────
function DataQualityTab({ quality }: { quality: QualityReport }) {
  const entries = Object.entries(quality)
  if (!entries.length) return (
    <div style={{ padding: '48px 20px', textAlign: 'center', color: C.dim, fontSize: 13 }}>
      No quality data available.
    </div>
  )

  const byType: Record<string, number> = {}
  const scores = entries.map(([, q]) => {
    byType[q.series_type] = (byType[q.series_type] ?? 0) + 1
    return q.quality_score
  })
  const avgScore = scores.reduce((a, b) => a + b, 0) / scores.length
  const invalid  = entries.filter(([, q]) => !q.is_valid).length
  const warned   = entries.filter(([, q]) => q.warnings.length > 0).length
  const avgColor = avgScore > 0.7 ? C.green : avgScore > 0.4 ? C.amber : C.red

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      {/* KPI strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, padding: 16, borderBottom: `1px solid ${C.border}` }}>
        {[
          { label: 'Total SKUs',    value: entries.length, color: C.indigo },
          { label: 'Avg Quality',   value: pct(avgScore),  color: avgColor },
          { label: 'With Warnings', value: warned,         color: warned > 0 ? C.amber : C.green },
          { label: 'Invalid',       value: invalid,        color: invalid > 0 ? C.red : C.green },
        ].map(({ label, value, color }) => (
          <div key={label} style={{
            background: C.card, borderRadius: 8, padding: '12px 14px',
            border: `1px solid ${C.border}`, textAlign: 'center',
          }}>
            <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
            <div style={{ fontSize: 11, color: C.dim, marginTop: 3 }}>{label}</div>
          </div>
        ))}
      </div>

      {/* Donut + type breakdown */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0, borderBottom: `1px solid ${C.border}` }}>
        <div style={{ padding: '16px 20px', borderRight: `1px solid ${C.border}` }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: C.dim, marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Series Type Mix
          </div>
          <QualityDonut byType={byType} total={entries.length} />
        </div>
        <div style={{ padding: '16px 20px' }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: C.dim, marginBottom: 12, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Type Breakdown
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {Object.entries(byType).sort((a, b) => b[1] - a[1]).map(([type, count]) => {
              const color = TYPE_COLOR[type] ?? C.slate
              return (
                <div key={type} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{
                    fontSize: 10, fontWeight: 600, color,
                    background: color + '18', borderRadius: 4, padding: '2px 8px',
                    width: 88, textAlign: 'center', flexShrink: 0,
                  }}>
                    {type}
                  </span>
                  <Bar value={count} max={entries.length} color={color} />
                  <span style={{ fontSize: 12, color: C.dim, minWidth: 24, textAlign: 'right' }}>{count}</span>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* Flagged SKUs */}
      {(invalid > 0 || warned > 0) && (
        <div style={{ padding: '16px 20px' }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: C.dim, marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Flagged SKUs
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 240, overflowY: 'auto' }}>
            {entries.filter(([, q]) => !q.is_valid || q.warnings.length).map(([sku, q]) => (
              <div key={sku} style={{
                display: 'flex', alignItems: 'flex-start', gap: 10,
                padding: '9px 12px', borderRadius: 8,
                background: C.card, border: `1px solid ${C.border}`,
              }}>
                <AlertTriangle size={13} color={!q.is_valid ? C.red : C.amber} style={{ flexShrink: 0, marginTop: 1 }} />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, color: C.text, fontFamily: 'monospace' }}>{sku}</div>
                  {q.warnings.map((w, i) => (
                    <div key={i} style={{ fontSize: 11, color: C.dim, marginTop: 2 }}>{w}</div>
                  ))}
                </div>
                <span style={{
                  fontSize: 10, fontWeight: 600, borderRadius: 4, padding: '2px 8px',
                  background: (!q.is_valid ? C.red : C.amber) + '18',
                  color: !q.is_valid ? C.red : C.amber,
                }}>
                  {pct(q.quality_score)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Export tab ────────────────────────────────────────────────────────────────
function ExportTab({ sessionId, metrics, report }: {
  sessionId: string
  metrics: MetricRow[]
  report: Record<string, unknown>
}) {
  const cancelRef = useRef(false)
  const [exporting, setExporting] = useState<string | null>(null)
  const [exportMsg, setExportMsg] = useState<string | null>(null)
  const [exportErr, setExportErr] = useState<string | null>(null)

  useEffect(() => { return () => { cancelRef.current = true } }, [])

  function downloadJSON(data: unknown, name: string) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href = url; a.download = name; a.click()
    URL.revokeObjectURL(url)
  }

  function downloadCSV(rows: MetricRow[], name: string) {
    const headers = ['model', 'type', 'sku', 'mae', 'rmse', 'wape', 'bias', 'n_folds']
    const lines = [
      headers.join(','),
      ...rows.map(r => headers.map(h => r[h as keyof MetricRow] ?? '').join(',')),
    ]
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href = url; a.download = name; a.click()
    URL.revokeObjectURL(url)
  }

  async function handleBinaryReport(format: 'excel' | 'pdf') {
    const id = `report_${format}`
    setExporting(id); setExportMsg('Generating report…'); setExportErr(null)
    try {
      await generateReport(sessionId, 'operational', [format])
      let attempts = 0
      while (attempts < 15) {
        await new Promise(r => setTimeout(r, 2000))
        if (cancelRef.current) return
        attempts++
        setExportMsg(`Generating report… (${attempts * 2}s)`)
        try {
          await downloadReportBlob(sessionId, format)
          setExportMsg(null); return
        } catch { /* not ready */ }
      }
      setExportMsg(null)
      setExportErr('Report generation timed out — try again')
    } catch (e: unknown) {
      setExportMsg(null)
      setExportErr(e instanceof Error ? e.message : 'Export failed')
    } finally {
      if (!cancelRef.current) setExporting(null)
    }
  }

  async function handleExport(type: string) {
    if (type === 'report_excel') { await handleBinaryReport('excel'); return }
    if (type === 'report_pdf')   { await handleBinaryReport('pdf');   return }
    setExporting(type)
    try {
      if (type === 'metrics_csv') downloadCSV(metrics, `metrics_${sessionId.slice(0, 8)}.csv`)
      if (type === 'report_json') downloadJSON(report, `report_${sessionId.slice(0, 8)}.json`)
      if (type === 'config_json') {
        const cfg = await exportConfig(sessionId)
        downloadJSON(cfg, `config_${sessionId.slice(0, 8)}.json`)
      }
    } finally { setExporting(null) }
  }

  const exports = [
    { id: 'metrics_csv',  label: 'Metrics CSV',    desc: 'All model × SKU accuracy metrics',     color: C.indigo,  disabled: !metrics.length },
    { id: 'report_excel', label: 'Excel Report',   desc: 'Full operational report (.xlsx)',       color: C.green,   disabled: false },
    { id: 'report_pdf',   label: 'PDF Report',     desc: 'Formatted PDF for stakeholders',        color: C.orange,  disabled: false },
    { id: 'report_json',  label: 'Report JSON',    desc: 'Raw session results from the engine',   color: C.slate,   disabled: !Object.keys(report).length },
    { id: 'config_json',  label: 'Session Config', desc: 'Reproducible configuration export',    color: C.cyan,    disabled: false },
  ]

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {exportMsg && (
        <div style={{
          padding: '10px 14px', borderRadius: 8, fontSize: 12,
          background: C.indigo + '12', border: `1px solid ${C.indigo}30`,
          color: C.indigo, display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <Spinner size={12} />{exportMsg}
        </div>
      )}
      {exportErr && (
        <div style={{
          padding: '10px 14px', borderRadius: 8, fontSize: 12,
          background: C.red + '10', border: `1px solid ${C.red}28`,
          color: C.red, display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <AlertTriangle size={12} />{exportErr}
          <button onClick={() => setExportErr(null)} style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto' }}>
            <X size={13} />
          </button>
        </div>
      )}
      {exports.map(({ id, label, desc, color, disabled }) => (
        <div key={id} style={{
          display: 'flex', alignItems: 'center', gap: 12,
          padding: '13px 16px', borderRadius: 9,
          background: C.card, border: `1px solid ${C.border}`,
          opacity: disabled ? 0.45 : 1,
        }}>
          <div style={{
            width: 34, height: 34, borderRadius: 8,
            background: color + '18', display: 'flex',
            alignItems: 'center', justifyContent: 'center', flexShrink: 0,
          }}>
            <Download size={14} color={color} />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 500, color: C.text }}>{label}</div>
            <div style={{ fontSize: 11, color: C.dim, marginTop: 2 }}>{desc}</div>
          </div>
          <button
            onClick={() => handleExport(id)}
            disabled={disabled || exporting !== null}
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '7px 14px', borderRadius: 7, border: `1px solid ${color}40`,
              background: color + '10', color, cursor: disabled || exporting ? 'default' : 'pointer',
              fontSize: 12, fontWeight: 500, opacity: exporting && exporting !== id ? 0.5 : 1,
            }}
          >
            {exporting === id ? <Spinner size={12} /> : <Download size={12} />}
            Export
          </button>
        </div>
      ))}
    </div>
  )
}

// ── Drift tab ─────────────────────────────────────────────────────────────────
function DriftTab({ sessionId }: { sessionId: string }) {
  const [dragging,  setDragging]  = useState(false)
  const [loading,   setLoading]   = useState(false)
  const [report,    setReport]    = useState<DriftReport | null>(null)
  const [error,     setError]     = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  async function runDrift(file: File) {
    setLoading(true); setError(null); setReport(null)
    const fd = new FormData()
    fd.append('file', file)
    try {
      setReport(await detectDrift(sessionId, fd))
    } catch (e: unknown) {
      const raw = e instanceof Error ? e.message : 'Drift detection failed'
      const lower = raw.toLowerCase()
      setError(lower.includes('format') || lower.includes('type')
        ? 'Unsupported file format — use CSV or Parquet'
        : lower.includes('size') || lower.includes('large') ? 'File too large' : raw)
    } finally {
      setLoading(false)
    }
  }

  const featureRows = report
    ? Object.entries(report.feature_drift).sort((a, b) => b[1].psi - a[1].psi)
    : []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      {/* Drop zone */}
      <div style={{ padding: 20, borderBottom: `1px solid ${C.border}` }}>
        {/* Explanatory paragraph */}
        <div style={{
          padding: '12px 16px', marginBottom: 16, borderRadius: 8,
          background: 'rgba(129,140,248,0.05)', border: '1px solid rgba(129,140,248,0.15)',
          fontSize: 13, color: 'var(--muted)', lineHeight: 1.7,
        }}>
          <strong style={{ color: 'var(--text)' }}>¿Para qué sirve esto?</strong>
          <br />
          Sube un archivo de ventas recientes para compararlo con el historial que usó el sistema para entrenar.
          Si tus ventas cambiaron significativamente (por ejemplo, ganaste un cliente grande, lanzaste una
          promoción o cambiaron los patrones de tu mercado), el sistema te avisará qué productos tienen
          comportamiento diferente al esperado.
        </div>
        <div
          onDragOver={e => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={e => { e.preventDefault(); setDragging(false); if (e.dataTransfer.files[0]) runDrift(e.dataTransfer.files[0]) }}
          onClick={() => inputRef.current?.click()}
          style={{
            border: `2px dashed ${dragging ? C.indigo : C.border2}`,
            borderRadius: 10, padding: '32px 20px', textAlign: 'center', cursor: 'pointer',
            background: dragging ? C.indigo + '08' : C.card,
            transition: 'all 0.15s',
          }}
        >
          <input ref={inputRef} type="file" accept=".csv,.xlsx,.xls" style={{ display: 'none' }}
            onChange={e => { if (e.target.files?.[0]) runDrift(e.target.files[0]) }} />
          {loading ? (
            <><Spinner /><div style={{ fontSize: 13, color: C.dim, marginTop: 10 }}>Running drift analysis…</div></>
          ) : (
            <>
              <UploadCloud size={28} color={C.dim} style={{ margin: '0 auto 8px' }} />
              <div style={{ fontSize: 13, fontWeight: 500, color: C.text }}>Upload new dataset to compare</div>
              <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>
                CSV or Excel — must share the same columns as the reference dataset
              </div>
            </>
          )}
        </div>
        {error && (
          <div style={{
            display: 'flex', gap: 8, alignItems: 'center', marginTop: 12,
            padding: '10px 14px', borderRadius: 8,
            background: C.red + '0e', border: `1px solid ${C.red}28`,
            fontSize: 13, color: C.red,
          }}>
            <AlertTriangle size={14} /> {error}
          </div>
        )}
      </div>

      {report && (
        <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Summary */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
            {[
              { label: 'Ref rows',    value: report.ref_rows,                color: C.indigo },
              { label: 'New rows',    value: report.cur_rows,                color: C.indigo },
              { label: 'Features',    value: featureRows.length,             color: C.indigo },
              { label: 'Drift found', value: report.has_drift ? 'Yes' : 'No', color: report.has_drift ? C.red : C.green },
            ].map(({ label, value, color }) => (
              <div key={label} style={{
                background: C.card, borderRadius: 8,
                padding: '12px 14px', border: `1px solid ${C.border}`,
              }}>
                <div style={{ fontSize: 20, fontWeight: 700, color }}>{value}</div>
                <div style={{ fontSize: 11, color: C.dim, marginTop: 2 }}>{label}</div>
              </div>
            ))}
          </div>

          {/* Alerts */}
          {report.alerts.length > 0 && (
            <div style={{
              borderRadius: 8, padding: '12px 14px',
              background: C.red + '08', border: `1px solid ${C.red}20`,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8, fontSize: 12, fontWeight: 600, color: C.red }}>
                <ShieldAlert size={13} /> Drift Alerts
              </div>
              {report.alerts.map((a, i) => (
                <div key={i} style={{ fontSize: 12, color: '#fca5a5', fontFamily: 'monospace' }}>{a}</div>
              ))}
            </div>
          )}

          {/* Feature table */}
          {featureRows.length > 0 && (
            <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, overflow: 'hidden' }}>
              <div style={{
                display: 'grid', gridTemplateColumns: '1fr 80px 110px 90px 70px',
                gap: 10, padding: '8px 14px',
                fontSize: 10, color: C.dim, fontWeight: 600, textTransform: 'uppercase',
                borderBottom: `1px solid ${C.border}`, background: C.card,
              }}>
                <span>Feature</span><span>PSI</span><span>Level</span><span>KS p-val</span><span>Drift</span>
              </div>
              {featureRows.map(([col, v]) => (
                <div key={col} style={{
                  display: 'grid', gridTemplateColumns: '1fr 80px 110px 90px 70px',
                  gap: 10, padding: '9px 14px', alignItems: 'center',
                  borderBottom: `1px solid ${C.border}`,
                  background: v.drift ? C.red + '05' : undefined,
                }}>
                  <span style={{ fontSize: 12, fontWeight: 500, fontFamily: 'monospace', color: C.text }}>{col}</span>
                  <span style={{ fontSize: 12, fontFamily: 'monospace', color: C.muted }}>{v.psi.toFixed(4)}</span>
                  <span>
                    <span style={{
                      fontSize: 10, fontWeight: 600,
                      color: PSI_COLOR[v.psi_level] ?? C.slate,
                      background: (PSI_COLOR[v.psi_level] ?? C.slate) + '18',
                      borderRadius: 4, padding: '2px 7px',
                    }}>
                      {v.psi_level}
                    </span>
                  </span>
                  <span style={{ fontSize: 12, color: C.dim, fontFamily: 'monospace' }}>{v.ks_p_value.toFixed(4)}</span>
                  <span>
                    {v.drift
                      ? <AlertTriangle size={13} color={C.red} />
                      : <CheckCircle2 size={13} color={C.green} />}
                  </span>
                </div>
              ))}
            </div>
          )}
          {!featureRows.length && (
            <div style={{ fontSize: 13, color: C.dim, textAlign: 'center', padding: '16px 0' }}>
              No numeric features to compare (need ≥10 rows each).
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function ReportsPage() {
  const [sessions,  setSessions]  = useState<SessionInfo[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [metrics,   setMetrics]   = useState<MetricRow[]>([])
  const [quality,   setQuality]   = useState<QualityReport>({})
  const [report,    setReport]    = useState<Record<string, unknown>>({})
  const [loading,   setLoading]   = useState(false)
  const [tab,       setTab]       = useState('performance')

  useEffect(() => {
    getSessions().then(s => {
      setSessions(s)
      const completed = s.find(x => x.status === 'COMPLETED')
      if (completed) setSessionId(completed.session_id)
    }).catch(console.error)
  }, [])

  useEffect(() => {
    if (!sessionId) return
    setLoading(true)
    Promise.all([
      getMetrics(sessionId).catch(() => ({ rows: [], by_model: {} })),
      getQuality(sessionId).catch(() => ({})),
      getResults(sessionId).catch(() => ({})),
    ]).then(([m, q, rep]) => {
      setMetrics(m.rows)
      setQuality(q as QualityReport)
      setReport(rep)
    }).finally(() => setLoading(false))
  }, [sessionId])

  const session = sessions.find(s => s.session_id === sessionId)
  const trained  = sessions.filter(s => s.status === 'COMPLETED')

  // Derived KPIs
  const bestModel = useMemo(() => {
    if (!metrics.length) return null
    const byModel: Record<string, number[]> = {}
    metrics.filter(r => r.mae !== null).forEach(r => {
      if (!byModel[r.model]) byModel[r.model] = []
      byModel[r.model].push(r.mae!)
    })
    return Object.entries(byModel)
      .map(([m, v]) => ({ model: m, avg: v.reduce((a, b) => a + b, 0) / v.length }))
      .sort((a, b) => a.avg - b.avg)[0] ?? null
  }, [metrics])

  const bestWape = useMemo(() => {
    const wapes = metrics.filter(r => r.wape !== null).map(r => r.wape!)
    return wapes.length ? wapes.reduce((a, b) => a + b, 0) / wapes.length : null
  }, [metrics])

  const TABS = [
    { id: 'performance', label: 'Confiabilidad del forecast', icon: BarChart2   },
    { id: 'quality',     label: 'Calidad de los datos',       icon: Activity    },
    { id: 'drift',       label: 'Cambios en la demanda',      icon: ShieldAlert },
    { id: 'export',      label: 'Descargar resultados',        icon: Download    },
  ]

  return (
    <div style={{ padding: '28px 32px', background: C.bg, minHeight: '100vh' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>
            Reports & Monitoring
          </h1>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: C.muted }}>
            Analyse model accuracy, data quality, and distribution drift.
          </p>
        </div>

        {/* Session picker */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 12, color: C.dim }}>Session:</span>
          <div style={{ position: 'relative' }}>
            <select
              value={sessionId ?? ''}
              onChange={e => setSessionId(e.target.value || null)}
              style={{
                background: C.surface, border: `1px solid ${C.border2}`,
                borderRadius: 8, color: C.text, fontSize: 13,
                padding: '8px 32px 8px 12px', outline: 'none', cursor: 'pointer', minWidth: 220,
              }}
            >
              <option value="" disabled>Select session…</option>
              {trained.map(s => (
                <option key={s.session_id} value={s.session_id}>{s.name}</option>
              ))}
            </select>
            <ChevronDown size={12} style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: C.dim }} />
          </div>
        </div>
      </div>

      {/* KPI strip — only when session loaded */}
      {session && !loading && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginBottom: 20 }}>
          <KpiCard icon={TrendingUp} label="Session"      value={session.name.length > 18 ? session.name.slice(0, 17) + '…' : session.name}  color={C.indigo} sub={fmtDate(session.created_at)} />
          <KpiCard icon={Award}      label="Best Model"   value={bestModel?.model ?? '—'}  color={C.indigo} sub={bestModel ? `MAE ${bestModel.avg.toFixed(3)}` : undefined} />
          <KpiCard icon={Target}     label="Best WAPE"    value={bestWape !== null ? pct(bestWape) : '—'}  color={bestWape !== null && bestWape < 0.2 ? C.green : C.amber} />
          <KpiCard icon={Layers}     label="SKUs Analysed" value={Object.keys(quality).length || '—'}  color={C.cyan} />
          <KpiCard icon={Database}   label="Experiments"   value={metrics.length || '—'}    color={C.slate} sub={`across ${trained.length} session${trained.length !== 1 ? 's' : ''}`} />
        </div>
      )}

      {/* Main content card */}
      {!sessionId && !loading && (
        <div style={{
          background: C.surface, border: `1px solid ${C.border}`,
          borderRadius: 12, padding: '60px 20px', textAlign: 'center',
        }}>
          <Package size={44} strokeWidth={1.2} color={C.border2} style={{ margin: '0 auto 16px' }} />
          <div style={{ fontSize: 15, fontWeight: 600, color: C.muted, marginBottom: 6 }}>No session selected</div>
          <div style={{ fontSize: 13, color: C.dim }}>
            {trained.length
              ? 'Pick a trained session from the dropdown above.'
              : 'Run the Forecast Studio first to generate a trained session.'}
          </div>
        </div>
      )}

      {sessionId && (
        <Card
          title={session?.name ?? 'Loading…'}
          sub={`${metrics.length} experiments · ${Object.keys(quality).length} SKUs analysed`}
        >
          <TabBar tabs={TABS} active={tab} onChange={setTab} />
          {loading ? (
            <div style={{ padding: '52px 20px', display: 'flex', justifyContent: 'center' }}>
              <Spinner />
            </div>
          ) : (
            <>
              {tab === 'performance' && <ModelPerformanceTab metrics={metrics} />}
              {tab === 'quality'     && <DataQualityTab quality={quality} />}
              {tab === 'drift'       && <DriftTab sessionId={sessionId} />}
              {tab === 'export'      && <ExportTab sessionId={sessionId} metrics={metrics} report={report} />}
            </>
          )}
        </Card>
      )}

      {/* All sessions table (when multiple trained sessions exist) */}
      {trained.length > 1 && (
        <div style={{ marginTop: 20 }}>
          <Card title="All Trained Sessions" sub={`${trained.length} completed sessions — click to switch`}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Session</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Dataset</th>
                </tr>
              </thead>
              <tbody>
                {trained.map(s => (
                  <tr
                    key={s.session_id}
                    style={{ cursor: 'pointer', background: s.session_id === sessionId ? C.indigo + '06' : undefined }}
                    onClick={() => setSessionId(s.session_id)}
                  >
                    <td style={{ fontWeight: 500 }}>
                      {s.name}
                      {s.session_id === sessionId && (
                        <span style={{ fontSize: 9, color: C.indigo, marginLeft: 8, fontWeight: 700 }}>SELECTED</span>
                      )}
                    </td>
                    <td>
                      <span style={{
                        fontSize: 10, fontWeight: 600, borderRadius: 4, padding: '2px 8px',
                        background: C.green + '18', color: C.green,
                      }}>
                        COMPLETED
                      </span>
                    </td>
                    <td style={{ color: C.dim, fontSize: 12 }}>{fmtDate(s.created_at)}</td>
                    <td style={{ fontSize: 12, color: C.dim, fontFamily: 'monospace' }}>
                      {s.file_path ? s.file_path.split(/[\\/]/).pop() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      )}
    </div>
  )
}
