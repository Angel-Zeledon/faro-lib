'use client'

// A tiny line with no axes: the shape of a series at a glance. Used by the
// product list, where a number would say less than the slope does.
export function Sparkline({ values, color = 'var(--accent)', width = 80, height = 28 }: {
  values: number[]; color?: string; width?: number; height?: number
}) {
  if (values.length < 2) return null
  const min = Math.min(...values), max = Math.max(...values)
  const range = max - min || 1
  const xs = values.map((_, i) => (i / (values.length - 1)) * width)
  const ys = values.map(v => height - ((v - min) / range) * (height - 2) - 1)
  const d = xs.map((x, i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ')
  return (
    <svg width={width} height={height} style={{ display: 'block', overflow: 'visible' }}>
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
