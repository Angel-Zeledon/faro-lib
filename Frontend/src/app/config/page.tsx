'use client'
import { useState, useEffect, useCallback } from 'react'
import {
  User, Settings2, Cpu, Activity,
  Moon, Sun, Globe, CheckCircle2, Edit2, X,
  ChevronDown, Clock, Shield, Sparkles, Lock, Eye, EyeOff, Mail,
} from 'lucide-react'
import Spinner from '@/components/ui/Spinner'
import { useTheme } from '@/contexts/ThemeContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'
import {
  getMe, updateMe,
  getPreferences, updatePreferences,
  getPlatformModels,
  getActivityLogs, getActivityActionTypes,
  requestPasswordChange, confirmPasswordChange,
} from '@/lib/api'
import type { PlatformModel, ActivityLog } from '@/lib/types'

// ── helpers ───────────────────────────────────────────────────────────────────

function formatDate(iso: string, lang: 'es' | 'en') {
  return new Date(iso).toLocaleString(lang === 'es' ? 'es-CR' : 'en-US', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

const CAT_COLOR: Record<string, string> = {
  'ML':            '#818cf8',
  'Statistical':   '#22c55e',
  'Deep Learning': '#f59e0b',
}

const STATUS_COLOR: Record<string, string> = {
  available: '#22c55e',
  beta:      '#f59e0b',
  disabled:  '#64748b',
}

// ── Section header ────────────────────────────────────────────────────────────

function SectionTitle({ icon: Icon, color, title, subtitle }: {
  icon: React.ElementType; color: string; title: string; subtitle: string
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
      <div style={{
        width: 38, height: 38, borderRadius: 10, flexShrink: 0,
        background: color + '18',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <Icon size={16} color={color} strokeWidth={1.8} />
      </div>
      <div>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)', letterSpacing: '-0.01em' }}>{title}</div>
        <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 1 }}>{subtitle}</div>
      </div>
    </div>
  )
}

// ── Card wrapper ──────────────────────────────────────────────────────────────

function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--border)',
      borderRadius: 14, padding: '24px',
      ...style,
    }}>
      {children}
    </div>
  )
}

// ── Toggle switch ─────────────────────────────────────────────────────────────

function Toggle({ on, onChange }: { on: boolean; onChange: () => void }) {
  return (
    <button
      onClick={onChange}
      style={{
        all: 'unset', cursor: 'pointer',
        width: 42, height: 22, borderRadius: 11,
        background: on ? 'var(--accent)' : 'var(--border-strong)',
        transition: 'background 0.2s',
        position: 'relative', flexShrink: 0,
      }}
    >
      <span style={{
        position: 'absolute', top: 3,
        left: on ? 22 : 3,
        width: 16, height: 16, borderRadius: '50%',
        background: '#fff',
        transition: 'left 0.2s',
        boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
      }} />
    </button>
  )
}

// ── Section 1: User Profile ───────────────────────────────────────────────────

