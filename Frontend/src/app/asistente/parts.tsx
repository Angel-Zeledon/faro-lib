'use client'
/**
 * The pieces of `/asistente` both layouts render: the message bubble (with its
 * markdown-lite renderer and source badge), the typing indicator and the
 * personal welcome with its suggested questions. Moved out of page.tsx when the
 * phone layout (AssistantMobile.tsx) was added, so a message reads the same on
 * both and the two cannot drift.
 */
import { useLanguage } from '@/contexts/LanguageContext'
import { chatSourceLabel } from '@/lib/enumLabels'
import type { AssistantWelcome, ChatMessage } from '@/lib/types'
import { User } from 'lucide-react'
import { AssistantMark } from '@/components/brand/AssistantMark'
import { Markdown } from '@/components/ui/Markdown'

// ── Colour helpers ─────────────────────────────────────────────────────────────
// Colour only — the badge text comes from `chatSourceLabel`, so the copy the
// user reads lives in the i18n catalog and not in this map.
export const SOURCE_COLOR: Record<string, string> = {
  // The assistant core (backend/assistant/): a model answer whose figures were
  // all verified, one that carries the guard's warning, and the rule-based
  // summary written when no model answered.
  assistant:            '#2E8B62',
  assistant_unverified: '#B7791F',
  rules:                '#94a3b8',
  // Messages stored before the assistant core existed.
  rag:           'var(--accent)',
  rag_retrieved: 'var(--accent)',
  fallback:      '#B7791F',
  general:       '#2E8B62',
  off_topic:     '#B7791F',
  no_access:     '#C0504D',
  error:         '#C0504D',
}

/**
 * The one-line text a chat list row shows for its last message: markdown marks
 * and line breaks flattened to single spaces, so a long answer reads as a
 * snippet and the row keeps the height of every other row. The full message is
 * one click away (opening the chat); the CSS clamp below only cuts what the
 * row has no room for.
 */
export function previewText(raw: string): string {
  return raw
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/^\s*([*-]|\d+\.)\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 200)
}

/** Clamp a list-row text to `lines` lines with an ellipsis. */
export function clampStyle(lines: number): React.CSSProperties {
  return {
    display: '-webkit-box', WebkitBoxOrient: 'vertical', WebkitLineClamp: lines,
    overflow: 'hidden', overflowWrap: 'anywhere',
  }
}

