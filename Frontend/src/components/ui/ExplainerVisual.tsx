'use client'
/**
 * Reusable visual pieces for explanation windows (help popovers, tour steps,
 * upload guides). An explanation that concerns a flow, a column mapping, a
 * semáforo state or a table should SHOW it, not only describe it.
 *
 * Rules: theme tokens only (calm colours, no neon), every piece reflows to a
 * narrow container, and every animation is CSS (`.xv-*` in globals.css) that is
 * switched off under prefers-reduced-motion.
 *
 * Copy is never hardcoded: pieces take already-translated strings, and the
 * named `ExplainerVisual` registry resolves its own text through `t`.
 */
import { ArrowRight, ArrowDown } from 'lucide-react'
import { SIGNAL_STYLES } from '@/components/ui/SignalBadge'
import SignalBadge from '@/components/ui/SignalBadge'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import type { InventorySignal } from '@/lib/types'

const box: React.CSSProperties = {
  border: '1px solid var(--border)', borderRadius: 8,
  background: 'var(--surface-2)', padding: '10px 12px',
}

/** Small table of example cells. `highlight` marks column indexes (e.g. the
 *  required ones) with the accent colour; `mono` keeps cells aligned. */
export function MiniTable({ headers, rows, highlight = [], caption }: {
  headers: string[]
  rows: string[][]
  highlight?: number[]
  caption?: string
}) {
  return (
    <div style={{ overflowX: 'auto', maxWidth: '100%' }}>
      <table style={{
        borderCollapse: 'collapse', fontSize: 11.5, width: '100%',
        border: '1px solid var(--border)', borderRadius: 6,
        fontVariantNumeric: 'tabular-nums',
      }}>
        {caption && <caption style={{ captionSide: 'bottom', fontSize: 11, color: 'var(--dim)', paddingTop: 4, textAlign: 'left' }}>{caption}</caption>}
        <thead>
          <tr style={{ background: 'var(--surface-3, var(--surface-2))' }}>
            {headers.map((h, i) => (
              <th key={i} style={{
                padding: '5px 9px', textAlign: 'left', whiteSpace: 'nowrap',
                borderBottom: '1px solid var(--border)', fontWeight: 700,
                color: highlight.includes(i) ? 'var(--accent)' : 'var(--dim)',
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri}>
              {r.map((c, ci) => (
                <td key={ci} style={{
                  padding: '4px 9px', whiteSpace: 'nowrap', color: 'var(--text)',
                  borderTop: ri ? '1px solid var(--border)' : 'none',
                }}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Numbered steps, each with an optional one-line detail. */
export function Steps({ items }: { items: { title: string; detail?: string }[] }) {
  return (
    <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {items.map((s, i) => (
        <li key={i} className="xv-in" style={{ display: 'flex', gap: 10, alignItems: 'flex-start', animationDelay: `${i * 90}ms` }}>
          <span style={{
            flexShrink: 0, width: 22, height: 22, borderRadius: '50%',
            background: 'var(--accent-dim, var(--surface-3))', color: 'var(--accent)',
            fontSize: 11.5, fontWeight: 700, display: 'inline-flex',
            alignItems: 'center', justifyContent: 'center',
          }}>{i + 1}</span>
          <span style={{ fontSize: 12, lineHeight: 1.5, color: 'var(--text)' }}>
            <strong style={{ fontWeight: 650 }}>{s.title}</strong>
            {s.detail && <span style={{ color: 'var(--dim)' }}> — {s.detail}</span>}
          </span>
        </li>
      ))}
    </ol>
  )
}

/** Boxes joined by arrows (a flow). Stacks vertically when narrow. */
export function FlowDiagram({ nodes }: { nodes: { label: string; sub?: string }[] }) {
  const narrow = useIsNarrow()
  return (
    <div style={{
      display: 'flex', flexDirection: narrow ? 'column' : 'row',
      alignItems: 'stretch', gap: 6,
    }}>
      {nodes.map((n, i) => (
        <div key={i} style={{
          display: 'flex', flexDirection: narrow ? 'column' : 'row',
          alignItems: 'center', gap: 6, flex: 1, minWidth: 0,
        }}>
          <div className="xv-in" style={{ ...box, flex: 1, width: '100%', textAlign: 'center', animationDelay: `${i * 120}ms` }}>
            <div style={{ fontSize: 12, fontWeight: 650, color: 'var(--text)' }}>{n.label}</div>
            {n.sub && <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2, lineHeight: 1.4 }}>{n.sub}</div>}
          </div>
          {i < nodes.length - 1 && (
            <span className="xv-arrow" aria-hidden="true" style={{ color: 'var(--dim)', display: 'inline-flex' }}>
              {narrow ? <ArrowDown size={14} /> : <ArrowRight size={14} />}
            </span>
          )}
        </div>
      ))}
    </div>
  )
}

/** Source column -> product field mapping, drawn as two lists with a link. */
export function ColumnMapDiagram({ pairs, fromLabel, toLabel }: {
  pairs: { from: string; to: string }[]
  fromLabel: string
  toLabel: string
}) {
  return (
    <div style={{ ...box, display: 'grid', gridTemplateColumns: '1fr auto 1fr', gap: '6px 8px', alignItems: 'center' }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{fromLabel}</div>
      <span />
      <div style={{ fontSize: 10.5, fontWeight: 700, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{toLabel}</div>
      {pairs.map((p, i) => (
        <PairRow key={i} {...p} />
      ))}
    </div>
  )
}
function PairRow({ from, to }: { from: string; to: string }) {
  const chip: React.CSSProperties = {
    fontSize: 11.5, padding: '3px 8px', borderRadius: 6, background: 'var(--surface)',
    border: '1px solid var(--border)', color: 'var(--text)', whiteSpace: 'nowrap',
    overflow: 'hidden', textOverflow: 'ellipsis',
  }
  return (
    <>
      <span style={chip}>{from}</span>
      <ArrowRight size={13} className="xv-arrow" aria-hidden="true" style={{ color: 'var(--accent)' }} />
      <span style={{ ...chip, borderColor: 'var(--accent)' }}>{to}</span>
    </>
  )
}

/** The four semáforo states, each with the real badge and a one-line meaning. */
export function SemaforoLegend({ meanings }: { meanings: Partial<Record<InventorySignal, string>> }) {
  const order = (Object.keys(SIGNAL_STYLES) as InventorySignal[]).filter(k => meanings[k])
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {order.map((s, i) => (
        <div key={s} className="xv-in" style={{ display: 'flex', alignItems: 'center', gap: 10, animationDelay: `${i * 90}ms` }}>
          <span style={{ flexShrink: 0, minWidth: 112 }}><SignalBadge signal={s} /></span>
          <span style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.45 }}>{meanings[s]}</span>
        </div>
      ))}
    </div>
  )
}

/** Wrapper that gives a visual a quiet panel and an optional caption. */
export function VisualFrame({ children, caption }: { children: React.ReactNode; caption?: string }) {
  return (
    <figure style={{ margin: '0 0 12px' }}>
      {children}
      {caption && <figcaption style={{ fontSize: 11, color: 'var(--dim)', marginTop: 5 }}>{caption}</figcaption>}
    </figure>
  )
}

export type VisualId = 'semaforo' | 'sales_table' | 'column_map' | 'pipeline'

/** Named visuals a tour step or help bubble can ask for by id. */
export default function ExplainerVisual({ id }: { id: VisualId }) {
  const { t } = useLanguage()
  switch (id) {
    case 'semaforo':
      return (
        <VisualFrame>
          <SemaforoLegend meanings={{
            PEDIR_YA: t('xv.semaforo.order_now'),
            PEDIR_PRONTO: t('xv.semaforo.order_soon'),
            OK: t('xv.semaforo.ok'),
            SOBRESTOCK: t('xv.semaforo.overstock'),
          }} />
        </VisualFrame>
      )
    case 'sales_table':
      return (
        <VisualFrame caption={t('xv.sales_table.caption')}>
          <MiniTable
            headers={['sku', 'fecha', 'demanda']}
            highlight={[0, 1, 2]}
            rows={[['SKU-001', '2026-01-01', '32'], ['SKU-001', '2026-01-02', '28'], ['SKU-002', '2026-01-01', '15']]}
          />
        </VisualFrame>
      )
    case 'column_map':
      return (
        <VisualFrame caption={t('xv.column_map.caption')}>
          <ColumnMapDiagram
            fromLabel={t('xv.column_map.from')} toLabel={t('xv.column_map.to')}
            pairs={[
              { from: 'Código', to: 'sku' },
              { from: 'Día', to: 'fecha' },
              { from: 'Unidades', to: 'demanda' },
            ]}
          />
        </VisualFrame>
      )
    case 'pipeline':
      return (
        <VisualFrame>
          <FlowDiagram nodes={[
            { label: t('xv.pipeline.sales'), sub: t('xv.pipeline.sales_sub') },
            { label: t('xv.pipeline.forecast'), sub: t('xv.pipeline.forecast_sub') },
            { label: t('xv.pipeline.signal'), sub: t('xv.pipeline.signal_sub') },
            { label: t('xv.pipeline.order'), sub: t('xv.pipeline.order_sub') },
          ]} />
        </VisualFrame>
      )
  }
}
