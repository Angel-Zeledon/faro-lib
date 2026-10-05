'use client'
/**
 * Physical stock count: count a warehouse with the phone, compare with the
 * system, apply the differences with a trail.
 *
 * `/conteo-fisico`          — start a count, resume one, see past ones.
 * `/conteo-fisico?id=<id>`  — one count: counting while open, review once
 *                             closed, summary once applied.
 *
 * Reached from Inventario's menu and the command palette; it is deliberately
 * not a sidebar entry.
 */
import { Suspense, useCallback, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { ScanLine } from 'lucide-react'

import {
  createStockCount, getStockCount, listStockCounts, listWarehouses,
  type StockCount, type StockCountDetail,
} from '@/lib/api'
import { getUser } from '@/lib/auth'
import type { Warehouse } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import Spinner from '@/components/ui/Spinner'
import { ErrorState, InlineError, LoadingState } from '@/components/ui/States'
import CountScan from './CountScan'
import CountReview from './CountReview'

const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', accent: 'var(--accent)',
}
const STATUS_TONE: Record<string, string> = {
  open: '#B7791F', closed: 'var(--accent)', applied: '#2E8B62', cancelled: 'var(--dim)',
}

function StockCountPageInner() {
  const router = useRouter()
  const params = useSearchParams()
  const id = params.get('id')
  const leave = useCallback(() => router.push('/conteo-fisico'), [router])
  return id ? <OneCount key={id} id={id} onLeave={leave} /> : <CountList />
}

// ── One count ────────────────────────────────────────────────────────────────

function OneCount({ id, onLeave }: { id: string; onLeave: () => void }) {
  const [count, setCount] = useState<StockCountDetail | null>(null)
  const [error, setError] = useState<unknown>(null)
  const role = getUser()?.role
  const canApply = role === 'admin' || role === 'analyst'

  const load = useCallback(() => {
    setError(null)
    getStockCount(id).then(setCount).catch(setError)
  }, [id])
  useEffect(() => { load() }, [load])

  if (error) return <ErrorState error={error} onRetry={load} />
  if (!count) return <LoadingState />

  if (count.status === 'open' && canApply) {
    return <CountScan count={count} onLeave={onLeave} onClosed={load} />
  }
  return <CountReview countId={id} canApply={canApply} onLeave={onLeave} onChanged={load} />
}

// ── The list ─────────────────────────────────────────────────────────────────

