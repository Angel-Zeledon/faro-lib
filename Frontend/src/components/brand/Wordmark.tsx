/**
 * The StockAI wordmark — type only, no icon.
 *
 * "stock" carries the surface's text colour and "ai" the brand accent, set in
 * Space Grotesk (loaded once in `app/layout.tsx` as `--font-brand`). The mark
 * is the name itself, so it reads the same at 13px in a footer as at 20px in
 * the sidebar, and there is no glyph to redraw when the theme changes.
 *
 * `compact` is for the collapsed sidebar (48px wide): it keeps the "ai" and
 * drops "stock" to its initial, which still reads as the same mark.
 */
interface WordmarkProps {
  size?: number
  /** Colour of "stock". Defaults to the theme's text colour. */
  color?: string
  /** Colour of "ai". Defaults to the theme's accent. */
  accent?: string
  compact?: boolean
}

export function Wordmark({
  size = 18,
  color = 'var(--text)',
  accent = 'var(--accent)',
  compact = false,
}: WordmarkProps) {
  return (
    <span
      aria-label="StockAI"
      role="img"
      style={{
        fontFamily: 'var(--font-brand), system-ui, sans-serif',
        fontSize: size,
        fontWeight: 600,
        letterSpacing: '-0.045em',
        lineHeight: 1,
        whiteSpace: 'nowrap',
        display: 'inline-flex',
        alignItems: 'baseline',
        userSelect: 'none',
      }}
    >
      <span aria-hidden="true" style={{ color }}>{compact ? 's' : 'stock'}</span>
      <span aria-hidden="true" style={{ color: accent, fontWeight: 700 }}>ai</span>
    </span>
  )
}
