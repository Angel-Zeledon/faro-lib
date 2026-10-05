'use client'
import {
  useState, useEffect, useRef, useCallback,
  type KeyboardEvent, type UIEvent,
} from 'react'
import {
  listChats, createChat, updateChat, deleteChat,
  getChatMessages, sendChatMessage, getAssistantWelcome, isApiError,
  starChatMessage, listFavoriteMessages,
} from '@/lib/api'
import type { AssistantWelcome, Chat, ChatMessage, FavoriteMessage } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import Button from '@/components/ui/Button'
import { useLanguage } from '@/contexts/LanguageContext'
import { useCapabilities } from '@/lib/capabilities'
import { useToast } from '@/contexts/ToastContext'
import { MessageBubble, TypingBubble, Welcome, previewText, clampStyle } from './parts'
import AssistantMobile from './AssistantMobile'
import FavoritesList from './Favorites'
import { AssistantAvatar } from '@/components/brand/AssistantAvatar'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import {
  Plus, Search, Star, Trash2, Send, Mic, Square,
  MessageSquare, X, AlertTriangle,
} from 'lucide-react'
import { useSpeechToText } from '@/hooks/useSpeechToText'

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

function fmtRelative(iso: string, t: (k: string) => string) {
  const diff = Date.now() - new Date(iso).getTime()
  if (diff < 60_000)  return t('analyst.time_just_now')
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}${t('analyst.time_minutes_ago_suffix')}`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}${t('analyst.time_hours_ago_suffix')}`
  return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' })
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
          ...clampStyle(1), flex: 1, minWidth: 0,
        }} title={chat.title}>
          {chat.title}
        </span>
      </div>

      {chat.last_message_preview && (
        <div style={{
          fontSize: 11, lineHeight: 1.35, color: 'var(--dim)',
          ...clampStyle(2),
          paddingLeft: 17, paddingRight: 28,
        }}>
          {previewText(chat.last_message_preview)}
        </div>
      )}

      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        paddingLeft: 17, marginTop: 3,
      }}>
        <span style={{ fontSize: 10, color: 'var(--dim)' }}>{fmtRelative(chat.last_message_at, t)}</span>
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
              color: chat.is_favorite ? '#B7791F' : 'var(--dim)',
              background: 'var(--surface)',
            }}
          >
            <Star size={11} fill={chat.is_favorite ? '#B7791F' : 'none'} />
          </button>
          {confirmDel ? (
            <button
              onClick={e => { e.stopPropagation(); onDelete() }}
              title={t('analyst.confirm_delete')}
              style={{
                all: 'unset', width: 22, height: 22, borderRadius: 4, cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: '#ef444420', color: '#C0504D',
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
function EmptyState({
  onCreate, welcome, onAsk, disabled,
}: {
  onCreate: () => void
  welcome: AssistantWelcome | null
  onAsk: (question: string) => void
  disabled: boolean
}) {
  const { t } = useLanguage()
  return (
    <div data-tour="an.start" style={{
      flex: 1, display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      gap: 16, padding: '40px 32px', textAlign: 'center', overflowY: 'auto',
    }}>
      <div style={{
        width: 64, height: 64, borderRadius: '50%', flexShrink: 0,
        background: 'color-mix(in srgb, var(--accent) 8%, transparent)',
        border: '1px solid color-mix(in srgb, var(--accent) 15%, transparent)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <AssistantAvatar size={40} />
      </div>
      {welcome ? (
        <Welcome welcome={welcome} onAsk={onAsk} disabled={disabled} />
      ) : (
        <div>
          {/* No title: the top bar already says "Asistente IA". */}
          <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.6, maxWidth: 300 }}>
            {t('analyst.empty_state_description')}
          </div>
        </div>
      )}
      <Button variant="secondary" icon={<Plus size={14} />} onClick={onCreate}>
        {t('analyst.new_chat')}
      </Button>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function AnalystPage() {
  const { t, lang } = useLanguage()
  const { undoable, addToast } = useToast()
  // Whether this deployment HAS a language model at all. Without it the answer
  // is always the same and always late: the user writes a question, waits out
  // the request, and reads "the assistant could not answer". Asking first turns
  // that into a sentence at the top of the screen, before anybody types.
  const { can } = useCapabilities()
  const assistantOff = !can('assistant')
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
  const [welcome,     setWelcome]     = useState<AssistantWelcome | null>(null)
  // Saved messages. null = not loaded yet; the sidebar tab and the phone's
  // Favorites tab both read this one list.
  const [savedMessages, setSavedMessages] = useState<FavoriteMessage[] | null>(null)
  const [favoritesError, setFavoritesError] = useState<string | null>(null)
  const [view,        setView]        = useState<'chats' | 'favorites'>('chats')
  // A favorite being opened: the message to bring into view once its chat loads.
  const [focusMsgId,  setFocusMsgId]  = useState<string | null>(null)
  const focusPagesRef = useRef(0)
  const narrow = useIsNarrow()
  const msgsRef    = useRef<HTMLDivElement>(null)
  const inputRef   = useRef<HTMLTextAreaElement>(null)
  const bottomRef  = useRef<HTMLDivElement>(null)
  const loadingRef = useRef(false)
  const justCreatedRef = useRef<string | null>(null)

  const activeChat = chats.find(c => c.id === activeChatId) ?? null

  // Dictation: the browser transcribes, the words land in the message box as
  // they are spoken. `inputValueRef` hands the hook the box's content at the
  // moment recording starts without re-creating the hook on every keystroke.
  const inputValueRef = useRef('')
  inputValueRef.current = input
  const speech = useSpeechToText({
    lang,
    getBase: () => inputValueRef.current,
    onText: setInput,
  })

  // ── Bootstrap ──────────────────────────────────────────────────────────────
  useEffect(() => {
    listChats().then(setChats).catch((e: unknown) => {
      setChatsError(e instanceof Error ? e.message : t('analyst.err_load_chats'))
    })
    // The greeting is decoration over a working screen: if it fails, the chat
    // still works and the screen falls back to its generic description.
    getAssistantWelcome().then(setWelcome).catch(console.error)
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
    // A chat handleSend just created to carry a question: it is empty on the
    // server and already shows the optimistic question here. Resetting would
    // wipe that question, and loading would race the POST that stores it —
    // the GET can land after the question is saved and before the answer,
    // and the thread then shows the question twice.
    if (activeChatId && justCreatedRef.current === activeChatId) {
      justCreatedRef.current = null
      return
    }
    setMessages([]); setHasMore(false); setMsgsError(null)
    if (!activeChatId) return
    loadMessages(activeChatId)
  }, [activeChatId, loadMessages])

  // ── Scroll to bottom on new message ───────────────────────────────────────
  const scrollToBottom = useCallback((instant = false) => {
    bottomRef.current?.scrollIntoView({ behavior: instant ? 'instant' : 'smooth' })
  }, [])

  // ── Infinite scroll: load older messages when near top ────────────────────
  // Takes the scroll container rather than the event, so the phone layout —
  // where the whole page scrolls — can call it with `.page-content`.
  const loadOlder = useCallback(async (el: HTMLElement) => {
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
  const handleScroll = useCallback(
    (e: UIEvent<HTMLDivElement>) => { loadOlder(e.currentTarget) }, [loadOlder])

  // ── Create a new chat ────────────────────────────────────────────────────
  const handleNewChat = useCallback(async (sessionId?: string) => {
    try {
      const chat = await createChat(sessionId ? { session_id: sessionId } : {})
      setChats(prev => [chat, ...prev])
      setView('chats')
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
        justCreatedRef.current = chat.id
        setChats(prev => [chat, ...prev])
        setActive(chat.id)
        targetId = chat.id
      } catch (e) { console.error(e); setCreatingChat(false); return }
      setCreatingChat(false)
    }

    setSending(true)
    // Stamped before the call so the catch can tell a slow model from a broken
    // one — the two need different advice.
    const startedAt = Date.now()
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

      const res = await sendChatMessage(targetId, q, lang)

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
      // An ApiError already carries the user's-language sentence for its code
      // or HTTP class (lib/errorMessage.ts); only a non-API failure (a dropped
      // connection surfaces as a TypeError) needs its own copy here.
      //
      // A failure that took the whole window is the model being slow, not a
      // broken server, and "intenta de nuevo en unos segundos" is the wrong
      // advice for it: measured against a local model, the proxy cut the request
      // at 30.0s while the answer landed at 63s. Say which one happened.
      const tookTooLong = Date.now() - startedAt >= 20_000
      const friendly = (isApiError(err) && err.code === 'ai_unavailable') || tookTooLong
        ? t('analyst.err_slow_model')
        : isApiError(err)
        ? err.message
        : err instanceof TypeError
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
      // Keep the question. It used to be filtered out with the optimistic
      // message, so a failed answer erased what the user had asked and they had
      // to retype it to try again.
      setMessages(prev => [...prev, errMsg])
    } finally {
      setSending(false)
      inputRef.current?.focus()
    }
  }, [activeChatId, chats, sending, creatingChat, scrollToBottom, t, lang])

  // ── Keyboard shortcuts ────────────────────────────────────────────────────
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); speech.stop(); handleSend(input); setInput('') }
  }
  // ── Favorite messages ─────────────────────────────────────────────────────
  function loadFavorites() {
    setFavoritesError(null)
    listFavoriteMessages().then(setSavedMessages).catch(() => {
      // The count in the tab is a nicety; the error shows when the tab is open.
      setFavoritesError(t('analyst.favorites_error'))
    })
  }

  const setStar = useCallback(async (msg: ChatMessage, starred: boolean) => {
    const apply = (on: boolean) => setMessages(prev => prev.map(m =>
      m.id === msg.id ? { ...m, starred_at: on ? new Date().toISOString() : null } : m))
    apply(starred)
    // Leaves the saved list at once; the server confirms below.
    if (!starred) setSavedMessages(prev => prev && prev.filter(f => f.id !== msg.id))
    try {
      await starChatMessage(msg.id, starred)
      // The list needs the chat title and the question, which only the server joins.
      if (starred) loadFavorites()
    } catch {
      apply(!starred)
      loadFavorites()
      addToast(t('analyst.star_failed'), '', 'error')
    }
  }, [addToast, t])

  const toggleStar = (msg: ChatMessage) => setStar(msg, !msg.starred_at)
  const removeFavorite = (item: FavoriteMessage) =>
    setStar({ id: item.id } as ChatMessage, false)
  const openFavorite = (item: FavoriteMessage) => {
    focusPagesRef.current = 0
    setView('chats')
    setFocusMsgId(item.id)
    setActive(item.chat_id)
  }

  // Bring the opened favorite into view once its chat has loaded, paging back
  // through older messages when it is further up than the first page.
  useEffect(() => {
    if (!focusMsgId || !activeChatId || loadingMsgs) return
    if (messages.length === 0 || messages[0].chat_id !== activeChatId) return
    let cancelled = false
    if (!messages.some(m => m.id === focusMsgId)) {
      if (!hasMore || focusPagesRef.current >= 10) { setFocusMsgId(null); return }
      focusPagesRef.current += 1
      getChatMessages(activeChatId, 100, messages[0].id).then(page => {
        if (cancelled) return
        setMessages(prev => [...page.messages, ...prev])
        setHasMore(page.has_more)
      }).catch(() => setFocusMsgId(null))
      return () => { cancelled = true }
    }
    // After loadMessages' own jump to the bottom.
    const timer = setTimeout(() => {
      const el = document.querySelector<HTMLElement>(`[data-msg-id="${CSS.escape(focusMsgId)}"]`)
      if (el) {
        // Scroll only the thread's own scroller: scrollIntoView would also
        // move the clipped app shell around it and push the headers off screen.
        let sc: HTMLElement | null = el.parentElement
        while (sc && !/(auto|scroll)/.test(getComputedStyle(sc).overflowY)) sc = sc.parentElement
        if (sc) {
          const delta = el.getBoundingClientRect().top - sc.getBoundingClientRect().top
          sc.scrollTop += delta - Math.max(0, (sc.clientHeight - el.offsetHeight) / 2)
        }
        el.classList.add('msg-focus')
        setTimeout(() => el.classList.remove('msg-focus'), 2000)
      }
      setFocusMsgId(null)
    }, 160)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [focusMsgId, activeChatId, loadingMsgs, messages, hasMore])

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

  // ── Separate favorites / recent ───────────────────────────────────────────
  const filtered  = chats.filter(c => !search || c.title.toLowerCase().includes(search.toLowerCase()))
  const favorites = filtered.filter(c => c.is_favorite)
  const recent    = filtered.filter(c => !c.is_favorite)

  // Retry the newest failed answer: drop the error bubble and the optimistic copy
  // of the question (handleSend adds a fresh one), then ask the same thing again.
  // Only the last message can be retried, so an old error never resends a
  // question the thread has moved past.
  const lastMessage = messages[messages.length - 1]
  const retryable = !!lastMessage && lastMessage.source === 'error' && !sending && !assistantOff
  const retryLast = useCallback(() => {
    const last = messages[messages.length - 1]
    if (!last || last.source !== 'error' || sending) return
    const idx = messages.map(m => m.role).lastIndexOf('user')
    const question = idx >= 0 ? messages[idx].content : ''
    if (!question) return
    setMessages(prev => prev.filter((m, i) =>
      m.id !== last.id && !(i === idx && m.id.startsWith('opt-'))))
    handleSend(question)
  }, [messages, sending, handleSend])

  // A suggestion is a complete question about the user's own data: send it.
  // With no chat open, handleSend creates one first.
  const askNow = (question: string) => { if (!assistantOff) handleSend(question) }

  // Phone: a chat app (AssistantMobile) over the same state and requests.
  if (narrow) {
    return (
      <AssistantMobile
        chats={chats}
        chatsError={chatsError}
        activeChat={activeChat}
        activeChatId={activeChatId}
        onOpenChat={setActive}
        messages={messages}
        loadingMsgs={loadingMsgs}
        msgsError={msgsError}
        onRetryMessages={() => activeChatId && loadMessages(activeChatId)}
        loadingMore={loadingMore}
        onNearTop={loadOlder}
        sending={sending}
        creatingChat={creatingChat}
        input={input}
        onInput={setInput}
        onSend={q => { if (!assistantOff) handleSend(q) }}
        onRetry={retryable ? retryLast : undefined}
        welcome={welcome}
        assistantOff={assistantOff}
        onToggleFavorite={toggleFav}
        onDelete={handleDelete}
        relTime={iso => fmtRelative(iso, t)}
        onToggleStar={toggleStar}
        favorites={savedMessages}
        favoritesError={favoritesError}
        onLoadFavorites={loadFavorites}
        onOpenFavorite={openFavorite}
        onRemoveFavorite={removeFavorite}
      />
    )
  }

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
                <MessageSquare size={14} color="var(--accent)" />
                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>{t('analyst.chats_title')}</span>
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

            {/* Conversations / saved messages */}
            <div role="tablist" aria-label={t('analyst.chats_title')} style={{
              display: 'flex', gap: 3, padding: 3, marginBottom: 10, borderRadius: 8,
              background: 'var(--surface-2)', border: '1px solid var(--border)',
            }}>
              {(['chats', 'favorites'] as const).map(id => {
                const on = view === id
                return (
                  <button
                    key={id} role="tab" type="button" aria-selected={on}
                    data-testid={`assistant-tab-${id}`}
                    onClick={() => { setView(id); if (id === 'favorites') loadFavorites() }}
                    style={{
                      all: 'unset', boxSizing: 'border-box', flex: 1, height: 26, borderRadius: 6, cursor: 'pointer',
                      display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5,
                      fontSize: 12, fontWeight: 600,
                      color: on ? 'var(--text)' : 'var(--muted)',
                      background: on ? 'var(--surface)' : 'transparent',
                      boxShadow: on ? '0 1px 2px rgba(0,0,0,0.08)' : 'none',
                    }}
                  >
                    {id === 'favorites' && <Star size={11} aria-hidden="true" fill={on ? '#B7791F' : 'none'} color={on ? '#B7791F' : 'currentColor'} />}
                    {id === 'chats' ? t('analyst.tab_chats') : t('analyst.tab_favorites')}
                    {id === 'favorites' && savedMessages && savedMessages.length > 0 && (
                      <span style={{ fontSize: 10, color: 'var(--dim)', fontWeight: 500 }}>{savedMessages.length}</span>
                    )}
                  </button>
                )
              })}
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
              <div style={{ padding: 16, textAlign: 'center', fontSize: 12, color: '#C0504D' }}>
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
                        onSelect={() => { setView('chats'); setActive(c.id) }}
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
                        onSelect={() => { setView('chats'); setActive(c.id) }}
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
          {/* Above the branch on purpose. This used to sit inside the composer,
              which only renders once a chat is open — so the reader met an
              inviting empty state, created a chat, typed a question, and only
              THEN learned that this installation has no model. The sentence
              belongs before the first click, not after the third. */}
          {assistantOff && (
            <div role="status" style={{
              display: 'flex', gap: 8, alignItems: 'flex-start',
              padding: '12px 16px', margin: '12px 16px 0', borderRadius: 8,
              background: 'var(--surface-2)', border: '1px solid var(--border)',
              fontSize: 12, color: 'var(--text)', lineHeight: 1.55, flexShrink: 0,
            }}>
              <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2, color: '#B7791F' }} aria-hidden="true" />
              <span>{t('analyst.unavailable_banner')}</span>
            </div>
          )}
          {view === 'favorites' ? (
            <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '20px 24px' }}>
              <div style={{ maxWidth: 760, margin: '0 auto' }}>
                <h2 style={{ margin: '0 0 14px', fontSize: 15, fontWeight: 700, color: 'var(--text)' }}>
                  {t('analyst.favorites_title')}
                </h2>
                <FavoritesList
                  items={savedMessages} error={favoritesError}
                  onOpen={openFavorite} onRemove={removeFavorite}
                />
              </div>
            </div>
          ) : !activeChatId ? (
            <EmptyState
              onCreate={() => handleNewChat()}
              welcome={welcome}
              onAsk={askNow}
              disabled={assistantOff || sending || creatingChat}
            />
          ) : (
            <>
              {/* Chat header */}
              <div style={{
                padding: '12px 20px', borderBottom: '1px solid var(--border)',
                display: 'flex', alignItems: 'center', gap: 12,
                background: 'var(--surface)', flexShrink: 0,
              }}>
                <MessageSquare size={14} color="var(--accent)" />
                <span style={{ fontSize: 13, fontWeight: 600, flex: 1, overflow: 'hidden', overflowWrap: 'anywhere', }}>
                  {activeChat?.title ?? t('analyst.chat_fallback_title')}
                </span>

              </div>

              {/* Messages area */}
              <div
                ref={msgsRef}
                data-tour="an.thread"
                role="log" aria-live="polite" aria-busy={sending}
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
                    <AlertTriangle size={28} color="#C0504D" style={{ opacity: 0.8 }} />
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
                    {welcome ? (
                      <Welcome welcome={welcome} onAsk={askNow}
                               disabled={assistantOff || sending || creatingChat} />
                    ) : (
                      <>
                        <MessageSquare size={32} strokeWidth={1} style={{ opacity: 0.3 }} />
                        <div style={{ fontSize: 13 }}>{t('analyst.empty_messages_account')}</div>
                      </>
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
                    {messages.map(msg => (
                      <MessageBubble key={msg.id} msg={msg} onToggleStar={toggleStar}
                        onRetry={retryable && msg.id === lastMessage?.id ? retryLast : undefined} />
                    ))}
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
                  <textarea
                    ref={inputRef}
                    name="analyst_message"
                    aria-label={t('analyst.input_placeholder')}
                    value={input}
                    // Typing while the microphone is open would be overwritten by the
                    // next transcript update, so a keystroke ends the dictation.
                    onChange={e => { if (speech.listening) speech.stop(); setInput(e.target.value) }}
                    onKeyDown={onKeyDown}
                    placeholder={
                      assistantOff ? t('analyst.unavailable_placeholder')
                      : speech.listening ? t('analyst.mic_listening')
                      : creatingChat ? t('analyst.creating_chat_placeholder')
                      : t('analyst.input_placeholder')
                    }
                    disabled={creatingChat || assistantOff}
                    rows={1}
                    style={{
                      flex: 1, resize: 'none', minHeight: 40, maxHeight: 160,
                      background: 'var(--surface-2)',
                      border: `1px solid ${speech.listening ? '#ef4444' : 'var(--border)'}`,
                      borderRadius: 10, padding: '10px 14px',
                      fontSize: 13, color: 'var(--text)', lineHeight: 1.5,
                      outline: 'none', fontFamily: 'inherit',
                      transition: 'border-color 0.15s',
                      opacity: creatingChat || assistantOff ? 0.6 : 1,
                    }}
                    onFocus={e => { e.currentTarget.style.borderColor = 'color-mix(in srgb, var(--accent) 40%, transparent)' }}
                    onBlur={e => { e.currentTarget.style.borderColor = speech.listening ? '#ef4444' : 'var(--border)' }}
                  />

                  {/* A disabled button swallows hover in several browsers, so the
                      explanation rides on the wrapper too. */}
                  <span
                    title={speech.supported ? undefined : t('analyst.mic_unsupported')}
                    style={{ display: 'inline-flex', flexShrink: 0 }}
                  >
                    <button
                      type="button"
                      data-testid="analyst-mic"
                      onClick={speech.toggle}
                      disabled={!speech.supported || creatingChat || assistantOff}
                      aria-pressed={speech.listening}
                      aria-label={
                        !speech.supported ? t('analyst.mic_unsupported')
                        : speech.listening ? t('analyst.mic_stop')
                        : t('analyst.mic_start')
                      }
                      title={
                        !speech.supported ? t('analyst.mic_unsupported')
                        : speech.listening ? t('analyst.mic_stop')
                        : t('analyst.mic_start')
                      }
                      style={{
                        all: 'unset', position: 'relative', width: 40, height: 40, borderRadius: 10,
                        background: speech.listening ? '#ef4444' : 'var(--surface-2)',
                        border: `1px solid ${speech.listening ? '#ef4444' : 'var(--border)'}`,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        cursor: speech.supported && !creatingChat && !assistantOff ? 'pointer' : 'not-allowed',
                        opacity: speech.supported && !assistantOff ? 1 : 0.5,
                        flexShrink: 0, transition: 'all 0.15s',
                      }}
                    >
                      {speech.listening
                        ? <Square size={14} color="#fff" fill="#fff" />
                        : <Mic size={16} color="var(--muted)" />}
                      {speech.listening && (
                        <span aria-hidden style={{
                          position: 'absolute', top: 5, right: 5, width: 7, height: 7,
                          borderRadius: '50%', background: '#fff',
                          animation: 'pulse-dot 1s ease-in-out infinite',
                        }} />
                      )}
                    </button>
                  </span>

                  <button
                    onClick={() => { speech.stop(); handleSend(input); setInput('') }}
                    aria-label={t('messages.send')}
                    title={t('messages.send')}
                    disabled={!input.trim() || sending || creatingChat || assistantOff}
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
                {/* Announced politely: it is feedback on an action, not an alarm. */}
                <div role="status" aria-live="polite">
                  {speech.error && (
                    <div style={{ marginTop: 6, fontSize: 12, color: 'var(--muted)', display: 'flex', gap: 6, alignItems: 'center' }}>
                      <span>{t(`analyst.mic_err_${speech.error}`)}</span>
                      <button
                        type="button" onClick={speech.clearError} aria-label={t('common.close')}
                        style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)', display: 'inline-flex' }}
                      ><X size={12} /></button>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>

    </>
  )
}
