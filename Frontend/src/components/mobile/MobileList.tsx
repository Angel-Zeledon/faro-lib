'use client'
import Link from 'next/link'
import { ChevronRight } from 'lucide-react'
import type { InventorySignal } from '@/lib/types'

/**
 * Lists of cards — what a desktop table becomes on a phone.
 *
 * A table row with eight columns cannot shrink into 360px; it scrolls sideways
 * and hides the one number the person came for. A card keeps the two or three
 * facts that matter (title, subtitle, a value on the right, a status) and puts
 * the rest one tap away, on a detail view or in a BottomSheet.
 *
 * ```tsx
 * <MobileList ariaLabel={t('orders.page_title')}>
 *   {orders.map(o => (
 *     <MobileCard
 *       key={o.id}
 *       href={`/pedidos?po=${o.id}`}            // or onClick={() => open(o)}
 *       title={o.po_number}
 *       subtitle={`${o.supplier} · ${fmtDate(o.date)}`}
 *       value={formatMoney(o.total)}
 *       valueCaption={t('mobile.pedidos_card_skus', { n: o.lines })}
 *       status={{ label: t('po.reception_pending'), tone: 'warning' }}
 *     />
 *   ))}
 * </MobileList>
 * ```
 *
 * Every card is at least 56px tall (the tap target is the whole card), shows a
 * chevron when it navigates or opens something, and is a real <a> or <button>
 * so it is keyboard- and screen-reader-operable. A card with neither `href`
 * nor `onClick` renders as static content with no chevron.
 */

// ── Status badge ─────────────────────────────────────────────────────────────

export type StatusTone = 'danger' | 'warning' | 'success' | 'info' | 'neutral'

const TONE: Record<StatusTone, { fg: string; bg: string }> = {
  danger:  { fg: 'var(--signal-order-now-fg)',  bg: 'var(--signal-order-now-bg)' },
  warning: { fg: 'var(--signal-order-soon-fg)', bg: 'var(--signal-order-soon-bg)' },
  success: { fg: 'var(--signal-ok-fg)',         bg: 'var(--signal-ok-bg)' },
  info:    { fg: 'var(--signal-overstock-fg)',  bg: 'var(--signal-overstock-bg)' },
  neutral: { fg: 'var(--signal-sin-datos-fg)',  bg: 'var(--signal-sin-datos-bg)' },
}

/** The stock semáforo mapped onto badge tones — same colours as desktop. */
export function signalTone(signal: InventorySignal | string | null | undefined): StatusTone {
  switch (signal) {
    case 'PEDIR_YA': return 'danger'
    case 'PEDIR_PRONTO': return 'warning'
    case 'OK': return 'success'
    case 'SOBRESTOCK': return 'info'
    default: return 'neutral'
  }
}

export function StatusBadge({ label, tone = 'neutral' }: { label: React.ReactNode; tone?: StatusTone }) {
  const c = TONE[tone]
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap',
      padding: '2px 8px', borderRadius: 999, fontSize: 11, fontWeight: 700,
      color: c.fg, background: c.bg,
    }}>
      {label}
    </span>
  )
}

// ── List + card ──────────────────────────────────────────────────────────────

export function MobileList({ children, ariaLabel, inset = true, style }: {
  children: React.ReactNode
  ariaLabel?: string
  /** Rounded card group with a border (default). `false` = edge-to-edge rows. */
  inset?: boolean
  style?: React.CSSProperties
}) {
  return (
    <ul
      aria-label={ariaLabel}
      className="mobile-list"
      style={{
        listStyle: 'none', margin: 0, padding: 0,
        background: 'var(--surface)',
        ...(inset ? { border: '1px solid var(--border)', borderRadius: 14, overflow: 'hidden' } : {}),
        ...style,
      }}
    >
      {children}
    </ul>
  )
}

export interface MobileCardProps {
  title: React.ReactNode
  subtitle?: React.ReactNode
  /** Right-aligned headline figure (a quantity, an amount). */
  value?: React.ReactNode
  /** Small line under `value`. */
  valueCaption?: React.ReactNode
  status?: { label: React.ReactNode; tone?: StatusTone }
  /** Icon or avatar on the left. */
  leading?: React.ReactNode
  /** Extra content under the title row (chips, a progress bar, actions). */
  children?: React.ReactNode
  href?: string
  onClick?: () => void
  /** Override the chevron (shown by default when href/onClick is set). */
  chevron?: boolean
  selected?: boolean
  ariaLabel?: string
}

