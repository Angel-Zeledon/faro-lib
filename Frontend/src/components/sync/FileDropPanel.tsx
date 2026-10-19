'use client'
/**
 * File drops: an SFTP directory or an S3-compatible bucket the company drops
 * files into. StockAI reads a file once it is finished uploading, checks it
 * against the data it replaces, and either applies it, holds it for a person, or
 * rejects it with the reason. It only reads and moves files; it never deletes one
 * it has not copied aside first.
 *
 * Managing a source (credentials for the company's server) is an admin's job;
 * settling a held file is an analyst's. The server enforces both: the buttons
 * here only follow.
 */
import { useCallback, useEffect, useState } from 'react'
import {
  acceptFileDropHostKey, checkFileDropSource, createFileDropSource, decideFileDropFile,
  deleteFileDropSource, listDataSources, listFileDropFiles, listFileDropSources,
  testFileDropSource, updateFileDropSource,
} from '@/lib/api'
import type {
  FileDropFile, FileDropInput, FileDropKind, FileDropSource, FileDropTest,
} from '@/lib/api'
import type { DataSource } from '@/lib/types'
import Card from '@/components/ui/Card'
import Input, { Field, Select } from '@/components/ui/Input'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
            red: '#C0504D', amber: '#B7791F', green: '#2F855A' }
const btn: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
  padding: '5px 11px', borderRadius: 8, fontSize: 12, fontWeight: 600,
  border: `1px solid ${C.border}`, color: C.text,
}
const primary: React.CSSProperties = { ...btn, background: 'var(--accent, #0C3A40)', color: '#fff', border: 'none' }
const small: React.CSSProperties = { margin: 0, fontSize: 12, color: C.muted, lineHeight: 1.5 }

const BLANK = {
  name: '', kind: 'sftp' as FileDropKind, host: '', port: '22', username: '', path: '.',
  password: '', private_key: '', bucket: '', endpoint: '', region: 'us-east-1', prefix: '',
  access_key: '', secret_key: '', pattern: '*.csv', interval: '15', target: '',
}

