'use client'
/**
 * PENDIENTES #1: instead of wiring Faro to every supplier's WhatsApp, the
 * order is delivered to the BUYER, who forwards it. Works with zero Twilio
 * configuration — the endpoint always returns the message text and a wa.me
 * deep link, so "open in WhatsApp" and "copy" never depend on delivery.
 */
import { useState } from 'react'
import { sendPOToSelf } from '@/lib/api'
import { useErrorDetail } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { Check, Copy, MessageCircle, Smartphone } from 'lucide-react'

const C = {
  border: 'var(--border)', text: 'var(--text)', dim: 'var(--dim)',
  green: '#22c55e', red: '#ef4444',
}

const btn: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center',
  gap: 4, padding: '3px 10px', borderRadius: 7, fontSize: 11, fontWeight: 600,
  border: `1px solid ${C.border}`, color: C.text,
}

export function ForwardPOActions({ poLogId }: { poLogId: string }) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const [busy,    setBusy]    = useState(false)
  const [payload, setPayload] = useState<{ text: string; url: string } | null>(null)
  const [note,    setNote]    = useState<{ ok: boolean; msg: string } | null>(null)
  const [copied,  setCopied]  = useState(false)

  async function fetchMessage() {
    if (busy) return null
    setBusy(true)
    setNote(null)
    try {
      const res = await sendPOToSelf(poLogId)
      const next = { text: res.message_text, url: res.wa_me_url }
      setPayload(next)
      setNote({
        ok: true,
        msg: res.sent ? t('po.forward_sent') : t('po.forward_no_number'),
      })
      return next
    } catch (e: unknown) {
      setNote({ ok: false, msg: errorDetail(e) || t('common.error') })
      return null
    } finally {
      setBusy(false)
    }
  }

  async function copyText() {
    const data = payload ?? await fetchMessage()
    if (!data) return
    try {
      await navigator.clipboard.writeText(data.text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      setNote({ ok: false, msg: t('po.forward_copy_failed') })
    }
  }

  async function openWhatsApp() {
    const data = payload ?? await fetchMessage()
    if (data) window.open(data.url, '_blank', 'noopener')
  }

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
      <button onClick={fetchMessage} disabled={busy} style={btn} title={t('po.forward_to_me_title')}>
        <Smartphone size={11} aria-hidden="true" />
        {busy ? t('po.forward_sending') : t('po.forward_to_me')}
      </button>
      <button onClick={openWhatsApp} disabled={busy} style={btn}>
        <MessageCircle size={11} aria-hidden="true" />
        {t('po.forward_open_whatsapp')}
      </button>
      <button onClick={copyText} disabled={busy} style={btn}>
        {copied ? <Check size={11} aria-hidden="true" /> : <Copy size={11} aria-hidden="true" />}
        {copied ? t('po.forward_copied') : t('po.forward_copy')}
      </button>
      {note && (
        <span style={{ fontSize: 11, fontWeight: 600, color: note.ok ? C.green : C.red }}>
          {note.msg}
        </span>
      )}
    </span>
  )
}
