'use client'
// INSTANCIA-3 TAREA-3B: COMPLETADA
import { useState, useEffect, useRef, useCallback } from 'react'
import dynamic from 'next/dynamic'
import { getSessions, getAccuracyReport, uploadActuals } from '@/lib/api'
import type { SessionInfo, AccuracyReport, AccuracySnapshot } from '@/lib/types'
import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'
import Badge from '@/components/ui/Badge'
import { AlertTriangle, Upload, X, TrendingDown, CheckCircle2 } from 'lucide-react'

const ReactECharts = dynamic(() => import('echarts-for-react'), { ssr: false })

// ── WAPE over time chart ───────────────────────────────────────────────────────
function WapeChart({ snapshots, threshold }: { snapshots: AccuracySnapshot[]; threshold: number }) {
  const byDate = snapshots.reduce<Record<string, number[]>>((acc, s) => {
    if (s.wape !== null) {
      if (!acc[s.date]) acc[s.date] = []
      acc[s.date].push(s.wape)
    }
    return acc
  }, {})

  const dates     = Object.keys(byDate).sort()
  const avgWapes  = dates.map(d => {
    const vals = byDate[d]
    return +(vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(4)
  })

  const option = {
    backgroundColor: 'transparent',
    grid: { top: 24, right: 16, bottom: 36, left: 52, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#0f1015',
      borderColor: '#1e2030',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
      formatter: (params: any[]) =>
        `${params[0].axisValue}: <strong>${(params[0].value * 100).toFixed(1)}%</strong>`,
    },
    xAxis: {
      type: 'category', data: dates, boundaryGap: false,
      axisLabel: { color: '#64748b', fontSize: 10 },
      axisLine: { lineStyle: { color: '#1e2030' } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#64748b', fontSize: 10, formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
      axisLine: { show: false }, axisTick: { show: false },
      splitLine: { lineStyle: { color: '#1e2030' } },
    },
    series: [
      {
        name: 'Avg WAPE',
        type: 'line',
        data: avgWapes,
        lineStyle: { color: '#818cf8', width: 2 },
        itemStyle: { color: '#818cf8' },
        areaStyle: { color: '#818cf8', opacity: 0.08 },
        symbol: 'circle', symbolSize: 5,
        markLine: {
          silent: true,
          lineStyle: { color: '#f59e0b', type: 'dashed', width: 1 },
          data: [{ yAxis: threshold, label: { formatter: 'Threshold', color: '#f59e0b', fontSize: 10 } }],
        },
      },
    ],
  }

  if (!dates.length) {
    return (
      <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--dim)', fontSize: 13 }}>
        No accuracy data yet — upload actuals to see the chart.
      </div>
    )
  }

  return <ReactECharts option={option} style={{ height: 200, width: '100%' }} theme="dark" opts={{ renderer: 'canvas' }} notMerge />
}

// ── Upload Actuals Modal ──────────────────────────────────────────────────────
function UploadModal({ sessionId, onClose, onSuccess }: {
  sessionId: string; onClose: () => void; onSuccess: (n: number) => void
}) {
  const [file,      setFile]    = useState<File | null>(null)
  const [dragging,  setDrag]    = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error,     setError]   = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDrag(false)
    const f = e.dataTransfer.files[0]
    if (f) setFile(f)
  }

  const submit = async () => {
    if (!file) return
    setUploading(true); setError(null)
    try {
      const result = await uploadActuals(sessionId, file)
      onSuccess(result.matched_rows)
    } catch (e: any) {
      setError(e.message)
    } finally { setUploading(false) }
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
    }}>
      <div style={{
        background: 'var(--surface)', borderRadius: 12, padding: 28, width: 420,
        border: '1px solid var(--border)', boxShadow: '0 24px 48px rgba(0,0,0,0.5)',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700 }}>Upload Actuals</div>
            <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>Compare forecast against real values</div>
          </div>
          <button onClick={onClose} style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)' }}>
            <X size={18} />
          </button>
        </div>

        <div
          onDragOver={e => { e.preventDefault(); setDrag(true) }}
          onDragLeave={() => setDrag(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          style={{
            border: `2px dashed ${dragging ? 'var(--accent)' : 'var(--border)'}`,
            borderRadius: 8, padding: 32, textAlign: 'center', cursor: 'pointer',
            background: dragging ? 'var(--accent-dim)' : 'var(--surface-2)',
            transition: 'all 0.15s', marginBottom: 16,
          }}
        >
          <Upload size={22} style={{ color: 'var(--dim)', marginBottom: 8 }} />
          <div style={{ fontSize: 13, color: file ? 'var(--accent)' : 'var(--muted)' }}>
            {file ? file.name : 'Drop CSV here or click to browse'}
          </div>
          {!file && <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 4 }}>Required columns: date, actual_value (+ sku if multi-SKU)</div>}
          <input ref={inputRef} type="file" accept=".csv" style={{ display: 'none' }}
            onChange={e => { if (e.target.files?.[0]) setFile(e.target.files[0]) }} />
        </div>

        {error && (
          <div style={{ fontSize: 12, color: '#ef4444', marginBottom: 12, padding: '8px 12px', borderRadius: 6, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)' }}>
            {error}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!file} loading={uploading} onClick={submit}>
            Upload
          </Button>
        </div>
      </div>
    </div>
  )
}

