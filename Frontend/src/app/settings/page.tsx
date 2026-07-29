'use client'
import { useState, useEffect, useCallback } from 'react'
import {
  listApiKeys, createApiKey, revokeApiKey,
  listWebhooks, createWebhook, deleteWebhook,
  getSessions, getSchedule, saveSchedule, deleteSchedule,
} from '@/lib/api'
import type { ApiKey, Webhook, JobSchedule, SessionInfo } from '@/lib/types'
import Button from '@/components/ui/Button'
import Input, { Select } from '@/components/ui/Input'
import Card from '@/components/ui/Card'
import Spinner from '@/components/ui/Spinner'
import { Key, Webhook as WebhookIcon, Clock, Copy, Check, X, Plus, Trash2, AlertTriangle } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { webhookEventLabel } from '@/lib/enumLabels'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useToast } from '@/contexts/ToastContext'

type Tab = 'api-keys' | 'webhooks' | 'schedules'

const WEBHOOK_EVENTS = [
  { id: 'job.completed',     labelKey: 'settings.event_job_completed' },
  { id: 'job.failed',        labelKey: 'settings.event_job_failed' },
]

const CRON_OPTIONS = [
  { labelKey: 'settings.cron_mon_6am',        value: '0 6 * * 1' },
  { labelKey: 'settings.cron_daily_midnight', value: '0 0 * * *' },
  { labelKey: 'settings.cron_weekdays_6am',   value: '0 6 * * 1-5' },
  { labelKey: 'settings.cron_sun_8am',        value: '0 8 * * 0' },
  { labelKey: 'settings.cron_hourly',         value: '0 * * * *' },
  { labelKey: 'settings.cron_month_first',    value: '0 0 1 * *' },
]