export function MobileCard({
  title, subtitle, value, valueCaption, status, leading, children,
  href, onClick, chevron, selected, ariaLabel,
}: MobileCardProps) {
  const interactive = !!href || !!onClick
  const showChevron = chevron ?? interactive

  const body = (
    <>
      {leading && <span style={{ flexShrink: 0, display: 'flex', alignItems: 'center' }}>{leading}</span>}
      <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <span style={{
            fontSize: 15, fontWeight: 600, color: 'var(--text)', minWidth: 0,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>{title}</span>
          {status && <StatusBadge label={status.label} tone={status.tone} />}
        </span>
        {subtitle && (
          <span style={{
            fontSize: 13, color: 'var(--muted)', lineHeight: 1.35,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>{subtitle}</span>
        )}
        {children && <span style={{ display: 'block', marginTop: 4 }}>{children}</span>}
      </span>
      {(value !== undefined || valueCaption) && (
        <span style={{ flexShrink: 0, textAlign: 'right', display: 'flex', flexDirection: 'column', gap: 2, maxWidth: '45%' }}>
          {value !== undefined && (
            <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{value}</span>
          )}
          {valueCaption && <span style={{ fontSize: 11.5, color: 'var(--dim)', whiteSpace: 'nowrap' }}>{valueCaption}</span>}
        </span>
      )}
      {showChevron && <ChevronRight size={18} color="var(--dim)" aria-hidden="true" style={{ flexShrink: 0 }} />}
    </>
  )

  const rowStyle: React.CSSProperties = {
    all: 'unset', boxSizing: 'border-box', width: '100%',
    display: 'flex', alignItems: 'center', gap: 12,
    minHeight: 56, padding: '10px 14px',
    background: selected ? 'var(--accent-dim)' : 'transparent',
    cursor: interactive ? 'pointer' : 'default',
    textDecoration: 'none', color: 'inherit',
  }

  return (
    <li className="mobile-list-item">
      {href ? (
        <Link href={href} onClick={onClick} aria-label={ariaLabel} className="tap-feedback" style={rowStyle}>{body}</Link>
      ) : onClick ? (
        <button type="button" onClick={onClick} aria-label={ariaLabel} aria-pressed={selected} className="tap-feedback" style={rowStyle}>{body}</button>
      ) : (
        <div style={rowStyle}>{body}</div>
      )}
    </li>
  )
}

// ── Table → cards ────────────────────────────────────────────────────────────

/**
 * The table→cards pattern in one component. Keep the desktop table exactly as
 * it is and branch on `useIsNarrow()`:
 *
 * ```tsx
 * const narrow = useIsNarrow()
 * return narrow
 *   ? <MobileTableCards
 *       rows={items}
 *       getKey={i => i.sku}
 *       toCard={i => ({
 *         title: i.display_name || i.sku,
 *         subtitle: i.sku,
 *         value: fmtNum(i.current_stock),
 *         valueCaption: t('inventory.col_current_stock'),
 *         status: { label: t(`signal.${i.signal}`), tone: signalTone(i.signal) },
 *         onClick: () => setDetail(i),        // detail in a BottomSheet
 *       })}
 *       empty={<EmptyState … />}
 *     />
 *   : <table>…unchanged…</table>
 * ```
 *
 * Choose for each card: the identifying name (title), the one secondary fact
 * (subtitle), the number the person came for (value), and the status. The rest
 * of the columns go to the detail view — do not cram them into the card.
 */
export function MobileTableCards<T>({ rows, getKey, toCard, empty, ariaLabel }: {
  rows: T[]
  getKey: (row: T) => string
  toCard: (row: T) => MobileCardProps
  empty?: React.ReactNode
  ariaLabel?: string
}) {
  if (rows.length === 0) return <>{empty ?? null}</>
  return (
    <MobileList ariaLabel={ariaLabel}>
      {rows.map(row => <MobileCard key={getKey(row)} {...toCard(row)} />)}
    </MobileList>
  )
}
