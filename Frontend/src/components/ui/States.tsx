'use client'
/**
 * Shared empty / loading / error states (feature 2.6).
 *
 * The five daily screens (/hoy, /orders, /skus, /inventory,
 * /inventory/suppliers) each used to hand-roll these three moments, so the
 * same situation looked different depending on where you hit it. These
 * primitives are the single source of truth for that layer:
 *
 *   Skeleton / SkeletonTable / LoadingState — shaped placeholders, never a
 *     bare "loading" string.
 *   EmptyState — sells the next step: every empty screen carries the action
 *     that fills it, not just the news that it is empty.
 *   ErrorState — a legible reason (derived from ApiError.kind, so the copy for
 *     "no permission" is written once) plus a retry affordance.
 */
import type { ReactNode } from 'react'
import Link from 'next/link'
import { RefreshCw, AlertTriangle, Lock, SearchX, WifiOff, ServerCrash } from 'lucide-react'
import { isApiError, type ApiErrorKind } from '@/lib/api'
import { useLanguage } from '@/contexts/LanguageContext'
import type { TranslationKey } from '@/i18n/translations'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  red: '#ef4444', indigo: '#818cf8',
}

/* ── Error copy ────────────────────────────────────────────────────────────── */

/**
 * Maps an error to the copy the user reads. Anything that is not an ApiError
 * (a bug in our own code, most likely) falls back to the generic bucket rather
 * than leaking a stack-trace-ish message into the UI.
 */
export function errorKindOf(err: unknown): ApiErrorKind | 'unknown' {
  return isApiError(err) ? err.kind : 'unknown'
}

const ERROR_COPY: Record<ApiErrorKind | 'unknown', {
  title: TranslationKey; body: TranslationKey; icon: ReactNode; retryable: boolean
}> = {
  session:    { title: 'states.err_session_title',    body: 'states.err_session_body',    icon: <Lock size={22} />,        retryable: false },
  permission: { title: 'states.err_permission_title', body: 'states.err_permission_body', icon: <Lock size={22} />,        retryable: false },
  notfound:   { title: 'states.err_notfound_title',   body: 'states.err_notfound_body',   icon: <SearchX size={22} />,     retryable: true  },
  validation: { title: 'states.err_validation_title', body: 'states.err_validation_body', icon: <AlertTriangle size={22} />, retryable: true },
  server:     { title: 'states.err_server_title',     body: 'states.err_server_body',     icon: <ServerCrash size={22} />, retryable: true  },
  network:    { title: 'states.err_network_title',    body: 'states.err_network_body',    icon: <WifiOff size={22} />,     retryable: true  },
  unknown:    { title: 'states.err_unknown_title',    body: 'states.err_unknown_body',    icon: <AlertTriangle size={22} />, retryable: true },
}

/**
 * Standard toast payload for an error. Shared by the api interceptor bridge and
 * by screens that want to toast an error they caught themselves.
 */
export function useErrorCopy() {
  const { t } = useLanguage()
  return (err: unknown): { title: string; body: string; detail: string } => {
    const copy = ERROR_COPY[errorKindOf(err)]
    // The backend's own reason is more useful than our generic sentence when it
    // exists — a 422 naming the field that failed beats "check the fields".
    const detail = isApiError(err) ? err.detail : err instanceof Error ? err.message : ''
    return { title: t(copy.title), body: t(copy.body), detail }
  }
}

/* ── Skeletons ─────────────────────────────────────────────────────────────── */

/** A single shimmering placeholder block. `.skeleton` lives in globals.css. */
export function Skeleton({ height = 14, width = '100%', radius = 8, style }: {
  height?: number | string; width?: number | string; radius?: number
  style?: React.CSSProperties
}) {
  return <div className="skeleton" style={{ height, width, borderRadius: radius, ...style }} />
}

