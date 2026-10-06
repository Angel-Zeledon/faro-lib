'use client'
import { useCallback, useEffect, useState } from 'react'
import { Check, Copy, RotateCw, Send, Trash2, Rewind, Power, Radio } from 'lucide-react'
import {
  getAuditStream, putAuditStream, deleteAuditStream, enableAuditStream, disableAuditStream,
  rotateAuditStreamSecret, replayAuditStream, testAuditStream, listAuditStreamDeliveries,
} from '@/lib/api'
import type { AuditStreamDelivery, AuditStreamState } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useErrorDetail } from '@/components/ui/States'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'

type Configured = Extract<AuditStreamState, { configured: true }>

const REFRESH_MS = 10_000

const STATUS_VARIANT: Record<AuditStreamDelivery['status'], 'success' | 'danger' | 'muted'> = {
  delivered: 'success', failed: 'danger', superseded: 'muted',
}

/**
 * Continuous audit export: where this account's audit trail and activity feed
 * are sent as they happen (an HTTPS endpoint of the customer's SIEM), whether it
 * is on, how far it has got, and the log of every delivery attempt.
 *
 * Admin only; the hub decides who sees it and the endpoints refuse everybody
 * else. The signing secret is shown ONCE (on connect and on rotate) and is never
 * readable again, so the card keeps it in memory only until it is dismissed.
 */
