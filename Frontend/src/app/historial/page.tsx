'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Archive, ArchiveRestore, Check, ChevronLeft, ChevronRight, History, Pencil, Search, Target, X } from 'lucide-react'
import { archiveSession, getSessionLibrary, patchSession, restoreSession } from '@/lib/api'
import type { SessionLibraryQuery } from '@/lib/api'
import type { SessionStatus, SessionSummary } from '@/lib/types'
import { EmptyState, ErrorState, LoadingState, SkeletonTable } from '@/components/ui/States'
import Card from '@/components/ui/Card'
import Table, { Th, Td } from '@/components/ui/Table'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'
import { fmtNum, localeFor } from '@/lib/numberLocale'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { useTenantFacts, has } from '@/hooks/useTenantFacts'
import {
  BottomSheet, MobileList, MobileCard, type StatusTone,
} from '@/components/mobile'

// The sessions library. Every session the tenant ever trained is here, for as
// long as the account exists: nothing expires, and "archive" hides a run from
// the working list without erasing it (it can be restored). Search, filters,
// sort and paging run on the server, so hundreds of runs stay quick.

const PAGE_SIZE = 25

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

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 10, marginLeft: 6,
      background: 'rgba(148,163,184,0.16)', color: 'var(--dim)', whiteSpace: 'nowrap',
      verticalAlign: 'middle',
    }}>{children}</span>
  )
}

const iconBtnStyle: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', padding: 5, borderRadius: 6,
  color: 'var(--dim)', display: 'inline-flex', alignItems: 'center',
}

const fieldStyle: React.CSSProperties = {
  padding: '7px 10px', borderRadius: 8, border: `1px solid ${C.border}`,
  background: C.surface, color: C.text, fontSize: 13, minHeight: 34, boxSizing: 'border-box',
}

type Scope = 'active' | 'archived' | 'all'
type StatusFilter = 'all' | 'COMPLETED' | 'FAILED' | 'IN_PROGRESS' | 'DRAFT'
const STATUS_GROUPS: Record<StatusFilter, string[] | undefined> = {
  all: undefined,
  COMPLETED: ['COMPLETED'],
  FAILED: ['FAILED', 'CANCELLED'],
  IN_PROGRESS: ['QUEUED', 'RUNNING'],
  DRAFT: ['DRAFT', 'DATASET_LOADED', 'INSPECTED', 'COLUMNS_CONFIGURED', 'FEATURES_CONFIGURED', 'MODELS_CONFIGURED'],
}
type SortKey = 'newest' | 'oldest' | 'name' | 'accuracy' | 'accuracy_low'
const SORTS: Record<SortKey, Pick<SessionLibraryQuery, 'sort' | 'order'>> = {
  newest: { sort: 'created_at', order: 'desc' },
  oldest: { sort: 'created_at', order: 'asc' },
  name: { sort: 'name', order: 'asc' },
  accuracy: { sort: 'accuracy', order: 'desc' },
  accuracy_low: { sort: 'accuracy', order: 'asc' },
}

