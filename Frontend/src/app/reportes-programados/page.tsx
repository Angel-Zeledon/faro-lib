'use client'
/**
 * /reportes-programados — recurring management reports by email.
 *
 * An admin or analyst picks sections from a small fixed catalogue, a weekly or
 * monthly time (in the company's time zone) and who receives it: people of the
 * account, plus external addresses an ADMIN allowed. Nothing here sends: the
 * worker does, and every report says "not available" instead of inventing a
 * number. The preview renders for the person looking, and mails nobody.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  addAllowedReportRecipient, createReportSchedule, deleteReportSchedule, getDmContacts, getMe,
  getReportCatalog, listAllowedReportRecipients, listReportRuns, listReportSchedules,
  pauseReportSchedule, previewReport, removeAllowedReportRecipient, resumeReportSchedule,
  updateReportSchedule,
  type ReportCatalog, type ReportFrequency, type ReportRun, type ReportSchedule,
  type ReportScheduleInput, type ReportSection,
} from '@/lib/api'
import type { DmContact } from '@/lib/types'
import Card from '@/components/ui/Card'
import Input, { Field, Select } from '@/components/ui/Input'
import { LoadingState, useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D' }

const btn: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
  padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600,
  border: `1px solid ${C.border}`, color: C.text,
}

interface Draft {
  id: string | null
  name: string
  sections: ReportSection[]
  frequency: ReportFrequency
  weekday: number
  day_of_month: number
  hour: number
  user_ids: string[]
  external_emails: string
}

const EMPTY: Draft = {
  id: null, name: '', sections: ['purchasing_summary'], frequency: 'weekly', weekday: 1,
  day_of_month: 1, hour: 6, user_ids: [], external_emails: '',
}

const pad = (n: number) => String(n).padStart(2, '0')

export default function ScheduledReportsPage() {
  const { t, lang } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const role = getUser()?.role
  const isAdmin = role === 'admin'
  const canEdit = role === 'admin' || role === 'analyst'

  const [catalog, setCatalog] = useState<ReportCatalog | null>(null)
  const [items, setItems] = useState<ReportSchedule[] | null>(null)
  const [people, setPeople] = useState<DmContact[]>([])
  const [meId, setMeId] = useState<string>('')
  const [allowed, setAllowed] = useState<string[]>([])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<{ title: string; html: string } | null>(null)
  const [runs, setRuns] = useState<{ title: string; items: ReportRun[] } | null>(null)
  const [allowInput, setAllowInput] = useState('')

  const load = useCallback(() => {
    listReportSchedules().then(r => setItems(r.items)).catch(e => setError(errorDetail(e)))
    listAllowedReportRecipients().then(r => setAllowed(r.items.map(i => i.email))).catch(() => {})
  }, [errorDetail])

  useEffect(() => {
    if (!canEdit) return
    getReportCatalog().then(setCatalog).catch(e => setError(errorDetail(e)))
    getDmContacts({ silent: true }).then(setPeople).catch(() => {})
    getMe().then(m => setMeId(m.id)).catch(() => {})
    load()
  }, [canEdit, load, errorDetail])

  const when = useCallback((iso: string | null) =>
    iso ? new Date(iso).toLocaleString(lang === 'en' ? 'en-US' : 'es-CR', { dateStyle: 'medium', timeStyle: 'short' }) : '', [lang])

  const everyone = useMemo(() => {
    const me = meId ? [{ id: meId, full_name: getUser()?.full_name ?? null, email: getUser()?.email ?? '', role: role ?? '' } as DmContact] : []
    return [...me, ...people]
  }, [meId, people, role])

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError(null)
    try { await fn(); load() } catch (e: unknown) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  if (!canEdit) return <p style={{ fontSize: 13, color: C.muted }}>{t('sr.no_access')}</p>
  if (!items || !catalog) {
    return error ? <p role="alert" style={{ color: C.red, fontSize: 13 }}>{error}</p> : <LoadingState label={t('common.loading')} />
  }

  const zone = catalog.timezone
  const frequencyLine = (s: ReportSchedule) => s.frequency === 'weekly'
    ? t('sr.freq_weekly_line', { day: t(`sr.weekday.${s.weekday}`).toLowerCase(), hour: pad(s.hour), zone: s.timezone })
    : t('sr.freq_monthly_line', { day: s.day_of_month ?? '', hour: pad(s.hour), zone: s.timezone })

  function edit(s: ReportSchedule) {
    setDraft({
      id: s.id, name: s.name, sections: s.sections, frequency: s.frequency, weekday: s.weekday ?? 1,
      day_of_month: s.day_of_month ?? 1, hour: s.hour,
      user_ids: s.recipients.filter(r => r.kind === 'user' && r.user_id).map(r => r.user_id as string),
      external_emails: s.recipients.filter(r => r.kind === 'external').map(r => r.email).join(', '),
    })
  }

  function payload(d: Draft): ReportScheduleInput {
    return {
      name: d.name.trim(), sections: d.sections, frequency: d.frequency, hour: d.hour,
      ...(d.frequency === 'weekly' ? { weekday: d.weekday } : { day_of_month: d.day_of_month }),
      user_ids: d.user_ids,
      external_emails: d.external_emails.split(/[,\s;]+/).map(s => s.trim()).filter(Boolean),
    }
  }

  async function save() {
    if (!draft) return
    const body = payload(draft)
    await run(async () => {
      if (draft.id) await updateReportSchedule(draft.id, body)
      else await createReportSchedule(body)
      setDraft(null)
    })
  }

  async function showPreview(body: Parameters<typeof previewReport>[0], title: string) {
    setBusy(true); setError(null)
    try {
      const r = await previewReport(body)
      setPreview({ title, html: r.html })
    } catch (e: unknown) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  async function showRuns(s: ReportSchedule) {
    setBusy(true); setError(null)
    try { setRuns({ title: s.name, items: (await listReportRuns(s.id)).items }) }
    catch (e: unknown) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  const draftValid = !!draft && draft.name.trim() !== '' && draft.sections.length > 0
    && (draft.user_ids.length > 0 || draft.external_emails.trim() !== '')

  return (
    <div style={{ width: '100%', maxWidth: 820, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 20 }}>
      <p style={{ margin: 0, fontSize: 13, color: C.muted, lineHeight: 1.5 }}>{t('sr.intro', { zone })}</p>
      {error && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{error}</p>}

      {items.length === 0 && !draft && <p style={{ margin: 0, fontSize: 13, color: C.dim }}>{t('sr.empty')}</p>}

      {items.map(s => (
        <Card key={s.id} padding={16} style={{ opacity: s.enabled ? 1 : 0.85 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ minWidth: 0 }}>
              <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700, overflowWrap: 'anywhere' }}>{s.name}</h2>
              <p style={{ margin: '2px 0 0', fontSize: 12.5, color: C.muted }}>{frequencyLine(s)}</p>
            </div>
            {!s.enabled && (
              <span style={{ fontSize: 12, fontWeight: 700, color: C.red }}>
                {t('sr.paused')}{s.paused_reason ? ` · ${t(`sr.paused_reason.${s.paused_reason}`)}` : ''}
              </span>
            )}
          </div>
          <p style={{ margin: '8px 0 0', fontSize: 12.5 }}>
            {s.sections.map(c => t(`sr.section.${c}`)).join(' · ')}
          </p>
          <p style={{ margin: '6px 0 0', fontSize: 12, color: C.muted }}>
            {s.enabled ? t('sr.next_run', { when: when(s.next_run_at) }) : t('sr.not_scheduled')}
            {' · '}
            {s.last_run_at
              ? t('sr.last_run', { when: when(s.last_run_at), status: t(`sr.status.${s.last_status ?? 'queued'}`) })
              : t('sr.never_ran')}
          </p>
          {s.consecutive_failures > 0 && (
            <p role="alert" style={{ margin: '6px 0 0', fontSize: 12, color: C.red }}>
              {t('sr.failing', { n: s.consecutive_failures })}
            </p>
          )}
          <ul style={{ listStyle: 'none', margin: '10px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
            {s.recipients.map(r => (
              <li key={r.id} style={{ fontSize: 12.5, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ overflowWrap: 'anywhere' }}>{r.full_name || r.email}</span>
                {r.full_name && <span style={{ color: C.dim }}>{r.email}</span>}
                <span style={{ color: C.dim }}>{r.kind === 'external' ? t('sr.recipient_external') : ''}</span>
                {!r.would_send && (
                  <span style={{ color: C.red }}>{t(`sr.skip.${r.skip_reason ?? 'unsubscribed'}`)}</span>
                )}
              </li>
            ))}
          </ul>
          <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
            <button type="button" style={btn} disabled={busy} onClick={() => void showPreview({ schedule_id: s.id }, s.name)}>
              {t('sr.preview')}
            </button>
            <button type="button" style={btn} disabled={busy} onClick={() => void showRuns(s)}>{t('sr.history')}</button>
            <button type="button" style={btn} disabled={busy} onClick={() => edit(s)}>{t('sr.edit')}</button>
            <button type="button" style={btn} disabled={busy}
                    onClick={() => run(() => s.enabled ? pauseReportSchedule(s.id) : resumeReportSchedule(s.id))}>
              {s.enabled ? t('sr.pause') : t('sr.resume')}
            </button>
            <button type="button" style={btn} disabled={busy} onClick={async () => {
              if (await confirm({ title: t('sr.delete_title'), message: t('sr.delete_body', { name: s.name }),
                                  confirmLabel: t('sr.delete') })) void run(() => deleteReportSchedule(s.id))
            }}>{t('sr.delete')}</button>
          </div>
        </Card>
      ))}

      {draft ? (
        <Card padding={16}>
          <h2 style={{ margin: '0 0 12px', fontSize: 14, fontWeight: 700 }}>{draft.id ? t('sr.edit_title') : t('sr.new_title')}</h2>
          <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
            <Field label={t('sr.field_name')}>
              <Input value={draft.name} maxLength={catalog.limits.name_length}
                     onChange={e => setDraft(d => d && { ...d, name: e.target.value })} />
            </Field>
            <Field label={t('sr.field_frequency')}>
              <Select value={draft.frequency} onChange={e => setDraft(d => d && { ...d, frequency: e.target.value as ReportFrequency })}>
                {catalog.frequencies.map(f => <option key={f} value={f}>{t(`sr.frequency.${f}`)}</option>)}
              </Select>
            </Field>
            {draft.frequency === 'weekly' ? (
              <Field label={t('sr.field_weekday')}>
                <Select value={draft.weekday} onChange={e => setDraft(d => d && { ...d, weekday: Number(e.target.value) })}>
                  {[1, 2, 3, 4, 5, 6, 7].map(n => <option key={n} value={n}>{t(`sr.weekday.${n}`)}</option>)}
                </Select>
              </Field>
            ) : (
              <Field label={t('sr.field_day')} hint={t('sr.field_day_hint')}>
                <Select value={draft.day_of_month} onChange={e => setDraft(d => d && { ...d, day_of_month: Number(e.target.value) })}>
                  {Array.from({ length: 28 }, (_, i) => i + 1).map(n => <option key={n} value={n}>{n}</option>)}
                </Select>
              </Field>
            )}
            <Field label={t('sr.field_hour', { zone })}>
              <Select value={draft.hour} onChange={e => setDraft(d => d && { ...d, hour: Number(e.target.value) })}>
                {Array.from({ length: 24 }, (_, i) => i).map(n => <option key={n} value={n}>{pad(n)}:00</option>)}
              </Select>
            </Field>
          </div>

          <h3 style={{ margin: '16px 0 6px', fontSize: 12.5, fontWeight: 700, color: C.muted }}>{t('sr.field_sections')}</h3>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {catalog.sections.map(code => (
              <li key={code}>
                <label style={{ display: 'flex', gap: 10, fontSize: 13, cursor: 'pointer', alignItems: 'flex-start' }}>
                  <input type="checkbox" checked={draft.sections.includes(code)}
                         onChange={e => setDraft(d => d && {
                           ...d, sections: e.target.checked
                             ? catalog.sections.filter(c => c === code || d.sections.includes(c))
                             : d.sections.filter(c => c !== code),
                         })} />
                  <span>
                    {t(`sr.section.${code}`)}
                    <span style={{ display: 'block', fontSize: 12, color: C.dim }}>{t(`sr.section_hint.${code}`)}</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>

          <h3 style={{ margin: '16px 0 6px', fontSize: 12.5, fontWeight: 700, color: C.muted }}>{t('sr.field_people')}</h3>
          <p style={{ margin: '0 0 8px', fontSize: 12, color: C.dim }}>{t('sr.field_people_hint')}</p>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {everyone.map(u => (
              <li key={u.id}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, cursor: 'pointer' }}>
                  <input type="checkbox" checked={draft.user_ids.includes(u.id)}
                         onChange={e => setDraft(d => d && {
                           ...d, user_ids: e.target.checked ? [...d.user_ids, u.id] : d.user_ids.filter(i => i !== u.id),
                         })} />
                  <span style={{ overflowWrap: 'anywhere' }}>{u.full_name || u.email}</span>
                  <span style={{ color: C.dim, fontSize: 12 }}>{u.email}</span>
                </label>
              </li>
            ))}
          </ul>
          <div style={{ marginTop: 12 }}>
            <Field label={t('sr.field_external')}
                   hint={allowed.length ? t('sr.field_external_hint', { list: allowed.join(', ') }) : t('sr.field_external_none')}>
              <Input value={draft.external_emails} placeholder="nombre@empresa.com"
                     onChange={e => setDraft(d => d && { ...d, external_emails: e.target.value })} />
            </Field>
          </div>

          <div style={{ display: 'flex', gap: 8, marginTop: 16, flexWrap: 'wrap' }}>
            <button type="button" style={{ ...btn, opacity: draftValid && !busy ? 1 : 0.5 }} disabled={!draftValid || busy}
                    onClick={() => void save()}>{t('sr.save')}</button>
            <button type="button" style={btn} disabled={busy}
                    onClick={() => void showPreview({ sections: draft.sections, frequency: draft.frequency, name: draft.name }, draft.name)}>
              {t('sr.preview')}
            </button>
            <button type="button" style={btn} disabled={busy} onClick={() => setDraft(null)}>{t('sr.cancel')}</button>
          </div>
        </Card>
      ) : (
        <div>
          <button type="button" style={{ ...btn, opacity: items.length >= catalog.limits.schedules ? 0.5 : 1 }}
                  disabled={busy || items.length >= catalog.limits.schedules}
                  onClick={() => setDraft({ ...EMPTY, user_ids: meId ? [meId] : [] })}>
            {t('sr.new')}
          </button>
          {items.length >= catalog.limits.schedules && (
            <span style={{ marginLeft: 10, fontSize: 12, color: C.dim }}>{t('sr.limit_reached', { max: catalog.limits.schedules })}</span>
          )}
        </div>
      )}

      <Card padding={16}>
        <h2 style={{ margin: '0 0 4px', fontSize: 14, fontWeight: 700 }}>{t('sr.allow_title')}</h2>
        <p style={{ margin: '0 0 12px', fontSize: 12.5, color: C.muted }}>{t('sr.allow_hint')}</p>
        {allowed.length === 0 && <p style={{ margin: '0 0 8px', fontSize: 12.5, color: C.dim }}>{t('sr.allow_empty')}</p>}
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {allowed.map(email => (
            <li key={email} style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 13 }}>
              <span style={{ overflowWrap: 'anywhere' }}>{email}</span>
              {isAdmin && (
                <button type="button" style={btn} disabled={busy} onClick={() => run(() => removeAllowedReportRecipient(email))}>
                  {t('sr.allow_remove')}
                </button>
              )}
            </li>
          ))}
        </ul>
        {isAdmin ? (
          <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 240px' }}>
              <Input value={allowInput} placeholder="nombre@empresa.com" aria-label={t('sr.allow_title')}
                     onChange={e => setAllowInput(e.target.value)} />
            </div>
            <button type="button" style={{ ...btn, opacity: allowInput.trim() && !busy ? 1 : 0.5 }} disabled={!allowInput.trim() || busy}
                    onClick={() => run(async () => { await addAllowedReportRecipient(allowInput.trim()); setAllowInput('') })}>
              {t('sr.allow_add')}
            </button>
          </div>
        ) : (
          <p style={{ margin: '10px 0 0', fontSize: 12, color: C.dim }}>{t('sr.allow_admin_only')}</p>
        )}
      </Card>

      {preview && (
        <Card padding={16}>
          <h2 style={{ margin: '0 0 4px', fontSize: 14, fontWeight: 700 }}>{t('sr.preview_title', { name: preview.title || t('sr.unnamed') })}</h2>
          <p style={{ margin: '0 0 10px', fontSize: 12.5, color: C.muted }}>{t('sr.preview_note')}</p>
          {/* The mail body is built and escaped by the backend; sandboxed anyway, no scripts, no navigation. */}
          <iframe title={t('sr.preview_title', { name: preview.title || t('sr.unnamed') })} sandbox="" srcDoc={preview.html}
                  style={{ width: '100%', height: 520, border: `1px solid ${C.border}`, borderRadius: 8, background: '#fff' }} />
          <div style={{ marginTop: 10 }}>
            <button type="button" style={btn} onClick={() => setPreview(null)}>{t('sr.close')}</button>
          </div>
        </Card>
      )}

      {runs && (
        <Card padding={16}>
          <h2 style={{ margin: '0 0 10px', fontSize: 14, fontWeight: 700 }}>{t('sr.history_title', { name: runs.title })}</h2>
          {runs.items.length === 0 && <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('sr.history_empty')}</p>}
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {runs.items.map(r => (
              <li key={r.id} style={{ fontSize: 12.5, borderBottom: `1px solid ${C.border}`, paddingBottom: 8 }}>
                <strong>{when(r.due_at)}</strong>{' · '}{t(`sr.status.${r.status}`)}
                {r.status === 'queued' && (
                  <span style={{ color: C.muted }}>
                    {' · '}{t('sr.delivery', { sent: r.delivery.sent, pending: r.delivery.pending,
                                               failed: r.delivery.failed, abandoned: r.delivery.abandoned })}
                  </span>
                )}
                {r.error && <div style={{ color: C.red }}>{t(`sr.run_error.${r.error}`) === `sr.run_error.${r.error}`
                  ? t('sr.run_error_other', { error: r.error }) : t(`sr.run_error.${r.error}`)}</div>}
                {r.recipients_skipped.length > 0 && (
                  <div style={{ color: C.muted }}>
                    {t('sr.skipped_n', { n: r.recipients_skipped.length })}
                    {': '}{r.recipients_skipped.map(x => t(`sr.skip.${x.reason}`)).join(', ')}
                  </div>
                )}
              </li>
            ))}
          </ul>
          <div style={{ marginTop: 10 }}>
            <button type="button" style={btn} onClick={() => setRuns(null)}>{t('sr.close')}</button>
          </div>
        </Card>
      )}
    </div>
  )
}
