'use client'
import { useState, useEffect, useCallback, useContext, createContext, useRef } from 'react'
import {
  User, Settings2, Cpu, Activity,
  Moon, Sun, Globe, CheckCircle2, Edit2, X,
  ChevronDown, Clock, Shield, Lock, Eye, EyeOff, Mail,
  MessageCircle, Unlink, CalendarClock, MessageSquare, Coins, Gauge,
} from 'lucide-react'
import { MobileList, MobileCard, MobileSection, useMobileHeader } from '@/components/mobile'
import MobileFormScope from '@/components/mobile/MobileFormScope'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import CurrencySection from '@/components/billing/CurrencySection'
import LimitsSection from '@/components/limits/LimitsSection'
import TimezoneSection from '@/components/billing/TimezoneSection'
import Spinner from '@/components/ui/Spinner'
import { useTheme } from '@/contexts/ThemeContext'
import BaseCard from '@/components/ui/Card'
import Input, { FieldLabel } from '@/components/ui/Input'
import { useLanguage } from '@/contexts/LanguageContext'
import { roleLabel, activityActionLabel } from '@/lib/enumLabels'
import { getUser, patchUser } from '@/lib/auth'
import { useErrorDetail } from '@/components/ui/States'
import {
  getMe, updateMe,
  getPreferences, updatePreferences,
  getActivityLogs, getActivityActionTypes,
  requestPasswordChange, confirmPasswordChange,
  linkWhatsappNumber, confirmWhatsappNumber,
  getPlanning, setPlanning,
  isApiError,
} from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import type { ActivityLog, PlanningState, PlanningPeriod } from '@/lib/types'

// ── helpers ───────────────────────────────────────────────────────────────────

function formatDate(iso: string, lang: 'es' | 'en') {
  return new Date(iso).toLocaleString(lang === 'es' ? 'es-CR' : 'en-US', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

// ── Phone drill-in ────────────────────────────────────────────────────────────
//
// On a phone this screen is a grouped settings list (MobileSettings, bottom of
// the file); tapping a row opens ONE section full-screen. The sections are the
// same components desktop renders, told by this context that they are drilled
// into: the header already names the section, so they drop their title row,
// and their controls grow to a 44px tap target.

const DrillIn = createContext(false)

/** Style for a control that must be a 44px tap target in a phone drill-in. */
function tap(drill: boolean): React.CSSProperties {
  return drill
    ? { minHeight: 44, minWidth: 44, boxSizing: 'border-box', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }
    : {}
}

// ── Section header ────────────────────────────────────────────────────────────

function SectionTitle({ icon: Icon, color, title, subtitle }: {
  icon: React.ElementType; color: string; title: string; subtitle: string
}) {
  const drill = useContext(DrillIn)
  if (drill) {
    return <p style={{ margin: '0 0 16px', fontSize: 13, color: 'var(--muted)', lineHeight: 1.5 }}>{subtitle}</p>
  }
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

// A local preset, not a fork: /config is a settings screen of large panels, so
// its cards are one step rounder and roomier than the list screens' default.
// The shape itself still comes from the shared primitive.
function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  const drill = useContext(DrillIn)
  return <BaseCard radius={14} padding={drill ? '16px' : '24px'} style={style}>{children}</BaseCard>
}

// /config labels its fields with a slightly larger, wider-tracked eyebrow than
// the FieldLabel default.
const EYEBROW_STYLE: React.CSSProperties = {
  fontSize: 11, fontWeight: 600, letterSpacing: '0.06em',
}

// One confirmation-code field, spelled once instead of twice: wide tracking and
// monospace figures so a six-digit code reads as six separate digits.
const OTP_STYLE: React.CSSProperties = {
  width: 160, padding: '10px 14px',
  fontSize: 22, fontWeight: 700, letterSpacing: 8,
  fontFamily: 'monospace', textAlign: 'center',
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
  // Held in state, not re-read from localStorage on every render: the saved
  // name has to be what the screen shows straight after saving it.
  const [me, setMe] = useState(() => getUser())
  const [editing,  setEditing]  = useState(false)
  const [name,     setName]     = useState(me?.full_name || '')
  const [saving,   setSaving]   = useState(false)
  const [feedback, setFeedback] = useState<'saved' | null>(null)
  const [error,    setError]    = useState<unknown>(null)
  // Renders `errors.<code>` in the user's language instead of the backend's
  // English sentence — the helper this screen already had and did not use.
  const errorDetail = useErrorDetail()
  const drill = useContext(DrillIn)

  async function handleSave() {
    if (!name.trim()) return
    setSaving(true)
    setError(null)
    try {
      await updateMe({ full_name: name.trim() })
      // `if (me) me.full_name = ...` used to mutate the object `getUser()` had
      // just parsed out of localStorage and thrown away, so the cache — which
      // the sidebar footer and the /compras greeting also read — kept the old
      // name until the next login, under a green "Guardado".
      setMe(patchUser({ full_name: name.trim() }))
      setFeedback('saved')
      setEditing(false)
      setTimeout(() => setFeedback(null), 2500)
    } catch (e: unknown) {
      // Was `try/finally` with no catch: a failed save rejected unhandled and
      // left the form open with nothing said.
      setError(e)
    } finally {
      setSaving(false)
    }
  }

  const initials = (me?.full_name || me?.email || 'U')
    .split(' ').map((w: string) => w[0]).slice(0, 2).join('').toUpperCase()

  return (
    <Card>
      {!drill && <SectionTitle icon={User} color="var(--accent)" title={t('user_profile')} subtitle={t('email')} />}
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start', flexDirection: drill ? 'column' : undefined }}>
        {/* Avatar */}
        <div style={{
          width: 64, height: 64, borderRadius: 16, flexShrink: 0,
          background: 'var(--accent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 22, fontWeight: 700, color: '#fff',
        }}>
          {initials}
        </div>

        {/* Fields */}
        <div data-tour="config.profile" style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 12, ...(drill ? { width: '100%', minWidth: 0 } : {}) }}>

          {/* Full name */}
          <div>
            <FieldLabel variant="eyebrow" style={{ ...EYEBROW_STYLE, marginBottom: 0 }}>
              {t('full_name')}
            </FieldLabel>
            {editing ? (
              <div style={{ display: 'flex', gap: 8, marginTop: 5, flexWrap: drill ? 'wrap' : undefined }}>
                <Input
                  value={name}
                  onChange={e => setName(e.target.value)}
                  autoFocus
                  autoComplete="name"
                  enterKeyHint="done"
                  onKeyDown={e => e.key === 'Enter' && handleSave()}
                  style={{ fontSize: 13, ...(drill ? { flex: '1 1 100%', width: '100%' } : {}) }}
                />
                <button
                  onClick={handleSave}
                  disabled={saving}
                  style={{
                    all: 'unset', cursor: 'pointer',
                    padding: '7px 14px', borderRadius: 7, fontSize: 12, fontWeight: 600,
                    background: 'var(--accent)', color: '#fff',
                    opacity: saving ? 0.6 : 1,
                    ...tap(drill), ...(drill ? { flex: 1, fontSize: 15, borderRadius: 12 } : {}),
                  }}
                >
                  {saving ? t('saving') : t('save_changes')}
                </button>
                <button
                  onClick={() => { setEditing(false); setName(me?.full_name || '') }}
                  aria-label={t('cancel')}
                  style={{
                    all: 'unset', cursor: 'pointer', padding: '7px 10px',
                    borderRadius: 7, color: 'var(--dim)',
                    border: '1px solid var(--border)',
                    ...tap(drill), ...(drill ? { borderRadius: 12 } : {}),
                  }}
                >
                  <X size={13} aria-hidden="true" />
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
                {error != null && (
                  <span style={{ fontSize: 11, color: 'var(--danger)' }}>
                    {errorDetail(error)}
                  </span>
                )}
                <button
                  onClick={() => setEditing(true)}
                  title={t('edit')}
                  aria-label={t('edit')}
                  style={{
                    all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5,
                    color: 'var(--dim)', display: 'flex', alignItems: 'center',
                    ...tap(drill), ...(drill ? { marginLeft: 'auto' } : {}),
                  }}
                >
                  <Edit2 size={drill ? 16 : 12} aria-hidden="true" />
                </button>
              </div>
            )}
          </div>

          {/* Email (read-only) */}
          <div>
            <FieldLabel variant="eyebrow" style={{ ...EYEBROW_STYLE, marginBottom: 0 }}>
              {t('email')}
            </FieldLabel>
            <div style={{ fontSize: 13, color: 'var(--muted)', marginTop: 4 }}>{me?.email}</div>
          </div>

          {/* Role + Status */}
          <div style={{ display: 'flex', gap: 16 }}>
            <div>
              <FieldLabel variant="eyebrow" style={{ ...EYEBROW_STYLE, marginBottom: 0 }}>
                {t('role')}
              </FieldLabel>
              <div style={{
                marginTop: 5, display: 'inline-block',
                padding: '3px 10px', borderRadius: 20, fontSize: 11, fontWeight: 600,
                background: 'color-mix(in srgb, var(--accent) 12%, transparent)', color: 'var(--accent)',
              }}>
                {me?.role ? roleLabel(t, me.role) : '—'}
              </div>
            </div>
            <div>
              <FieldLabel variant="eyebrow" style={{ ...EYEBROW_STYLE, marginBottom: 0 }}>
                {t('account_status')}
              </FieldLabel>
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
  const drill = useContext(DrillIn)

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

      <div data-tour="config.appearance" style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>

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
                  ...tap(drill),
                }}
                aria-pressed={lang === l}
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
              background: 'color-mix(in srgb, var(--accent) 10%, transparent)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              {theme === 'dark' ? <Moon size={14} color="var(--accent)" /> : <Sun size={14} color="var(--accent)" />}
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
                  ...tap(drill),
                }}
                aria-pressed={theme === th}
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

