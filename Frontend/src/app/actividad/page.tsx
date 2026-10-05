'use client'
/**
 * /actividad — everything StockAI did for this account, and why.
 *
 * The bell is deliberately a SUBSET: only what needs a decision reaches it,
 * because a bell that rings for every successful import is a bell people stop
 * reading. This is the other half of the same promise — the full history,
 * `info` rows included, so "what happened while I was not looking" always has
 * an answer that is not a log file the tenant cannot open.
 *
 * Every role can read it. The point of the screen is that nobody has to ask.
 *
 * Rows are rendered by `AlertRow`, the same component the bell uses, so a
 * change to how an event reads happens once. The two filters come from
 * `GET /alerts/kinds` rather than a list written here, so the screen cannot
 * offer a topic nothing can ever be recorded under.
 */
import { useCallback, useEffect, useState } from 'react'
import { ScrollText } from 'lucide-react'

import { getActivity, getActivityKinds } from '@/lib/api'
import type { AlertEntry, AlertSeverity } from '@/components/alerts/types'
import { AlertRow } from '@/components/alerts/AlertBell'
import AuditTrail from '@/components/alerts/AuditTrail'
import { getUser } from '@/lib/auth'
import Card from '@/components/ui/Card'
import { EmptyState, ErrorState, LoadingState, SkeletonTable } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'

const PAGE = 50

const SEVERITIES: AlertSeverity[] = ['critical', 'warning', 'info']

const C = {
  text: 'var(--text)', dim: 'var(--dim)', border: 'var(--border)',
}

const selectStyle: React.CSSProperties = {
  fontSize: 12, padding: '6px 10px', borderRadius: 8,
  border: `1px solid ${C.border}`, background: 'var(--surface)',
  color: C.text, cursor: 'pointer',
}

