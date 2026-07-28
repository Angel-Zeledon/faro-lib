'use client'
// Inter-warehouse transfers list + reception (feature 5.4).
// Mirrors the PO reception UX: partial quantities accumulate until complete.
import { useCallback, useEffect, useState } from 'react'
import { listTransfers, receiveTransfer, cancelTransfer, closeTransfer } from '@/lib/api'
import type { Transfer } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { ArrowLeftRight, PackageCheck, XCircle } from 'lucide-react'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', indigo: 'var(--accent)',
  green: '#22c55e', amber: '#f59e0b',
}

const STATUS_STYLE: Record<Transfer['status'], { color: string; key: string }> = {
  in_transit: { color: C.amber, key: 'transfers.status_in_transit' },
  partial:    { color: C.amber, key: 'transfers.status_partial' },
  received:   { color: C.green, key: 'transfers.status_received' },
  cancelled:  { color: C.dim,   key: 'transfers.status_cancelled' },
  closed:     { color: C.dim,   key: 'transfers.status_closed' },
}

function ReceiveForm({ transfer, onDone }: { transfer: Transfer; onDone: () => void }) {
  const { t } = useLanguage()
  const [qty, setQty] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  const outstanding = (it: Transfer['items'][number]) => it.qty_sent - it.qty_received

  async function submit(all: boolean) {
    setSaving(true)
    try {
      const lines = all ? null : transfer.items
        .filter(it => outstanding(it) > 0)
        .map(it => ({ sku: it.sku, received_qty: Number(qty[it.sku] ?? 0) }))
        .filter(l => l.received_qty > 0)
      await receiveTransfer(transfer.id, lines)
      onDone()
    } finally { setSaving(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '10px 12px',
                  borderTop: `1px dashed ${C.border}` }}>
      {transfer.items.filter(it => outstanding(it) > 0).map(it => (
        <label key={it.id} style={{ display: 'flex', alignItems: 'center', gap: 8,
                                    fontSize: 12.5, color: C.text }}>
          <span style={{ flex: 1 }}>{it.sku}</span>
          <span style={{ fontSize: 11, color: C.dim }}>
            {t('transfers.outstanding').replace('{qty}', String(outstanding(it)))}
          </span>
          <input type="number" min={0} max={outstanding(it)}
                 value={qty[it.sku] ?? ''}
                 placeholder={String(outstanding(it))}
                 onChange={e => setQty(q => ({ ...q, [it.sku]: e.target.value }))}
                 style={{ width: 70, background: 'transparent', border: `1px solid ${C.border}`,
                          borderRadius: 6, color: C.text, fontSize: 12, padding: '4px 7px' }} />
        </label>
      ))}
      <div style={{ display: 'flex', gap: 12 }}>
        <button onClick={() => submit(true)} disabled={saving}
                style={{ all: 'unset', cursor: 'pointer', fontSize: 12, fontWeight: 600, color: C.green }}>
          {t('transfers.receive_all')}
        </button>
        <button onClick={() => submit(false)} disabled={saving}
                style={{ all: 'unset', cursor: 'pointer', fontSize: 12, fontWeight: 600, color: C.indigo }}>
          {t('transfers.receive_partial')}
        </button>
      </div>
    </div>
  )
}

export function TransfersPanel() {
  const { t, lang } = useLanguage()
  const confirm = useConfirm()
  const [transfers, setTransfers] = useState<Transfer[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [receivingId, setReceivingId] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    listTransfers().then(setTransfers).catch(e => setError(e))
  }, [])
  useEffect(() => { load() }, [load])

  if (error) return <ErrorState error={error} onRetry={load} />
  if (transfers === null) return <LoadingState />
  if (transfers.length === 0) {
    return <EmptyState title={t('transfers.empty_title')} body={t('transfers.empty_sub')} />
  }

  async function onCancel(tr: Transfer) {
    const okd = await confirm({
      title: t('transfers.cancel_title'),
      message: t('transfers.cancel_msg')
        .replace('{from}', tr.from_warehouse).replace('{to}', tr.to_warehouse),
      danger: true,
    })
    if (!okd) return
    await cancelTransfer(tr.id)
    load()
  }

  async function onClose(tr: Transfer) {
    const missing = tr.items.reduce((s, it) => s + (it.qty_sent - it.qty_received), 0)
    const okd = await confirm({
      title: t('transfers.close_title'),
      message: t('transfers.close_msg').replace('{qty}', String(missing)),
      danger: true,
    })
    if (!okd) return
    await closeTransfer(tr.id)
    load()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {transfers.map(tr => {
        const st = STATUS_STYLE[tr.status]
        const receivable = tr.status === 'in_transit' || tr.status === 'partial'
        return (
          <div key={tr.id} style={{ borderRadius: 10, border: `1px solid ${C.border}`,
                                    background: C.surface }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px' }}>
              <ArrowLeftRight size={15} color={C.indigo} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>
                  {tr.from_warehouse} → {tr.to_warehouse}
                </div>
                <div style={{ fontSize: 11, color: C.dim }}>
                  {new Date(tr.created_at).toLocaleDateString(lang === 'es' ? 'es-CR' : 'en-US')}
                  {' · '}{tr.items.length} SKU{tr.items.length !== 1 ? 's' : ''}
                  {/* ETA from the lane's lead time, frozen at send time.
                      Absent on transfers created before lanes existed. */}
                  {receivable && tr.expected_arrival && (
                    <>{' · '}{t('transfers.eta')
                      .replace('{date}', new Date(tr.expected_arrival)
                        .toLocaleDateString(lang === 'es' ? 'es-CR' : 'en-US'))}</>
                  )}
                </div>
              </div>
              <span style={{ fontSize: 11.5, fontWeight: 700, color: st.color }}>
                {t(st.key)}
              </span>
              {receivable && (
                <>
                  <button onClick={() => setReceivingId(id => id === tr.id ? null : tr.id)}
                          style={{ all: 'unset', cursor: 'pointer', display: 'flex',
                                   alignItems: 'center', gap: 5, color: C.green,
                                   fontSize: 12, fontWeight: 600 }}>
                    <PackageCheck size={14} /> {t('transfers.receive_btn')}
                  </button>
                  {tr.status === 'in_transit' &&
                    tr.items.every(i => i.qty_received === 0) && (
                    <button onClick={() => onCancel(tr)}
                            aria-label={t('transfers.cancel_btn')}
                            style={{ all: 'unset', cursor: 'pointer', display: 'flex' }}>
                      <XCircle size={14} color={C.dim} />
                    </button>
                  )}
                  {tr.status === 'partial' && (
                    /* The remainder is lost: write it off as shrinkage and
                       stop the transfer from sitting in 'partial' forever. */
                    <button onClick={() => onClose(tr)}
                            style={{ all: 'unset', cursor: 'pointer', fontSize: 12,
                                     fontWeight: 600, color: C.dim }}>
                      {t('transfers.close_btn')}
                    </button>
                  )}
                </>
              )}
            </div>
            {receivingId === tr.id && receivable && (
              <ReceiveForm transfer={tr} onDone={() => { setReceivingId(null); load() }} />
            )}
          </div>
        )
      })}
    </div>
  )
}
