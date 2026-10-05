'use client'
/**
 * The connection form for a SQL data source — create and edit.
 *
 * What it adds over a bare host/port/user/password form, each because a real
 * connection attempt failed without it:
 *  - per-engine fields and hints (Oracle's service name, SQL Server's named
 *    instance) and the engine's default port;
 *  - paste a connection string (URL, JDBC, ADO.NET, libpq): the SERVER parses
 *    it (one parser for UI, API and tests) and the password never comes back
 *    to the browser — the string itself is sent again on save;
 *  - the TLS mode, limited to what the engine's driver can honour, and an
 *    optional CA certificate (stored encrypted, shown as subject + expiry);
 *  - timeouts, tucked under "advanced";
 *  - on edit, nothing has to be typed twice — except the password when the
 *    engine, host or port change, which the form says BEFORE the server
 *    refuses it (the stored password is never sent to a new server).
 */
import { useId, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, ClipboardPaste, FileKey2, Save, ShieldCheck, X } from 'lucide-react'
import Input, { FieldLabel, Select } from '@/components/ui/Input'
import Spinner from '@/components/ui/Spinner'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { useErrorDetail } from '@/components/ui/States'
import { parseConnectionString, type SqlConnectionBody } from '@/lib/api'
import type { DataSource, SqlEngine, SqlSslMode } from '@/lib/types'
import {
  CA_ENGINES, DEFAULT_PORT, DEFAULT_SSL_MODE, ENGINES, MAX_CA_BYTES, SSL_MODES, TIMEOUTS,
  engineLabelKey,
} from './sqlEngines'

const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', inset: 'var(--surface-3)',
  border: 'var(--border)', border2: 'var(--border-strong)',
  text: 'var(--text)', muted: 'var(--muted)', green: 'var(--accent)',
  amber: 'var(--warning)', red: 'var(--danger)', blue: 'var(--info)',
}
const MONO = "ui-monospace, 'JetBrains Mono', 'SF Mono', 'Cascadia Mono', Consolas, monospace"

export interface SqlFormSubmit {
  name?: string
  description?: string
  body: SqlConnectionBody & { clear_ssl_ca?: boolean }
}

interface Props {
  mode: 'create' | 'edit'
  source?: DataSource
  saving?: boolean
  onSubmit: (data: SqlFormSubmit) => void | Promise<void>
  onCancel?: () => void
}

interface FormState {
  name: string; description: string
  engine: SqlEngine; host: string; port: string; database: string
  username: string; password: string
  sslMode: SqlSslMode
  connectTimeout: string; statementTimeout: string
}

const hostKey = (h: string) => h.trim().toLowerCase().replace(/^\[|\]$/g, '').replace(/\.$/, '')

