'use client'
/**
 * Approved purchase orders out to the company's ERP: pick the orders, pick the
 * layout the ERP imports, download it, and write back the ERP's own document
 * number so the order is never keyed twice.
 *
 * The list is the server's "approved, open, not yet confirmed by the ERP" set, so
 * an order waiting for approval is not offered here. A layout that was written
 * from the vendor's documentation and never run against a live system says so
 * before the file is downloaded.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { FileOutput, X } from 'lucide-react'
import {
  downloadErpFile, getErpFormats, getErpUnacknowledged, setErpReference,
} from '@/lib/api'
import type { ErpFormat, ErpOrder } from '@/lib/api'
import { useErrorDetail } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'

const C = {
  surface: 'var(--surface)', border: 'var(--border)', text: 'var(--text)',
  dim: 'var(--dim)', amber: '#B7791F', red: '#C0504D',
}

const smallBtn: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
  padding: '4px 11px', borderRadius: 7, fontSize: 12, fontWeight: 600,
  border: `1px solid ${C.border}`, color: C.text,
}

export default function ErpExportDialog({ onClose }: { onClose: () => void }) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const canWrite = getUser()?.role !== 'viewer'
  const [orders, setOrders] = useState<ErpOrder[] | null>(null)
  const [formats, setFormats] = useState<ErpFormat[]>([])
  const [format, setFormat] = useState('csv')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [refs, setRefs] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    Promise.all([getErpUnacknowledged({ silent: true }), getErpFormats({ silent: true })])
      .then(([o, f]) => { setOrders(o); setFormats(f) })
      .catch(e => { setOrders([]); setError(errorDetail(e)) })
  }, [errorDetail])
  useEffect(() => { load() }, [load])

  const chosen = useMemo(() => formats.find(f => f.key === format), [formats, format])
  const unmapped = useMemo(() => {
    const names = new Set<string>()
    for (const o of orders ?? []) if (picked.has(o.id))
      for (const d of o.documents) if (!d.supplier.mapped && d.supplier.name) names.add(d.supplier.name)
    return Array.from(names)
  }, [orders, picked])

  const toggle = (id: string) => setPicked(prev => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  })

  const download = async () => {
    if (!chosen || picked.size === 0) return
    setBusy(true); setError(null)
    try { await downloadErpFile(Array.from(picked), chosen.key, chosen.extension) }
    catch (e: unknown) { setError(errorDetail(e)) }
    finally { setBusy(false) }
  }

  const saveRef = async (orderId: string, supplierId: string | null, key: string) => {
    const value = (refs[key] ?? '').trim()
    if (!value) return
    setBusy(true); setError(null)
    try {
      await setErpReference(orderId, value, supplierId)
      setRefs(prev => { const n = { ...prev }; delete n[key]; return n })
      load()
    } catch (e: unknown) { setError(errorDetail(e)) }
    finally { setBusy(false) }
  }

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }}>
      <div role="dialog" aria-modal="true" aria-label={t('po.erp_title')}
           onClick={e => e.stopPropagation()}
           style={{ width: '100%', maxWidth: 720, maxHeight: '85vh', overflowY: 'auto',
                    background: C.surface, border: `1px solid ${C.border}`,
                    borderRadius: 14, padding: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <FileOutput size={16} aria-hidden="true" />
          <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>{t('po.erp_title')}</span>
          <button onClick={onClose} aria-label={t('common.close')}
                  style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', color: C.dim }}>
            <X size={16} />
          </button>
        </div>
        <p style={{ margin: '0 0 14px', fontSize: 12, color: C.dim }}>{t('po.erp_intro')}</p>

        {error && <p role="alert" style={{ margin: '0 0 10px', fontSize: 12, color: C.red }}>{error}</p>}

        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: C.dim,
                        marginBottom: 10 }}>
          {t('po.erp_format')}
          <select value={format} onChange={e => setFormat(e.target.value)}
                  style={{ padding: '6px 8px', borderRadius: 7, border: `1px solid ${C.border}`,
                           background: 'var(--surface-2)', color: C.text, fontSize: 12.5 }}>
            {formats.map(f => (
              <option key={f.key} value={f.key}>
                {f.label}{f.verified ? '' : ` - ${t('po.erp_unverified_short')}`}
              </option>
            ))}
          </select>
        </label>
        {chosen && !chosen.verified && (
          <p style={{ margin: '0 0 10px', fontSize: 12, color: C.amber }}>
            {t('po.erp_unverified')}
            {chosen.unmapped_fields.length > 0 &&
              ` ${t('po.erp_fill_fields', { fields: chosen.unmapped_fields.join(', ') })}`}
          </p>
        )}

        {orders === null ? (
          <p style={{ fontSize: 12.5, color: C.dim }}>{t('common.loading')}</p>
        ) : orders.length === 0 ? (
          <p style={{ fontSize: 12.5, color: C.dim }}>{t('po.erp_empty')}</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {orders.map(o => (
              <div key={o.id} style={{ border: `1px solid ${C.border}`, borderRadius: 10, padding: 10 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13,
                                fontWeight: 600, color: C.text, cursor: 'pointer' }}>
                  <input type="checkbox" checked={picked.has(o.id)} onChange={() => toggle(o.id)} />
                  {o.number}
                </label>
                {o.documents.map(d => {
                  const key = d.document_id
                  return (
                    <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 10,
                                            flexWrap: 'wrap', marginTop: 6, fontSize: 12, color: C.dim }}>
                      <span style={{ flex: '1 1 180px', minWidth: 0 }}>
                        {d.supplier.name ?? t('po.erp_no_supplier')} · {d.lines.length} {t('po.erp_lines')}
                        {d.expected_date ? ` · ${t('po.erp_expected')} ${d.expected_date}` : ''}
                      </span>
                      {!d.supplier.mapped && (
                        <span style={{ color: C.amber }}>{t('po.erp_unmapped')}</span>
                      )}
                      {d.mixed_currency && (
                        <span style={{ color: C.amber }}>{t('po.erp_mixed_currency')}</span>
                      )}
                      {canWrite && (
                        <span style={{ display: 'inline-flex', gap: 6 }}>
                          <input value={refs[key] ?? ''} placeholder={t('po.erp_reference_ph')}
                                 aria-label={t('po.erp_reference_label')}
                                 onChange={e => setRefs(prev => ({ ...prev, [key]: e.target.value }))}
                                 style={{ width: 150, padding: '4px 8px', borderRadius: 7, fontSize: 12,
                                          border: `1px solid ${C.border}`, background: 'var(--surface-2)',
                                          color: C.text }} />
                          <button type="button" disabled={busy || !(refs[key] ?? '').trim()}
                                  onClick={() => saveRef(o.id, d.supplier.id, key)}
                                  style={{ ...smallBtn, opacity: busy || !(refs[key] ?? '').trim() ? 0.5 : 1 }}>
                            {t('po.erp_reference_save')}
                          </button>
                        </span>
                      )}
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        )}

        {unmapped.length > 0 && (
          <p style={{ margin: '10px 0 0', fontSize: 12, color: C.amber }}>
            {t('po.erp_unmapped_hint', { suppliers: unmapped.join(', ') })}
          </p>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
          <button type="button" onClick={onClose} style={smallBtn}>{t('common.close')}</button>
          <button type="button" disabled={busy || picked.size === 0} onClick={download}
                  style={{ ...smallBtn, background: 'var(--accent)', color: '#fff',
                           border: '1px solid var(--accent)',
                           opacity: busy || picked.size === 0 ? 0.5 : 1 }}>
            {t('po.erp_download', { count: picked.size })}
          </button>
        </div>
      </div>
    </div>
  )
}
