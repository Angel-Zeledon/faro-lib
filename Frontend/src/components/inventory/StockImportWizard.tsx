'use client'
/**
 * Stock importer with the same manners as the sales one (friction plan #1,
 * phase 4).
 *
 * The old flow demanded our exact headers, which no LatAm ERP produces — so it
 * went unused. Here the file is checked in the browser first (separator,
 * comma decimals, garbage cells — `validateStockCsv`), then the backend
 * previews it and says which column it read as what, and the user corrects
 * that mapping before anything is written. Excel goes straight to the preview,
 * since only the server can read it.
 */
import { useRef, useState } from 'react'
import { AlertTriangle, Check, FileSpreadsheet, Upload } from 'lucide-react'

import Button from '@/components/ui/Button'
import { useSetupCopy } from '@/i18n/useSetupCopy'
import { importStockFile, previewStockImport } from '@/lib/api'
import { isApiError } from '@/lib/api'
import { validateStockCsv, type StockCsvCheckResult } from '@/lib/csvCheck'
import type {
  StockImportMapping, StockImportPreview, StockImportResult,
} from '@/lib/stockSetupTypes'

const RED   = '#ef4444'
const AMBER = '#f59e0b'
const GREEN = '#22c55e'

const EXCEL_RE = /\.(xlsx|xlsm|xls)$/i

