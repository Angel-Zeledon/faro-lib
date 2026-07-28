'use client'
/**
 * Data table — header, cell and row geometry in one place.
 *
 * Every list screen used to declare a local `th` / `td` / `TD` constant. They
 * agreed on the shape — an uppercase dim header over a `var(--border)`
 * hairline, rows divided by the same hairline — and disagreed on every number:
 * six different cell paddings, four table font sizes, two header weights.
 *
 * The three steps below are the three clusters those six paddings fall into,
 * not new values:
 *
 *   sm  12px table, 10px header, 8-10 / 9-10 cells    dense summaries with few
 *                                                     columns (/scenarios)
 *   md  13px table, 11px header, 8-12 / 10-12 cells   the `.data-table` rule in
 *                                                     globals.css — the default
 *   lg  12px table, 10px header, 9-14 / 11-14 cells   wide catalogues: smaller
 *                                                     type, more horizontal air
 *                                                     (/suppliers, scorecard,
 *                                                     /inventory/roi, PO history)
 *
 * Header weight standardises on 600, which is what `.data-table` declares; the
 * screens that had reached for 700 were the ones that never saw the rule.
 *
 * `tableStyle`, `thStyle` and `tdStyle` are exported as plain style objects so
 * a virtualized grid — which renders divs with `role="row"` rather than real
 * `<tr>` elements — lines up pixel for pixel with the normal tables beside it
 * without importing the components.
 */
import type { ReactNode } from 'react'

export type TableSize = 'sm' | 'md' | 'lg'

const SIZES: Record<TableSize, {
  fontSize: number
  th: { padding: string; fontSize: number; letterSpacing: string }
  td: { padding: string }
}> = {
  sm: {
    fontSize: 12,
    th: { padding: '8px 10px', fontSize: 10, letterSpacing: '0.06em' },
    td: { padding: '9px 10px' },
  },
  md: {
    fontSize: 13,
    th: { padding: '8px 12px', fontSize: 11, letterSpacing: '0.05em' },
    td: { padding: '10px 12px' },
  },
  lg: {
    fontSize: 12,
    th: { padding: '9px 14px', fontSize: 10, letterSpacing: '0.06em' },
    td: { padding: '11px 14px' },
  },
}

export type CellAlign = 'left' | 'right' | 'center'

/** Base `<table>` style. `minWidth` forces the wrapper to scroll instead of squashing. */
export function tableStyle(size: TableSize = 'md', minWidth?: number): React.CSSProperties {
  return {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: SIZES[size].fontSize,
    ...(minWidth ? { minWidth } : {}),
  }
}

/**
 * Where a cell draws its hairline. `top` is for tables that separate rows with
 * a rule above rather than below — the header then needs `divider={false}`, or
 * the two rules stack into a 2px line under it.
 */
export type CellDivider = boolean | 'top'

function dividerStyle(divider: CellDivider): React.CSSProperties {
  if (divider === 'top') return { borderTop: '1px solid var(--border)' }
  return divider ? { borderBottom: '1px solid var(--border)' } : {}
}

/** Header cell style, for callers rendering their own markup. */
export function thStyle(
  size: TableSize = 'md', align: CellAlign = 'left', divider: CellDivider = true,
): React.CSSProperties {
  return {
    ...SIZES[size].th,
    textAlign: align,
    color: 'var(--dim)',
    fontWeight: 600,
    textTransform: 'uppercase',
    whiteSpace: 'nowrap',
    ...dividerStyle(divider),
  }
}

/** Body cell style, for callers rendering their own markup. */
export function tdStyle(
  size: TableSize = 'md', align: CellAlign = 'left', divider: CellDivider = true,
): React.CSSProperties {
  return {
    ...SIZES[size].td,
    textAlign: align,
    color: 'var(--text)',
    ...dividerStyle(divider),
  }
}

/* ── Components ────────────────────────────────────────────────────────────── */

export interface TableProps extends React.TableHTMLAttributes<HTMLTableElement> {
  size?: TableSize
  /** Below this width the wrapper scrolls horizontally rather than squashing columns. */
  minWidth?: number
  /** Set false when the table is already inside a scrolling container. */
  scroll?: boolean
  wrapperStyle?: React.CSSProperties
  children?: ReactNode
}

/**
 * A `<table>` inside its own horizontal scroller. The scroller is the default
 * because a table that squashes below its minimum is unreadable on a phone,
 * and every screen that got this right had hand-rolled the same wrapper div.
 */
export default function Table({
  size = 'md', minWidth, scroll = true, wrapperStyle, className, style, children, ...props
}: TableProps) {
  const table = (
    <table {...props} className={className} style={{ ...tableStyle(size, minWidth), ...style }}>
      {children}
    </table>
  )
  return scroll
    ? <div style={{ overflowX: 'auto', ...wrapperStyle }}>{table}</div>
    : table
}

export interface ThProps extends Omit<React.ThHTMLAttributes<HTMLTableCellElement>, 'align'> {
  size?: TableSize
  align?: CellAlign
  /** Off for tables whose rows carry a top rule instead (see `CellDivider`). */
  divider?: CellDivider
  /** Renders the sort affordance and a pointer cursor. */
  sortable?: boolean
  children?: ReactNode
}

export function Th({
  size = 'md', align = 'left', divider = true, sortable, style, children, ...props
}: ThProps) {
  return (
    <th
      {...props}
      style={{
        ...thStyle(size, align, divider),
        ...(sortable ? { cursor: 'pointer', userSelect: 'none' } : {}),
        ...style,
      }}
    >
      {children}
    </th>
  )
}

export interface TdProps extends Omit<React.TdHTMLAttributes<HTMLTableCellElement>, 'align'> {
  size?: TableSize
  align?: CellAlign
  /** Off for rows that carry the hairline on the `<tr>`; `'top'` to rule above. */
  divider?: CellDivider
  nowrap?: boolean
  /** Tabular figures for quantities and money, so columns of numbers line up. */
  mono?: boolean
  children?: ReactNode
}

export function Td({
  size = 'md', align = 'left', divider = true, nowrap, mono, style, children, ...props
}: TdProps) {
  return (
    <td
      {...props}
      style={{
        ...tdStyle(size, align, divider),
        ...(nowrap ? { whiteSpace: 'nowrap' } : {}),
        ...(mono ? { fontFamily: 'ui-monospace, monospace' } : {}),
        ...style,
      }}
    >
      {children}
    </td>
  )
}

export interface TrProps extends React.HTMLAttributes<HTMLTableRowElement> {
  /** Tints the row on hover — use only when the row is clickable. */
  hoverable?: boolean
  children?: ReactNode
}

export function Tr({ hoverable, style, children, ...props }: TrProps) {
  return (
    <tr
      {...props}
      style={{ ...(hoverable ? { cursor: 'pointer' } : {}), ...style }}
      onMouseEnter={e => {
        if (hoverable) e.currentTarget.style.background = 'var(--surface-2)'
        props.onMouseEnter?.(e)
      }}
      onMouseLeave={e => {
        if (hoverable) e.currentTarget.style.background = ''
        props.onMouseLeave?.(e)
      }}
    >
      {children}
    </tr>
  )
}
