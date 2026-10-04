'use client'
/**
 * "What exactly do I upload?" — shown BEFORE the file picker on every import.
 *
 * It states the contract the importer enforces (it does not loosen it): the
 * required columns, the optional ones, a one-line meaning for each, a small
 * example table, a ready-to-fill template (CSV and XLSX) and a checklist to run
 * through before choosing the file. Required columns are always listed first
 * and badged, so nobody has to infer them from an error afterwards.
 *
 * The column lists mirror the backend/engine contract:
 *   · sales  — forecasting_core/data/canonical.py REQUIRED_FIELDS (sku, date, demand)
 *              and the headers of the downloadable template in lib/csvCheck.ts.
 *   · stock  — backend/api/v1/inventory.py `_TEMPLATE_COLUMNS`; only `sku` is required.
 * Header names are the Spanish aliases the template ships (CSV vocabulary is
 * deliberately Spanish, see CLAUDE.md); meanings are i18n keys.
 */
import { useState } from 'react'
import { Download, FileSpreadsheet, ChevronDown, ChevronRight, Check } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { MiniTable } from '@/components/ui/ExplainerVisual'
import { downloadWorkbook } from '@/lib/excel'
import { downloadCsvTemplate, CSV_TEMPLATE_HEADERS, CSV_TEMPLATE_ROWS } from '@/lib/csvCheck'
import { downloadInventoryTemplate } from '@/lib/api'

export type UploadKind = 'sales' | 'stock'

interface Col { name: string; required: boolean; example: string }

const SALES_COLS: Col[] = [
  { name: 'sku',        required: true,  example: 'SKU-001' },
  { name: 'fecha',      required: true,  example: '2026-01-01' },
  { name: 'demanda',    required: true,  example: '32' },
  { name: 'tienda',     required: false, example: 'Bodega Central' },
  { name: 'inventario', required: false, example: '480' },
  { name: 'costo',      required: false, example: '8.50' },
  { name: 'precio',     required: false, example: '12.90' },
]

const STOCK_COLS: Col[] = [
  { name: 'sku',             required: true,  example: 'SKU001' },
  { name: 'warehouse',       required: false, example: 'principal' },
  { name: 'display_name',    required: false, example: 'Agua 600ml' },
  { name: 'category',        required: false, example: 'Bebidas' },
  { name: 'brand',           required: false, example: 'AguaPura' },
  { name: 'unit_of_measure', required: false, example: 'caja' },
  { name: 'barcode',         required: false, example: '7501234567890' },
  { name: 'current_stock',   required: false, example: '120' },
  { name: 'min_stock',       required: false, example: '20' },
  { name: 'lead_time_days',  required: false, example: '7' },
  { name: 'unit_cost',       required: false, example: '3.50' },
  { name: 'sale_price',      required: false, example: '5.90' },
  { name: 'moq',             required: false, example: '12' },
  { name: 'supplier',        required: false, example: 'Distribuidora Sur' },
  { name: 'notes',           required: false, example: '' },
]

const NUMERIC_STOCK = /^(current_stock|min_stock|lead_time_days|unit_cost|sale_price|moq)$/

const CHECKLIST: Record<UploadKind, string[]> = {
  sales: ['header', 'one_row', 'date_format', 'numbers', 'history'],
  stock: ['header', 'one_row', 'numbers', 'sku_unique'],
}

