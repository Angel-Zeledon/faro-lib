'use client'
/**
 * Contract renewals: which active blanket contracts end soon (or already did),
 * how each performed against what it committed (units committed vs delivered,
 * fill rate, late deliveries), and the Renew action, which creates the next
 * term as a NEW DRAFT contract for the buyer to review before activating.
 *
 * The maths (buckets, fill rate, late deliveries, the renewed term) is the
 * server's: this component only shows it. The routes are served by the Rust
 * API; where it is not deployed the old Python answers "contract not found" to
 * the literal id `renewals`, which would read as a lie, so that case is turned
 * into "not available here" instead.
 */
import { useCallback, useEffect, useState } from 'react'
import { CalendarClock, ChevronDown, ChevronRight, RefreshCw } from 'lucide-react'
import { getContractComparison, getContractRenewals, isApiError, renewSupplyContract } from '@/lib/api'
import type {
  ContractComparisonResponse, ContractRenewalBucket, ContractRenewalItem, ContractRenewalList,
} from '@/lib/types'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { getUser } from '@/lib/auth'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D', amber: '#B7791F', green: '#2E7D5B' }
const WITHIN_OPTIONS = [30, 60, 90, 180, 365]
const BUCKET_COLOR: Record<ContractRenewalBucket, string> = {
  expired: C.red, notice_passed: C.red, due_soon: C.amber, upcoming: C.muted,
}