export default function StockImportWizard({ onImported }: { onImported?: () => void }) {
  const c = useSetupCopy()
  const fileInput = useRef<HTMLInputElement>(null)

  const [file, setFile]         = useState<File | null>(null)
  const [localCheck, setLocal]  = useState<StockCsvCheckResult | null>(null)
  const [preview, setPreview]   = useState<StockImportPreview | null>(null)
  const [mapping, setMapping]   = useState<StockImportMapping>({})
  const [busy, setBusy]         = useState(false)
  const [result, setResult]     = useState<StockImportResult | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)
  const [errorParams, setErrorParams] = useState<Record<string, string | number>>({})

  function reset() {
    setLocal(null); setPreview(null); setMapping({}); setResult(null)
    setErrorKey(null); setErrorParams({})
  }

  async function pick(f: File | null) {
    if (!f) return
    reset()
    setFile(f)
    setBusy(true)
    try {
      if (!EXCEL_RE.test(f.name)) {
        // Browser-side check first: it names the row and the column before the
        // file leaves the machine, which is the whole value of doing it here.
        setLocal(validateStockCsv(await f.text()))
      }
      const pv = await previewStockImport(f, undefined, { silent: true })
      setPreview(pv)
      setMapping(pv.mapping)
    } catch (e) {
      setErrorKey(isApiError(e) && e.code ? `errors.${e.code}` : 'errors.inventory_import_unreadable_file')
      setErrorParams(isApiError(e) ? (e.params as Record<string, string | number>) : {})
    } finally {
      setBusy(false)
    }
  }

  async function repreview(next: StockImportMapping) {
    if (!file) return
    setMapping(next)
    setBusy(true)
    try {
      const pv = await previewStockImport(file, next, { silent: true })
      setPreview(pv)
    } catch (e) {
      setErrorKey(isApiError(e) && e.code ? `errors.${e.code}` : 'errors.inventory_import_bad_mapping')
      setErrorParams(isApiError(e) ? (e.params as Record<string, string | number>) : {})
    } finally {
      setBusy(false)
    }
  }

  async function commit() {
    if (!file) return
    setBusy(true); setErrorKey(null)
    try {
      const res = await importStockFile(file, mapping, { silent: true })
      setResult(res)
      onImported?.()
    } catch (e) {
      setErrorKey(isApiError(e) && e.code ? `errors.${e.code}` : 'errors.inventory_import_no_valid_rows')
      setErrorParams(isApiError(e) ? (e.params as Record<string, string | number>) : {})
    } finally {
      setBusy(false)
    }
  }

  const missingSku = preview ? preview.missing_required.includes('sku') : false

  return (
    <section style={{
      border: '1px solid var(--border)', borderRadius: 12,
      background: 'var(--surface)', padding: '18px 20px',
    }}>
      <h2 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', margin: 0 }}>
        {c('setupStock.import.title')}
      </h2>
      <p style={{ fontSize: 12.5, color: 'var(--dim)', margin: '6px 0 12px', lineHeight: 1.5 }}>
        {c('setupStock.import.subtitle')}
      </p>

      <input
        ref={fileInput} type="file" accept=".csv,.txt,.xlsx,.xlsm,.xls"
        style={{ display: 'none' }}
        onChange={e => void pick(e.target.files?.[0] ?? null)}
      />
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <Button variant="primary" icon={<Upload size={14} />} loading={busy && !preview}
                onClick={() => fileInput.current?.click()}>
          {file ? c('setupStock.import.change') : c('setupStock.import.pick')}
        </Button>
        {file && (
          <span style={{ fontSize: 12.5, color: 'var(--text)', display: 'inline-flex', gap: 6 }}>
            <FileSpreadsheet size={14} color="var(--dim)" />
            {c('setupStock.import.rows_found', {
              count: preview?.total_rows ?? localCheck?.rowCount ?? 0, name: file.name,
            })}
          </span>
        )}
      </div>

      {errorKey && (
        <div style={{ marginTop: 12, color: RED, fontSize: 12.5, display: 'flex', gap: 7 }}>
          <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>{c(errorKey, errorParams)}</span>
        </div>
      )}

      {/* Browser-side findings: named rows and columns, before uploading. */}
      {localCheck && localCheck.groups.length > 0 && (
        <div style={{
          marginTop: 12, border: `1px solid ${AMBER}44`, borderLeft: `3px solid ${AMBER}`,
          borderRadius: 8, padding: '10px 12px',
        }}>
          {localCheck.groups.map(g => (
            <div key={g.kind} style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}>
                {c(g.titleKey)} ({g.count})
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--dim)', margin: '2px 0 4px' }}>
                {c(g.hintKey)}
              </div>
              {g.samples.map(s => (
                <div key={`${s.kind}-${s.row}`} style={{ fontSize: 11.5, color: 'var(--dim)' }}>
                  {c(s.key, s.params)}
                </div>
              ))}
              {g.hidden > 0 && (
                <div style={{ fontSize: 11.5, color: 'var(--dim)' }}>
                  {c('csvStock.and_more', { count: g.hidden })}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {preview && !result && (
        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
            {c('setupStock.import.mapping_title')}
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--dim)', margin: '3px 0 10px' }}>
            {c('setupStock.import.mapping_hint')}
          </div>

          <div style={{
            display: 'grid', gap: 8,
            gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))',
          }}>
            {preview.fields.map(field => (
              <label key={field} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                <span style={{
                  fontSize: 11.5,
                  color: field === 'sku' && missingSku ? RED : 'var(--dim)',
                  fontWeight: field === 'sku' ? 700 : 500,
                }}>
                  {c(`setupStock.import.field.${field}`)}
                </span>
                <select
                  value={mapping[field] ?? ''}
                  onChange={e => {
                    const next = { ...mapping }
                    if (e.target.value) next[field] = e.target.value
                    else delete next[field]
                    void repreview(next)
                  }}
                  style={{
                    padding: '5px 8px', fontSize: 12, borderRadius: 6,
                    border: `1px solid ${field === 'sku' && missingSku ? RED : 'var(--border)'}`,
                    background: 'var(--surface-2)', color: 'var(--text)',
                  }}
                >
                  <option value="">{c('setupStock.import.ignore')}</option>
                  {preview.columns.map(col => (
                    <option key={col} value={col}>{col}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>

          {missingSku && (
            <div style={{ marginTop: 10, color: RED, fontSize: 12.5 }}>
              {c('setupStock.import.missing_sku')}
            </div>
          )}

          <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--text)' }}>
            {c('setupStock.import.ready', { count: preview.importable_rows })}
            {preview.rejected_rows > 0 && (
              <span style={{ color: AMBER }}>
                {' · '}{c('setupStock.import.rejected', { count: preview.rejected_rows })}
              </span>
            )}
            {preview.skipped_no_sku > 0 && (
              <span style={{ color: 'var(--dim)' }}>
                {' · '}{c('setupStock.import.skipped_no_sku', { count: preview.skipped_no_sku })}
              </span>
            )}
          </div>

          {preview.issues.map(issue => (
            <div key={`${issue.code}-${issue.column}`}
                 style={{ marginTop: 6, fontSize: 12, color: AMBER }}>
              {c(`setupStock.import.issue.${issue.code}`, {
                count: issue.count,
                // Name the column the USER sees in their file, not our
                // canonical field: "Existencia", not "current_stock".
                column: (issue.column && mapping[issue.column]) || issue.column || '',
              })}
              {issue.samples.slice(0, 3).map(s => (
                <div key={`${s.row}`} style={{ color: 'var(--dim)', fontSize: 11.5 }}>
                  {c('setupStock.import.issue.sample', {
                    row: s.row, sku: s.sku, value: String(s.value ?? ''),
                  })}
                </div>
              ))}
            </div>
          ))}

          <Button
            variant="primary" style={{ marginTop: 14 }} loading={busy}
            disabled={missingSku || preview.importable_rows === 0}
            onClick={() => void commit()}
          >
            {busy
              ? c('setupStock.import.importing')
              : c('setupStock.import.confirm', { count: preview.importable_rows })}
          </Button>
        </div>
      )}

      {result && (
        <div style={{ marginTop: 14, fontSize: 13, color: GREEN, display: 'flex', gap: 7 }}>
          <Check size={15} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>
            {result.error_count
              ? c('setupStock.import.done_with_errors', {
                  count: result.imported, failed: result.error_count,
                })
              : c('setupStock.import.done', { count: result.imported })}
          </span>
        </div>
      )}
    </section>
  )
}
