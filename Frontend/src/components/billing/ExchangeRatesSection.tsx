'use client'
/**
 * Exchange rates: what turns a supplier's price or an order in another currency
 * into the company's own money for totals and budget checks.
 *
 * Entered BY PEOPLE (an admin), never fetched from a feed, with an optional note
 * saying where the number came from. A rate says "1 USD = 520.50 CRC" from a
 * date on; the latest one not after the order's date is the one used, and an
 * order records the rate it used so a later rate never rewrites it. With no rate
 * a line in another currency is NOT converted (and never valued at 1): the order
 * says so, and a hard purchasing budget will not wave it through.
 *
 * The rate is exact decimal text end to end: it is typed as text, sent as text
 * and shown as text.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Plus, Trash2, Pencil, Check, X } from 'lucide-react'

import Spinner from '@/components/ui/Spinner'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { createExchangeRate, deleteExchangeRate, listExchangeRates, updateExchangeRate } from '@/lib/api'
import type { ExchangeRate, ExchangeRateList } from '@/lib/types'
import { getUser } from '@/lib/auth'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'

const field: React.CSSProperties = {
  padding: '7px 9px', borderRadius: 8, border: '1px solid var(--border-strong)',
  background: 'var(--surface)', color: 'var(--text)', fontSize: 12.5, minWidth: 0,
}

const today = () => new Date().toISOString().slice(0, 10)

export default function ExchangeRatesSection() {
  const { t } = useLanguage()
  const toast = useToast()
  const confirm = useConfirm()
  const isAdmin = getUser()?.role === 'admin'

  const [data, setData] = useState<ExchangeRateList | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [currency, setCurrency] = useState('')
  const [rate, setRate] = useState('')
  const [date, setDate] = useState(today())
  const [note, setNote] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [editRate, setEditRate] = useState('')

  const load = useCallback(() => {
    listExchangeRates({ limit: 500 }, { silent: true })
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [])
  useEffect(load, [load])

  const options = useMemo(
    () => (data?.supported ?? []).filter(c => c !== data?.base_currency),
    [data])

  if (loading) return <div style={{ padding: 8 }}><Spinner size={14} /></div>
  if (!data) return null
  const base = data.base_currency

  const add = async () => {
    if (!currency || !rate.trim()) return
    setBusy(true)
    try {
      await createExchangeRate({
        currency, rate: rate.trim(), effective_date: date || undefined,
        source_note: note.trim() || null,
      })
      setRate(''); setNote('')
      toast.addToast(t('fx.title'), t('fx.saved'), 'success')
      load()
    } catch {
      /* the interceptor surfaced it (errors.fx_*) */
    } finally {
      setBusy(false)
    }
  }

  const saveEdit = async (r: ExchangeRate) => {
    if (!editRate.trim()) return
    setBusy(true)
    try {
      await updateExchangeRate(r.id, { rate: editRate.trim() })
      setEditing(null)
      toast.addToast(t('fx.title'), t('fx.saved'), 'success')
      load()
    } catch {
      /* surfaced */
    } finally {
      setBusy(false)
    }
  }

  const remove = async (r: ExchangeRate) => {
    const ok = await confirm({
      title: t('fx.delete_title'),
      message: t('fx.delete_body', { currency: r.currency, date: r.effective_date }),
      confirmLabel: t('common.delete'),
      danger: true,
    })
    if (!ok) return
    setBusy(true)
    try {
      await deleteExchangeRate(r.id)
      load()
    } catch {
      /* surfaced */
    } finally {
      setBusy(false)
    }
  }

  const th: React.CSSProperties = {
    textAlign: 'left', padding: '6px 8px', fontSize: 10.5, color: 'var(--dim)',
    textTransform: 'uppercase', letterSpacing: '0.05em', borderBottom: '1px solid var(--border)',
  }
  const td: React.CSSProperties = { padding: '8px', fontSize: 12.5, color: 'var(--text)', borderBottom: '1px solid var(--border)' }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <p style={{ fontSize: 11.5, color: 'var(--dim)', margin: 0, lineHeight: 1.65, maxWidth: 640 }}>
        {t('fx.scope_hint', { base })}
      </p>

      {data.other_base_count > 0 && (
        <div style={{ display: 'flex', gap: 7, alignItems: 'flex-start', fontSize: 11.5, color: 'var(--warning)', lineHeight: 1.6, maxWidth: 640 }}>
          <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2 }} aria-hidden="true" />
          {t('fx.other_base', { n: data.other_base_count, base })}
        </div>
      )}

      {data.items.length === 0 ? (
        <p style={{ fontSize: 12, color: 'var(--muted)', margin: 0, lineHeight: 1.6, maxWidth: 640 }}>
          {t('fx.empty', { base })}
        </p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 480 }}>
            <thead>
              <tr>
                <th style={th}>{t('fx.col_currency')}</th>
                <th style={th}>{t('fx.col_rate', { base })}</th>
                <th style={th}>{t('fx.col_from')}</th>
                <th style={th}>{t('fx.col_source')}</th>
                <th style={th}>{t('fx.col_status')}</th>
                {isAdmin && <th style={th} aria-label={t('fx.col_actions')} />}
              </tr>
            </thead>
            <tbody>
              {data.items.map(r => (
                <tr key={r.id}>
                  <td style={{ ...td, fontWeight: 600 }}>{r.currency}</td>
                  <td style={{ ...td, fontFamily: 'monospace' }}>
                    {editing === r.id ? (
                      <input
                        value={editRate} onChange={e => setEditRate(e.target.value)}
                        inputMode="decimal" aria-label={t('fx.col_rate', { base })}
                        style={{ ...field, width: 110, fontFamily: 'monospace' }}
                      />
                    ) : `1 ${r.currency} = ${r.rate} ${base}`}
                  </td>
                  <td style={td}>{r.effective_date}</td>
                  <td style={{ ...td, color: 'var(--dim)' }}>{r.source_note || '—'}</td>
                  <td style={{ ...td, color: r.in_force ? 'var(--success, #2f855a)' : 'var(--dim)' }}>
                    {r.in_force ? t('fx.in_force') : (r.effective_date > today() ? t('fx.not_yet') : t('fx.superseded'))}
                  </td>
                  {isAdmin && (
                    <td style={{ ...td, whiteSpace: 'nowrap', textAlign: 'right' }}>
                      {editing === r.id ? (
                        <>
                          <button onClick={() => void saveEdit(r)} disabled={busy} aria-label={t('common.save')}
                            style={{ all: 'unset', cursor: 'pointer', marginRight: 8, color: 'var(--accent)' }}>
                            <Check size={14} aria-hidden="true" />
                          </button>
                          <button onClick={() => setEditing(null)} aria-label={t('common.cancel')}
                            style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)' }}>
                            <X size={14} aria-hidden="true" />
                          </button>
                        </>
                      ) : (
                        <>
                          <button onClick={() => { setEditing(r.id); setEditRate(r.rate) }} disabled={busy}
                            aria-label={t('fx.edit')} style={{ all: 'unset', cursor: 'pointer', marginRight: 10, color: 'var(--dim)' }}>
                            <Pencil size={13} aria-hidden="true" />
                          </button>
                          <button onClick={() => void remove(r)} disabled={busy}
                            aria-label={t('common.delete')} style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)' }}>
                            <Trash2 size={13} aria-hidden="true" />
                          </button>
                        </>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {isAdmin ? (
        <form
          onSubmit={e => { e.preventDefault(); void add() }}
          style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'flex-end' }}
        >
          <label style={{ display: 'grid', gap: 3, fontSize: 11, color: 'var(--dim)' }}>
            {t('fx.col_currency')}
            <select value={currency} onChange={e => setCurrency(e.target.value)} style={{ ...field, width: 100 }}>
              <option value="">{t('fx.pick_currency')}</option>
              {options.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label style={{ display: 'grid', gap: 3, fontSize: 11, color: 'var(--dim)' }}>
            {t('fx.form_rate', { base })}
            <input value={rate} onChange={e => setRate(e.target.value)} inputMode="decimal"
              placeholder="520.50" style={{ ...field, width: 120, fontFamily: 'monospace' }} />
          </label>
          <label style={{ display: 'grid', gap: 3, fontSize: 11, color: 'var(--dim)' }}>
            {t('fx.col_from')}
            <input type="date" value={date} onChange={e => setDate(e.target.value)} style={{ ...field, width: 140 }} />
          </label>
          <label style={{ display: 'grid', gap: 3, fontSize: 11, color: 'var(--dim)', flex: '1 1 160px' }}>
            {t('fx.col_source')}
            <input value={note} maxLength={200} onChange={e => setNote(e.target.value)}
              placeholder={t('fx.source_placeholder')} style={field} />
          </label>
          <button
            type="submit" disabled={busy || !currency || !rate.trim()}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 5, padding: '8px 14px', borderRadius: 8,
              border: 'none', fontSize: 12.5, fontWeight: 700, color: '#fff',
              cursor: busy || !currency || !rate.trim() ? 'not-allowed' : 'pointer',
              background: busy || !currency || !rate.trim()
                ? 'color-mix(in srgb, var(--accent) 35%, transparent)' : 'var(--accent)',
            }}
          >
            <Plus size={13} aria-hidden="true" /> {t('fx.add')}
          </button>
        </form>
      ) : (
        <p style={{ fontSize: 11.5, color: 'var(--dim)', margin: 0, lineHeight: 1.6 }}>{t('fx.admin_only')}</p>
      )}
    </div>
  )
}
