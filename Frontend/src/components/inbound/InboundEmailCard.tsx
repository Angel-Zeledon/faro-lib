'use client'
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { Check, Copy, Mail, RefreshCw, X } from 'lucide-react'
import {
  getInboundEmail, regenerateInboundEmail, setInboundEmailSenders,
} from '@/lib/api'
import type { InboundEmailMessage, InboundEmailState, InboundOutcome } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useErrorDetail } from '@/components/ui/States'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'

const OUTCOME_VARIANT: Record<string, 'success' | 'warning' | 'danger' | 'muted'> = {
  ingested: 'success',
  needs_review: 'warning',
  rejected: 'danger',
  processing: 'muted',
}

/**
 * Sales by e-mail: the private address of this account, who may write to it,
 * and what happened to the last messages. Admin only (the address is a
 * credential); the hub decides who sees it.
 *
 * With no inbound provider configured it says so plainly instead of showing an
 * address that could never receive anything.
 */
export default function InboundEmailCard() {
  const { t, lang } = useLanguage()
  const { addToast } = useToast()
  const confirm = useConfirm()
  const errorDetail = useErrorDetail()

  const [state, setState] = useState<InboundEmailState | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [senders, setSenders] = useState<string[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState<'save' | 'regen' | null>(null)
  const [copied, setCopied] = useState(false)

  const apply = useCallback((s: InboundEmailState) => {
    setState(s)
    setSenders(s.allowed_senders)
  }, [])

  const load = useCallback(async () => {
    try { apply(await getInboundEmail()); setLoadErr(null) }
    catch (e) { setLoadErr(errorDetail(e) || t('inbound.load_failed')) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apply, t])

  useEffect(() => { void load() }, [load])

  if (loadErr) {
    return <div role="alert" style={{ padding: '14px 16px', fontSize: 13, color: '#C0504D' }}>{loadErr}</div>
  }
  if (!state) {
    return <div style={{ padding: 20, display: 'flex', justifyContent: 'center' }}><Spinner size={18} /></div>
  }

  const copy = async () => {
    if (!state.address) return
    try {
      await navigator.clipboard.writeText(state.address)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      addToast(t('inbound.copy_failed'), state.address, 'error')
    }
  }

  const addDraft = () => {
    const v = draft.trim().toLowerCase()
    if (!v) return
    if (!senders.includes(v)) setSenders([...senders, v])
    setDraft('')
  }

  const dirty = senders.join('|') !== state.allowed_senders.join('|')

  const save = async () => {
    setBusy('save')
    try {
      apply(await setInboundEmailSenders(senders))
      addToast(t('inbound.senders_saved'), '', 'success')
    } catch (e) {
      addToast(t('inbound.senders_failed'), errorDetail(e), 'error')
    } finally { setBusy(null) }
  }

  const regenerate = async () => {
    if (!(await confirm({
      title: t('inbound.regen_confirm_title'),
      message: t('inbound.regen_confirm_body'),
      confirmLabel: t('inbound.regen_confirm_cta'),
      danger: true,
    }))) return
    setBusy('regen')
    try {
      apply(await regenerateInboundEmail())
      addToast(t('inbound.regen_done'), '', 'success')
    } catch (e) {
      addToast(t('inbound.regen_failed'), errorDetail(e), 'error')
    } finally { setBusy(null) }
  }

  const reasonText = (m: InboundEmailMessage): string => {
    if (!m.reason) return ''
    const key = `inbound.reason.${m.reason}`
    const text = t(key, m.reason_params as Record<string, string | number>)
    return text === key ? t('inbound.reason.unknown') : text
  }

  const when = (iso: string) =>
    new Date(iso).toLocaleString(lang === 'es' ? 'es' : 'en', { dateStyle: 'short', timeStyle: 'short' })

  return (
    <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <span aria-hidden="true" style={{
          width: 36, height: 36, borderRadius: 9, flexShrink: 0,
          background: 'var(--accent-dim)', color: 'var(--accent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}><Mail size={18} strokeWidth={1.7} /></span>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>{t('inbound.title')}</div>
          <div style={{ marginTop: 2, fontSize: 13, color: 'var(--muted)', lineHeight: 1.4 }}>{t('inbound.lead')}</div>
        </div>
      </div>

      {!state.enabled ? (
        <p role="status" style={{ margin: 0, fontSize: 13, color: 'var(--muted)', lineHeight: 1.5 }}>
          {t('inbound.disabled')}
        </p>
      ) : (
        <>
          <div>
            <div style={LABEL}>{t('inbound.address_label')}</div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <code style={{
                flex: '1 1 220px', minWidth: 0, overflowWrap: 'anywhere', fontSize: 13,
                padding: '8px 10px', borderRadius: 7, background: 'var(--surface-2)',
                border: '1px solid var(--border)',
              }}>{state.address}</code>
              <Button size="sm" onClick={copy} icon={copied ? <Check size={14} /> : <Copy size={14} />}>
                {copied ? t('inbound.copied') : t('inbound.copy')}
              </Button>
            </div>
          </div>

          <div>
            <div style={LABEL}>{t('inbound.senders_title')}</div>
            <p style={{ margin: '0 0 8px', fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.5 }}>
              {t('inbound.senders_help')}
            </p>
            {senders.length > 0 && (
              <ul style={{ listStyle: 'none', margin: '0 0 8px', padding: 0, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {senders.map(s => (
                  <li key={s} style={{
                    display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.5,
                    padding: '3px 4px 3px 9px', borderRadius: 999, border: '1px solid var(--border)',
                    background: 'var(--surface-2)', maxWidth: '100%', overflowWrap: 'anywhere',
                  }}>
                    {s}
                    <button type="button" aria-label={t('inbound.senders_remove', { email: s })}
                      onClick={() => setSenders(senders.filter(x => x !== s))}
                      style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--dim)', display: 'flex', padding: 2 }}>
                      <X size={13} aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <input
                type="email" name="inbound_sender" value={draft}
                aria-label={t('inbound.senders_title')}
                placeholder={t('inbound.senders_placeholder')}
                onChange={e => setDraft(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addDraft() } }}
                className="form-input"
                style={{ flex: '1 1 200px', minWidth: 0, fontSize: 16, padding: '8px 12px', borderRadius: 7 }}
              />
              <Button size="sm" onClick={addDraft} disabled={!draft.trim()}>{t('inbound.senders_add')}</Button>
              <Button size="sm" variant="primary" onClick={save} disabled={!dirty || busy !== null} loading={busy === 'save'}>
                {t('inbound.senders_save')}
              </Button>
            </div>
          </div>
        </>
      )}

      <div>
        <div style={LABEL}>{t('inbound.messages_title')}</div>
        {state.messages.length === 0 ? (
          <p style={{ margin: 0, fontSize: 13, color: 'var(--muted)' }}>{t('inbound.messages_empty')}</p>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column' }}>
            {state.messages.map(m => (
              <li key={m.id} style={{ padding: '9px 0', borderTop: '1px solid var(--border)', minWidth: 0 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <Badge variant={OUTCOME_VARIANT[m.outcome] ?? 'muted'}>
                    {t(`inbound.outcome.${m.outcome as InboundOutcome}`)}
                  </Badge>
                  <span style={{ fontSize: 13, fontWeight: 500, overflowWrap: 'anywhere', minWidth: 0 }}>
                    {m.filename || t('inbound.no_file')}
                  </span>
                </div>
                <div style={{ marginTop: 3, fontSize: 12, color: 'var(--muted)', overflowWrap: 'anywhere' }}>
                  {m.sender || '-'} · {when(m.received_at)}
                </div>
                {m.reason && (
                  <div style={{ marginTop: 3, fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.45 }}>
                    {reasonText(m)}
                  </div>
                )}
                {m.outcome === 'needs_review' && (
                  <Link href="/ventas" style={{ display: 'inline-block', marginTop: 4, fontSize: 12.5, color: 'var(--accent)', fontWeight: 600 }}>
                    {t('inbound.review_cta')}
                  </Link>
                )}
                {m.outcome === 'ingested' && m.retrain && m.retrain !== 'none' && (
                  <div style={{ marginTop: 3, fontSize: 12.5, color: 'var(--muted)' }}>
                    {t(`inbound.retrain.${m.retrain}`)}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {state.enabled && (
        <div>
          <Button size="sm" variant="danger" onClick={regenerate} loading={busy === 'regen'}
            disabled={busy !== null} icon={<RefreshCw size={14} />}>
            {t('inbound.regen')}
          </Button>
        </div>
      )}
    </div>
  )
}

const LABEL: React.CSSProperties = {
  fontSize: 11.5, fontWeight: 700, color: 'var(--dim)', textTransform: 'uppercase',
  letterSpacing: '0.05em', marginBottom: 6,
}
