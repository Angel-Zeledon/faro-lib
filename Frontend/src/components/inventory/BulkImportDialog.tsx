'use client'
/**
 * Bulk import of suppliers and of purchase orders, from a CSV or Excel file.
 *
 * One dialog for both screens, in the same four moves the stock importer uses:
 *   1. SEE what the file must look like — a downloadable template (CSV or XLSX),
 *      a column-by-column explanation and an example table;
 *   2. PICK the file — the backend answers with a dry run that writes nothing;
 *   3. CHECK the preview — which column was read as what (correctable), every
 *      row that will be refused and why, and for orders the orders that would
 *      be created;
 *   4. CONFIRM — and read what actually happened, row by row.
 *
 * Every sentence comes from the catalogue (`bulk.*`); the backend only ships a
 * code and params.
 */
import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, Check, Download, FileSpreadsheet, Upload, Upload as UploadIcon, X,
} from 'lucide-react'

import Button from '@/components/ui/Button'
import { useErrorDetail } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { commitBulkImport, downloadImportTemplate, previewBulkImport } from '@/lib/api'
import type {
  BulkImportKind, BulkImportMapping, BulkImportRowError,
  OrdersImportPreview, OrdersImportResult, SuppliersImportPreview, SuppliersImportResult,
} from '@/lib/bulkImportTypes'

const RED = '#ef4444'
const AMBER = '#f59e0b'
const GREEN = '#22c55e'
const MAX_SHOWN_ERRORS = 20

interface ColumnSpec { field: string; required?: boolean }

// What the backend reads, in the order the template lists it. The explanation
// of each column is `bulk.<kind>.col.<field>`.
const COLUMNS: Record<BulkImportKind, ColumnSpec[]> = {
  suppliers: [
    { field: 'name', required: true }, { field: 'email' }, { field: 'phone' },
    { field: 'whatsapp' }, { field: 'lead_time_days' }, { field: 'lead_time_std' },
    { field: 'review_period_days' }, { field: 'payment_terms' },
    { field: 'payment_terms_days' }, { field: 'notes' },
  ],
  orders: [
    { field: 'order_ref' }, { field: 'supplier', required: true },
    { field: 'sku', required: true }, { field: 'qty', required: true },
    { field: 'unit_cost' }, { field: 'warehouse' },
  ],
}

// The same rows the downloaded template carries: real-looking data, so the
// example table reads like the file the user will build. Names and payment
// terms are values a LatAm distributor actually types.
const EXAMPLES: Record<BulkImportKind, string[][]> = {
  suppliers: [
    ['Distribuidora Sur', 'ventas@distribuidorasur.com', '2222 3333', '50688887777',
     '7', '2', '14', '30 dias', '30', 'Entrega los martes'],
    ['Importadora Andina', 'pedidos@andina.example', '', '', '21', '5', '30', 'contado', '', ''],
  ],
  orders: [
    ['PO-1001', 'Distribuidora Sur', 'SKU001', '120', '3.50', 'principal'],
    ['PO-1001', 'Distribuidora Sur', 'SKU002', '48', '7.25', 'principal'],
    ['PO-1002', 'Importadora Andina', 'SKU003', '300', '', 'principal'],
  ],
}

const EXCEL_RE = /\.(xlsx|xlsm|xls)$/i

type Preview = SuppliersImportPreview | OrdersImportPreview
type Result = SuppliersImportResult | OrdersImportResult

