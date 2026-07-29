'use client'
import {
  useState, useEffect, useRef, useCallback,
  type KeyboardEvent, type UIEvent,
} from 'react'
import {
  listChats, createChat, updateChat, deleteChat,
  getChatMessages, sendChatMessage, getDataSourceTypes, getSessions,
  getSuggestedQuestions,
} from '@/lib/api'
import type { Chat, ChatMessage, ChatSourceType, SessionInfo, SuggestedQuestion } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import Button from '@/components/ui/Button'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'
import { chatSourceLabel, chatDataSourceLabel } from '@/lib/enumLabels'
import {
  Plus, Search, Star, Trash2, Bot, User, Send,
  Sparkles, MessageSquare, ChevronDown, X, Filter,
  CheckSquare, Square, AlertTriangle,
} from 'lucide-react'

// ── Colour helpers ─────────────────────────────────────────────────────────────
// Colour only — the badge text comes from `chatSourceLabel`, so the copy the
// user reads lives in the i18n catalog and not in this map.
const SOURCE_COLOR: Record<string, string> = {
  rag:           'var(--accent)',
  rag_retrieved: 'var(--accent)',
  fallback:      '#f59e0b',
  general:       '#22c55e',
  off_topic:     '#f59e0b',
  no_access:     '#ef4444',
  error:         '#ef4444',
}

// ── Shell geometry ─────────────────────────────────────────────────────────────
// This screen bleeds to the edges of the app shell instead of living inside its
// padding, so it still cancels `.page-content`'s padding with an equal negative
// margin (Tailwind `p-6`, which computes to 21px at this app's root font size).
//
// It no longer needs to know the top bar's height. `.page-content` is a flex
// column and `.page-enter` grows to fill it, so `height: 100%` now resolves —
// this used to be `calc(100vh - 52px)`, a number copied out of AppShell that
// would have gone quietly wrong the day the top bar changed height.
const PAGE_PAD = 21

function fmtTime(iso: string) {
  const d = new Date(iso)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function fmtRelative(iso: string, t: (k: string) => string) {
  const diff = Date.now() - new Date(iso).getTime()
  if (diff < 60_000)  return t('analyst.time_just_now')
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}${t('analyst.time_minutes_ago_suffix')}`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}${t('analyst.time_hours_ago_suffix')}`
  return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' })
}

// ── Markdown lite renderer ─────────────────────────────────────────────────────
function Md({ text }: { text: string }) {
  return (
    <div style={{ fontSize: 13, lineHeight: 1.75, color: 'var(--text)' }}>
      {text.split('\n').map((line, i) => {
        if (!line.trim()) return <div key={i} style={{ height: 7 }} />
        const bold = (s: string) =>
          s.split(/(\*\*[^*]+\*\*)/).map((p, j) =>
            p.startsWith('**') ? <strong key={j}>{p.slice(2, -2)}</strong> : p,
          )
        if (/^(\*|-|\d+\.) /.test(line.trim())) {
          return (
            <div key={i} style={{ display: 'flex', gap: 7, margin: '2px 0' }}>
              <span style={{ color: 'var(--accent)', flexShrink: 0, marginTop: 1 }}>·</span>
              <span>{bold(line.replace(/^(\s*(\*|-|\d+\.)\s*)/, ''))}</span>
            </div>
          )
        }
        return <div key={i} style={{ margin: '2px 0' }}>{bold(line)}</div>
      })}
    </div>
  )
}

