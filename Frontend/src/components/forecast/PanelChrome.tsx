'use client'
import { Package } from 'lucide-react'

// ── Empty state ───────────────────────────────────────────────────────────────

export function PanelPlaceholder({ message }: { message: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12, color: 'var(--dim)', minHeight: 200 }}>
      <Package size={36} strokeWidth={1} style={{ opacity: 0.3 }} />
      <span style={{ fontSize: 13 }}>{message}</span>
    </div>
  )
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

export function TabBar({ tabs, active, onChange, labelFor, tourAnchor, trailing }: { tabs: string[]; active: string; onChange: (tab: string) => void; labelFor?: (tab: string) => string; tourAnchor?: string; trailing?: React.ReactNode }) {
  return (
    <div data-tour={tourAnchor} style={{ display: 'flex', alignItems: 'center', gap: 2, borderBottom: '1px solid var(--border)', padding: '0 16px', background: 'var(--surface)' }}>
      {tabs.map(tabKey => (
        <button
          key={tabKey}
          onClick={() => onChange(tabKey)}
          style={{
            all: 'unset', cursor: 'pointer',
            padding: '9px 12px', fontSize: 12, fontWeight: 500,
            color: tabKey === active ? 'var(--accent)' : 'var(--dim)',
            borderBottom: `2px solid ${tabKey === active ? 'var(--accent)' : 'transparent'}`,
            marginBottom: -1, transition: 'all 0.12s',
          }}
        >
          {labelFor ? labelFor(tabKey) : tabKey}
        </button>
      ))}
      {trailing && <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center' }}>{trailing}</div>}
    </div>
  )
}
