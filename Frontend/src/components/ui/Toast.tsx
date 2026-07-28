'use client'
import { CheckCircle2, AlertTriangle, Info, X, Undo2 } from 'lucide-react'
import { useToast, type ToastItem } from '@/contexts/ToastContext'

const ICONS = {
  success: <CheckCircle2 size={15} color="#22c55e" style={{ flexShrink: 0, marginTop: 1 }} />,
  error:   <AlertTriangle size={15} color="#ef4444" style={{ flexShrink: 0, marginTop: 1 }} />,
  info:    <Info size={15} color="#0ea5e9" style={{ flexShrink: 0, marginTop: 1 }} />,
}
const BORDER: Record<string, string> = {
  success: 'rgba(34,197,94,0.35)',
  error:   'rgba(239,68,68,0.35)',
  info:    'rgba(14,165,233,0.35)',
}

function ToastRow({ t }: { t: ToastItem }) {
  const { dismiss, runAction } = useToast()
  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 10,
      padding: '12px 14px',
      background: 'var(--surface)',
      border: `1px solid ${BORDER[t.type]}`,
      borderRadius: 10,
      boxShadow: '0 8px 32px rgba(0,0,0,0.28)',
      minWidth: 280, maxWidth: 400,
      animation: t.exiting ? 'toast-out 0.3s ease-in forwards' : 'toast-in 0.25s ease-out',
      pointerEvents: 'auto',
    }}>
      {ICONS[t.type]}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 2 }}>{t.title}</div>
        {t.message && <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.4 }}>{t.message}</div>}
      </div>
      {t.actionLabel && (
        <button
          onClick={() => runAction(t.id)}
          style={{
            all: 'unset', cursor: 'pointer', flexShrink: 0,
            padding: '4px 10px', borderRadius: 7,
            border: '1px solid var(--border)',
            fontSize: 12, fontWeight: 700, color: 'var(--accent)',
          }}
        >
          <Undo2 size={11} style={{ verticalAlign: -1, marginRight: 4 }} aria-hidden="true" />
          {t.actionLabel}
        </button>
      )}
      <button
        onClick={() => dismiss(t.id)}
        style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)', display: 'flex', padding: 2, marginTop: -1 }}
      >
        <X size={13} />
      </button>
    </div>
  )
}

export default function ToastContainer() {
  const { toasts } = useToast()
  if (!toasts.length) return null
  return (
    <div role="status" aria-live="polite" style={{
      position: 'fixed', bottom: 24, right: 24, zIndex: 9999,
      display: 'flex', flexDirection: 'column', gap: 8,
      pointerEvents: 'none',
    }}>
      {toasts.map(t => <ToastRow key={t.id} t={t} />)}
    </div>
  )
}
