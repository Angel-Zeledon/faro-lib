'use client'
/**
 * Form controls — the single source of truth for input geometry.
 *
 * Before this file, every screen redeclared its own `inputS` / `inputM` /
 * `inputStyle` local constant and they had quietly drifted apart: eight
 * distinct geometries across the app, differing by a pixel of border radius or
 * two of padding. Nothing looked broken, but a redesign (the mobile view above
 * all) had no single place to change.
 *
 * The scale below collapses those eight into four steps. Each step is a
 * geometry that already existed in the codebase — nothing here is invented:
 *
 *   xs  r5 / 12px / 3-7    dense inline editing inside a data grid row
 *   sm  r6 / 12px / 6-9    compact filter bars and toolbar controls
 *   md  r7 / 13px / 8-12   the `.form-input` rule in globals.css — the default
 *   lg  r8 / 13px / 9-12   roomy modal and wizard forms
 *
 * `md` is the default because it is the geometry `globals.css` already declares
 * as `.form-input`, which is what `/config`, `/settings` and `/skus` render.
 *
 * Every control keeps the `form-input` class so it inherits the two behaviours
 * that only CSS can express — the accent focus border and the dimmed
 * placeholder — while the inline size styles override the geometry. Inline
 * styles always beat a class, so the class is purely additive.
 */
import clsx from 'clsx'
import type { ReactNode } from 'react'

export type InputSize = 'xs' | 'sm' | 'md' | 'lg'

/** Which surface the control sits on. Matches the CSS variables in globals.css. */
export type InputTone = 'inset' | 'surface' | 'bg'

const SIZE_STYLES: Record<InputSize, React.CSSProperties> = {
  xs: { borderRadius: 5, fontSize: 12, padding: '3px 7px' },
  sm: { borderRadius: 6, fontSize: 12, padding: '6px 9px' },
  md: { borderRadius: 7, fontSize: 13, padding: '8px 12px' },
  lg: { borderRadius: 8, fontSize: 13, padding: '9px 12px' },
}

const TONE_BACKGROUND: Record<InputTone, string> = {
  inset:   'var(--surface-2)',
  surface: 'var(--surface)',
  bg:      'var(--bg)',
}

interface ControlOptions {
  size?: InputSize
  tone?: InputTone
  /** `strong` uses `--border-strong`, for controls that must read as editable
   *  against a busy panel. */
  border?: 'default' | 'strong'
  invalid?: boolean
}

/**
 * The bare style object for one control.
 *
 * Exported because a virtualized grid renders its cells as divs, not as real
 * inputs, and still has to line up with the form controls next to it.
 */
export function controlStyle({
  size = 'md', tone = 'inset', border = 'default', invalid = false,
}: ControlOptions = {}): React.CSSProperties {
  return {
    width: '100%',
    boxSizing: 'border-box',
    background: TONE_BACKGROUND[tone],
    border: `1px solid ${invalid ? 'var(--signal-order-now-fg)' : border === 'strong' ? 'var(--border-strong)' : 'var(--border)'}`,
    color: 'var(--text)',
    outline: 'none',
    ...SIZE_STYLES[size],
  }
}

/* ── Input ─────────────────────────────────────────────────────────────────── */

export interface InputProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'>, ControlOptions {}

export default function Input({
  size, tone, border, invalid, className, style, ...props
}: InputProps) {
  return (
    <input
      {...props}
      aria-invalid={invalid || undefined}
      className={clsx('form-input', className)}
      style={{ ...controlStyle({ size, tone, border, invalid }), ...style }}
    />
  )
}

/* ── Select ────────────────────────────────────────────────────────────────── */

export interface SelectProps
  extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'size'>, ControlOptions {
  /**
   * Replaces the OS dropdown arrow with the app's own chevron (`.form-select`).
   * Off by default: most screens render the native arrow today, and switching
   * them all at once would be a visible change, not a refactor.
   */
  chevron?: boolean
}

export function Select({
  size, tone, border, invalid, chevron = false, className, style, children, ...props
}: SelectProps) {
  return (
    <select
      {...props}
      aria-invalid={invalid || undefined}
      className={clsx('form-input', chevron && 'form-select', className)}
      style={{
        ...controlStyle({ size, tone, border, invalid }),
        cursor: 'pointer',
        ...style,
      }}
    >
      {children}
    </select>
  )
}

/* ── Textarea ──────────────────────────────────────────────────────────────── */

export interface TextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement>, ControlOptions {}

export function Textarea({
  size, tone, border, invalid, className, style, ...props
}: TextareaProps) {
  return (
    <textarea
      {...props}
      aria-invalid={invalid || undefined}
      className={clsx('form-input', className)}
      style={{
        ...controlStyle({ size, tone, border, invalid }),
        resize: 'vertical',
        fontFamily: 'inherit',
        lineHeight: 1.5,
        ...style,
      }}
    />
  )
}

/* ── Label + field wrapper ─────────────────────────────────────────────────── */

/**
 * The small caption above a control.
 *
 * `eyebrow` is the uppercase micro-label the app already uses in wizards and
 * dense panels; `plain` is the sentence-case one used in modals. Both variants
 * were hand-rolled on at least four screens before they lived here.
 */
export function FieldLabel({
  htmlFor, variant = 'plain', children, style,
}: {
  htmlFor?: string
  variant?: 'plain' | 'eyebrow'
  children: ReactNode
  style?: React.CSSProperties
}) {
  const base: React.CSSProperties = variant === 'eyebrow'
    ? {
        fontSize: 10, fontWeight: 700, color: 'var(--dim)',
        textTransform: 'uppercase', letterSpacing: '0.05em',
      }
    : { fontSize: 12, fontWeight: 600, color: 'var(--muted)' }

  return (
    <label htmlFor={htmlFor} style={{ display: 'block', marginBottom: 4, ...base, ...style }}>
      {children}
    </label>
  )
}

/**
 * A label stacked on top of its control, with room underneath for a hint or a
 * validation message. The wrapper exists so the label/control gap is decided
 * once instead of per screen.
 */
export function Field({
  label, htmlFor, variant, hint, error, children, style, labelStyle, hintStyle,
}: {
  label?: ReactNode
  htmlFor?: string
  variant?: 'plain' | 'eyebrow'
  hint?: ReactNode
  error?: ReactNode
  children: ReactNode
  style?: React.CSSProperties
  labelStyle?: React.CSSProperties
  hintStyle?: React.CSSProperties
}) {
  return (
    <div style={style}>
      {label && (
        <FieldLabel htmlFor={htmlFor} variant={variant} style={labelStyle}>{label}</FieldLabel>
      )}
      {children}
      {error
        ? (
          <div role="alert" style={{ fontSize: 11.5, color: 'var(--signal-order-now-fg)', marginTop: 4, lineHeight: 1.45 }}>
            {error}
          </div>
        )
        : hint
          ? <div style={{ fontSize: 11.5, color: 'var(--dim)', marginTop: 4, lineHeight: 1.45, ...hintStyle }}>{hint}</div>
          : null}
    </div>
  )
}
