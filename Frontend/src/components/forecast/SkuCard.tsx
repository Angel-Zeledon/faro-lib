'use client'
import { Package } from 'lucide-react'
import type { MetricRow, QualityReport, InventorySignal } from '@/lib/types'
import SignalBadge from '@/components/ui/SignalBadge'
import { useLanguage } from '@/contexts/LanguageContext'
import { SERIES_COLOR, reliabilityInfo } from './shared'

// ── Sparkline ─────────────────────────────────────────────────────────────────

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

// ── SKU card ──────────────────────────────────────────────────────────────────

export function SkuCard({ sku, quality, metrics, signal, selected, onClick, tourAnchor, showTechnical }: {
  sku: string
  quality?: QualityReport[string]
  metrics: MetricRow[]
  // Live semáforo (from inventory_stock), not the training-time recommendation.
  signal?: InventorySignal
  selected: boolean
  onClick: () => void
  /** Set on the first card only — a tour anchor has to be unique in the DOM. */
  tourAnchor?: string
  /** The sparkline is each model's error side by side — the model competition
   *  in miniature — so it is drawn in the technical view only. */
  showTechnical: boolean
}) {
  const { t } = useLanguage()
  const seriesType = quality?.series_type ?? 'unknown'
  const color = SERIES_COLOR[seriesType] ?? SERIES_COLOR.unknown
  const sparkVals = metrics.map(r => r.mae).filter((v): v is number => v !== null)

  return (
    <button
      data-tour={tourAnchor}
      onClick={onClick}
      style={{
        all: 'unset', cursor: 'pointer', display: 'block', width: '100%',
        // `all: unset` also resets box-sizing to content-box, so `width: 100%`
        // plus 28px of padding and the 3px selected border made every card
        // exactly 31px wider than the column holding it — at ANY column width,
        // which is why the list scrolled sideways and the reliability pill was
        // cut off no matter how much room the column was given.
        boxSizing: 'border-box',
        padding: '11px 14px',
        background: selected ? 'var(--surface-2)' : 'transparent',
        borderLeft: `3px solid ${selected ? color : 'transparent'}`,
        borderBottom: '1px solid var(--border)',
        transition: 'background 0.12s, border-color 0.12s',
      }}
    >
      {/* Two rows that can only shrink, never overflow. Every element used to
          be `flexShrink: 0` inside a column narrower than their sum, so the
          card spilled to the right and the list scrolled sideways — the SKU
          badge and the reliability pill were cut in half. */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0, flex: 1 }}>
          <Package size={11} color={color} style={{ flexShrink: 0 }} />
          <span style={{ fontSize: 12.5, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {sku}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          {showTechnical && sparkVals.length > 0 && <Sparkline values={sparkVals} color={color} width={44} height={20} />}
          {signal && <SignalBadge signal={signal} />}
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 5, flexWrap: 'wrap' }}>
        {quality && (() => {
          const { label, color } = reliabilityInfo(quality.quality_score, t)
          return (
            <span style={{
              fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 10,
              background: color + '18', color,
              marginLeft: 'auto',
            }}>
              {label}
            </span>
          )
        })()}
      </div>
    </button>
  )
}
