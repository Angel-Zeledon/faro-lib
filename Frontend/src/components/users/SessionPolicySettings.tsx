'use client'
/**
 * Session and password policy - the administrator's side.
 *
 * A collapsed card under the company sign-in card on the users screen,
 * admin-only like the screen. Every limit is a number (or a switch) with an
 * empty / off state that means "not enforced": a company that never opens this
 * card keeps exactly the behaviour it had. The form is a REPLACEMENT of the
 * stored policy, so what it shows after saving is what is enforced.
 *
 * Below the form, the accounts the lockout setting has locked right now, each
 * with an unlock button (resetting a password also unlocks, and a lock ends by
 * itself after its period).
 */
import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ShieldCheck, AlertTriangle, CheckCircle2 } from 'lucide-react'
import {
  getSessionPolicy, saveSessionPolicy, resetSessionPolicy, unlockSessionPolicyUser,
  type SessionPolicySettings, type SessionPolicyView,
} from '@/lib/api'
import Card from '@/components/ui/Card'
import Input, { Field } from '@/components/ui/Input'
import { useLanguage } from '@/contexts/LanguageContext'
import { localeFor } from '@/lib/numberLocale'

type IntField =
  | 'max_session_hours' | 'idle_timeout_minutes' | 'min_password_length'
  | 'password_max_age_days' | 'max_concurrent_sessions' | 'lockout_threshold' | 'lockout_minutes'

/** Form state: numbers are kept as text so "empty" stays distinct from 0. */
type Draft = Record<IntField, string> & { require_mixed_case: boolean; require_symbol: boolean }

const SECTIONS: { title: string; fields: { name: IntField; unit: string }[]; flags?: ('require_mixed_case' | 'require_symbol')[] }[] = [
  {
    title: 'session_policy.section_sessions',
    fields: [
      { name: 'max_session_hours', unit: 'hours' },
      { name: 'idle_timeout_minutes', unit: 'minutes' },
      { name: 'max_concurrent_sessions', unit: 'sessions' },
    ],
  },
  {
    title: 'session_policy.section_passwords',
    fields: [
      { name: 'min_password_length', unit: 'characters' },
      { name: 'password_max_age_days', unit: 'days' },
    ],
    flags: ['require_mixed_case', 'require_symbol'],
  },
  {
    title: 'session_policy.section_lockout',
    fields: [
      { name: 'lockout_threshold', unit: 'attempts' },
      { name: 'lockout_minutes', unit: 'minutes' },
    ],
  },
]

function draftFrom(p: SessionPolicySettings): Draft {
  const text = (v: number | null) => (v === null ? '' : String(v))
  return {
    max_session_hours: text(p.max_session_hours),
    idle_timeout_minutes: text(p.idle_timeout_minutes),
    min_password_length: text(p.min_password_length),
    password_max_age_days: text(p.password_max_age_days),
    max_concurrent_sessions: text(p.max_concurrent_sessions),
    lockout_threshold: text(p.lockout_threshold),
    lockout_minutes: text(p.lockout_minutes),
    require_mixed_case: p.require_mixed_case,
    require_symbol: p.require_symbol,
  }
}

