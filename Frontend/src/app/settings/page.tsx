'use client'
// INSTANCIA-3 TAREA-3D: COMPLETADA
import { useState, useEffect, useCallback } from 'react'
import {
  listApiKeys, createApiKey, revokeApiKey,
  listWebhooks, createWebhook, deleteWebhook,
  getSessions, getSchedule, saveSchedule, deleteSchedule,
} from '@/lib/api'
import type { ApiKey, Webhook, JobSchedule, SessionInfo } from '@/lib/types'
import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'
import { Key, Webhook as WebhookIcon, Clock, Copy, Check, X, Plus, Trash2, AlertTriangle } from 'lucide-react'

type Tab = 'api-keys' | 'webhooks' | 'schedules'

const WEBHOOK_EVENTS = [
  { id: 'job.completed',     label: 'Job completed' },
  { id: 'job.failed',        label: 'Job failed' },
  { id: 'accuracy.degraded', label: 'Accuracy degraded' },
]

const CRON_OPTIONS = [
  { label: 'Every Monday at 6am',  value: '0 6 * * 1' },
  { label: 'Every day at midnight', value: '0 0 * * *' },
  { label: 'Weekdays at 6am',      value: '0 6 * * 1-5' },
  { label: 'Every Sunday at 8am',  value: '0 8 * * 0' },
  { label: 'Every hour',           value: '0 * * * *' },
  { label: 'First day of month',   value: '0 0 1 * *' },
]

