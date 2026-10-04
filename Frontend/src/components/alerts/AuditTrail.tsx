'use client'
/**
 * The audit trail — who did what to which object, when, and what changed.
 *
 * The second tab of /actividad, for administrators. The feed on the first tab
 * answers "what did StockAI do for me"; this answers "what did people and
 * integrations do to my account", with the previous and the new value where
 * the change has one. Filtering, paging and the CSV export all happen on the
 * server (`GET /audit`), so it stays usable with a very long history.
 */
import { Fragment, useCallback, useEffect, useState } from 'react'
import { Download } from 'lucide-react'

import {
  downloadAuditCsv, getAuditFilters, getAuditTrail,
  type AuditEntry, type AuditFilters, type AuditQuery,
} from '@/lib/api'
import Card from '@/components/ui/Card'
import { EmptyState, ErrorState, LoadingState, SkeletonTable } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { localeFor } from '@/lib/numberLocale'

const PAGE = 50

const field: React.CSSProperties = {
  fontSize: 12, padding: '6px 10px', borderRadius: 8, minWidth: 0,
  border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)',
}

/** `{a: 1, b: 'x'}` as `a: 1 · b: x`. Values are machine values (a role, a cron
 *  expression), never prose, so they are shown as they are. */
function summary(v: Record<string, unknown> | null): string {
  if (!v) return ''
  return Object.entries(v)
    .filter(([, x]) => x !== null && x !== undefined && x !== '')
    .map(([k, x]) => `${k}: ${Array.isArray(x) ? (x.length ? x.join(', ') : '—') : typeof x === 'object' ? JSON.stringify(x) : String(x)}`)
    .join(' · ')
}

