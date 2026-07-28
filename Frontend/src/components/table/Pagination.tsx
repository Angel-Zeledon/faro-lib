'use client'
import { useEffect, useMemo } from 'react'
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'

// A 2.000-SKU catalogue used to be one <tr> per SKU: ~108k DOM nodes on the
// full table and 6.000 inputs in bulk-edit mode, which made scrolling, typing
// and search all wait on React re-rendering the whole catalogue. Both big
// tables now render one page at a time. Pagination — not virtualization —
// because these tables have rows whose height is not knowable in advance
// (the inventory row expands into the calculation panel) and because a paged
// table keeps working with Ctrl+F, Tab order and screen readers, none of which
// survive a windowed list.

export const PAGE_SIZE = 100

export interface Page<T> {
  /** Rows of the current page. */
  rows:      T[]
  /** Total number of pages (never below 1, so an empty list is "page 1 of 1"). */
  pageCount: number
  /** The requested page clamped into range — use this to drive the UI. */
  page:      number
  /** Index of the first row of the page inside the full list (0-based). */
  offset:    number
  total:     number
}

/** Slices `rows` for `page`, clamping the page into range. Pure: safe in render. */
export function paginate<T>(rows: T[], page: number, pageSize: number = PAGE_SIZE): Page<T> {
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize))
  const safePage  = Math.min(Math.max(1, Math.floor(page) || 1), pageCount)
  const offset    = (safePage - 1) * pageSize
  return { rows: rows.slice(offset, offset + pageSize), pageCount, page: safePage, offset, total: rows.length }
}

/** `paginate` memoized, plus the effect that pulls the caller's page state back
 *  into range when filtering shrinks the list under the current page. */
export function usePage<T>(
  rows: T[],
  page: number,
  setPage: (p: number) => void,
  pageSize: number = PAGE_SIZE,
): Page<T> {
  const result = useMemo(() => paginate(rows, page, pageSize), [rows, page, pageSize])
  useEffect(() => {
    if (result.page !== page) setPage(result.page)
  }, [result.page, page, setPage])
  return result
}

// `t` returns the key itself when the catalog has no entry, so a build whose
// copy has not landed yet would print "table.pagination_next" at the user.
// Same guard the inventory page uses: probe, and fall back to real words.
type Translate = (key: string, params?: Record<string, unknown>) => string
function tOr(t: Translate, key: string, fallback: string, params?: Record<string, unknown>): string {
  const text = t(key, params)
  return text === key ? fallback : text
}

const btnBase: React.CSSProperties = {
  all: 'unset', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  width: 26, height: 26, borderRadius: 6, border: '1px solid var(--border)',
  color: 'var(--dim)', boxSizing: 'border-box',
}

function NavButton({ label, disabled, onClick, children }: {
  label: string; disabled: boolean; onClick: () => void; children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      style={{ ...btnBase, cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.35 : 1 }}
    >
      {children}
    </button>
  )
}

/**
 * Page navigation for a table. Renders nothing when everything fits on one
 * page, so a small tenant never sees pagination chrome it does not need.
 *
 * `label` names what is being paged ("SKUs") and is only used to give the nav
 * landmark a distinguishable accessible name when a screen lists them all.
 */
export default function Pagination({ page, pageCount, offset, total, rowsOnPage, onPage, label }: {
  page:       number
  pageCount:  number
  offset:     number
  total:      number
  rowsOnPage: number
  onPage:     (page: number) => void
  label?:     string
}) {
  const { t } = useLanguage()
  if (pageCount <= 1) return null

  const from = total === 0 ? 0 : offset + 1
  const to   = offset + rowsOnPage
  const navLabel = label
    ? tOr(t, 'table.pagination_label_for', `Pagination: ${label}`, { label })
    : tOr(t, 'table.pagination_label', 'Pagination')

  return (
    <nav
      aria-label={navLabel}
      style={{
        display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
        padding: '8px 12px', borderTop: '1px solid var(--border)',
        background: 'var(--surface-2)', flexShrink: 0,
      }}
    >
      {/* Announced on every page change: a keyboard user who pressed "next"
          otherwise gets no feedback that anything happened. */}
      <span aria-live="polite" style={{ fontSize: 11, color: 'var(--dim)', flex: 1, minWidth: 0 }}>
        {tOr(t, 'table.pagination_range', `${from}–${to} of ${total}`, { from, to, total })}
        {' · '}
        {tOr(t, 'table.pagination_page_of', `page ${page} of ${pageCount}`, { page, pages: pageCount })}
      </span>

      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
        <NavButton
          label={tOr(t, 'table.pagination_first', 'First page')}
          disabled={page <= 1}
          onClick={() => onPage(1)}
        >
          <ChevronsLeft size={13} aria-hidden="true" />
        </NavButton>
        <NavButton
          label={tOr(t, 'table.pagination_prev', 'Previous page')}
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          <ChevronLeft size={13} aria-hidden="true" />
        </NavButton>
        <NavButton
          label={tOr(t, 'table.pagination_next', 'Next page')}
          disabled={page >= pageCount}
          onClick={() => onPage(page + 1)}
        >
          <ChevronRight size={13} aria-hidden="true" />
        </NavButton>
        <NavButton
          label={tOr(t, 'table.pagination_last', 'Last page')}
          disabled={page >= pageCount}
          onClick={() => onPage(pageCount)}
        >
          <ChevronsRight size={13} aria-hidden="true" />
        </NavButton>
      </div>
    </nav>
  )
}