export default function SessionsHistoryPage() {
  const { t, lang } = useLanguage()
  // Comparing a forecast with reality only means something once there are two
  // finished runs to tell apart (docs/simplicity-audit.md R3).
  const canCompareReality = has(useTenantFacts().completedSessions, 2)
  const router   = useRouter()
  const confirm  = useConfirm()
  const user     = getUser()
  const canEdit  = user?.role === 'admin' || user?.role === 'analyst'

  const [items,     setItems]     = useState<SessionSummary[]>([])
  const [total,     setTotal]     = useState(0)
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState<unknown>(null)
  // Filters (server-side). `search` is what the box shows, `q` what was sent.
  const [search,    setSearch]    = useState('')
  const [q,         setQ]         = useState('')
  const [scope,     setScope]     = useState<Scope>('active')
  const [statusF,   setStatusF]   = useState<StatusFilter>('all')
  const [sortKey,   setSortKey]   = useState<SortKey>('newest')
  const [page,      setPage]      = useState(0)
  // Inline rename state — one row at a time.
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName,  setEditName]  = useState('')
  const [saving,    setSaving]    = useState(false)
  // Phones: the run whose detail sheet is open. Kept as an id so a rename
  // shows up in the open sheet without re-opening it.
  const narrow = useIsNarrow()
  const [detailId,  setDetailId]  = useState<string | null>(null)

  // Typing waits a beat before it hits the server, and goes back to page one.
  useEffect(() => {
    const h = setTimeout(() => { setQ(search); setPage(0) }, 300)
    return () => clearTimeout(h)
  }, [search])

  const reqSeq = useRef(0)
  const load = useCallback(async (initial = false) => {
    const seq = ++reqSeq.current
    if (initial) setLoading(true)
    setError(null)
    try {
      const r = await getSessionLibrary({
        skip: page * PAGE_SIZE, limit: PAGE_SIZE, q, archived: scope,
        status: STATUS_GROUPS[statusF], ...SORTS[sortKey],
      })
      if (seq !== reqSeq.current) return   // a newer request owns the screen
      setItems(r.items)
      setTotal(r.total)
      // The last page emptied (an archive, a narrower filter): step back.
      if (r.items.length === 0 && r.total > 0 && page > 0) setPage(p => p - 1)
    } catch (e: unknown) {
      if (seq === reqSeq.current) setError(e)
    } finally {
      if (seq === reqSeq.current && initial) setLoading(false)
    }
  }, [page, q, scope, statusF, sortKey])

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

  // Archiving is deliberate (a confirmation that says what it does and does
  // not do) and recorded in the activity log by the server. Nothing is erased.
  const archive = async (s: SessionSummary) => {
    const okToArchive = await confirm({
      title: t('sessions.archive_title'),
      message: t('sessions.archive_msg', { name: s.name }),
      confirmLabel: t('sessions.archive_action'),
    })
    if (!okToArchive) return
    try {
      await archiveSession(s.session_id)
    } catch { return }   // the interceptor already told the user why
    setDetailId(null)
    load()
  }

  const restore = async (s: SessionSummary) => {
    try {
      await restoreSession(s.session_id)
    } catch { return }   // e.g. at the plan's ceiling: the message says so
    setDetailId(null)
    load()
  }

  const openResults = (s: SessionSummary) => {
    if (s.status !== 'COMPLETED') return
    router.push(`/pronosticos?session=${encodeURIComponent(s.session_id)}`)
  }
  const openAccuracy = (s: SessionSummary) =>
    router.push(`/precision?session=${encodeURIComponent(s.session_id)}`)

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

  const accuracyLabel = (s: SessionSummary) =>
    s.accuracy == null ? '—' : `${fmtNum(Math.max(0, s.accuracy) * 100, { maximumFractionDigits: 1 })}%`
  // Two names and a "+N": the full list is in the tooltip and the phone sheet.
  const modelsLabel = (s: SessionSummary, full = false) =>
    !s.models || !s.models.length ? '—'
      : full || s.models.length <= 2 ? s.models.join(', ')
      : `${s.models.slice(0, 2).join(', ')} +${s.models.length - 2}`

  const filtersActive = q.trim() !== '' || statusF !== 'all' || scope !== 'active'
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1)
  const from = total === 0 ? 0 : page * PAGE_SIZE + 1
  const to = Math.min(total, page * PAGE_SIZE + items.length)

  const resetFilters = () => {
    setSearch(''); setQ(''); setScope('active'); setStatusF('all'); setPage(0)
  }

  const toolbar = (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
      <label style={{ position: 'relative', flex: '1 1 220px', maxWidth: 360 }}>
        <Search size={14} aria-hidden="true"
                style={{ position: 'absolute', left: 10, top: 10, color: C.dim }} />
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder={t('sessions.search_placeholder')}
          aria-label={t('sessions.search_placeholder')}
          style={{ ...fieldStyle, width: '100%', paddingLeft: 30 }}
        />
      </label>
      <select value={statusF} aria-label={t('sessions.filter_status')}
              onChange={e => { setStatusF(e.target.value as StatusFilter); setPage(0) }} style={fieldStyle}>
        <option value="all">{t('sessions.filter_status_all')}</option>
        <option value="COMPLETED">{t('sessions.status_COMPLETED')}</option>
        <option value="IN_PROGRESS">{t('sessions.filter_status_in_progress')}</option>
        <option value="FAILED">{t('sessions.filter_status_failed')}</option>
        <option value="DRAFT">{t('sessions.filter_status_draft')}</option>
      </select>
      <select value={scope} aria-label={t('sessions.filter_scope')}
              onChange={e => { setScope(e.target.value as Scope); setPage(0) }} style={fieldStyle}>
        <option value="active">{t('sessions.scope_active')}</option>
        <option value="archived">{t('sessions.scope_archived')}</option>
        <option value="all">{t('sessions.scope_all')}</option>
      </select>
      <select value={sortKey} aria-label={t('sessions.sort_label')}
              onChange={e => { setSortKey(e.target.value as SortKey); setPage(0) }} style={fieldStyle}>
        <option value="newest">{t('sessions.sort_newest')}</option>
        <option value="oldest">{t('sessions.sort_oldest')}</option>
        <option value="name">{t('sessions.sort_name')}</option>
        <option value="accuracy">{t('sessions.sort_accuracy_high')}</option>
        <option value="accuracy_low">{t('sessions.sort_accuracy_low')}</option>
      </select>
    </div>
  )

  const pager = total > PAGE_SIZE && (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10,
                  fontSize: 12, color: C.dim, flexWrap: 'wrap' }}>
      <span data-testid="sessions-range">{t('sessions.pager_range', { from, to, total })}</span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <button className="btn-secondary" style={{ ...fieldStyle, cursor: page === 0 ? 'not-allowed' : 'pointer', opacity: page === 0 ? 0.5 : 1 }}
                disabled={page === 0} onClick={() => setPage(p => Math.max(0, p - 1))}
                aria-label={t('sessions.pager_prev')}>
          <ChevronLeft size={14} aria-hidden="true" />
        </button>
        <span>{t('sessions.pager_page', { page: page + 1, pages: lastPage + 1 })}</span>
        <button className="btn-secondary" style={{ ...fieldStyle, cursor: page >= lastPage ? 'not-allowed' : 'pointer', opacity: page >= lastPage ? 0.5 : 1 }}
                disabled={page >= lastPage} onClick={() => setPage(p => Math.min(lastPage, p + 1))}
                aria-label={t('sessions.pager_next')}>
          <ChevronRight size={14} aria-hidden="true" />
        </button>
      </span>
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

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

      <div style={{ fontSize: 12, color: C.dim, lineHeight: 1.5 }}>{t('sessions.permanent_note')}</div>

      {toolbar}

      {loading ? (
        <Card padding={8}>
          <LoadingState label={t('common.loading')}>
            <SkeletonTable rows={6} columns={narrow ? 1 : 8} />
          </LoadingState>
        </Card>
      ) : error ? (
        <ErrorState error={error} onRetry={() => load(true)} />
      ) : items.length === 0 ? (
        filtersActive ? (
          <EmptyState
            icon={<Search size={22} />}
            title={t('sessions.no_matches_title')}
            body={t('sessions.no_matches_hint')}
            actions={[{ label: t('sessions.clear_filters'), onClick: resetFilters }]}
          />
        ) : (
          <EmptyState
            icon={<History size={22} />}
            title={t('sessions.empty_title')}
            body={t('sessions.empty_hint')}
            actions={[{ label: t('hoy.empty_cta_primary'), href: '/ventas' }]}
          />
        )
      ) : narrow ? (
        <>
          <HistoryCards
            items={items}
            detailId={detailId}
            onOpen={id => { cancelRename(); setDetailId(id) }}
            onClose={() => { cancelRename(); setDetailId(null) }}
            canEdit={canEdit}
            fmtDate={fmtDate}
            granularityLabel={granularityLabel}
            accuracyLabel={accuracyLabel}
            modelsLabel={modelsLabel}
            onOpenResults={openResults}
            onOpenAccuracy={openAccuracy}
            editingId={editingId}
            editName={editName}
            saving={saving}
            onStartRename={startRename}
            onEditName={setEditName}
            onSaveRename={saveRename}
            onCancelRename={cancelRename}
            onArchive={archive}
            onRestore={restore}
          />
          {pager}
        </>
      ) : (
        <>
          <Card padding={0} overflow="auto">
            <Table>
              <thead>
                <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                  {[
                    t('sessions.col_name'), t('sessions.col_status'), t('sessions.col_dataset'),
                    t('sessions.col_created'), t('sessions.col_horizon'),
                    t('sessions.col_granularity'), t('sessions.col_skus'),
                    t('sessions.col_accuracy'), t('sessions.col_models'), '',
                  ].map((h, i) => <Th key={i}>{h}</Th>)}
                </tr>
              </thead>
              <tbody>
                {items.map((s, idx) => {
                  const clickable = s.status === 'COMPLETED'
                  const isEditing = editingId === s.session_id
                  const archived = !!s.archived_at
                  return (
                    <tr
                      key={s.session_id}
                      data-tour={idx === 0 ? 'ses.row' : undefined}
                      data-archived={archived ? 'true' : undefined}
                      onClick={() => { if (!isEditing) openResults(s) }}
                      title={clickable ? t('sessions.view_results') : undefined}
                      style={{
                        borderBottom: `1px solid ${C.border}`,
                        cursor: clickable && !isEditing ? 'pointer' : 'default',
                        opacity: archived ? 0.72 : 1,
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
                        ) : (
                          <>
                            {s.name}
                            {s.is_backtest && <Tag>{t('sessions.tag_backtest')}</Tag>}
                            {archived && <Tag>{t('sessions.tag_archived')}</Tag>}
                          </>
                        )}
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
                      <Td divider={false} nowrap style={{ color: C.text, fontVariantNumeric: 'tabular-nums' }}
                          title={t('sessions.accuracy_hint')}>{accuracyLabel(s)}</Td>
                      <Td divider={false} title={modelsLabel(s, true)}
                          style={{ color: C.muted, maxWidth: 150, whiteSpace: 'normal', fontSize: 12 }}>
                        {modelsLabel(s)}
                      </Td>
                      <Td divider={false} nowrap align="right"
                          data-tour={idx === 0 ? 'ses.actions' : undefined}
                          onClick={e => e.stopPropagation()}>
                        {s.status === 'COMPLETED' && canCompareReality && (
                          <button onClick={() => openAccuracy(s)} title={t('sessions.compare_reality')}
                                  aria-label={t('sessions.compare_reality')} style={iconBtnStyle}>
                            <Target size={14} />
                          </button>
                        )}
                        {canEdit && !isEditing && (
                          <>
                            <button onClick={() => startRename(s)}
                                    data-tour={idx === 0 ? 'ses.rename' : undefined}
                                    title={t('sessions.rename_action')} style={iconBtnStyle}>
                              <Pencil size={14} />
                            </button>
                            {archived ? (
                              <button onClick={() => restore(s)}
                                      title={t('sessions.restore_action')}
                                      aria-label={t('sessions.restore_action')} style={iconBtnStyle}>
                                <ArchiveRestore size={14} />
                              </button>
                            ) : (
                              <button
                                onClick={() => archive(s)}
                                data-tour={idx === 0 ? 'ses.delete' : undefined}
                                disabled={s.status === 'RUNNING' || s.status === 'QUEUED'}
                                title={s.status === 'RUNNING' || s.status === 'QUEUED'
                                  ? t('sessions.archive_running_hint')
                                  : t('sessions.archive_action')}
                                aria-label={t('sessions.archive_action')}
                                style={{
                                  ...iconBtnStyle,
                                  opacity: s.status === 'RUNNING' || s.status === 'QUEUED' ? 0.4 : 1,
                                  cursor: s.status === 'RUNNING' || s.status === 'QUEUED' ? 'not-allowed' : 'pointer',
                                }}
                              >
                                <Archive size={14} />
                              </button>
                            )}
                          </>
                        )}
                      </Td>
                    </tr>
                  )
                })}
              </tbody>
            </Table>
          </Card>
          {pager}
        </>
      )}
    </div>
  )
}