export default function SqlConnectionForm({ mode, source, saving, onSubmit, onCancel }: Props) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const narrow = useIsNarrow()
  const uid = useId()
  const fid = (k: string) => `sqlc-${k}-${uid}`
  const cfg = source?.sql_config ?? null
  const isEdit = mode === 'edit'

  const initial: FormState = useMemo(() => {
    const engine = (cfg?.engine ?? 'postgresql') as SqlEngine
    return {
      name: '', description: '',
      engine,
      host: cfg?.host ?? '',
      port: String(cfg?.port ?? DEFAULT_PORT[engine]),
      database: cfg?.database ?? '',
      username: cfg?.username ?? '',
      password: '',
      sslMode: (cfg?.ssl_mode ?? DEFAULT_SSL_MODE[engine]) as SqlSslMode,
      connectTimeout: String(cfg?.connect_timeout_s ?? TIMEOUTS.connect.def),
      statementTimeout: String(cfg?.statement_timeout_s ?? TIMEOUTS.statement.def),
    }
  }, [cfg])

  const [form, setForm] = useState<FormState>(initial)
  const [localErr, setLocalErr] = useState<string | null>(null)
  // Paste a connection string
  const [pasteOpen, setPasteOpen] = useState(false)
  const [pasteText, setPasteText] = useState('')
  const [parsing, setParsing] = useState(false)
  const [pasteMsg, setPasteMsg] = useState<{ ok: boolean; text: string } | null>(null)
  // The string is sent again on save ONLY to carry its password: the server
  // never returns the password, so this is the one way it reaches the form.
  const [csWithPassword, setCsWithPassword] = useState<string | null>(null)
  // CA certificate
  const caInput = useRef<HTMLInputElement>(null)
  const [caPem, setCaPem] = useState<string | null>(null)
  const [caName, setCaName] = useState<string | null>(null)
  const [clearCa, setClearCa] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm(f => ({ ...f, [k]: v }))

  const changeEngine = (engine: SqlEngine) => setForm(f => {
    const portWasDefault = !f.port.trim() || f.port === String(DEFAULT_PORT[f.engine])
    const sslOk = SSL_MODES[engine].includes(f.sslMode)
    return {
      ...f, engine,
      port: portWasDefault ? String(DEFAULT_PORT[engine]) : f.port,
      sslMode: sslOk ? f.sslMode : DEFAULT_SSL_MODE[engine],
    }
  })

  // On edit: a different engine, host or port means the stored password would
  // be sent to a new server. The backend refuses that; say it here first.
  const retargeted = isEdit && !!cfg && (
    form.engine !== cfg.engine
    || hostKey(form.host) !== hostKey(cfg.host ?? '')
    || Number(form.port) !== Number(cfg.port))
  const passwordMissing = retargeted && !form.password && !csWithPassword

  const caSupported = CA_ENGINES.includes(form.engine)
  const storedCa = cfg?.has_ssl_ca && cfg.ssl_ca && !clearCa ? cfg.ssl_ca : null

  const applyPaste = async () => {
    if (!pasteText.trim() || parsing) return
    setParsing(true); setPasteMsg(null)
    try {
      const p = await parseConnectionString(pasteText.trim())
      setForm(f => {
        const engine = (p.engine ?? f.engine) as SqlEngine
        return {
          ...f,
          engine,
          host: p.host ?? f.host,
          port: p.port ? String(p.port) : (p.engine && p.engine !== f.engine ? String(DEFAULT_PORT[engine]) : f.port),
          database: p.database ?? f.database,
          username: p.username ?? f.username,
          sslMode: (p.ssl_mode && SSL_MODES[engine].includes(p.ssl_mode))
            ? p.ssl_mode
            : (SSL_MODES[engine].includes(f.sslMode) ? f.sslMode : DEFAULT_SSL_MODE[engine]),
        }
      })
      setCsWithPassword(p.has_password ? pasteText.trim() : null)
      setPasteMsg({ ok: true, text: p.has_password
        ? `${t('data.conn.paste_applied')} ${t('data.conn.paste_password_kept')}`
        : t('data.conn.paste_applied') })
    } catch (e) {
      setPasteMsg({ ok: false, text: errorDetail(e) })
    } finally { setParsing(false) }
  }

  const pickCa = (file: File | undefined) => {
    if (!file) return
    if (file.size > MAX_CA_BYTES) {
      setLocalErr(t('errors.data_source_ssl_ca_invalid'))
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      setCaPem(String(reader.result ?? ''))
      setCaName(file.name)
      setClearCa(false)
      setLocalErr(null)
    }
    reader.onerror = () => setLocalErr(t('data.conn.ssl_ca_read_failed'))
    reader.readAsText(file)
  }

  const submit = async () => {
    setLocalErr(null)
    const missing: string[] = []
    if (!isEdit && !form.name.trim()) missing.push(t('data.field_source_name'))
    if (!form.host.trim()) missing.push(t('data.field_host'))
    if (!form.database.trim()) missing.push(form.engine === 'oracle' ? t('data.conn.database_oracle') : t('data.field_database'))
    if (!form.username.trim()) missing.push(t('data.field_username'))
    if (missing.length) { setLocalErr(t('data.conn.required_missing', { fields: missing.join(', ') })); return }
    const port = Number(form.port)
    if (!Number.isInteger(port) || port < 1 || port > 65535) { setLocalErr(t('data.conn.port_invalid')); return }
    if (passwordMissing) { setLocalErr(t('data.conn.password_new_required')); return }
    const toInt = (v: string, d: number) => { const n = Number(v); return Number.isFinite(n) && v.trim() ? Math.round(n) : d }

    const body: SqlFormSubmit['body'] = {
      engine: form.engine,
      host: form.host.trim(),
      port,
      database: form.database.trim(),
      username: form.username.trim(),
      ssl_mode: form.sslMode,
      connect_timeout_s: toInt(form.connectTimeout, TIMEOUTS.connect.def),
      statement_timeout_s: toInt(form.statementTimeout, TIMEOUTS.statement.def),
    }
    if (form.password) body.password = form.password
    if (csWithPassword && !form.password) body.connection_string = csWithPassword
    if (caPem && caSupported) body.ssl_ca = caPem
    if (isEdit && clearCa && !caPem) body.clear_ssl_ca = true
    await onSubmit({
      name: isEdit ? undefined : form.name.trim(),
      description: isEdit ? undefined : (form.description.trim() || undefined),
      body,
    })
  }

  const cols = (n: number) => narrow ? 'minmax(0, 1fr)' : `repeat(${n}, minmax(0, 1fr))`
  const hint: React.CSSProperties = { color: C.muted, fontSize: 11.5, lineHeight: 1.5, margin: '5px 0 0' }
  const sectionToggle: React.CSSProperties = {
    display: 'flex', alignItems: 'center', gap: 6, background: 'transparent', border: 'none',
    color: C.muted, fontSize: 12, fontWeight: 600, cursor: 'pointer', padding: '4px 0',
    ...(narrow ? { minHeight: 44 } : {}),
  }
  const ghostBtn: React.CSSProperties = {
    padding: '7px 12px', borderRadius: 8, background: 'transparent',
    border: `1px solid ${C.border2}`, color: C.muted, fontWeight: 600, fontSize: 12,
    cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6,
    ...(narrow ? { minHeight: 44 } : {}),
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {!isEdit && (
        <div style={{ display: 'grid', gridTemplateColumns: cols(2), gap: 12 }}>
          <div>
            <FieldLabel htmlFor={fid('name')}>{t('data.field_source_name')} *</FieldLabel>
            <Input id={fid('name')} name="name" size="lg" tone="surface" border="strong"
              value={form.name} onChange={e => set('name', e.target.value)} placeholder={t('data.field_source_name_ph')} />
          </div>
          <div>
            <FieldLabel htmlFor={fid('description')}>{t('data.field_description')}</FieldLabel>
            <Input id={fid('description')} name="description" size="lg" tone="surface" border="strong"
              value={form.description} onChange={e => set('description', e.target.value)} placeholder={t('data.field_optional_ph')} />
          </div>
        </div>
      )}

      {/* Paste a connection string */}
      <div style={{ border: `1px solid ${C.border}`, borderRadius: 10, background: C.surface }}>
        <button type="button" onClick={() => setPasteOpen(o => !o)} aria-expanded={pasteOpen}
          style={{ ...sectionToggle, width: '100%', padding: '10px 12px', color: C.text }}>
          {pasteOpen ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
          <ClipboardPaste size={14} aria-hidden="true" /> {t('data.conn.paste_title')}
        </button>
        {pasteOpen && (
          <div style={{ padding: '0 12px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <p style={{ ...hint, margin: 0 }}>{t('data.conn.paste_hint')}</p>
            <div style={{ display: 'flex', gap: 8, flexDirection: narrow ? 'column' : 'row' }}>
              <Input id={fid('cs')} name="connection_string" aria-label={t('data.conn.paste_title')}
                size="lg" tone="surface" border="strong" autoComplete="off" spellCheck={false}
                value={pasteText} onChange={e => { setPasteText(e.target.value); setPasteMsg(null) }}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); applyPaste() } }}
                placeholder={t('data.conn.paste_placeholder')}
                style={{ fontFamily: MONO, fontSize: narrow ? 16 : 12, flex: 1, minWidth: 0 }} />
              <button type="button" onClick={applyPaste} disabled={parsing || !pasteText.trim()} style={ghostBtn}>
                {parsing ? <Spinner size={12} /> : null} {t('data.conn.paste_apply')}
              </button>
            </div>
            {pasteMsg && (
              <p role="status" style={{ ...hint, margin: 0, color: pasteMsg.ok ? C.green : C.red }}>{pasteMsg.text}</p>
            )}
          </div>
        )}
      </div>

      {/* Where */}
      <div style={{ display: 'grid', gridTemplateColumns: narrow ? 'minmax(0,1fr)' : 'minmax(0,1fr) minmax(0,1.6fr) minmax(0,0.7fr)', gap: 12 }}>
        <div>
          <FieldLabel htmlFor={fid('engine')}>{t('data.field_engine')} *</FieldLabel>
          <Select id={fid('engine')} name="engine" size="lg" tone="surface" border="strong"
            value={form.engine} onChange={e => changeEngine(e.target.value as SqlEngine)}>
            {ENGINES.map(e => <option key={e} value={e}>{t(engineLabelKey(e))}</option>)}
          </Select>
        </div>
        <div>
          <FieldLabel htmlFor={fid('host')}>{t('data.field_host')} *</FieldLabel>
          <Input id={fid('host')} name="host" size="lg" tone="surface" border="strong" autoComplete="off"
            spellCheck={false} value={form.host} onChange={e => set('host', e.target.value)}
            placeholder={form.engine === 'mssql' ? 'erp.example.com\\SQLEXPRESS' : 'db.example.com'}
            aria-describedby={fid('host-hint')} />
          <p id={fid('host-hint')} style={hint}>
            {form.engine === 'mssql' ? t('data.conn.host_hint_mssql') : t('data.conn.host_hint')}
          </p>
        </div>
        <div>
          <FieldLabel htmlFor={fid('port')}>{t('data.field_port')} *</FieldLabel>
          <Input id={fid('port')} name="port" size="lg" tone="surface" border="strong"
            value={form.port} onChange={e => set('port', e.target.value.replace(/[^0-9]/g, ''))}
            inputMode="numeric" placeholder={String(DEFAULT_PORT[form.engine])} />
        </div>
      </div>

      {/* Who */}
      <div style={{ display: 'grid', gridTemplateColumns: cols(3), gap: 12 }}>
        <div>
          <FieldLabel htmlFor={fid('database')}>
            {form.engine === 'oracle' ? t('data.conn.database_oracle') : t('data.field_database')} *
          </FieldLabel>
          <Input id={fid('database')} name="database" size="lg" tone="surface" border="strong" autoComplete="off"
            value={form.database} onChange={e => set('database', e.target.value)}
            placeholder={form.engine === 'oracle' ? 'ORCLPDB1' : t('data.field_database_ph')} />
          {form.engine === 'oracle' && <p style={hint}>{t('data.conn.database_hint_oracle')}</p>}
        </div>
        <div>
          <FieldLabel htmlFor={fid('username')}>{t('data.field_username')} *</FieldLabel>
          <Input id={fid('username')} name="username" size="lg" tone="surface" border="strong" autoComplete="off"
            value={form.username} onChange={e => set('username', e.target.value)} placeholder={t('data.field_username_ph')} />
        </div>
        <div>
          <FieldLabel htmlFor={fid('password')}>{t('data.field_password')}</FieldLabel>
          <Input id={fid('password')} name="password" size="lg" tone="surface" border="strong"
            type="password" autoComplete="new-password" invalid={passwordMissing}
            value={form.password} onChange={e => set('password', e.target.value)}
            placeholder={isEdit && cfg?.has_password ? '••••••••' : ''}
            aria-describedby={fid('password-hint')} />
          <p id={fid('password-hint')} style={{ ...hint, color: passwordMissing ? C.red : C.muted }}>
            {passwordMissing
              ? t('data.conn.password_new_required')
              : isEdit && cfg?.has_password ? t('data.conn.password_stored') : ''}
          </p>
        </div>
      </div>
      <p style={{ ...hint, margin: '-6px 0 0', display: 'flex', gap: 6, alignItems: 'flex-start' }}>
        <ShieldCheck size={13} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2, color: C.green }} />
        {t('data.conn.readonly_advice')}
      </p>

      {/* Security */}
      <fieldset style={{ border: `1px solid ${C.border}`, borderRadius: 10, padding: '10px 12px 12px', margin: 0, minWidth: 0 }}>
        <legend style={{ padding: '0 6px', color: C.muted, fontSize: 11, fontWeight: 700,
          textTransform: 'uppercase', letterSpacing: '0.06em' }}>{t('data.conn.section_security')}</legend>
        <div style={{ display: 'grid', gridTemplateColumns: cols(2), gap: 12 }}>
          <div>
            <FieldLabel htmlFor={fid('ssl')}>{t('data.conn.field_ssl_mode')}</FieldLabel>
            <Select id={fid('ssl')} name="ssl_mode" size="lg" tone="surface" border="strong"
              value={form.sslMode} onChange={e => set('sslMode', e.target.value as SqlSslMode)}>
              {SSL_MODES[form.engine].map(m => <option key={m} value={m}>{t(`data.conn.ssl.${m}`)}</option>)}
            </Select>
            <p style={hint}>{t('data.conn.ssl_hint')}</p>
          </div>
          <div>
            <FieldLabel htmlFor={fid('ca')}>{t('data.conn.field_ssl_ca')}</FieldLabel>
            {!caSupported ? (
              <p style={{ ...hint, marginTop: 8 }}>{t('data.conn.ssl_ca_unsupported')}</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <input ref={caInput} id={fid('ca')} type="file" accept=".pem,.crt,.cer,.ca-bundle,text/plain"
                  style={{ display: 'none' }} onChange={e => { pickCa(e.target.files?.[0]); e.target.value = '' }} />
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button type="button" style={ghostBtn} onClick={() => caInput.current?.click()}>
                    <FileKey2 size={13} aria-hidden="true" />
                    {storedCa || caPem ? t('data.conn.ssl_ca_replace') : t('data.conn.ssl_ca_upload')}
                  </button>
                  {(storedCa || caPem) && (
                    <button type="button" style={ghostBtn}
                      onClick={() => { setCaPem(null); setCaName(null); if (isEdit) setClearCa(true) }}>
                      <X size={13} aria-hidden="true" /> {t('data.conn.ssl_ca_remove')}
                    </button>
                  )}
                </div>
                {caPem && caName && <p style={{ ...hint, margin: 0 }}>{t('data.conn.ssl_ca_pending', { name: caName })}</p>}
                {!caPem && storedCa && (
                  <p style={{ ...hint, margin: 0, overflowWrap: 'anywhere' }}>
                    {t('data.conn.ssl_ca_stored', { subject: storedCa.subject, expires: storedCa.expires })}
                  </p>
                )}
                {!caPem && isEdit && clearCa && <p style={{ ...hint, margin: 0, color: C.amber }}>{t('data.conn.ssl_ca_will_remove')}</p>}
              </div>
            )}
          </div>
        </div>
      </fieldset>

      {/* Advanced */}
      <div>
        <button type="button" onClick={() => setAdvancedOpen(o => !o)} aria-expanded={advancedOpen} style={sectionToggle}>
          {advancedOpen ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
          {t('data.conn.section_advanced')}
        </button>
        {advancedOpen && (
          <div style={{ display: 'grid', gridTemplateColumns: cols(2), gap: 12, marginTop: 8 }}>
            <div>
              <FieldLabel htmlFor={fid('ct')}>{t('data.conn.field_connect_timeout')}</FieldLabel>
              <Input id={fid('ct')} name="connect_timeout_s" size="lg" tone="surface" border="strong" inputMode="numeric"
                value={form.connectTimeout} onChange={e => set('connectTimeout', e.target.value.replace(/[^0-9]/g, ''))}
                placeholder={`${TIMEOUTS.connect.min}–${TIMEOUTS.connect.max}`} />
            </div>
            <div>
              <FieldLabel htmlFor={fid('st')}>{t('data.conn.field_statement_timeout')}</FieldLabel>
              <Input id={fid('st')} name="statement_timeout_s" size="lg" tone="surface" border="strong" inputMode="numeric"
                value={form.statementTimeout} onChange={e => set('statementTimeout', e.target.value.replace(/[^0-9]/g, ''))}
                placeholder={`${TIMEOUTS.statement.min}–${TIMEOUTS.statement.max}`} />
              <p style={hint}>{t('data.conn.timeouts_hint')}</p>
            </div>
          </div>
        )}
      </div>

      {localErr && (
        <div role="alert" style={{ background: C.surface, border: '1px solid color-mix(in srgb, var(--danger) 38%, transparent)',
          borderLeft: '3px solid var(--danger)', borderRadius: 8, padding: '9px 12px', color: C.red, fontSize: 12.5 }}>
          {localErr}
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', ...(narrow ? { flexDirection: 'column-reverse' } : {}) }}>
        {onCancel && (
          <button type="button" className="btn" onClick={onCancel} style={{ ...ghostBtn, padding: '9px 18px', fontSize: 13, justifyContent: 'center' }}>
            {t('common.cancel')}
          </button>
        )}
        <button type="button" className="btn" onClick={submit} disabled={saving}
          style={{ padding: '9px 20px', borderRadius: 8, background: C.green, border: 'none', color: '#fff',
            fontWeight: 600, fontSize: 13, cursor: saving ? 'not-allowed' : 'pointer', opacity: saving ? 0.7 : 1,
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            ...(narrow ? { minHeight: 48, borderRadius: 12 } : {}) }}>
          {saving ? <Spinner size={14} /> : <Save size={14} aria-hidden="true" />}
          {isEdit ? t('data.btn_save_changes') : t('data.btn_create_connection')}
        </button>
      </div>
    </div>
  )
}
