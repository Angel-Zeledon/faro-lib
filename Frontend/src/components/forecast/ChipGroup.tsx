'use client'

// ── Chip button group ─────────────────────────────────────────────────────────

export function ChipGroup<T extends string>({ options, value, onChange, label, tourAnchor }: {
  options: { value: T; label: string; icon?: React.ReactNode; title?: string }[]
  value: T; onChange: (v: T) => void; label?: string
  /** Set on the single-session panel only — a tour anchor has to be unique. */
  tourAnchor?: string
}) {
  return (
    <div data-tour={tourAnchor} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      {label && <span style={{ fontSize: 11, color: 'var(--dim)', whiteSpace: 'nowrap' }}>{label}</span>}
      <div style={{ display: 'flex', gap: 2, background: 'var(--surface-2)', borderRadius: 8, padding: 3, border: '1px solid var(--border)' }}>
        {options.map(o => (
          <button
            key={o.value}
            title={o.title ?? o.label}
            onClick={() => onChange(o.value)}
            style={{
              all: 'unset', cursor: 'pointer',
              padding: '3px 9px', borderRadius: 6, fontSize: 11, fontWeight: 500,
              display: 'flex', alignItems: 'center', gap: 4,
              background: value === o.value ? 'var(--accent)' : 'transparent',
              color: value === o.value ? '#fff' : 'var(--dim)',
              transition: 'all 0.12s',
            }}
          >
            {o.icon}{o.label}
          </button>
        ))}
      </div>
    </div>
  )
}
