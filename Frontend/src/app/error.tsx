'use client'
import { useEffect } from 'react'
import Button from '@/components/ui/Button'
import { AlertTriangle, RefreshCw } from 'lucide-react'

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => { console.error('[page error]', error) }, [error])

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', minHeight: '60vh', gap: 16, padding: 40,
    }}>
      <div style={{
        width: 48, height: 48, borderRadius: 12,
        background: 'rgba(239,68,68,0.1)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <AlertTriangle size={22} color="#ef4444" />
      </div>
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>Something went wrong</div>
        <div style={{ fontSize: 13, color: 'var(--dim)', maxWidth: 400, lineHeight: 1.5 }}>
          {error.message || 'An unexpected error occurred on this page.'}
        </div>
        {error.digest && (
          <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 8, fontFamily: 'monospace' }}>
            Error ID: {error.digest}
          </div>
        )}
      </div>
      <Button variant="primary" icon={<RefreshCw size={13} />} onClick={reset}>
        Try again
      </Button>
    </div>
  )
}