// ── API Keys tab ──────────────────────────────────────────────────────────────
function ApiKeysTab() {
  const { t } = useLanguage()
  const confirm = useConfirm()
  const [keys,    setKeys]    = useState<ApiKey[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [newKey,  setNewKey]  = useState<string | null>(null)
  const [copied,  setCopied]  = useState(false)
  const [revoking, setRevoking] = useState<string | null>(null)

  const load = useCallback(() => {
    listApiKeys()
      .then(setKeys)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const handleCreate = async () => {
    if (!newName.trim()) return
    setCreating(true); setError(null)
    try {
      const result = await createApiKey(newName.trim())
      setNewKey(result.key)
      setNewName('')
      load()
    } catch (e: any) { setError(e.message) }
    finally { setCreating(false) }
  }

  const handleRevoke = async (id: string) => {
    if (!(await confirm({ title: t('settings.revoke_title'), message: t('settings.revoke_confirm'), danger: true }))) return
    setRevoking(id)
    try { await revokeApiKey(id); load() }
    catch (e: any) { setError(e.message) }
    finally { setRevoking(null) }
  }

  const copyKey = () => {
    if (newKey) { navigator.clipboard.writeText(newKey); setCopied(true); setTimeout(() => setCopied(false), 2000) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div role="status" style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '12px 16px', borderRadius: 8, background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)', fontSize: 12, color: 'var(--text)' }}>
        <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1, color: '#f59e0b' }} aria-hidden="true" />
        <span>{t('settings.api_keys_coming_soon')}</span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ fontSize: 12, color: 'var(--dim)' }}>
          {t('settings.api_keys_desc')}
        </div>
        <Button variant="primary" size="sm" icon={<Plus size={12} />} onClick={() => setShowCreate(v => !v)}>
          {t('settings.generate_key')}
        </Button>
      </div>

      {showCreate && (
        <Card tone="inset" radius={8} padding="14px 16px" style={{ display: 'flex', gap: 8 }}>
          <Input
            placeholder={t('settings.key_name_placeholder')}
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleCreate() }}
            style={{ flex: 1, fontSize: 12 }}
          />
          <Button variant="primary" size="sm" loading={creating} disabled={!newName.trim()} onClick={handleCreate}>
            {t('settings.create')}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => { setShowCreate(false); setNewName('') }}>
            {t('common.cancel')}
          </Button>
        </Card>
      )}

      {newKey && (
        <div style={{ padding: '14px 16px', borderRadius: 8, background: 'rgba(34,197,94,0.07)', border: '1px solid rgba(34,197,94,0.25)' }}>
          <div style={{ fontSize: 12, color: '#22c55e', fontWeight: 600, marginBottom: 8 }}>
            {t('settings.key_generated')}
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Input
              readOnly value={newKey}
              style={{ flex: 1, fontSize: 11, fontFamily: 'monospace', background: 'var(--surface)' }}
            />
            <Button variant="secondary" size="sm" icon={copied ? <Check size={12} /> : <Copy size={12} />} onClick={copyKey}>
              {copied ? t('settings.copied') : t('settings.copy')}
            </Button>
            <button onClick={() => setNewKey(null)} aria-label={t('common.close')} style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)' }}>
              <X size={16} aria-hidden="true" />
            </button>
          </div>
        </div>
      )}

      {error && (
        <div style={{ fontSize: 12, color: '#ef4444', display: 'flex', gap: 6, alignItems: 'center' }}>
          <AlertTriangle size={13} />{error}
        </div>
      )}

      {loading ? (
        <div style={{ textAlign: 'center', padding: 32 }}><Spinner /></div>
      ) : keys.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 32, color: 'var(--dim)', fontSize: 13 }}>
          {t('settings.no_api_keys')}
        </div>
      ) : (
        <table className="data-table">
          <thead>
            <tr><th>{t('settings.col_name')}</th><th>{t('settings.col_created')}</th><th>{t('settings.col_last_used')}</th><th></th></tr>
          </thead>
          <tbody>
            {keys.map(k => (
              <tr key={k.id}>
                <td style={{ fontWeight: 500 }}>{k.name}</td>
                <td style={{ fontSize: 11, color: 'var(--dim)' }}>{k.created_at.slice(0, 10)}</td>
                <td style={{ fontSize: 11, color: 'var(--dim)' }}>{k.last_used ? k.last_used.slice(0, 10) : t('settings.never')}</td>
                <td>
                  <Button
                    variant="danger" size="sm"
                    loading={revoking === k.id}
                    onClick={() => handleRevoke(k.id)}
                  >
                    {t('settings.revoke')}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

// ── Webhooks tab ──────────────────────────────────────────────────────────────
function WebhooksTab() {
  const { t } = useLanguage()
  const { undoable } = useToast()
  const [hooks,    setHooks]    = useState<Webhook[]>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [url,      setUrl]      = useState('')
  const [events,   setEvents]   = useState<string[]>([])
  const [saving,   setSaving]   = useState(false)

  const load = useCallback(() => {
    listWebhooks()
      .then(setHooks)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const handleCreate = async () => {
    if (!url.startsWith('https://') || !events.length) return
    setSaving(true); setError(null)
    try { await createWebhook(url, events); setUrl(''); setEvents([]); setShowForm(false); load() }
    catch (e: any) { setError(e.message) }
    finally { setSaving(false) }
  }

  // Re-adding a webhook is retyping a URL and ticking two boxes, so the modal
  // was pure friction. The row leaves at once and the DELETE is held back for
  // the length of the undo window.
  const handleDelete = (id: string) => {
    const hook = hooks.find(h => h.id === id)
    if (!hook) return
    const index = hooks.findIndex(h => h.id === id)
    undoable({
      title:     t('settings.webhook_deleted'),
      message:   hook.url,
      undoLabel: t('common.undo'),
      apply:  () => setHooks(prev => prev.filter(h => h.id !== id)),
      revert: () => setHooks(prev => {
        if (prev.some(h => h.id === id)) return prev
        const next = [...prev]
        next.splice(index < 0 ? next.length : index, 0, hook)
        return next
      }),
      commit: () => deleteWebhook(id),
      onCommitError: (e: unknown) => { setError(e instanceof Error ? e.message : t('common.error')); load() },
    })
  }

  const toggleEvent = (id: string) =>
    setEvents(e => e.includes(id) ? e.filter(x => x !== id) : [...e, id])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ fontSize: 12, color: 'var(--dim)' }}>
          {t('settings.webhooks_desc')}
        </div>
        <Button variant="primary" size="sm" icon={<Plus size={12} />} onClick={() => setShowForm(v => !v)}>
          {t('settings.add_webhook')}
        </Button>
      </div>

      {showForm && (
        <Card tone="inset" radius={8} padding="16px 18px" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label style={{ fontSize: 11, color: 'var(--dim)', display: 'block', marginBottom: 4 }}>{t('settings.endpoint_url')}</label>
            <Input
              placeholder={t('settings.webhook_url_placeholder')}
              value={url}
              onChange={e => setUrl(e.target.value)}
              style={{ width: '100%', fontSize: 12 }}
            />
            {url && !url.startsWith('https://') && (
              <div style={{ fontSize: 11, color: '#ef4444', marginTop: 3 }}>{t('settings.must_start_https')}</div>
            )}
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--dim)', display: 'block', marginBottom: 6 }}>{t('settings.events_to_subscribe')}</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {WEBHOOK_EVENTS.map(ev => (
                <label key={ev.id} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 12 }}>
                  <input type="checkbox" checked={events.includes(ev.id)} onChange={() => toggleEvent(ev.id)} style={{ accentColor: 'var(--accent)' }} />
                  {t(ev.labelKey)}
                </label>
              ))}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <Button variant="ghost" size="sm" onClick={() => { setShowForm(false); setUrl(''); setEvents([]) }}>{t('common.cancel')}</Button>
            <Button
              variant="primary" size="sm" loading={saving}
              disabled={!url.startsWith('https://') || !events.length}
              onClick={handleCreate}
            >
              {t('common.save')}
            </Button>
          </div>
        </Card>
      )}

      {error && (
        <div style={{ fontSize: 12, color: '#ef4444', display: 'flex', gap: 6, alignItems: 'center' }}>
          <AlertTriangle size={13} />{error}
        </div>
      )}

      {loading ? (
        <div style={{ textAlign: 'center', padding: 32 }}><Spinner /></div>
      ) : hooks.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 32, color: 'var(--dim)', fontSize: 13 }}>{t('settings.no_webhooks')}</div>
      ) : (
        <table className="data-table">
          <thead>
            <tr><th>{t('settings.col_url')}</th><th>{t('settings.col_events')}</th><th>{t('settings.col_created')}</th><th></th></tr>
          </thead>
          <tbody>
            {hooks.map(h => (
              <tr key={h.id}>
                <td style={{ fontFamily: 'monospace', fontSize: 11, maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis' }}>{h.url}</td>
                <td style={{ fontSize: 11 }}>{h.events.map(e => webhookEventLabel(t, e)).join(', ')}</td>
                <td style={{ fontSize: 11, color: 'var(--dim)' }}>{h.created_at.slice(0, 10)}</td>
                <td>
                  <Button variant="danger" size="sm" icon={<Trash2 size={11} />} onClick={() => handleDelete(h.id)}>
                    {t('common.delete')}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

// ── Schedules tab ─────────────────────────────────────────────────────────────
function SchedulesTab() {
  const { t } = useLanguage()
  const confirm = useConfirm()
  const [sessions,   setSessions]  = useState<SessionInfo[]>([])
  const [sessionId,  setSessionId] = useState<string>('')
  const [schedule,   setSchedule]  = useState<JobSchedule | null>(null)
  const [loading,    setLoading]   = useState(false)
  const [saving,     setSaving]    = useState(false)
  const [deleting,   setDeleting]  = useState(false)
  const [error,      setError]     = useState<string | null>(null)
  const [saved,      setSaved]     = useState(false)
  const [cronExpr,   setCron]      = useState(CRON_OPTIONS[0].value)
  const [enabled,    setEnabled]   = useState(true)

  useEffect(() => {
    getSessions()
      .then(ss => {
        const completed = ss.filter(s => s.status === 'COMPLETED')
        setSessions(completed)
        if (completed.length) setSessionId(completed[0].session_id)
      })
      .catch(e => setError(e.message))
  }, [])

  useEffect(() => {
    if (!sessionId) return
    setLoading(true); setSchedule(null); setError(null)
    getSchedule(sessionId)
      .then(s => {
        if (s) { setSchedule(s); setCron(s.cron_expr); setEnabled(s.enabled) }
        else { setSchedule(null); setCron(CRON_OPTIONS[0].value); setEnabled(true) }
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [sessionId])

  const handleSave = async () => {
    setSaving(true); setError(null)
    try {
      const s = await saveSchedule(sessionId, cronExpr, enabled)
      setSchedule(s); setSaved(true); setTimeout(() => setSaved(false), 3000)
    } catch (e: any) { setError(e.message) }
    finally { setSaving(false) }
  }

  const handleDelete = async () => {
    if (!(await confirm({ title: t('settings.remove_schedule_confirm'), danger: true }))) return
    setDeleting(true)
    try { await deleteSchedule(sessionId); setSchedule(null) }
    catch (e: any) { setError(e.message) }
    finally { setDeleting(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ fontSize: 12, color: 'var(--dim)' }}>
        {t('settings.schedules_desc')}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <label style={{ fontSize: 12, color: 'var(--dim)', whiteSpace: 'nowrap' }}>{t('settings.session_label')}</label>
        <Select
          chevron
          value={sessionId}
          onChange={e => setSessionId(e.target.value)}
          style={{ fontSize: 12, flex: 1, maxWidth: 340 }}
        >
          {sessions.length === 0 && <option value="">{t('settings.no_completed_sessions')}</option>}
          {sessions.map(s => (
            <option key={s.session_id} value={s.session_id}>{s.name}</option>
          ))}
        </Select>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 32 }}><Spinner /></div>
      ) : sessionId ? (
        <Card tone="inset" padding="18px 20px" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 8 }}>{t('settings.frequency')}</label>
            <Select
              chevron
              value={cronExpr}
              onChange={e => setCron(e.target.value)}
              style={{ fontSize: 12, width: '100%', maxWidth: 320 }}
            >
              {CRON_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{t(o.labelKey)}</option>
              ))}
            </Select>
            <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 4, fontFamily: 'monospace' }}>
              {cronExpr}
            </div>
          </div>

          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 12 }}>
            <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} style={{ accentColor: 'var(--accent)' }} />
            {t('settings.schedule_enabled')}
          </label>

          {schedule?.next_run && (
            <div style={{ fontSize: 11, color: 'var(--dim)' }}>
              {t('settings.next_run')} <strong style={{ color: 'var(--text)' }}>{new Date(schedule.next_run).toLocaleString()}</strong>
            </div>
          )}

          {error && (
            <div style={{ fontSize: 12, color: '#ef4444', display: 'flex', gap: 6, alignItems: 'center' }}>
              <AlertTriangle size={13} />{error}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8 }}>
            <Button variant="primary" size="sm" loading={saving} onClick={handleSave}>
              {schedule ? t('settings.update_schedule') : t('settings.save_schedule')}
            </Button>
            {schedule && (
              <Button variant="danger" size="sm" loading={deleting} icon={<Trash2 size={11} />} onClick={handleDelete}>
                {t('settings.remove')}
              </Button>
            )}
            {saved && <span style={{ fontSize: 12, color: '#22c55e', alignSelf: 'center' }}>{t('settings.saved')}</span>}
          </div>
        </Card>
      ) : (
        <div style={{ textAlign: 'center', padding: 32, color: 'var(--dim)', fontSize: 13 }}>
          {t('settings.no_sessions_available')}
        </div>
      )}
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────
/**
 * Two of the three tabs are hidden, because neither does what its UI implies.
 *
 * · API keys: a key can be created and listed, and then authenticates nothing
 *   — no endpoint accepts one. The tab shipped its own "coming soon" banner,
 *   which is an admission that the screen was misleading, not a fix for it.
 * · Webhooks: the delivery is real (HMAC-signed) but it is a single POST with
 *   no retry and only two events, both about training runs. A user who
 *   configures one reasonably expects it to be reliable.
 *
 * A control that does nothing is worse than an absent one: it spends the
 * user's trust and their time. They come back by flipping these to true —
 * the tabs and their components are untouched underneath.
 */