/** Placeholder shaped like a data table, so the layout does not jump on load. */
export function SkeletonTable({ rows = 6, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div role="status" aria-busy="true" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${columns}, 1fr)`, gap: 12 }}>
        {Array.from({ length: columns }, (_, i) => (
          <Skeleton key={`h${i}`} height={11} width="70%" radius={4} />
        ))}
      </div>
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} style={{ display: 'grid', gridTemplateColumns: `repeat(${columns}, 1fr)`, gap: 12 }}>
          {Array.from({ length: columns }, (_, c) => (
            <Skeleton key={c} height={15} width={c === 0 ? '85%' : '55%'} radius={5} />
          ))}
        </div>
      ))}
    </div>
  )
}

/** Placeholder shaped like a row of KPI cards. */
export function SkeletonCards({ count = 4, height = 74 }: { count?: number; height?: number }) {
  return (
    <div role="status" aria-busy="true" style={{ display: 'grid', gridTemplateColumns: `repeat(${count}, 1fr)`, gap: 12 }}>
      {Array.from({ length: count }, (_, i) => <Skeleton key={i} height={height} radius={10} />)}
    </div>
  )
}

/**
 * Full-region loading state. Always pairs the skeleton with a label so screen
 * readers (and users on a slow link) know what is being fetched.
 */
export function LoadingState({ label, children }: { label?: string; children?: ReactNode }) {
  const { t } = useLanguage()
  const text = label ?? t('states.loading_generic')
  return (
    <div role="status" aria-live="polite" aria-busy="true" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <span style={{ fontSize: 12, color: C.dim }}>{text}</span>
      {children ?? <SkeletonTable />}
    </div>
  )
}

/* ── Empty state ───────────────────────────────────────────────────────────── */

export interface EmptyStateAction {
  label: string
  /** Internal route. Provide either `href` or `onClick`. */
  href?: string
  onClick?: () => void
  icon?: ReactNode
  variant?: 'primary' | 'secondary'
}

/**
 * Designed empty state. `bullets` and `action` are what make it sell the next
 * step instead of merely reporting absence — an empty screen with no way
 * forward is a dead end.
 */
export function EmptyState({ icon, title, body, bullets, actions, compact }: {
  icon?: ReactNode
  title: string
  body?: string
  bullets?: string[]
  actions?: EmptyStateAction[]
  compact?: boolean
}) {
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12,
      padding: compact ? '32px 28px' : '48px 40px',
      maxWidth: 560, margin: '0 auto', width: '100%', textAlign: 'center',
    }}>
      {icon && (
        <div style={{
          width: 48, height: 48, borderRadius: 13, margin: '0 auto 16px',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: 'rgba(129,140,248,0.12)', border: '1px solid rgba(129,140,248,0.28)',
          color: C.indigo,
        }}>
          {icon}
        </div>
      )}

      <h2 style={{ fontSize: compact ? 15 : 17, fontWeight: 700, color: C.text, margin: '0 0 8px' }}>
        {title}
      </h2>
      {body && (
        <p style={{ fontSize: 13, color: C.muted, margin: '0 0 20px', lineHeight: 1.65 }}>{body}</p>
      )}

      {bullets && bullets.length > 0 && (
        <ul style={{
          listStyle: 'none', margin: '0 0 24px', padding: 0, textAlign: 'left',
          display: 'flex', flexDirection: 'column', gap: 8,
        }}>
          {bullets.map(b => (
            <li key={b} style={{ display: 'flex', alignItems: 'flex-start', gap: 9 }}>
              <span style={{
                width: 5, height: 5, borderRadius: '50%', background: C.indigo,
                flexShrink: 0, marginTop: 7,
              }} />
              <span style={{ fontSize: 13, color: C.muted, lineHeight: 1.55 }}>{b}</span>
            </li>
          ))}
        </ul>
      )}

      {actions && actions.length > 0 && (
        <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
          {actions.map(a => {
            const primary = (a.variant ?? 'primary') === 'primary'
            const style: React.CSSProperties = {
              display: 'inline-flex', alignItems: 'center', gap: 7,
              padding: '10px 20px', borderRadius: 9,
              fontSize: 13, fontWeight: 700, textDecoration: 'none',
              cursor: 'pointer',
              background: primary ? C.indigo : 'transparent',
              color: primary ? '#fff' : C.indigo,
              border: primary ? '1px solid transparent' : `1px solid ${C.indigo}`,
            }
            return a.href
              ? <Link key={a.label} href={a.href} style={style}>{a.icon}{a.label}</Link>
              : (
                <button key={a.label} onClick={a.onClick} style={{ all: 'unset', ...style }}>
                  {a.icon}{a.label}
                </button>
              )
          })}
        </div>
      )}
    </div>
  )
}

/* ── Error state ───────────────────────────────────────────────────────────── */

/**
 * Full-region error state: legible reason plus a retry action. `onRetry` is
 * omitted for kinds where retrying cannot help (403 stays 403 until a role
 * changes), and the raw backend detail is shown underneath when present so a
 * support conversation has something concrete to work with.
 */
export function ErrorState({ error, onRetry, compact }: {
  error: unknown
  onRetry?: () => void
  compact?: boolean
}) {
  const { t } = useLanguage()
  const kind = errorKindOf(error)
  const copy = ERROR_COPY[kind]
  const detail = isApiError(error) ? error.detail : error instanceof Error ? error.message : ''
  const showRetry = Boolean(onRetry) && copy.retryable

  return (
    <div role="alert" style={{
      background: C.surface, border: '1px solid rgba(239,68,68,0.28)', borderRadius: 12,
      padding: compact ? '20px 22px' : '36px 32px',
      maxWidth: 520, margin: '0 auto', width: '100%', textAlign: 'center',
    }}>
      <div style={{
        width: 44, height: 44, borderRadius: 12, margin: '0 auto 14px',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(239,68,68,0.1)', color: C.red,
      }}>
        {copy.icon}
      </div>

      <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginBottom: 7 }}>
        {t(copy.title)}
      </div>
      <p style={{ fontSize: 13, color: C.muted, margin: '0 0 6px', lineHeight: 1.6 }}>
        {t(copy.body)}
      </p>

      {detail && (
        <p style={{
          fontSize: 11.5, color: C.dim, margin: '0 0 18px', lineHeight: 1.5,
          fontFamily: 'ui-monospace, monospace', wordBreak: 'break-word',
        }}>
          {detail}
        </p>
      )}

      {showRetry && (
        <button
          onClick={onRetry}
          style={{
            all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 7,
            padding: '9px 20px', borderRadius: 9, background: C.indigo, color: '#fff',
            fontSize: 13, fontWeight: 700,
          }}
        >
          <RefreshCw size={13} /> {t('states.retry')}
        </button>
      )}
    </div>
  )
}

/**
 * Compact inline error banner for secondary failures (a panel inside a screen
 * that otherwise loaded fine). Keeps the same wording as ErrorState.
 */
export function InlineError({ error, onRetry, onDismiss }: {
  error: unknown
  onRetry?: () => void
  onDismiss?: () => void
}) {
  const { t } = useLanguage()
  const kind = errorKindOf(error)
  const copy = ERROR_COPY[kind]
  const detail = isApiError(error) ? error.detail : error instanceof Error ? error.message : ''

  return (
    <div role="alert" style={{
      display: 'flex', alignItems: 'center', gap: 9, padding: '10px 14px', borderRadius: 8,
      background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.2)',
      fontSize: 13, color: C.text,
    }}>
      <AlertTriangle size={14} color={C.red} style={{ flexShrink: 0 }} />
      <span style={{ flex: 1, minWidth: 0 }}>
        {t(copy.title)}
        {detail && <span style={{ color: C.dim }}> — {detail}</span>}
      </span>
      {onRetry && copy.retryable && (
        <button
          onClick={onRetry}
          style={{
            all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5,
            fontSize: 12, fontWeight: 600, color: C.indigo, flexShrink: 0,
          }}
        >
          <RefreshCw size={12} /> {t('states.retry')}
        </button>
      )}
      {onDismiss && (
        <button
          onClick={onDismiss}
          aria-label={t('states.dismiss')}
          style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', flexShrink: 0 }}
        >
          ×
        </button>
      )}
    </div>
  )
}
