'use client'
import { useState, useRef, useEffect } from 'react'

interface OverrideCellProps {
  value:       number | null
  original:    number | null
  overridden:  boolean
  onSave:      (newValue: number) => void
}

export default function OverrideCell({ value, original, overridden, onSave }: OverrideCellProps) {
  const [editing, setEditing] = useState(false)
  const [draft,   setDraft]   = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus()
      inputRef.current.select()
    }
  }, [editing])

  const commit = () => {
    const n = parseFloat(draft)
    if (!isNaN(n)) onSave(n)
    setEditing(false)
  }

  if (value === null) return <span>—</span>

  if (editing) {
    return (
      <input
        ref={inputRef}
        type="number"
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={e => {
          if (e.key === 'Enter') commit()
          if (e.key === 'Escape') setEditing(false)
        }}
        style={{
          width: 90, fontSize: 12, padding: '2px 6px', borderRadius: 4,
          border: '1px solid var(--accent)', background: 'var(--surface)',
          color: 'var(--text)', outline: 'none',
        }}
      />
    )
  }

  return (
    <span
      onDoubleClick={() => { setDraft(String(value)); setEditing(true) }}
      title={overridden ? `Original: ${original?.toFixed(4)} — double-click to edit` : 'Double-click to override'}
      style={{ cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5 }}
    >
      {value.toFixed(4)}
      {overridden && (
        <span style={{
          fontSize: 9, padding: '1px 5px', borderRadius: 4,
          background: 'rgba(245,158,11,0.15)', color: '#f59e0b', fontWeight: 600,
        }}>
          edited
        </span>
      )}
    </span>
  )
}
