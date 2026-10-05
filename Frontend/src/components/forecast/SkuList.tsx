'use client'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Search, X } from 'lucide-react'
import type { InventorySignal } from '@/lib/types'
import { SIGNAL_ORDER, SIGNAL_STYLES } from '@/components/ui/SignalBadge'
import { useLanguage } from '@/contexts/LanguageContext'
import { Sparkline } from './MiniSpark'
import { formatQty } from './BuyerChart'

// ── What a row knows about its product ───────────────────────────────────────

export interface SkuRowInfo {
  signal?: InventorySignal
  /** Units the live status recommends ordering now; null without a stock row. */
  qty: number | null
  /** Recent stock levels, oldest first. Empty when the product has no history. */
  spark: number[]
  /** 0-100 accuracy of the model the orders come from, or null. */
  accuracy: number | null
  /** What ordering the recommendation costs: qty x unit cost (qty alone when
   *  there is no cost). The sort key for "impact". */
  impact: number
  /** Coverage in the planning unit, the tie-breaker for urgency. */
  coverage: number | null
  name: string | null
}

export type SkuSort = 'urgency' | 'impact' | 'accuracy'
export const SKU_SORTS: SkuSort[] = ['urgency', 'impact', 'accuracy']

const EMPTY_INFO: SkuRowInfo = {
  qty: null, spark: [], accuracy: null, impact: 0, coverage: null, name: null,
}

const urgencyRank = (s?: InventorySignal) => {
  const i = s ? SIGNAL_ORDER.indexOf(s) : -1
  return i < 0 ? SIGNAL_ORDER.length : i
}

/** Stable, deterministic ordering for the list. Ties always fall back to the
 *  SKU name so the list never reshuffles between renders. */
export function sortSkus(skus: string[], info: Map<string, SkuRowInfo>, sort: SkuSort): string[] {
  const get = (s: string) => info.get(s) ?? EMPTY_INFO
  const byName = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true })
  const nullsLast = (a: number | null, b: number | null, dir: 1 | -1) =>
    a === null && b === null ? 0 : a === null ? 1 : b === null ? -1 : dir * (a - b)
  const urgency = (a: string, b: string) =>
    urgencyRank(get(a).signal) - urgencyRank(get(b).signal)
    || nullsLast(get(a).coverage, get(b).coverage, 1)
  const cmp = sort === 'impact'
    ? (a: string, b: string) => get(b).impact - get(a).impact || urgency(a, b) || byName(a, b)
    : sort === 'accuracy'
    ? (a: string, b: string) => nullsLast(get(a).accuracy, get(b).accuracy, -1) || byName(a, b)
    : (a: string, b: string) => urgency(a, b) || byName(a, b)
  return [...skus].sort(cmp)
}

// ── The list ─────────────────────────────────────────────────────────────────

const OVERSCAN = 6

/**
 * The product list: search, three sorts, and compact rows (status dot, name,
 * stock sparkline, units to order). Only the rows in view are rendered, so a
 * catalogue of thousands costs the same as one of fifty. Arrow keys, Home,
 * End and Page Up/Down move the selection; Enter in the search box opens the
 * first match. The selection belongs to the page, so filtering never loses it.
 */
