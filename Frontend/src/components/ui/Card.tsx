'use client'
/**
 * Card — the panel every screen is built out of.
 *
 * The shape was already unanimous in the codebase (`var(--surface)`, a 1px
 * `var(--border)` hairline, 12px radius); what drifted was that each screen
 * spelled it out inline, so the radius quietly ranged 10–14 and no redesign
 * could reach all of them at once.
 *
 * 12px is the standardised radius because it is what ~20 call sites already
 * render. The `.card` rule in globals.css says 10px, but nothing uses it —
 * it lost the vote.
 *
 * `tone="inset"` is the nested panel (a card inside a card): `--surface-2`
 * with a 10px radius, which is the second shape the app already had.
 */
import type { ReactNode } from 'react'

export type CardTone = 'surface' | 'inset'

const TONE: Record<CardTone, { background: string; radius: number }> = {
  surface: { background: 'var(--surface)', radius: 12 },
  inset:   { background: 'var(--surface-2)', radius: 10 },
}

export interface CardProps extends Omit<React.HTMLAttributes<HTMLDivElement>, 'title'> {
  tone?: CardTone
  /** Inner padding. `0` for cards whose only child is a table or a header strip. */
  padding?: number | string
  radius?: number
  /** `hidden` clips a table's corners to the card radius. */
  overflow?: React.CSSProperties['overflow']
  /** Adds the accent hairline used to mark the selected/current card. */
  highlighted?: boolean
  children?: ReactNode
}

export default function Card({
  tone = 'surface', padding = '18px 20px', radius, overflow, highlighted,
  style, children, ...props
}: CardProps) {
  const t = TONE[tone]
  return (
    <div
      {...props}
      style={{
        background: t.background,
        border: highlighted ? '1px solid var(--accent)' : '1px solid var(--border)',
        borderRadius: radius ?? t.radius,
        padding,
        overflow,
        ...style,
      }}
    >
      {children}
    </div>
  )
}

/**
 * The title strip at the top of a card, separated by the same hairline as the
 * card's own border. Use inside a `padding={0}` Card so the strip spans the
 * full width and the body below can set its own padding.
 */
export function CardHeader({
  title, subtitle, actions, icon, padding = '14px 18px', style, titleStyle, children,
}: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  icon?: ReactNode
  padding?: number | string
  style?: React.CSSProperties
  titleStyle?: React.CSSProperties
  children?: ReactNode
}) {
  return (
    <div
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        gap: 12, padding, borderBottom: '1px solid var(--border)',
        ...style,
      }}
    >
      {children ?? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          {icon}
          <div style={{ minWidth: 0 }}>
            {title && <CardTitle style={titleStyle}>{title}</CardTitle>}
            {subtitle && <CardSubtitle>{subtitle}</CardSubtitle>}
          </div>
        </div>
      )}
      {actions && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>{actions}</div>
      )}
    </div>
  )
}

// Weight 600, not 700: that is what the `.card-title` rule in globals.css has
// always declared, and what every card header on screen already renders.
export function CardTitle({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', letterSpacing: '-0.01em', ...style }}>
      {children}
    </div>
  )
}

export function CardSubtitle({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2, lineHeight: 1.5, ...style }}>
      {children}
    </div>
  )
}
