'use client'
/**
 * `/asistente` on a phone — a chat app.
 *
 * Two screens:
 *   · home: the personal welcome (today from the user's own account), the
 *     suggested questions as chips, and the previous conversations as a list
 *     (favourites first; ⋯ on each row for favourite / delete). The composer is
 *     already there: typing a question starts a new conversation.
 *   · a conversation, full screen: its title and a back arrow in the app
 *     header, the messages, older ones loaded as you scroll up, and the
 *     composer pinned above the tab bar — or above the keyboard while typing.
 *     An empty conversation offers the suggestions as chips over the composer.
 *
 * Every request and all state live in page.tsx; this file only lays them out.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  AlertTriangle, MessageSquare, MoreHorizontal, Search, Send, Star, Trash2, X,
} from 'lucide-react'
import type { AssistantWelcome, Chat, ChatMessage } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import BottomSheet from '@/components/mobile/BottomSheet'
import ComposerDock, { scrollPageToBottom } from '@/components/mobile/ComposerDock'
import { useMobileHeader } from '@/components/mobile/MobileHeaderContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { MessageBubble, TypingBubble, previewText, clampStyle } from './parts'

export interface AssistantMobileProps {
  chats: Chat[]
  chatsError: string | null
  activeChat: Chat | null
  activeChatId: string | null
  onOpenChat: (id: string | null) => void
  messages: ChatMessage[]
  loadingMsgs: boolean
  msgsError: string | null
  onRetryMessages: () => void
  loadingMore: boolean
  /** Called with the page's scroll container when the reader nears the top. */
  onNearTop: (el: HTMLElement) => void
  sending: boolean
  creatingChat: boolean
  input: string
  onInput: (s: string) => void
  onSend: (text: string) => void
  /** Present only while the newest message is a failed answer. */
  onRetry?: () => void
  welcome: AssistantWelcome | null
  assistantOff: boolean
  onToggleFavorite: (id: string) => void
  onDelete: (id: string) => void
  relTime: (iso: string) => string
}

export default function AssistantMobile(p: AssistantMobileProps) {
  const { t } = useLanguage()
  useMobileHeader(p.activeChatId
    ? { title: p.activeChat?.title ?? t('analyst.chat_fallback_title'), onBack: () => p.onOpenChat(null) }
    : null)

  const busy = p.sending || p.creatingChat
  const disabled = p.assistantOff || busy
  const suggestions = (p.welcome?.suggestions ?? []).map(sg => t(`analyst.suggest.${sg.code}`, sg.params))
  const showChips = !p.assistantOff && suggestions.length > 0
    && (!p.activeChatId || (!p.loadingMsgs && p.messages.length === 0))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: '100%' }}>
      {p.assistantOff && (
        <div role="status" style={{
          display: 'flex', gap: 8, alignItems: 'flex-start', padding: '12px 14px', marginBottom: 12,
          borderRadius: 12, background: 'var(--surface-2)', border: '1px solid var(--border)',
          fontSize: 13.5, color: 'var(--text)', lineHeight: 1.5,
        }}>
          <AlertTriangle size={15} style={{ flexShrink: 0, marginTop: 2, color: '#B7791F' }} aria-hidden="true" />
          <span>{t('analyst.unavailable_banner')}</span>
        </div>
      )}

      {p.activeChatId ? <Thread {...p} /> : <Home {...p} />}

      <Composer
        input={p.input}
        onInput={p.onInput}
        onSend={() => { const q = p.input; p.onInput(''); p.onSend(q) }}
        busy={busy}
        disabled={p.assistantOff || p.creatingChat}
        placeholder={p.assistantOff ? t('analyst.unavailable_placeholder')
          : p.creatingChat ? t('analyst.creating_chat_placeholder')
          : t('analyst.input_placeholder_mobile')}
        chips={showChips && p.activeChatId ? suggestions : []}
        onChip={q => { if (!disabled) p.onSend(q) }}
        chipsDisabled={disabled}
      />
    </div>
  )
}