// ── Section: Planning grain ───────────────────────────────────────────────────
//
// This used to be a bare "Ver por" dropdown in the top bar, which read as a
// personal view toggle. It is nothing of the sort: it decides which trained
// sibling feeds the purchasing panel, inventory AND the daily alert emails; it
// is stored per tenant; and only an admin can change it. It belongs with the
// other account settings, stating what the app chose and why.

function PlanningSection({ t }: { t: (k: string, p?: Record<string, unknown>) => string }) {
  const { addToast } = useToast()
  const [state, setState] = useState<PlanningState | null>(null)
  const [busy, setBusy]   = useState(false)
  const isAdmin = getUser()?.role === 'admin'
  const drill = useContext(DrillIn)

  useEffect(() => {
    getPlanning().then(setState).catch(() => setState(null))
  }, [])

  // Nothing to say when the data affords a single grain: there is no decision.
  if (!state || state.available_periods.length <= 1) return null

  async function apply(period: PlanningPeriod) {
    if (!state) return
    setBusy(true)
    try {
      setState(await setPlanning(period, Math.max(1, Math.min(state.horizon, state.max_horizon))))
      addToast(t('planning.saved'), '', 'success')
    } catch (e) {
      addToast(t('planning.save_error'), isApiError(e) ? e.detail : '', 'error')
    } finally {
      setBusy(false)
    }
  }

  const grain = t(`planning.${state.period}`)

  return (
    <Card>
      <SectionTitle
        icon={CalendarClock} color="var(--accent)"
        title={t('planning.section_title')}
        subtitle={t('planning.section_subtitle')}
      />

      <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.6 }}>
        {t(`planning.reason.${state.period_reason}`, {
          grain,
          requested: state.requested_period ? t(`planning.${state.requested_period}`) : '',
        })}
      </div>

      <div style={{
        fontSize: 12, color: 'var(--dim)', lineHeight: 1.55,
        marginTop: 8, paddingLeft: 10, borderLeft: '2px solid var(--border)',
      }}>
        {t('planning.scope_warning')}
      </div>

      <div style={{ display: 'flex', gap: 6, marginTop: 14, flexWrap: 'wrap' }}>
        {state.available_periods.map(p => (
          <button
            key={p}
            onClick={() => apply(p)}
            disabled={!isAdmin || busy || p === state.period}
            style={{
              all: 'unset',
              cursor: !isAdmin || busy || p === state.period ? 'default' : 'pointer',
              padding: '6px 14px', borderRadius: 7, fontSize: 12, fontWeight: 600,
              border: `1px solid ${p === state.period ? 'var(--accent)' : 'var(--border)'}`,
              background: p === state.period ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : 'transparent',
              color: p === state.period ? 'var(--accent)' : 'var(--muted)',
              opacity: !isAdmin && p !== state.period ? 0.45 : 1,
              ...tap(drill),
            }}
          >
            {t(`planning.${p}`)}
          </button>
        ))}
      </div>

      {!isAdmin && (
        <div style={{ fontSize: 11.5, color: 'var(--dim)', marginTop: 10 }}>
          {t('planning.admin_only')}
        </div>
      )}
    </Card>
  )
}


