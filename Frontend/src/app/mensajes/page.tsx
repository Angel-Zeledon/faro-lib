'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import { MessageSquare, Send, ArrowLeft, Search } from 'lucide-react'
import { getUser } from '@/lib/auth'
import {
  getDmContacts, getDmConversations, getDmThread, sendDm, markDmRead,
} from '@/lib/api'
import type { DmContact, DmConversation, DirectMessage } from '@/lib/types'
import Card from '@/components/ui/Card'
import Spinner from '@/components/ui/Spinner'
import Input from '@/components/ui/Input'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'

const CONVERSATIONS_POLL_MS = 15000
const THREAD_POLL_MS = 5000

function displayName(c: { full_name: string | null; email: string }) {
  return c.full_name || c.email.split('@')[0]
}

function initial(c: { full_name: string | null; email: string }) {
  return displayName(c).charAt(0).toUpperCase()
}

function timeLabel(iso: string, lang: string) {
  const d = new Date(iso)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) return d.toLocaleTimeString(lang === 'es' ? 'es' : 'en-US', { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString(lang === 'es' ? 'es' : 'en-US', { day: 'numeric', month: 'short' })
}

export default function MessagesPage() {
  const { t, lang } = useLanguage()
  const me = getUser()
  const narrow = useIsNarrow()

  const [conversations, setConversations] = useState<DmConversation[] | null>(null)
  const [contacts,      setContacts]      = useState<DmContact[]>([])
  const [activeId,      setActiveId]      = useState<string | null>(null)
  const [activeName,    setActiveName]    = useState('')
  const [messages,      setMessages]      = useState<DirectMessage[]>([])
  const [threadLoading, setThreadLoading] = useState(false)
  const [draft,         setDraft]         = useState('')
  const [sending,       setSending]       = useState(false)
  // A search box that is always on screen, rather than a panel that expands.
  // The old "+" toggle inserted a 220px list ABOVE the conversations, so the
  // moment you opened it every conversation slid five rows down — and a click
  // aimed at a name landed on whichever row had moved into that spot. The
  // search field never moves, so nothing moves under the cursor.
  const [search, setSearch] = useState('')

  const scrollRef  = useRef<HTMLDivElement>(null)
  const activeRef  = useRef<string | null>(null)
  activeRef.current = activeId

  const loadConversations = useCallback(async () => {
    try { setConversations(await getDmConversations()) } catch { /* toast via interceptor */ }
  }, [])

  useEffect(() => {
    loadConversations()
    getDmContacts().then(setContacts).catch(() => {})
    const id = setInterval(loadConversations, CONVERSATIONS_POLL_MS)
    return () => clearInterval(id)
  }, [loadConversations])

  const loadThread = useCallback(async (userId: string, showSpinner: boolean) => {
    if (showSpinner) setThreadLoading(true)
    try {
      const data = await getDmThread(userId)
      // The user may have switched threads while this request was in flight.
      if (activeRef.current !== userId) return
      setActiveName(displayName(data.counterpart))
      setMessages(prev => {
        const changed = prev.length !== data.messages.length
          || prev[prev.length - 1]?.id !== data.messages[data.messages.length - 1]?.id
        return changed ? data.messages : prev
      })
      const hasUnreadIncoming = data.messages.some(m => m.sender_id === userId && !m.read_at)
      if (hasUnreadIncoming) {
        await markDmRead(userId)
        loadConversations()
      }
    } catch { /* toast via interceptor */ } finally {
      if (showSpinner) setThreadLoading(false)
    }
  }, [loadConversations])

  useEffect(() => {
    if (!activeId) return
    loadThread(activeId, true)
    const id = setInterval(() => loadThread(activeId, false), THREAD_POLL_MS)
    return () => clearInterval(id)
  }, [activeId, loadThread])

  // Pin the view to the newest message whenever the thread grows.
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, activeId])

  function openThread(userId: string, name: string) {
    setMessages([])
    setActiveName(name)
    setActiveId(userId)
    setSearch('')
  }

  // Everyone this person can write to, whether or not they have talked before.
  // A colleague you have never messaged is exactly who you need the search for,
  // so the two lists are merged rather than kept in separate places: matches
  // you already have a thread with come first, everybody else after.
  const q = search.trim().toLowerCase()
  const matches = (c: { full_name: string | null; email: string }) =>
    !q || displayName(c).toLowerCase().includes(q) || c.email.toLowerCase().includes(q)

  const shownConversations = (conversations ?? []).filter(matches)
  const talkedTo = new Set((conversations ?? []).map(c => c.counterpart_id))
  const newContacts = q
    ? contacts.filter(c => !talkedTo.has(c.id) && matches(c))
    : []

  async function handleSend() {
    const text = draft.trim()
    if (!text || !activeId || sending) return
    setSending(true)
    try {
      const sent = await sendDm(activeId, text)
      setDraft('')
      setMessages(prev => [...prev, sent])
      loadConversations()
    } catch { /* toast via interceptor */ } finally {
      setSending(false)
    }
  }

  const showList   = !narrow || activeId === null
  const showThread = !narrow || activeId !== null

  return (
    <div style={{ padding: narrow ? 12 : 24, height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Card padding={0} style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>

        {/* ── Conversation list ── */}
        {showList && (
          <div style={{
            width: narrow ? '100%' : 280, minWidth: narrow ? undefined : 280,
            borderRight: narrow ? 'none' : '1px solid var(--border)',
            display: 'flex', flexDirection: 'column', minHeight: 0,
          }}>
            <div style={{ padding: '14px 16px 12px', borderBottom: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8, marginBottom: 10 }}>
                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>
                  {t('messages.page_title')}
                </span>
                <span style={{ fontSize: 11, color: 'var(--dim)' }}>
                  {t('messages.people_count', { n: contacts.length })}
                </span>
              </div>
              <div style={{ position: 'relative' }}>
                <Search
                  size={13}
                  style={{
                    position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)',
                    color: 'var(--dim)', pointerEvents: 'none',
                  }}
                />
                <Input
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  placeholder={t('messages.search_people')}
                  aria-label={t('messages.search_people')}
                  style={{ paddingLeft: 28, fontSize: 12.5 }}
                />
              </div>
            </div>

            <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
              {conversations === null && (
                <div style={{ display: 'flex', justifyContent: 'center', padding: 24 }}><Spinner size={16} /></div>
              )}
              {conversations !== null && conversations.length === 0 && !q && (
                <div style={{ padding: '28px 20px', textAlign: 'center' }}>
                  <MessageSquare size={22} color="var(--dim)" style={{ marginBottom: 8 }} />
                  <div style={{ fontSize: 12.5, color: 'var(--muted)', fontWeight: 500 }}>
                    {t('messages.no_conversations')}
                  </div>
                  <div style={{ fontSize: 11.5, color: 'var(--dim)', marginTop: 4 }}>
                    {t('messages.start_hint')}
                  </div>
                </div>
              )}
              {q && shownConversations.length === 0 && newContacts.length === 0 && (
                <div style={{ padding: '24px 20px', textAlign: 'center', fontSize: 12, color: 'var(--dim)' }}>
                  {t('messages.no_search_results', { q: search.trim() })}
                </div>
              )}
              {shownConversations.map(c => {
                const active = c.counterpart_id === activeId
                return (
                  <button
                    key={c.counterpart_id}
                    onClick={() => openThread(c.counterpart_id, displayName(c))}
                    style={{
                      all: 'unset', boxSizing: 'border-box', cursor: 'pointer', width: '100%',
                      display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px',
                      background: active ? 'var(--accent-dim)' : 'transparent',
                    }}
                  >
                    <div style={{
                      width: 30, height: 30, borderRadius: '50%', flexShrink: 0,
                      background: 'var(--accent-dim)', color: 'var(--accent)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 13, fontWeight: 700,
                    }}>
                      {initial(c)}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
                        <span style={{
                          fontSize: 12.5, fontWeight: c.unread_count ? 700 : 500,
                          color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>
                          {displayName(c)}
                        </span>
                        <span style={{ fontSize: 10.5, color: 'var(--dim)', flexShrink: 0 }}>
                          {timeLabel(c.last_at, lang)}
                        </span>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{
                          fontSize: 11.5, color: c.unread_count ? 'var(--text)' : 'var(--dim)',
                          fontWeight: c.unread_count ? 600 : 400, flex: 1, minWidth: 0,
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>
                          {c.last_is_mine ? `${t('messages.you')}: ` : ''}{c.last_body}
                        </span>
                        {c.unread_count > 0 && (
                          <span style={{
                            minWidth: 17, height: 17, borderRadius: 9, padding: '0 5px', flexShrink: 0,
                            background: 'var(--accent)', color: '#fff',
                            fontSize: 10, fontWeight: 700,
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                          }}>
                            {c.unread_count}
                          </span>
                        )}
                      </div>
                    </div>
                  </button>
                )
              })}

              {/* Colleagues with no conversation yet. Only while searching:
                  the point of the search is to reach someone you have not
                  written to before, and listing the whole company by default
                  would bury the threads you actually use. */}
              {newContacts.length > 0 && (
                <>
                  <div style={{
                    padding: '12px 16px 6px', fontSize: 10.5, fontWeight: 700,
                    letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--dim)',
                  }}>
                    {t('messages.start_new_with')}
                  </div>
                  {newContacts.map(c => (
                    <button
                      key={c.id}
                      onClick={() => openThread(c.id, displayName(c))}
                      className="nav-item-idle"
                      style={{
                        all: 'unset', boxSizing: 'border-box', cursor: 'pointer', width: '100%',
                        display: 'flex', alignItems: 'center', gap: 10, padding: '9px 16px',
                      }}
                    >
                      <div style={{
                        width: 30, height: 30, borderRadius: '50%', flexShrink: 0,
                        background: 'var(--surface-3)', color: 'var(--muted)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 13, fontWeight: 700,
                      }}>
                        {initial(c)}
                      </div>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {displayName(c)}
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {c.email}
                        </div>
                      </div>
                    </button>
                  ))}
                </>
              )}
            </div>
          </div>
        )}

        {/* ── Thread ── */}
        {showThread && (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }}>
            {activeId === null ? (
              <div style={{
                flex: 1, display: 'flex', flexDirection: 'column',
                alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--dim)',
              }}>
                <MessageSquare size={28} />
                <div style={{ fontSize: 13 }}>{t('messages.pick_conversation')}</div>
              </div>
            ) : (
              <>
                <div style={{
                  padding: '12px 16px', borderBottom: '1px solid var(--border)',
                  display: 'flex', alignItems: 'center', gap: 10,
                }}>
                  {narrow && (
                    <button
                      onClick={() => setActiveId(null)}
                      aria-label={t('go_back')}
                      style={{
                        all: 'unset', cursor: 'pointer', width: 32, height: 32, borderRadius: 7,
                        display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)',
                      }}
                    >
                      <ArrowLeft size={16} />
                    </button>
                  )}
                  <div style={{
                    width: 28, height: 28, borderRadius: '50%',
                    background: 'var(--accent-dim)', color: 'var(--accent)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 12, fontWeight: 700,
                  }}>
                    {activeName.charAt(0).toUpperCase()}
                  </div>
                  <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{activeName}</span>
                </div>

                <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto', padding: 16, minHeight: 0 }}>
                  {threadLoading && (
                    <div style={{ display: 'flex', justifyContent: 'center', padding: 24 }}><Spinner size={16} /></div>
                  )}
                  {!threadLoading && messages.length === 0 && (
                    <div style={{ textAlign: 'center', padding: 24, fontSize: 12, color: 'var(--dim)' }}>
                      {t('messages.thread_empty')}
                    </div>
                  )}
                  {messages.map(m => {
                    const mine = m.sender_id === me?.id
                    return (
                      <div key={m.id} style={{ display: 'flex', justifyContent: mine ? 'flex-end' : 'flex-start', marginBottom: 8 }}>
                        <div style={{
                          maxWidth: '72%', padding: '8px 12px',
                          borderRadius: mine ? '12px 12px 3px 12px' : '12px 12px 12px 3px',
                          background: mine ? 'var(--accent)' : 'var(--surface-2)',
                          color: mine ? '#fff' : 'var(--text)',
                          border: mine ? 'none' : '1px solid var(--border)',
                        }}>
                          <div style={{ fontSize: 13, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.body}</div>
                          <div style={{
                            fontSize: 10, marginTop: 3, textAlign: 'right',
                            color: mine ? 'rgba(255,255,255,0.75)' : 'var(--dim)',
                          }}>
                            {timeLabel(m.created_at, lang)}
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>

                <div style={{
                  padding: 12, borderTop: '1px solid var(--border)',
                  display: 'flex', gap: 8, alignItems: 'center',
                }}>
                  <Input
                    name="dm_body"
                    value={draft}
                    onChange={e => setDraft(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() } }}
                    placeholder={t('messages.placeholder')}
                    maxLength={4000}
                    style={{ flex: 1 }}
                  />
                  <button
                    onClick={handleSend}
                    disabled={sending || !draft.trim()}
                    aria-label={t('messages.send')}
                    style={{
                      all: 'unset', boxSizing: 'border-box',
                      cursor: sending || !draft.trim() ? 'default' : 'pointer',
                      width: 38, height: 38, borderRadius: 9, flexShrink: 0,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      background: 'var(--accent)', color: '#fff',
                      opacity: sending || !draft.trim() ? 0.5 : 1, transition: 'opacity 0.15s',
                    }}
                  >
                    {sending ? <Spinner size={14} /> : <Send size={15} />}
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </Card>
    </div>
  )
}
