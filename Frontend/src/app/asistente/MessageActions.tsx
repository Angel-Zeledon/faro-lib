'use client'
/**
 * The two per-message actions of /asistente: copy as plain text, and star.
 * Shared by the message bubble and the favorites list, so a copy behaves the
 * same wherever it is offered. Visibility rules (hover on a mouse, always on a
 * phone) live in globals.css under `.msg-action-btn`.
 */
import { useEffect, useRef, useState } from 'react'
import { Check, Copy, Star } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { copyText } from '@/lib/chatText'

export function CopyButton({ getText }: { getText: () => string }) {
  const { t } = useLanguage()
  const [state, setState] = useState<'idle' | 'ok' | 'fail'>('idle')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])

  const onClick = async () => {
    const ok = await copyText(getText())
    setState(ok ? 'ok' : 'fail')
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setState('idle'), 1800)
  }

  return (
    <>
      <button
        type="button"
        className="msg-action-btn"
        data-testid="message-copy"
        data-on={state === 'ok' ? 'true' : undefined}
        onClick={onClick}
        aria-label={t('analyst.copy_message')}
        title={t('analyst.copy_message')}
        style={state === 'ok' ? { color: 'var(--accent)' } : undefined}
      >
        {state === 'ok'
          ? <Check size={14} aria-hidden="true" />
          : <Copy size={14} aria-hidden="true" />}
      </button>
      {/* Inline confirmation, announced politely; empty (and silent) at rest. */}
      <span role="status" aria-live="polite" className="msg-action-note">
        {state === 'ok' ? t('analyst.copied') : state === 'fail' ? t('analyst.copy_failed') : ''}
      </span>
    </>
  )
}

export function StarButton({ starred, onToggle }: { starred: boolean; onToggle: () => void }) {
  const { t } = useLanguage()
  const label = starred ? t('analyst.unstar_message') : t('analyst.star_message')
  return (
    <button
      type="button"
      className="msg-action-btn"
      data-testid="message-star"
      data-on={starred ? 'true' : undefined}
      aria-pressed={starred}
      aria-label={label}
      title={label}
      onClick={onToggle}
    >
      <Star size={14} aria-hidden="true" fill={starred ? 'currentColor' : 'none'} />
    </button>
  )
}
