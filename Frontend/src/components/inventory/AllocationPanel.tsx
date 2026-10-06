'use client'
/**
 * Sharing scarce stock among committed customers.
 *
 * When the stock on hand plus the arrivals that can be counted cannot cover
 * every open commitment of a product, this says who gets what: by the customer
 * priority tiers the company defines, optionally proportionally inside a tier,
 * never serving an order from supply that arrives after its date. A what-if
 * preview changes tiers, fair-share tiers or adds a hypothetical order without
 * saving anything; "Record reservations" is the explicit step that stores the
 * result.
 *
 * It is ADVISORY. Nothing here moves a stock row and the purchase
 * recommendation does not read it: the server (Rust) owns the arithmetic and
 * this panel only shows it. Reads are open to every signed-in user; the
 * controls that write are hidden for viewers (the server enforces it too).
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Scale } from 'lucide-react'
import {
  applyAllocation, getAllocationOverview, getAllocationPriorities, previewAllocation,
  releaseAllocation, saveAllocationPriorities,
} from '@/lib/api'
import type {
  AllocationOverview, AllocationPreview, AllocationPriorities, AllocationWhatIf,
} from '@/lib/types'
import { useErrorDetail } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { getUser } from '@/lib/auth'
import { localeFor } from '@/lib/numberLocale'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D', amber: '#B7791F' }
const TIERS = [1, 2, 3, 4, 5, 6, 7, 8, 9]

interface ExtraArrival { date: string; quantity: string }

export default function AllocationPanel({ reloadToken }: { reloadToken?: number } = {}) {
  const { t, lang } = useLanguage()
  const errorDetail = useErrorDetail()
  const narrow = useIsNarrow()
  const role = getUser()?.role
  const canWrite = role === 'admin' || role === 'analyst'
  const num = (n: number | null | undefined) => (n == null ? '-' : n.toLocaleString(localeFor(lang), { maximumFractionDigits: 2 }))

  const [priorities, setPriorities] = useState<AllocationPriorities | null>(null)
  const [overview, setOverview] = useState<AllocationOverview | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // Priority editor: customer key -> tier ('' = no tier, the default applies).
  const [editorOpen, setEditorOpen] = useState(false)
  const [draftTiers, setDraftTiers] = useState<Record<string, string>>({})
  const [draftFair, setDraftFair] = useState<number[]>([])

  // One product's allocation and the what-if controls over it.
  const [skuInput, setSkuInput] = useState('')
  const [preview, setPreview] = useState<AllocationPreview | null>(null)
  const [overrides, setOverrides] = useState<Record<string, string>>({})
  const [fairEdited, setFairEdited] = useState<number[] | null>(null)
  const [extras, setExtras] = useState<ExtraArrival[]>([])

  const loadAll = useCallback(() => {
    Promise.all([getAllocationPriorities(), getAllocationOverview()])
      .then(([p, o]) => { setPriorities(p); setOverview(o); setLoadError(null) })
      .catch(e => setLoadError(errorDetail(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { loadAll() }, [loadAll, reloadToken])

  const openEditor = () => {
    if (!priorities) return
    const tiers: Record<string, string> = {}
    for (const p of priorities.priorities) tiers[p.customer_key] = String(p.tier)
    setDraftTiers(tiers)
    setDraftFair(priorities.fair_share_tiers)
    setEditorOpen(true)
    setError(null)
  }

  const saveEditor = async () => {
    if (!priorities) return
    setBusy(true); setError(null); setNotice(null)
    try {
      // Only what changed: a tier set, a tier cleared, the fair-share list.
      const rows: { customer: string; tier: number | null }[] = []
      const known = new Map<string, string>()
      for (const p of priorities.priorities) known.set(p.customer_key, p.customer)
      for (const u of priorities.unassigned_customers) known.set(u.customer_key, u.customer)
      for (const [key, name] of Array.from(known.entries())) {
        const before = priorities.priorities.find(p => p.customer_key === key)?.tier ?? null
        const raw = draftTiers[key] ?? ''
        const after = raw === '' ? null : Number(raw)
        if (after !== before) rows.push({ customer: name, tier: after })
      }
      const fairChanged = [...draftFair].sort().join() !== [...priorities.fair_share_tiers].sort().join()
      if (rows.length === 0 && !fairChanged) { setNotice(t('alloc.nothing_changed')); setEditorOpen(false); return }
      await saveAllocationPriorities({
        ...(rows.length > 0 ? { priorities: rows } : {}),
        ...(fairChanged ? { fair_share_tiers: draftFair } : {}),
      })
      setNotice(t('alloc.priorities_saved'))
      setEditorOpen(false)
      loadAll()
      if (preview) await runPreview(preview.sku)
    } catch (e) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  const whatIfBody = (sku: string, withExtras: boolean): AllocationWhatIf => {
    const tier_overrides = Object.entries(overrides).filter(([, v]) => v !== '').map(([customer, v]) => ({ customer, tier: Number(v) }))
    const extra_arrivals = withExtras
      ? extras.filter(x => x.date && Number(x.quantity) > 0).map(x => ({ date: x.date, quantity: Number(x.quantity) }))
      : []
    return {
      sku,
      ...(tier_overrides.length > 0 ? { tier_overrides } : {}),
      ...(fairEdited ? { fair_share_tiers: fairEdited } : {}),
      ...(extra_arrivals.length > 0 ? { extra_arrivals } : {}),
    }
  }

  const runPreview = async (sku: string) => {
    setBusy(true); setError(null)
    try {
      const r = await previewAllocation(whatIfBody(sku, true))
      setPreview(r)
      setSkuInput(sku)
    } catch (e) { setPreview(null); setError(errorDetail(e)) } finally { setBusy(false) }
  }

  const choose = (sku: string) => {
    setOverrides({}); setFairEdited(null); setExtras([]); setNotice(null)
    setBusy(true); setError(null)
    previewAllocation({ sku })
      .then(r => { setPreview(r); setSkuInput(sku) })
      .catch(e => { setPreview(null); setError(errorDetail(e)) })
      .finally(() => setBusy(false))
  }

  const hasExtras = extras.some(x => x.date && Number(x.quantity) > 0)

  const apply = async () => {
    if (!preview || hasExtras) return
    setBusy(true); setError(null); setNotice(null)
    try {
      const body = whatIfBody(preview.sku, false)
      const r = await applyAllocation({ ...body, result_hash: preview.result_hash })
      setNotice(t('alloc.applied', { n: r.reservations, reserved: num(r.reserved), short: num(r.short) }))
      loadAll()
      await runPreview(preview.sku)
    } catch (e) {
      setError(errorDetail(e))
    } finally { setBusy(false) }
  }

  const release = async () => {
    if (!preview) return
    setBusy(true); setError(null); setNotice(null)
    try {
      const r = await releaseAllocation(preview.sku)
      setNotice(t('alloc.released', { n: r.released }))
      loadAll()
      await runPreview(preview.sku)
    } catch (e) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  const activeReservations = useMemo(() => preview?.lines.filter(l => l.reserved != null).length ?? 0, [preview])
  const staleReservations = useMemo(() => preview?.lines.filter(l => l.reservation_stale).length ?? 0, [preview])
  const customerNames = useMemo(() => {
    const seen = new Map<string, string>()
    for (const l of preview?.lines ?? []) if (l.customer) seen.set(l.customer.trim().toLowerCase(), l.customer)
    return Array.from(seen.values())
  }, [preview])

  const lbl: React.CSSProperties = { fontSize: narrow ? 13 : 11.5, color: C.muted }
  const field: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box', fontSize: narrow ? 16 : 12.5, padding: narrow ? '10px 10px' : '6px 8px',
    borderRadius: narrow ? 10 : 7, border: `1px solid ${C.border}`, background: 'var(--surface-2)', color: C.text,
    minHeight: narrow ? 44 : 32,
  }
  const btn = (disabled = false, solid = false): React.CSSProperties => ({
    all: 'unset', cursor: disabled ? 'default' : 'pointer', boxSizing: 'border-box', display: 'inline-flex',
    alignItems: 'center', justifyContent: 'center', gap: 5, padding: narrow ? '0 14px' : '5px 12px', minHeight: narrow ? 44 : undefined,
    borderRadius: narrow ? 10 : 7, fontSize: narrow ? 14 : 12, fontWeight: 600, opacity: disabled ? 0.5 : 1,
    border: `1px solid ${C.border}`, color: C.text,
    ...(solid ? { background: 'var(--accent)', borderColor: 'var(--accent)', color: '#fff' } : {}),
  })
  const card: React.CSSProperties = {
    background: narrow ? 'var(--surface)' : 'var(--surface-2)', border: `1px solid ${C.border}`, borderRadius: 8, padding: '12px 16px',
  }
  const heading: React.CSSProperties = { fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }
  const pill = (color: string): React.CSSProperties => ({
    fontSize: 10.5, fontWeight: 700, color, border: `1px solid ${color}`, borderRadius: 6, padding: '1px 6px',
  })
  const th: React.CSSProperties = { textAlign: 'left', fontWeight: 600, color: C.muted, padding: '4px 8px', whiteSpace: 'nowrap' }
  const td: React.CSSProperties = { padding: '4px 8px', verticalAlign: 'top' }

  const sourceLabel = (s: string) => t(`alloc.source_${s}`)

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 12,
      ...(narrow ? {} : { padding: '16px 20px', border: `1px solid ${C.border}`, borderRadius: 12, background: 'var(--surface)' }),
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 700, color: C.text }}>
        <Scale size={15} color="var(--accent)" aria-hidden="true" /> {t('alloc.title')}
      </div>
      <p style={{ margin: 0, fontSize: 13, color: C.dim, lineHeight: 1.5 }}>{t('alloc.intro')}</p>
      <p style={{ margin: 0, fontSize: 12, color: C.dim }}>{t('alloc.advisory_note')}</p>

      {notice && <p role="status" style={{ margin: 0, fontSize: 12.5, color: C.text }}>{notice}</p>}
      {error && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{error}</p>}
      {loadError && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{loadError}</p>}

      {/* ── Who is short, product by product ── */}
      {overview && (
        <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={heading}>{t('alloc.overview_title')}</div>
          {overview.contested.length === 0 && (
            <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>
              {t(overview.skus_with_commitments === 0 ? 'alloc.overview_no_commitments' : 'alloc.overview_none_short')}
            </p>
          )}
          {overview.contested.map(o => (
            <div key={o.sku} style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', alignItems: 'center', fontSize: 12.5, color: C.text }}>
              <span style={{ fontWeight: 600, overflowWrap: 'anywhere' }}>{o.sku}</span>
              <span style={{ color: C.red, fontWeight: 600 }}>
                {t('alloc.overview_short', { units: num(o.short), demand: num(o.demand), n: o.customers_short })}
              </span>
              {o.has_reservations && <span style={pill('var(--accent)')}>{t('alloc.has_reservations')}</span>}
              <button type="button" disabled={busy} style={btn(busy)} onClick={() => choose(o.sku)}>{t('alloc.view')}</button>
            </div>
          ))}
          {overview.stock_unknown.length > 0 && (
            <p style={{ margin: 0, fontSize: 12, color: C.amber }}>
              {t('alloc.overview_stock_unknown', { skus: overview.stock_unknown.map(u => u.sku).join(', ') })}
            </p>
          )}
          {overview.too_many_commitments.length > 0 && (
            <p style={{ margin: 0, fontSize: 12, color: C.amber }}>
              {t('alloc.overview_too_many', { skus: overview.too_many_commitments.map(u => u.sku).join(', ') })}
            </p>
          )}
          <form style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}
            onSubmit={e => { e.preventDefault(); if (skuInput.trim()) choose(skuInput.trim()) }}>
            <label style={{ ...lbl, flex: '1 1 200px' }}>{t('alloc.sku_label')}
              <input style={field} type="text" maxLength={200} value={skuInput} onChange={e => setSkuInput(e.target.value)} />
            </label>
            <button type="submit" disabled={busy || !skuInput.trim()} style={btn(busy || !skuInput.trim())}>{t('alloc.calculate')}</button>
          </form>
        </div>
      )}

      {/* ── One product ── */}
      {preview && (
        <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
            <span style={{ fontSize: 14, fontWeight: 700, color: C.text, overflowWrap: 'anywhere' }}>{preview.sku}</span>
            {preview.what_if && <span style={pill(C.amber)}>{t('alloc.what_if_badge')}</span>}
            {activeReservations > 0 && <span style={pill('var(--accent)')}>{t('alloc.has_reservations')}</span>}
            {staleReservations > 0 && <span style={pill(C.red)}>{t('alloc.stale_badge', { n: staleReservations })}</span>}
          </div>

          {preview.status === 'stock_unknown' ? (
            <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.amber }}>{t('alloc.stock_unknown')}</p>
          ) : (
            <>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px', fontSize: 12.5, color: C.text }}>
                <span>{t('alloc.stock_on_hand', { n: num(preview.stock) })}</span>
                <span>{t('alloc.total_demand', { n: num(preview.totals.demand) })}</span>
                <span>{t('alloc.total_allocated', { n: num(preview.totals.allocated) })}</span>
                <span style={{ color: (preview.totals.short ?? 0) > 0 ? C.red : C.text, fontWeight: 700 }}>
                  {t('alloc.total_short', { n: num(preview.totals.short) })}
                </span>
              </div>
              {!preview.contested && <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('alloc.not_contested')}</p>}
            </>
          )}

          {preview.incoming.length > 0 && (
            <div style={{ fontSize: 12, color: C.muted }}>
              <div style={heading}>{t('alloc.incoming_title')}</div>
              <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                {preview.incoming.map((a, i) => (
                  <li key={i}>{t('alloc.incoming_row', { qty: num(a.quantity), date: a.date ?? '-', ref: a.reference ?? t(`alloc.kind_${a.kind}`), source: sourceLabel(a.source) })}</li>
                ))}
              </ul>
            </div>
          )}
          {preview.incoming_not_counted.length > 0 && (
            <div style={{ fontSize: 12, color: C.amber }}>
              <div style={heading}>{t('alloc.not_counted_title')}</div>
              <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                {preview.incoming_not_counted.map((a, i) => (
                  <li key={i}>{t('alloc.not_counted_row', { qty: num(a.quantity), ref: a.reference ?? '-', source: sourceLabel(a.source) })}</li>
                ))}
              </ul>
            </div>
          )}

          {preview.status === 'ok' && (
            <>
              <div style={heading}>{t('alloc.customers_title')}</div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', fontSize: 12.5, color: C.text, minWidth: 420 }}>
                  <thead><tr>
                    <th style={th}>{t('alloc.col_customer')}</th><th style={th}>{t('alloc.col_tier')}</th>
                    <th style={th}>{t('alloc.col_wanted')}</th><th style={th}>{t('alloc.col_allocated')}</th><th style={th}>{t('alloc.col_short')}</th>
                  </tr></thead>
                  <tbody>
                    {preview.customers.map((c, i) => (
                      <tr key={i} style={{ borderTop: `1px solid ${C.border}` }}>
                        <td style={{ ...td, overflowWrap: 'anywhere' }}>{c.customer || t('committed.customer_unknown')}</td>
                        <td style={td}>{c.tier}</td>
                        <td style={td}>{num(c.units)}</td>
                        <td style={td}>{num(c.allocated)}</td>
                        <td style={{ ...td, color: (c.short ?? 0) > 0 ? C.red : C.text, fontWeight: (c.short ?? 0) > 0 ? 700 : 400 }}>{num(c.short)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div style={heading}>{t('alloc.lines_title')}</div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', fontSize: 12, color: C.text, minWidth: 560 }}>
                  <thead><tr>
                    <th style={th}>{t('alloc.col_date')}</th><th style={th}>{t('alloc.col_customer')}</th><th style={th}>{t('alloc.col_tier')}</th>
                    <th style={th}>{t('alloc.col_wanted')}</th><th style={th}>{t('alloc.col_allocated')}</th><th style={th}>{t('alloc.col_short')}</th>
                    <th style={th}>{t('alloc.col_reserved')}</th>
                  </tr></thead>
                  <tbody>
                    {preview.lines.map(l => (
                      <tr key={l.commitment_id} style={{ borderTop: `1px solid ${C.border}` }}>
                        <td style={td}>{l.delivery_date}{l.overdue && <> <span style={pill(C.amber)}>{t('committed.overdue')}</span></>}</td>
                        <td style={{ ...td, overflowWrap: 'anywhere' }}>{l.customer || t('committed.customer_unknown')}</td>
                        <td style={td}>{l.tier}{l.tier_source === 'default' && <span title={t('alloc.default_tier_hint')} style={{ color: C.dim }}> *</span>}</td>
                        <td style={td}>{num(l.units)}</td>
                        <td style={td}>{num(l.allocated)}</td>
                        <td style={{ ...td, color: (l.short ?? 0) > 0 ? C.red : C.text, fontWeight: (l.short ?? 0) > 0 ? 700 : 400 }}>{num(l.short)}</td>
                        <td style={td}>
                          {l.reserved == null ? '-' : num(l.reserved)}
                          {l.reservation_stale && <> <span title={t(`alloc.stale_${l.reservation_stale}`)} style={pill(C.red)}>{t('alloc.stale')}</span></>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('alloc.default_tier_note', { tier: priorities?.default_tier ?? 5 })}</p>

              {/* What-if controls */}
              <div style={{ ...card, background: 'var(--surface)', display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={heading}>{t('alloc.what_if_title')}</div>
                <p style={{ margin: 0, fontSize: 12, color: C.dim }}>{t('alloc.what_if_hint')}</p>
                <div style={{ display: 'grid', gap: 8, gridTemplateColumns: narrow ? '1fr' : 'repeat(auto-fit, minmax(190px, 1fr))' }}>
                  {customerNames.map(name => (
                    <label key={name} style={lbl}>{t('alloc.override_for', { customer: name })}
                      <select style={field} value={overrides[name] ?? ''} onChange={e => setOverrides(o => ({ ...o, [name]: e.target.value }))}>
                        <option value="">{t('alloc.override_keep')}</option>
                        {TIERS.map(n => <option key={n} value={n}>{t('alloc.tier_n', { n })}</option>)}
                      </select>
                    </label>
                  ))}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', fontSize: 12.5, color: C.text }}>
                  <span style={lbl}>{t('alloc.fair_share_tiers')}</span>
                  {TIERS.map(n => {
                    const current = fairEdited ?? preview.fair_share_tiers
                    return (
                      <label key={n} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, minHeight: narrow ? 44 : undefined }}>
                        <input type="checkbox" checked={current.includes(n)}
                          onChange={e => setFairEdited(e.target.checked ? [...current, n].sort() : current.filter(x => x !== n))} />
                        {n}
                      </label>
                    )
                  })}
                </div>
                {extras.map((x, i) => (
                  <div key={i} style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                    <label style={{ ...lbl, flex: '1 1 140px' }}>{t('alloc.extra_date')}
                      <input style={field} type="date" value={x.date}
                        onChange={e => setExtras(a => a.map((r, j) => (j === i ? { ...r, date: e.target.value } : r)))} />
                    </label>
                    <label style={{ ...lbl, flex: '1 1 140px' }}>{t('alloc.extra_quantity')}
                      <input style={field} type="number" inputMode="decimal" min={0} value={x.quantity}
                        onChange={e => setExtras(a => a.map((r, j) => (j === i ? { ...r, quantity: e.target.value } : r)))} />
                    </label>
                    <button type="button" style={{ ...btn(), border: 'none', color: C.muted }} onClick={() => setExtras(a => a.filter((_, j) => j !== i))}>
                      {t('alloc.extra_remove')}
                    </button>
                  </div>
                ))}
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button type="button" style={btn()} disabled={extras.length >= 20}
                    onClick={() => setExtras(a => [...a, { date: '', quantity: '' }])}>{t('alloc.extra_add')}</button>
                  <button type="button" style={btn(busy, true)} disabled={busy} onClick={() => runPreview(preview.sku)}>{t('alloc.recalculate')}</button>
                </div>
              </div>

              {canWrite && (
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                  <button type="button" style={btn(busy || hasExtras, true)} disabled={busy || hasExtras} onClick={apply}>
                    {t('alloc.apply')}
                  </button>
                  {activeReservations > 0 && (
                    <button type="button" style={{ ...btn(busy), color: C.red }} disabled={busy} onClick={release}>{t('alloc.release')}</button>
                  )}
                  {hasExtras && <span style={{ fontSize: 12, color: C.amber }}>{t('alloc.apply_blocked_extra')}</span>}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ── Priorities ── */}
      {priorities && (
        <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <div style={heading}>{t('alloc.priorities_title')}</div>
            {canWrite && !editorOpen && <button type="button" style={btn()} onClick={openEditor}>{t('alloc.edit_priorities')}</button>}
          </div>
          <p style={{ margin: 0, fontSize: 12, color: C.dim }}>{t('alloc.priorities_hint', { tier: priorities.default_tier })}</p>

          {!editorOpen && (
            <>
              {priorities.priorities.length === 0 && <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('alloc.priorities_empty')}</p>}
              {priorities.priorities.map(p => (
                <div key={p.customer_key} style={{ fontSize: 12.5, color: C.text }}>
                  <span style={{ fontWeight: 600 }}>{p.customer}</span> · {t('alloc.tier_n', { n: p.tier })}
                </div>
              ))}
              {priorities.fair_share_tiers.length > 0 && (
                <p style={{ margin: 0, fontSize: 12, color: C.muted }}>{t('alloc.fair_share_saved', { tiers: priorities.fair_share_tiers.join(', ') })}</p>
              )}
              {priorities.unassigned_customers.length > 0 && (
                <p style={{ margin: 0, fontSize: 12, color: C.amber }}>
                  {t('alloc.unassigned', { customers: priorities.unassigned_customers.map(u => u.customer).join(', '), tier: priorities.default_tier })}
                </p>
              )}
              {priorities.commitments_without_customer > 0 && (
                <p style={{ margin: 0, fontSize: 12, color: C.amber }}>{t('alloc.no_customer_commitments', { n: priorities.commitments_without_customer, tier: priorities.default_tier })}</p>
              )}
            </>
          )}

          {editorOpen && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {[...priorities.priorities.map(p => ({ key: p.customer_key, name: p.customer })),
                ...priorities.unassigned_customers.map(u => ({ key: u.customer_key, name: u.customer }))].map(r => (
                <label key={r.key} style={{ ...lbl, display: 'grid', gridTemplateColumns: narrow ? '1fr' : '1fr 160px', gap: 8, alignItems: 'center' }}>
                  <span style={{ fontSize: 12.5, color: C.text, overflowWrap: 'anywhere' }}>{r.name}</span>
                  <select style={field} value={draftTiers[r.key] ?? ''} onChange={e => setDraftTiers(d => ({ ...d, [r.key]: e.target.value }))}>
                    <option value="">{t('alloc.tier_default', { tier: priorities.default_tier })}</option>
                    {TIERS.map(n => <option key={n} value={n}>{t('alloc.tier_n', { n })}</option>)}
                  </select>
                </label>
              ))}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', fontSize: 12.5, color: C.text }}>
                <span style={lbl}>{t('alloc.fair_share_tiers')}</span>
                {TIERS.map(n => (
                  <label key={n} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, minHeight: narrow ? 44 : undefined }}>
                    <input type="checkbox" checked={draftFair.includes(n)}
                      onChange={e => setDraftFair(f => (e.target.checked ? [...f, n].sort() : f.filter(x => x !== n)))} />
                    {n}
                  </label>
                ))}
              </div>
              <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('alloc.fair_share_hint')}</p>
              <div style={{ display: 'flex', gap: 8 }}>
                <button type="button" disabled={busy} style={btn(busy, true)} onClick={saveEditor}>{busy ? t('common.saving') : t('alloc.save_priorities')}</button>
                <button type="button" style={{ ...btn(), border: 'none', color: C.muted }} onClick={() => setEditorOpen(false)}>{t('common.cancel')}</button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
