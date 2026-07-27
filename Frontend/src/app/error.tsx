'use client'
import { useEffect } from 'react'
import Button from '@/components/ui/Button'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  const { t } = useLanguage()
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
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>{t('states.err_unknown_title')}</div>
        {/* `error.message` is a JS exception string — English, and written for
            us, not for the user. The digest below is what support actually
            needs, so the body stays the localized generic sentence. */}
        <div style={{ fontSize: 13, color: 'var(--dim)', maxWidth: 400, lineHeight: 1.5 }}>
          {t('states.err_unknown_body')}
        </div>
        {error.digest && (
          <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 8, fontFamily: 'monospace' }}>
            {t('states.error_id')}: {error.digest}
          </div>
        )}
      </div>
      <Button variant="primary" icon={<RefreshCw size={13} />} onClick={reset}>
        {t('states.retry')}
      </Button>
    </div>
  )
}
