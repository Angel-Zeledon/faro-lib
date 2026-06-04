import clsx from 'clsx'

type Variant = 'default' | 'success' | 'warning' | 'danger' | 'info' | 'muted'

const STYLES: Record<Variant, { bg: string; color: string }> = {
  default: { bg: 'var(--accent-dim)',           color: 'var(--accent)' },
  success: { bg: 'rgba(34,197,94,0.12)',         color: '#22c55e' },
  warning: { bg: 'rgba(245,158,11,0.12)',        color: '#f59e0b' },
  danger:  { bg: 'rgba(239,68,68,0.12)',         color: '#ef4444' },
  info:    { bg: 'rgba(14,165,233,0.12)',         color: '#0ea5e9' },
  muted:   { bg: 'rgba(100,116,139,0.12)',        color: 'var(--dim)' },
}

interface BadgeProps {
  variant?: Variant
  children: React.ReactNode
  dot?: boolean
  pulse?: boolean
  className?: string
  style?: React.CSSProperties
}

export default function Badge({ variant = 'default', children, dot, pulse, className, style }: BadgeProps) {
  const { bg, color } = STYLES[variant]
  return (
    <span
      className={clsx('inline-flex items-center gap-1.5', className)}
      style={{
        padding: '2px 8px', borderRadius: 5,
        fontSize: 11, fontWeight: 600,
        background: bg, color,
        letterSpacing: '0.01em',
        ...style,
      }}
    >
      {(dot || pulse) && (
        <span style={{
          width: 5, height: 5, borderRadius: '50%',
          background: color, flexShrink: 0,
          animation: pulse ? 'pulse-dot 1.4s ease-in-out infinite' : undefined,
        }} />
      )}
      {children}
    </span>
  )
}