export default function AuditStreamCard() {
  const { t, lang } = useLanguage()
  const { addToast } = useToast()
  const confirm = useConfirm()
  const errorDetail = useErrorDetail()

  const [state, setState] = useState<AuditStreamState | null>(null)
  const [log, setLog] = useState<AuditStreamDelivery[]>([])
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [url, setUrl] = useState('')
  const [since, setSince] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [secret, setSecret] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const load = useCallback(async () => {
    try {
      const s = await getAuditStream()
      setState(s)
      setLoadErr(null)
      setLog(s.configured ? await listAuditStreamDeliveries(20) : [])
    } catch (e) {
      setLoadErr(errorDetail(e) || t('auditstream.load_failed'))
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    const id = setInterval(() => { void load() }, REFRESH_MS)
    return () => clearInterval(id)
  }, [load])
  // The address box follows the server unless somebody is typing in it.
  const savedUrl = state && state.configured ? state.url : ''
  useEffect(() => { setUrl(savedUrl) }, [savedUrl])

  if (loadErr && !state) {
    return <div role="alert" style={{ padding: '14px 16px', fontSize: 13, color: '#C0504D' }}>{loadErr}</div>
  }
  if (!state) {
    return <div style={{ padding: 20, display: 'flex', justifyContent: 'center' }}><Spinner size={18} /></div>
  }

  const run = async (name: string, action: () => Promise<void>, failTitle: string) => {
    setBusy(name)
    try { await action() }
    catch (e) { addToast(failTitle, errorDetail(e), 'error') }
    finally { setBusy(null) }
  }

  const when = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString(lang === 'es' ? 'es' : 'en', { dateStyle: 'short', timeStyle: 'short' }) : t('auditstream.never')

  const age = (seconds: number | null) => {
    if (seconds === null) return t('auditstream.caught_up')
    if (seconds < 60) return t('auditstream.age_s', { n: seconds })
    if (seconds < 3600) return t('auditstream.age_m', { n: Math.floor(seconds / 60) })
    if (seconds < 86400) return t('auditstream.age_h', { n: Math.floor(seconds / 3600) })
    return t('auditstream.age_d', { n: Math.floor(seconds / 86400) })
  }

  const connect = () => run('connect', async () => {
    const s = await putAuditStream({ url: url.trim() })
    setState(s)
    if (s.configured && s.secret) setSecret(s.secret)
    addToast(t('auditstream.connected'), '', 'success')
    await load()
  }, t('auditstream.action_failed'))

  const saveUrl = () => run('save', async () => {
    setState(await putAuditStream({ url: url.trim() }))
    addToast(t('auditstream.saved'), '', 'success')
  }, t('auditstream.action_failed'))

  const toggle = (s: Configured) => run('toggle', async () => {
    setState(s.enabled ? await disableAuditStream() : await enableAuditStream())
  }, t('auditstream.action_failed'))

  const sendTest = () => run('test', async () => {
    await testAuditStream()
    addToast(t('auditstream.test_queued'), t('auditstream.test_queued_body'), 'success')
    setTimeout(() => { void load() }, 4000)
  }, t('auditstream.action_failed'))

  const rotate = async () => {
    if (!(await confirm({
      title: t('auditstream.rotate_confirm_title'), message: t('auditstream.rotate_confirm_body'),
      confirmLabel: t('auditstream.rotate_confirm_cta'), danger: true,
    }))) return
    await run('rotate', async () => {
      const r = await rotateAuditStreamSecret()
      setSecret(r.secret)
      await load()
    }, t('auditstream.action_failed'))
  }

  const replay = async (body: { cursor: string } | { since: string }, label: string) => {
    if (!(await confirm({
      title: t('auditstream.replay_confirm_title'),
      message: t('auditstream.replay_confirm_body', { from: label }),
      confirmLabel: t('auditstream.replay_confirm_cta'),
    }))) return
    await run('replay', async () => {
      const r = await replayAuditStream(body)
      addToast(r.moved ? t('auditstream.replay_done') : t('auditstream.replay_nothing'), '', r.moved ? 'success' : 'info')
      await load()
    }, t('auditstream.action_failed'))
  }

  const remove = async () => {
    if (!(await confirm({
      title: t('auditstream.remove_confirm_title'), message: t('auditstream.remove_confirm_body'),
      confirmLabel: t('auditstream.remove_confirm_cta'), danger: true,
    }))) return
    await run('remove', async () => {
      await deleteAuditStream()
      setSecret(null)
      setUrl('')
      addToast(t('auditstream.removed'), '', 'success')
      await load()
    }, t('auditstream.action_failed'))
  }

  const copySecret = async () => {
    if (!secret) return
    try {
      await navigator.clipboard.writeText(secret)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      addToast(t('auditstream.copy_failed'), secret, 'error')
    }
  }

  const header = (
    <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
      <span aria-hidden="true" style={{
        width: 36, height: 36, borderRadius: 9, flexShrink: 0,
        background: 'var(--accent-dim)', color: 'var(--accent)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}><Radio size={18} strokeWidth={1.7} /></span>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>{t('auditstream.title')}</div>
        <div style={{ marginTop: 2, fontSize: 13, color: 'var(--muted)', lineHeight: 1.4 }}>{t('auditstream.lead')}</div>
      </div>
    </div>
  )

  const urlRow = (primary: React.ReactNode) => (
    <div>
      <label htmlFor="audit-stream-url" style={LABEL}>{t('auditstream.url_label')}</label>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <input
          id="audit-stream-url" type="url" name="audit_stream_url" value={url}
          placeholder={t('auditstream.url_placeholder')} autoComplete="off" spellCheck={false}
          onChange={e => setUrl(e.target.value)}
          className="form-input"
          style={{ flex: '1 1 260px', minWidth: 0, fontSize: 16, padding: '8px 12px', borderRadius: 7 }}
        />
        {primary}
      </div>
      <p style={{ margin: '6px 0 0', fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.5 }}>{t('auditstream.url_help')}</p>
    </div>
  )

  const secretPanel = secret && (
    <div role="status" style={{
      padding: 12, borderRadius: 9, border: '1px solid var(--border)', background: 'var(--surface-2)',
      display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      <div style={{ fontSize: 13, fontWeight: 600 }}>{t('auditstream.secret_title')}</div>
      <p style={{ margin: 0, fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.5 }}>{t('auditstream.secret_once')}</p>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <code style={{
          flex: '1 1 240px', minWidth: 0, overflowWrap: 'anywhere', fontSize: 12.5,
          padding: '8px 10px', borderRadius: 7, background: 'var(--surface)', border: '1px solid var(--border)',
        }}>{secret}</code>
        <Button size="sm" onClick={copySecret} icon={copied ? <Check size={14} /> : <Copy size={14} />}>
          {copied ? t('auditstream.copied') : t('auditstream.copy')}
        </Button>
        <Button size="sm" onClick={() => setSecret(null)}>{t('auditstream.secret_done')}</Button>
      </div>
    </div>
  )

  if (!state.configured) {
    return (
      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
        {header}
        {urlRow(
          <Button size="sm" variant="primary" onClick={connect} disabled={!url.trim() || busy !== null}
            loading={busy === 'connect'}>{t('auditstream.connect')}</Button>,
        )}
        <p style={{ margin: 0, fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.5 }}>{t('auditstream.starts_now')}</p>
      </div>
    )
  }

  const s = state
  const offReason = s.disabled_reason ? t(`auditstream.off_${s.disabled_reason}`) : ''
  const backlog = s.pending_capped ? `${s.pending_records}+` : String(s.pending_records)

  return (
    <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
      {header}
      {secretPanel}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <Badge variant={s.enabled ? 'success' : 'danger'} dot>
          {s.enabled ? t('auditstream.state_on') : t('auditstream.state_off')}
        </Badge>
        {!s.enabled && offReason && (
          <span style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.45 }}>{offReason}</span>
        )}
        {s.enabled && s.consecutive_failures > 0 && (
          <Badge variant="warning">{t('auditstream.retrying', { n: s.consecutive_failures })}</Badge>
        )}
      </div>

      {urlRow(
        <Button size="sm" variant="primary" onClick={saveUrl}
          disabled={!url.trim() || url.trim() === s.url || busy !== null} loading={busy === 'save'}>
          {t('auditstream.save')}
        </Button>,
      )}

      <dl style={{
        margin: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12,
      }}>
        <Stat label={t('auditstream.pending_label')} value={backlog} />
        <Stat label={t('auditstream.lag_label')} value={age(s.lag_seconds)} />
        <Stat label={t('auditstream.delivered_label')} value={String(s.delivered_records)} />
        <Stat label={t('auditstream.last_success_label')} value={when(s.last_success_at)} />
        <Stat label={t('auditstream.cursor_label')} value={s.cursor} mono />
      </dl>

      {s.last_error && (
        <p role="status" style={{ margin: 0, fontSize: 12.5, color: '#C0504D', lineHeight: 1.5, overflowWrap: 'anywhere' }}>
          {t('auditstream.last_error', { error: s.last_error })}
        </p>
      )}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Button size="sm" onClick={() => toggle(s)} loading={busy === 'toggle'} disabled={busy !== null}
          icon={<Power size={14} />}>
          {s.enabled ? t('auditstream.disable') : t('auditstream.enable')}
        </Button>
        <Button size="sm" onClick={sendTest} loading={busy === 'test'} disabled={busy !== null || s.test_pending}
          icon={<Send size={14} />}>
          {t('auditstream.send_test')}
        </Button>
        <Button size="sm" onClick={rotate} loading={busy === 'rotate'} disabled={busy !== null}
          icon={<RotateCw size={14} />}>
          {t('auditstream.rotate')}
        </Button>
        <Button size="sm" variant="danger" onClick={remove} loading={busy === 'remove'} disabled={busy !== null}
          icon={<Trash2 size={14} />}>
          {t('auditstream.remove')}
        </Button>
      </div>

      <div>
        <div style={LABEL}>{t('auditstream.replay_title')}</div>
        <p style={{ margin: '0 0 8px', fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.5 }}>
          {t('auditstream.replay_help')}
        </p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input
            type="date" name="audit_stream_since" value={since} aria-label={t('auditstream.replay_since_label')}
            onChange={e => setSince(e.target.value)} className="form-input"
            style={{ flex: '0 1 180px', minWidth: 0, fontSize: 16, padding: '8px 12px', borderRadius: 7 }}
          />
          <Button size="sm" onClick={() => replay({ since }, since)} disabled={!since || busy !== null}
            icon={<Rewind size={14} />}>
            {t('auditstream.replay_since_cta')}
          </Button>
          <Button size="sm" onClick={() => replay({ cursor: '0:0' }, t('auditstream.replay_start'))}
            disabled={busy !== null} icon={<Rewind size={14} />}>
            {t('auditstream.replay_all_cta')}
          </Button>
        </div>
      </div>

      <div>
        <div style={LABEL}>{t('auditstream.log_title')}</div>
        {log.length === 0 ? (
          <p style={{ margin: 0, fontSize: 13, color: 'var(--muted)' }}>{t('auditstream.log_empty')}</p>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column' }}>
            {log.map(d => (
              <li key={d.id} style={{ padding: '8px 0', borderTop: '1px solid var(--border)', minWidth: 0 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <Badge variant={STATUS_VARIANT[d.status]}>{t(`auditstream.status_${d.status}`)}</Badge>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>
                    {d.kind === 'test'
                      ? t('auditstream.kind_test')
                      : t('auditstream.log_records', { n: d.records })}
                  </span>
                  {d.status_code !== null && (
                    <span style={{ fontSize: 12, color: 'var(--muted)' }}>HTTP {d.status_code}</span>
                  )}
                </div>
                <div style={{ marginTop: 3, fontSize: 12, color: 'var(--muted)', overflowWrap: 'anywhere' }}>
                  {when(d.created_at)}
                  {d.duration_ms !== null ? ` · ${d.duration_ms} ms` : ''}
                  {d.error ? ` · ${d.error}` : ''}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div style={{ minWidth: 0 }}>
      <dt style={LABEL}>{label}</dt>
      <dd style={{
        margin: 0, fontSize: 13.5, fontWeight: 600, overflowWrap: 'anywhere',
        fontFamily: mono ? 'ui-monospace, SFMono-Regular, Menlo, monospace' : undefined,
      }}>{value}</dd>
    </div>
  )
}

const LABEL: React.CSSProperties = {
  display: 'block', fontSize: 11.5, fontWeight: 700, color: 'var(--dim)', textTransform: 'uppercase',
  letterSpacing: '0.05em', marginBottom: 6,
}
