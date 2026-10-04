'use client'
import { useState } from 'react'
import type { MetricRow } from '@/lib/types'
import { downloadWorkbook } from '@/lib/excel'
import Button from '@/components/ui/Button'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { modelLabel } from '@/lib/modelLabel'
import { Download, TableProperties, Grid3x3 } from 'lucide-react'
import { type Translate, makeChampionRank, tOr, pct, fmt, downloadCSV } from './shared'

// Exports carry the SAME neutral labels the screen shows, not the raw
// algorithm ids. The tempting alternative — real names in the file, numbers on
// screen — hands the user a sheet full of words the product never showed them
// and that nobody in support can reconcile with "Modelo 3". The labels are a
// bijection with the ids, so nothing analytical is lost: rows still join and
// group exactly as before. If a technical audience ever needs the algorithm,
// that belongs in its own diagnostic export, not mixed into the user's sheet.
export function exportMetricsCSV(t: Translate, sku: string, rows: MetricRow[]) {
  downloadCSV(
    `metrics_${sku}.csv`,
    // `cost` is the column the champion was chosen by; a sheet without it
    // cannot explain why the winning row is not the one with the lowest WAPE.
    ['model', 'type', 'cost', 'mae', 'rmse', 'wape', 'bias', 'n_folds'],
    rows.map(r => [modelLabel(t, r.model), r.type, r.cost_horizon ?? r.cost ?? null,
                   r.mae, r.rmse, r.wape, r.bias, r.n_folds ?? null]),
  )
}

export function exportMetricsExcel(t: Translate, sku: string, rows: MetricRow[]) {
  downloadWorkbook(`metrics_${sku}.xlsx`, [{
    name: 'Metrics',
    rows: [
      ['model', 'type', 'cost', 'mae', 'rmse', 'wape', 'bias', 'n_folds'],
      ...rows.map(r => [modelLabel(t, r.model), r.type, r.cost_horizon ?? r.cost ?? null,
                        r.mae, r.rmse, r.wape, r.bias, r.n_folds ?? null]),
    ],
  }])
}

// ── Metrics table ─────────────────────────────────────────────────────────────

export type MetricViewMode = 'table' | 'heatmap'

export function heatCell(val: number | null, min: number, max: number, lowerIsBetter = true): string {
  if (val == null || min === max) return 'transparent'
  const t = (val - min) / (max - min) // 0=low, 1=high
  const bad = lowerIsBetter ? t : 1 - t // 0=good, 1=bad
  const r = Math.round(34  + (239 - 34)  * bad)
  const g = Math.round(197 + (68  - 197) * bad)
  const b = Math.round(94  + (68  - 94)  * bad)
  return `rgba(${r},${g},${b},0.22)`
}

