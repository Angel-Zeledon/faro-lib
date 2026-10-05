'use client'
import Link from 'next/link'
import type { ReactNode } from 'react'

/**
 * A calm status chip for a row: neutral background, hairline border, muted text
 * and at most a tiny dot. It tells the user something about THIS row without
 * becoming an alert box. With `href` it links; with `onClick` it is a button.
 */
const base: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 5, boxSizing: 'border-box',
  maxWidth: '100%', padding: '2px 8px', borderRadius: 999, fontSize: 11, fontWeight: 500,
  lineHeight: 1.4, color: 'var(--muted)', background: 'var(--surface-2)',
  border: '1px solid var(--border)', textDecoration: 'none', whiteSpace: 'nowrap',
}

export default function AttentionChip({ children, href, onClick, dot = true }: {
  children: ReactNode
  href?: string
  onClick?: () => void
  dot?: boolean
}) {
  const inner = (
    <>
      {dot && <span aria-hidden="true" style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--dim)', flexShrink: 0 }} />}
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{children}</span>
    </>
  )
  if (href) return <Link href={href} style={{ ...base, color: 'var(--text)' }}>{inner}</Link>
  if (onClick) {
    return (
      <button type="button" onClick={onClick}
              style={{ ...base, color: 'var(--text)', cursor: 'pointer', font: 'inherit', fontSize: 11, fontWeight: 500 }}>
        {inner}
      </button>
    )
  }
  return <span style={base}>{inner}</span>
}
