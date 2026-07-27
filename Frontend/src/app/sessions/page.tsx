'use client'
import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Check, History, Pencil, Trash2, X } from 'lucide-react'
import { deleteSession, getSessionSummaries, patchSession } from '@/lib/api'
import type { SessionStatus, SessionSummary } from '@/lib/types'
import { EmptyState, ErrorState, LoadingState, SkeletonTable } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', muted: 'var(--muted)',
  accent: 'var(--accent)',
}

const STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  COMPLETED: { bg: 'rgba(16,185,129,0.12)', fg: '#10b981' },
  FAILED:    { bg: 'rgba(239,68,68,0.12)',  fg: '#ef4444' },
  CANCELLED: { bg: 'rgba(239,68,68,0.08)',  fg: '#f87171' },
  RUNNING:   { bg: 'rgba(245,158,11,0.12)', fg: '#f59e0b' },
  QUEUED:    { bg: 'rgba(245,158,11,0.08)', fg: '#f59e0b' },
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

const iconBtnStyle: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', padding: 5, borderRadius: 6,
  color: 'var(--dim)', display: 'inline-flex', alignItems: 'center',
}

export default function SessionsHistoryPage() {
  const { t }    = useLanguage()
  const router   = useRouter()
  const confirm  = useConfirm()
  const user     = getUser()
  const canEdit  = user?.role === 'admin' || user?.role === 'analyst'

  const [items,     setItems]     = useState<SessionSummary[]>([])
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState<unknown>(null)
  // Inline rename state — one row at a time.
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName,  setEditName]  = useState('')
  const [saving,    setSaving]    = useState(false)

  const load = useCallback(async (initial = false) => {
    if (initial) setLoading(true)
    setError(null)
    try {
      const r = await getSessionSummaries(0, 200)
      setItems(r.items)
    } catch (e: unknown) {
      setError(e)
    } finally {
      if (initial) setLoading(false)
    }
  }, [])

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
    load()
  }

  const openResults = (s: SessionSummary) => {
    if (s.status !== 'COMPLETED') return
    router.push(`/skus?session=${encodeURIComponent(s.session_id)}`)
  }

  const fmtDate = (iso: string) => {
    const d = new Date(iso)
    return isNaN(d.getTime()) ? '—' : d.toLocaleDateString(undefined, {
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, animation: 'fadeIn 0.3s ease-out' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 9, background: '#6366f1',
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

      {loading ? (
        <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: 8 }}>
          <LoadingState label={t('common.loading')}>
            <SkeletonTable rows={6} columns={7} />
          </LoadingState>
        </div>
      ) : error ? (
        <ErrorState error={error} onRetry={() => load(true)} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<History size={22} />}
          title={t('sessions.empty_title')}
          body={t('sessions.empty_hint')}
        />
      ) : (
        <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                {[
                  t('sessions.col_name'), t('sessions.col_status'), t('sessions.col_dataset'),
                  t('sessions.col_created'), t('sessions.col_horizon'),
                  t('sessions.col_granularity'), t('sessions.col_skus'), '',
                ].map((h, i) => (
                  <th key={i} style={{
                    textAlign: 'left', padding: '10px 14px', fontSize: 11, fontWeight: 700,
                    color: C.dim, textTransform: 'uppercase', letterSpacing: '0.05em', whiteSpace: 'nowrap',
                  }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map(s => {
                const clickable = s.status === 'COMPLETED'
                const isEditing = editingId === s.session_id
                return (
                  <tr
                    key={s.session_id}
                    onClick={() => { if (!isEditing) openResults(s) }}
                    title={clickable ? t('sessions.view_results') : undefined}
                    style={{
                      borderBottom: `1px solid ${C.border}`,
                      cursor: clickable && !isEditing ? 'pointer' : 'default',
                    }}
                  >
                    <td style={{ padding: '10px 14px', color: C.text, fontWeight: 600, minWidth: 180 }}>
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
                                  title={t('sessions.rename_save')} style={{ ...iconBtnStyle, color: '#10b981' }}>
                            <Check size={14} />
                          </button>
                          <button onClick={cancelRename} disabled={saving}
                                  title={t('sessions.rename_cancel')} style={iconBtnStyle}>
                            <X size={14} />
                          </button>
                        </span>
                      ) : s.name}
                    </td>
                    <td style={{ padding: '10px 14px' }}><StatusBadge status={s.status} /></td>
                    <td style={{ padding: '10px 14px', color: C.muted, whiteSpace: 'nowrap' }}>
                      {s.dataset_filename ?? s.dataset_name ?? '—'}
                    </td>
                    <td style={{ padding: '10px 14px', color: C.muted, whiteSpace: 'nowrap' }}>
                      {fmtDate(s.created_at)}
                    </td>
                    <td style={{ padding: '10px 14px', color: C.muted }}>{s.horizon ?? '—'}</td>
                    <td style={{ padding: '10px 14px', color: C.muted }}>{granularityLabel(s.granularity)}</td>
                    <td style={{ padding: '10px 14px', color: C.muted }}>{s.sku_count ?? '—'}</td>
                    <td style={{ padding: '10px 14px', whiteSpace: 'nowrap', textAlign: 'right' }}
                        onClick={e => e.stopPropagation()}>
                      {canEdit && !isEditing && (
                        <>
                          <button onClick={() => startRename(s)}
                                  title={t('sessions.rename_action')} style={iconBtnStyle}>
                            <Pencil size={14} />
                          </button>
                          <button
                            onClick={() => removeSession(s)}
                            disabled={s.status === 'RUNNING'}
                            title={s.status === 'RUNNING'
                              ? t('sessions.delete_running_hint')
                              : t('sessions.delete_action')}
                            style={{
                              ...iconBtnStyle,
                              color: s.status === 'RUNNING' ? 'var(--dim)' : '#ef4444',
                              opacity: s.status === 'RUNNING' ? 0.4 : 1,
                              cursor: s.status === 'RUNNING' ? 'not-allowed' : 'pointer',
                            }}
                          >
                            <Trash2 size={14} />
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
