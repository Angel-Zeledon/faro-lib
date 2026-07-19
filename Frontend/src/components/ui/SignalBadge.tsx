'use client'
/**
 * Inventory signal — one accessible presentation for the whole app (feature 2.8).
 *
 * The signal is THE central artefact of the product, so it must not rely on
 * colour as its only channel of meaning (WCAG 1.4.1 "Use of Color"): every
 * state ALWAYS carries an icon + a text label in addition to the colour.
 *
 * This config used to be duplicated in `/inventory` and in
 * `SkuSearchOverlay`, with hardcoded colours that failed contrast in light
 * theme. It now lives here and the colours come from per-theme CSS variables
 * (see `--signal-*` in globals.css), verified at >=4.5:1.
 */
import { AlertTriangle, Clock, CheckCircle2, TrendingDown, HelpCircle } from 'lucide-react'
import type { InventorySignal } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'

interface SignalStyle {
  labelKey: string
  fg:   string
  bg:   string
  icon: React.ElementType
}

export const SIGNAL_STYLES: Record<InventorySignal, SignalStyle> = {
  PEDIR_YA: {
    labelKey: 'inventory.signal_order_now',
    fg: 'var(--signal-order-now-fg)', bg: 'var(--signal-order-now-bg)',
    icon: AlertTriangle,
  },
  PEDIR_PRONTO: {
    labelKey: 'inventory.signal_order_soon',
    fg: 'var(--signal-order-soon-fg)', bg: 'var(--signal-order-soon-bg)',
    // Clock, not triangle: "soon" is temporal urgency, and reusing the same
    // icon as PEDIR_YA would leave colour as the only differentiator again.
    icon: Clock,
  },
  OK: {
    labelKey: 'inventory.signal_ok',
    fg: 'var(--signal-ok-fg)', bg: 'var(--signal-ok-bg)',
    icon: CheckCircle2,
  },
  SOBRESTOCK: {
    labelKey: 'inventory.signal_overstock',
    fg: 'var(--signal-overstock-fg)', bg: 'var(--signal-overstock-bg)',
    icon: TrendingDown,
  },
  SIN_DATOS: {
    labelKey: 'inventory.signal_sin_datos',
    fg: 'var(--signal-sin-datos-fg)', bg: 'var(--signal-sin-datos-bg)',
    icon: HelpCircle,
  },
}

export const SIGNAL_ORDER: InventorySignal[] = [
  'PEDIR_YA', 'PEDIR_PRONTO', 'SOBRESTOCK', 'OK', 'SIN_DATOS',
]

/** Color de primer plano del semáforo, para texto/bordes fuera del badge. */
export function signalColor(signal: InventorySignal | string | null | undefined): string {
  const s = SIGNAL_STYLES[signal as InventorySignal]
  return s ? s.fg : 'var(--signal-sin-datos-fg)'
}

/** Etiqueta traducida — hook, para usar donde ya hay contexto de idioma. */
export function useSignalLabel() {
  const { t } = useLanguage()
  return (signal: InventorySignal | string | null | undefined) => {
    const s = SIGNAL_STYLES[signal as InventorySignal]
    return s ? t(s.labelKey) : t('inventory.signal_sin_datos')
  }
}

interface SignalBadgeProps {
  signal: InventorySignal | string | null | undefined
  /** `sm` para tablas densas, `md` para tarjetas y cabeceras. */
  size?: 'sm' | 'md'
  /** Oculta la etiqueta visualmente pero la deja para lectores de pantalla.
   *  Úsalo SÓLO donde el texto ya aparece adyacente — nunca como default. */
  iconOnly?: boolean
  className?: string
  style?: React.CSSProperties
}

export default function SignalBadge({
  signal, size = 'sm', iconOnly = false, className, style,
}: SignalBadgeProps) {
  const { t } = useLanguage()
  const cfg = SIGNAL_STYLES[signal as InventorySignal] ?? SIGNAL_STYLES.SIN_DATOS
  const Icon = cfg.icon
  const label = t(cfg.labelKey)
  const iconSize = size === 'md' ? 13 : 11

  return (
    <span
      className={className}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        padding: size === 'md' ? '4px 11px' : '3px 9px',
        borderRadius: 20,
        fontSize: size === 'md' ? 12 : 11,
        fontWeight: 700,
        lineHeight: 1.3,
        whiteSpace: 'nowrap',
        background: cfg.bg,
        color: cfg.fg,
        ...style,
      }}
    >
      <Icon size={iconSize} aria-hidden="true" style={{ flexShrink: 0 }} />
      {iconOnly ? <span className="sr-only">{label}</span> : label}
    </span>
  )
}
