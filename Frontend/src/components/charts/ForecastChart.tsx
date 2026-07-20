'use client'
import dynamic from 'next/dynamic'
import { useState, useMemo } from 'react'

const ReactECharts = dynamic(() => import('echarts-for-react'), { ssr: false })

export interface ForecastChartPoint {
  date:    string
  p10?:    number
  p50:     number
  p90?:    number
  actual?: number
}

interface ForecastChartProps {
  series:  ForecastChartPoint[]
  height?: number
  isDark?: boolean
}

export default function ForecastChart({ series, height = 280, isDark = true }: ForecastChartProps) {
  const hasBands   = series.some(p => p.p10 !== undefined && p.p90 !== undefined)
  const hasActuals = series.some(p => p.actual !== undefined)
  const [showBands, setShowBands] = useState(true)

  const option = useMemo(() => {
    const dates    = series.map(p => p.date)
    const p50Vals  = series.map(p => p.p50)
    const p10Vals  = series.map(p => p.p10 ?? null)
    const bandVals = series.map((p, i) =>
      p.p10 !== undefined && p.p90 !== undefined ? Math.max(0, p.p90 - p.p10) : null
    )
    const actualVals = series.map(p => p.actual ?? null)

    const gridLine   = isDark ? '#1e2030' : '#e2e8f0'
    const tooltipBg  = isDark ? '#0f1015' : '#ffffff'
    const tooltipBdr = isDark ? '#1e2030' : '#e2e8f0'
    const textColor  = isDark ? '#e2e8f0' : '#1e293b'
    const dimColor   = '#64748b'

    const seriesList: object[] = []

    if (hasBands && showBands) {
      // Invisible base at P10 (stacked area anchoring)
      seriesList.push({
        name: '_band_base', type: 'line', data: p10Vals,
        lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 },
        stack: 'ci_band', symbol: 'none', silent: true,
        legendHoverLink: false, tooltip: { show: false },
      })
      // Visible band = P90 - P10 stacked on top of base
      seriesList.push({
        name: 'P10–P90', type: 'line', data: bandVals,
        lineStyle: { opacity: 0 },
        areaStyle: { color: '#6366f1', opacity: 0.13 },
        stack: 'ci_band', symbol: 'none', legendHoverLink: true,
        tooltip: { show: false },
      })
    }

    seriesList.push({
      name: 'Forecast (P50)', type: 'line', data: p50Vals,
      lineStyle: { color: '#818cf8', width: 2 },
      itemStyle: { color: '#818cf8' }, symbol: 'none',
      z: 3,
    })

    if (hasActuals) {
      seriesList.push({
        name: 'Actual', type: 'line', data: actualVals,
        lineStyle: { color: '#22c55e', width: 2, type: 'dashed' },
        itemStyle: { color: '#22c55e' }, symbol: 'circle', symbolSize: 4,
        z: 4,
      })
    }

    return {
      backgroundColor: 'transparent',
      grid: { top: 24, right: 16, bottom: 36, left: 48, containLabel: true },
      tooltip: {
        trigger: 'axis',
        backgroundColor: tooltipBg,
        borderColor: tooltipBdr,
        textStyle: { color: textColor, fontSize: 12 },
        formatter: (params: any[]) => {
          const date = params[0]?.axisValue ?? ''
          const p = series.find(s => s.date === date)
          if (!p) return date
          let html = `<div style="font-weight:600;margin-bottom:4px">${date}</div>`
          html += `<div>Forecast: <strong>${p.p50.toFixed(2)}</strong></div>`
          if (p.p10 !== undefined && p.p90 !== undefined)
            html += `<div style="color:#a5b4fc">Range: ${p.p10.toFixed(2)} – ${p.p90.toFixed(2)}</div>`
          if (p.actual !== undefined && p.actual !== null)
            html += `<div style="color:#22c55e">Actual: <strong>${p.actual.toFixed(2)}</strong></div>`
          return html
        },
      },
      legend: {
        data: [
          'Forecast (P50)',
          ...(hasBands && showBands ? ['P10–P90'] : []),
          ...(hasActuals ? ['Actual'] : []),
        ],
        bottom: 0, textStyle: { color: dimColor, fontSize: 11 },
        itemWidth: 14, itemHeight: 8,
      },
      xAxis: {
        type: 'category', data: dates, boundaryGap: false,
        axisLine: { lineStyle: { color: gridLine } },
        axisTick: { show: false },
        axisLabel: { color: dimColor, fontSize: 10 },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: dimColor, fontSize: 10 },
        splitLine: { lineStyle: { color: gridLine } },
      },
      series: seriesList,
    }
  }, [series, showBands, hasBands, hasActuals, isDark])

  return (
    <div>
      {hasBands && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 6 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--dim)', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={showBands}
              onChange={e => setShowBands(e.target.checked)}
              style={{ accentColor: 'var(--accent)' }}
            />
            Show uncertainty bands
          </label>
        </div>
      )}
      <ReactECharts
        option={option}
        style={{ height, width: '100%' }}
        theme={isDark ? 'dark' : undefined}
        opts={{ renderer: 'canvas' }}
        notMerge
      />
    </div>
  )
}
