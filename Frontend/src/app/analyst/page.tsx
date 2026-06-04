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
import {
  Plus, Search, Star, Trash2, Bot, User, Send,
  Sparkles, MessageSquare, ChevronDown, X, Filter,
  CheckSquare, Square,
} from 'lucide-react'

// ── Colour helpers ─────────────────────────────────────────────────────────────
const SOURCE_META: Record<string, { label: string; color: string }> = {
  rag:       { label: 'RAG',     color: '#818cf8' },
  fallback:  { label: 'Fallback', color: '#f59e0b' },
  general:   { label: 'General', color: '#22c55e' },
  off_topic: { label: 'Off-topic', color: '#f59e0b' },
  no_access: { label: 'No Access', color: '#ef4444' },
  error:     { label: 'Error',   color: '#ef4444' },
}

function fmtTime(iso: string) {
  const d = new Date(iso)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function fmtRelative(iso: string) {
  const diff = Date.now() - new Date(iso).getTime()
  if (diff < 60_000)  return 'just now'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`
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
            animation: 'pulse 1.4s ease-in-out infinite',
            animationDelay: `${i * 0.2}s`,
          }} />
        ))}
      </div>
    </div>
  )
}

// ── Message bubble ─────────────────────────────────────────────────────────────
function MessageBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user'
  const src    = msg.source ? SOURCE_META[msg.source] : null
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
        background: isUser ? 'rgba(129,140,248,0.15)' : 'rgba(34,197,94,0.12)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        {isUser ? <User size={13} color="#818cf8" /> : <Bot size={13} color="#22c55e" />}
      </div>

      {/* Bubble */}
      <div style={{
        maxWidth: '75%', display: 'flex', flexDirection: 'column',
        alignItems: isUser ? 'flex-end' : 'flex-start', gap: 3,
      }}>
        <div style={{
          background: isUser ? 'rgba(99,102,241,0.14)' : 'var(--surface-2)',
          border: `1px solid ${isUser ? 'rgba(99,102,241,0.22)' : 'var(--border)'}`,
          borderRadius: isUser ? '16px 4px 16px 16px' : '4px 16px 16px 16px',
          padding: '10px 14px',
        }}>
          {isUser
            ? <div style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{msg.content}</div>
            : <Md text={msg.content} />}
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span style={{ fontSize: 10, color: 'var(--dim)' }}>{fmtTime(msg.created_at)}</span>
          {src && !isUser && (
            <span style={{
              fontSize: 9, fontWeight: 600, letterSpacing: '0.05em',
              color: src.color, background: src.color + '18',
              borderRadius: 4, padding: '1px 6px',
            }}>
              {src.label}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Sources filter popover ─────────────────────────────────────────────────────
function SourcesFilter({
  sources, allTypes, onChange,
}: {
  sources: string[]
  allTypes: ChatSourceType[]
  onChange: (v: string[]) => void
}) {
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
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(o => !o)}
        title="Filter data sources"
        style={{
          all: 'unset', cursor: 'pointer',
          display: 'flex', alignItems: 'center', gap: 5,
          padding: '6px 10px', borderRadius: 7,
          background: active ? 'rgba(129,140,248,0.1)' : 'var(--surface-2)',
          border: `1px solid ${active ? 'rgba(129,140,248,0.3)' : 'var(--border)'}`,
          fontSize: 11, color: active ? 'var(--accent)' : 'var(--muted)',
          transition: 'all 0.15s',
        }}
      >
        <Filter size={11} />
        {active ? `${sources.length} source${sources.length > 1 ? 's' : ''}` : 'All sources'}
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
            Data Sources
          </div>
          {allTypes.map(t => {
            const checked = sources.length === 0 || sources.includes(t.id)
            const selected = sources.includes(t.id)
            return (
              <div
                key={t.id}
                onClick={() => toggle(t.id)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 12px', cursor: 'pointer', fontSize: 12,
                  color: selected ? 'var(--text)' : 'var(--muted)',
                  background: selected ? 'rgba(129,140,248,0.06)' : 'transparent',
                  transition: 'all 0.1s',
                }}
              >
                {selected
                  ? <CheckSquare size={13} color="var(--accent)" />
                  : <Square size={13} color="var(--dim)" />}
                {t.label}
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
              Clear filter (use all)
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
          ? 'rgba(129,140,248,0.1)'
          : hover ? 'rgba(255,255,255,0.03)' : 'transparent',
        border: `1px solid ${active ? 'rgba(129,140,248,0.25)' : 'transparent'}`,
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
        <span style={{ fontSize: 10, color: 'var(--dim)' }}>{fmtRelative(chat.last_message_at)}</span>
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
            title={chat.is_favorite ? 'Remove from favorites' : 'Add to favorites'}
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
              title="Confirm delete"
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
              title="Delete chat"
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
  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      gap: 16, padding: '40px 32px', textAlign: 'center',
    }}>
      <div style={{
        width: 64, height: 64, borderRadius: '50%',
        background: 'rgba(129,140,248,0.08)',
        border: '1px solid rgba(129,140,248,0.15)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <Sparkles size={28} color="var(--accent)" strokeWidth={1.5} />
      </div>
      <div>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>AI Analyst</div>
        <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.6, maxWidth: 300 }}>
          Ask questions about your forecast results, model accuracy, inventory, or anything
          about the platform.
        </div>
      </div>
      <Button variant="primary" icon={<Plus size={14} />} onClick={onCreate}>
        New Chat
      </Button>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function AnalystPage() {
  const [chats,       setChats]       = useState<Chat[]>([])
  const [activeChatId, setActive]     = useState<string | null>(null)
  const [messages,    setMessages]    = useState<ChatMessage[]>([])
  const [hasMore,     setHasMore]     = useState(false)
  const [loadingMsgs, setLoadingMsgs] = useState(false)
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
    listChats().then(setChats).catch(console.error)
    getSessions().then(setSessions).catch(console.error)
    getDataSourceTypes().then(setSourceTypes).catch(console.error)

    const bp = (typeof window !== 'undefined' ? localStorage.getItem('bp') : null) || 'distributor'
    getSuggestedQuestions(bp, true, false)
      .then(setSuggestedQs)
      .catch(() => {})
  }, [])

  // ── Load messages when chat changes ───────────────────────────────────────
  useEffect(() => {
    if (!activeChatId) { setMessages([]); setHasMore(false); return }
    setLoadingMsgs(true)
    getChatMessages(activeChatId, 30)
      .then(page => {
        setMessages(page.messages)
        setHasMore(page.has_more)
        setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'instant' }), 50)
      })
      .catch(console.error)
      .finally(() => setLoadingMsgs(false))
  }, [activeChatId])

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
        ? 'Too many requests — please wait a moment before sending another message.'
        : raw.includes('500') || raw.toLowerCase().includes('server error')
        ? 'Server error — please try again in a few seconds.'
        : err instanceof TypeError || raw.toLowerCase().includes('network') || raw.toLowerCase().includes('fetch')
        ? 'Connection error — please check your internet connection.'
        : 'Failed to get a response. Please try again.'
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
  }, [activeChatId, chats, sending, scrollToBottom])

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
  const handleDelete = async (chatId: string) => {
    await deleteChat(chatId)
    setChats(prev => prev.filter(c => c.id !== chatId))
    if (activeChatId === chatId) setActive(null)
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
      {/* Inject keyframe animation */}
      <style>{`
        @keyframes slideUp {
          from { opacity: 0; transform: translateY(8px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes pulse {
          0%, 80%, 100% { transform: scale(0); opacity: 0.4; }
          40% { transform: scale(1); opacity: 1; }
        }
        .chat-item-hover:hover { background: rgba(255,255,255,0.03) !important; }
      `}</style>

      <div style={{
        display: 'flex',
        height: 'calc(100vh - 56px)',
        margin: '-24px',
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
                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>AI Analyst</span>
              </div>
              <button
                onClick={() => handleNewChat()}
                title="New chat"
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
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Search chats…"
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
          <div style={{ flex: 1, overflowY: 'auto', padding: '8px 6px' }}>
            {chats.length === 0 ? (
              <div style={{ padding: 16, textAlign: 'center', fontSize: 12, color: 'var(--dim)' }}>
                No chats yet
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
                      <Star size={9} fill="var(--dim)" /> Favorites
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
                        Recent
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
                  {activeChat?.title ?? 'Chat'}
                </span>

                {/* Session picker */}
                {completedSessions.length > 0 && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 11, color: 'var(--dim)' }}>Session:</span>
                    <select
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
                      <option value="">— General —</option>
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
                    sources={activeChat?.data_sources ?? []}
                    allTypes={sourceTypes}
                    onChange={handleSourcesChange}
                  />
                )}
              </div>

              {/* Messages area */}
              <div
                ref={msgsRef}
                onScroll={handleScroll}
                style={{
                  flex: 1, overflowY: 'auto',
                  padding: '20px 24px',
                  display: 'flex', flexDirection: 'column',
                }}
              >
                {loadingMore && (
                  <div style={{ textAlign: 'center', padding: '8px 0', color: 'var(--dim)', fontSize: 11 }}>
                    <Spinner size={12} /> Loading older messages…
                  </div>
                )}

                {loadingMsgs ? (
                  <div data-testid="messages-loading-spinner" style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Spinner size={18} />
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
                        ? 'Ask about your forecast results, accuracy, inventory, or SKU trends.'
                        : 'Ask me anything about forecasting or the platform.'}
                    </div>
                    {/* Suggestion chips */}
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'center', marginTop: 8 }}>
                      {(activeChat?.session_id
                        ? ['How accurate are my models?', 'Which SKUs need attention?', 'Summarize inventory risks']
                        : ['What is WAPE?', 'How does the platform work?', 'What models are available?']
                      ).map(chip => (
                        <button
                          key={chip}
                          onClick={() => { setInput(chip); inputRef.current?.focus() }}
                          style={{
                            all: 'unset', cursor: 'pointer',
                            padding: '6px 12px', borderRadius: 20,
                            background: 'var(--surface-2)', border: '1px solid var(--border)',
                            fontSize: 12, color: 'var(--muted)', transition: 'all 0.15s',
                          }}
                          onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--accent)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--text)' }}
                          onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--border)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--muted)' }}
                        >
                          {chip}
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
                          Preguntas frecuentes
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
                              onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = '#818cf8'; (e.currentTarget as HTMLButtonElement).style.color = '#818cf8' }}
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
                  <>
                    {messages.map(msg => <MessageBubble key={msg.id} msg={msg} />)}
                    {sending && <TypingBubble />}
                  </>
                )}
                <div ref={bottomRef} />
              </div>

              {/* Input bar */}
              <div style={{
                padding: '12px 20px', borderTop: '1px solid var(--border)',
                background: 'var(--surface)', flexShrink: 0,
              }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
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
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={onKeyDown}
                    placeholder={creatingChat ? 'Creating chat…' : 'Ask anything… (Enter to send, Shift+Enter for new line)'}
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
                    onFocus={e => { e.currentTarget.style.borderColor = 'rgba(129,140,248,0.4)' }}
                    onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)' }}
                  />

                  <button
                    onClick={() => { handleSend(input); setInput('') }}
                    disabled={!input.trim() || sending || creatingChat}
                    style={{
                      all: 'unset', width: 40, height: 40, borderRadius: 10,
                      background: input.trim() && !sending && !creatingChat ? '#6366f1' : 'var(--surface-2)',
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
