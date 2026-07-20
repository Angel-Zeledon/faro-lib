'use client'
import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { Search, X, Package, AlertTriangle, CornerDownLeft, ArrowUp, ArrowDown } from 'lucide-react'
import { getSessions, getInventoryStatus, getSkuIntelligence } from '@/lib/api'
import type { InventoryStatusItem, InventorySignal, SkuIntelligenceData } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import SignalBadge, { SIGNAL_ORDER } from '@/components/ui/SignalBadge'
import { useSkuSearch } from '@/contexts/SkuSearchContext'
import { useLanguage } from '@/contexts/LanguageContext'

// ── Signal presentation ──────────────────────────────────────────────────────
// Single source: components/ui/SignalBadge (icon + label + accessible colour).

function fmtNum(n: number | null | undefined, d = 0) {
  if (n == null || isNaN(n)) return '—'
  return n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })
}

// ── Mini forecast sparkline — last 14 historical points + next 7 forecast ────

function MiniForecastChart({ historical, forecast }: {
  historical: { date: string; value: number }[]
  forecast:   { date: string; value: number }[]
}) {
  const width = 100, height = 68
  const histVals  = historical.slice(-14).map(p => p.value)
  const fcastVals = forecast.slice(0, 7).map(p => p.value)
  const all = [...histVals, ...fcastVals]
  if (all.length < 2) return null

  const min = Math.min(...all), max = Math.max(...all)
  const range = max - min || 1
  const n = all.length
  const xs = all.map((_, i) => (i / (n - 1)) * width)
  const ys = all.map(v => height - ((v - min) / range) * (height - 8) - 4)

  const histCount = histVals.length
  const histPath = xs
    .slice(0, histCount)
    .map((x, i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${ys[i].toFixed(1)}`)
    .join(' ')
  const fcastPath = histCount > 0
    ? xs
        .slice(histCount - 1)
        .map((x, i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${ys[histCount - 1 + i].toFixed(1)}`)
        .join(' ')
    : ''

  return (
    <svg width={width} height={height} style={{ display: 'block', overflow: 'visible' }}>
      {histPath && (
        <path d={histPath} fill="none" stroke="#818cf8" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" />
      )}
      {fcastVals.length > 0 && fcastPath && (
        <path d={fcastPath} fill="none" stroke="#22c55e" strokeWidth={1.75} strokeDasharray="3,3" strokeLinecap="round" strokeLinejoin="round" />
      )}
    </svg>
  )
}

// ── Result row ────────────────────────────────────────────────────────────────

function ResultRow({ item, active, onClick, onMouseEnter }: {
  item: InventoryStatusItem
  active: boolean
  onClick: () => void
  onMouseEnter: () => void
}) {
  const { t } = useLanguage()
  return (
    <button
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      style={{
        all: 'unset', cursor: 'pointer', display: 'block', width: '100%', boxSizing: 'border-box',
        padding: '9px 14px',
        background: active ? 'var(--surface-2)' : 'transparent',
        borderLeft: `3px solid ${active ? 'var(--accent)' : 'transparent'}`,
        borderBottom: '1px solid var(--border)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <Package size={13} style={{ flexShrink: 0, color: 'var(--dim)' }} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {item.display_name || item.sku}
            </div>
            <div style={{ fontSize: 10.5, color: 'var(--dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {item.sku}
            </div>
          </div>
        </div>
        <SignalBadge signal={item.signal} style={{ fontSize: 9, flexShrink: 0 }} />
      </div>
    </button>
  )
}

// ── Main overlay ──────────────────────────────────────────────────────────────

export default function SkuSearchOverlay() {
  const { isOpen, close, toggle } = useSkuSearch()
  const { t } = useLanguage()

  const [sessionId,   setSessionId]   = useState<string | null>(null)
  const [items,        setItems]      = useState<InventoryStatusItem[] | null>(null)
  const [loadingList,  setLoadingList] = useState(false)
  const [listError,    setListError]  = useState<string | null>(null)

  const [query,        setQuery]      = useState('')
  const [activeIndex,  setActiveIndex] = useState(0)
  const [selectedSku,  setSelectedSku] = useState<string | null>(null)

  const [intel,        setIntel]      = useState<SkuIntelligenceData | null>(null)
  const [intelLoading, setIntelLoading] = useState(false)
  const [intelError,   setIntelError] = useState<string | null>(null)

  const inputRef = useRef<HTMLInputElement>(null)

  // ── Global Ctrl/Cmd-K shortcut (always listening, regardless of open state) ─
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const isK = e.key === 'k' || e.key === 'K'
      if (isK && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        toggle()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [toggle])

  // ── Reset transient state whenever the overlay opens ─────────────────────────
  useEffect(() => {
    if (isOpen) {
      setQuery('')
      setActiveIndex(0)
      setSelectedSku(null)
      setIntel(null)
      setIntelError(null)
      // Autofocus after the panel has mounted.
      const id = setTimeout(() => inputRef.current?.focus(), 30)
      return () => clearTimeout(id)
    }
  }, [isOpen])

  // ── Escape closes the detail view first, then the overlay ────────────────────
  useEffect(() => {
    if (!isOpen) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault()
        if (selectedSku) setSelectedSku(null)
        else close()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isOpen, selectedSku, close])

  // ── Lazily resolve the latest completed session on first open ────────────────
  useEffect(() => {
    if (!isOpen || sessionId || items) return
    setLoadingList(true)
    setListError(null)
    getSessions()
      .then(list => {
        const completed = list
          .filter(s => s.status === 'COMPLETED')
          .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
        if (completed.length === 0) {
          setListError(t('search.no_session'))
          setLoadingList(false)
          return
        }
        setSessionId(completed[0].session_id)
      })
      .catch(() => {
        setListError(t('search.error_loading_sessions'))
        setLoadingList(false)
      })
  }, [isOpen, sessionId, items, t])

  // ── Load the full per-SKU inventory status list for that session ─────────────
  useEffect(() => {
    if (!sessionId) return
    setLoadingList(true)
    setListError(null)
    getInventoryStatus(sessionId)
      .then(res => setItems(res.items))
      .catch(() => setListError(t('search.error_loading_items')))
      .finally(() => setLoadingList(false))
  }, [sessionId, t])

  // ── Filtering ──────────────────────────────────────────────────────────────
  const results = useMemo(() => {
    if (!items) return []
    const q = query.trim().toLowerCase()
    const sorted = [...items].sort((a, b) => SIGNAL_ORDER.indexOf(a.signal) - SIGNAL_ORDER.indexOf(b.signal))
    if (!q) return sorted.slice(0, 8)
    return items
      .filter(i =>
        i.sku.toLowerCase().includes(q) ||
        (i.display_name ?? '').toLowerCase().includes(q) ||
        (i.category ?? '').toLowerCase().includes(q) ||
        (i.brand ?? '').toLowerCase().includes(q) ||
        (i.barcode ?? '').toLowerCase().includes(q) ||
        (i.supplier ?? '').toLowerCase().includes(q),
      )
      .slice(0, 30)
  }, [items, query])

  useEffect(() => { setActiveIndex(0) }, [query])

  const selectedItem = items?.find(i => i.sku === selectedSku) ?? null

  const selectSku = useCallback((sku: string) => {
    setSelectedSku(sku)
  }, [])

  // ── Mini-forecast fetch for the selected SKU ──────────────────────────────────
  useEffect(() => {
    if (!selectedSku || !sessionId) { setIntel(null); return }
    setIntelLoading(true)
    setIntelError(null)
    getSkuIntelligence(sessionId, selectedSku)
      .then(setIntel)
      .catch((e: unknown) => setIntelError(e instanceof Error ? e.message : t('search.error_loading_forecast')))
      .finally(() => setIntelLoading(false))
  }, [selectedSku, sessionId, t])

  // ── List keyboard navigation (only while no SKU is selected) ──────────────────
  const onInputKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (selectedSku) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex(i => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const hit = results[activeIndex]
      if (hit) selectSku(hit.sku)
    }
  }, [selectedSku, results, activeIndex, selectSku])

  if (!isOpen) return null

  return (
    <div
      onClick={close}
      style={{
        position: 'fixed', inset: 0, zIndex: 300,
        background: 'rgba(8,9,13,0.55)',
        display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
        padding: '10vh 16px 16px',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={t('search.placeholder')}
        style={{
          width: '100%', maxWidth: 640,
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
          overflow: 'hidden',
          display: 'flex', flexDirection: 'column',
          maxHeight: '76vh',
        }}
      >
        {/* Search input */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px',
          borderBottom: '1px solid var(--border)', flexShrink: 0,
        }}>
          <Search size={15} style={{ color: 'var(--dim)', flexShrink: 0 }} />
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={onInputKeyDown}
            placeholder={t('search.placeholder')}
            style={{
              flex: 1, background: 'transparent', border: 'none', outline: 'none',
              fontSize: 14, color: 'var(--fg)',
            }}
          />
          {loadingList && <Spinner size={13} />}
          <button
            onClick={close}
            title={t('search.close_title')}
            aria-label={t('search.close_title')}
            style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)', display: 'flex', flexShrink: 0 }}
          >
            <X size={15} aria-hidden="true" />
          </button>
        </div>

        {selectedItem ? (
          <SkuDetail
            item={selectedItem}
            intel={intel}
            loading={intelLoading}
            error={intelError}
            onBack={() => setSelectedSku(null)}
          />
        ) : (
          <div style={{ overflowY: 'auto', flex: 1, minHeight: 0 }}>
            {listError ? (
              <div style={{ padding: '28px 16px', textAlign: 'center', color: '#ef4444', fontSize: 12.5, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
                <AlertTriangle size={18} />
                {listError}
              </div>
            ) : !items && loadingList ? (
              <div style={{ padding: '28px 16px', textAlign: 'center', color: 'var(--dim)', fontSize: 12.5 }}>
                {t('search.loading')}
              </div>
            ) : results.length === 0 ? (
              <div style={{ padding: '28px 16px', textAlign: 'center', color: 'var(--dim)', fontSize: 12.5 }}>
                {query ? t('search.no_results') : t('search.empty_hint')}
              </div>
            ) : (
              <>
                {!query && (
                  <div style={{ padding: '8px 16px 2px', fontSize: 10.5, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                    {t('search.suggestions_label')}
                  </div>
                )}
                {results.map((item, i) => (
                  <ResultRow
                    key={item.sku}
                    item={item}
                    active={i === activeIndex}
                    onClick={() => selectSku(item.sku)}
                    onMouseEnter={() => setActiveIndex(i)}
                  />
                ))}
              </>
            )}
          </div>
        )}

        {/* Footer hints */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 14, padding: '8px 16px',
          borderTop: '1px solid var(--border)', flexShrink: 0,
          fontSize: 10.5, color: 'var(--dim)',
        }}>
          {!selectedItem && (
            <>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><ArrowUp size={10} /><ArrowDown size={10} /> {t('search.hint_navigate')}</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><CornerDownLeft size={10} /> {t('search.hint_select')}</span>
            </>
          )}
          <span style={{ marginLeft: 'auto' }}>{t('search.hint_close')}: Esc</span>
        </div>
      </div>
    </div>
  )
}

// ── Detail panel ──────────────────────────────────────────────────────────────

function SkuDetail({ item, intel, loading, error, onBack }: {
  item: InventoryStatusItem
  intel: SkuIntelligenceData | null
  loading: boolean
  error: string | null
  onBack: () => void
}) {
  const { t } = useLanguage()
  return (
    <div style={{ overflowY: 'auto', flex: 1, minHeight: 0, padding: '14px 16px' }}>
      <button
        onClick={onBack}
        style={{
          all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4,
          fontSize: 11, color: 'var(--dim)', marginBottom: 10,
        }}
      >
        ← {t('search.back_to_results')}
      </button>

      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, marginBottom: 12 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {item.display_name || item.sku}
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--dim)' }}>{item.sku}</div>
        </div>
        <SignalBadge signal={item.signal} size="md" style={{ flexShrink: 0 }} />
      </div>

      {/* Stats row */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 0,
        border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', marginBottom: 14,
      }}>
        <div style={{ padding: '9px 10px', textAlign: 'center', borderRight: '1px solid var(--border)' }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>{fmtNum(item.current_stock)}</div>
          <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 1 }}>{t('search.stat_current_stock')}</div>
        </div>
        <div style={{ padding: '9px 10px', textAlign: 'center', borderRight: '1px solid var(--border)' }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>
            {item.coverage_days != null ? `${fmtNum(item.coverage_days)}d` : '—'}
          </div>
          <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 1 }}>{t('search.stat_coverage_days')}</div>
        </div>
        <div style={{ padding: '9px 10px', textAlign: 'center' }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>
            {item.recommended_qty != null && item.recommended_qty > 0 ? fmtNum(item.recommended_qty) : '—'}
          </div>
          <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 1 }}>{t('search.stat_recommended_qty')}</div>
        </div>
      </div>

      {/* Mini forecast */}
      <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--dim)', marginBottom: 8 }}>
          {t('search.mini_forecast_label')}
        </div>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--dim)', fontSize: 11.5 }}>
            <Spinner size={13} /> {t('search.loading')}
          </div>
        ) : error ? (
          <div style={{ color: '#ef4444', fontSize: 11.5 }}>{error}</div>
        ) : intel && (intel.historical.length > 0 || intel.forecast.length > 0) ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <MiniForecastChart historical={intel.historical} forecast={intel.forecast} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11 }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ width: 10, height: 2, background: '#818cf8', display: 'inline-block' }} />
                {t('search.legend_historical')}
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ width: 10, height: 2, background: '#22c55e', display: 'inline-block' }} />
                {t('search.legend_forecast')}
              </span>
              {intel.model && <span style={{ color: 'var(--dim)' }}>{t('search.model_label')}: {intel.model}</span>}
            </div>
          </div>
        ) : (
          <div style={{ color: 'var(--dim)', fontSize: 11.5 }}>{t('search.no_forecast_data')}</div>
        )}
      </div>
    </div>
  )
}
