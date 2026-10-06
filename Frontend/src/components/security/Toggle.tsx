'use client'

/** A switch (same look as the one in Mi cuenta) that says what it is to a screen reader. */
export function Toggle({ on, onChange, label }: { on: boolean; onChange: () => void; label: string }) {
  return (
    <button
      type="button" role="switch" aria-checked={on} aria-label={label}
      onClick={onChange}
      style={{
        all: 'unset', cursor: 'pointer',
        width: 42, height: 22, borderRadius: 11,
        background: on ? 'var(--accent)' : 'var(--border-strong)',
        transition: 'background 0.2s',
        position: 'relative', flexShrink: 0,
      }}
    >
      <span style={{
        position: 'absolute', top: 3, left: on ? 22 : 3,
        width: 16, height: 16, borderRadius: '50%', background: '#fff',
        transition: 'left 0.2s', boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
      }} />
    </button>
  )
}