// ── Home: welcome, suggestions, previous conversations ──────────────────────
function Home(p: AssistantMobileProps) {
  const { t } = useLanguage()
  const [search, setSearch] = useState('')
  const [actionsFor, setActionsFor] = useState<Chat | null>(null)
  const busy = p.sending || p.creatingChat
  const disabled = p.assistantOff || busy

  const q = search.trim().toLowerCase()
  const filtered = p.chats.filter(c => !q || c.title.toLowerCase().includes(q))
  const favorites = filtered.filter(c => c.is_favorite)
  const recent = filtered.filter(c => !c.is_favorite)

  const w = p.welcome
  const s = w?.summary
  const parts: string[] = []
  if (s) {
    if (s.order_now) parts.push(t('analyst.summary_order_now', { n: s.order_now }))
    if (s.order_soon) parts.push(t('analyst.summary_order_soon', { n: s.order_soon }))
    if (s.overdue_orders) parts.push(t('analyst.summary_overdue', { n: s.overdue_orders }))
    if (s.overstock) parts.push(t('analyst.summary_overstock', { n: s.overstock }))
    if (s.no_stock_data) parts.push(t('analyst.summary_no_stock_data', { n: s.no_stock_data }))
  }

  return (
    <div data-tour="an.start" style={{ display: 'flex', flexDirection: 'column', gap: 18, minWidth: 0 }}>
      {/* The personal opening */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <span aria-hidden="true" style={{
          width: 44, height: 44, borderRadius: '50%', flexShrink: 0,
          background: 'color-mix(in srgb, var(--accent) 10%, transparent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <MessageSquare size={22} color="var(--accent)" strokeWidth={1.7} />
        </span>
        <div data-testid="assistant-welcome" style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 19, fontWeight: 700, color: 'var(--text)', lineHeight: 1.3 }}>
            {w ? (w.first_name ? t('analyst.greeting', { name: w.first_name }) : t('analyst.greeting_anonymous')) : t('analyst.greeting_anonymous')}
          </div>
          <div style={{ fontSize: 14, color: 'var(--muted)', lineHeight: 1.55, marginTop: 4 }}>
            {!w ? t('analyst.empty_state_description')
              : !w.has_forecast ? t('analyst.summary_no_forecast')
              : <>
                  {w.company ? t('analyst.greeting_company', { company: w.company }) : t('analyst.greeting_today')}{' '}
                  {parts.length ? parts.join(' · ') : t('analyst.summary_all_clear')}
                </>}
          </div>
        </div>
      </div>

      {/* Suggested questions — each one is sent as soon as it is tapped */}
      {(w?.suggestions.length ?? 0) > 0 && !p.assistantOff && (
        <section>
          <h2 style={sectionHeading}>{t('analyst.suggestions_header')}</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {w!.suggestions.map(sg => {
              const text = t(`analyst.suggest.${sg.code}`, sg.params)
              return (
                <button
                  key={sg.code}
                  data-testid="assistant-suggestion"
                  disabled={disabled}
                  onClick={() => p.onSend(text)}
                  className="tap-feedback"
                  style={{
                    all: 'unset', boxSizing: 'border-box', cursor: disabled ? 'default' : 'pointer',
                    display: 'flex', alignItems: 'center', gap: 10, minHeight: 48, padding: '10px 14px',
                    borderRadius: 14, fontSize: 14.5, lineHeight: 1.4, color: 'var(--text)',
                    background: 'var(--surface)', border: '1px solid var(--border)', opacity: disabled ? 0.5 : 1,
                  }}
                >
                  <MessageSquare size={15} color="var(--accent)" aria-hidden="true" style={{ flexShrink: 0 }} />
                  <span style={{ flex: 1, minWidth: 0 }}>{text}</span>
                </button>
              )
            })}
          </div>
        </section>
      )}

      {/* Previous conversations */}
      <section data-tour="an.chats">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <h2 style={{ ...sectionHeading, margin: 0, flex: 1 }}>{t('analyst.recent_header')}</h2>
        </div>
        {p.chats.length > 4 && (
          <div style={{ position: 'relative', marginBottom: 10 }}>
            <Search size={16} aria-hidden="true" style={{ position: 'absolute', left: 13, top: '50%', transform: 'translateY(-50%)', color: 'var(--dim)' }} />
            <input
              type="text" inputMode="search" autoComplete="off" enterKeyHint="search"
              name="chat_search"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder={t('analyst.search_chats_placeholder')}
              aria-label={t('analyst.search_chats_placeholder')}
              style={{
                width: '100%', boxSizing: 'border-box', minHeight: 44, borderRadius: 12,
                padding: '0 44px 0 38px', fontSize: 16, color: 'var(--text)',
                background: 'var(--surface)', border: '1px solid var(--border)', outline: 'none',
              }}
            />
            {search && (
              <button onClick={() => setSearch('')} aria-label={t('messages.clear_search')} style={{
                all: 'unset', position: 'absolute', right: 0, top: 0, width: 44, height: 44, cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--dim)',
              }}>
                <X size={16} aria-hidden="true" />
              </button>
            )}
          </div>
        )}
        {p.chatsError ? (
          <div role="alert" style={{ padding: 16, textAlign: 'center', fontSize: 14, color: '#C0504D' }}>{p.chatsError}</div>
        ) : p.chats.length === 0 ? (
          <div style={{ padding: '22px 16px', textAlign: 'center', fontSize: 14, color: 'var(--dim)', lineHeight: 1.5,
                        border: '1px dashed var(--border)', borderRadius: 14 }}>
            <MessageSquare size={22} aria-hidden="true" style={{ display: 'block', margin: '0 auto 8px', opacity: 0.6 }} />
            {t('analyst.no_chats_yet')}
          </div>
        ) : (
          <>
            {favorites.length > 0 && (
              <ul style={{ ...groupStyle, marginBottom: 10 }} aria-label={t('analyst.favorites_header')}>
                {favorites.map(c => <ChatRow key={c.id} chat={c} p={p} onActions={() => setActionsFor(c)} />)}
              </ul>
            )}
            {recent.length > 0 && (
              <ul style={groupStyle} aria-label={t('analyst.recent_header')}>
                {recent.map(c => <ChatRow key={c.id} chat={c} p={p} onActions={() => setActionsFor(c)} />)}
              </ul>
            )}
          </>
        )}
      </section>

      {/* Per-conversation actions: favourite, delete (with undo) */}
      <BottomSheet
        open={!!actionsFor}
        onClose={() => setActionsFor(null)}
        title={actionsFor?.title ?? ''}
      >
        {actionsFor && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, paddingBottom: 4 }}>
            <button className="mobile-btn mobile-btn-secondary" style={{ width: '100%', flex: 'none' }}
              onClick={() => { p.onToggleFavorite(actionsFor.id); setActionsFor(null) }}>
              <Star size={17} aria-hidden="true" fill={actionsFor.is_favorite ? '#B7791F' : 'none'} color={actionsFor.is_favorite ? '#B7791F' : 'currentColor'} />
              {actionsFor.is_favorite ? t('analyst.remove_from_favorites') : t('analyst.add_to_favorites')}
            </button>
            <button className="mobile-btn mobile-btn-secondary" style={{ width: '100%', flex: 'none', color: '#C0504D' }}
              onClick={() => { p.onDelete(actionsFor.id); setActionsFor(null) }}>
              <Trash2 size={17} aria-hidden="true" /> {t('analyst.delete_chat_title')}
            </button>
          </div>
        )}
      </BottomSheet>
    </div>
  )
}