// ── Section 3: How StockAI calculates ────────────────────────────────────────────
//
// This used to list every algorithm by its raw id (lightgbm, xgboost, prophet,
// lstm...) tagged "MACHINE LEARNING" / "DEEP LEARNING" / "beta" — an analyst's
// instrument panel on a screen a distributor opens. It undid the `Modelo N`
// abstraction that lib/modelLabel.ts exists to provide everywhere else. There
// is nothing to configure here, so one sentence replaces the catalogue.

function ModelsSection({ t }: { t: (k: string) => string }) {
  return (
    <Card>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Cpu size={16} color="var(--dim)" strokeWidth={1.8} style={{ flexShrink: 0 }} />
        <span style={{ fontSize: 12.5, color: 'var(--dim)', lineHeight: 1.5 }}>
          {t('config.how_stockai_calculates')}
        </span>
      </div>
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
  const drill = useContext(DrillIn)

  // Phone: a native picker for the filter and one row per entry. The desktop
  // grid has three fixed columns (140 + 80 + 150px) and ran 240px off a phone.
  if (drill) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ fontSize: 13, color: 'var(--muted)', padding: '0 4px', visibility: loading ? 'hidden' : undefined }}>
          {`${total} ${t('records_count')}`}
        </div>
        <select
          name="activity_action"
          aria-label={t('all_actions')}
          value={actionFilter}
          onChange={e => setActionFilter(e.target.value)}
          style={{
            width: '100%', padding: '0 12px', borderRadius: 12,
            border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)',
          }}
        >
          {['', ...actionTypes].map(a => (
            <option key={a || '__all__'} value={a}>{a ? activityActionLabel(t, a) : t('all_actions')}</option>
          ))}
        </select>
        {actionTypesErr && (
          <div style={{ fontSize: 12, color: 'var(--dim)' }}>{t('config.action_types_load_error')}</div>
        )}
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 32 }}><Spinner size={20} /></div>
        ) : logs.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '32px 0', color: 'var(--dim)', fontSize: 14 }}>
            <Clock size={28} style={{ marginBottom: 8, opacity: 0.4 }} aria-hidden="true" />
            <div>{t('no_activity')}</div>
          </div>
        ) : (
          <>
            <MobileList ariaLabel={t('activity_logs')}>
              {logs.map(log => (
                <MobileCard
                  key={log.id}
                  title={activityActionLabel(t, log.action)}
                  subtitle={`${log.resource || '—'} · ${formatDate(log.created_at, lang)}`}
                  status={{
                    label: log.status === 'success' ? t('success') : t('error'),
                    tone: log.status === 'success' ? 'success' : 'danger',
                  }}
                />
              ))}
            </MobileList>
            {hasMore && (
              <button type="button" className="mobile-btn mobile-btn-secondary" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? <Spinner size={14} /> : null}
                {t('load_more')} ({total - logs.length} {t('config.remaining')})
              </button>
            )}
          </>
        )}
      </div>
    )
  }

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
              {actionFilter ? activityActionLabel(t, actionFilter) : t('all_actions')}
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
                  {a ? activityActionLabel(t, a) : t('all_actions')}
                </button>
              ))}
            </div>
          )}
          {actionTypesErr && (
            <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 4, textAlign: 'right' }}>
              {t('config.action_types_load_error')}
            </div>
          )}
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 32 }}><Spinner size={20} /></div>
      ) : logs.length === 0 ? (
        <div data-tour="config.activity" style={{ textAlign: 'center', padding: '32px 0', color: 'var(--dim)', fontSize: 13 }}>
          <Clock size={28} style={{ marginBottom: 8, opacity: 0.4 }} />
          <div>{t('no_activity')}</div>
        </div>
      ) : (
        <div data-tour="config.activity" style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
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
                color: 'var(--text)', fontSize: 11,
                background: 'var(--surface-2)', padding: '2px 8px',
                borderRadius: 5, display: 'inline-block',
              }}>
                {activityActionLabel(t, log.action)}
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
                {t('load_more')} ({total - logs.length} {t('config.remaining')})
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
  // `e.message` on an ApiError is the BACKEND's English sentence (or the
  // literal "HTTP 0" offline), shown under a Spanish form. This resolves
  // `error_code` + `params` against the catalogue first — the WhatsApp section
  // right below already maps its failures to catalogue copy; this one did not.
  const errorDetail = useErrorDetail()
  const drill = useContext(DrillIn)

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
      setError(errorDetail(e) || t('pw_error_send'))
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
      setError(errorDetail(e) || t('pw_error_confirm'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card>
      <SectionTitle icon={Lock} color="#f59e0b" title={t('security')} subtitle={t('change_password')} />

      {step === 'idle' && (
        <div data-tour="config.security" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
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
              ...tap(drill),
            }}
            onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--accent)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--accent)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--border)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--muted)' }}
          >
            {t('change_pw_btn')}
          </button>
        </div>
      )}

      {step === 'form' && (
        <div data-tour="config.security" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ fontSize: 12, color: 'var(--dim)' }}>{t('pw_form_desc')}</div>
          <div style={{ position: 'relative' }}>
            <Input
              size="lg"
              invalid={Boolean(error)}
              type={showPw ? 'text' : 'password'}
              value={newPw}
              onChange={e => { setNewPw(e.target.value); if (error) setError(null) }}
              placeholder={t('pw_placeholder')}
              autoFocus
              onKeyDown={e => e.key === 'Enter' && handleRequestCode()}
              /* Extra right padding clears the reveal button sitting on top. */
              style={{ paddingRight: 40 }}
            />
            <button
              onClick={() => setShowPw(v => !v)}
              aria-label={showPw ? t('auth.hide_password') : t('auth.show_password')}
              style={{
                all: 'unset', position: 'absolute', right: 10, top: '50%',
                transform: 'translateY(-50%)', cursor: 'pointer', color: 'var(--dim)',
                display: 'flex',
                ...tap(drill),
              }}
            >
              {showPw ? <EyeOff size={14} aria-hidden="true" /> : <Eye size={14} aria-hidden="true" />}
            </button>
          </div>
          {error && <div style={{ fontSize: 12, color: 'var(--danger)' }}>{error}</div>}
          <div style={{ display: 'flex', gap: 8, flexWrap: drill ? 'wrap' : undefined }}>
            <button
              onClick={handleRequestCode}
              disabled={loading || !newPw.trim()}
              style={{
                all: 'unset', cursor: loading || !newPw.trim() ? 'default' : 'pointer',
                display: 'flex', alignItems: 'center', gap: 7,
                padding: '8px 18px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                background: 'var(--accent)', color: '#fff',
                opacity: loading || !newPw.trim() ? 0.55 : 1, transition: 'opacity 0.15s',
                ...tap(drill),
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
                ...tap(drill),
              }}
            >
              {t('cancel')}
            </button>
          </div>
        </div>
      )}

      {step === 'code' && (
        <div data-tour="config.security" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
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
            <FieldLabel variant="eyebrow" style={{ ...EYEBROW_STYLE, marginBottom: 8 }}>
              {t('six_digit_code')}
            </FieldLabel>
            <Input
              type="text"
              inputMode="numeric"
              maxLength={6}
              value={code}
              onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              placeholder="000000"
              autoFocus
              onKeyDown={e => e.key === 'Enter' && handleConfirm()}
              size="lg"
              style={{ ...OTP_STYLE, borderColor: code.length === 6 ? 'var(--accent)' : 'var(--border)' }}
            />
          </div>
          {error && <div style={{ fontSize: 12, color: 'var(--danger)' }}>{error}</div>}
          <div style={{ display: 'flex', gap: 8, flexWrap: drill ? 'wrap' : undefined }}>
            <button
              onClick={handleConfirm}
              disabled={loading || code.length !== 6}
              style={{
                all: 'unset', cursor: loading || code.length !== 6 ? 'default' : 'pointer',
                display: 'flex', alignItems: 'center', gap: 7,
                padding: '8px 18px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                background: 'var(--accent)', color: '#fff',
                opacity: loading || code.length !== 6 ? 0.55 : 1, transition: 'opacity 0.15s',
                ...tap(drill),
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
                ...tap(drill),
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

// ── Section: Link WhatsApp number ────────────────────────────────────────────

type WaStep = 'loading' | 'form' | 'code' | 'verified'

const isE164 = (s: string) => /^\+[1-9]\d{7,14}$/.test(s.trim())
const IS_DEV = process.env.NODE_ENV !== 'production'

function WhatsAppSection({ t }: { t: (k: string) => string }) {
  const [step,           setStep]           = useState<WaStep>('loading')
  const [number,         setNumber]         = useState('')
  const [pendingNumber,  setPendingNumber]  = useState('')
  const [verifiedNumber, setVerifiedNumber] = useState('')
  const [code,           setCode]           = useState('')
  const [debugCode,      setDebugCode]      = useState<string | null>(null)
  const [loading,        setLoading]        = useState(false)
  const [unlinking,      setUnlinking]      = useState(false)
  const [error,          setError]          = useState<string | null>(null)
  // Seconds until the resend button re-enables (mirrors the backend cooldown).
  const [resendIn,       setResendIn]       = useState(0)
  const drill = useContext(DrillIn)

  useEffect(() => {
    if (resendIn <= 0) return
    const id = setTimeout(() => setResendIn(s => Math.max(0, s - 1)), 1000)
    return () => clearTimeout(id)
  }, [resendIn])

  useEffect(() => {
    getMe()
      .then(u => {
        if (u.whatsapp_verified_at) {
          setVerifiedNumber(u.whatsapp_number || '')
          setStep('verified')
        } else {
          setNumber(u.whatsapp_number || '')
          setStep('form')
        }
      })
      .catch(() => setStep('form'))
  }, [])

  async function handleSendCode() {
    if (!isE164(number)) { setError(t('config.wa_hint')); return }
    setLoading(true); setError(null)
    try {
      const res = await linkWhatsappNumber(number.trim())
      setPendingNumber(number.trim())
      setDebugCode(res.debug_code ?? null)
      setCode('')
      setResendIn(60)
      setStep('code')
    } catch (e: unknown) {
      if (isApiError(e) && e.status === 429) {
        const ra = Number((e.params as { retry_after?: unknown }).retry_after) || 60
        setResendIn(ra)
        setError(t('config.wa_err_cooldown').replace('{s}', String(ra)))
      }
      else if (isApiError(e) && e.status === 409) setError(t('config.wa_err_taken'))
      else if (isApiError(e) && e.status === 503) setError(t('config.wa_err_unavailable'))
      else setError(t('config.wa_err_send'))
    } finally {
      setLoading(false)
    }
  }

  async function handleConfirm() {
    if (code.length !== 6) return
    setLoading(true); setError(null)
    try {
      await confirmWhatsappNumber(code)
      setVerifiedNumber(pendingNumber)
      setDebugCode(null)
      setStep('verified')
    } catch {
      setError(t('config.wa_err_confirm'))
    } finally {
      setLoading(false)
    }
  }

  async function handleUnlink() {
    setUnlinking(true); setError(null)
    try {
      await updateMe({ whatsapp_number: '' })
      setVerifiedNumber(''); setNumber(''); setCode(''); setPendingNumber('')
      setStep('form')
    } catch {
      setError(t('config.wa_err_unlink'))
    } finally {
      setUnlinking(false)
    }
  }

  function handleChangeNumber() {
    setNumber(verifiedNumber)
    setCode(''); setError(null); setStep('form')
  }

  return (
    <Card>
      <SectionTitle icon={MessageCircle} color="#22c55e" title={t('config.wa_title')} subtitle={t('config.wa_subtitle')} />

      {step === 'loading' && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 20 }}><Spinner size={18} /></div>
      )}

      {step === 'form' && (
        <div data-tour="config.whatsapp" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ fontSize: 12, color: 'var(--dim)' }}>{t('config.wa_intro')}</div>
          <div>
            <FieldLabel htmlFor="wa-number" variant="eyebrow" style={{ ...EYEBROW_STYLE, marginBottom: 6 }}>
              {t('config.wa_number_label')}
            </FieldLabel>
            <Input
              id="wa-number"
              name="whatsapp_number"
              type="tel"
              inputMode="tel"
              placeholder="+50688888888"
              value={number}
              onChange={e => { setNumber(e.target.value); if (error) setError(null) }}
              autoFocus
              onKeyDown={e => e.key === 'Enter' && handleSendCode()}
              style={{ fontSize: 13, maxWidth: 240 }}
            />
            <p style={{ fontSize: 11, color: 'var(--dim)', margin: '6px 0 0' }}>{t('config.wa_hint')}</p>
          </div>
          {error && <div style={{ fontSize: 12, color: 'var(--danger)' }}>{error}</div>}
          <div>
            <button
              onClick={handleSendCode}
              disabled={loading || !number.trim()}
              style={{
                all: 'unset', cursor: loading || !number.trim() ? 'default' : 'pointer',
                display: 'inline-flex', alignItems: 'center', gap: 7,
                padding: '8px 18px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                background: 'var(--accent)', color: '#fff',
                opacity: loading || !number.trim() ? 0.55 : 1, transition: 'opacity 0.15s',
                ...tap(drill),
              }}
            >
              {loading ? <Spinner size={12} /> : <MessageCircle size={12} />}
              {t('config.wa_send_code')}
            </button>
          </div>
        </div>
      )}

      {step === 'code' && (
        <div data-tour="config.whatsapp" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{
            display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 6, padding: '10px 14px',
            background: 'rgba(34,197,94,0.07)', border: '1px solid rgba(34,197,94,0.2)',
            borderRadius: 8, fontSize: 12, color: '#22c55e',
          }}>
            <MessageCircle size={13} />
            {t('config.wa_code_sent_to')} <strong>{pendingNumber}</strong>
          </div>
          {IS_DEV && debugCode && (
            <div style={{ fontSize: 11, color: 'var(--dim)' }}>
              {t('config.wa_dev_code')} <code style={{ fontFamily: 'monospace', color: 'var(--muted)' }}>{debugCode}</code>
            </div>
          )}
          <div>
            <FieldLabel htmlFor="wa-code" variant="eyebrow" style={{ ...EYEBROW_STYLE, marginBottom: 8 }}>
              {t('config.wa_code_label')}
            </FieldLabel>
            <Input
              id="wa-code"
              name="whatsapp_code"
              type="text"
              inputMode="numeric"
              maxLength={6}
              value={code}
              onChange={e => { setCode(e.target.value.replace(/\D/g, '').slice(0, 6)); if (error) setError(null) }}
              placeholder="000000"
              autoFocus
              onKeyDown={e => e.key === 'Enter' && handleConfirm()}
              size="lg"
              style={{ ...OTP_STYLE, borderColor: code.length === 6 ? 'var(--accent)' : 'var(--border)' }}
            />
          </div>
          {error && <div style={{ fontSize: 12, color: 'var(--danger)' }}>{error}</div>}
          <div style={{ display: 'flex', gap: 8, flexWrap: drill ? 'wrap' : undefined }}>
            <button
              onClick={handleConfirm}
              disabled={loading || code.length !== 6}
              style={{
                all: 'unset', cursor: loading || code.length !== 6 ? 'default' : 'pointer',
                display: 'flex', alignItems: 'center', gap: 7,
                padding: '8px 18px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                background: 'var(--accent)', color: '#fff',
                opacity: loading || code.length !== 6 ? 0.55 : 1, transition: 'opacity 0.15s',
                ...tap(drill),
              }}
            >
              {loading ? <Spinner size={12} /> : <CheckCircle2 size={12} />}
              {t('config.wa_confirm')}
            </button>
            <button
              onClick={handleSendCode}
              disabled={loading || resendIn > 0}
              style={{
                all: 'unset', cursor: loading || resendIn > 0 ? 'default' : 'pointer',
                padding: '8px 14px', borderRadius: 8, fontSize: 12,
                border: '1px solid var(--border)',
                color: resendIn > 0 ? 'var(--dim)' : 'var(--muted)',
                opacity: loading || resendIn > 0 ? 0.6 : 1, transition: 'opacity 0.15s',
                ...tap(drill),
              }}
            >
              {resendIn > 0
                ? t('config.wa_resend_in').replace('{s}', String(resendIn))
                : t('config.wa_resend')}
            </button>
            <button
              onClick={() => { setStep('form'); setCode(''); setError(null) }}
              style={{
                all: 'unset', cursor: 'pointer',
                padding: '8px 14px', borderRadius: 8, fontSize: 12,
                border: '1px solid var(--border)', color: 'var(--dim)',
                ...tap(drill),
              }}
            >
              {t('go_back')}
            </button>
          </div>
        </div>
      )}

      {step === 'verified' && (
        <div data-tour="config.whatsapp" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '12px 16px',
            background: 'rgba(34,197,94,0.07)', border: '1px solid rgba(34,197,94,0.2)',
            borderRadius: 8,
          }}>
            <CheckCircle2 size={16} color="#22c55e" />
            <div>
              <div style={{ fontSize: 13, color: '#22c55e', fontWeight: 600 }}>
                {t('config.wa_verified_title')}
              </div>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>
                {t('config.wa_linked_number')}: <strong style={{ fontFamily: 'monospace', color: 'var(--text)' }}>{verifiedNumber}</strong>
              </div>
            </div>
            <span style={{
              marginLeft: 'auto', fontSize: 10, fontWeight: 700,
              padding: '3px 9px', borderRadius: 20, textTransform: 'uppercase', letterSpacing: '0.05em',
              background: 'rgba(34,197,94,0.14)', color: '#22c55e',
            }}>
              {t('config.wa_verified_badge')}
            </span>
          </div>
          {error && <div style={{ fontSize: 12, color: 'var(--danger)' }}>{error}</div>}
          <div style={{ display: 'flex', gap: 8, flexWrap: drill ? 'wrap' : undefined }}>
            <button
              onClick={handleChangeNumber}
              style={{
                all: 'unset', cursor: 'pointer',
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '7px 16px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                border: '1px solid var(--border)', color: 'var(--muted)', background: 'var(--surface-2)',
                ...tap(drill),
              }}
            >
              <Edit2 size={12} /> {t('config.wa_change')}
            </button>
            <button
              onClick={handleUnlink}
              disabled={unlinking}
              style={{
                all: 'unset', cursor: unlinking ? 'default' : 'pointer',
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '7px 16px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                border: '1px solid var(--border)', color: 'var(--danger)',
                opacity: unlinking ? 0.6 : 1,
                ...tap(drill),
              }}
            >
              {unlinking ? <Spinner size={12} /> : <Unlink size={12} />}
              {unlinking ? t('config.wa_unlinking') : t('config.wa_unlink')}
            </button>
          </div>
        </div>
      )}
    </Card>
  )
}

