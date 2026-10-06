'use client'
/**
 * /reportes-programados/baja?token=... — "stop sending me this report".
 *
 * Public: the person may be an external address with no account. Opening the
 * page changes nothing; the button does.
 */
import { Suspense, useEffect, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { confirmUnsubscribe, describeUnsubscribeLink, UnsubscribeError } from '@/lib/reportUnsubscribe'
import { useLanguage } from '@/contexts/LanguageContext'

type View =
  | { kind: 'loading' }
  | { kind: 'ready'; name: string | null; already: boolean }
  | { kind: 'done'; name: string | null }
  | { kind: 'invalid' }
  | { kind: 'failed' }

function Content() {
  const { t } = useLanguage()
  const token = useSearchParams().get('token') ?? ''
  const [view, setView] = useState<View>({ kind: 'loading' })
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!token) { setView({ kind: 'invalid' }); return }
    describeUnsubscribeLink(token)
      .then(d => setView({ kind: 'ready', name: d.schedule_name, already: d.already_unsubscribed }))
      .catch(e => setView({ kind: e instanceof UnsubscribeError && e.kind === 'invalid' ? 'invalid' : 'failed' }))
  }, [token])

  async function confirm() {
    setBusy(true)
    try {
      const r = await confirmUnsubscribe(token)
      setView({ kind: 'done', name: r.schedule_name })
    } catch (e) {
      setView({ kind: e instanceof UnsubscribeError && e.kind === 'invalid' ? 'invalid' : 'failed' })
    } finally { setBusy(false) }
  }

  const box: React.CSSProperties = {
    maxWidth: 460, margin: '12vh auto 0', padding: 24, borderRadius: 12,
    border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)',
  }
  return (
    <main style={box}>
      <h1 style={{ margin: '0 0 10px', fontSize: 18 }}>{t('sr.unsub.title')}</h1>
      {view.kind === 'loading' && <p style={{ fontSize: 13 }}>{t('common.loading')}</p>}
      {view.kind === 'invalid' && <p role="alert" style={{ fontSize: 13 }}>{t('sr.unsub.invalid')}</p>}
      {view.kind === 'failed' && <p role="alert" style={{ fontSize: 13 }}>{t('sr.unsub.failed')}</p>}
      {view.kind === 'ready' && (
        <>
          <p style={{ fontSize: 13, lineHeight: 1.5 }}>
            {view.already ? t('sr.unsub.already', { name: view.name ?? '' }) : t('sr.unsub.body', { name: view.name ?? '' })}
          </p>
          {!view.already && (
            <button type="button" disabled={busy} onClick={() => void confirm()} style={{
              all: 'unset', cursor: 'pointer', padding: '8px 16px', borderRadius: 8, fontSize: 13, fontWeight: 600,
              border: '1px solid var(--border)', opacity: busy ? 0.6 : 1,
            }}>{t('sr.unsub.confirm')}</button>
          )}
        </>
      )}
      {view.kind === 'done' && <p style={{ fontSize: 13, lineHeight: 1.5 }}>{t('sr.unsub.done', { name: view.name ?? '' })}</p>}
    </main>
  )
}

export default function UnsubscribePage() {
  return <Suspense fallback={null}><Content /></Suspense>
}