const sectionHeading: React.CSSProperties = {
  margin: '0 0 8px', fontSize: 12, fontWeight: 700, color: 'var(--dim)',
  textTransform: 'uppercase', letterSpacing: '0.07em',
}

const groupStyle: React.CSSProperties = {
  listStyle: 'none', margin: 0, padding: 0, background: 'var(--surface)',
  border: '1px solid var(--border)', borderRadius: 14, overflow: 'hidden',
}

function ChatRow({ chat, p, onActions }: { chat: Chat; p: AssistantMobileProps; onActions: () => void }) {
  const { t } = useLanguage()
  return (
    <li className="mobile-list-item" style={{ display: 'flex', alignItems: 'stretch' }}>
      <button
        type="button"
        onClick={() => p.onOpenChat(chat.id)}
        className="tap-feedback"
        style={{
          all: 'unset', boxSizing: 'border-box', flex: 1, minWidth: 0, cursor: 'pointer',
          display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 2,
          padding: '10px 4px 10px 14px', minHeight: 60,
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          {chat.is_favorite && <Star size={13} fill="#B7791F" color="#B7791F" aria-label={t('analyst.favorites_header')} style={{ flexShrink: 0 }} />}
          <span style={{ flex: 1, minWidth: 0, fontSize: 15, fontWeight: 600, color: 'var(--text)', ...clampStyle(1) }} title={chat.title}>
            {chat.title}
          </span>
          <span style={{ fontSize: 12, color: 'var(--dim)', flexShrink: 0 }}>{p.relTime(chat.last_message_at)}</span>
        </span>
        {chat.last_message_preview && (
          <span style={{ fontSize: 13.5, lineHeight: 1.35, color: 'var(--dim)', ...clampStyle(2) }}>
            {previewText(chat.last_message_preview)}
          </span>
        )}
      </button>
      <button
        type="button"
        onClick={onActions}
        aria-label={`${t('mobile.more_actions')} — ${chat.title}`}
        aria-haspopup="dialog"
        style={{
          all: 'unset', boxSizing: 'border-box', flexShrink: 0, width: 48, cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--dim)',
        }}
      >
        <MoreHorizontal size={20} aria-hidden="true" />
      </button>
    </li>
  )
}

// ── One conversation, full screen ────────────────────────────────────────────
function Thread(p: AssistantMobileProps) {
  const { t } = useLanguage()
  const lastId = p.messages[p.messages.length - 1]?.id
  const firstId = p.messages[0]?.id
  const prevFirst = useRef<string | undefined>(firstId)
  // The last answer already brought into view, and whether the thread had
  // finished loading before it arrived (an answer loaded with the history is
  // not "fresh").
  const answeredRef = useRef<string | undefined>(undefined)
  const seenRef = useRef(false)

  // New message at the bottom (or a freshly opened chat): follow it. Older
  // messages prepended at the top: leave the reader where they were — the
  // page keeps their place (see onNearTop in page.tsx).
  useLayoutEffect(() => {
    const prepended = prevFirst.current !== undefined && firstId !== prevFirst.current
    prevFirst.current = firstId
    if (prepended) return
    // A fresh answer is read from its first line, the way chat assistants do:
    // the question goes to the top of the screen with the answer under it.
    // Anything else (opening a chat, the question just sent, the typing dots)
    // follows the bottom.
    const last = p.messages[p.messages.length - 1]
    const prev = p.messages[p.messages.length - 2]
    if (!p.sending && last?.role === 'assistant' && prev?.role === 'user' && answeredRef.current !== last.id
        && seenRef.current) {
      answeredRef.current = last.id
      document.getElementById(`msg-${prev.id}`)?.scrollIntoView({ block: 'start' })
      return
    }
    seenRef.current = !p.loadingMsgs && p.messages.length > 0
    scrollPageToBottom()
  }, [lastId, firstId, p.sending, p.loadingMsgs])

  // Older messages load as the reader scrolls up — on the page's own scroll
  // container, which is what scrolls on a phone.
  const onNearTop = useRef(p.onNearTop)
  onNearTop.current = p.onNearTop
  useEffect(() => {
    const el = document.querySelector<HTMLElement>('.page-content')
    if (!el) return
    const onScroll = () => { if (el.scrollTop < 120) onNearTop.current(el) }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <div data-tour="an.thread" role="log" aria-live="polite" style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      {p.loadingMore && (
        <div style={{ textAlign: 'center', padding: '8px 0', color: 'var(--dim)', fontSize: 12 }}>
          <Spinner size={12} /> {t('analyst.loading_older_messages')}
        </div>
      )}
      {p.loadingMsgs ? (
        <div data-testid="messages-loading-spinner" style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
          <Spinner size={20} />
        </div>
      ) : p.msgsError ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, padding: '40px 16px', textAlign: 'center' }}>
          <AlertTriangle size={28} color="#C0504D" style={{ opacity: 0.8 }} aria-hidden="true" />
          <div style={{ fontSize: 14, color: 'var(--text)', lineHeight: 1.5 }}>{p.msgsError}</div>
          <button className="mobile-btn mobile-btn-primary" style={{ flex: 'none' }} onClick={p.onRetryMessages}>
            {t('analyst.retry')}
          </button>
        </div>
      ) : p.messages.length === 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, padding: '48px 16px', color: 'var(--dim)', textAlign: 'center' }}>
          <MessageSquare size={28} strokeWidth={1.5} color="var(--accent)" aria-hidden="true" />
          <div style={{ fontSize: 14.5, lineHeight: 1.5 }}>{t('analyst.empty_messages_account')}</div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', paddingTop: 4 }}>
          {p.messages.map(m => (
            <div key={m.id} id={`msg-${m.id}`} style={{ scrollMarginTop: 8 }}><MessageBubble msg={m} large
              onRetry={p.onRetry && m.id === lastId ? p.onRetry : undefined} /></div>
          ))}
          {p.sending && <TypingBubble />}
        </div>
      )}
    </div>
  )
}