export default function ContractRenewalsPanel({ onChanged, reloadToken }: { onChanged?: () => void; reloadToken?: number }) {
  const { t, lang } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const narrow = useIsNarrow()
  const role = getUser()?.role
  const canWrite = role === 'admin' || role === 'analyst'

  const [open, setOpen] = useState(false)
  const [within, setWithin] = useState(90)
  const [data, setData] = useState<ContractRenewalList | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [compare, setCompare] = useState<Record<string, ContractComparisonResponse | 'loading' | { error: string }>>({})

  const load = useCallback(() => {
    getContractRenewals(within)
      .then(r => { setData(r); setLoadError(null); setUnavailable(false) })
      .catch(e => {
        setData(null)
        if (isApiError(e) && e.code === 'supply_contract_not_found') { setUnavailable(true); setLoadError(null) }
        else { setUnavailable(false); setLoadError(errorDetail(e)) }
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [within])
  useEffect(() => { if (open) load() }, [open, load, reloadToken])

  const fmt = (n: number) => n.toLocaleString(lang === 'en' ? 'en-US' : 'es-CO', { maximumFractionDigits: 2 })
  const btn = (primary = false, disabled = false): React.CSSProperties => ({
    all: 'unset', cursor: disabled ? 'default' : 'pointer', boxSizing: 'border-box', display: 'inline-flex',
    alignItems: 'center', gap: 5, padding: narrow ? '0 14px' : '5px 12px', minHeight: narrow ? 44 : undefined,
    borderRadius: narrow ? 10 : 7, fontSize: narrow ? 14 : 12, fontWeight: 600, opacity: disabled ? 0.5 : 1,
    border: `1px solid ${C.border}`, color: C.text,
    ...(primary ? { background: 'color-mix(in srgb, var(--accent) 10%, transparent)' } : {}),
  })
  const chip = (color: string): React.CSSProperties => ({
    fontSize: 10.5, fontWeight: 700, color, border: `1px solid ${color}`, borderRadius: 6, padding: '1px 6px',
  })
  const card: React.CSSProperties = { background: 'var(--surface)', border: `1px solid ${C.border}`, borderRadius: 8, padding: '12px 16px' }

  const daysText = (n: number) =>
    n < 0 ? t('renewals.days_overdue', { n: -n }) : n === 0 ? t('renewals.expires_today') : t('renewals.days_left', { n })
  const leadsText = (it: ContractRenewalItem) => it.renewal_lead_days_is_default
    ? t('renewals.default_leads', { days: it.renewal_lead_days_effective.join(' / ') })
    : t('renewals.custom_leads', { days: it.renewal_lead_days_effective.join(' / ') })

  async function toggleCompare(it: ContractRenewalItem) {
    if (compare[it.root_id]) {
      setCompare(c => { const n = { ...c }; delete n[it.root_id]; return n })
      return
    }
    setCompare(c => ({ ...c, [it.root_id]: 'loading' }))
    try {
      const r = await getContractComparison(it.root_id)
      setCompare(c => ({ ...c, [it.root_id]: r }))
    } catch (e: unknown) {
      setCompare(c => ({ ...c, [it.root_id]: { error: errorDetail(e) } }))
    }
  }

  async function renew(it: ContractRenewalItem) {
    const ok = await confirm({
      title: t('renewals.renew_confirm_title'),
      message: t('renewals.renew_confirm_message', { customer: it.customer }),
      confirmLabel: t('renewals.renew_confirm_label'),
      cancelLabel: t('contracts.confirm_keep'),
    })
    if (!ok) return
    setBusy(true); setError(null); setNotice(null)
    try {
      const r = await renewSupplyContract(it.root_id, it.revision)
      setNotice(t(r.prices_carried ? 'renewals.renewed_done_prices' : 'renewals.renewed_done',
        { customer: r.customer, start: r.period_start, end: r.period_end }))
      load(); onChanged?.()
    } catch (e: unknown) {
      setError(errorDetail(e))
    } finally { setBusy(false) }
  }

  const comparisonTable = (r: ContractComparisonResponse) => {
    const c = r.comparison
    return (
      <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12, color: C.text }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 14px', color: C.dim }}>
          <span>{t('renewals.cmp_term', { elapsed: r.elapsed_days, total: r.term_days })}</span>
          <span>{t('renewals.cmp_committed', { units: fmt(c.committed_units) })}</span>
          <span>{t('renewals.cmp_due', { units: fmt(c.due_to_date) })}</span>
          <span>{t('renewals.cmp_delivered', { units: fmt(c.delivered_units) })}</span>
          <span>{t('renewals.cmp_shortfall', { units: fmt(c.shortfall_to_date) })}</span>
          <span>{c.fill_rate_pct == null ? t('renewals.fill_rate_none') : t('renewals.cmp_fill', { pct: fmt(c.fill_rate_pct) })}</span>
          {c.term_fill_pct != null && <span>{t('renewals.cmp_term_fill', { pct: fmt(c.term_fill_pct) })}</span>}
          <span style={{ color: c.late_deliveries > 0 ? C.amber : C.dim }}>
            {t('renewals.cmp_late', { n: c.late_deliveries, units: fmt(c.late_units), days: c.max_days_late })}
          </span>
          <span style={{ color: c.overdue_open > 0 ? C.amber : C.dim }}>
            {t('renewals.cmp_overdue', { n: c.overdue_open, units: fmt(c.overdue_open_units) })}
          </span>
        </div>
        {c.fulfilled_undated > 0 && (
          <div style={{ color: C.amber }}>{t('renewals.undated_hint', { n: c.fulfilled_undated })}</div>
        )}
        <div style={{ overflowX: 'auto', border: `1px solid ${C.border}`, borderRadius: 6 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 420 }}>
            <thead>
              <tr style={{ color: C.muted, textAlign: 'left' }}>
                <th style={{ padding: '4px 8px', fontWeight: 600 }}>{t('contracts.col_sku')}</th>
                <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }}>{t('renewals.col_committed')}</th>
                <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }}>{t('renewals.col_delivered')}</th>
                <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }}>{t('renewals.col_fill')}</th>
                <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }}>{t('renewals.col_late')}</th>
              </tr>
            </thead>
            <tbody>
              {c.lines.map(l => (
                <tr key={l.sku} style={{ borderTop: `1px solid ${C.border}` }}>
                  <td style={{ padding: '4px 8px', overflowWrap: 'anywhere' }}>{l.sku}</td>
                  <td style={{ padding: '4px 8px', textAlign: 'right' }}>{fmt(l.committed)}</td>
                  <td style={{ padding: '4px 8px', textAlign: 'right' }}>{fmt(l.delivered)}</td>
                  <td style={{ padding: '4px 8px', textAlign: 'right' }}>{l.fill_rate_pct == null ? '-' : `${fmt(l.fill_rate_pct)}%`}</td>
                  <td style={{ padding: '4px 8px', textAlign: 'right' }}>{l.late_deliveries}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    )
  }

  return (
    <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
        style={{ ...btn(), border: 'none', padding: 0, fontWeight: 700, fontSize: 13.5 }}>
        {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
        <CalendarClock size={15} color="var(--accent)" aria-hidden="true" />
        {t('renewals.title')}
      </button>
      {open && (
        <>
          <p style={{ margin: 0, fontSize: 12.5, color: C.dim, lineHeight: 1.5 }}>{t('renewals.intro')}</p>
          <label style={{ fontSize: narrow ? 13 : 11.5, color: C.muted, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            {t('renewals.within_label')}
            <select value={within} onChange={e => setWithin(Number(e.target.value))}
              style={{ fontSize: narrow ? 16 : 12.5, padding: narrow ? '10px' : '4px 8px', borderRadius: 7, border: `1px solid ${C.border}`, background: 'var(--surface-2)', color: C.text, minHeight: narrow ? 44 : undefined }}>
              {WITHIN_OPTIONS.map(n => <option key={n} value={n}>{t('renewals.within_option', { n })}</option>)}
            </select>
          </label>
          {notice && <p role="status" style={{ margin: 0, fontSize: 12.5, color: C.text }}>{notice}</p>}
          {error && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{error}</p>}
          {unavailable && <p role="status" style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('renewals.unavailable')}</p>}
          {loadError && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{loadError}</p>}
          {data && data.items.length === 0 && (
            <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t('renewals.empty', { n: data.within_days })}</p>
          )}
          {data && data.items.length > 0 && (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {data.items.map(it => {
                const r = it.renewal
                const cmp = it.comparison
                const view = compare[it.root_id]
                return (
                  <li key={it.root_id} style={{ border: `1px solid ${C.border}`, borderRadius: 8, padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                      <div style={{ minWidth: 0, flex: '1 1 260px', overflowWrap: 'anywhere' }}>
                        <div style={{ fontSize: 13, fontWeight: 600, color: C.text, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                          <span>{it.customer}</span>
                          {it.reference && <span style={{ fontWeight: 500, color: C.muted }}>· {it.reference}</span>}
                          <span style={chip(BUCKET_COLOR[r.bucket])}>{t(`renewals.bucket_${r.bucket}`)}</span>
                          {r.auto_renew && <span style={chip(C.green)} title={t('renewals.auto_renew_hint')}>{t('renewals.auto_renew_chip')}</span>}
                        </div>
                        <div style={{ fontSize: 12, color: C.dim, marginTop: 2 }}>
                          {t('renewals.expires_on', { date: r.expiry_date })} · {daysText(r.days_to_expiry)}
                          {r.notice_deadline && <> · {t('renewals.notice_by', { date: r.notice_deadline })}</>}
                          {' · '}{it.warehouse_name ?? t('contracts.warehouse_all')}
                        </div>
                        <div style={{ fontSize: 11.5, color: C.dim, marginTop: 2 }}>{leadsText(it)}</div>
                        <div style={{ fontSize: 12, color: C.muted, marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: '2px 14px' }}>
                          <span>{cmp.fill_rate_pct == null ? t('renewals.fill_rate_none') : t('renewals.cmp_fill', { pct: fmt(cmp.fill_rate_pct) })}</span>
                          <span>{t('renewals.cmp_delivered', { units: fmt(cmp.delivered_units) })} / {t('renewals.cmp_committed', { units: fmt(cmp.committed_units) })}</span>
                          {cmp.late_deliveries > 0 && <span style={{ color: C.amber }}>{t('renewals.late_chip', { n: cmp.late_deliveries })}</span>}
                          {cmp.overdue_open > 0 && <span style={{ color: C.amber }}>{t('renewals.overdue_chip', { n: cmp.overdue_open })}</span>}
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        <button type="button" style={btn()} onClick={() => toggleCompare(it)} aria-expanded={!!view}>
                          {view ? t('renewals.hide_compare') : t('renewals.compare')}
                        </button>
                        {canWrite && (
                          <button type="button" disabled={busy} style={btn(true, busy)} onClick={() => renew(it)}>
                            <RefreshCw size={12} aria-hidden="true" /> {t('renewals.renew')}
                          </button>
                        )}
                      </div>
                    </div>
                    {view === 'loading' && <p style={{ margin: 0, fontSize: 12, color: C.dim }}>{t('common.loading')}</p>}
                    {view && typeof view === 'object' && 'error' in view && (
                      <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{view.error}</p>
                    )}
                    {view && typeof view === 'object' && 'comparison' in view && comparisonTable(view)}
                  </li>
                )
              })}
            </ul>
          )}
          {data && (data.later_count > 0 || data.hidden_renewed > 0) && (
            <p style={{ margin: 0, fontSize: 11.5, color: C.dim }}>
              {data.later_count > 0 && t('renewals.later_note', { n: data.later_count, days: data.within_days })}
              {data.later_count > 0 && data.hidden_renewed > 0 && ' '}
              {data.hidden_renewed > 0 && t('renewals.hidden_renewed', { n: data.hidden_renewed })}
            </p>
          )}
        </>
      )}
    </div>
  )
}
