'use client'
/**
 * Fulfillment outlook, next to the committed-demand list: for each open
 * commitment, will it be met on its delivery date?
 *
 * The server does all the arithmetic and returns a verdict, the figures behind
 * it and a stable reason code; this panel only renders them (the sentence is
 * `outlook.reason.<code>` filled with the figures, es/en via i18n). A figure
 * that does not exist arrives as null and is never shown as a zero: the
 * verdict then says "insufficient data" and the panel says what is missing.
 */
import { useCallback, useEffect, useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { getCommitmentOutlook, getCommitmentOutlookDetail, getCommitmentOutlookSummary } from '@/lib/api'
import type { CommitmentOutlook, OutlookDetail, OutlookTenantSummary, OutlookVerdict } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { localeFor } from '@/lib/numberLocale'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D', amber: '#B7791F', green: '#3F7D58' }

const VERDICTS: OutlookVerdict[] = ['will_miss', 'at_risk', 'insufficient_data', 'on_track']
const VERDICT_COLOR: Record<OutlookVerdict, string> = {
  will_miss: C.red, at_risk: C.amber, insufficient_data: C.muted, on_track: C.green,
}

export default function CommitmentOutlookPanel({ reloadToken }: { reloadToken?: number } = {}) {
  const { t, lang } = useLanguage()
  const narrow = useIsNarrow()
  const [filter, setFilter] = useState<OutlookVerdict | null>(null)
  const [items, setItems] = useState<CommitmentOutlook[]>([])
  const [summary, setSummary] = useState<OutlookTenantSummary | null>(null)
  const [scoped, setScoped] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [unavailable, setUnavailable] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const [detail, setDetail] = useState<OutlookDetail | null>(null)
  const [detailError, setDetailError] = useState(false)

  const load = useCallback(() => {
    Promise.all([getCommitmentOutlook({ verdict: filter ?? undefined }), getCommitmentOutlookSummary()])
      .then(([list, sum]) => { setItems(list.items); setSummary(sum); setScoped(list.scope === 'warehouses'); setUnavailable(false) })
      .catch(() => { setItems([]); setSummary(null); setUnavailable(true) })
      .finally(() => setLoaded(true))
  }, [filter])
  useEffect(() => { load() }, [load, reloadToken])

  function toggle(id: string) {
    if (openId === id) { setOpenId(null); setDetail(null); return }
    setOpenId(id); setDetail(null); setDetailError(false)
    getCommitmentOutlookDetail(id).then(setDetail).catch(() => setDetailError(true))
  }

  const loc = localeFor(lang)
  const num = (n: unknown) => (typeof n === 'number' ? n.toLocaleString(loc, { maximumFractionDigits: 2 }) : '')

  /** The reason sentence: the code picks the text, the figures fill it. */
  function reasonText(c: CommitmentOutlook): string {
    const p = c.reason.params
    const filled: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(p)) filled[k] = typeof v === 'number' ? num(v) : (v ?? '')
    if (!filled.driver_reference) filled.driver_reference = '-'
    return t(`outlook.reason.${c.reason.code}`, filled)
  }

  const chip = (active: boolean): React.CSSProperties => ({
    all: 'unset', cursor: 'pointer', boxSizing: 'border-box', padding: narrow ? '0 12px' : '4px 10px',
    minHeight: narrow ? 40 : undefined, display: 'inline-flex', alignItems: 'center', borderRadius: 7,
    fontSize: narrow ? 13 : 12, fontWeight: active ? 700 : 500, color: C.text, border: `1px solid ${C.border}`,
    ...(active ? { background: 'color-mix(in srgb, var(--accent) 10%, transparent)' } : {}),
  })
  const badge = (v: OutlookVerdict): React.CSSProperties => ({
    fontSize: 10.5, fontWeight: 700, color: VERDICT_COLOR[v], border: `1px solid ${VERDICT_COLOR[v]}`,
    borderRadius: 6, padding: '1px 6px', whiteSpace: 'nowrap',
  })
  const card: React.CSSProperties = {
    background: narrow ? 'var(--surface)' : 'var(--surface-2)', border: `1px solid ${C.border}`, borderRadius: 8, padding: '12px 16px',
  }
  const heading: React.CSSProperties = { fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }

  const s = summary?.summary
  const arrivalWhen = (a: OutlookDetail['supply']['arrivals'][number]) => {
    const date = a.date ?? a.expected_date ?? ''
    const text = t(`outlook.arrival_source.${a.source}`, { date })
    return a.date && !a.counted ? `${text} (${t('outlook.arrival_not_counted')})` : text
  }

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 12,
      ...(narrow ? {} : { padding: '16px 20px', border: `1px solid ${C.border}`, borderRadius: 12, background: 'var(--surface)' }),
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 700, color: C.text }}>
        <ShieldCheck size={15} color="var(--accent)" aria-hidden="true" /> {t('outlook.title')}
      </div>
      <p style={{ margin: 0, fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('outlook.intro')}</p>
      {scoped && <p style={{ margin: 0, fontSize: 12, color: C.dim }}>{t('outlook.scoped_note')}</p>}

      {unavailable && loaded && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{t('outlook.unavailable')}</p>}
      {!loaded && <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('outlook.loading')}</p>}

      {s && s.total > 0 && (
        <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', fontSize: 12.5, color: C.text }}>
            <span style={{ fontWeight: 600 }}>{t('outlook.tile_total', { n: s.total })}</span>
            {VERDICTS.map(v => s[v] > 0 && (
              <span key={v} style={{ color: VERDICT_COLOR[v], fontWeight: 600 }}>{t(`outlook.verdict.${v}`)}: {s[v]}</span>
            ))}
          </div>
          {s.shortfall_units > 0 && (
            <div style={{ fontSize: 12.5, color: C.red }}>
              {t(s.shortfall_has_minimum ? 'outlook.tile_shortfall_min' : 'outlook.tile_shortfall', { units: num(s.shortfall_units) })}
              {s.first_problem_date && <> · {t('outlook.tile_first_problem', { date: s.first_problem_date })}</>}
            </div>
          )}
          {(summary?.data_gaps.length ?? 0) > 0 && (
            <div style={{ marginTop: 4 }}>
              <div style={heading}>{t('outlook.gaps_title')}</div>
              <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12, color: C.amber }}>
                {summary?.data_gaps.map(g => (
                  <li key={g.reason}>{t(`outlook.gap.${g.reason}`, { n: g.commitments, skus: (g.skus ?? []).join(', ') })}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {loaded && !unavailable && (
        <div role="group" aria-label={t('outlook.filter_aria')} style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          <button type="button" style={chip(filter === null)} onClick={() => setFilter(null)}>{t('outlook.filter_all')}</button>
          {VERDICTS.map(v => (
            <button key={v} type="button" style={chip(filter === v)} onClick={() => setFilter(f => (f === v ? null : v))}>
              {t(`outlook.verdict.${v}`)}
            </button>
          ))}
        </div>
      )}

      {loaded && !unavailable && items.length === 0 && (
        <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t(filter ? 'outlook.empty_filtered' : 'outlook.empty')}</p>
      )}

      {items.length > 0 && (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {items.map(c => (
            <li key={c.id} style={{ ...card, display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', fontSize: 13, fontWeight: 600, color: C.text, overflowWrap: 'anywhere' }}>
                <span style={badge(c.verdict)}>{t(`outlook.verdict.${c.verdict}`)}</span>
                <span>{c.sku}</span>
                <span style={{ fontWeight: 500, color: C.muted }}>{t('outlook.line_units', { units: num(c.expected_units) })}</span>
                {c.shortfall_units != null && c.shortfall_units > 0 && (
                  <span style={{ fontWeight: 600, color: C.red }}>
                    {t(c.shortfall_is_minimum ? 'outlook.shortfall_min' : 'outlook.shortfall', { units: num(c.shortfall_units) })}
                  </span>
                )}
              </div>
              <div style={{ fontSize: 12, color: C.dim }}>
                {c.delivery_date} · {c.customer || t('committed.customer_unknown')}
                {c.contract_reference && <> · {c.contract_reference}</>}
                {c.cover_date && c.verdict !== 'on_track' && c.cover_source && (
                  <> · {t('outlook.cover_date', { date: c.cover_date })} ({t(`outlook.cover_source.${c.cover_source}`)}
                  {c.late_days ? `, ${t('outlook.late_days', { n: c.late_days })}` : ''})</>
                )}
              </div>
              <div style={{ fontSize: 12.5, color: C.text, lineHeight: 1.5 }}>{reasonText(c)}</div>
              {c.shortfall_is_minimum && c.undated_units > 0 && (
                <div style={{ fontSize: 11.5, color: C.amber }}>{t('outlook.minimum_note', { units: num(c.undated_units) })}</div>
              )}
              <div>
                <button type="button" onClick={() => toggle(c.id)} aria-expanded={openId === c.id}
                  style={{ all: 'unset', cursor: 'pointer', fontSize: 12, fontWeight: 600, color: 'var(--accent)', minHeight: narrow ? 40 : undefined, display: 'inline-flex', alignItems: 'center' }}>
                  {openId === c.id ? t('outlook.hide') : t('outlook.details')}
                </button>
              </div>
              {openId === c.id && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12, color: C.text, borderTop: `1px solid ${C.border}`, paddingTop: 8 }}>
                  {detailError && <span role="alert" style={{ color: C.red }}>{t('outlook.unavailable')}</span>}
                  {!detail && !detailError && <span style={{ color: C.dim }}>{t('outlook.loading')}</span>}
                  {detail && (
                    <>
                      <div style={heading}>{t('outlook.detail_competing')}</div>
                      <ul style={{ margin: 0, paddingLeft: 18 }}>
                        {detail.competing.map(k => (
                          <li key={k.id} style={{ fontWeight: k.is_this ? 700 : 400 }}>
                            {k.delivery_date} · {k.customer || t('committed.customer_unknown')} · {num(k.units)} u ({num(k.cumulative_units)})
                          </li>
                        ))}
                      </ul>
                      <div style={heading}>{t('outlook.detail_supply')}</div>
                      <div>{detail.supply.stock == null
                        ? t('outlook.reason.no_stock_figure')
                        : t('outlook.detail_stock', { units: num(detail.supply.stock) })}</div>
                      <div>{detail.supply.lead_time_days == null
                        ? t('outlook.detail_lead_unknown')
                        : t('outlook.detail_lead', {
                          days: detail.supply.lead_time_days,
                          source: detail.supply.lead_time_source ? t(`outlook.lead_source.${detail.supply.lead_time_source}`) : '',
                        })}</div>
                      {detail.supply.arrivals.length === 0 ? (
                        <div style={{ color: C.dim }}>{t('outlook.detail_no_arrivals')}</div>
                      ) : (
                        <ul style={{ margin: 0, paddingLeft: 18 }}>
                          {detail.supply.arrivals.map((a, i) => (
                            <li key={i}>{t('outlook.arrival_line', { reference: a.reference, qty: num(a.qty), when: arrivalWhen(a) })}</li>
                          ))}
                        </ul>
                      )}
                    </>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