// ── The composer ─────────────────────────────────────────────────────────────
function Composer({ input, onInput, onSend, busy, disabled, placeholder, chips, onChip, chipsDisabled }: {
  input: string
  onInput: (s: string) => void
  onSend: () => void
  busy: boolean
  disabled: boolean
  placeholder: string
  chips: string[]
  onChip: (q: string) => void
  chipsDisabled: boolean
}) {
  const { t } = useLanguage()
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 132)}px`
  }, [input])
  const canSend = !!input.trim() && !busy && !disabled

  return (
    <ComposerDock ariaLabel={t('analyst.input_placeholder_mobile')} onHeightChange={() => scrollPageToBottom()}>
      {chips.length > 0 && (
        <div className="mobile-tabs-scroller" style={{
          display: 'flex', gap: 8, overflowX: 'auto', margin: '0 -10px 8px', padding: '0 10px',
          scrollSnapType: 'x proximity',
        }}>
          {chips.map(c => (
            <button
              key={c}
              data-testid="assistant-suggestion"
              disabled={chipsDisabled}
              onClick={() => onChip(c)}
              style={{
                all: 'unset', boxSizing: 'border-box', flexShrink: 0, cursor: chipsDisabled ? 'default' : 'pointer',
                maxWidth: 260, minHeight: 40, padding: '0 14px', borderRadius: 20, scrollSnapAlign: 'start',
                display: 'flex', alignItems: 'center', fontSize: 13.5, color: 'var(--text)',
                background: 'var(--surface-2)', border: '1px solid var(--border)', opacity: chipsDisabled ? 0.5 : 1,
                overflow: 'hidden', overflowWrap: 'anywhere',
              }}
            >
              {c}
            </button>
          ))}
        </div>
      )}
      <div data-tour="an.input" style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
        <textarea
          ref={ref}
          name="analyst_message"
          rows={1}
          value={input}
          onChange={e => onInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (canSend) onSend() } }}
          onFocus={() => setTimeout(() => scrollPageToBottom(true), 250)}
          placeholder={placeholder}
          aria-label={t('analyst.input_placeholder_mobile')}
          disabled={disabled}
          enterKeyHint="send"
          style={{
            flex: 1, minWidth: 0, boxSizing: 'border-box', resize: 'none',
            minHeight: 44, maxHeight: 132, padding: '11px 14px', borderRadius: 22,
            border: '1px solid var(--border)', background: 'var(--surface-2)',
            color: 'var(--text)', fontSize: 16, lineHeight: 1.35, fontFamily: 'inherit', outline: 'none',
            opacity: disabled ? 0.6 : 1,
          }}
        />
        <button
          onClick={onSend}
          disabled={!canSend}
          aria-label={t('messages.send')}
          style={{
            all: 'unset', boxSizing: 'border-box', flexShrink: 0, width: 44, height: 44, borderRadius: 22,
            cursor: canSend ? 'pointer' : 'default',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'var(--accent)', color: '#fff', opacity: canSend || busy ? 1 : 0.45,
            transition: 'opacity var(--dur-2) var(--ease-out)',
          }}
        >
          {busy ? <Spinner size={16} /> : <Send size={18} aria-hidden="true" />}
        </button>
      </div>
    </ComposerDock>
  )
}