function CountList() {
  const { t, lang } = useLanguage()
  const router = useRouter()
  const narrow = useIsNarrow()
  const role = getUser()?.role
  const canStart = role === 'admin' || role === 'analyst'

  const [counts, setCounts] = useState<StockCount[] | null>(null)
  const [warehouses, setWarehouses] = useState<Warehouse[]>([])
  const [error, setError] = useState<unknown>(null)
  const [startError, setStartError] = useState<unknown>(null)
  const [warehouse, setWarehouse] = useState('')
  const [category, setCategory] = useState('')
  const [starting, setStarting] = useState(false)

  const load = useCallback(() => {
    setError(null)
    listStockCounts().then(setCounts).catch(setError)
    listWarehouses().then(w => {
      setWarehouses(w)
      setWarehouse(cur => cur || (w.find(x => x.is_default) ?? w[0])?.name || '')
    }).catch(() => setWarehouses([]))
  }, [])
  useEffect(() => { load() }, [load])

  const start = async () => {
    setStarting(true)
    setStartError(null)
    try {
      const c = await createStockCount({
        ...(warehouse ? { warehouse } : {}),
        ...(category.trim() ? { scope_category: category.trim() } : {}),
      })
      router.push(`/conteo-fisico?id=${encodeURIComponent(c.id)}`)
    } catch (err) {
      setStartError(err)
      setStarting(false)
    }
  }

  if (error) return <ErrorState error={error} onRetry={load} />
  if (!counts) return <div style={{ padding: 48, display: 'flex', justifyContent: 'center' }}><Spinner /></div>

  const active = counts.filter(c => c.status === 'open' || c.status === 'closed')
  const past = counts.filter(c => c.status === 'applied' || c.status === 'cancelled')
  const when = (iso: string) => new Date(iso).toLocaleDateString(lang === 'en' ? 'en-US' : 'es', { day: 'numeric', month: 'short' })

  const row = (c: StockCount, i: number) => (
    <button
      key={c.id} type="button"
      onClick={() => router.push(`/conteo-fisico?id=${encodeURIComponent(c.id)}`)}
      style={{ all: 'unset', boxSizing: 'border-box', width: '100%', display: 'flex', alignItems: 'center', gap: 10, minHeight: 56, padding: '10px 14px', cursor: 'pointer', borderTop: i ? `1px solid ${C.border}` : 'none' }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: C.text }}>{c.warehouse}{c.scope_category ? ` · ${c.scope_category}` : ''}</div>
        <div style={{ fontSize: 12, color: C.muted }}>
          {t('count.started_on', { date: when(c.created_at) })} · {t('count.lines_n', { n: c.lines_count ?? 0 })}
        </div>
      </div>
      <span style={{ fontSize: 12, fontWeight: 700, color: STATUS_TONE[c.status] }}>{t(`count.status.${c.status}`)}</span>
    </button>
  )

  const group = (title: string, items: StockCount[]) => items.length > 0 && (
    <div style={{ marginTop: 18 }}>
      <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase', color: C.dim, margin: '0 2px 6px' }}>{title}</div>
      <div style={{ border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden', background: C.surface }}>
        {items.map(row)}
      </div>
    </div>
  )

  return (
    <div style={{ padding: narrow ? 0 : '22px 26px', maxWidth: 720, margin: '0 auto' }}>
      <p style={{ fontSize: 13.5, color: C.muted, lineHeight: 1.6, margin: '0 0 14px' }}>{t('count.intro')}</p>

      {canStart ? (
        <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 14, padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: C.text }}>{t('count.start_title')}</div>
          {warehouses.length > 1 && (
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12.5, color: C.muted }}>
              {t('count.warehouse')}
              <select value={warehouse} onChange={e => setWarehouse(e.target.value)} style={fieldStyle}>
                {warehouses.map(w => <option key={w.id} value={w.name}>{w.name}</option>)}
              </select>
            </label>
          )}
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12.5, color: C.muted }}>
            {t('count.scope_category')}
            <input value={category} onChange={e => setCategory(e.target.value)} maxLength={100} style={fieldStyle} />
          </label>
          {startError != null && <InlineError error={startError} onDismiss={() => setStartError(null)} />}
          <button
            type="button" onClick={() => void start()} disabled={starting}
            style={{ all: 'unset', cursor: starting ? 'default' : 'pointer', boxSizing: 'border-box', minHeight: 48, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8, borderRadius: 10, background: C.accent, color: '#fff', fontSize: 15, fontWeight: 600, opacity: starting ? 0.6 : 1 }}
          >
            <ScanLine size={17} aria-hidden="true" /> {t('count.start')}
          </button>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: C.muted }}>{t('count.viewer_note')}</p>
      )}

      {group(t('count.open_counts'), active)}
      {group(t('count.past_counts'), past)}
      {counts.length === 0 && <p style={{ fontSize: 13.5, color: C.dim, marginTop: 18 }}>{t('count.none_yet')}</p>}
    </div>
  )
}

const fieldStyle: React.CSSProperties = {
  minHeight: 44, padding: '0 12px', fontSize: 16, borderRadius: 10,
  border: `1px solid ${C.border}`, background: C.surface, color: C.text,
}

export default function StockCountPage() {
  return (
    <Suspense fallback={<div style={{ padding: 48, display: 'flex', justifyContent: 'center' }}><Spinner /></div>}>
      <StockCountPageInner />
    </Suspense>
  )
}
