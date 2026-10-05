'use client'
// "Update with the new sales": appears only when the session's data has changed
// since the forecast was made, and says in one line what pressing it does.
// A re-forecast is a NEW session (the one on screen is never modified) and no
// model is retrained; families that cannot be updated are named up front.

import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { getReforecastStatus, startReforecast } from '@/lib/api'
import type { ReforecastStatus } from '@/lib/api'
import { getUser } from '@/lib/auth'
import { useLanguage } from '@/contexts/LanguageContext'
import { modelLabel } from '@/lib/modelLabel'
import Button from '@/components/ui/Button'

export default function ReforecastAction({ sessionId }: { sessionId: string | null }) {
  const { t } = useLanguage()
  const [status, setStatus] = useState<ReforecastStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [started, setStarted] = useState(false)
  const [failed, setFailed] = useState(false)
  const user = getUser()
  const canEdit = user?.role === 'admin' || user?.role === 'analyst'

  useEffect(() => {
    setStatus(null); setStarted(false); setFailed(false)
    if (!sessionId) return
    let live = true
    getReforecastStatus(sessionId).then(s => { if (live) setStatus(s) }).catch(() => {})
    return () => { live = false }
  }, [sessionId])

  if (!sessionId || !status || !status.eligible) return null

  const go = async () => {
    setBusy(true); setFailed(false)
    try {
      await startReforecast(sessionId, status.newer_dataset_id)
      setStarted(true)
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  const refit = status.refit_families.map(f => modelLabel(t, f)).join(', ')

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                  padding: '8px 12px', marginBottom: 12, fontSize: 12.5,
                  border: '1px solid var(--border)', borderRadius: 8, background: 'var(--surface-2)' }}>
      <span style={{ flex: 1, minWidth: 220, color: 'var(--muted)' }}>
        {started
          ? t('reforecast.started')
          : failed
            ? t('reforecast.failed')
            : refit
              ? t('reforecast.what_with_refit', { families: refit })
              : t('reforecast.what')}
      </span>
      {canEdit && !started && (
        <Button size="sm" variant="secondary" loading={busy} onClick={go}
                icon={<RefreshCw size={13} />}>
          {t('reforecast.action')}
        </Button>
      )}
    </div>
  )
}