export default function BulkImportDialog({ kind, onClose, onImported }: {
  kind: BulkImportKind
  onClose: () => void
  /** Called once the import wrote something, so the screen behind refreshes. */
  onImported?: () => void
}) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const narrow = useIsNarrow()
  const fileInput = useRef<HTMLInputElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [mapping, setMapping] = useState<BulkImportMapping>({})
  const [onExisting, setOnExisting] = useState<'skip' | 'update'>('skip')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState<'csv' | 'xlsx' | null>(null)

  // Escape closes, and focus starts inside the dialog.
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null
    panelRef.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onClose() }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); prev?.focus?.() }
  }, [onClose, busy])

  const columns = COLUMNS[kind]
  const fieldLabel = (f: string) => t(`bulk.${kind}.field.${f}`)

  async function download(format: 'csv' | 'xlsx') {
    setDownloading(format); setError(null)
    try { await downloadImportTemplate(kind, format) }
    catch (e) { setError(errorDetail(e) || t('common.error')) }
    finally { setDownloading(null) }
  }

  async function pick(f: File | null) {
    if (!f) return
    setFile(f); setPreview(null); setResult(null); setError(null); setMapping({})
    setBusy(true)
    try {
      const pv = await previewBulkImport<Preview>(kind, f, undefined, { silent: true })
      setPreview(pv); setMapping(pv.mapping)
    } catch (e) {
      setError(errorDetail(e) || t('bulk.error_generic'))
    } finally { setBusy(false) }
  }

  async function repreview(next: BulkImportMapping) {
    if (!file) return
    setMapping(next); setBusy(true); setError(null)
    try { setPreview(await previewBulkImport<Preview>(kind, file, next, { silent: true })) }
    catch (e) { setError(errorDetail(e) || t('bulk.error_generic')) }
    finally { setBusy(false) }
  }

  async function commit() {
    if (!file) return
    setBusy(true); setError(null)
    try {
      const res = await commitBulkImport<Result>(
        kind, file, mapping, { onExisting }, { silent: true })
      setResult(res)
      onImported?.()
    } catch (e) {
      setError(errorDetail(e) || t('bulk.error_generic'))
    } finally { setBusy(false) }
  }

  function reset() {
    setFile(null); setPreview(null); setResult(null); setError(null); setMapping({})
    if (fileInput.current) fileInput.current.value = ''
  }

  const missing = preview?.missing_required ?? []
  const isSuppliers = kind === 'suppliers'
  const sPreview = isSuppliers ? (preview as SuppliersImportPreview | null) : null
  const oPreview = !isSuppliers ? (preview as OrdersImportPreview | null) : null
  const nothingToDo = !preview || missing.length > 0 || preview.importable_rows === 0
    || (!isSuppliers && oPreview?.order_count === 0)

  const confirmLabel = busy ? t('bulk.importing')
    : isSuppliers
      ? t('bulk.suppliers.confirm', {
          count: (sPreview?.new_suppliers ?? 0)
            + (onExisting === 'update' ? (sPreview?.existing_suppliers ?? 0) : 0),
        })
      : t('bulk.orders.confirm', { count: oPreview?.order_count ?? 0 })

  return (
    <div
      role="presentation"
      onMouseDown={e => { if (e.target === e.currentTarget && !busy) onClose() }}
      style={{
        position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.45)',
        display: 'flex', alignItems: narrow ? 'flex-end' : 'center', justifyContent: 'center',
        padding: narrow ? 0 : 20,
      }}
    >
      <div
        ref={panelRef} tabIndex={-1} role="dialog" aria-modal="true"
        aria-label={t(`bulk.${kind}.title`)}
        style={{
          background: 'var(--surface)', color: 'var(--text)', outline: 'none',
          border: '1px solid var(--border)',
          borderRadius: narrow ? '16px 16px 0 0' : 14,
          width: '100%', maxWidth: 820, maxHeight: narrow ? '94vh' : '90vh',
          overflowY: 'auto', padding: narrow ? '16px 14px 24px' : '20px 24px 24px',
          boxSizing: 'border-box',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2 style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>{t(`bulk.${kind}.title`)}</h2>
            <p style={{ margin: '4px 0 0', fontSize: 12.5, color: 'var(--dim)', lineHeight: 1.5 }}>
              {t(`bulk.${kind}.subtitle`)}
            </p>
          </div>
          <button
            type="button" onClick={onClose} disabled={busy} aria-label={t('common.close')}
            style={{ all: 'unset', cursor: 'pointer', padding: 6, color: 'var(--dim)',
                     ...(narrow ? { minWidth: 44, minHeight: 44, textAlign: 'center' } : {}) }}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        {/* ── 1. What the file looks like ─────────────────────────────────── */}
        {!result && (
          <section style={{ marginTop: 16 }}>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>{t('bulk.template_title')}</span>
              <Button size="sm" icon={<Download size={13} />} loading={downloading === 'csv'}
                      onClick={() => void download('csv')}>
                {t('bulk.template_csv')}
              </Button>
              <Button size="sm" icon={<Download size={13} />} loading={downloading === 'xlsx'}
                      onClick={() => void download('xlsx')}>
                {t('bulk.template_xlsx')}
              </Button>
            </div>

            <details style={{ marginTop: 12 }} open={!file}>
              <summary style={{ cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
                {t('bulk.columns_title')}
              </summary>
              <p style={{ fontSize: 12, color: 'var(--dim)', margin: '6px 0 8px', lineHeight: 1.5 }}>
                {t(`bulk.${kind}.columns_hint`)}
              </p>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', fontSize: 12, width: '100%' }}>
                  <thead>
                    <tr>
                      {['bulk.col_header_column', 'bulk.col_header_required', 'bulk.col_header_meaning']
                        .map(k => (
                          <th key={k} style={{ textAlign: 'left', padding: '4px 10px 4px 0',
                                               color: 'var(--dim)', fontWeight: 600 }}>
                            {t(k)}
                          </th>
                        ))}
                    </tr>
                  </thead>
                  <tbody>
                    {columns.map(c => (
                      <tr key={c.field} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '5px 10px 5px 0', whiteSpace: 'nowrap',
                                     fontFamily: 'var(--font-mono, monospace)' }}>{c.field}</td>
                        <td style={{ padding: '5px 10px 5px 0', whiteSpace: 'nowrap',
                                     color: c.required ? RED : 'var(--dim)' }}>
                          {c.required ? t('bulk.required') : t('bulk.optional')}
                        </td>
                        <td style={{ padding: '5px 0', color: 'var(--text)', lineHeight: 1.45 }}>
                          {t(`bulk.${kind}.col.${c.field}`)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div style={{ fontSize: 12, fontWeight: 600, margin: '12px 0 4px' }}>
                {t('bulk.example_title')}
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', fontSize: 11.5 }}>
                  <thead>
                    <tr>
                      {columns.map(c => (
                        <th key={c.field} style={{ textAlign: 'left', padding: '4px 12px 4px 0',
                                                   color: 'var(--dim)', whiteSpace: 'nowrap',
                                                   fontFamily: 'var(--font-mono, monospace)' }}>
                          {c.field}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {EXAMPLES[kind].map((row, i) => (
                      <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                        {row.map((cell, j) => (
                          <td key={j} style={{ padding: '4px 12px 4px 0', whiteSpace: 'nowrap' }}>
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p style={{ fontSize: 11.5, color: 'var(--dim)', margin: '8px 0 0', lineHeight: 1.5 }}>
                {t(`bulk.${kind}.rules`)}
              </p>
            </details>
          </section>
        )}

        {/* ── 2. Pick the file ────────────────────────────────────────────── */}
        {!result && (
          <section style={{ marginTop: 16 }}>
            <input
              ref={fileInput} type="file" accept=".csv,.txt,.xlsx,.xlsm,.xls"
              style={{ display: 'none' }} data-testid="bulk-file-input"
              onChange={e => void pick(e.target.files?.[0] ?? null)}
            />
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <Button variant="primary" icon={<Upload size={14} />} loading={busy && !preview}
                      onClick={() => fileInput.current?.click()}>
                {file ? t('bulk.change_file') : t('bulk.pick_file')}
              </Button>
              {file && (
                <span style={{ fontSize: 12.5, display: 'inline-flex', gap: 6, minWidth: 0,
                               overflowWrap: 'anywhere', alignItems: 'center' }}>
                  <FileSpreadsheet size={14} color="var(--dim)" aria-hidden="true" />
                  {file.name}
                  {preview && (
                    <span style={{ color: 'var(--dim)' }}>
                      {' · '}{t('bulk.rows_in_file', { count: preview.total_rows })}
                      {' · '}{EXCEL_RE.test(file.name) ? 'Excel' : 'CSV'}
                    </span>
                  )}
                </span>
              )}
            </div>
          </section>
        )}

        {error && (
          <div role="alert" style={{ marginTop: 12, color: RED, fontSize: 12.5,
                                     display: 'flex', gap: 7 }}>
            <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}

        {/* ── 3. The preview ──────────────────────────────────────────────── */}
        {preview && !result && (
          <section style={{ marginTop: 16 }} aria-busy={busy}>
            {missing.length > 0 && (
              <div role="alert" style={{ color: RED, fontSize: 12.5, marginBottom: 10 }}>
                {t('bulk.missing_columns', { fields: missing.map(fieldLabel).join(', ') })}
              </div>
            )}

            <details style={{ marginBottom: 12 }} open={missing.length > 0}>
              <summary style={{ cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
                {t('bulk.mapping_title')}
              </summary>
              <div style={{ fontSize: 11.5, color: 'var(--dim)', margin: '3px 0 10px' }}>
                {t('bulk.mapping_hint')}
              </div>
              <div style={{ display: 'grid', gap: 8,
                            gridTemplateColumns: 'repeat(auto-fill, minmax(min(220px, 100%), 1fr))' }}>
                {preview.fields.map(field => {
                  const required = columns.find(c => c.field === field)?.required
                  const bad = required && !mapping[field]
                  return (
                    <label key={field} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                      <span style={{ fontSize: 11.5, color: bad ? RED : 'var(--dim)',
                                     fontWeight: required ? 700 : 500 }}>
                        {fieldLabel(field)}
                      </span>
                      <select
                        value={mapping[field] ?? ''} disabled={busy}
                        onChange={e => {
                          const next = { ...mapping }
                          if (e.target.value) next[field] = e.target.value
                          else delete next[field]
                          void repreview(next)
                        }}
                        style={{
                          padding: '5px 8px', fontSize: 12, borderRadius: 6,
                          border: `1px solid ${bad ? RED : 'var(--border)'}`,
                          background: 'var(--surface-2)', color: 'var(--text)',
                          ...(narrow ? { fontSize: 16, minHeight: 44, borderRadius: 10 } : {}),
                        }}
                      >
                        <option value="">{t('bulk.ignore_column')}</option>
                        {preview.columns.map(col => <option key={col} value={col}>{col}</option>)}
                      </select>
                    </label>
                  )
                })}
              </div>
            </details>

            {/* The headline numbers. */}
            <div style={{ fontSize: 13, display: 'flex', flexWrap: 'wrap', gap: '4px 14px' }}>
              {isSuppliers && sPreview && (
                <>
                  <span style={{ color: GREEN, fontWeight: 600 }}>
                    {t('bulk.suppliers.n_new', { count: sPreview.new_suppliers })}
                  </span>
                  {sPreview.existing_suppliers > 0 && (
                    <span>{t('bulk.suppliers.n_existing', { count: sPreview.existing_suppliers })}</span>
                  )}
                  {sPreview.deactivated_suppliers > 0 && (
                    <span style={{ color: AMBER }}>
                      {t('bulk.suppliers.n_deactivated', { count: sPreview.deactivated_suppliers })}
                    </span>
                  )}
                  {sPreview.duplicate_rows > 0 && (
                    <span style={{ color: AMBER }}>
                      {t('bulk.suppliers.n_duplicates', { count: sPreview.duplicate_rows })}
                    </span>
                  )}
                </>
              )}
              {!isSuppliers && oPreview && (
                <>
                  <span style={{ color: GREEN, fontWeight: 600 }}>
                    {t('bulk.orders.n_orders', { count: oPreview.order_count })}
                  </span>
                  {oPreview.already_imported_orders > 0 && (
                    <span style={{ color: AMBER }}>
                      {t('bulk.orders.n_already', { count: oPreview.already_imported_orders })}
                    </span>
                  )}
                  {oPreview.folded_rows > 0 && (
                    <span style={{ color: AMBER }}>
                      {t('bulk.orders.n_folded', { count: oPreview.folded_rows })}
                    </span>
                  )}
                </>
              )}
              {preview.rejected_rows > 0 && (
                <span style={{ color: RED, fontWeight: 600 }}>
                  {t('bulk.n_rejected', { count: preview.rejected_rows })}
                </span>
              )}
              {preview.blank_rows > 0 && (
                <span style={{ color: 'var(--dim)' }}>
                  {t('bulk.n_blank', { count: preview.blank_rows })}
                </span>
              )}
            </div>

            {/* Orders the file would create. */}
            {!isSuppliers && oPreview && oPreview.orders.length > 0 && (
              <div style={{ marginTop: 12, overflowX: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', fontSize: 12, width: '100%' }}>
                  <thead>
                    <tr>
                      {['order_ref', 'supplier', 'warehouse', 'lines', 'units', 'value'].map(h => (
                        <th key={h} style={{ textAlign: 'left', padding: '4px 10px 4px 0',
                                             color: 'var(--dim)', fontWeight: 600, whiteSpace: 'nowrap' }}>
                          {t(`bulk.orders.th.${h}`)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {oPreview.orders.map((o, i) => (
                      <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '4px 10px 4px 0' }}>
                          {o.order_ref || t('bulk.orders.no_ref')}
                          {o.already_imported && (
                            <span style={{ color: AMBER, marginLeft: 6 }}>
                              {t('bulk.orders.already_badge')}
                            </span>
                          )}
                        </td>
                        <td style={{ padding: '4px 10px 4px 0' }}>{o.supplier}</td>
                        <td style={{ padding: '4px 10px 4px 0' }}>
                          {o.warehouse ?? t('bulk.orders.default_warehouse')}
                        </td>
                        <td style={{ padding: '4px 10px 4px 0' }}>{o.line_count}</td>
                        <td style={{ padding: '4px 10px 4px 0' }}>{o.total_units}</td>
                        <td style={{ padding: '4px 0' }}>
                          {o.total_value == null ? '—' : o.total_value.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                          {o.lines_without_cost > 0 && o.total_value != null && (
                            <span style={{ color: AMBER, marginLeft: 6 }}
                                  title={t('bulk.orders.partial_cost_hint')}>*</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {oPreview.order_count > oPreview.orders.length && (
                  <div style={{ fontSize: 11.5, color: 'var(--dim)', marginTop: 4 }}>
                    {t('bulk.more_orders', { count: oPreview.order_count - oPreview.orders.length })}
                  </div>
                )}
              </div>
            )}

            {/* Suppliers the file would add. */}
            {isSuppliers && preview.sample_rows.length > 0 && (
              <div style={{ marginTop: 12, overflowX: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', fontSize: 11.5 }}>
                  <thead>
                    <tr>
                      {Object.keys(preview.sample_rows[0]).map(k => (
                        <th key={k} style={{ textAlign: 'left', padding: '4px 12px 4px 0',
                                             color: 'var(--dim)', whiteSpace: 'nowrap' }}>
                          {t(`bulk.suppliers.field.${k}`) === `bulk.suppliers.field.${k}`
                            ? k : t(`bulk.suppliers.field.${k}`)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.sample_rows.slice(0, 5).map((row, i) => (
                      <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                        {Object.keys(preview.sample_rows[0]).map(k => (
                          <td key={k} style={{ padding: '4px 12px 4px 0', whiteSpace: 'nowrap' }}>
                            {k === 'status' ? t(`bulk.suppliers.status.${String(row[k])}`) : String(row[k] ?? '')}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {isSuppliers && (sPreview?.existing_suppliers ?? 0) > 0 && (
              <fieldset style={{ border: '1px solid var(--border)', borderRadius: 8,
                                 margin: '12px 0 0', padding: '8px 12px' }}>
                <legend style={{ fontSize: 12.5, fontWeight: 600, padding: '0 4px' }}>
                  {t('bulk.suppliers.existing_question')}
                </legend>
                {(['skip', 'update'] as const).map(opt => (
                  <label key={opt} style={{ display: 'flex', gap: 8, alignItems: 'flex-start',
                                            fontSize: 12.5, padding: '3px 0', cursor: 'pointer',
                                            ...(narrow ? { minHeight: 44, alignItems: 'center' } : {}) }}>
                    <input type="radio" name="on_existing" checked={onExisting === opt}
                           onChange={() => setOnExisting(opt)} style={{ marginTop: 2 }} />
                    <span>
                      {t(`bulk.suppliers.existing_${opt}`)}
                      <span style={{ display: 'block', color: 'var(--dim)', fontSize: 11.5 }}>
                        {t(`bulk.suppliers.existing_${opt}_hint`)}
                      </span>
                    </span>
                  </label>
                ))}
              </fieldset>
            )}

            <RowErrors errors={preview.errors} total={preview.error_count} />

            <Button variant="primary" loading={busy}
                    style={{ marginTop: 14, ...(narrow ? { width: '100%' } : {}) }}
                    disabled={nothingToDo} onClick={() => void commit()}>
              {confirmLabel}
            </Button>
          </section>
        )}

        {/* ── 4. The result ───────────────────────────────────────────────── */}
        {result && (
          <section style={{ marginTop: 16 }} aria-live="polite">
            <div style={{ display: 'flex', gap: 8, color: GREEN, fontSize: 14, fontWeight: 600 }}>
              <Check size={16} style={{ flexShrink: 0, marginTop: 2 }} aria-hidden="true" />
              <span>
                {isSuppliers
                  ? t('bulk.suppliers.done', {
                      created: (result as SuppliersImportResult).created,
                      updated: (result as SuppliersImportResult).updated,
                    })
                  : t('bulk.orders.done', { count: (result as OrdersImportResult).created_orders })}
              </span>
            </div>
            <ul style={{ margin: '8px 0 0', paddingLeft: 20, fontSize: 12.5, lineHeight: 1.7 }}>
              {isSuppliers && (result as SuppliersImportResult).skipped_existing > 0 && (
                <li>{t('bulk.suppliers.r_skipped', { count: (result as SuppliersImportResult).skipped_existing })}</li>
              )}
              {isSuppliers && (result as SuppliersImportResult).duplicate_rows > 0 && (
                <li>{t('bulk.suppliers.n_duplicates', { count: (result as SuppliersImportResult).duplicate_rows })}</li>
              )}
              {!isSuppliers && (result as OrdersImportResult).already_imported_orders > 0 && (
                <li>{t('bulk.orders.r_already', { count: (result as OrdersImportResult).already_imported_orders })}</li>
              )}
              {!isSuppliers && (result as OrdersImportResult).folded_rows > 0 && (
                <li>{t('bulk.orders.n_folded', { count: (result as OrdersImportResult).folded_rows })}</li>
              )}
              {result.blank_rows > 0 && <li>{t('bulk.n_blank', { count: result.blank_rows })}</li>}
            </ul>
            {!isSuppliers && (result as OrdersImportResult).orders.length > 0 && (
              <div style={{ marginTop: 8, fontSize: 12.5 }}>
                {(result as OrdersImportResult).orders.slice(0, 10).map(o => (
                  <div key={o.id}>
                    <strong>{o.po_number}</strong>
                    {' · '}{o.supplier}{' · '}{t('bulk.orders.n_lines', { count: o.line_count })}
                  </div>
                ))}
              </div>
            )}
            {result.error_count > 0 && (
              <div style={{ marginTop: 10, color: AMBER, fontSize: 12.5, fontWeight: 600 }}>
                {t('bulk.result_errors', { count: result.error_count })}
              </div>
            )}
            <RowErrors errors={result.errors} total={result.error_count} />
            <div style={{ display: 'flex', gap: 8, marginTop: 16, flexWrap: 'wrap' }}>
              <Button variant="primary" onClick={onClose}>{t('bulk.close')}</Button>
              <Button icon={<UploadIcon size={13} />} onClick={reset}>{t('bulk.import_another')}</Button>
            </div>
          </section>
        )}
      </div>
    </div>
  )
}

/** The rows that were refused, by line number, with the reason in words. */
function RowErrors({ errors, total }: { errors: BulkImportRowError[]; total: number }) {
  const { t } = useLanguage()
  if (!errors || errors.length === 0) return null
  const shown = errors.slice(0, MAX_SHOWN_ERRORS)

  const message = (e: BulkImportRowError) => {
    const key = `bulk.row.${e.code}`
    const text = t(key, { ...e.params, ref: e.ref })
    return text === key ? e.error : text
  }

  return (
    <div style={{ marginTop: 12, border: `1px solid ${AMBER}55`, borderLeft: `3px solid ${AMBER}`,
                  borderRadius: 8, padding: '8px 12px' }}>
      <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 4 }}>{t('bulk.errors_title')}</div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 12, width: '100%' }}>
          <thead>
            <tr>
              {['bulk.th_row', 'bulk.th_ref', 'bulk.th_problem'].map(k => (
                <th key={k} style={{ textAlign: 'left', padding: '3px 12px 3px 0',
                                     color: 'var(--dim)', fontWeight: 600, whiteSpace: 'nowrap' }}>
                  {t(k)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((e, i) => (
              <tr key={`${e.row}-${i}`} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '3px 12px 3px 0', whiteSpace: 'nowrap' }}>{e.row}</td>
                <td style={{ padding: '3px 12px 3px 0', overflowWrap: 'anywhere' }}>{e.ref || '—'}</td>
                <td style={{ padding: '3px 0', lineHeight: 1.4 }}>{message(e)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {total > shown.length && (
        <div style={{ fontSize: 11.5, color: 'var(--dim)', marginTop: 4 }}>
          {t('bulk.and_more_errors', { count: total - shown.length })}
        </div>
      )}
    </div>
  )
}