export function SessionPolicyCard() {
  const { t, lang } = useLanguage()
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<SessionPolicyView | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const apply = useCallback((v: SessionPolicyView) => {
    setView(v)
    setDraft(draftFrom(v.policy))
  }, [])

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      apply(await getSessionPolicy())
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : t('users.operation_failed'))
    }
  }, [apply, t])

  useEffect(() => { if (open && !view) void load() }, [open, view, load])

  /** The settings the draft describes, or the first thing wrong with it. */
  function parse(d: Draft): { policy: SessionPolicySettings } | { error: string } {
    const out: Record<string, number | null> = {}
    for (const section of SECTIONS) {
      for (const { name } of section.fields) {
        const raw = d[name].trim()
        if (raw === '') { out[name] = null; continue }
        const b = view?.bounds[name]
        const n = Number(raw)
        if (!/^\d+$/.test(raw) || (b && (n < b.min || n > b.max))) {
          return {
            error: t('session_policy.invalid', {
              field: t(`session_policy.field.${name}`), min: b?.min ?? 1, max: b?.max ?? 1,
            }),
          }
        }
        out[name] = n
      }
    }
    if (out.lockout_minutes !== null && out.lockout_threshold === null) {
      return { error: t('session_policy.lockout_needs_threshold') }
    }
    return {
      policy: {
        ...(out as Omit<SessionPolicySettings, 'require_mixed_case' | 'require_symbol'>),
        require_mixed_case: d.require_mixed_case,
        require_symbol: d.require_symbol,
      },
    }
  }

  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(null); setNote(null)
    try { await fn() } catch (e) {
      setError(e instanceof Error ? e.message : t('users.operation_failed'))
    } finally { setBusy(false) }
  }

  const save = (e: React.FormEvent) => {
    e.preventDefault()
    if (!draft) return
    const parsed = parse(draft)
    if ('error' in parsed) { setError(parsed.error); setNote(null); return }
    void run(async () => {
      apply(await saveSessionPolicy(parsed.policy))
      setNote(t('session_policy.saved'))
    })
  }

  const reset = () => void run(async () => {
    if (!window.confirm(t('session_policy.confirm_reset'))) return
    apply(await resetSessionPolicy())
  })

  const unlock = (userId: string) => void run(async () => {
    await unlockSessionPolicyUser(userId)
    await load()
    setNote(t('session_policy.unlock_done'))
  })

  const when = (iso: string) =>
    new Date(iso).toLocaleString(localeFor(lang), { dateStyle: 'short', timeStyle: 'short' })

  const set = (patch: Partial<Draft>) => setDraft(d => (d ? { ...d, ...patch } : d))
  const lockoutOn = (draft?.lockout_threshold.trim() ?? '') !== ''

  return (
    <div style={{ marginTop: 16 }} data-testid="session-policy-settings">
      <Card padding={0}>
        <button
          type="button" onClick={() => setOpen(v => !v)} aria-expanded={open}
          style={{
            all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10,
            width: '100%', boxSizing: 'border-box', padding: '14px 16px',
          }}
        >
          <ShieldCheck size={15} color="var(--dim)" aria-hidden="true" />
          <span style={{ flex: 1 }}>
            <span style={{ display: 'block', fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{t('session_policy.title')}</span>
            <span style={{ display: 'block', fontSize: 11.5, color: 'var(--dim)', marginTop: 2 }}>{t('session_policy.subtitle')}</span>
          </span>
          <ChevronDown size={14} color="var(--dim)" style={{ transform: open ? 'rotate(180deg)' : undefined }} aria-hidden="true" />
        </button>

        {open && (
          <div style={{ padding: '4px 16px 18px', borderTop: '1px solid var(--border)' }}>
            {loadError && <p role="alert" style={{ fontSize: 12.5, color: '#C0504D' }}>{loadError}</p>}
            {!view && !loadError && <p style={{ fontSize: 12.5, color: 'var(--dim)' }}>{t('session_policy.loading')}</p>}

            {view && draft && (
              <form onSubmit={save} style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 14 }}>
                <p style={{ margin: 0, fontSize: 12, color: 'var(--dim)', lineHeight: 1.5 }}>
                  {t('session_policy.intro')}
                </p>
                <p style={{ margin: 0, fontSize: 12, color: view.is_default ? 'var(--dim)' : 'var(--text)', fontWeight: 600 }}>
                  {view.is_default ? t('session_policy.state_default') : t('session_policy.state_custom')}
                </p>
                {view.is_default && (
                  <p style={{ margin: 0, fontSize: 11.5, color: 'var(--dim)', lineHeight: 1.5 }}>
                    {t('session_policy.defaults_note', {
                      access: view.defaults.access_token_minutes,
                      refresh: view.defaults.refresh_token_days,
                      sessions: view.defaults.max_sessions_per_person,
                      length: view.defaults.min_password_length,
                    })}
                  </p>
                )}

                {SECTIONS.map(section => (
                  <fieldset key={section.title} style={{ border: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
                    <legend style={{ padding: 0, marginBottom: 8, fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}>
                      {t(section.title)}
                    </legend>
                    {section.fields.map(({ name, unit }) => {
                      const b = view.bounds[name]
                      const disabled = name === 'lockout_minutes' && !lockoutOn
                      return (
                        <Field
                          key={name}
                          label={t(`session_policy.field.${name}`)}
                          htmlFor={`sp-${name}`}
                          hint={<>
                            {t(`session_policy.hint.${name}`)}
                            {b ? <> {t('session_policy.range', { min: b.min, max: b.max })}</> : null}
                          </>}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <Input
                              id={`sp-${name}`} name={`sp_${name}`} inputMode="numeric"
                              value={draft[name]} disabled={disabled}
                              placeholder={t('session_policy.off')} style={{ maxWidth: 160 }}
                              onChange={e => set({ [name]: e.target.value } as Partial<Draft>)}
                            />
                            <span style={{ fontSize: 12, color: 'var(--dim)' }}>{t(`session_policy.unit.${unit}`)}</span>
                          </div>
                        </Field>
                      )
                    })}
                    {section.flags?.map(flag => (
                      <label key={flag} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 12.5, color: 'var(--text)' }}>
                        <input
                          type="checkbox" name={`sp_${flag}`} checked={draft[flag]} style={{ marginTop: 2 }}
                          onChange={e => set({ [flag]: e.target.checked } as Partial<Draft>)}
                        />
                        <span>{t(`session_policy.field.${flag}`)}</span>
                      </label>
                    ))}
                  </fieldset>
                ))}

                {error && (
                  <p role="alert" style={{ margin: 0, display: 'flex', gap: 6, alignItems: 'center', fontSize: 12.5, color: '#C0504D' }}>
                    <AlertTriangle size={13} aria-hidden="true" /> {error}
                  </p>
                )}
                {note && !error && (
                  <p style={{ margin: 0, display: 'flex', gap: 6, alignItems: 'center', fontSize: 12.5, color: '#2E8B62' }}>
                    <CheckCircle2 size={13} aria-hidden="true" /> {note}
                  </p>
                )}
                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                  {!view.is_default && (
                    <button type="button" onClick={reset} disabled={busy} style={{
                      padding: '8px 14px', borderRadius: 7, border: '1px solid var(--border)',
                      background: 'transparent', color: 'var(--muted)', fontSize: 13, cursor: 'pointer',
                    }}>{t('session_policy.reset')}</button>
                  )}
                  <button type="submit" disabled={busy} style={{
                    padding: '8px 20px', borderRadius: 7, border: 'none', background: 'var(--accent)',
                    color: '#fff', fontSize: 13, fontWeight: 600, cursor: busy ? 'wait' : 'pointer',
                  }}>{busy ? t('users.saving') : t('session_policy.save')}</button>
                </div>

                {view.policy.lockout_threshold !== null && (
                  <section style={{ borderTop: '1px solid var(--border)', paddingTop: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <h3 style={{ margin: 0, fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}>{t('session_policy.locked_title')}</h3>
                    {view.locked_users.length === 0 && (
                      <p style={{ margin: 0, fontSize: 12, color: 'var(--dim)' }}>{t('session_policy.locked_none')}</p>
                    )}
                    {view.locked_users.map(u => (
                      <div key={u.user_id} style={{ display: 'flex', gap: 10, alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' }}>
                        <span style={{ minWidth: 0 }}>
                          <span style={{ display: 'block', fontSize: 12.5, color: 'var(--text)', wordBreak: 'break-all' }}>{u.full_name || u.email}</span>
                          <span style={{ display: 'block', fontSize: 11.5, color: 'var(--dim)' }}>
                            {u.full_name ? `${u.email} · ` : ''}
                            {t('session_policy.locked_row', { attempts: u.failed_attempts, until: when(u.locked_until) })}
                          </span>
                        </span>
                        <button type="button" onClick={() => unlock(u.user_id)} disabled={busy} style={{
                          padding: '6px 12px', borderRadius: 7, border: '1px solid var(--border)',
                          background: 'transparent', color: 'var(--text)', fontSize: 12.5, cursor: 'pointer',
                        }}>{t('session_policy.unlock')}</button>
                      </div>
                    ))}
                  </section>
                )}
              </form>
            )}
          </div>
        )}
      </Card>
    </div>
  )
}