function ProfileSection({ t, lang }: { t: (k: string) => string; lang: 'es' | 'en' }) {
  const me = getUser()
  const [editing,  setEditing]  = useState(false)
  const [name,     setName]     = useState(me?.full_name || '')
  const [saving,   setSaving]   = useState(false)
  const [feedback, setFeedback] = useState<'saved' | null>(null)

  // WhatsApp opt-in for daily inventory alerts (stored server-side)
  const [wa,         setWa]         = useState('')
  const [waSaving,   setWaSaving]   = useState(false)
  const [waFeedback, setWaFeedback] = useState<'saved' | 'error' | null>(null)
  const [waError,    setWaError]    = useState('')

  useEffect(() => {
    getMe()
      .then(u => setWa((u as { whatsapp_number?: string | null }).whatsapp_number || ''))
      .catch(() => {})
  }, [])

  async function handleSaveWa() {
    setWaSaving(true)
    setWaFeedback(null)
    try {
      await updateMe({ whatsapp_number: wa.trim() })
      setWaFeedback('saved')
      setTimeout(() => setWaFeedback(null), 2500)
    } catch (e: unknown) {
      setWaError(e instanceof Error ? e.message : 'Error')
      setWaFeedback('error')
    } finally {
      setWaSaving(false)
    }
  }

  async function handleSave() {
    if (!name.trim()) return
    setSaving(true)
    try {
      await updateMe({ full_name: name.trim() })
      if (me) me.full_name = name.trim()
      setFeedback('saved')
      setEditing(false)
      setTimeout(() => setFeedback(null), 2500)
    } finally {
      setSaving(false)
    }
  }

  const initials = (me?.full_name || me?.email || 'U')
    .split(' ').map((w: string) => w[0]).slice(0, 2).join('').toUpperCase()

  return (
    <Card>
      <SectionTitle icon={User} color="#818cf8" title={t('user_profile')} subtitle={t('email')} />
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        {/* Avatar */}
        <div style={{
          width: 64, height: 64, borderRadius: 16, flexShrink: 0,
          background: 'linear-gradient(135deg, #818cf8, #6366f1)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 22, fontWeight: 700, color: '#fff',
        }}>
          {initials}
        </div>

        {/* Fields */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 12 }}>

          {/* Full name */}
          <div>
            <label style={{ fontSize: 11, color: 'var(--dim)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              {t('full_name')}
            </label>
            {editing ? (
              <div style={{ display: 'flex', gap: 8, marginTop: 5 }}>
                <input
                  className="form-input"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  autoFocus
                  onKeyDown={e => e.key === 'Enter' && handleSave()}
                  style={{ fontSize: 13 }}
                />
                <button
                  onClick={handleSave}
                  disabled={saving}
                  style={{
                    all: 'unset', cursor: 'pointer',
                    padding: '7px 14px', borderRadius: 7, fontSize: 12, fontWeight: 600,
                    background: 'var(--accent)', color: '#fff',
                    opacity: saving ? 0.6 : 1,
                  }}
                >
                  {saving ? t('saving') : t('save_changes')}
                </button>
                <button
                  onClick={() => { setEditing(false); setName(me?.full_name || '') }}
                  style={{
                    all: 'unset', cursor: 'pointer', padding: '7px 10px',
                    borderRadius: 7, color: 'var(--dim)',
                    border: '1px solid var(--border)',
                  }}
                >
                  <X size={13} />
                </button>
              </div>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
                <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>
                  {me?.full_name || '—'}
                </span>
                {feedback === 'saved' && (
                  <span style={{ fontSize: 11, color: 'var(--success)', display: 'flex', alignItems: 'center', gap: 4 }}>
                    <CheckCircle2 size={11} /> {t('saved')}
                  </span>
                )}
                <button
                  onClick={() => setEditing(true)}
                  title={t('edit')}
                  style={{
                    all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5,
                    color: 'var(--dim)', display: 'flex', alignItems: 'center',
                  }}
                >
                  <Edit2 size={12} />
                </button>
              </div>
            )}
          </div>

          {/* Email (read-only) */}
          <div>
            <label style={{ fontSize: 11, color: 'var(--dim)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              {t('email')}
            </label>
            <div style={{ fontSize: 13, color: 'var(--muted)', marginTop: 4 }}>{me?.email}</div>
          </div>

          {/* WhatsApp para alertas diarias de inventario */}
          <div>
            <label style={{ fontSize: 11, color: 'var(--dim)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              WhatsApp (alertas de inventario)
            </label>
            <div style={{ display: 'flex', gap: 8, marginTop: 5, alignItems: 'center' }}>
              <input
                className="form-input"
                placeholder="+573001234567"
                value={wa}
                onChange={e => setWa(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleSaveWa()}
                style={{ fontSize: 13, maxWidth: 220 }}
              />
              <button
                onClick={handleSaveWa}
                disabled={waSaving}
                style={{
                  all: 'unset', cursor: 'pointer',
                  padding: '7px 14px', borderRadius: 7, fontSize: 12, fontWeight: 600,
                  background: 'var(--accent)', color: '#fff',
                  opacity: waSaving ? 0.6 : 1,
                }}
              >
                {waSaving ? t('saving') : t('save_changes')}
              </button>
              {waFeedback === 'saved' && (
                <span style={{ fontSize: 11, color: 'var(--success)', display: 'flex', alignItems: 'center', gap: 4 }}>
                  <CheckCircle2 size={11} /> {t('saved')}
                </span>
              )}
              {waFeedback === 'error' && (
                <span style={{ fontSize: 11, color: '#ef4444' }}>{waError}</span>
              )}
            </div>
            <p style={{ fontSize: 11, color: 'var(--dim)', margin: '5px 0 0' }}>
              Con código de país (ej. +57…). Déjalo vacío para no recibir alertas por WhatsApp.
            </p>
          </div>

          {/* Role + Status */}
          <div style={{ display: 'flex', gap: 16 }}>
            <div>
              <label style={{ fontSize: 11, color: 'var(--dim)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                {t('role')}
              </label>
              <div style={{
                marginTop: 5, display: 'inline-block',
                padding: '3px 10px', borderRadius: 20, fontSize: 11, fontWeight: 600,
                background: 'rgba(129,140,248,0.12)', color: 'var(--accent)',
                textTransform: 'capitalize',
              }}>
                {me?.role || '—'}
              </div>
            </div>
            <div>
              <label style={{ fontSize: 11, color: 'var(--dim)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                {t('account_status')}
              </label>
              <div style={{
                marginTop: 5, display: 'inline-block',
                padding: '3px 10px', borderRadius: 20, fontSize: 11, fontWeight: 600,
                background: 'rgba(34,197,94,0.12)', color: 'var(--success)',
              }}>
                {t('active')}
              </div>
            </div>
          </div>
        </div>
      </div>
    </Card>
  )
}

// ── Section 2: App Config ─────────────────────────────────────────────────────

function AppConfigSection({ t }: { t: (k: string) => string }) {
  const { theme, setTheme }  = useTheme()
  const { lang, setLang }    = useLanguage()
  const [saving, setSaving]  = useState<'lang' | 'theme' | null>(null)

  async function handleTheme(val: 'dark' | 'light') {
    setTheme(val)
    setSaving('theme')
    try { await updatePreferences({ theme: val }) } finally { setSaving(null) }
  }

  async function handleLang(val: 'es' | 'en') {
    setLang(val)
    setSaving('lang')
    try { await updatePreferences({ language: val }) } finally { setSaving(null) }
  }

  return (
    <Card>
      <SectionTitle icon={Settings2} color="#22c55e" title={t('app_settings')} subtitle={`${t('language')} · ${t('theme')}`} />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>

        {/* Language */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '14px 0', borderBottom: '1px solid var(--border)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              width: 32, height: 32, borderRadius: 8,
              background: 'rgba(34,197,94,0.1)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <Globe size={14} color="#22c55e" />
            </div>
            <div>
              <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)' }}>{t('language')}</div>
              <div style={{ fontSize: 11, color: 'var(--dim)' }}>{lang === 'es' ? t('spanish') : t('english')}</div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            {(['es', 'en'] as const).map(l => (
              <button
                key={l}
                onClick={() => handleLang(l)}
                style={{
                  all: 'unset', cursor: 'pointer',
                  padding: '6px 14px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                  border: `1px solid ${lang === l ? 'var(--accent)' : 'var(--border)'}`,
                  background: lang === l ? 'var(--accent-dim)' : 'transparent',
                  color: lang === l ? 'var(--accent)' : 'var(--muted)',
                  transition: 'all 0.15s',
                  opacity: saving === 'lang' ? 0.6 : 1,
                }}
              >
                {l === 'es' ? 'Español' : 'English'}
              </button>
            ))}
          </div>
        </div>

        {/* Theme */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '14px 0',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              width: 32, height: 32, borderRadius: 8,
              background: 'rgba(129,140,248,0.1)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              {theme === 'dark' ? <Moon size={14} color="#818cf8" /> : <Sun size={14} color="#818cf8" />}
            </div>
            <div>
              <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)' }}>{t('theme')}</div>
              <div style={{ fontSize: 11, color: 'var(--dim)' }}>{theme === 'dark' ? t('dark') : t('light')}</div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            {(['dark', 'light'] as const).map(th => (
              <button
                key={th}
                onClick={() => handleTheme(th)}
                style={{
                  all: 'unset', cursor: 'pointer',
                  padding: '6px 14px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                  border: `1px solid ${theme === th ? 'var(--accent)' : 'var(--border)'}`,
                  background: theme === th ? 'var(--accent-dim)' : 'transparent',
                  color: theme === th ? 'var(--accent)' : 'var(--muted)',
                  transition: 'all 0.15s',
                  opacity: saving === 'theme' ? 0.6 : 1,
                }}
              >
                {th === 'dark' ? t('dark') : t('light')}
              </button>
            ))}
          </div>
        </div>
      </div>
    </Card>
  )
}

// ── Section 3: Available Models ───────────────────────────────────────────────

function ModelsSection({ t }: { t: (k: string) => string }) {
  const [models,  setModels]  = useState<PlatformModel[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getPlatformModels()
      .then(setModels)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  return (
    <Card>
      <SectionTitle icon={Cpu} color="#f59e0b" title={t('available_models')} subtitle={`${models.length} ${t('models_count')}`} />
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 20 }}><Spinner size={18} /></div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 10 }}>
          {models.map(m => {
            const catColor    = CAT_COLOR[m.category]    ?? '#64748b'
            const statusColor = STATUS_COLOR[m.status]   ?? '#64748b'
            return (
              <div
                key={m.name}
                style={{
                  padding: '14px 16px', borderRadius: 10,
                  border: '1px solid var(--border)',
                  background: 'var(--surface-2)',
                  display: 'flex', flexDirection: 'column', gap: 6,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{
                    fontFamily: 'monospace', fontSize: 13, fontWeight: 700,
                    color: 'var(--text)',
                  }}>{m.name}</span>
                  <span style={{
                    fontSize: 9, fontWeight: 700, borderRadius: 5,
                    padding: '2px 7px', textTransform: 'uppercase', letterSpacing: '0.05em',
                    background: statusColor + '18', color: statusColor,
                  }}>
                    {m.status === 'available' ? t('available') : t('beta')}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{
                    fontSize: 9, fontWeight: 600,
                    background: catColor + '18', color: catColor,
                    borderRadius: 5, padding: '2px 7px',
                    textTransform: 'uppercase', letterSpacing: '0.05em',
                  }}>
                    {m.category}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: 'var(--dim)', lineHeight: 1.5 }}>
                  {m.description}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}

// ── Section 4: Activity Logs ──────────────────────────────────────────────────

const PAGE = 15

function ActivitySection({ t, lang }: { t: (k: string) => string; lang: 'es' | 'en' }) {
  const [logs,       setLogs]       = useState<ActivityLog[]>([])
  const [total,      setTotal]      = useState(0)
  const [offset,     setOffset]     = useState(0)
  const [loading,    setLoading]    = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [actionFilter, setActionFilter] = useState('')
  const [actionTypes, setActionTypes]   = useState<string[]>([])
  const [actionTypesErr, setActionTypesErr] = useState(false)
  const [filterOpen,  setFilterOpen]    = useState(false)

  const fetchLogs = useCallback(async (off: number, action: string, append: boolean) => {
    if (off === 0) setLoading(true)
    else setLoadingMore(true)
    try {
      const res = await getActivityLogs({ limit: PAGE, offset: off, action: action || undefined })
      setLogs(prev => append ? [...prev, ...res.items] : res.items)
      setTotal(res.total)
    } finally {
      setLoading(false)
      setLoadingMore(false)
    }
  }, [])

  useEffect(() => {
    fetchLogs(0, actionFilter, false)
    setOffset(0)
  }, [actionFilter, fetchLogs])

  useEffect(() => {
    getActivityActionTypes()
      .then(setActionTypes)
      .catch(() => {
        setActionTypes(['login', 'logout', 'password_change', 'session_create', 'data_export', 'config_update'])
        setActionTypesErr(true)
      })
  }, [])

  function loadMore() {
    const next = offset + PAGE
    setOffset(next)
    fetchLogs(next, actionFilter, true)
  }

  const hasMore = logs.length < total

  return (
    <Card>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 20 }}>
        <SectionTitle
          icon={Activity}
          color="#0ea5e9"
          title={t('activity_logs')}
          subtitle={`${total} ${t('records_count')}`}
        />
        {/* Filter dropdown */}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => setFilterOpen(p => !p)}
            style={{
              all: 'unset', cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '7px 12px', borderRadius: 8,
              border: '1px solid var(--border)',
              fontSize: 12, color: 'var(--muted)',
              background: actionFilter ? 'var(--accent-dim)' : 'transparent',
            }}
          >
            <Shield size={12} color={actionFilter ? 'var(--accent)' : undefined} />
            <span style={{ color: actionFilter ? 'var(--accent)' : undefined }}>
              {actionFilter || t('all_actions')}
            </span>
            <ChevronDown size={11} />
          </button>
          {filterOpen && (
            <div style={{
              position: 'absolute', top: '100%', right: 0, marginTop: 4,
              background: 'var(--surface)', border: '1px solid var(--border)',
              borderRadius: 10, zIndex: 50, minWidth: 180,
              boxShadow: '0 8px 24px rgba(0,0,0,0.25)',
              overflow: 'hidden',
            }}>
              {['', ...actionTypes].map(a => (
                <button
                  key={a || '__all__'}
                  onClick={() => { setActionFilter(a); setFilterOpen(false) }}
                  style={{
                    all: 'unset', cursor: 'pointer', width: '100%',
                    display: 'block', padding: '9px 14px', fontSize: 12,
                    color: a === actionFilter ? 'var(--accent)' : 'var(--muted)',
                    background: a === actionFilter ? 'var(--accent-dim)' : 'transparent',
                    borderBottom: '1px solid var(--border)',
                  }}
                >
                  {a || t('all_actions')}
                </button>
              ))}
            </div>
          )}
          {actionTypesErr && (
            <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 4, textAlign: 'right' }}>
              Could not load action types — showing defaults
            </div>
          )}
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 32 }}><Spinner size={20} /></div>
      ) : logs.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '32px 0', color: 'var(--dim)', fontSize: 13 }}>
          <Clock size={28} style={{ marginBottom: 8, opacity: 0.4 }} />
          <div>{t('no_activity')}</div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
          {/* Header */}
          <div style={{
            display: 'grid', gridTemplateColumns: '1fr 140px 80px 150px',
            padding: '6px 12px', gap: 12,
            fontSize: 10, fontWeight: 600, color: 'var(--dim)',
            textTransform: 'uppercase', letterSpacing: '0.06em',
            borderBottom: '1px solid var(--border)',
          }}>
            <span>{t('action')}</span>
            <span>{t('resource')}</span>
            <span>{t('status_col')}</span>
            <span>{t('date')}</span>
          </div>

          {logs.map(log => (
            <div
              key={log.id}
              style={{
                display: 'grid', gridTemplateColumns: '1fr 140px 80px 150px',
                padding: '10px 12px', gap: 12, alignItems: 'center',
                borderBottom: '1px solid var(--border)',
                fontSize: 12,
                transition: 'background 0.1s',
              }}
              onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface-2)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <span style={{
                color: 'var(--text)', fontFamily: 'monospace', fontSize: 11,
                background: 'var(--surface-2)', padding: '2px 8px',
                borderRadius: 5, display: 'inline-block',
              }}>
                {log.action}
              </span>
              <span style={{ color: 'var(--muted)', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {log.resource || '—'}
              </span>
              <span>
                <span style={{
                  fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 20,
                  background: log.status === 'success' ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)',
                  color: log.status === 'success' ? 'var(--success)' : 'var(--danger)',
                }}>
                  {log.status === 'success' ? t('success') : t('error')}
                </span>
              </span>
              <span style={{ color: 'var(--dim)', fontSize: 11 }}>
                {formatDate(log.created_at, lang)}
              </span>
            </div>
          ))}

          {/* Load more */}
          {hasMore && (
            <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 16 }}>
              <button
                onClick={loadMore}
                disabled={loadingMore}
                style={{
                  all: 'unset', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 6,
                  padding: '8px 20px', borderRadius: 8,
                  border: '1px solid var(--border)',
                  fontSize: 12, color: 'var(--muted)',
                  opacity: loadingMore ? 0.6 : 1,
                }}
              >
                {loadingMore ? <Spinner size={12} /> : null}
                {t('load_more')} ({total - logs.length} {lang === 'es' ? 'restantes' : 'remaining'})
              </button>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

// ── Section: Security / Change password ──────────────────────────────────────

type PwStep = 'idle' | 'form' | 'code' | 'done'

function SecuritySection({ t }: { t: (k: string) => string }) {
  const me = getUser()
  const [step,       setStep]       = useState<PwStep>('idle')
  const [newPw,      setNewPw]      = useState('')
  const [showPw,     setShowPw]     = useState(false)
  const [code,       setCode]       = useState('')
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState<string | null>(null)

  function reset() {
    setStep('idle'); setNewPw(''); setCode(''); setError(null); setShowPw(false)
  }

  async function handleRequestCode() {
    if (!newPw.trim()) return
    if (newPw.trim().length < 8) { setError(t('pw_min_length')); return }
    setLoading(true); setError(null)
    try {
      await requestPasswordChange(newPw)
      setStep('code')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('pw_error_send') || 'Error')
    } finally {
      setLoading(false)
    }
  }

  async function handleConfirm() {
    if (code.length !== 6) return
    setLoading(true); setError(null)
    try {
      await confirmPasswordChange(code, newPw)
      setStep('done')
      setTimeout(reset, 3500)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('pw_error_confirm') || 'Error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card>
      <SectionTitle icon={Lock} color="#f59e0b" title={t('security')} subtitle={t('change_password')} />

      {step === 'idle' && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: 13, color: 'var(--text)', fontWeight: 500 }}>{t('password_label')}</div>
            <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>
              {t('pw_code_hint')} {me?.email}
            </div>
          </div>
          <button
            onClick={() => setStep('form')}
            style={{
              all: 'unset', cursor: 'pointer',
              padding: '7px 16px', borderRadius: 8, fontSize: 12, fontWeight: 600,
              border: '1px solid var(--border)',
              color: 'var(--muted)', background: 'var(--surface-2)',
              transition: 'all 0.15s',
            }}
            onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--accent)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--accent)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--border)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--muted)' }}
          >
            {t('change_pw_btn')}
          </button>
        </div>
      )}

      {step === 'form' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ fontSize: 12, color: 'var(--dim)' }}>{t('pw_form_desc')}</div>
          <div style={{ position: 'relative' }}>
            <input
              type={showPw ? 'text' : 'password'}
              value={newPw}
              onChange={e => { setNewPw(e.target.value); if (error) setError(null) }}
              placeholder={t('pw_placeholder')}
              autoFocus
              onKeyDown={e => e.key === 'Enter' && handleRequestCode()}
              style={{
                width: '100%', background: 'var(--surface-2)',
                border: `1px solid ${error ? 'var(--danger)' : 'var(--border)'}`,
                borderRadius: 8, padding: '9px 40px 9px 12px',
                fontSize: 13, color: 'var(--text)',
                outline: 'none', boxSizing: 'border-box',
                transition: 'border-color 0.15s',
              }}
            />
            <button
              onClick={() => setShowPw(v => !v)}
              style={{
                all: 'unset', position: 'absolute', right: 10, top: '50%',
                transform: 'translateY(-50%)', cursor: 'pointer', color: 'var(--dim)',
                display: 'flex',
              }}
            >
              {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>
          {error && <div style={{ fontSize: 12, color: 'var(--danger)' }}>{error}</div>}
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={handleRequestCode}
              disabled={loading || !newPw.trim()}
              style={{
                all: 'unset', cursor: loading || !newPw.trim() ? 'default' : 'pointer',
                display: 'flex', alignItems: 'center', gap: 7,
                padding: '8px 18px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                background: 'var(--accent)', color: '#fff',
                opacity: loading || !newPw.trim() ? 0.55 : 1, transition: 'opacity 0.15s',
              }}
            >
              {loading ? <Spinner size={12} /> : <Mail size={12} />}
              {t('send_code')}
            </button>
            <button
              onClick={reset}
              style={{
                all: 'unset', cursor: 'pointer',
                padding: '8px 14px', borderRadius: 8, fontSize: 12,
                border: '1px solid var(--border)', color: 'var(--dim)',
              }}
            >
              {t('cancel')}
            </button>
          </div>
        </div>
      )}

      {step === 'code' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px',
            background: 'rgba(34,197,94,0.07)', border: '1px solid rgba(34,197,94,0.2)',
            borderRadius: 8, fontSize: 12, color: '#22c55e',
          }}>
            <Mail size={13} />
            {t('code_sent_to')} <strong style={{ marginLeft: 4 }}>{me?.email}</strong>
            <span style={{ marginLeft: 4 }}>{t('code_expires')}</span>
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--dim)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', display: 'block', marginBottom: 8 }}>
              {t('six_digit_code')}
            </label>
            <input
              type="text"
              inputMode="numeric"
              maxLength={6}
              value={code}
              onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              placeholder="000000"
              autoFocus
              onKeyDown={e => e.key === 'Enter' && handleConfirm()}
              style={{
                width: 160, background: 'var(--surface-2)',
                border: `1px solid ${code.length === 6 ? 'var(--accent)' : 'var(--border)'}`,
                borderRadius: 8, padding: '10px 14px',
                fontSize: 22, fontWeight: 700, letterSpacing: 8,
                color: 'var(--text)', outline: 'none', fontFamily: 'monospace',
                textAlign: 'center', transition: 'border-color 0.15s',
              }}
            />
          </div>
          {error && <div style={{ fontSize: 12, color: 'var(--danger)' }}>{error}</div>}
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={handleConfirm}
              disabled={loading || code.length !== 6}
              style={{
                all: 'unset', cursor: loading || code.length !== 6 ? 'default' : 'pointer',
                display: 'flex', alignItems: 'center', gap: 7,
                padding: '8px 18px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                background: 'var(--accent)', color: '#fff',
                opacity: loading || code.length !== 6 ? 0.55 : 1, transition: 'opacity 0.15s',
              }}
            >
              {loading ? <Spinner size={12} /> : <CheckCircle2 size={12} />}
              {t('confirm_change')}
            </button>
            <button
              onClick={() => { setStep('form'); setCode(''); setError(null) }}
              style={{
                all: 'unset', cursor: 'pointer',
                padding: '8px 14px', borderRadius: 8, fontSize: 12,
                border: '1px solid var(--border)', color: 'var(--dim)',
              }}
            >
              {t('go_back')}
            </button>
          </div>
        </div>
      )}

      {step === 'done' && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '12px 16px',
          background: 'rgba(34,197,94,0.07)', border: '1px solid rgba(34,197,94,0.2)',
          borderRadius: 8, fontSize: 13, color: '#22c55e', fontWeight: 500,
        }}>
          <CheckCircle2 size={16} />
          {t('pw_updated')}
        </div>
      )}
    </Card>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ConfigPage() {
  const { t, lang, setLang }  = useLanguage()
  const { setTheme }          = useTheme()

  // Sync stored preferences from DB to context/localStorage on mount
  useEffect(() => {
    getPreferences()
      .then(prefs => {
        setTheme(prefs.theme)
        setLang(prefs.language)
      })
      .catch(() => {})
  }, [setTheme, setLang])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, animation: 'fadeIn 0.25s ease-out' }}>

      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 9,
          background: 'linear-gradient(135deg, #818cf8 0%, #6366f1 100%)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          flexShrink: 0,
        }}>
          <Sparkles size={16} color="#fff" strokeWidth={2} />
        </div>
        <div>
          <h1 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', letterSpacing: '-0.02em', margin: 0 }}>
            {t('configuration')}
          </h1>
          <p style={{ fontSize: 11, color: 'var(--dim)', margin: 0, marginTop: 1 }}>
            Faro — {t('user_profile')}, {t('app_settings')}, {t('available_models')}
          </p>
        </div>
      </div>

      {/* Sections */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <ProfileSection t={t} lang={lang} />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <AppConfigSection t={t} />
          <SecuritySection t={t} />
        </div>
      </div>

      <ModelsSection t={t} />

      <ActivitySection t={t} lang={lang} />
    </div>
  )
}