export default function ActivityPage() {
  const { t } = useLanguage()
  // Phones: the two filters share the full width at a thumb's height, and
  // "show more" is a full-width button instead of a 12px link.
  const narrow = useIsNarrow()
  const pickerStyle: React.CSSProperties = narrow
    ? { ...selectStyle, fontSize: 16, minHeight: 44, flex: 1, minWidth: 0, width: '100%' }
    : selectStyle

  const [items,    setItems]    = useState<AlertEntry[]>([])
  const [total,    setTotal]    = useState(0)
  const [kinds,    setKinds]    = useState<string[]>([])
  const [kind,     setKind]     = useState('')
  const [severity, setSeverity] = useState('')
  const [loading,  setLoading]  = useState(true)
  const [more,     setMore]     = useState(false)
  const [error,    setError]    = useState<unknown>(null)
  // The audit trail names people, so only an administrator gets its tab.
  const isAdmin = getUser()?.role === 'admin'
  const [tab, setTab] = useState<'feed' | 'audit'>('feed')

  // The filter vocabulary is served, not hardcoded. A failure here is not
  // worth a red screen: the feed is still readable with the filters missing.
  useEffect(() => {
    getActivityKinds({ silent: true })
      .then(r => setKinds(r.kinds))
      .catch(() => { /* filters degrade to "all"; the list still loads */ })
  }, [])

  const load = useCallback(async (offset: number) => {
    if (offset === 0) setLoading(true); else setMore(true)
    setError(null)
    try {
      const r = await getActivity({
        limit: PAGE, offset,
        kind: kind || undefined,
        severity: severity || undefined,
      })
      setTotal(r.total)
      setItems(prev => offset === 0 ? r.items : [...prev, ...r.items])
    } catch (e: unknown) {
      // Only a first page failing is a dead screen, and only that replaces the
      // list with an error and a retry. A failed "show more" keeps every row
      // already on screen — `request` has raised its own toast by then, so the
      // user is told without losing what they were reading.
      if (offset === 0) setError(e)
    } finally {
      setLoading(false)
      setMore(false)
    }
  }, [kind, severity])

  // Changing a filter restarts the paging, or "show more" would append page 2
  // of the previous query underneath page 1 of this one.
  useEffect(() => { load(0) }, [load])

  const filtered = Boolean(kind || severity)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        flexWrap: 'wrap', justifyContent: 'space-between',
      }}>
        {/* The top bar already names the screen: only the one-line purpose here. */}
        <p style={{ margin: 0, fontSize: 12, color: C.dim, flex: '1 1 240px', minWidth: 0 }}>
          {t(tab === 'audit' ? 'audit.subtitle' : 'activity.subtitle')}
        </p>

        {tab === 'feed' && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', ...(narrow ? { width: '100%' } : {}) }}>
          <select
            value={kind}
            onChange={e => setKind(e.target.value)}
            aria-label={t('activity.filter_kind')}
            style={pickerStyle}
          >
            <option value="">{t('activity.all_kinds')}</option>
            {kinds.map(k => (
              <option key={k} value={k}>{t(`events.kind.${k}`)}</option>
            ))}
          </select>
          <select
            value={severity}
            onChange={e => setSeverity(e.target.value)}
            aria-label={t('activity.filter_severity')}
            style={pickerStyle}
          >
            <option value="">{t('activity.all_severities')}</option>
            {SEVERITIES.map(s => (
              <option key={s} value={s}>{t(`events.severity.${s}`)}</option>
            ))}
          </select>
        </div>
        )}
      </div>

      {isAdmin && (
        <div role="tablist" style={{ display: 'flex', gap: 4 }}>
          {(['feed', 'audit'] as const).map(id => (
            <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
                    style={{
                      all: 'unset', cursor: 'pointer', padding: '5px 12px', borderRadius: 7,
                      fontSize: 11.5, fontWeight: 600,
                      background: tab === id ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : 'transparent',
                      color: tab === id ? 'var(--accent)' : C.dim,
                    }}>
              {t(id === 'feed' ? 'audit.tab_activity' : 'audit.tab_audit')}
            </button>
          ))}
        </div>
      )}

      {tab === 'audit' && isAdmin ? <AuditTrail /> : loading ? (
        <Card padding={8}>
          <LoadingState label={t('common.loading')}>
            <SkeletonTable rows={6} columns={3} />
          </LoadingState>
        </Card>
      ) : error ? (
        <ErrorState error={error} onRetry={() => load(0)} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<ScrollText size={22} />}
          title={filtered ? t('activity.empty_filtered') : t('activity.empty')}
          body={filtered ? '' : t('activity.empty_hint')}
        />
      ) : (
        <Card padding={0} overflow="hidden">
          {items.map(a => <AlertRow key={a.id} alert={a} dense />)}

          <div style={{
            padding: '10px 16px', display: 'flex', alignItems: 'center',
            justifyContent: 'space-between', gap: 12,
            ...(narrow ? { flexDirection: 'column' as const, alignItems: 'stretch', padding: 12 } : {}),
          }}>
            <span style={{ fontSize: narrow ? 13 : 11, color: C.dim, textAlign: narrow ? 'center' : undefined }}>
              {t('activity.showing', { shown: items.length, total })}
            </span>
            {items.length < total && narrow && (
              <button
                onClick={() => load(items.length)}
                disabled={more}
                className="mobile-btn mobile-btn-secondary"
                style={{ flex: 'none', width: '100%' }}
              >
                {more ? t('common.loading') : t('activity.load_more')}
              </button>
            )}
            {items.length < total && !narrow && (
              <button
                onClick={() => load(items.length)}
                disabled={more}
                style={{
                  all: 'unset', cursor: more ? 'default' : 'pointer', fontSize: 12,
                  color: 'var(--accent)', opacity: more ? 0.5 : 1,
                }}
              >
                {more ? t('common.loading') : t('activity.load_more')}
              </button>
            )}
          </div>
        </Card>
      )}
    </div>
  )
}
