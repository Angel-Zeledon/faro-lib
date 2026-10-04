'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Check, History, Pencil, Trash2, X } from 'lucide-react'
import { deleteSession, getSessionSummaries, patchSession } from '@/lib/api'
import type { SessionStatus, SessionSummary } from '@/lib/types'
import { EmptyState, ErrorState, LoadingState, SkeletonTable } from '@/components/ui/States'
import Card from '@/components/ui/Card'
import Table, { Th, Td } from '@/components/ui/Table'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'
import { localeFor } from '@/lib/numberLocale'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import {
  BottomSheet, MobileList, MobileCard, type StatusTone,
} from '@/components/mobile'

/** Run status on the phone cards — the same reading as the desktop badge. */
const STATUS_TONE: Record<string, StatusTone> = {
  COMPLETED: 'success', FAILED: 'danger', CANCELLED: 'danger', RUNNING: 'warning', QUEUED: 'warning',
}

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', muted: 'var(--muted)',
  accent: 'var(--accent)',
}

const STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  COMPLETED: { bg: 'rgba(16,185,129,0.12)', fg: '#2E8B62' },
  FAILED:    { bg: 'rgba(192,80,77,0.12)',  fg: '#C0504D' },
  CANCELLED: { bg: 'rgba(192,80,77,0.08)',  fg: '#D07878' },
  RUNNING:   { bg: 'rgba(183,121,31,0.12)', fg: '#B7791F' },
  QUEUED:    { bg: 'rgba(183,121,31,0.08)', fg: '#B7791F' },
}
const DEFAULT_STATUS_COLOR = { bg: 'rgba(148,163,184,0.12)', fg: 'var(--dim)' }

function StatusBadge({ status }: { status: SessionStatus }) {
  const { t } = useLanguage()
  const c = STATUS_COLORS[status] ?? DEFAULT_STATUS_COLOR
  return (
    <span style={{
      fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 20,
      background: c.bg, color: c.fg, whiteSpace: 'nowrap',
    }}>
      {t(`sessions.status_${status}`)}
    </span>
  )
}

const PAGE = 50
const STATUS_OPTIONS = ['COMPLETED', 'FAILED', 'RUNNING', 'QUEUED', 'CANCELLED'] as const
const SORT_OPTIONS = ['created_desc', 'created_asc', 'name_asc', 'name_desc', 'status'] as const

const ctlStyle: React.CSSProperties = {
  fontSize: 12, padding: '6px 10px', borderRadius: 8, minWidth: 0,
  border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)',
}

const iconBtnStyle: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', padding: 5, borderRadius: 6,
  color: 'var(--dim)', display: 'inline-flex', alignItems: 'center',
}

