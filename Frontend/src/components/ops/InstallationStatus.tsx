'use client'
/**
 * Compact "Installation status" panel for the instance operator.
 *
 * Reads `GET /service-config/ops` (operator-only). One line per check with a
 * state pill, then the three numbers an operator asks first: queue, failures in
 * 24 h, and the slowest route families. A reading the backend could not take is
 * shown as unknown - never as fine.
 */
import { useCallback, useEffect, useState } from 'react'
import { getOpsSnapshot, type OpsSnapshot } from '@/lib/api'
import { SERVICE_CONFIG } from '@/i18n/serviceConfig'
import { useLanguage } from '@/contexts/LanguageContext'
import Card from '@/components/ui/Card'
import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'

const TONE: Record<string, { color: string; bg: string }> = {
  ok:       { color: '#2E8B62', bg: 'rgba(46,139,98,0.10)' },
  degraded: { color: '#C0504D', bg: 'rgba(192,80,77,0.10)' },
  unknown:  { color: '#B7791F', bg: 'rgba(183,121,31,0.10)' },
}

function Pill({ state, label }: { state: string; label: string }) {
  const tone = TONE[state] ?? TONE.unknown
  return (
    <span style={{
      padding: '2px 9px', borderRadius: 999, fontSize: 11, fontWeight: 600,
      color: tone.color, background: tone.bg, whiteSpace: 'nowrap',
    }}>{label}</span>
  )
}

function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '-'
  if (seconds < 90) return `${Math.round(seconds)} s`
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`
  return `${(seconds / 3600).toFixed(1)} h`
}

export default function InstallationStatus() {
  const { lang } = useLanguage()
  const ui = SERVICE_CONFIG[lang].ui
  const t = SERVICE_CONFIG[lang].ops
  const [snap, setSnap] = useState<OpsSnapshot | null>(null)
  const [failed, setFailed] = useState(false)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setSnap(await getOpsSnapshot())
      setFailed(false)
    } catch {
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const stateLabel = (state: string) =>
    state === 'ok' ? ui.stateReady : state === 'degraded' ? ui.stateDegraded : ui.stateNotConfigured

  return (
    <Card padding="16px 20px">
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>{t.title}</div>
        {snap && <Pill state={snap.overall} label={t.overall[snap.overall]} />}
        <div style={{ marginLeft: 'auto' }}>
          <Button variant="secondary" onClick={load} disabled={loading}>{t.refresh}</Button>
        </div>
      </div>

      {loading && !snap && <div style={{ padding: 16, textAlign: 'center' }}><Spinner /></div>}
      {failed && <div role="status" style={{ fontSize: 12, color: 'var(--dim)', marginTop: 8 }}>{t.loadError}</div>}

      {snap && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 18px' }}>
            {snap.checks.map(c => (
              <div key={c.key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                <span style={{ color: 'var(--muted)' }}>{t.checks[c.key] ?? c.key}</span>
                <Pill state={c.state} label={stateLabel(c.state)} />
              </div>
            ))}
          </div>

          <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.7 }}>
            <div>
              {t.queueLine}: <b>{snap.queue.queued}</b>
              {snap.queue.oldest_queued_age_seconds != null &&
                <> - {t.oldestQueued} {fmtDuration(snap.queue.oldest_queued_age_seconds)}</>}
              {' · '}{t.running}: <b>{snap.running_jobs.length}</b>
              {' · '}{t.heartbeatLine}: {snap.worker.last_heartbeat
                ? new Date(snap.worker.last_heartbeat).toLocaleString() : t.never}
            </div>
            <div>
              {t.failed24h}: {snap.failed_jobs_24h.by_error_class.length === 0
                ? t.noFailures
                : snap.failed_jobs_24h.by_error_class.map(f => `${f.error_class} (${f.count})`).join(', ')}
            </div>
            <div>{t.slowQueries}: <b>{snap.slow_queries.count}</b>
              {snap.slow_queries.worst_ms != null && <> - max {Math.round(snap.slow_queries.worst_ms)} ms</>}
            </div>
          </div>

          <div>
            <div style={{ fontSize: 12, fontWeight: 600 }}>{t.latencyTitle}</div>
            <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 4 }}>{t.latencyScope}</div>
            {snap.latency.families.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--dim)' }}>{t.noTraffic}</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 12 }}>
                {snap.latency.families.slice(0, 6).map(f => (
                  <div key={f.family} style={{ display: 'flex', gap: 12, color: 'var(--muted)' }}>
                    <span style={{ minWidth: 120 }}>{f.family}</span>
                    <span>n={f.count}</span>
                    <span>p50 {Math.round(f.p50_ms)} ms</span>
                    <span>p95 {Math.round(f.p95_ms)} ms</span>
                    {f.errors_5xx > 0 && <span style={{ color: '#C0504D' }}>5xx {f.errors_5xx}</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  )
}