export function fmtTime(iso: string) {
  const d = new Date(iso)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// ── Markdown renderer ──────────────────────────────────────────────────────────
// The renderer itself is shared with the narrative cards (components/ui/Markdown.tsx).
export function Md({ text, large = false }: { text: string; large?: boolean }) {
  return <Markdown text={text} large={large} />
}

// ── Typing indicator ───────────────────────────────────────────────────────────
export function TypingBubble() {
  return (
    <div data-testid="typing-indicator" style={{ display: 'flex', gap: 10, alignItems: 'flex-end', marginBottom: 4 }}>
      <div style={{
        width: 30, height: 30, borderRadius: '50%', flexShrink: 0,
        background: 'rgba(46,139,98,0.12)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <AssistantMark size={14} />
      </div>
      <div style={{
        background: 'var(--surface-2)', border: '1px solid var(--border)',
        borderRadius: '4px 16px 16px 16px', padding: '10px 16px',
        display: 'flex', alignItems: 'center', gap: 5,
      }}>
        {[0, 1, 2].map(i => (
          <span key={i} style={{
            display: 'inline-block', width: 6, height: 6, borderRadius: '50%',
            background: 'var(--dim)',
            animation: 'typing-dot 1.4s ease-in-out infinite',
            animationDelay: `${i * 0.2}s`,
          }} />
        ))}
      </div>
    </div>
  )
}

// ── Message bubble ─────────────────────────────────────────────────────────────
/** `large`: the phone layout — 15px text, no avatars, wider bubbles. */
export function MessageBubble({ msg, large = false }: { msg: ChatMessage; large?: boolean }) {
  const { t }  = useLanguage()
  const isUser = msg.role === 'user'
  const srcColor = msg.source ? (SOURCE_COLOR[msg.source] ?? '#94a3b8') : null
  return (
    <div
      data-testid={isUser ? 'user-message' : 'assistant-message'}
      style={{
        display: 'flex', gap: 10, alignItems: 'flex-end',
        flexDirection: isUser ? 'row-reverse' : 'row',
        marginBottom: 12,
        animation: 'slideUp 0.2s ease-out',
      }}
    >
      {!large && (<>
      {/* Avatar */}
      <div style={{
        width: 30, height: 30, borderRadius: '50%', flexShrink: 0,
        background: isUser ? 'color-mix(in srgb, var(--accent) 15%, transparent)' : 'rgba(46,139,98,0.12)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        {isUser ? <User size={13} color="var(--accent)" /> : <AssistantMark size={13} />}
      </div>

      </>)}

      {/* Bubble */}
      <div style={{
        maxWidth: large ? '92%' : '78%', minWidth: 0, display: 'flex', flexDirection: 'column',
        alignItems: isUser ? 'flex-end' : 'flex-start', gap: 3,
      }}>
        <div style={{
          background: isUser ? 'color-mix(in srgb, var(--accent) 14%, transparent)' : 'var(--surface-2)',
          border: `1px solid ${isUser ? 'color-mix(in srgb, var(--accent) 22%, transparent)' : 'var(--border)'}`,
          borderRadius: isUser ? '16px 4px 16px 16px' : '4px 16px 16px 16px',
          padding: '10px 14px',
        }}>
          {isUser
            ? <div className={`msg-prose msg-plain${large ? ' msg-prose-lg' : ''}`}>{msg.content}</div>
            : <Md text={msg.content} large={large} />}
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span className="msg-meta">{fmtTime(msg.created_at)}</span>
          {srcColor && msg.source && !isUser && (
            <span style={{
              fontSize: 10, fontWeight: 600, letterSpacing: '0.04em',
              color: srcColor, background: srcColor + '18',
              borderRadius: 4, padding: '1px 6px',
            }}>
              {chatSourceLabel(t, msg.source)}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

// ── The personal opening ───────────────────────────────────────────────────────
// Greets the user by name, sums up today from THEIR account and offers questions
// built from their own top risks (GET /analyst/welcome). A suggestion is sent as
// soon as it is clicked — it is already a complete question about their data.
export function Welcome({
  welcome, onAsk, disabled,
}: {
  welcome: AssistantWelcome | null
  onAsk: (question: string) => void
  disabled: boolean
}) {
  const { t } = useLanguage()
  if (!welcome) return null
  const s = welcome.summary
  const parts: string[] = []
  if (s) {
    if (s.order_now) parts.push(t('analyst.summary_order_now', { n: s.order_now }))
    if (s.order_soon) parts.push(t('analyst.summary_order_soon', { n: s.order_soon }))
    if (s.overdue_orders) parts.push(t('analyst.summary_overdue', { n: s.overdue_orders }))
    if (s.overstock) parts.push(t('analyst.summary_overstock', { n: s.overstock }))
    if (s.no_stock_data) parts.push(t('analyst.summary_no_stock_data', { n: s.no_stock_data }))
  }
  return (
    <div data-testid="assistant-welcome" style={{ width: '100%', maxWidth: 520, textAlign: 'center' }}>
      <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--text)', marginBottom: 6 }}>
        {welcome.first_name
          ? t('analyst.greeting', { name: welcome.first_name })
          : t('analyst.greeting_anonymous')}
      </div>
      <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.6 }}>
        {!welcome.has_forecast
          ? t('analyst.summary_no_forecast')
          : <>
              {welcome.company
                ? t('analyst.greeting_company', { company: welcome.company })
                : t('analyst.greeting_today')}{' '}
              {parts.length ? parts.join(' · ') : t('analyst.summary_all_clear')}
            </>}
      </div>
      {welcome.suggestions.length > 0 && (
        <div style={{ marginTop: 18 }}>
          <div style={{
            fontSize: 11, fontWeight: 700, color: 'var(--dim)',
            textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10,
          }}>
            {t('analyst.suggestions_header')}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center' }}>
            {welcome.suggestions.map(sg => {
              const text = t(`analyst.suggest.${sg.code}`, sg.params)
              return (
                <button
                  key={sg.code}
                  data-testid="assistant-suggestion"
                  disabled={disabled}
                  onClick={() => onAsk(text)}
                  style={{
                    all: 'unset', cursor: disabled ? 'default' : 'pointer',
                    padding: '7px 13px', borderRadius: 20, fontSize: 12,
                    border: '1px solid var(--border)', color: 'var(--text)',
                    background: 'var(--surface-2)', opacity: disabled ? 0.5 : 1,
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={e => { if (!disabled) e.currentTarget.style.borderColor = 'var(--accent)' }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)' }}
                >
                  {text}
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