// ── API Keys tab ──────────────────────────────────────────────────────────────
function ApiKeysTab() {
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
    if (!window.confirm('Revoke this API key? Any apps using it will stop working.')) return
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
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ fontSize: 12, color: 'var(--dim)' }}>
          API keys let external systems access your forecasts programmatically.
        </div>
        <Button variant="primary" size="sm" icon={<Plus size={12} />} onClick={() => setShowCreate(v => !v)}>
          Generate key
        </Button>
      </div>

      {showCreate && (
        <div style={{ display: 'flex', gap: 8, padding: '14px 16px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
          <input
            className="form-input"
            placeholder="Key name (e.g. 'ERP integration')"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleCreate() }}
            style={{ flex: 1, fontSize: 12 }}
          />
          <Button variant="primary" size="sm" loading={creating} disabled={!newName.trim()} onClick={handleCreate}>
            Create
          </Button>
          <Button variant="ghost" size="sm" onClick={() => { setShowCreate(false); setNewName('') }}>
            Cancel
          </Button>
        </div>
      )}

      {newKey && (
        <div style={{ padding: '14px 16px', borderRadius: 8, background: 'rgba(34,197,94,0.07)', border: '1px solid rgba(34,197,94,0.25)' }}>
          <div style={{ fontSize: 12, color: '#22c55e', fontWeight: 600, marginBottom: 8 }}>
            Key generated — copy it now. It will not be shown again.
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              readOnly value={newKey}
              className="form-input"
              style={{ flex: 1, fontSize: 11, fontFamily: 'monospace', background: 'var(--surface)' }}
            />
            <Button variant="secondary" size="sm" icon={copied ? <Check size={12} /> : <Copy size={12} />} onClick={copyKey}>
              {copied ? 'Copied' : 'Copy'}
            </Button>
            <button onClick={() => setNewKey(null)} style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)' }}>
              <X size={16} />
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
          No API keys yet.
        </div>
      ) : (
        <table className="data-table">
          <thead>
            <tr><th>Name</th><th>Created</th><th>Last used</th><th></th></tr>
          </thead>
          <tbody>
            {keys.map(k => (
              <tr key={k.id}>
                <td style={{ fontWeight: 500 }}>{k.name}</td>
                <td style={{ fontSize: 11, color: 'var(--dim)' }}>{k.created_at.slice(0, 10)}</td>
                <td style={{ fontSize: 11, color: 'var(--dim)' }}>{k.last_used ? k.last_used.slice(0, 10) : 'Never'}</td>
                <td>
                  <Button
                    variant="danger" size="sm"
                    loading={revoking === k.id}
                    onClick={() => handleRevoke(k.id)}
                  >
                    Revoke
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
  const [hooks,    setHooks]    = useState<Webhook[]>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [url,      setUrl]      = useState('')
  const [events,   setEvents]   = useState<string[]>([])
  const [saving,   setSaving]   = useState(false)
  const [deleting, setDeleting] = useState<string | null>(null)

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

  const handleDelete = async (id: string) => {
    if (!window.confirm('Delete this webhook?')) return
    setDeleting(id)
    try { await deleteWebhook(id); load() }
    catch (e: any) { setError(e.message) }
    finally { setDeleting(null) }
  }

  const toggleEvent = (id: string) =>
    setEvents(e => e.includes(id) ? e.filter(x => x !== id) : [...e, id])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ fontSize: 12, color: 'var(--dim)' }}>
          Receive HTTP POST notifications when events occur in your account.
        </div>
        <Button variant="primary" size="sm" icon={<Plus size={12} />} onClick={() => setShowForm(v => !v)}>
          Add webhook
        </Button>
      </div>

      {showForm && (
        <div style={{ padding: '16px 18px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label style={{ fontSize: 11, color: 'var(--dim)', display: 'block', marginBottom: 4 }}>Endpoint URL (https://)</label>
            <input
              className="form-input"
              placeholder="https://your-app.com/webhook"
              value={url}
              onChange={e => setUrl(e.target.value)}
              style={{ width: '100%', fontSize: 12 }}
            />
            {url && !url.startsWith('https://') && (
              <div style={{ fontSize: 11, color: '#ef4444', marginTop: 3 }}>Must start with https://</div>
            )}
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--dim)', display: 'block', marginBottom: 6 }}>Events to subscribe</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {WEBHOOK_EVENTS.map(ev => (
                <label key={ev.id} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 12 }}>
                  <input type="checkbox" checked={events.includes(ev.id)} onChange={() => toggleEvent(ev.id)} style={{ accentColor: 'var(--accent)' }} />
                  {ev.label}
                </label>
              ))}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <Button variant="ghost" size="sm" onClick={() => { setShowForm(false); setUrl(''); setEvents([]) }}>Cancel</Button>
            <Button
              variant="primary" size="sm" loading={saving}
              disabled={!url.startsWith('https://') || !events.length}
              onClick={handleCreate}
            >
              Save
            </Button>
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
      ) : hooks.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 32, color: 'var(--dim)', fontSize: 13 }}>No webhooks configured.</div>
      ) : (
        <table className="data-table">
          <thead>
            <tr><th>URL</th><th>Events</th><th>Created</th><th></th></tr>
          </thead>
          <tbody>
            {hooks.map(h => (
              <tr key={h.id}>
                <td style={{ fontFamily: 'monospace', fontSize: 11, maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis' }}>{h.url}</td>
                <td style={{ fontSize: 11 }}>{h.events.join(', ')}</td>
                <td style={{ fontSize: 11, color: 'var(--dim)' }}>{h.created_at.slice(0, 10)}</td>
                <td>
                  <Button variant="danger" size="sm" loading={deleting === h.id} icon={<Trash2 size={11} />} onClick={() => handleDelete(h.id)}>
                    Delete
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
    if (!window.confirm('Remove this schedule?')) return
    setDeleting(true)
    try { await deleteSchedule(sessionId); setSchedule(null) }
    catch (e: any) { setError(e.message) }
    finally { setDeleting(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ fontSize: 12, color: 'var(--dim)' }}>
        Automatically retrain a session on a recurring schedule.
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <label style={{ fontSize: 12, color: 'var(--dim)', whiteSpace: 'nowrap' }}>Session:</label>
        <select
          value={sessionId}
          onChange={e => setSessionId(e.target.value)}
          className="form-input form-select"
          style={{ fontSize: 12, flex: 1, maxWidth: 340 }}
        >
          {sessions.length === 0 && <option value="">No completed sessions</option>}
          {sessions.map(s => (
            <option key={s.session_id} value={s.session_id}>{s.name}</option>
          ))}
        </select>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 32 }}><Spinner /></div>
      ) : sessionId ? (
        <div style={{ padding: '18px 20px', borderRadius: 10, background: 'var(--surface-2)', border: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 8 }}>Frequency</label>
            <select
              value={cronExpr}
              onChange={e => setCron(e.target.value)}
              className="form-input form-select"
              style={{ fontSize: 12, width: '100%', maxWidth: 320 }}
            >
              {CRON_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 4, fontFamily: 'monospace' }}>
              {cronExpr}
            </div>
          </div>

          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 12 }}>
            <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} style={{ accentColor: 'var(--accent)' }} />
            Schedule enabled
          </label>

          {schedule?.next_run && (
            <div style={{ fontSize: 11, color: 'var(--dim)' }}>
              Next run: <strong style={{ color: 'var(--text)' }}>{new Date(schedule.next_run).toLocaleString()}</strong>
            </div>
          )}

          {error && (
            <div style={{ fontSize: 12, color: '#ef4444', display: 'flex', gap: 6, alignItems: 'center' }}>
              <AlertTriangle size={13} />{error}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8 }}>
            <Button variant="primary" size="sm" loading={saving} onClick={handleSave}>
              {schedule ? 'Update schedule' : 'Save schedule'}
            </Button>
            {schedule && (
              <Button variant="danger" size="sm" loading={deleting} icon={<Trash2 size={11} />} onClick={handleDelete}>
                Remove
              </Button>
            )}
            {saved && <span style={{ fontSize: 12, color: '#22c55e', alignSelf: 'center' }}>Saved.</span>}
          </div>
        </div>
      ) : (
        <div style={{ textAlign: 'center', padding: 32, color: 'var(--dim)', fontSize: 13 }}>
          No completed sessions available.
        </div>
      )}
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────
const TABS: { id: Tab; label: string; Icon: React.ComponentType<any> }[] = [
  { id: 'api-keys',   label: 'API Keys',   Icon: Key },
  { id: 'webhooks',   label: 'Webhooks',   Icon: WebhookIcon },
  { id: 'schedules',  label: 'Schedules',  Icon: Clock },
]

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>('api-keys')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, animation: 'fadeIn 0.3s ease-out' }}>
      <div>
        <div style={{ fontSize: 20, fontWeight: 700 }}>Settings</div>
        <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 2 }}>
          API keys, webhooks, and automated schedules
        </div>
      </div>

      {/* Tab bar */}
      <div style={{ display: 'flex', gap: 4, borderBottom: '1px solid var(--border)', paddingBottom: 0 }}>
        {TABS.map(({ id, label, Icon }) => {
          const active = tab === id
          return (
            <button
              key={id}
              onClick={() => setTab(id)}
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
              {label}
            </button>
          )
        })}
      </div>

      {/* Tab content */}
      <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 10, padding: '20px 24px' }}>
        {tab === 'api-keys'  && <ApiKeysTab />}
        {tab === 'webhooks'  && <WebhooksTab />}
        {tab === 'schedules' && <SchedulesTab />}
      </div>
    </div>
  )
}
