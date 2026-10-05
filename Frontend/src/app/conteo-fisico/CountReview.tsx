'use client'
/**
 * After counting: the differences, biggest money first, and one tap to apply.
 * Also the calm summary of an applied count and the note of a cancelled one.
 *
 * Nothing in the stock changes until "Apply" — the preview is the whole point.
 * A SKU with no unit cost shows "no cost", never a zero: its value is unknown.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft } from 'lucide-react'

import {
  applyStockCount, cancelStockCount, previewStockCount,
  type StockCountApplyResult, type StockCountPreview,
} from '@/lib/api'
import { formatMoney } from '@/lib/currency'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { ErrorState, LoadingState, InlineError } from '@/components/ui/States'
import StickyActionBar from '@/components/mobile/StickyActionBar'

const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', accent: 'var(--accent)',
  green: '#2E8B62', amber: '#B7791F', red: '#C0504D',
}
const fmt = (n: number) => (Number.isInteger(n) ? String(n) : String(Math.round(n * 100) / 100))
const signed = (n: number) => `${n > 0 ? '+' : ''}${fmt(n)}`

export default function CountReview({ countId, canApply, onLeave, onChanged }: {
  countId:   string
  canApply:  boolean
  onLeave:   () => void
  onChanged: () => void
}) {
  const { t } = useLanguage()
  const narrow = useIsNarrow()
  const confirm = useConfirm()
  const [data, setData] = useState<StockCountPreview | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [working, setWorking] = useState(false)
  const [actionError, setActionError] = useState<unknown>(null)
  const [result, setResult] = useState<StockCountApplyResult | null>(null)

  const load = useCallback(() => {
    setError(null)
    previewStockCount(countId).then(d => {
      setData(d)
      setPicked(new Set(d.lines.filter(l => Math.abs(l.difference) > 1e-9 && !l.applied_at).map(l => l.sku)))
    }).catch(setError)
  }, [countId])
  useEffect(() => { load() }, [load])

  const withDiff = useMemo(
    () => (data?.lines ?? []).filter(l => Math.abs(l.difference) > 1e-9 && !l.applied_at), [data])

  if (error) return <ErrorState error={error} onRetry={load} />
  if (!data) return <LoadingState />

  const status = data.count.status
  const money = (v: number | null) => (v == null ? t('count.rv_no_cost') : `${v > 0 ? '+' : ''}${formatMoney(v)}`)

  const doApply = async (skus?: string[]) => {
    const n = skus ? skus.length : withDiff.length
    if (!(await confirm({
      title: t('count.rv_confirm_title'),
      message: t('count.rv_confirm_message', { n, warehouse: data.count.warehouse }),
      confirmLabel: t('count.rv_apply_confirm'),
    }))) return
    setWorking(true)
    setActionError(null)
    try {
      setResult(await applyStockCount(countId, skus))
      onChanged()
    } catch (err) {
      setActionError(err)
    } finally {
      setWorking(false)
    }
  }

  const doCancel = async () => {
    if (!(await confirm({ title: t('count.rv_cancel_title'), message: t('count.rv_cancel_message'), danger: true }))) return
    setWorking(true)
    try { await cancelStockCount(countId); onChanged() } catch (err) { setActionError(err) } finally { setWorking(false) }
  }

  const backBtn = (
    <button type="button" onClick={onLeave} style={{ all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6, minHeight: 44, fontSize: 13, fontWeight: 600, color: C.muted }}>
      <ArrowLeft size={15} aria-hidden="true" /> {t('count.back')}
    </button>
  )

  // ── Applied: a calm summary ────────────────────────────────────────────────
  if (status === 'applied' || result) {
    const r = result
    const adjusted = r ? r.lines_adjusted : data.lines.filter(l => l.applied_at && l.applied_from !== l.applied_to).length
    const unchanged = r ? r.lines_unchanged : data.lines.filter(l => l.applied_at && l.applied_from === l.applied_to).length
    const added = r ? r.units_added : data.lines.reduce((s, l) => s + (l.applied_to != null && l.applied_from != null && l.applied_to > l.applied_from ? l.applied_to - l.applied_from : 0), 0)
    const removed = r ? r.units_removed : data.lines.reduce((s, l) => s + (l.applied_to != null && l.applied_from != null && l.applied_to < l.applied_from ? l.applied_from - l.applied_to : 0), 0)
    const net = r ? r.net_value : data.lines.reduce((s, l) => s + (l.applied_to != null && l.applied_from != null && l.unit_cost != null ? (l.applied_to - l.applied_from) * l.unit_cost : 0), 0)
    const unpriced = r ? r.unpriced_lines : data.lines.filter(l => l.applied_from !== l.applied_to && l.applied_at && l.unit_cost == null).length
    return (
      <div style={{ maxWidth: 640, margin: '0 auto' }}>
        {backBtn}
        <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 14, padding: 18, marginTop: 6 }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: C.text }}>{t('count.rv_applied_title')}</div>
          <p style={{ fontSize: 14, color: C.muted, lineHeight: 1.6, margin: '8px 0 0' }}>
            {t('count.rv_applied_body', { adjusted, warehouse: data.count.warehouse, added: fmt(added), removed: fmt(removed) })}
            {unchanged > 0 && <> {t('count.rv_applied_unchanged', { n: unchanged })}</>}
          </p>
          <p style={{ fontSize: 14, color: C.text, margin: '10px 0 0' }}>
            {t('count.rv_applied_value', { value: `${net > 0 ? '+' : ''}${formatMoney(net)}` })}
          </p>
          {unpriced > 0 && <p style={{ fontSize: 12.5, color: C.dim, margin: '6px 0 0' }}>{t('count.rv_unpriced', { n: unpriced })}</p>}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 16 }}>
            <Link href="/inventario" style={{ ...linkBtn, background: C.accent, color: '#fff', border: 'none' }}>{t('count.rv_back_inventory')}</Link>
            <button type="button" onClick={onLeave} style={{ ...linkBtn, cursor: 'pointer' }}>{t('count.rv_new_count')}</button>
          </div>
        </div>
      </div>
    )
  }

  if (status === 'cancelled') {
    return (
      <div style={{ maxWidth: 640, margin: '0 auto' }}>
        {backBtn}
        <p style={{ fontSize: 14, color: C.muted }}>{t('count.rv_cancelled')}</p>
      </div>
    )
  }

  // ── Closed: review ─────────────────────────────────────────────────────────
  const tot = data.totals
  const allPicked = withDiff.length > 0 && withDiff.every(l => picked.has(l.sku))
  const chosen = withDiff.filter(l => picked.has(l.sku)).map(l => l.sku)
  const toggle = (sku: string) => setPicked(p => { const n = new Set(p); if (n.has(sku)) n.delete(sku); else n.add(sku); return n })

  const actions = canApply ? (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', width: '100%' }}>
      <button type="button" disabled={working || withDiff.length === 0} onClick={() => void doApply()} style={{ ...primaryBtn, flex: '1 1 auto' }}>
        {working ? t('count.rv_applying') : t('count.rv_apply_all')}
      </button>
      <button type="button" disabled={working || chosen.length === 0 || allPicked} onClick={() => void doApply(chosen)} style={{ ...linkBtn, cursor: 'pointer', flex: '1 1 auto' }}>
        {t('count.rv_apply_selected', { n: chosen.length })}
      </button>
    </div>
  ) : null

  return (
    <div style={{ maxWidth: 760, margin: '0 auto' }}>
      {backBtn}
      <p style={{ fontSize: 13.5, color: C.muted, lineHeight: 1.6, margin: '2px 0 12px' }}>{t('count.review_intro')}</p>

      <div style={{ display: 'grid', gridTemplateColumns: narrow ? '1fr 1fr' : 'repeat(3, 1fr)', gap: 8, marginBottom: 12 }}>
        <Stat label={t('count.rv_over')} value={`+${fmt(tot.units_over)}`} sub={tot.value_over ? `+${formatMoney(tot.value_over)}` : undefined} color={C.green} />
        <Stat label={t('count.rv_short')} value={`−${fmt(tot.units_short)}`} sub={tot.value_short ? `−${formatMoney(tot.value_short)}` : undefined} color={C.red} />
        <Stat label={t('count.rv_net')} value={`${tot.net_value > 0 ? '+' : ''}${formatMoney(tot.net_value)}`} color={C.text} />
      </div>

      {tot.unpriced_lines > 0 && <p style={noteStyle}>{t('count.rv_unpriced', { n: tot.unpriced_lines })}</p>}
      {tot.uncounted_skus > 0 && <p style={noteStyle}>{t('count.rv_uncounted', { n: tot.uncounted_skus })}</p>}
      {!canApply && <p style={noteStyle}>{t('count.rv_readonly')}</p>}
      {actionError != null && <InlineError error={actionError} onDismiss={() => setActionError(null)} />}

      {withDiff.length === 0 ? (
        <p style={{ fontSize: 14, color: C.text, padding: '14px 2px' }}>{t('count.rv_no_differences')}</p>
      ) : (
        <div style={{ border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden', background: C.surface }}>
          {canApply && (
            <label style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', minHeight: 44, fontSize: 13, color: C.muted, borderBottom: `1px solid ${C.border}`, cursor: 'pointer' }}>
              <input type="checkbox" checked={allPicked} onChange={() => setPicked(allPicked ? new Set() : new Set(withDiff.map(l => l.sku)))} style={{ width: 18, height: 18 }} />
              {t('count.rv_select_all')}
            </label>
          )}
          {withDiff.map((l, i) => (
            <label key={l.sku} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 12px', borderTop: i || canApply ? `1px solid ${C.border}` : 'none', cursor: canApply ? 'pointer' : 'default' }}>
              {canApply && <input type="checkbox" checked={picked.has(l.sku)} onChange={() => toggle(l.sku)} style={{ width: 18, height: 18, marginTop: 3 }} />}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: C.text }}>{l.display_name || l.sku}</div>
                <div style={{ fontSize: 12, color: C.muted }}>{l.sku} · {t('count.col', { counted: fmt(l.counted_qty), system: fmt(l.system_qty_at_count) })}</div>
                {l.moved_since_count && <div style={{ fontSize: 11.5, color: C.amber, marginTop: 3 }}>{t('count.rv_moved')}</div>}
              </div>
              <div style={{ textAlign: 'right', flexShrink: 0 }}>
                <div style={{ fontSize: 16, fontWeight: 700, color: l.difference > 0 ? C.green : C.red }}>{signed(l.difference)}</div>
                <div style={{ fontSize: 12, color: l.value_impact == null ? C.dim : C.muted }}>{money(l.value_impact)}</div>
              </div>
            </label>
          ))}
        </div>
      )}

      <div style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {!narrow && actions}
        {canApply && (
          <button type="button" onClick={() => void doCancel()} disabled={working} style={{ all: 'unset', cursor: 'pointer', minHeight: 44, padding: '0 6px', fontSize: 13, color: C.dim, textDecoration: 'underline' }}>
            {t('count.rv_cancel')}
          </button>
        )}
      </div>
      {narrow && actions && <StickyActionBar>{actions}</StickyActionBar>}
    </div>
  )
}

function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color: string }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 12, padding: '10px 12px' }}>
      <div style={{ fontSize: 11.5, color: C.dim, fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: C.muted }}>{sub}</div>}
    </div>
  )
}

const noteStyle: React.CSSProperties = { fontSize: 12.5, color: C.muted, lineHeight: 1.6, margin: '0 0 8px' }
const linkBtn: React.CSSProperties = {
  boxSizing: 'border-box', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', minHeight: 44,
  padding: '0 16px', borderRadius: 10, fontSize: 14, fontWeight: 600, border: `1px solid ${C.border}`,
  color: C.text, background: C.surface, textDecoration: 'none',
}
const primaryBtn: React.CSSProperties = { ...linkBtn, background: C.accent, color: '#fff', border: 'none', cursor: 'pointer' }