// ── Typing indicator ───────────────────────────────────────────────────────────
function TypingBubble() {
  return (
    <div data-testid="typing-indicator" style={{ display: 'flex', gap: 10, alignItems: 'flex-end', marginBottom: 4 }}>
      <div style={{
        width: 30, height: 30, borderRadius: '50%', flexShrink: 0,
        background: 'rgba(34,197,94,0.12)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <Bot size={14} color="#22c55e" />
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
function MessageBubble({ msg }: { msg: ChatMessage }) {
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
      {/* Avatar */}
      <div style={{
        width: 30, height: 30, borderRadius: '50%', flexShrink: 0,
        background: isUser ? 'color-mix(in srgb, var(--accent) 15%, transparent)' : 'rgba(34,197,94,0.12)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        {isUser ? <User size={13} color="var(--accent)" /> : <Bot size={13} color="#22c55e" />}
      </div>

      {/* Bubble */}
      <div style={{
        maxWidth: '75%', display: 'flex', flexDirection: 'column',
        alignItems: isUser ? 'flex-end' : 'flex-start', gap: 3,
      }}>
        <div style={{
          background: isUser ? 'color-mix(in srgb, var(--accent) 14%, transparent)' : 'var(--surface-2)',
          border: `1px solid ${isUser ? 'color-mix(in srgb, var(--accent) 22%, transparent)' : 'var(--border)'}`,
          borderRadius: isUser ? '16px 4px 16px 16px' : '4px 16px 16px 16px',
          padding: '10px 14px',
        }}>
          {isUser
            ? <div style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{msg.content}</div>
            : <Md text={msg.content} />}
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span style={{ fontSize: 10, color: 'var(--dim)' }}>{fmtTime(msg.created_at)}</span>
          {srcColor && msg.source && !isUser && (
            <span style={{
              fontSize: 9, fontWeight: 600, letterSpacing: '0.05em',
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

// ── Sources filter popover ─────────────────────────────────────────────────────
function SourcesFilter({
  sources, allTypes, onChange, dataTour,
}: {
  sources: string[]
  allTypes: ChatSourceType[]
  onChange: (v: string[]) => void
  /** `data-tour` anchor. Only the header copy carries it, so it resolves once. */
  dataTour?: string
}) {
  const { t } = useLanguage()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function outside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', outside)
    return () => document.removeEventListener('mousedown', outside)
  }, [])

  const toggle = (id: string) => {
    const next = sources.includes(id) ? sources.filter(s => s !== id) : [...sources, id]
    onChange(next)
  }

  const active = sources.length > 0

  return (
    <div ref={ref} data-tour={dataTour} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(o => !o)}
        title={t('analyst.filter_data_sources_title')}
        style={{
          all: 'unset', cursor: 'pointer',
          display: 'flex', alignItems: 'center', gap: 5,
          padding: '6px 10px', borderRadius: 7,
          background: active ? 'color-mix(in srgb, var(--accent) 10%, transparent)' : 'var(--surface-2)',
          border: `1px solid ${active ? 'color-mix(in srgb, var(--accent) 30%, transparent)' : 'var(--border)'}`,
          fontSize: 11, color: active ? 'var(--accent)' : 'var(--muted)',
          transition: 'all 0.15s',
        }}
      >
        <Filter size={11} />
        {active ? `${sources.length} ${sources.length > 1 ? t('analyst.sources_plural') : t('analyst.sources_singular')}` : t('analyst.all_sources')}
        <ChevronDown size={10} />
      </button>

      {open && (
        <div style={{
          position: 'absolute', bottom: '110%', left: 0,
          width: 220, background: 'var(--surface)',
          border: '1px solid var(--border-strong)', borderRadius: 10,
          boxShadow: '0 8px 32px rgba(0,0,0,0.4)', overflow: 'hidden', zIndex: 100,
        }}>
          <div style={{
            padding: '8px 12px 6px', fontSize: 10, fontWeight: 700,
            color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.07em',
            borderBottom: '1px solid var(--border)',
          }}>
            {t('analyst.data_sources_header')}
          </div>
          {allTypes.map(st => {
            const selected = sources.includes(st.id)
            return (
              <div
                key={st.id}
                onClick={() => toggle(st.id)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 12px', cursor: 'pointer', fontSize: 12,
                  color: selected ? 'var(--text)' : 'var(--muted)',
                  background: selected ? 'color-mix(in srgb, var(--accent) 6%, transparent)' : 'transparent',
                  transition: 'all 0.1s',
                }}
              >
                {selected
                  ? <CheckSquare size={13} color="var(--accent)" />
                  : <Square size={13} color="var(--dim)" />}
                {chatDataSourceLabel(t, st.id, st.label)}
              </div>
            )
          })}
          {sources.length > 0 && (
            <div
              onClick={() => { onChange([]); setOpen(false) }}
              style={{
                padding: '6px 12px', fontSize: 11, color: 'var(--dim)',
                borderTop: '1px solid var(--border)', cursor: 'pointer',
                textAlign: 'center',
              }}
            >
              {t('analyst.clear_filter')}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Chat sidebar item ─────────────────────────────────────────────────────────
function ChatItem({
  chat, active, onSelect, onFavorite, onDelete,
}: {
  chat: Chat
  active: boolean
  onSelect: () => void
  onFavorite: () => void
  onDelete: () => void
}) {
  const { t } = useLanguage()
  const [hover, setHover] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)

  return (
    <div
      onClick={onSelect}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => { setHover(false); setConfirmDel(false) }}
      style={{
        position: 'relative', padding: '10px 12px', borderRadius: 8,
        cursor: 'pointer', transition: 'all 0.12s',
        background: active
          ? 'color-mix(in srgb, var(--accent) 10%, transparent)'
          : hover ? 'rgba(255,255,255,0.03)' : 'transparent',
        border: `1px solid ${active ? 'color-mix(in srgb, var(--accent) 25%, transparent)' : 'transparent'}`,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
        <MessageSquare size={11} color={active ? 'var(--accent)' : 'var(--dim)'} style={{ flexShrink: 0 }} />
        <span style={{
          fontSize: 12, fontWeight: active ? 600 : 400,
          color: active ? 'var(--text)' : 'var(--muted)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          flex: 1,
        }}>
          {chat.title}
        </span>
      </div>

      {chat.last_message_preview && (
        <div style={{
          fontSize: 11, color: 'var(--dim)', overflow: 'hidden',
          textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          paddingLeft: 17,
        }}>
          {chat.last_message_preview}
        </div>
      )}

      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        paddingLeft: 17, marginTop: 3,
      }}>
        <span style={{ fontSize: 10, color: 'var(--dim)' }}>{fmtRelative(chat.last_message_at, t)}</span>
        {chat.session_id && (
          <span style={{
            fontSize: 9, padding: '1px 5px', borderRadius: 3,
            background: 'rgba(34,197,94,0.1)', color: '#22c55e',
          }}>RAG</span>
        )}
      </div>

      {/* Action buttons on hover */}
      {(hover || active) && (
        <div style={{
          position: 'absolute', right: 6, top: '50%', transform: 'translateY(-50%)',
          display: 'flex', gap: 2,
        }}>
          <button
            onClick={e => { e.stopPropagation(); onFavorite() }}
            title={chat.is_favorite ? t('analyst.remove_from_favorites') : t('analyst.add_to_favorites')}
            style={{
              all: 'unset', width: 22, height: 22, borderRadius: 4, cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: chat.is_favorite ? '#f59e0b' : 'var(--dim)',
              background: 'var(--surface)',
            }}
          >
            <Star size={11} fill={chat.is_favorite ? '#f59e0b' : 'none'} />
          </button>
          {confirmDel ? (
            <button
              onClick={e => { e.stopPropagation(); onDelete() }}
              title={t('analyst.confirm_delete')}
              style={{
                all: 'unset', width: 22, height: 22, borderRadius: 4, cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: '#ef444420', color: '#ef4444',
              }}
            >
              <Trash2 size={11} />
            </button>
          ) : (
            <button
              onClick={e => { e.stopPropagation(); setConfirmDel(true) }}
              title={t('analyst.delete_chat_title')}
              style={{
                all: 'unset', width: 22, height: 22, borderRadius: 4, cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: 'var(--dim)', background: 'var(--surface)',
              }}
            >
              <Trash2 size={11} />
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// ── Empty state ────────────────────────────────────────────────────────────────
function EmptyState({ onCreate }: { onCreate: () => void }) {
  const { t } = useLanguage()
  return (
    <div data-tour="an.start" style={{
      flex: 1, display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      gap: 16, padding: '40px 32px', textAlign: 'center',
    }}>
      <div style={{
        width: 64, height: 64, borderRadius: '50%',
        background: 'color-mix(in srgb, var(--accent) 8%, transparent)',
        border: '1px solid color-mix(in srgb, var(--accent) 15%, transparent)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <Sparkles size={28} color="var(--accent)" strokeWidth={1.5} />
      </div>
      <div>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>{t('analyst.title')}</div>
        <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.6, maxWidth: 300 }}>
          {t('analyst.empty_state_description')}
        </div>
      </div>
      <Button variant="primary" icon={<Plus size={14} />} onClick={onCreate}>
        {t('analyst.new_chat')}
      </Button>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function AnalystPage() {
  const { t } = useLanguage()
  const { undoable, addToast } = useToast()
  const [chats,       setChats]       = useState<Chat[]>([])
  const [activeChatId, setActive]     = useState<string | null>(null)
  const [messages,    setMessages]    = useState<ChatMessage[]>([])
  const [hasMore,     setHasMore]     = useState(false)
  const [loadingMsgs, setLoadingMsgs] = useState(false)
  const [msgsError,   setMsgsError]   = useState<string | null>(null)
  const [chatsError,  setChatsError]  = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [sending,     setSending]     = useState(false)
  const [creatingChat, setCreatingChat] = useState(false)
  const [input,       setInput]       = useState('')
  const [search,      setSearch]      = useState('')
  const [sessions,    setSessions]    = useState<SessionInfo[]>([])
  const [sourceTypes, setSourceTypes] = useState<ChatSourceType[]>([])
  const [suggestedQs, setSuggestedQs] = useState<SuggestedQuestion[]>([])
  const msgsRef    = useRef<HTMLDivElement>(null)
  const inputRef   = useRef<HTMLTextAreaElement>(null)
  const bottomRef  = useRef<HTMLDivElement>(null)
  const loadingRef = useRef(false)

  const activeChat = chats.find(c => c.id === activeChatId) ?? null

  // ── Bootstrap ──────────────────────────────────────────────────────────────
  useEffect(() => {
    listChats().then(setChats).catch((e: unknown) => {
      setChatsError(e instanceof Error ? e.message : t('analyst.err_load_chats'))
    })
    getSessions().then(setSessions).catch(console.error)
    getDataSourceTypes().then(setSourceTypes).catch(console.error)

    const bp = (typeof window !== 'undefined' ? localStorage.getItem('bp') : null) || 'distributor'
    getSuggestedQuestions(bp, true, false)
      .then(setSuggestedQs)
      .catch(() => {})
  }, [])

  // ── Load messages when chat changes ───────────────────────────────────────
  const loadMessages = useCallback((chatId: string) => {
    setLoadingMsgs(true)
    setMsgsError(null)
    getChatMessages(chatId, 30)
      .then(page => {
        setMessages(page.messages)
        setHasMore(page.has_more)
        setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'instant' }), 50)
      })
      .catch((e: unknown) => {
        setMsgsError(e instanceof Error ? e.message : t('analyst.err_load_history'))
      })
      .finally(() => setLoadingMsgs(false))
  }, [t])

  useEffect(() => {
    setMessages([]); setHasMore(false); setMsgsError(null)
    if (!activeChatId) return
    loadMessages(activeChatId)
  }, [activeChatId, loadMessages])

  // ── Scroll to bottom on new message ───────────────────────────────────────
  const scrollToBottom = useCallback((instant = false) => {
    bottomRef.current?.scrollIntoView({ behavior: instant ? 'instant' : 'smooth' })
  }, [])

  // ── Infinite scroll: load older messages when near top ────────────────────
  const handleScroll = useCallback(async (e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    if (el.scrollTop > 120 || !hasMore || loadingRef.current || !activeChatId) return
    loadingRef.current = true
    setLoadingMore(true)
    try {
      const oldest   = messages[0]
      const prevH    = el.scrollHeight
      const page     = await getChatMessages(activeChatId, 30, oldest?.id)
      if (page.messages.length) {
        setMessages(prev => [...page.messages, ...prev])
        setHasMore(page.has_more)
        // Restore scroll position after prepend
        requestAnimationFrame(() => {
          el.scrollTop = el.scrollHeight - prevH
        })
      }
    } finally {
      setLoadingMore(false)
      loadingRef.current = false
    }
  }, [hasMore, activeChatId, messages])

  // ── Create a new chat ────────────────────────────────────────────────────
  const handleNewChat = useCallback(async (sessionId?: string) => {
    try {
      const chat = await createChat(sessionId ? { session_id: sessionId } : {})
      setChats(prev => [chat, ...prev])
      setActive(chat.id)
    } catch (e) { console.error(e) }
  }, [])

  // ── Send a message ────────────────────────────────────────────────────────
  const handleSend = useCallback(async (text: string, chatId?: string) => {
    const q = text.trim()
    if (!q || sending || creatingChat) return

    let targetId = chatId ?? activeChatId
    if (!targetId) {
      setCreatingChat(true)
      try {
        const chat = await createChat()
        setChats(prev => [chat, ...prev])
        setActive(chat.id)
        targetId = chat.id
      } catch (e) { console.error(e); setCreatingChat(false); return }
      setCreatingChat(false)
    }

    setSending(true)
    try {
      // Optimistically show user message
      const optimistic: ChatMessage = {
        id: `opt-${Date.now()}`,
        chat_id: targetId,
        role: 'user',
        content: q,
        created_at: new Date().toISOString(),
      }
      setMessages(prev => [...prev, optimistic])
      setTimeout(() => scrollToBottom(), 30)

      const res = await sendChatMessage(
        targetId, q,
        chats.find(c => c.id === targetId)?.session_id,
      )

      // Replace optimistic with real messages
      setMessages(prev => [
        ...prev.filter(m => m.id !== optimistic.id),
        res.user_message,
        res.ai_message,
      ])

      // Update chat list (new title, last_message_at)
      setChats(prev => prev.map(c => {
        if (c.id !== targetId) return c
        return {
          ...c,
          last_message_at: res.ai_message.created_at,
          last_message_preview: q,
          message_count: (c.message_count || 0) + 2,
        }
      }))

      // Re-fetch updated title after first message
      if ((chats.find(c => c.id === targetId)?.message_count ?? 0) === 0) {
        listChats().then(list => {
          setChats(list)
        })
      }

      setTimeout(() => scrollToBottom(), 30)
    } catch (err) {
      const raw = err instanceof Error ? err.message : String(err)
      const friendly = raw.includes('429') || raw.toLowerCase().includes('too many')
        ? t('analyst.err_too_many_requests')
        : raw.includes('500') || raw.toLowerCase().includes('server error')
        ? t('analyst.err_server_error')
        : err instanceof TypeError || raw.toLowerCase().includes('network') || raw.toLowerCase().includes('fetch')
        ? t('analyst.err_connection')
        : t('analyst.err_failed_response')
      const errMsg: ChatMessage = {
        id: `err-${Date.now()}`,
        chat_id: targetId,
        role: 'assistant',
        content: friendly,
        source: 'error',
        created_at: new Date().toISOString(),
      }
      setMessages(prev => [...prev.filter(m => !m.id.startsWith('opt-')), errMsg])
    } finally {
      setSending(false)
      inputRef.current?.focus()
    }
  }, [activeChatId, chats, sending, scrollToBottom, t])

  // ── Keyboard shortcuts ────────────────────────────────────────────────────
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(input); setInput('') }
  }
  // ── Toggle favorite ───────────────────────────────────────────────────────
  const toggleFav = async (chatId: string) => {
    const chat = chats.find(c => c.id === chatId)
    if (!chat) return
    const next = { ...chat, is_favorite: !chat.is_favorite }
    setChats(prev => prev.map(c => c.id === chatId ? next : c)
      .sort((a, b) => {
        if (a.is_favorite !== b.is_favorite) return b.is_favorite ? 1 : -1
        return new Date(b.last_message_at).getTime() - new Date(a.last_message_at).getTime()
      }))
    await updateChat(chatId, { is_favorite: next.is_favorite })
  }

  // ── Delete chat ───────────────────────────────────────────────────────────
  // Deleting used to be instant and unrecoverable — one stray click and the
  // conversation was gone. The row disappears immediately, but the DELETE only
  // leaves once the undo window closes, so "Deshacer" costs nothing and needs
  // no second request that could fail.
  const handleDelete = (chatId: string) => {
    const chat = chats.find(c => c.id === chatId)
    if (!chat) return
    const wasActive = activeChatId === chatId
    undoable({
      title:     t('analyst.chat_deleted'),
      message:   chat.title,
      undoLabel: t('common.undo'),
      apply: () => {
        setChats(prev => prev.filter(c => c.id !== chatId))
        if (wasActive) setActive(null)
      },
      revert: () => {
        setChats(prev => prev.some(c => c.id === chatId)
          ? prev
          // Same ordering the list is built with, so the row comes back where
          // it was rather than jumping to the top.
          : [chat, ...prev].sort((a, b) => {
              if (a.is_favorite !== b.is_favorite) return b.is_favorite ? 1 : -1
              return new Date(b.last_message_at).getTime() - new Date(a.last_message_at).getTime()
            }))
        if (wasActive) setActive(chatId)
      },
      commit: () => deleteChat(chatId),
      onCommitError: () => addToast(t('analyst.chat_delete_failed'), chat.title, 'error'),
    })
  }

  // ── Update sources for active chat ───────────────────────────────────────
  const handleSourcesChange = async (sources: string[]) => {
    if (!activeChatId) return
    setChats(prev => prev.map(c => c.id === activeChatId ? { ...c, data_sources: sources } : c))
    await updateChat(activeChatId, { data_sources: sources })
  }

  // ── Separate favorites / recent ───────────────────────────────────────────
  const filtered  = chats.filter(c => !search || c.title.toLowerCase().includes(search.toLowerCase()))
  const favorites = filtered.filter(c => c.is_favorite)
  const recent    = filtered.filter(c => !c.is_favorite)

  // ── Attach session to active chat ─────────────────────────────────────────
  const completedSessions = sessions.filter(s => s.status === 'COMPLETED')

  return (
    <>
      {/* `slideUp` and the typing dots live in globals.css now. They were
          injected here under the name `pulse`, which NarrativeCard also
          injected with a different shape — same name, one document, so
          whichever mounted last won. */}
      <style>{`
        .chat-item-hover:hover { background: rgba(255,255,255,0.03) !important; }
      `}</style>

      <div style={{
        display: 'flex',
        height: '100%',
        margin: -PAGE_PAD,
        overflow: 'hidden',
      }}>

        {/* ── LEFT SIDEBAR ────────────────────────────────────────── */}
        <div style={{
          width: 280, flexShrink: 0, display: 'flex', flexDirection: 'column',
          borderRight: '1px solid var(--border)',
          background: 'var(--surface)',
        }}>
          {/* Header */}
          <div style={{
            padding: '16px 14px 10px',
            borderBottom: '1px solid var(--border)',
            flexShrink: 0,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <Sparkles size={14} color="var(--accent)" />
                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>{t('analyst.title')}</span>
              </div>
              <button
                data-tour="an.new"
                onClick={() => handleNewChat()}
                title={t('analyst.new_chat_title')}
                style={{
                  all: 'unset', width: 28, height: 28, borderRadius: 7, cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: 'var(--accent)', color: '#fff',
                  transition: 'opacity 0.15s',
                }}
                onMouseEnter={e => (e.currentTarget.style.opacity = '0.85')}
                onMouseLeave={e => (e.currentTarget.style.opacity = '1')}
              >
                <Plus size={14} />
              </button>
            </div>

            {/* Search */}
            <div style={{ position: 'relative' }}>
              <Search size={12} color="var(--dim)" style={{
                position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)',
              }} />
              <input
                type="search" name="chat_search"
                aria-label={t('analyst.search_chats_placeholder')}
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder={t('analyst.search_chats_placeholder')}
                style={{
                  width: '100%', background: 'var(--surface-2)',
                  border: '1px solid var(--border)', borderRadius: 7,
                  padding: '6px 10px 6px 28px', fontSize: 12, color: 'var(--text)',
                  outline: 'none',
                }}
              />
              {search && (
                <button onClick={() => setSearch('')} style={{
                  all: 'unset', position: 'absolute', right: 8, top: '50%',
                  transform: 'translateY(-50%)', cursor: 'pointer', color: 'var(--dim)',
                }}>
                  <X size={11} />
                </button>
              )}
            </div>
          </div>

          {/* Chat list */}
          <div data-tour="an.chats" style={{ flex: 1, overflowY: 'auto', padding: '8px 6px' }}>
            {chatsError ? (
              <div style={{ padding: 16, textAlign: 'center', fontSize: 12, color: '#ef4444' }}>
                {chatsError}
              </div>
            ) : chats.length === 0 ? (
              <div style={{ padding: 16, textAlign: 'center', fontSize: 12, color: 'var(--dim)' }}>
                {t('analyst.no_chats_yet')}
              </div>
            ) : (
              <>
                {favorites.length > 0 && (
                  <>
                    <div style={{
                      fontSize: 10, fontWeight: 700, color: 'var(--dim)',
                      textTransform: 'uppercase', letterSpacing: '0.08em',
                      padding: '4px 8px 2px', display: 'flex', alignItems: 'center', gap: 4,
                    }}>
                      <Star size={9} fill="var(--dim)" /> {t('analyst.favorites_header')}
                    </div>
                    {favorites.map(c => (
                      <ChatItem
                        key={c.id} chat={c}
                        active={c.id === activeChatId}
                        onSelect={() => setActive(c.id)}
                        onFavorite={() => toggleFav(c.id)}
                        onDelete={() => handleDelete(c.id)}
                      />
                    ))}
                    <div style={{ height: 8 }} />
                  </>
                )}

                {recent.length > 0 && (
                  <>
                    {favorites.length > 0 && (
                      <div style={{
                        fontSize: 10, fontWeight: 700, color: 'var(--dim)',
                        textTransform: 'uppercase', letterSpacing: '0.08em',
                        padding: '4px 8px 2px',
                      }}>
                        {t('analyst.recent_header')}
                      </div>
                    )}
                    {recent.map(c => (
                      <ChatItem
                        key={c.id} chat={c}
                        active={c.id === activeChatId}
                        onSelect={() => setActive(c.id)}
                        onFavorite={() => toggleFav(c.id)}
                        onDelete={() => handleDelete(c.id)}
                      />
                    ))}
                  </>
                )}
              </>
            )}
          </div>
        </div>

        {/* ── MAIN PANEL ──────────────────────────────────────────── */}
        <div style={{
          flex: 1, display: 'flex', flexDirection: 'column',
          background: 'var(--bg)', overflow: 'hidden',
        }}>
          {!activeChatId ? (
            <EmptyState onCreate={() => handleNewChat()} />
          ) : (
            <>
              {/* Chat header */}
              <div style={{
                padding: '12px 20px', borderBottom: '1px solid var(--border)',
                display: 'flex', alignItems: 'center', gap: 12,
                background: 'var(--surface)', flexShrink: 0,
              }}>
                <MessageSquare size={14} color="var(--accent)" />
                <span style={{ fontSize: 13, fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {activeChat?.title ?? t('analyst.chat_fallback_title')}
                </span>

                {/* Session picker */}
                {completedSessions.length > 0 && (
                  <div data-tour="an.session" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('analyst.session_label')}</span>
                    <select
                      name="chat_session"
                      aria-label={t('analyst.session_label')}
                      value={activeChat?.session_id ?? ''}
                      onChange={async e => {
                        const sid = e.target.value || null
                        setChats(prev => prev.map(c =>
                          c.id === activeChatId ? { ...c, session_id: sid } : c,
                        ))
                        if (activeChatId) await updateChat(activeChatId, { session_id: sid })
                      }}
                      style={{
                        background: 'var(--surface-2)', border: '1px solid var(--border)',
                        borderRadius: 6, padding: '4px 8px', fontSize: 11,
                        color: 'var(--text)', cursor: 'pointer', maxWidth: 160,
                      }}
                    >
                      <option value="">{t('analyst.session_general_option')}</option>
                      {completedSessions.map(s => (
                        <option key={s.session_id} value={s.session_id}>{s.name}</option>
                      ))}
                    </select>
                    {activeChat?.session_id && (
                      <span style={{
                        fontSize: 9, padding: '2px 6px', borderRadius: 4,
                        background: 'rgba(34,197,94,0.1)', color: '#22c55e', fontWeight: 600,
                      }}>
                        RAG
                      </span>
                    )}
                  </div>
                )}

                {/* Sources filter */}
                {sourceTypes.length > 0 && activeChat?.session_id && (
                  <SourcesFilter
                    dataTour="an.sources"
                    sources={activeChat?.data_sources ?? []}
                    allTypes={sourceTypes}
                    onChange={handleSourcesChange}
                  />
                )}
              </div>

              {/* Messages area */}
              <div
                ref={msgsRef}
                data-tour="an.thread"
                onScroll={handleScroll}
                style={{
                  flex: 1,
                  // `minHeight: 0` reads as noise until it bites: a flex child
                  // defaults to `min-height: auto`, i.e. it refuses to shrink
                  // below the height of its own content. Without it a long
                  // thread grows this row past the pane and pushes the composer
                  // out of the viewport instead of scrolling inside itself.
                  minHeight: 0,
                  overflowY: 'auto',
                  padding: '20px 24px',
                  display: 'flex', flexDirection: 'column',
                }}
              >
                {loadingMore && (
                  <div style={{ textAlign: 'center', padding: '8px 0', color: 'var(--dim)', fontSize: 11 }}>
                    <Spinner size={12} /> {t('analyst.loading_older_messages')}
                  </div>
                )}

                {loadingMsgs ? (
                  <div data-testid="messages-loading-spinner" style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Spinner size={18} />
                  </div>
                ) : msgsError ? (
                  <div style={{
                    flex: 1, display: 'flex', flexDirection: 'column',
                    alignItems: 'center', justifyContent: 'center', gap: 12, textAlign: 'center',
                  }}>
                    <AlertTriangle size={28} color="#ef4444" style={{ opacity: 0.8 }} />
                    <div style={{ fontSize: 13, color: 'var(--text)', maxWidth: 360 }}>{msgsError}</div>
                    <button
                      onClick={() => activeChatId && loadMessages(activeChatId)}
                      style={{ all: 'unset', cursor: 'pointer', padding: '7px 16px', borderRadius: 8, background: 'var(--accent)', color: '#fff', fontSize: 12, fontWeight: 600 }}
                    >
                      {t('analyst.retry')}
                    </button>
                  </div>
                ) : messages.length === 0 ? (
                  <div style={{
                    flex: 1, display: 'flex', flexDirection: 'column',
                    alignItems: 'center', justifyContent: 'center',
                    gap: 10, color: 'var(--dim)', textAlign: 'center',
                  }}>
                    <Bot size={32} strokeWidth={1} style={{ opacity: 0.3 }} />
                    <div style={{ fontSize: 13 }}>
                      {activeChat?.session_id
                        ? t('analyst.empty_messages_with_session')
                        : t('analyst.empty_messages_general')}
                    </div>
                    {/* Suggestion chips */}
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'center', marginTop: 8 }}>
                      {(activeChat?.session_id
                        ? ['analyst.chip_model_accuracy', 'analyst.chip_skus_attention', 'analyst.chip_inventory_risks']
                        : ['analyst.chip_what_is_wape', 'analyst.chip_how_platform_works', 'analyst.chip_available_models']
                      ).map(chipKey => (
                        <button
                          key={chipKey}
                          onClick={() => { setInput(t(chipKey)); inputRef.current?.focus() }}
                          style={{
                            all: 'unset', cursor: 'pointer',
                            padding: '6px 12px', borderRadius: 20,
                            background: 'var(--surface-2)', border: '1px solid var(--border)',
                            fontSize: 12, color: 'var(--muted)', transition: 'all 0.15s',
                          }}
                          onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--accent)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--text)' }}
                          onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--border)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--muted)' }}
                        >
                          {t(chipKey)}
                        </button>
                      ))}
                    </div>

                    {/* AI-generated suggested questions */}
                    {suggestedQs.length > 0 && (
                      <div style={{ marginTop: 20, width: '100%', maxWidth: 480, textAlign: 'left' }}>
                        <div style={{
                          fontSize: 11, fontWeight: 700, color: 'var(--dim)',
                          textTransform: 'uppercase' as const, letterSpacing: '0.07em', marginBottom: 10,
                          textAlign: 'center',
                        }}>
                          {t('analyst.suggested_questions_header')}
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap' as const, gap: 8, justifyContent: 'center' }}>
                          {suggestedQs.map(q => (
                            <button
                              key={q.text}
                              onClick={() => { setInput(q.text); inputRef.current?.focus() }}
                              style={{
                                all: 'unset', cursor: 'pointer',
                                padding: '6px 12px', borderRadius: 20, fontSize: 12,
                                border: '1px solid var(--border)', color: 'var(--muted)',
                                background: 'var(--surface-2)',
                                transition: 'all 0.15s',
                              }}
                              onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--accent)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--accent)' }}
                              onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--border)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--muted)' }}
                            >
                              {q.text}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  // A short thread reads from the top with the free space
                  // BELOW it, which is what WhatsApp does and what /mensajes
                  // does. This used to carry `marginTop: auto`, which swallowed
                  // the free space and pinned two messages to the bottom edge
                  // against the composer — the two chat screens in the same app
                  // disagreeing about which way a conversation stacks.
                  <div style={{ display: 'flex', flexDirection: 'column' }}>
                    {messages.map(msg => <MessageBubble key={msg.id} msg={msg} />)}
                    {sending && <TypingBubble />}
                  </div>
                )}
                <div ref={bottomRef} />
              </div>

              {/* Input bar */}
              <div style={{
                padding: '12px 20px', borderTop: '1px solid var(--border)',
                background: 'var(--surface)', flexShrink: 0,
              }}>
                <div data-tour="an.input" style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
                  {/* Sources filter (mobile-friendly placement) */}
                  {sourceTypes.length > 0 && activeChat?.session_id && (
                    <div style={{ flexShrink: 0 }}>
                      <SourcesFilter
                        sources={activeChat?.data_sources ?? []}
                        allTypes={sourceTypes}
                        onChange={handleSourcesChange}
                      />
                    </div>
                  )}

                  <textarea
                    ref={inputRef}
                    name="analyst_message"
                    aria-label={t('analyst.input_placeholder')}
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={onKeyDown}
                    placeholder={creatingChat ? t('analyst.creating_chat_placeholder') : t('analyst.input_placeholder')}
                    disabled={creatingChat}
                    rows={1}
                    style={{
                      flex: 1, resize: 'none', minHeight: 40, maxHeight: 160,
                      background: 'var(--surface-2)', border: '1px solid var(--border)',
                      borderRadius: 10, padding: '10px 14px',
                      fontSize: 13, color: 'var(--text)', lineHeight: 1.5,
                      outline: 'none', fontFamily: 'inherit',
                      transition: 'border-color 0.15s',
                      opacity: creatingChat ? 0.6 : 1,
                    }}
                    onFocus={e => { e.currentTarget.style.borderColor = 'color-mix(in srgb, var(--accent) 40%, transparent)' }}
                    onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)' }}
                  />

                  <button
                    onClick={() => { handleSend(input); setInput('') }}
                    disabled={!input.trim() || sending || creatingChat}
                    style={{
                      all: 'unset', width: 40, height: 40, borderRadius: 10,
                      background: input.trim() && !sending && !creatingChat ? 'var(--accent)' : 'var(--surface-2)',
                      border: '1px solid var(--border)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      cursor: input.trim() && !sending && !creatingChat ? 'pointer' : 'default',
                      flexShrink: 0, transition: 'all 0.15s',
                    }}
                  >
                    {sending || creatingChat ? <Spinner size={14} /> : <Send size={14} color={input.trim() && !sending && !creatingChat ? '#fff' : 'var(--dim)'} />}
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

    </>
  )
}