export function MetricsTable({ rows, sku }: { rows: MetricRow[]; sku: string }) {
  const { t } = useLanguage()
  const narrow = useIsNarrow()
  const [viewMode, setViewMode] = useState<MetricViewMode>('table')

  if (!rows.length) return (
    <div style={{ padding: '24px', textAlign: 'center', color: 'var(--dim)', fontSize: 13 }}>{t('skus.no_metrics')}</div>
  )

  // Ordered by the metric the champion is chosen with, not by WAPE. Sorting by
  // WAPE put the "mejor" badge on a model that was not the one behind this
  // SKU's forecast, its reorder point or its purchase order — and nothing on
  // screen said so.
  const tableRank = makeChampionRank(rows)
  const sorted = [...rows].sort((a, b) =>
    (tableRank(a) ?? Infinity) - (tableRank(b) ?? Infinity))
  // A baseline is scored so the real models have something to beat; it is not a
  // candidate, and the engine refuses to buy from one. Badging the cheapest row
  // outright put "MEJOR" on `Referencia (temporada)` on real data — a naive
  // forecast the pipeline had already excluded, and which produced none of the
  // numbers on this screen. Baselines stay listed, just out of the race.
  const best   = sorted.find(r => r.type !== 'baseline') ?? null
  const caption = tOr(t, 'skus.metrics_table_caption',
    `Model accuracy for ${sku}, ordered by expected cost, best first.`, { sku })

  const numVals = (col: keyof MetricRow) =>
    sorted.map(r => r[col]).filter((v): v is number => typeof v === 'number')

  // A session trained before `cost` existed has the column on no row; showing an
  // all-dashes column there would only be noise.
  const hasCost = rows.some(r => r.cost_horizon != null || r.cost != null)
  const costOf = (r: MetricRow) => r.cost_horizon ?? r.cost ?? null

  const stats = {
    mae:  { min: Math.min(...numVals('mae')),  max: Math.max(...numVals('mae'))  },
    rmse: { min: Math.min(...numVals('rmse')), max: Math.max(...numVals('rmse')) },
    wape: { min: Math.min(...numVals('wape')), max: Math.max(...numVals('wape')) },
    bias: { min: Math.min(...numVals('bias')), max: Math.max(...numVals('bias')) },
    cost: {
      min: Math.min(...sorted.map(costOf).filter((v): v is number => v !== null)),
      max: Math.max(...sorted.map(costOf).filter((v): v is number => v !== null)),
    },
  }

  // Phones: one card per model instead of an eight-column table that scrolled
  // sideways and hid WAPE and bias. Same order, same badge, and the heat
  // colours are always on — a card has room for them without a second view.
  if (narrow) {
    const tile = (label: string, value: string, bg?: string, color?: string) => (
      <div style={{ minWidth: 0, padding: '6px 8px', borderRadius: 8, background: bg ?? 'var(--surface-2)' }}>
        <div style={{ fontSize: 11, color: 'var(--dim)' }}>{label}</div>
        <div style={{ fontSize: 14, fontWeight: 600, fontVariantNumeric: 'tabular-nums', color: color ?? 'var(--text)' }}>{value}</div>
      </div>
    )
    const biasMax = Math.max(...numVals('bias').map(Math.abs))
    return (
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        <p className="sr-only">{caption}</p>
        <div style={{ padding: '10px 12px', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', borderBottom: '1px solid var(--border)' }}>
          <span style={{ fontSize: 13, color: 'var(--dim)', flex: '1 1 100%' }}>
            {rows.length} {rows.length !== 1 ? t('skus.models_evaluated_plural') : t('skus.models_evaluated_singular')}
          </span>
          <button className="mobile-btn mobile-btn-secondary" onClick={() => exportMetricsCSV(t, sku, sorted)}>
            <Download size={16} aria-hidden="true" /> {t('skus.export_csv_short')}
          </button>
          <button className="mobile-btn mobile-btn-secondary" onClick={() => exportMetricsExcel(t, sku, sorted)}>
            <Download size={16} aria-hidden="true" /> {t('skus.export_excel_short')}
          </button>
        </div>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }} aria-label={caption}>
          {sorted.map((r, i) => (
            <li key={i} style={{
              padding: '12px', borderBottom: i < sorted.length - 1 ? '1px solid var(--border)' : undefined,
              background: r === best ? 'color-mix(in srgb, var(--accent) 6%, transparent)' : undefined,
            }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8, minWidth: 0 }}>
                <span style={{ fontSize: 15, fontWeight: r === best ? 700 : 600, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {modelLabel(t, r.model)}
                </span>
                {r === best && <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent)' }}>{t('skus.badge_best')}</span>}
                <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--dim)', flexShrink: 0 }}>{r.type}</span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 6 }}>
                {hasCost && tile(t('skus.col_cost'), fmt(costOf(r)), heatCell(costOf(r), stats.cost.min, stats.cost.max))}
                {tile(t('skus.col_mae'), fmt(r.mae), heatCell(r.mae, stats.mae.min, stats.mae.max))}
                {tile(t('skus.col_rmse'), fmt(r.rmse), heatCell(r.rmse, stats.rmse.min, stats.rmse.max))}
                {tile(t('skus.col_wape'), r.wape !== null ? pct(r.wape) : '—', heatCell(r.wape, stats.wape.min, stats.wape.max))}
                {tile(t('skus.col_bias'), fmt(r.bias),
                  heatCell(r.bias != null ? Math.abs(r.bias) : null, 0, biasMax),
                  r.bias !== null && r.bias > 0 ? '#f59e0b' : '#22c55e')}
                {tile(t('skus.col_folds'), String(r.n_folds ?? '—'))}
              </div>
            </li>
          ))}
        </ul>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{
        padding: '8px 16px', flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        borderBottom: '1px solid var(--border)',
      }}>
        <span style={{ fontSize: 11, color: 'var(--dim)' }}>
          {rows.length} {rows.length !== 1 ? t('skus.models_evaluated_plural') : t('skus.models_evaluated_singular')}
        </span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {/* View toggle */}
          <div style={{ display: 'flex', background: 'var(--surface-2)', borderRadius: 6, padding: 2, border: '1px solid var(--border)', gap: 1 }}>
            {(['table', 'heatmap'] as MetricViewMode[]).map(m => (
              <button
                key={m}
                title={m === 'table' ? t('skus.table_view_title') : t('skus.heatmap_view_title')}
                onClick={() => setViewMode(m)}
                style={{
                  all: 'unset', cursor: 'pointer',
                  padding: '3px 7px', borderRadius: 4, fontSize: 11,
                  display: 'flex', alignItems: 'center', gap: 3,
                  background: viewMode === m ? 'var(--accent)' : 'transparent',
                  color: viewMode === m ? '#fff' : 'var(--dim)',
                  transition: 'all 0.12s',
                }}
              >
                {m === 'table' ? <TableProperties size={11} /> : <Grid3x3 size={11} />}
                {m === 'table' ? t('skus.view_table') : t('skus.view_heatmap')}
              </button>
            ))}
          </div>
          <Button variant="ghost" size="sm" icon={<Download size={11} />} onClick={() => exportMetricsCSV(t, sku, sorted)}>{t('skus.export_csv_short')}</Button>
          <Button variant="ghost" size="sm" icon={<Download size={11} />} onClick={() => exportMetricsExcel(t, sku, sorted)}>{t('skus.export_excel_short')}</Button>
        </div>
      </div>

      <div style={{ overflow: 'auto', flex: 1 }}>
        {viewMode === 'heatmap' ? (
          <div style={{ padding: 16 }}>
            <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 12 }}>
              {t('skus.color_scale_label')}: <span style={{ color: '#22c55e' }}>{t('skus.color_scale_green')}</span> → <span style={{ color: '#ef4444' }}>{t('skus.color_scale_red')}</span>
            </div>
            <table className="data-table" style={{ tableLayout: 'fixed' }}>
              <caption className="sr-only">{caption}</caption>
              <thead>
                <tr>
                  <th scope="col" style={{ width: '20%' }}>{t('skus.col_model')}</th>
                  <th scope="col" style={{ width: '10%' }}>{t('skus.col_type')}</th>
                  {/* The table is ordered by expected cost, best first. Saying
                      so is the only way a screen-reader user learns why the
                      rows are in this order — and which column decided it. */}
                  {hasCost && (
                    <th scope="col" aria-sort="ascending" style={{ width: '15%' }} title={t('skus.col_cost_help')}>
                      {t('skus.col_cost')}
                    </th>
                  )}
                  <th scope="col" style={{ width: '15%' }}>{t('skus.col_mae')}</th>
                  <th scope="col" style={{ width: '15%' }}>{t('skus.col_rmse')}</th>
                  <th scope="col" style={{ width: '15%' }}>{t('skus.col_wape')}</th>
                  <th scope="col" style={{ width: '15%' }}>{t('skus.col_bias')}</th>
                  <th scope="col" style={{ width: '10%' }}>{t('skus.col_folds')}</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r, i) => (
                  <tr key={i}>
                    <th scope="row" style={{ fontWeight: r === best ? 600 : 400, textAlign: 'left' }}>
                      {modelLabel(t, r.model)}
                      {r === best && <span style={{ fontSize: 9, color: 'var(--accent)', marginLeft: 5 }}>{t('skus.badge_best')}</span>}
                    </th>
                    <td><span style={{ fontSize: 11, color: 'var(--dim)' }}>{r.type}</span></td>
                    {hasCost && (
                      <td style={{ fontFamily: 'monospace', background: heatCell(costOf(r), stats.cost.min, stats.cost.max), borderRadius: 4 }}>{fmt(costOf(r))}</td>
                    )}
                    <td style={{ fontFamily: 'monospace', background: heatCell(r.mae,  stats.mae.min,  stats.mae.max),  borderRadius: 4 }}>{fmt(r.mae)}</td>
                    <td style={{ fontFamily: 'monospace', background: heatCell(r.rmse, stats.rmse.min, stats.rmse.max), borderRadius: 4 }}>{fmt(r.rmse)}</td>
                    <td style={{ fontFamily: 'monospace', background: heatCell(r.wape, stats.wape.min, stats.wape.max), borderRadius: 4 }}>{r.wape !== null ? pct(r.wape) : '—'}</td>
                    <td style={{ fontFamily: 'monospace', background: heatCell(r.bias != null ? Math.abs(r.bias) : null, 0, Math.max(...numVals('bias').map(Math.abs))), borderRadius: 4, color: r.bias !== null && r.bias > 0 ? '#f59e0b' : '#22c55e' }}>{fmt(r.bias)}</td>
                    <td style={{ color: 'var(--dim)' }}>{r.n_folds ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <table className="data-table">
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr>
                <th scope="col">{t('skus.col_model')}</th>
                <th scope="col">{t('skus.col_type')}</th>
                {hasCost && (
                  <th scope="col" aria-sort="ascending" title={t('skus.col_cost_help')}>{t('skus.col_cost')}</th>
                )}
                <th scope="col">{t('skus.col_mae')}</th>
                <th scope="col">{t('skus.col_rmse')}</th>
                <th scope="col">{t('skus.col_wape')}</th>
                <th scope="col">{t('skus.col_bias')}</th>
                <th scope="col">{t('skus.col_folds')}</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, i) => (
                <tr key={i} style={{ background: r === best ? 'color-mix(in srgb, var(--accent) 6%, transparent)' : undefined }}>
                  <th scope="row" style={{ fontWeight: r === best ? 600 : 400, textAlign: 'left' }}>
                    {modelLabel(t, r.model)}{r === best && <span style={{ fontSize: 9, color: 'var(--accent)', marginLeft: 6 }}>{t('skus.badge_best')}</span>}
                  </th>
                  <td><span style={{ fontSize: 11, color: 'var(--dim)' }}>{r.type}</span></td>
                  {hasCost && <td style={{ fontFamily: 'monospace' }}>{fmt(costOf(r))}</td>}
                  <td style={{ fontFamily: 'monospace' }}>{fmt(r.mae)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmt(r.rmse)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{r.wape !== null ? pct(r.wape) : '—'}</td>
                  <td style={{ fontFamily: 'monospace', color: r.bias !== null && r.bias > 0 ? '#f59e0b' : '#22c55e' }}>{fmt(r.bias)}</td>
                  <td style={{ color: 'var(--dim)' }}>{r.n_folds ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
