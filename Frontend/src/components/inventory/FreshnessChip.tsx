'use client'
/**
 * "Data as of <time>" + "Refresh now", on the Inventario screen.
 *
 * Shown only when the account has an inventory sync. The colour says how
 * current the stock is (fresh / stale / blocked); opening it says, per sync,
 * what is wrong and what to do. Refresh never blanks the table: while a run
 * is going the chip says "still updating, data as of HH:MM" and the table
 * keeps the last good data until the run finishes, then reloads.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { RefreshCw } from 'lucide-react'
import { getSyncFreshness, refreshInventorySync, type SyncFreshness, type SyncRefreshResult } from '@/lib/api'
import { useLanguage } from '@/contexts/LanguageContext'
import { useErrorDetail } from '@/components/ui/States'

const COLOR = { fresh: '#2F855A', stale: '#B7791F', blocked: '#C0504D', none: 'var(--muted)' } as const

export default function FreshnessChip({ canRefresh, visibleSkus, onRefreshed }: {
  canRefresh: boolean
  visibleSkus?: string[]
  onRefreshed: () => void
}) {
  const { t, lang } = useLanguage()
  const errorDetailRaw = useErrorDetail()
  const errorRef = useRef(errorDetailRaw)
  errorRef.current = errorDetailRaw
  const [fresh, setFresh] = useState<SyncFreshness | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const refreshedRef = useRef(onRefreshed)
  refreshedRef.current = onRefreshed

  const load = useCallback(async () => {
    try { setFresh(await getSyncFreshness()) } catch { /* the chip is optional; the table is not */ }
  }, [])

  useEffect(() => {
    void load()
    const id = setInterval(load, 60_000)
    return () => { clearInterval(id); if (pollRef.current) clearInterval(pollRef.current) }
  }, [load])

  const when = (iso: string | null | undefined) => iso
    ? new Date(iso).toLocaleString(lang === 'en' ? 'en-US' : 'es-CR', { dateStyle: 'short', timeStyle: 'short' })
    : t('isync.never')

  const follow = useCallback(() => {
    // Poll until no sync is running any more, then reload the table once.
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const f = await getSyncFreshness()
        setFresh(f)
        if (!f.profiles.some(p => p.running)) {
          if (pollRef.current) clearInterval(pollRef.current)
          pollRef.current = null
          setMessage(t('isync.refresh_done'))
          refreshedRef.current()
        }
      } catch { /* keep trying on the next tick */ }
    }, 3000)
  }, [t])

  async function refresh(scoped: boolean) {
    setBusy(true); setMessage(null)
    try {
      const r: SyncRefreshResult = await refreshInventorySync(
        scoped && visibleSkus?.length ? { skus: visibleSkus.slice(0, 1000) } : {})
      setFresh(r.freshness)
      setMessage(summarize(r))
      if (r.profiles.some(p => p.status === 'running')) follow()
      else if (r.profiles.some(p => p.status === 'done')) refreshedRef.current()
    } catch (e) {
      setMessage(errorRef.current(e))
    } finally { setBusy(false) }
  }

  function summarize(r: SyncRefreshResult): string {
    const parts = r.profiles.map(p => {
      if (p.status === 'done') return t('isync.refresh_status.done', { name: p.profile_name })
      if (p.status === 'running') return t('isync.refresh_status.running', { name: p.profile_name, when: when(p.data_as_of) })
      if (p.status === 'cooldown') return t('isync.refresh_status.cooldown', { name: p.profile_name, n: p.retry_after_s ?? 0 })
      if (p.status === 'paused' || p.status === 'blocked')
        return t('isync.refresh_status.blocked', { name: p.profile_name, reason: reasonText(p.reason) })
      return t('isync.refresh_status.failed', { name: p.profile_name, reason: reasonText(p.error_code) })
    })
    return parts.join(' · ') || t('isync.refresh_nothing')
  }

  function reasonText(code?: string | null): string {
    if (!code) return ''
    const own = t(`isync.code.${code}`)
    if (own !== `isync.code.${code}`) return own
    const err = t(`errors.${code}`)
    return err !== `errors.${code}` ? err : code
  }

  if (!fresh || fresh.state === 'none') return null
  const color = COLOR[fresh.state]
  const running = fresh.profiles.some(p => p.running)
  // A sync writes every warehouse: a user limited to some is not offered it.
  const mayRefresh = canRefresh && fresh.can_refresh !== false

  return (
    <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <button type="button" data-tour="inv.freshness" onClick={() => setOpen(o => !o)} aria-expanded={open}
              title={t(`isync.fresh_state.${fresh.state}`)}
              style={{ all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6,
                       padding: '5px 10px', borderRadius: 999, fontSize: 12, border: `1px solid ${color}`, color }}>
        <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 999, background: color }} />
        {running ? t('isync.chip_updating', { when: when(fresh.data_as_of) })
                 : t('isync.chip_as_of', { when: when(fresh.data_as_of) })}
      </button>
      {mayRefresh && (
        <button type="button" data-tour="inv.refresh_now" disabled={busy} onClick={() => void refresh(false)}
                style={{ all: 'unset', cursor: busy ? 'default' : 'pointer', display: 'inline-flex', alignItems: 'center',
                         gap: 4, padding: '5px 10px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                         border: '1px solid var(--border)', opacity: busy ? 0.6 : 1 }}>
          <RefreshCw size={12} aria-hidden="true" /> {busy ? t('isync.refreshing') : t('isync.refresh_now')}
        </button>
      )}
      {message && <span role="status" style={{ fontSize: 12, color: 'var(--muted)', maxWidth: 360 }}>{message}</span>}
      {open && (
        <div role="dialog" aria-label={t('isync.fresh_title')}
             style={{ position: 'absolute', top: '110%', right: 0, zIndex: 30, minWidth: 300, maxWidth: 420,
                      background: 'var(--surface, #fff)', border: '1px solid var(--border)', borderRadius: 10,
                      padding: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.12)' }}>
          <p style={{ margin: '0 0 8px', fontSize: 13, fontWeight: 700 }}>{t(`isync.fresh_state.${fresh.state}`)}</p>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {fresh.profiles.map(p => (
              <li key={p.profile_id} style={{ fontSize: 12.5 }}>
                <span style={{ color: COLOR[p.state], fontWeight: 700 }}>●</span> <strong>{p.name}</strong>
                {' · '}{t('isync.chip_as_of', { when: when(p.data_as_of) })}
                {p.state !== 'fresh' && (
                  <div style={{ color: 'var(--muted)', marginTop: 2 }}>
                    {p.reason ? reasonText(p.reason) : t(`isync.fresh_state.${p.state}`)}
                    {p.reason && t(`isync.remedy.${p.reason}`) !== `isync.remedy.${p.reason}` && (
                      <div>{t(`isync.remedy.${p.reason}`)}</div>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
          {mayRefresh && visibleSkus && visibleSkus.length > 0 && (
            <button type="button" disabled={busy} onClick={() => void refresh(true)}
                    style={{ all: 'unset', cursor: 'pointer', marginTop: 10, fontSize: 12, fontWeight: 600,
                             textDecoration: 'underline' }}>
              {t('isync.refresh_page', { n: Math.min(visibleSkus.length, 1000) })}
            </button>
          )}
          <p style={{ margin: '10px 0 0', fontSize: 11.5, color: 'var(--muted)' }}>{t('isync.refresh_never_blank')}</p>
          <Link href="/sincronizacion" style={{ display: 'inline-block', marginTop: 6, fontSize: 12, fontWeight: 600 }}>
            {t('isync.open_sync')}
          </Link>
        </div>
      )}
    </div>
  )
}
