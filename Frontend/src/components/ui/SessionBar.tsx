'use client'
import { useState, useRef, useEffect } from 'react'
import { BarChart2, ChevronDown, RefreshCw } from 'lucide-react'
import type { SessionInfo } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'

interface SessionBarProps {
  currentSession:    SessionInfo | undefined
  completedSessions: SessionInfo[]
  sessionId:         string
  onSelect:          (id: string) => void
  loading?:          boolean
  onRefresh?:        () => void
}

export default function SessionBar({
  currentSession, completedSessions, sessionId,
  onSelect, loading = false, onRefresh,
}: SessionBarProps) {
  const { t } = useLanguage()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    function handle(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [])

  function relativeTime(iso: string) {
    const diff = Date.now() - new Date(iso).getTime()
    const days = Math.floor(diff / 86_400_000)
    if (days === 0) return 'hoy'
    if (days === 1) return 'hace 1 día'
    if (days < 7)  return `hace ${days} días`
    const weeks = Math.floor(days / 7)
    return weeks === 1 ? 'hace 1 semana' : `hace ${weeks} semanas`
  }

  if (!completedSessions.length && !loading) return null

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '5px 10px', borderRadius: 8,
        background: 'var(--surface-2)', border: '1px solid var(--border)',
        fontSize: 12,
      }}>
        <BarChart2 size={12} color="var(--dim)" />
        <span style={{ color: 'var(--dim)' }}>Forecast:</span>
        {loading ? (
          <span style={{ color: 'var(--muted)' }}>Cargando…</span>
        ) : currentSession ? (
          <>
            <span style={{ fontWeight: 600, color: 'var(--text)', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }}>
              {currentSession.name}
            </span>
            <span style={{ color: 'var(--dim)' }}>·</span>
            <span style={{ color: 'var(--dim)' }}>
              {relativeTime(currentSession.updated_at)}
            </span>
          </>
        ) : (
          <span style={{ color: 'var(--signal-order-soon-fg)' }}>{t('common.no_session')}</span>
        )}

        {completedSessions.length > 1 && (
          <button
            onClick={() => setOpen(v => !v)}
            style={{
              all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center',
              gap: 3, color: 'var(--accent)', fontSize: 11, fontWeight: 600,
              padding: '1px 6px', borderRadius: 5,
              background: open ? 'var(--accent-dim)' : 'transparent',
            }}
          >
            Cambiar <ChevronDown size={10} style={{ transform: open ? 'rotate(180deg)' : undefined, transition: 'transform 0.15s' }} />
          </button>
        )}

        {onRefresh && (
          <button
            onClick={onRefresh}
            title="Actualizar datos"
            style={{ all: 'unset', cursor: 'pointer', display: 'flex', color: 'var(--dim)', padding: 2 }}
          >
            <RefreshCw size={11} />
          </button>
        )}
      </div>

      {/* Dropdown */}
      {open && completedSessions.length > 1 && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 200,
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 10, minWidth: 240, boxShadow: '0 8px 24px rgba(0,0,0,0.25)',
          overflow: 'hidden',
        }}>
          <div style={{ padding: '6px 12px 4px', fontSize: 10, fontWeight: 700, color: 'var(--dim)', textTransform: 'uppercase' as const, letterSpacing: '0.07em' }}>
            Sesiones entrenadas
          </div>
          {completedSessions.map(s => (
            <button
              key={s.session_id}
              onClick={() => { onSelect(s.session_id); setOpen(false) }}
              style={{
                all: 'unset', cursor: 'pointer', width: '100%', boxSizing: 'border-box' as const,
                display: 'flex', flexDirection: 'column' as const, gap: 1,
                padding: '8px 12px', fontSize: 12,
                background: s.session_id === sessionId ? 'var(--accent-dim)' : 'transparent',
                color: s.session_id === sessionId ? 'var(--accent)' : 'var(--text)',
                borderBottom: '1px solid var(--border)',
              }}
              onMouseEnter={e => { if (s.session_id !== sessionId) (e.currentTarget as HTMLButtonElement).style.background = 'var(--surface-2)' }}
              onMouseLeave={e => { if (s.session_id !== sessionId) (e.currentTarget as HTMLButtonElement).style.background = 'transparent' }}
            >
              <span style={{ fontWeight: s.session_id === sessionId ? 700 : 500 }}>{s.name}</span>
              <span style={{ fontSize: 10, color: 'var(--dim)' }}>{relativeTime(s.updated_at)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