export default function UploadGuide({ kind, defaultOpen = true }: { kind: UploadKind; defaultOpen?: boolean }) {
  const { t } = useLanguage()
  const [open, setOpen] = useState(defaultOpen)
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const cols = kind === 'sales' ? SALES_COLS : STOCK_COLS
  const required = cols.filter(c => c.required)
  const optional = cols.filter(c => !c.required)

  const downloadXlsx = () => {
    if (kind === 'sales') {
      const numeric = new Set([2, 4, 5, 6])
      void downloadWorkbook('plantilla_stockai_ventas.xlsx', [{
        name: 'ventas',
        rows: [
          [...CSV_TEMPLATE_HEADERS],
          ...CSV_TEMPLATE_ROWS.map(r => r.map((v, i) => (numeric.has(i) ? Number(v) : v))),
        ],
      }])
    } else {
      void downloadWorkbook('plantilla_stockai_inventario.xlsx', [{
        name: 'inventario',
        rows: [
          cols.map(c => c.name),
          cols.map(c => (NUMERIC_STOCK.test(c.name) ? Number(c.example) : c.example)),
        ],
      }])
    }
  }
  const downloadCsv = () => {
    if (kind === 'sales') downloadCsvTemplate()
    else void downloadInventoryTemplate().catch(() => { /* the wizard shows its own error line */ })
  }

  const exampleCols = kind === 'sales' ? cols.slice(0, 4) : cols.slice(0, 8)
  const exampleRows = kind === 'sales'
    ? [['SKU-001', '2026-01-01', '32', 'Bodega Central'], ['SKU-001', '2026-01-02', '28', 'Bodega Central'], ['SKU-002', '2026-01-01', '15', 'Bodega Central']]
    : [['SKU001', 'principal', 'Agua 600ml', 'Bebidas', 'AguaPura', 'caja', '7501234567890', '120'], ['SKU002', 'principal', 'Jugo 1L', 'Bebidas', 'AguaPura', 'caja', '7501234567891', '45']]

  const btn: React.CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: 6, padding: '7px 13px',
    borderRadius: 8, fontSize: 12.5, fontWeight: 600, cursor: 'pointer',
    background: 'transparent', color: 'var(--accent)', border: '1px solid var(--accent)',
  }

  const row = (c: Col) => (
    <tr key={c.name}>
      <td style={{ padding: '6px 10px 6px 0', verticalAlign: 'top', whiteSpace: 'nowrap' }}>
        <code style={{ fontSize: 12, fontWeight: 700, color: 'var(--text)' }}>{c.name}</code>
      </td>
      <td style={{ padding: '6px 10px 6px 0', verticalAlign: 'top', fontSize: 12, color: 'var(--muted)', lineHeight: 1.45 }}>
        {t(`upload_guide.${kind}.col.${c.name}`)}
      </td>
      <td style={{ padding: '6px 0', verticalAlign: 'top', fontSize: 11.5, color: 'var(--dim)', whiteSpace: 'nowrap' }}>{c.example}</td>
    </tr>
  )

  const groupHead = (label: string, accent: boolean, count: number) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '12px 0 2px' }}>
      <span style={{
        fontSize: 10.5, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase',
        padding: '2px 8px', borderRadius: 10,
        background: accent ? 'var(--accent-dim, var(--surface-3))' : 'var(--surface-3)',
        color: accent ? 'var(--accent)' : 'var(--dim)',
      }}>{label}</span>
      <span style={{ fontSize: 11.5, color: 'var(--dim)' }}>{count}</span>
    </div>
  )

  return (
    <section data-tour="upload.guide" style={{
      border: '1px solid var(--border)', borderRadius: 12, background: 'var(--surface)',
      padding: '14px 16px', marginBottom: 16, textAlign: 'left',
    }}>
      <button
        type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
        style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, width: '100%' }}
      >
        {open ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
        <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>{t(`upload_guide.${kind}.title`)}</span>
      </button>

      {open && (
        <div style={{ marginTop: 8 }}>
          <p style={{ margin: '0 0 4px', fontSize: 12.5, color: 'var(--dim)', lineHeight: 1.55 }}>
            {t(`upload_guide.${kind}.intro`, { count: required.length })}
          </p>

          {groupHead(t('upload_guide.required'), true, required.length)}
          <div style={{ overflowX: 'auto' }}><table style={{ borderCollapse: 'collapse', width: '100%' }}><tbody>{required.map(row)}</tbody></table></div>

          {groupHead(t('upload_guide.optional'), false, optional.length)}
          <div style={{ overflowX: 'auto' }}><table style={{ borderCollapse: 'collapse', width: '100%' }}><tbody>{optional.map(row)}</tbody></table></div>

          <div style={{ margin: '14px 0 4px', fontSize: 12, fontWeight: 650, color: 'var(--text)' }}>{t('upload_guide.example')}</div>
          <MiniTable
            headers={exampleCols.map(c => c.name)}
            highlight={exampleCols.map((c, i) => (c.required ? i : -1)).filter(i => i >= 0)}
            rows={exampleRows}
          />

          <div style={{ margin: '14px 0 6px', fontSize: 12, fontWeight: 650, color: 'var(--text)' }}>{t('upload_guide.checklist')}</div>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 5 }}>
            {CHECKLIST[kind].map(k => (
              <li key={k}>
                <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', cursor: 'pointer', fontSize: 12.5, color: checked[k] ? 'var(--dim)' : 'var(--text)', lineHeight: 1.45 }}>
                  <input
                    type="checkbox" checked={!!checked[k]}
                    onChange={e => setChecked(c => ({ ...c, [k]: e.target.checked }))}
                    style={{ marginTop: 2, accentColor: 'var(--accent)' }}
                  />
                  <span>{t(`upload_guide.${kind}.check.${k}`)}</span>
                  {checked[k] && <Check size={13} aria-hidden="true" style={{ color: 'var(--accent)', marginTop: 2 }} />}
                </label>
              </li>
            ))}
          </ul>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
            <button type="button" style={btn} onClick={downloadCsv}><Download size={14} aria-hidden="true" />{t('upload_guide.download_csv')}</button>
            <button type="button" style={btn} onClick={downloadXlsx}><FileSpreadsheet size={14} aria-hidden="true" />{t('upload_guide.download_xlsx')}</button>
          </div>
        </div>
      )}
    </section>
  )
}