const ENABLED: Record<Tab, boolean> = {
  'api-keys':  false,
  'webhooks':  false,
  'schedules': true,
}

const ALL_TABS: { id: Tab; labelKey: string; Icon: React.ComponentType<any> }[] = [
  { id: 'api-keys',   labelKey: 'settings.tab_api_keys',   Icon: Key },
  { id: 'webhooks',   labelKey: 'settings.tab_webhooks',   Icon: WebhookIcon },
  { id: 'schedules',  labelKey: 'settings.tab_schedules',  Icon: Clock },
]
const TABS = ALL_TABS.filter(tab => ENABLED[tab.id])

export default function SettingsPage() {
  const { t } = useLanguage()
  const [tab, setTab] = useState<Tab>(TABS[0]?.id ?? 'schedules')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <div>
        <div style={{ fontSize: 20, fontWeight: 700 }}>{t('settings.title')}</div>
        <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 2 }}>
          {t('settings.subtitle')}
        </div>
      </div>

      {/* Tab bar — pointless with a single tab, so it only renders when there
          is a choice to make. */}
      {TABS.length > 1 && (
      <div style={{ display: 'flex', gap: 4, borderBottom: '1px solid var(--border)', paddingBottom: 0 }}>
        {TABS.map(({ id, labelKey, Icon }) => {
          const active = tab === id
          return (
            <button
              key={id}
              onClick={() => setTab(id)}
              data-tour={`settings.${id}`}
              style={{
                all: 'unset', cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 7,
                padding: '8px 16px', fontSize: 13, fontWeight: active ? 600 : 400,
                color: active ? 'var(--accent)' : 'var(--muted)',
                borderBottom: `2px solid ${active ? 'var(--accent)' : 'transparent'}`,
                marginBottom: -1, transition: 'all 0.15s',
              }}
            >
              <Icon size={13} />
              {t(labelKey)}
            </button>
          )
        })}
      </div>
      )}

      {/* Tab content. Each arm is gated too, so a stale `tab` in state can
          never render a disabled surface.

          The tour anchor lives here rather than on a tab button: with one tab
          left there is no tab bar, and an anchor that only exists when the bar
          is drawn left the tour pointing at nothing. */}
      <Card tone="inset" padding="20px 24px" data-tour={`settings.${tab}`}>
        {ENABLED['api-keys']  && tab === 'api-keys'  && <ApiKeysTab />}
        {ENABLED['webhooks']  && tab === 'webhooks'  && <WebhooksTab />}
        {ENABLED['schedules'] && tab === 'schedules' && <SchedulesTab />}
      </Card>
    </div>
  )
}