export default function AuditTrail() {
  const { t, lang } = useLanguage()
  const narrow = useIsNarrow()

  const [filters, setFilters] = useState<AuditFilters | null>(null)
  const [q, setQ] = useState<AuditQuery>({})
  const [items, setItems] = useState<AuditEntry[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [more, setMore] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    getAuditFilters().then(setFilters).catch(() => { /* the list works without the dropdowns */ })
  }, [])

  const load = useCallback(async (offset: number) => {
    if (offset === 0) setLoading(true); else setMore(true)
    setError(null)
    try {
      const r = await getAuditTrail({ ...q, limit: PAGE, offset })
      setTotal(r.total)
      setItems(prev => (offset === 0 ? r.items : [...prev, ...r.items]))
    } catch (e: unknown) {
      if (offset === 0) setError(e)
    } finally {
      setLoading(false)
      setMore(false)
    }
  }, [q])

  useEffect(() => { load(0) }, [load])

  const set = (patch: AuditQuery) => setQ(prev => ({ ...prev, ...patch }))
  const filtered = Object.values(q).some(Boolean)

  const when = (iso: string) => new Date(iso).toLocaleString(localeFor(lang), {
    dateStyle: 'short', timeStyle: 'medium',
  })
  const actorLabel = (e: AuditEntry) =>
    e.actor.label ?? (e.actor.kind === 'user' ? e.actor.id : t(`audit.actor_${e.actor.kind}`))
  const targetLabel = (e: AuditEntry) => e.target.label ?? e.target.id ?? ''
  const actionLabel = (e: AuditEntry) => {
    const key = `audit.action.${e.action}`
    const text = t(key)
    return text === key ? e.action : text
  }
  const targetType = (type: string | null) => {
    if (!type) return ''
    const key = `audit.target.${type}`
    const text = t(key)
    return text === key ? type : text
  }

  const doExport = async () => {
    setExporting(true)
    try { await downloadAuditCsv(q) } finally { setExporting(false) }
  }

  const pick: React.CSSProperties = narrow ? { ...field, fontSize: 16, minHeight: 44, width: '100%' } : field

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }} data-testid="audit-trail">
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <select aria-label={t('audit.filter_target')} style={pick} value={q.target_type ?? ''}
                onChange={e => set({ target_type: e.target.value || undefined, action: undefined })}>
          <option value="">{t('audit.all_targets')}</option>
          {(filters?.target_types ?? []).map(x => (
            <option key={x} value={x}>{targetType(x)}</option>
          ))}
        </select>
        <select aria-label={t('audit.filter_action')} style={pick} value={q.action ?? ''}
                onChange={e => set({ action: e.target.value || undefined })}>
          <option value="">{t('audit.all_actions')}</option>
          {(filters?.actions ?? []).map(x => {
            const key = `audit.action.${x}`
            const text = t(key)
            return <option key={x} value={x}>{text === key ? x : text}</option>
          })}
        </select>
        <select aria-label={t('audit.filter_actor')} style={pick} value={q.actor ?? ''}
                onChange={e => set({ actor: e.target.value || undefined })}>
          <option value="">{t('audit.all_actors')}</option>
          {(filters?.actors ?? []).map(a => (
            <option key={a.id} value={a.id}>{a.label ?? t(`audit.actor_${a.kind}`)}</option>
          ))}
        </select>
        <select aria-label={t('audit.filter_status')} style={pick} value={q.status ?? ''}
                onChange={e => set({ status: e.target.value || undefined })}>
          <option value="">{t('audit.all_statuses')}</option>
          <option value="success">{t('audit.status_success')}</option>
          <option value="error">{t('audit.status_error')}</option>
        </select>
        <label style={{ fontSize: 12, color: 'var(--dim)', display: 'flex', alignItems: 'center', gap: 6 }}>
          {t('audit.date_from')}
          <input type="date" style={field} value={q.date_from ?? ''}
                 onChange={e => set({ date_from: e.target.value || undefined })} />
        </label>
        <label style={{ fontSize: 12, color: 'var(--dim)', display: 'flex', alignItems: 'center', gap: 6 }}>
          {t('audit.date_to')}
          <input type="date" style={field} value={q.date_to ?? ''}
                 onChange={e => set({ date_to: e.target.value || undefined })} />
        </label>
        <button type="button" onClick={doExport} disabled={exporting}
                style={{ ...field, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6,
                         marginLeft: narrow ? 0 : 'auto', fontWeight: 600 }}>
          <Download size={13} aria-hidden="true" />{t('audit.export_csv')}
        </button>
      </div>

      {loading ? (
        <Card padding={8}>
          <LoadingState label={t('common.loading')}><SkeletonTable rows={6} columns={4} /></LoadingState>
        </Card>
      ) : error ? (
        <ErrorState error={error} onRetry={() => load(0)} />
      ) : items.length === 0 ? (
        <EmptyState title={filtered ? t('audit.empty_filtered') : t('audit.empty')} body="" />
      ) : (
        <Card padding={0} overflow="hidden">
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--dim)', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                  {['when', 'actor', 'action', 'target', 'change'].map(c => (
                    <th key={c} style={{ padding: '8px 12px', fontWeight: 700 }}>{t(`audit.col_${c}`)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map(e => (
                  <Fragment key={e.id}>
                    <tr style={{ borderTop: '1px solid var(--border)', verticalAlign: 'top' }}>
                      <td style={{ padding: '8px 12px', whiteSpace: 'nowrap', color: 'var(--dim)' }}>{when(e.at)}</td>
                      <td style={{ padding: '8px 12px', overflowWrap: 'break-word', minWidth: 210 }}>
                        <div style={{ color: 'var(--text)', fontWeight: 600 }}>{actorLabel(e)}</div>
                        <div style={{ color: 'var(--dim)', fontSize: 11 }}>{t(`audit.actor_${e.actor.kind}`)}</div>
                      </td>
                      <td style={{ padding: '8px 12px', color: e.status === 'error' ? '#B94A4A' : 'var(--text)' }}>
                        {actionLabel(e)}
                      </td>
                      <td style={{ padding: '8px 12px', overflowWrap: 'anywhere' }}>
                        <div style={{ color: 'var(--text)' }}>{targetLabel(e)}</div>
                        <div style={{ color: 'var(--dim)', fontSize: 11 }}>{targetType(e.target.type)}</div>
                      </td>
                      <td style={{ padding: '8px 12px', overflowWrap: 'anywhere', minWidth: 180 }}>
                        {e.before && (
                          <div><span style={{ color: 'var(--dim)' }}>{t('audit.before')}: </span>{summary(e.before)}</div>
                        )}
                        {e.after && (
                          <div><span style={{ color: 'var(--dim)' }}>{t('audit.after')}: </span>{summary(e.after)}</div>
                        )}
                      </td>
                    </tr>
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: '10px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        gap: 12, borderTop: '1px solid var(--border)', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 11, color: 'var(--dim)' }}>
              {t('audit.showing', { shown: items.length, total })}
            </span>
            {items.length < total && (
              <button type="button" onClick={() => load(items.length)} disabled={more}
                      style={{ all: 'unset', cursor: more ? 'default' : 'pointer', fontSize: 12,
                               color: 'var(--accent)', opacity: more ? 0.5 : 1 }}>
                {more ? t('common.loading') : t('audit.load_more')}
              </button>
            )}
          </div>
        </Card>
      )}
    </div>
  )
}
