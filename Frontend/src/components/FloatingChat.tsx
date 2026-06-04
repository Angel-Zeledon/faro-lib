'use client'
import { useState, useEffect, useRef } from 'react'
import { getSessions, createChat, sendChatMessage, getChatMessages } from '@/lib/api'
import type { SessionInfo, ChatMessage } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import { MessageCircle, X, Send, Bot, User, Sparkles, ExternalLink } from 'lucide-react'
import Link from 'next/link'

const SOURCE_TAG: Record<string, { label: string; color: string }> = {
  rag:       { label: 'RAG',      color: '#818cf8' },
  fallback:  { label: 'Fallback', color: '#f59e0b' },
  general:   { label: 'General',  color: '#22c55e' },
  off_topic: { label: 'Off-topic', color: '#f59e0b' },
  no_access: { label: 'No Access', color: '#ef4444' },
  error:     { label: 'Error',    color: '#ef4444' },
}

export default function FloatingChat() {
  const [open,      setOpen]      = useState(false)
  const [sessions,  setSessions]  = useState<SessionInfo[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [chatId,    setChatId]    = useState<string | null>(null)
  const [messages,  setMessages]  = useState<ChatMessage[]>([])
  const [input,     setInput]     = useState('')
  const [loading,   setLoading]   = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textRef   = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    getSessions()
      .then(list => {
        const done = list.filter(s => s.status === 'COMPLETED')
        setSessions(done)
        if (done.length) setSessionId(done[0].session_id)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (open) {
      setTimeout(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
        textRef.current?.focus()
      }, 50)
    }
  }, [open])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  async function send() {
    const q = input.trim()
    if (!q || loading) return
    setInput('')
    setLoading(true)
    try {
      // Ensure we have a chat
      let id = chatId
      if (!id) {
        const chat = await createChat(sessionId ? { session_id: sessionId } : {})
        setChatId(chat.id)
        id = chat.id
      }

      // Optimistic user message
      const optimistic: ChatMessage = {
        id: `opt-${Date.now()}`, chat_id: id,
        role: 'user', content: q,
        created_at: new Date().toISOString(),
      }
      setMessages(prev => [...prev, optimistic])

      const res = await sendChatMessage(id, q, sessionId)
      setMessages(prev => [
        ...prev.filter(m => m.id !== optimistic.id),
        res.user_message,
        res.ai_message,
      ])
    } catch (e: unknown) {
      const errMsg: ChatMessage = {
        id: `err-${Date.now()}`, chat_id: chatId ?? '',
        role: 'assistant',
        content: e instanceof Error ? e.message : 'Could not get a response.',
        source: 'error', created_at: new Date().toISOString(),
      }
      setMessages(prev => [...prev.filter(m => !m.id.startsWith('opt-')), errMsg])
    } finally {
      setLoading(false)
    }
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  const canSend = !!input.trim() && !loading

  return (
    <>
      {/* Floating action button */}
      <button
        onClick={() => setOpen(o => !o)}
        title="AI Analyst"
        style={{
          position: 'fixed', bottom: 24, right: 24, zIndex: 1000,
          width: 52, height: 52, borderRadius: '50%', border: 'none',
          background: open ? '#4338ca' : 'linear-gradient(135deg, #818cf8, #6366f1)',
          cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: '0 4px 20px rgba(99,102,241,0.50)',
          transition: 'background 0.2s, transform 0.15s',
        }}
        onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.transform = 'scale(1.08)' }}
        onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.transform = 'scale(1)' }}
      >
        {open ? <X size={20} color="#fff" /> : <MessageCircle size={20} color="#fff" />}
      </button>

      {/* Chat drawer */}
      {open && (
        <div style={{
          position: 'fixed', bottom: 88, right: 24, zIndex: 999,
          width: 390, height: 540,
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 16,
          display: 'flex', flexDirection: 'column',
          boxShadow: '0 20px 60px rgba(0,0,0,0.50)',
          overflow: 'hidden',
        }}>

          {/* Header */}
          <div style={{
            padding: '11px 14px', borderBottom: '1px solid var(--border)',
            background: 'var(--surface-2)',
            display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0,
          }}>
            <div style={{
              width: 30, height: 30, borderRadius: 8, flexShrink: 0,
              background: 'rgba(129,140,248,0.15)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <Sparkles size={14} color="#818cf8" />
            </div>

            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>AI Analyst</div>
              {sessions.length === 0 ? (
                <div style={{ fontSize: 10, color: 'var(--dim)' }}>No completed sessions yet</div>
              ) : (
                <select
                  value={sessionId ?? ''}
                  onChange={e => { setSessionId(e.target.value || null); setChatId(null); setMessages([]) }}
                  style={{
                    all: 'unset', fontSize: 10, color: 'var(--dim)',
                    cursor: 'pointer', maxWidth: 200,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}
                >
                  <option value="">— General —</option>
                  {sessions.map(s => (
                    <option key={s.session_id} value={s.session_id}>{s.name}</option>
                  ))}
                </select>
              )}
            </div>

            <Link href="/analyst" onClick={() => setOpen(false)} title="Open full chat" style={{ color: 'var(--dim)', display: 'flex' }}>
              <ExternalLink size={13} />
            </Link>
            <button
              onClick={() => setOpen(false)}
              style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)', display: 'flex', padding: 2 }}
            >
              <X size={14} />
            </button>
          </div>

          {/* Messages */}
          <div style={{
            flex: 1, overflowY: 'auto', padding: '14px',
            display: 'flex', flexDirection: 'column', gap: 12,
          }}>
            {messages.length === 0 && !loading && (
              <div style={{
                flex: 1, display: 'flex', flexDirection: 'column',
                alignItems: 'center', justifyContent: 'center',
                gap: 10, color: 'var(--dim)', textAlign: 'center',
                padding: '20px', opacity: 0.6,
              }}>
                <Sparkles size={28} strokeWidth={1} />
                <div style={{ fontSize: 12, lineHeight: 1.5 }}>
                  Ask about forecasts, accuracy, inventory risks, or SKU trends.
                </div>
              </div>
            )}

            {messages.map(m => {
              const isUser = m.role === 'user'
              const tag    = m.source && !isUser ? SOURCE_TAG[m.source] : null
              return (
                <div key={m.id} style={{
                  display: 'flex', gap: 8, alignItems: 'flex-start',
                  flexDirection: isUser ? 'row-reverse' : 'row',
                }}>
                  <div style={{
                    width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
                    background: isUser ? 'rgba(129,140,248,0.15)' : 'rgba(34,197,94,0.12)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}>
                    {isUser ? <User size={11} color="#818cf8" /> : <Bot size={11} color="#22c55e" />}
                  </div>

                  <div style={{
                    maxWidth: '78%', display: 'flex', flexDirection: 'column',
                    gap: 3, alignItems: isUser ? 'flex-end' : 'flex-start',
                  }}>
                    <div style={{
                      fontSize: 12, lineHeight: 1.6,
                      background: isUser ? 'rgba(99,102,241,0.12)' : 'var(--surface-2)',
                      border: `1px solid ${isUser ? 'rgba(99,102,241,0.22)' : 'var(--border)'}`,
                      borderRadius: isUser ? '12px 3px 12px 12px' : '3px 12px 12px 12px',
                      padding: '8px 12px', color: 'var(--text)',
                      whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                    }}>
                      {m.content}
                    </div>
                    {tag && (
                      <span style={{
                        fontSize: 9, fontWeight: 600, color: tag.color,
                        background: tag.color + '18', borderRadius: 4, padding: '1px 6px',
                        letterSpacing: '0.04em',
                      }}>
                        {tag.label}
                      </span>
                    )}
                  </div>
                </div>
              )
            })}

            {loading && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <div style={{
                  width: 26, height: 26, borderRadius: '50%',
                  background: 'rgba(34,197,94,0.12)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  <Bot size={11} color="#22c55e" />
                </div>
                <div style={{
                  background: 'var(--surface-2)', border: '1px solid var(--border)',
                  borderRadius: '3px 12px 12px 12px',
                  padding: '8px 12px', display: 'flex', alignItems: 'center', gap: 7,
                }}>
                  <Spinner size={10} />
                  <span style={{ fontSize: 11, color: 'var(--dim)' }}>Thinking…</span>
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input area */}
          <div style={{
            padding: '10px 12px', borderTop: '1px solid var(--border)',
            display: 'flex', gap: 8, alignItems: 'flex-end', flexShrink: 0,
          }}>
            <textarea
              ref={textRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={onKey}
              placeholder="Ask about your data… (Enter to send)"
              disabled={loading}
              rows={1}
              style={{
                flex: 1, resize: 'none', minHeight: 38, maxHeight: 110,
                background: 'var(--surface-2)', border: '1px solid var(--border)',
                borderRadius: 8, padding: '9px 11px',
                fontSize: 12, color: 'var(--text)', lineHeight: 1.4,
                outline: 'none', fontFamily: 'inherit',
              }}
            />
            <button
              onClick={send}
              disabled={!canSend}
              style={{
                all: 'unset', width: 36, height: 36, borderRadius: 8, flexShrink: 0,
                background: canSend ? '#6366f1' : 'var(--surface-2)',
                border: '1px solid var(--border)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                cursor: canSend ? 'pointer' : 'default',
                transition: 'background 0.15s',
              }}
            >
              <Send size={13} color={canSend ? '#fff' : 'var(--dim)'} />
            </button>
          </div>
        </div>
      )}
    </>
  )
}
