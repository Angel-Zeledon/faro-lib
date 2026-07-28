import clsx from 'clsx'
import Spinner from './Spinner'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size    = 'sm' | 'md' | 'lg'

const VARIANT_STYLES: Record<Variant, React.CSSProperties> = {
  primary:   { background: 'var(--accent)', color: '#fff', border: '1px solid var(--accent)' },
  secondary: { background: 'var(--surface-2)', color: 'var(--text)', border: '1px solid var(--border)' },
  ghost:     { background: 'transparent', color: 'var(--muted)', border: '1px solid transparent' },
  danger:    { background: 'transparent', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)' },
}
const SIZE_STYLES: Record<Size, React.CSSProperties> = {
  sm: { padding: '5px 12px', fontSize: 12, borderRadius: 6 },
  md: { padding: '8px 16px', fontSize: 13, borderRadius: 7 },
  lg: { padding: '10px 22px', fontSize: 14, borderRadius: 8 },
}

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  icon?: React.ReactNode
}

export default function Button({
  variant = 'secondary', size = 'md', loading, icon,
  children, disabled, style, ...props
}: ButtonProps) {
  // `.btn` transitions colour only, never `all`: animating `all` also caught
  // `padding` and the disabled `opacity`, so a button entering its loading state
  // faded oddly. It also carries the :active press, CSS-only so the tap
  // acknowledgement can never delay the click.
  return (
    <button
      {...props}
      className={clsx('btn', props.className)}
      disabled={disabled || loading}
      style={{
        ...VARIANT_STYLES[variant],
        ...SIZE_STYLES[size],
        display: 'inline-flex', alignItems: 'center', gap: 7,
        fontWeight: 500, cursor: disabled || loading ? 'not-allowed' : 'pointer',
        opacity: disabled || loading ? 0.6 : 1,
        outline: 'none', userSelect: 'none',
        ...style,
      }}
    >
      {loading ? <Spinner size={12} /> : icon}
      {children}
    </button>
  )
}