export function SkuList({
  items, total, info, search, onSearch, sort, onSort, selected, onSelect,
  touch = false, height,
}: {
  /** Filtered and sorted. */
  items: string[]
  /** Unfiltered count, to say "3 of 120" while a search narrows the list. */
  total: number
  info: Map<string, SkuRowInfo>
  search: string
  onSearch: (s: string) => void
  sort: SkuSort
  onSort: (s: SkuSort) => void
  selected: string | null
  onSelect: (sku: string) => void
  /** Phone layout: 44px+ targets and a 16px search box (no iOS zoom). */
  touch?: boolean
  /** Fixed pixel height; omit to fill the parent. */
  height?: number | string
}) {
  const { t, lang } = useLanguage()
  const rowH = touch ? 64 : 54
  const boxRef = useRef<HTMLDivElement>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewH, setViewH] = useState(420)

  useLayoutEffect(() => {
    const el = boxRef.current
    if (!el) return
    setViewH(el.clientHeight || 420)
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setViewH(el.clientHeight || 420))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const selectedIdx = selected ? items.indexOf(selected) : -1

  const reveal = useCallback((i: number) => {
    const el = boxRef.current
    if (!el || i < 0) return
    const top = i * rowH
    if (top < el.scrollTop) el.scrollTop = top
    else if (top + rowH > el.scrollTop + el.clientHeight) el.scrollTop = top + rowH - el.clientHeight
  }, [rowH])

  // Keep the open product in view when it changes from outside (first load,
  // a different sort) so the list always shows where you are.
  useEffect(() => { reveal(selectedIdx) }, [selectedIdx, reveal, items.length])

  const move = (to: number) => {
    if (!items.length) return
    const i = Math.min(items.length - 1, Math.max(0, to))
    onSelect(items[i])
    reveal(i)
  }
  const onKeyDown = (e: React.KeyboardEvent) => {
    const page = Math.max(1, Math.floor(viewH / rowH) - 1)
    const cur = selectedIdx < 0 ? -1 : selectedIdx
    switch (e.key) {
      case 'ArrowDown': e.preventDefault(); move(cur + 1); break
      case 'ArrowUp':   e.preventDefault(); move(cur < 0 ? 0 : cur - 1); break
      case 'PageDown':  e.preventDefault(); move(cur + page); break
      case 'PageUp':    e.preventDefault(); move(cur - page); break
      case 'Home':      e.preventDefault(); move(0); break
      case 'End':       e.preventDefault(); move(items.length - 1); break
    }
  }

  const start = Math.max(0, Math.floor(scrollTop / rowH) - OVERSCAN)
  const end = Math.min(items.length, Math.ceil((scrollTop + viewH) / rowH) + OVERSCAN)

  const sortLabel: Record<SkuSort, string> = {
    urgency: t('skus.sort_urgency'), impact: t('skus.sort_impact'), accuracy: t('skus.sort_accuracy'),
  }
  const listId = 'sku-listbox'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: height ?? '100%' }}>
      <div style={{ padding: touch ? '4px 0 8px' : '10px 12px 8px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ position: 'relative' }}>
          <Search size={touch ? 16 : 13} aria-hidden="true"
            style={{ position: 'absolute', left: touch ? 12 : 9, top: '50%', transform: 'translateY(-50%)', color: 'var(--dim)' }} />
          <input
            data-tour="skus.search"
            type="search"
            inputMode="search"
            enterKeyHint="search"
            value={search}
            onChange={e => onSearch(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'ArrowDown') { e.preventDefault(); boxRef.current?.focus(); if (selectedIdx < 0) move(0) }
              else if (e.key === 'Enter' && items.length) { e.preventDefault(); onSelect(items[0]) }
            }}
            placeholder={t('skus.search_placeholder')}
            aria-label={t('skus.search_placeholder')}
            className="form-input"
            style={{
              width: '100%', boxSizing: 'border-box',
              paddingLeft: touch ? 36 : 28, fontSize: touch ? 16 : 13, minHeight: touch ? 44 : undefined,
            }}
          />
          {search && (
            <button
              onClick={() => onSearch('')}
              aria-label={t('skus.search_clear')}
              style={{
                all: 'unset', cursor: 'pointer', position: 'absolute', right: 4, top: '50%',
                transform: 'translateY(-50%)', width: touch ? 36 : 24, height: touch ? 36 : 24,
                display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--dim)',
              }}
            >
              <X size={touch ? 16 : 13} aria-hidden="true" />
            </button>
          )}
        </div>
        <div role="radiogroup" aria-label={t('skus.sort_label')} style={{ display: 'flex', gap: 4 }}>
          {SKU_SORTS.map(k => {
            const on = sort === k
            return (
              <button
                key={k}
                role="radio"
                aria-checked={on}
                onClick={() => onSort(k)}
                style={{
                  all: 'unset', cursor: 'pointer', boxSizing: 'border-box',
                  flex: 1, textAlign: 'center', borderRadius: 7,
                  padding: touch ? '0 6px' : '4px 6px', minHeight: touch ? 40 : undefined,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: touch ? 13 : 11.5, fontWeight: 600,
                  color: on ? 'var(--accent)' : 'var(--dim)',
                  background: on ? 'color-mix(in srgb, var(--accent) 10%, transparent)' : 'transparent',
                  border: `1px solid ${on ? 'color-mix(in srgb, var(--accent) 35%, var(--border))' : 'var(--border)'}`,
                }}
              >
                {sortLabel[k]}
              </button>
            )
          })}
        </div>
        <div aria-live="polite" style={{ fontSize: touch ? 13 : 11, color: 'var(--dim)' }}>
          {search && items.length !== total
            ? t('skus.list_count_filtered', { n: items.length, total })
            : `${total} ${total !== 1 ? t('skus.skus_count_plural') : t('skus.skus_count_singular')}`}
        </div>
      </div>

      <div
        ref={boxRef}
        id={listId}
        role="listbox"
        tabIndex={0}
        aria-label={t('skus.mobile_list_aria')}
        aria-activedescendant={selectedIdx >= 0 ? `sku-opt-${selectedIdx}` : undefined}
        onKeyDown={onKeyDown}
        onScroll={e => setScrollTop(e.currentTarget.scrollTop)}
        style={{
          flex: 1, minHeight: 0, overflowY: 'auto', position: 'relative', outlineOffset: -2,
          borderTop: '1px solid var(--border)',
        }}
      >
        {items.length === 0 ? (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--dim)', fontSize: touch ? 14 : 12.5, lineHeight: 1.5 }}>
            {t('skus.list_no_match')}
            {search && (
              <div style={{ marginTop: 8 }}>
                <button
                  onClick={() => onSearch('')}
                  style={{ all: 'unset', cursor: 'pointer', color: 'var(--accent)', fontWeight: 600, minHeight: touch ? 44 : undefined }}
                >
                  {t('skus.search_clear')}
                </button>
              </div>
            )}
          </div>
        ) : (
          <div style={{ height: items.length * rowH, position: 'relative' }}>
            {items.slice(start, end).map((sku, k) => {
              const i = start + k
              const row = info.get(sku) ?? EMPTY_INFO
              const on = sku === selected
              const style = row.signal ? SIGNAL_STYLES[row.signal] : undefined
              const subtitle = sort === 'accuracy' && row.accuracy != null
                ? t('skus.list_accuracy', { pct: row.accuracy })
                : row.name && row.name !== sku ? row.name : style ? t(style.labelKey) : ''
              return (
                <div
                  key={sku}
                  id={`sku-opt-${i}`}
                  role="option"
                  aria-selected={on}
                  data-tour={i === 0 ? 'skus.card' : undefined}
                  onClick={() => { onSelect(sku); boxRef.current?.focus({ preventScroll: true }) }}
                  style={{
                    position: 'absolute', top: i * rowH, left: 0, right: 0, height: rowH,
                    boxSizing: 'border-box', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', gap: 10, padding: '0 12px 0 10px',
                    borderLeft: `3px solid ${on ? 'var(--accent)' : 'transparent'}`,
                    borderBottom: '1px solid var(--border)',
                    background: on ? 'color-mix(in srgb, var(--accent) 8%, var(--surface))' : 'transparent',
                  }}
                >
                  <span
                    aria-hidden="true"
                    style={{
                      width: 9, height: 9, borderRadius: '50%', flexShrink: 0,
                      background: style ? style.fg : 'var(--border)',
                    }}
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontSize: touch ? 15 : 13, fontWeight: 600, whiteSpace: 'nowrap',
                      overflow: 'hidden', textOverflow: 'ellipsis',
                    }}>{sku}</div>
                    {subtitle && (
                      <div style={{
                        fontSize: touch ? 12.5 : 11, color: 'var(--dim)', whiteSpace: 'nowrap',
                        overflow: 'hidden', textOverflow: 'ellipsis', marginTop: 1,
                      }}>{subtitle}</div>
                    )}
                  </div>
                  {row.spark.length > 1 && (
                    <span aria-hidden="true" style={{ flexShrink: 0, opacity: 0.85 }}>
                      <Sparkline values={row.spark} color="var(--dim)" width={touch ? 44 : 40} height={18} />
                    </span>
                  )}
                  <div style={{
                    flexShrink: 0, minWidth: touch ? 52 : 46, textAlign: 'right',
                    fontSize: touch ? 14 : 12.5, fontVariantNumeric: 'tabular-nums',
                  }}>
                    {row.qty != null && row.qty > 0 ? (
                      <>
                        <div style={{ fontWeight: 700 }}>{formatQty(row.qty, lang === 'en' ? 'en' : 'es')}</div>
                        <div style={{ fontSize: touch ? 11.5 : 10, color: 'var(--dim)' }}>{t('skus.list_to_order')}</div>
                      </>
                    ) : (
                      <span style={{ color: 'var(--dim)' }} aria-label={t('skus.list_nothing_to_order')}>—</span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