export default function SessionsHistoryPage() {
  const { t, lang } = useLanguage()
  const router   = useRouter()
  const confirm  = useConfirm()
  const user     = getUser()
  const canEdit  = user?.role === 'admin' || user?.role === 'analyst'

  const [items,     setItems]     = useState<SessionSummary[]>([])
  // The history is filtered, sorted and paged by the server: a tenant with
  // thousands of runs used to get the newest 200 and no way to reach the rest.
  const [total,     setTotal]     = useState(0)
  const [search,    setSearch]    = useState('')
  const [query,     setQuery]     = useState('')        // `search`, after the typing pause
  const [statusF,   setStatusF]   = useState('')
  const [sort,      setSort]      = useState('created_desc')
  const [loadingMore, setLoadingMore] = useState(false)
  const loadedRef = useRef(0)
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState<unknown>(null)
  // Inline rename state — one row at a time.
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName,  setEditName]  = useState('')
  const [saving,    setSaving]    = useState(false)
  // Phones: the run whose detail sheet is open. Kept as an id so a rename
  // shows up in the open sheet without re-opening it.
  const narrow = useIsNarrow()
  const [detailId,  setDetailId]  = useState<string | null>(null)

  useEffect(() => {
    const id = setTimeout(() => setQuery(search.trim()), 300)
    return () => clearTimeout(id)
  }, [search])

  const load = useCallback(async (initial = false, append = false) => {
    if (initial) setLoading(true)
    if (append) setLoadingMore(true)
    setError(null)
    try {
      const r = await getSessionSummaries(append ? loadedRef.current : 0, PAGE, {
        q: query || undefined, status: statusF || undefined, sort,
      })
      setTotal(r.total)
      setItems(prev => {
        const next = append ? [...prev, ...r.items] : r.items
        loadedRef.current = next.length
        return next
      })
    } catch (e: unknown) {
      // A failed "show more" keeps the rows already on screen.
      if (!append) setError(e)
    } finally {
      if (initial) setLoading(false)
      setLoadingMore(false)
    }
  }, [query, statusF, sort])

  useEffect(() => { load(true) }, [load])

  const startRename = (s: SessionSummary) => {
    setEditingId(s.session_id)
    setEditName(s.name)
  }

  const cancelRename = () => { setEditingId(null); setEditName('') }

  const saveRename = async () => {
    if (!editingId || saving) return
    const name = editName.trim()
    const current = items.find(i => i.session_id === editingId)
    if (!name || !current || name === current.name) { cancelRename(); return }
    setSaving(true)
    try {
      // The API interceptor already toasts failures (403 for viewers, etc.).
      await patchSession(editingId, { name })
      setItems(prev => prev.map(i => (i.session_id === editingId ? { ...i, name } : i)))
      cancelRename()
    } catch {
      // Keep the editor open so the user can retry or cancel.
    } finally {
      setSaving(false)
    }
  }

  const removeSession = async (s: SessionSummary) => {
    const okToDelete = await confirm({
      title: t('sessions.delete_title'),
      message: t('sessions.delete_msg', { name: s.name }),
      confirmLabel: t('common.delete'),
      danger: true,
    })
    if (!okToDelete) return
    await deleteSession(s.session_id)
    setDetailId(null)
    load()
  }

  const openResults = (s: SessionSummary) => {
    if (s.status !== 'COMPLETED') return
    router.push(`/pronosticos?session=${encodeURIComponent(s.session_id)}`)
  }

  const fmtDate = (iso: string) => {
    const d = new Date(iso)
    return isNaN(d.getTime()) ? '—' : d.toLocaleDateString(localeFor(lang), {
      year: 'numeric', month: 'short', day: 'numeric',
    })
  }

  const granularityLabel = (g: string | null) => {
    if (!g) return '—'
    const key = `sessions.granularity_${g.toLowerCase()}`
    const label = t(key)
    return label === key ? g : label
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 9, background: 'var(--accent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <History size={17} color="#fff" strokeWidth={2.5} />
        </div>
        <div>
          <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>
            {t('sessions.page_title')}
          </h1>
          <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('sessions.page_subtitle')}</p>
        </div>
      </div>

      {(total > 0 || query || statusF) && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input
            type="search" value={search} onChange={e => setSearch(e.target.value)}
            placeholder={t('list.search_placeholder')} aria-label={t('list.search_placeholder')}
            style={{ ...ctlStyle, flex: narrow ? '1 1 100%' : '0 1 240px' }}
          />
          <select value={statusF} onChange={e => setStatusF(e.target.value)}
                  aria-label={t('sessions.col_status')} style={ctlStyle}>
            <option value="">{t('list.all_statuses')}</option>
            {STATUS_OPTIONS.map(x => <option key={x} value={x}>{t(`sessions.status_${x}`)}</option>)}
          </select>
          <select value={sort} onChange={e => setSort(e.target.value)}
                  aria-label={t('list.sort_label')} style={ctlStyle}>
            {SORT_OPTIONS.map(x => <option key={x} value={x}>{t(`sessions.sort_${x}`)}</option>)}
          </select>
        </div>
      )}

      {loading ? (
        <Card padding={8}>
          <LoadingState label={t('common.loading')}>
            <SkeletonTable rows={6} columns={narrow ? 1 : 7} />
          </LoadingState>
        </Card>
      ) : error ? (
        <ErrorState error={error} onRetry={() => load(true)} />
      ) : items.length === 0 ? (
        query || statusF ? (
          <EmptyState icon={<History size={22} />} title={t('list.no_results')} body="" />
        ) : (
          <EmptyState
            icon={<History size={22} />}
            title={t('sessions.empty_title')}
            body={t('sessions.empty_hint')}
          />
        )
      ) : narrow ? (
        <HistoryCards
          items={items}
          detailId={detailId}
          onOpen={id => { cancelRename(); setDetailId(id) }}
          onClose={() => { cancelRename(); setDetailId(null) }}
          canEdit={canEdit}
          fmtDate={fmtDate}
          granularityLabel={granularityLabel}
          onOpenResults={openResults}
          editingId={editingId}
          editName={editName}
          saving={saving}
          onStartRename={startRename}
          onEditName={setEditName}
          onSaveRename={saveRename}
          onCancelRename={cancelRename}
          onDelete={removeSession}
        />
      ) : (
        <Card padding={0} overflow="hidden">
          <Table>
            <thead>
              <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                {[
                  t('sessions.col_name'), t('sessions.col_status'), t('sessions.col_dataset'),
                  t('sessions.col_created'), t('sessions.col_horizon'),
                  t('sessions.col_granularity'), t('sessions.col_skus'), '',
                ].map((h, i) => <Th key={i}>{h}</Th>)}
              </tr>
            </thead>
            <tbody>
              {items.map((s, idx) => {
                const clickable = s.status === 'COMPLETED'
                const isEditing = editingId === s.session_id
                return (
                  <tr
                    key={s.session_id}
                    data-tour={idx === 0 ? 'ses.row' : undefined}
                    onClick={() => { if (!isEditing) openResults(s) }}
                    title={clickable ? t('sessions.view_results') : undefined}
                    style={{
                      borderBottom: `1px solid ${C.border}`,
                      cursor: clickable && !isEditing ? 'pointer' : 'default',
                    }}
                  >
                    {/* The divider is on the <tr>, so the cells do not draw their own. */}
                    <Td divider={false} style={{ fontWeight: 600, minWidth: 180 }}
                        data-tour={idx === 0 ? 'ses.name' : undefined}>
                      {isEditing ? (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                              onClick={e => e.stopPropagation()}>
                          <input
                            autoFocus
                            value={editName}
                            onChange={e => setEditName(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === 'Enter') saveRename()
                              if (e.key === 'Escape') cancelRename()
                            }}
                            aria-label={t('sessions.rename_action')}
                            disabled={saving}
                            style={{
                              background: 'transparent', border: `1px solid ${C.accent}`,
                              borderRadius: 6, padding: '4px 8px', fontSize: 13,
                              color: C.text, width: 200, outline: 'none',
                            }}
                          />
                          <button onClick={saveRename} disabled={saving}
                                  title={t('sessions.rename_save')} style={{ ...iconBtnStyle, color: '#2E8B62' }}>
                            <Check size={14} />
                          </button>
                          <button onClick={cancelRename} disabled={saving}
                                  title={t('sessions.rename_cancel')} style={iconBtnStyle}>
                            <X size={14} />
                          </button>
                        </span>
                      ) : s.name}
                    </Td>
                    <Td divider={false} data-tour={idx === 0 ? 'ses.status' : undefined}>
                      <StatusBadge status={s.status} />
                      {/* A bare "Fallida" left the buyer with nothing to act on:
                          the reason was in the job all along. */}
                      {s.status === 'FAILED' && s.failure_reason && (
                        // Labelled, because the engine's reason is free text in
                        // English: without a frame a Spanish-speaking buyer
                        // cannot tell whether they are reading an instruction
                        // for them or a detail for whoever they ask for help.
                        <div style={{ marginTop: 4, fontSize: 11, color: C.dim, maxWidth: 280,
                                      whiteSpace: 'normal', lineHeight: 1.35 }}
                             title={s.failure_reason}>
                          <span style={{ color: C.muted }}>
                            {t('sessions.failure_detail_label')}{' '}
                          </span>
                          {s.failure_reason.length > 220
                            ? `${s.failure_reason.slice(0, 220)}…`
                            : s.failure_reason}
                        </div>
                      )}
                    </Td>
                    <Td divider={false} nowrap style={{ color: C.muted }}
                        data-tour={idx === 0 ? 'ses.dataset' : undefined}>
                      {s.dataset_filename ?? s.dataset_name ?? '—'}
                    </Td>
                    <Td divider={false} nowrap style={{ color: C.muted }}
                        data-tour={idx === 0 ? 'ses.created' : undefined}>
                      {fmtDate(s.created_at)}
                    </Td>
                    <Td divider={false} style={{ color: C.muted }}
                        data-tour={idx === 0 ? 'ses.horizon' : undefined}>{s.horizon ?? '—'}</Td>
                    <Td divider={false} style={{ color: C.muted }}
                        data-tour={idx === 0 ? 'ses.granularity' : undefined}>{granularityLabel(s.granularity)}</Td>
                    <Td divider={false} style={{ color: C.muted }}
                        data-tour={idx === 0 ? 'ses.skus' : undefined}>{s.sku_count ?? '—'}</Td>
                    <Td divider={false} nowrap align="right"
                        data-tour={idx === 0 ? 'ses.actions' : undefined}
                        onClick={e => e.stopPropagation()}>
                      {canEdit && !isEditing && (
                        <>
                          <button onClick={() => startRename(s)}
                                  data-tour={idx === 0 ? 'ses.rename' : undefined}
                                  title={t('sessions.rename_action')} style={iconBtnStyle}>
                            <Pencil size={14} />
                          </button>
                          <button
                            onClick={() => removeSession(s)}
                            data-tour={idx === 0 ? 'ses.delete' : undefined}
                            disabled={s.status === 'RUNNING'}
                            title={s.status === 'RUNNING'
                              ? t('sessions.delete_running_hint')
                              : t('sessions.delete_action')}
                            style={{
                              ...iconBtnStyle,
                              color: s.status === 'RUNNING' ? 'var(--dim)' : '#C0504D',
                              opacity: s.status === 'RUNNING' ? 0.4 : 1,
                              cursor: s.status === 'RUNNING' ? 'not-allowed' : 'pointer',
                            }}
                          >
                            <Trash2 size={14} />
                          </button>
                        </>
                      )}
                    </Td>
                  </tr>
                )
              })}
            </tbody>
          </Table>
        </Card>
      )}

      {!loading && !error && items.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11, color: C.dim }}>
            {t('list.showing', { shown: items.length, total })}
          </span>
          {items.length < total && (
            <button type="button" disabled={loadingMore} onClick={() => load(false, true)}
                    className={narrow ? 'mobile-btn mobile-btn-secondary' : undefined}
                    style={narrow ? { flex: 'none', width: '100%' } : {
                      all: 'unset', cursor: loadingMore ? 'default' : 'pointer', fontSize: 12,
                      color: 'var(--accent)', opacity: loadingMore ? 0.5 : 1 }}>
              {loadingMore ? t('common.loading') : t('list.load_more')}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// ── Phone layout ─────────────────────────────────────────────────────────────
// Eight columns do not fit 360px, and the rename/delete icons were 24px
// targets at the far right of a sideways-scrolling row. On a phone each run is
// a card (name, dataset and date, status, SKU count); tapping it opens a sheet
// with every column, the failure reason, and the three things a run can be
// asked to do — open its results, rename it, delete it — as full-width buttons.
function HistoryCards({
  items, detailId, onOpen, onClose, canEdit, fmtDate, granularityLabel, onOpenResults,
  editingId, editName, saving, onStartRename, onEditName, onSaveRename, onCancelRename, onDelete,
}: {
  items: SessionSummary[]
  detailId: string | null
  onOpen: (id: string) => void
  onClose: () => void
  canEdit: boolean
  fmtDate: (iso: string) => string
  granularityLabel: (g: string | null) => string
  onOpenResults: (s: SessionSummary) => void
  editingId: string | null
  editName: string
  saving: boolean
  onStartRename: (s: SessionSummary) => void
  onEditName: (v: string) => void
  onSaveRename: () => void
  onCancelRename: () => void
  onDelete: (s: SessionSummary) => void
}) {
  const { t } = useLanguage()
  const detail = items.find(i => i.session_id === detailId) ?? null
  const renaming = !!detail && editingId === detail.session_id

  const row = (label: string, value: React.ReactNode) => (
    <div style={{
      display: 'flex', justifyContent: 'space-between', gap: 12, padding: '10px 0',
      borderBottom: `1px solid ${C.border}`, fontSize: 14,
    }}>
      <span style={{ color: C.dim, flexShrink: 0 }}>{label}</span>
      <span style={{ color: C.text, textAlign: 'right', minWidth: 0, overflowWrap: 'anywhere' }}>{value}</span>
    </div>
  )

  return (
    <>
      <MobileList ariaLabel={t('sessions.page_title')}>
        {items.map(s => (
          <MobileCard
            key={s.session_id}
            title={s.name}
            subtitle={`${s.dataset_filename ?? s.dataset_name ?? '—'} · ${fmtDate(s.created_at)}`}
            status={{ label: t(`sessions.status_${s.status}`), tone: STATUS_TONE[s.status] ?? 'neutral' }}
            value={s.sku_count ?? '—'}
            valueCaption={t('sessions.col_skus')}
            onClick={() => onOpen(s.session_id)}
          />
        ))}
      </MobileList>

      <BottomSheet
        open={!!detail}
        onClose={onClose}
        title={detail?.name ?? ''}
        footer={detail && !renaming ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: '100%' }}>
            {detail.status === 'COMPLETED' && (
              <button className="mobile-btn mobile-btn-primary" style={{ flex: 'none', width: '100%' }}
                      onClick={() => onOpenResults(detail)}>
                {t('sessions.view_results')}
              </button>
            )}
            {canEdit && (
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="mobile-btn mobile-btn-secondary" onClick={() => onStartRename(detail)}>
                  <Pencil size={16} aria-hidden="true" /> {t('sessions.rename_action')}
                </button>
                <button
                  className="mobile-btn mobile-btn-secondary"
                  style={{ color: detail.status === 'RUNNING' ? undefined : 'var(--signal-order-now-fg)' }}
                  disabled={detail.status === 'RUNNING'}
                  onClick={() => onDelete(detail)}
                >
                  <Trash2 size={16} aria-hidden="true" /> {t('sessions.delete_action')}
                </button>
              </div>
            )}
            {canEdit && detail.status === 'RUNNING' && (
              <div style={{ fontSize: 12.5, color: C.dim }}>{t('sessions.delete_running_hint')}</div>
            )}
          </div>
        ) : detail && renaming ? (
          <div style={{ display: 'flex', gap: 8, width: '100%' }}>
            <button className="mobile-btn mobile-btn-secondary" onClick={onCancelRename} disabled={saving}>
              {t('sessions.rename_cancel')}
            </button>
            <button className="mobile-btn mobile-btn-primary" onClick={onSaveRename} disabled={saving || !editName.trim()}>
              <Check size={16} aria-hidden="true" /> {t('sessions.rename_save')}
            </button>
          </div>
        ) : undefined}
      >
        {detail && (
          <div>
            {renaming && (
              <label style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 12, fontSize: 13, color: C.muted }}>
                {t('sessions.rename_action')}
                <input
                  autoFocus
                  value={editName}
                  onChange={e => onEditName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') onSaveRename() }}
                  enterKeyHint="done"
                  disabled={saving}
                  className="form-input"
                  style={{ fontSize: 16, minHeight: 44, boxSizing: 'border-box', width: '100%' }}
                />
              </label>
            )}
            {row(t('sessions.col_status'), <StatusBadge status={detail.status} />)}
            {detail.status === 'FAILED' && detail.failure_reason && (
              <div style={{
                padding: '10px 0', borderBottom: `1px solid ${C.border}`, fontSize: 13,
                color: C.dim, lineHeight: 1.45, overflowWrap: 'anywhere',
              }}>
                <span style={{ color: C.muted }}>{t('sessions.failure_detail_label')} </span>
                {detail.failure_reason}
              </div>
            )}
            {row(t('sessions.col_dataset'), detail.dataset_filename ?? detail.dataset_name ?? '—')}
            {row(t('sessions.col_created'), fmtDate(detail.created_at))}
            {row(t('sessions.col_horizon'), detail.horizon ?? '—')}
            {row(t('sessions.col_granularity'), granularityLabel(detail.granularity))}
            {row(t('sessions.col_skus'), detail.sku_count ?? '—')}
          </div>
        )}
      </BottomSheet>
    </>
  )
}