// ── Section: SMS heads-up for team messages ──────────────────────────────────
//
// Companion to /mensajes: when someone writes to you and you are away, StockAI
// sends one short SMS to the number linked above. It used to be hidden for
// plans without team_messaging; every tenant has the screen now.

function DmSmsSection({ t }: { t: (k: string) => string }) {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [hasNumber, setHasNumber] = useState(false)
  const [saving, setSaving] = useState(false)
  const drill = useContext(DrillIn)

  useEffect(() => {
    getPreferences().then(p => setEnabled(p.dm_sms_enabled)).catch(() => setEnabled(false))
    getMe().then(u => setHasNumber(!!u.whatsapp_number)).catch(() => {})
  }, [])

  const on = enabled === true
  const blocked = !hasNumber

  async function handleToggle() {
    if (enabled === null || saving || blocked) return
    const next = !on
    setEnabled(next)
    setSaving(true)
    try {
      await updatePreferences({ dm_sms_enabled: next })
    } catch {
      setEnabled(!next)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <SectionTitle
        icon={MessageSquare} color="var(--accent)"
        title={t('config.dm_sms_title')} subtitle={t('config.dm_sms_subtitle')}
      />
      <div data-tour="config.dm_sms" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)' }}>
            {t('config.dm_sms_toggle_label')}
          </div>
          <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>
            {blocked ? t('config.dm_sms_needs_number') : t('config.dm_sms_hint')}
          </div>
        </div>
        {drill ? (
          // Phone: an iOS-sized switch inside a 44px-tall hit area.
          <button
            role="switch"
            aria-checked={on}
            aria-label={t('config.dm_sms_toggle_label')}
            onClick={handleToggle}
            disabled={enabled === null || saving || blocked}
            style={{
              all: 'unset', boxSizing: 'border-box', position: 'relative', flexShrink: 0,
              width: 52, height: 44,
              cursor: enabled === null || saving || blocked ? 'default' : 'pointer',
              opacity: blocked ? 0.5 : 1,
            }}
          >
            <span aria-hidden="true" style={{
              position: 'absolute', left: 0, top: 6, width: 52, height: 32, borderRadius: 16,
              background: on ? 'var(--accent)' : 'var(--border-strong)',
              transition: 'background var(--dur-2) var(--ease-out)',
            }} />
            <span aria-hidden="true" style={{
              position: 'absolute', top: 8, left: 2, width: 28, height: 28, borderRadius: '50%',
              background: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
              transform: on ? 'translateX(20px)' : 'none',
              transition: 'transform var(--dur-2) var(--ease-out)',
            }} />
          </button>
        ) : (
        <button
          role="switch"
          aria-checked={on}
          aria-label={t('config.dm_sms_toggle_label')}
          onClick={handleToggle}
          disabled={enabled === null || saving || blocked}
          style={{
            all: 'unset', boxSizing: 'border-box',
            cursor: enabled === null || saving || blocked ? 'default' : 'pointer',
            width: 38, height: 22, borderRadius: 12, flexShrink: 0,
            background: on ? 'var(--accent)' : 'var(--border-strong)',
            position: 'relative', transition: 'background 0.15s',
            opacity: blocked ? 0.5 : 1,
          }}
        >
          <span style={{
            position: 'absolute', top: 3, left: on ? 19 : 3,
            width: 16, height: 16, borderRadius: '50%',
            background: '#fff', transition: 'left 0.15s',
          }} />
        </button>
        )}
      </div>
    </Card>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ConfigPage() {
  const { t, lang }           = useLanguage()
  const { theme }             = useTheme()

  // The device's current choice wins; the account copy follows it.
  //
  // This used to do the opposite — pull the account's theme and language down
  // on mount and apply them. Anything chosen where it could not be saved (the
  // theme/language toggles on the signed-out login screen) or not saved YET
  // (a toggle's PUT still in flight when this GET answered) was then reverted
  // the moment the user opened Mi cuenta: the theme flipped on navigation.
  // Pushing the local value up instead keeps the account in step without ever
  // changing what the user is looking at.
  useEffect(() => {
    getPreferences()
      .then(prefs => {
        const patch: { theme?: 'dark' | 'light'; language?: 'es' | 'en' } = {}
        if (prefs.theme !== theme) patch.theme = theme
        if (prefs.language !== lang) patch.language = lang
        if (patch.theme || patch.language) return updatePreferences(patch)
      })
      .catch(() => {})
    // Once per visit: the toggles persist their own changes after this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const narrow = useIsNarrow()
  if (narrow) return <MobileSettings />

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 9,
          background: 'var(--accent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          flexShrink: 0,
        }}>
          <Settings2 size={16} color="#fff" strokeWidth={2} />
        </div>
        <div>
          <h1 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', letterSpacing: '-0.02em', margin: 0 }}>
            {t('configuration')}
          </h1>
        </div>
      </div>

      {/* Sections */}
      {/* Two balanced columns, grouped like the phone's settings list: the
          person on the left, the company on the right. The left column used to
          hold the profile card alone while nine cards stacked on the right,
          leaving most of the screen empty. */}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 16, alignItems: 'start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
          <ProfileSection t={t} lang={lang} />
          <AppConfigSection t={t} />
          <SecuritySection t={t} />
          <WhatsAppSection t={t} />
          <DmSmsSection t={t} />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
          {/* How much room is left. First in this column on purpose: a
              ceiling nobody can see is a trap, and this is the screen where
              somebody goes looking before they go looking for us. */}
          <Card>
            <SectionTitle
              icon={Gauge} color="var(--accent)"
              title={t('limits.section.title')}
              subtitle={t('limits.section.header_subtitle')}
            />
            <LimitsSection />
          </Card>
          <PlanningSection t={t} />
          {/* What the customer's own figures are worth — their choice, and
              nothing to do with what StockAI costs, which is a conversation with
              us and not a setting on this screen. */}
          <Card>
            <SectionTitle
              icon={Coins} color="var(--accent)"
              title={t('currency.section_title')}
              subtitle={t('currency.section_subtitle')}
            />
            <CurrencySection />
          </Card>
          {/* Its own card, next to currency: both are "how this company's data is
              expressed", and the scheduled-retrain hours are meaningless without
              a zone attached. */}
          <Card>
            <SectionTitle
              icon={Clock} color="var(--accent)"
              title={t('timezone.section_title')}
              subtitle={t('timezone.section_subtitle')}
            />
            <TimezoneSection />
          </Card>
        </div>
      </div>

      <ModelsSection t={t} />

      <ActivitySection t={t} lang={lang} />
    </div>
  )
}

// ── Phone: grouped settings list with drill-in sections ───────────────────────
//
// The desktop page is a two-column grid of ten cards; at 360px it measured
// 601px wide. A phone gets what a phone's own Settings app looks like: one
// list, grouped, each row naming its current value, and a tap opening that one
// section full-screen with a back button. The open section lives in `?s=` so
// the system back gesture closes it instead of leaving the screen.

type DrillKey =
  | 'profile' | 'security' | 'whatsapp' | 'sms'
  | 'limits' | 'currency' | 'timezone' | 'planning'
  | 'appearance' | 'activity'

const DRILL_KEYS: DrillKey[] = [
  'profile', 'security', 'whatsapp', 'sms',
  'limits', 'currency', 'timezone', 'planning',
  'appearance', 'activity',
]

function Tile({ Icon, color }: { Icon: React.ElementType; color: string }) {
  return (
    <span aria-hidden="true" style={{
      width: 32, height: 32, borderRadius: 9, flexShrink: 0,
      background: `color-mix(in srgb, ${color} 16%, transparent)`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <Icon size={16} color={color} strokeWidth={2} />
    </span>
  )
}

function MobileSettings() {
  const { t, lang } = useLanguage()
  const { theme } = useTheme()
  const [key, setKey] = useState<DrillKey | null>(null)
  // Whether WE pushed the history entry for the open section. Opened from a
  // shared `?s=` link there is nothing of ours to pop, and history.back()
  // would leave the app.
  const pushed = useRef(false)
  const [waNumber, setWaNumber] = useState<string | null>(null)
  const [planning, setPlanningState] = useState<PlanningState | null>(null)
  // Re-read on every return to the list: the profile section may have just
  // renamed the person.
  const [me, setMe] = useState(() => getUser())

  useEffect(() => {
    const read = () => {
      const s = new URLSearchParams(window.location.search).get('s') as DrillKey | null
      setKey(s && DRILL_KEYS.includes(s) ? s : null)
      if (!s) pushed.current = false
    }
    read()
    window.addEventListener('popstate', read)
    return () => window.removeEventListener('popstate', read)
  }, [])

  useEffect(() => {
    if (key !== null) return
    setMe(getUser())
    getMe()
      .then(u => setWaNumber(u.whatsapp_verified_at ? (u.whatsapp_number || '') : ''))
      .catch(() => setWaNumber(null))
    getPlanning().then(setPlanningState).catch(() => setPlanningState(null))
  }, [key])

  function open(k: DrillKey) {
    window.history.pushState(null, '', `?s=${k}`)
    pushed.current = true
    setKey(k)
    document.querySelector('.page-content')?.scrollTo({ top: 0 })
  }

  const back = useCallback(() => {
    if (pushed.current) {
      window.history.back()
    } else {
      window.history.replaceState(null, '', window.location.pathname)
      setKey(null)
    }
  }, [])

  const titles: Record<DrillKey, string> = {
    profile: t('user_profile'),
    security: t('security'),
    whatsapp: t('config.wa_title'),
    sms: t('config.dm_sms_title'),
    limits: t('limits.section.title'),
    currency: t('currency.section_title'),
    timezone: t('timezone.section_title'),
    planning: t('planning.section_title'),
    appearance: t('app_settings'),
    activity: t('activity_logs'),
  }

  useMobileHeader(key ? { title: titles[key], onBack: back } : null)

  if (key) {
    let body: React.ReactNode
    switch (key) {
      case 'profile':    body = <ProfileSection t={t} lang={lang} />; break
      case 'security':   body = <SecuritySection t={t} />; break
      case 'whatsapp':   body = <WhatsAppSection t={t} />; break
      case 'sms':        body = <DmSmsSection t={t} />; break
      case 'appearance': body = <AppConfigSection t={t} />; break
      case 'planning':   body = <PlanningSection t={t} />; break
      case 'activity':   body = <ActivitySection t={t} lang={lang} />; break
      case 'limits':
        body = (
          <Card>
            <SectionTitle icon={Gauge} color="var(--accent)" title={t('limits.section.title')} subtitle={t('limits.section.header_subtitle')} />
            <div className="m-tap44"><LimitsSection /></div>
          </Card>
        )
        break
      case 'currency':
        body = (
          <Card>
            <SectionTitle icon={Coins} color="var(--accent)" title={t('currency.section_title')} subtitle={t('currency.section_subtitle')} />
            <CurrencySection />
          </Card>
        )
        break
      case 'timezone':
        body = (
          <Card>
            <SectionTitle icon={Clock} color="var(--accent)" title={t('timezone.section_title')} subtitle={t('timezone.section_subtitle')} />
            <TimezoneSection />
          </Card>
        )
        break
    }
    return (
      <DrillIn.Provider value={true}>
        <MobileFormScope>
          <div key={key} className="m-drill-enter">{body}</div>
        </MobileFormScope>
      </DrillIn.Provider>
    )
  }

  const initials = (me?.full_name || me?.email || 'U')
    .split(' ').map((w: string) => w[0]).slice(0, 2).join('').toUpperCase()
  const hasPlanningChoice = !!planning && planning.available_periods.length > 1

  return (
    <div className="m-drill-enter">
      <MobileSection>
        <MobileList ariaLabel={t('user_profile')}>
          <MobileCard
            leading={
              <span aria-hidden="true" style={{
                width: 52, height: 52, borderRadius: 14, background: 'var(--accent)', color: '#fff',
                display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 19, fontWeight: 700,
              }}>{initials}</span>
            }
            title={me?.full_name || me?.email || '—'}
            subtitle={`${me?.email ?? ''}${me?.role ? ` · ${roleLabel(t, me.role)}` : ''}`}
            onClick={() => open('profile')}
          />
        </MobileList>
      </MobileSection>

      <MobileSection title={t('config.m_group_account')}>
        <MobileList>
          <MobileCard leading={<Tile Icon={Lock} color="#f59e0b" />} title={t('security')}
                      subtitle={t('change_password')} onClick={() => open('security')} />
          <MobileCard leading={<Tile Icon={MessageCircle} color="#22c55e" />} title={t('config.wa_title')}
                      subtitle={waNumber ? waNumber : waNumber === '' ? t('config.m_wa_not_linked') : t('config.wa_subtitle')}
                      onClick={() => open('whatsapp')} />
          <MobileCard leading={<Tile Icon={MessageSquare} color="var(--accent)" />} title={t('config.dm_sms_title')}
                      subtitle={t('config.dm_sms_subtitle')} onClick={() => open('sms')} />
        </MobileList>
      </MobileSection>

      <MobileSection title={t('config.m_group_company')}>
        <MobileList>
          <MobileCard leading={<Tile Icon={Gauge} color="var(--accent)" />} title={t('limits.section.title')}
                      subtitle={t('limits.section.header_subtitle')} onClick={() => open('limits')} />
          <MobileCard leading={<Tile Icon={Coins} color="var(--accent)" />} title={t('currency.section_title')}
                      subtitle={t('currency.section_subtitle')} onClick={() => open('currency')} />
          <MobileCard leading={<Tile Icon={Clock} color="var(--accent)" />} title={t('timezone.section_title')}
                      subtitle={t('timezone.section_subtitle')} onClick={() => open('timezone')} />
          {hasPlanningChoice && (
            <MobileCard leading={<Tile Icon={CalendarClock} color="var(--accent)" />} title={t('planning.section_title')}
                        subtitle={t(`planning.${planning!.period}`)} onClick={() => open('planning')} />
          )}
        </MobileList>
      </MobileSection>

      <MobileSection title={t('config.m_group_app')}>
        <MobileList>
          <MobileCard leading={<Tile Icon={Settings2} color="#22c55e" />} title={t('app_settings')}
                      subtitle={`${lang === 'es' ? t('spanish') : t('english')} · ${theme === 'dark' ? t('dark') : t('light')}`}
                      onClick={() => open('appearance')} />
          <MobileCard leading={<Tile Icon={Activity} color="#0ea5e9" />} title={t('activity_logs')}
                      onClick={() => open('activity')} />
        </MobileList>
      </MobileSection>

      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '0 4px 8px' }}>
        <Cpu size={16} color="var(--dim)" strokeWidth={1.8} style={{ flexShrink: 0, marginTop: 2 }} aria-hidden="true" />
        <span style={{ fontSize: 13, color: 'var(--dim)', lineHeight: 1.5 }}>{t('config.how_stockai_calculates')}</span>
      </div>
    </div>
  )
}