// ── SKU table ─────────────────────────────────────────────────────────────────
type SortKey = 'sku' | 'mae' | 'wape'

function SkuTable({ snapshots, threshold }: { snapshots: AccuracySnapshot[]; threshold: number }) {
  const [sortKey, setSort] = useState<SortKey>('wape')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSort(key); setSortDir('desc') }
  }

  // Aggregate by SKU
  const bySkuMap = snapshots.reduce<Record<string, { mae: number[]; wape: number[]; rows: number }>>((acc, s) => {
    if (!acc[s.sku]) acc[s.sku] = { mae: [], wape: [], rows: 0 }
    acc[s.sku].rows++
    if (s.mae  !== null) acc[s.sku].mae.push(s.mae)
    if (s.wape !== null) acc[s.sku].wape.push(s.wape)
    return acc
  }, {})

  const rows = Object.entries(bySkuMap).map(([sku, v]) => ({
    sku,
    mae:  v.mae.length  ? +(v.mae.reduce((a, b) => a + b, 0) / v.mae.length).toFixed(4)  : null,
    wape: v.wape.length ? +(v.wape.reduce((a, b) => a + b, 0) / v.wape.length).toFixed(4) : null,
    rows: v.rows,
  }))

  const sorted = [...rows].sort((a, b) => {
    const av = a[sortKey] ?? 0, bv = b[sortKey] ?? 0
    if (typeof av === 'string') return sortDir === 'asc' ? av.localeCompare(String(bv)) : String(bv).localeCompare(av)
    return sortDir === 'asc' ? (av as number) - (bv as number) : (bv as number) - (av as number)
  })

  const Th = ({ label, k }: { label: string; k: SortKey }) => (
    <th onClick={() => toggleSort(k)} style={{ cursor: 'pointer', userSelect: 'none' }}>
      {label} {sortKey === k ? (sortDir === 'asc' ? '↑' : '↓') : ''}
    </th>
  )

  if (!rows.length) return (
    <div style={{ padding: 24, textAlign: 'center', color: 'var(--dim)', fontSize: 13 }}>
      No accuracy data yet.
    </div>
  )

  return (
    <table className="data-table">
      <thead>
        <tr>
          <Th label="SKU" k="sku" />
          <th>Rows</th>
          <Th label="Avg MAE" k="mae" />
          <Th label="Avg WAPE" k="wape" />
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map(r => {
          const over = r.wape !== null && r.wape > threshold
          return (
            <tr key={r.sku}>
              <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.sku}</td>
              <td style={{ color: 'var(--dim)' }}>{r.rows}</td>
              <td>{r.mae?.toFixed(4) ?? '—'}</td>
              <td style={{ color: over ? '#f87171' : '#22c55e', fontWeight: 600 }}>
                {r.wape !== null ? `${(r.wape * 100).toFixed(1)}%` : '—'}
              </td>
              <td>
                <Badge variant={over ? 'danger' : 'success'}>
                  {over ? 'Degraded' : 'OK'}
                </Badge>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function AccuracyPage() {
  const [sessions,     setSessions]   = useState<SessionInfo[]>([])
  const [sessionId,    setSessionId]  = useState<string>('')
  const [report,       setReport]     = useState<AccuracyReport | null>(null)
  const [loading,      setLoading]    = useState(false)
  const [loadError,    setLoadError]  = useState<string | null>(null)
  const [showUpload,   setShowUpload] = useState(false)
  const [uploadMsg,    setUploadMsg]  = useState<string | null>(null)

  useEffect(() => {
    getSessions()
      .then(ss => {
        const completed = ss.filter(s => s.status === 'COMPLETED')
        setSessions(completed)
        if (completed.length) setSessionId(completed[0].session_id)
      })
      .catch(e => setLoadError(e.message))
  }, [])

  const load = useCallback((id: string) => {
    if (!id) return
    setLoading(true); setLoadError(null); setReport(null)
    getAccuracyReport(id)
      .then(setReport)
      .catch(e => setLoadError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { if (sessionId) load(sessionId) }, [sessionId, load])

  const handleUploadSuccess = (matched: number) => {
    setShowUpload(false)
    setUploadMsg(`${matched} rows matched and saved.`)
    setTimeout(() => setUploadMsg(null), 4000)
    load(sessionId)
  }

  const degraded = report && report.overall_wape !== null && report.overall_wape > report.threshold

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, animation: 'fadeIn 0.3s ease-out' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>Accuracy Tracking</div>
          <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 2 }}>
            Compare forecasts against real outcomes
          </div>
        </div>
        <Button
          variant="primary"
          icon={<Upload size={13} />}
          disabled={!sessionId}
          onClick={() => setShowUpload(true)}
        >
          Upload Actuals
        </Button>
      </div>

      {/* Session selector */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <label style={{ fontSize: 12, color: 'var(--dim)', whiteSpace: 'nowrap' }}>Session:</label>
        <select
          value={sessionId}
          onChange={e => setSessionId(e.target.value)}
          className="form-input form-select"
          style={{ fontSize: 12, maxWidth: 340 }}
        >
          {sessions.length === 0 && <option value="">No completed sessions</option>}
          {sessions.map(s => (
            <option key={s.session_id} value={s.session_id}>{s.name}</option>
          ))}
        </select>
      </div>

      {/* Alerts */}
      {degraded && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderRadius: 8, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)', fontSize: 13, color: '#f87171' }}>
          <AlertTriangle size={16} style={{ flexShrink: 0 }} />
          <span style={{ flex: 1 }}>
            Accuracy degraded — overall WAPE {((report!.overall_wape ?? 0) * 100).toFixed(1)}% exceeds threshold {(report!.threshold * 100).toFixed(0)}%. Consider retraining.
          </span>
        </div>
      )}
      {loadError && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderRadius: 8, background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.2)', fontSize: 12, color: '#f87171' }}>
          <AlertTriangle size={14} />{loadError}
        </div>
      )}
      {uploadMsg && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderRadius: 8, background: 'rgba(34,197,94,0.07)', border: '1px solid rgba(34,197,94,0.2)', fontSize: 12, color: '#22c55e' }}>
          <CheckCircle2 size={14} />{uploadMsg}
        </div>
      )}

      {loading && (
        <div style={{ textAlign: 'center', padding: 60 }}><Spinner size={24} /></div>
      )}

      {!loading && report && (
        <>
          {/* KPI strip */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14 }}>
            {[
              { label: 'Overall WAPE', value: report.overall_wape !== null ? `${(report.overall_wape * 100).toFixed(1)}%` : '—', ok: !degraded },
              { label: 'Threshold',    value: `${(report.threshold * 100).toFixed(0)}%`, ok: true },
              { label: 'Data Points',  value: String(report.snapshots.length), ok: true },
            ].map(({ label, value, ok: isOk }) => (
              <div key={label} style={{ padding: '14px 18px', borderRadius: 10, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
                <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 4 }}>{label}</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: isOk ? 'var(--text)' : '#f87171' }}>{value}</div>
              </div>
            ))}
          </div>

          {/* Chart */}
          <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 10, padding: '16px 20px' }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>WAPE Over Time</div>
            <WapeChart snapshots={report.snapshots} threshold={report.threshold} />
          </div>

          {/* SKU table */}
          <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontSize: 13, fontWeight: 600 }}>
              By SKU
            </div>
            <SkuTable snapshots={report.snapshots} threshold={report.threshold} />
          </div>
        </>
      )}

      {!loading && !report && !loadError && sessionId && (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--dim)', fontSize: 13 }}>
          No accuracy data yet. Upload actuals to start tracking.
        </div>
      )}

      {showUpload && sessionId && (
        <UploadModal
          sessionId={sessionId}
          onClose={() => setShowUpload(false)}
          onSuccess={handleUploadSuccess}
        />
      )}
    </div>
  )
}