export default function FileDropPanel() {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const role = getUser()?.role
  const isAdmin = role === 'admin'
  const canDecide = role === 'admin' || role === 'analyst'
  const [sources, setSources] = useState<FileDropSource[] | null>(null)
  const [files, setFiles] = useState<FileDropFile[]>([])
  const [datasets, setDatasets] = useState<DataSource[]>([])
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(BLANK)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [tests, setTests] = useState<Record<string, FileDropTest>>({})

  const load = useCallback(() => {
    Promise.all([listFileDropSources({ silent: true }), listFileDropFiles(50, { silent: true })])
      .then(([s, f]) => { setSources(s); setFiles(f) })
      .catch(e => { setSources([]); setError(errorDetail(e)) })
  }, [errorDetail])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    if (!isAdmin) return
    listDataSources(0, 100).then(r => setDatasets(r.items.filter(d => d.source_type === 'file'))).catch(() => {})
  }, [isAdmin])

  const run = async (key: string, fn: () => Promise<string | void>) => {
    setBusy(key); setError(null); setNotice(null)
    try { const msg = await fn(); if (msg) setNotice(msg); load() }
    catch (e: unknown) { setError(errorDetail(e)) }
    finally { setBusy(null) }
  }

  const set = (k: keyof typeof BLANK) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  const save = () => run('save', async () => {
    const sftp = form.kind === 'sftp'
    const body: FileDropInput = {
      name: form.name.trim(), kind: form.kind,
      config: sftp
        ? { host: form.host.trim(), port: parseInt(form.port) || 22, username: form.username.trim(), path: form.path.trim() || '.' }
        : { bucket: form.bucket.trim(), endpoint: form.endpoint.trim(), region: form.region.trim(), prefix: form.prefix.trim() },
      secrets: sftp
        ? (form.private_key.trim() ? { private_key: form.private_key } : { password: form.password })
        : { access_key: form.access_key.trim(), secret_key: form.secret_key },
      file_pattern: form.pattern.trim() || '*.csv',
      interval_minutes: parseInt(form.interval) || 15,
      target_dataset_id: form.target || null,
    }
    await createFileDropSource(body)
    setAdding(false); setForm(BLANK)
    return t('filedrop.created')
  })

  const test = (s: FileDropSource) => run(`test:${s.id}`, async () => {
    const r = await testFileDropSource(s.id)
    setTests(prev => ({ ...prev, [s.id]: r }))
  })

  const fmtWhen = (iso: string | null) => iso ? new Date(iso).toLocaleString() : t('filedrop.never')
  const reason = (code: string | null, params: Record<string, unknown> | null) =>
    code ? t(`filedrop.code.${code}`, (params ?? {}) as Record<string, string | number>) : ''
  const statusColor = (s: FileDropSource['status']) =>
    s === 'ok' ? C.green : s === 'blocked' ? C.red : s === 'error' ? C.amber : C.muted

  if (sources === null) return null
  // Nothing configured and nobody who can add one: the section would be an empty box.
  if (sources.length === 0 && !isAdmin) return null

  return (
    <Card padding={16}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{t('filedrop.title')}</h2>
        {isAdmin && !adding && (
          <button type="button" style={primary} onClick={() => setAdding(true)}>{t('filedrop.add')}</button>
        )}
      </div>
      <p style={{ ...small, marginTop: 6 }}>{t('filedrop.intro')}</p>
      {error && <p role="alert" style={{ ...small, color: C.red, marginTop: 8 }}>{error}</p>}
      {notice && <p role="status" style={{ ...small, color: C.green, marginTop: 8 }}>{notice}</p>}

      {adding && (
        <div style={{ display: 'grid', gap: 10, marginTop: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}>
          <Field label={t('filedrop.f_name')}><Input value={form.name} onChange={set('name')} maxLength={100} /></Field>
          <Field label={t('filedrop.f_kind')}>
            <Select value={form.kind} onChange={set('kind')}>
              <option value="sftp">SFTP</option>
              <option value="s3">{t('filedrop.kind_s3')}</option>
            </Select>
          </Field>
          {form.kind === 'sftp' ? (<>
            <Field label={t('filedrop.f_host')}><Input value={form.host} onChange={set('host')} /></Field>
            <Field label={t('filedrop.f_port')}><Input value={form.port} onChange={set('port')} inputMode="numeric" /></Field>
            <Field label={t('filedrop.f_user')}><Input value={form.username} onChange={set('username')} autoComplete="off" /></Field>
            <Field label={t('filedrop.f_password')}>
              <Input type="password" value={form.password} onChange={set('password')} autoComplete="new-password" />
            </Field>
            <Field label={t('filedrop.f_key')} hint={t('filedrop.f_key_hint')}>
              <Input value={form.private_key} onChange={set('private_key')} autoComplete="off" />
            </Field>
            <Field label={t('filedrop.f_path')}><Input value={form.path} onChange={set('path')} /></Field>
          </>) : (<>
            <Field label={t('filedrop.f_bucket')}><Input value={form.bucket} onChange={set('bucket')} /></Field>
            <Field label={t('filedrop.f_prefix')}><Input value={form.prefix} onChange={set('prefix')} /></Field>
            <Field label={t('filedrop.f_endpoint')} hint={t('filedrop.f_endpoint_hint')}>
              <Input value={form.endpoint} onChange={set('endpoint')} />
            </Field>
            <Field label={t('filedrop.f_region')}><Input value={form.region} onChange={set('region')} /></Field>
            <Field label={t('filedrop.f_access_key')}><Input value={form.access_key} onChange={set('access_key')} autoComplete="off" /></Field>
            <Field label={t('filedrop.f_secret_key')}>
              <Input type="password" value={form.secret_key} onChange={set('secret_key')} autoComplete="new-password" />
            </Field>
          </>)}
          <Field label={t('filedrop.f_pattern')} hint={t('filedrop.f_pattern_hint')}>
            <Input value={form.pattern} onChange={set('pattern')} />
          </Field>
          <Field label={t('filedrop.f_interval')}><Input value={form.interval} onChange={set('interval')} inputMode="numeric" /></Field>
          <Field label={t('filedrop.f_target')} hint={t('filedrop.f_target_hint')}>
            <Select value={form.target} onChange={set('target')}>
              <option value="">{t('filedrop.target_auto')}</option>
              {datasets.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
            </Select>
          </Field>
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
            <button type="button" style={primary} disabled={busy === 'save'} onClick={save}>{t('filedrop.save')}</button>
            <button type="button" style={btn} onClick={() => { setAdding(false); setForm(BLANK) }}>{t('common.cancel')}</button>
          </div>
        </div>
      )}

      <ul style={{ listStyle: 'none', margin: '12px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {sources.map(s => {
          const tr = tests[s.id]
          return (
            <li key={s.id} style={{ border: `1px solid ${C.border}`, borderRadius: 10, padding: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
                <div style={{ minWidth: 0 }}>
                  <strong style={{ fontSize: 13, overflowWrap: 'anywhere' }}>{s.name}</strong>
                  <span style={{ fontSize: 12, color: C.muted }}>
                    {' · '}{s.kind === 'sftp' ? `SFTP ${s.config.host}` : `S3 ${s.config.bucket}`}
                    {' · '}{s.file_pattern}
                  </span>
                  <div style={{ fontSize: 12, marginTop: 2 }}>
                    <span style={{ color: statusColor(s.status), fontWeight: 700 }}>{t(`filedrop.status.${s.status}`)}</span>
                    {!s.enabled && <span style={{ color: C.muted }}>{' · '}{t('filedrop.paused')}</span>}
                    <span style={{ color: C.muted }}>{' · '}{t('filedrop.last_check', { when: fmtWhen(s.last_checked_at) })}</span>
                  </div>
                  {s.last_error_code && (
                    <p role="alert" style={{ ...small, color: C.red, marginTop: 4 }}>
                      {reason(s.last_error_code, s.last_error_params)}
                    </p>
                  )}
                  {s.kind === 'sftp' && s.host_key_fingerprint && (
                    <p style={{ ...small, marginTop: 4, overflowWrap: 'anywhere' }}>
                      {t('filedrop.pinned_key', { fp: s.host_key_fingerprint })}
                    </p>
                  )}
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'flex-start' }}>
                  {canDecide && (
                    <button type="button" style={btn} disabled={busy === `check:${s.id}`}
                            onClick={() => run(`check:${s.id}`, async () => {
                              const r = await checkFileDropSource(s.id)
                              return r.error ? undefined : t('filedrop.checked', { applied: r.applied, held: r.held, waiting: r.waiting })
                            })}>{t('filedrop.check_now')}</button>
                  )}
                  {isAdmin && (<>
                    <button type="button" style={btn} disabled={busy === `test:${s.id}`} onClick={() => test(s)}>{t('filedrop.test')}</button>
                    <button type="button" style={btn}
                            onClick={() => run(`en:${s.id}`, async () => { await updateFileDropSource(s.id, { enabled: !s.enabled }) })}>
                      {s.enabled ? t('filedrop.pause') : t('filedrop.resume')}
                    </button>
                    <button type="button" style={{ ...btn, color: C.red }}
                            onClick={async () => {
                              if (!(await confirm({ title: t('filedrop.delete_title'), message: t('filedrop.delete_message'), danger: true }))) return
                              run(`del:${s.id}`, async () => { await deleteFileDropSource(s.id) })
                            }}>{t('filedrop.delete')}</button>
                  </>)}
                </div>
              </div>
              {tr && (
                <div role="status" style={{ marginTop: 8, fontSize: 12.5 }}>
                  <p style={{ margin: 0 }}>{t('filedrop.test_ok', { seen: tr.files_seen, matching: tr.files_matching })}</p>
                  {tr.host_key && (
                    <p style={{ ...small, marginTop: 4, overflowWrap: 'anywhere' }}>
                      {tr.host_key.newly_pinned ? t('filedrop.key_pinned', { fp: tr.host_key.fingerprint }) : t('filedrop.key_same')}
                    </p>
                  )}
                  <p style={{ ...small, marginTop: 4 }}>
                    {t('filedrop.least_privilege', { list: tr.least_privilege.map(p => t(`filedrop.perm.${p}`)).join(', ') })}
                  </p>
                  {tr.kind === 's3' && <p style={{ ...small, marginTop: 4, color: C.amber }}>{t('filedrop.s3_unverified')}</p>}
                </div>
              )}
              {isAdmin && s.last_error_code === 'file_drop_host_key_changed' && (
                <div style={{ marginTop: 8 }}>
                  <p style={{ ...small, color: C.red }}>{t('filedrop.key_changed')}</p>
                  <button type="button" style={btn} onClick={async () => {
                    const seen = String(s.last_error_params?.observed ?? '')
                    if (!seen) return
                    if (!(await confirm({ title: t('filedrop.accept_key_title'), message: t('filedrop.accept_key_message', { fp: seen }), danger: true }))) return
                    run(`key:${s.id}`, async () => { await acceptFileDropHostKey(s.id, seen) })
                  }}>{t('filedrop.accept_key')}</button>
                </div>
              )}
            </li>
          )
        })}
      </ul>

      {files.length > 0 && (
        <div style={{ marginTop: 14, overflowX: 'auto' }}>
          <h3 style={{ margin: '0 0 6px', fontSize: 12.5, fontWeight: 700 }}>{t('filedrop.files_title')}</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
            <tbody>
              {files.map(f => (
                <tr key={f.id} style={{ borderTop: `1px solid ${C.border}` }}>
                  <td style={{ padding: '6px 8px 6px 0', overflowWrap: 'anywhere' }}>{f.remote_name}</td>
                  <td style={{ padding: '6px 8px' }}>
                    <span style={{ fontWeight: 700, color: f.status === 'applied' ? C.green : f.status === 'held' ? C.amber : f.status === 'applying' ? C.muted : C.red }}>
                      {t(`filedrop.file_status.${f.status}`)}
                    </span>
                    {f.reason_code && <div style={small}>{reason(f.reason_code, f.reason_params)}</div>}
                    {f.status === 'applied' && !f.moved && <div style={{ ...small, color: C.amber }}>{t('filedrop.not_moved')}</div>}
                  </td>
                  <td style={{ padding: '6px 0 6px 8px', whiteSpace: 'nowrap', color: C.muted }}>{fmtWhen(f.seen_at)}</td>
                  <td style={{ padding: '6px 0 6px 8px', whiteSpace: 'nowrap' }}>
                    {f.status === 'held' && canDecide && (<>
                      <button type="button" style={btn} disabled={busy === `d:${f.id}`}
                              onClick={() => run(`d:${f.id}`, async () => { await decideFileDropFile(f.id, 'apply') })}>{t('filedrop.apply_anyway')}</button>{' '}
                      <button type="button" style={btn} disabled={busy === `d:${f.id}`}
                              onClick={() => run(`d:${f.id}`, async () => { await decideFileDropFile(f.id, 'reject') })}>{t('filedrop.reject')}</button>
                    </>)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