// ── Phone layout ─────────────────────────────────────────────────────────────
// Ten columns do not fit 360px, and the row icons were 24px targets at the far
// right of a sideways-scrolling row. On a phone each run is a card (name,
// dataset and date, status, SKU count); tapping it opens a sheet with every
// column, the failure reason, and what a run can be asked to do — open its
// results, compare it with reality, rename it, archive or restore it — as
// full-width buttons.
function HistoryCards({
  items, detailId, onOpen, onClose, canEdit, fmtDate, granularityLabel, accuracyLabel, modelsLabel,
  onOpenResults, onOpenAccuracy,
  editingId, editName, saving, onStartRename, onEditName, onSaveRename, onCancelRename,
  onArchive, onRestore,
}: {
  items: SessionSummary[]
  detailId: string | null
  onOpen: (id: string) => void
  onClose: () => void
  canEdit: boolean
  fmtDate: (iso: string) => string
  granularityLabel: (g: string | null) => string
  accuracyLabel: (s: SessionSummary) => string
  modelsLabel: (s: SessionSummary, full?: boolean) => string
  onOpenResults: (s: SessionSummary) => void
  onOpenAccuracy: (s: SessionSummary) => void
  editingId: string | null
  editName: string
  saving: boolean
  onStartRename: (s: SessionSummary) => void
  onEditName: (v: string) => void
  onSaveRename: () => void
  onCancelRename: () => void
  onArchive: (s: SessionSummary) => void
  onRestore: (s: SessionSummary) => void
}) {
  const { t } = useLanguage()
  const canCompareReality = has(useTenantFacts().completedSessions, 2)
  const detail = items.find(i => i.session_id === detailId) ?? null
  const renaming = !!detail && editingId === detail.session_id
  const inFlight = !!detail && (detail.status === 'RUNNING' || detail.status === 'QUEUED')

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
            subtitle={`${s.dataset_filename ?? s.dataset_name ?? '—'} · ${fmtDate(s.created_at)}${s.archived_at ? ` · ${t('sessions.tag_archived')}` : ''}`}
            status={{ label: t(`sessions.status_${s.status}`), tone: STATUS_TONE[s.status] ?? 'neutral' }}
            value={s.accuracy != null ? accuracyLabel(s) : (s.sku_count ?? '—')}
            valueCaption={s.accuracy != null ? t('sessions.col_accuracy') : t('sessions.col_skus')}
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
            {detail.status === 'COMPLETED' && canCompareReality && (
              <button className="mobile-btn mobile-btn-secondary" style={{ flex: 'none', width: '100%' }}
                      onClick={() => onOpenAccuracy(detail)}>
                <Target size={16} aria-hidden="true" /> {t('sessions.compare_reality')}
              </button>
            )}
            {canEdit && (
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="mobile-btn mobile-btn-secondary" onClick={() => onStartRename(detail)}>
                  <Pencil size={16} aria-hidden="true" /> {t('sessions.rename_action')}
                </button>
                {detail.archived_at ? (
                  <button className="mobile-btn mobile-btn-secondary" onClick={() => onRestore(detail)}>
                    <ArchiveRestore size={16} aria-hidden="true" /> {t('sessions.restore_action')}
                  </button>
                ) : (
                  <button className="mobile-btn mobile-btn-secondary" disabled={inFlight}
                          onClick={() => onArchive(detail)}>
                    <Archive size={16} aria-hidden="true" /> {t('sessions.archive_action')}
                  </button>
                )}
              </div>
            )}
            {canEdit && inFlight && (
              <div style={{ fontSize: 12.5, color: C.dim }}>{t('sessions.archive_running_hint')}</div>
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
            {detail.archived_at && row(t('sessions.tag_archived'), fmtDate(detail.archived_at))}
            {detail.is_backtest && row(t('sessions.tag_backtest'),
              t('sessions.backtest_periods', { n: detail.backtest_holdout_periods ?? '—' }))}
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
            {row(t('sessions.col_accuracy'), accuracyLabel(detail))}
            {row(t('sessions.col_models'), modelsLabel(detail, true))}
          </div>
        )}
      </BottomSheet>
    </>
  )
}
