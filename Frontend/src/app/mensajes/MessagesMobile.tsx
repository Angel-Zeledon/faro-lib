'use client'
/**
 * `/mensajes` on a phone — a messaging app, not a two-pane table.
 *
 * Two screens, like every chat app: the conversation list (search always on
 * top, one 64px row per person, unread badges), and the thread, which takes the
 * whole screen with the person's name and a back arrow in the app header and
 * the composer pinned above the tab bar — or above the keyboard while typing.
 *
 * All state and every request live in page.tsx; this file only lays them out,
 * so the phone and the desktop cannot disagree about who wrote what.
 */
import { useEffect, useLayoutEffect, useRef } from 'react'
import { MessageSquare, Send, Search, X } from 'lucide-react'
import type { DmContact, DmConversation, DirectMessage } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import ComposerDock, { scrollPageToBottom } from '@/components/mobile/ComposerDock'
import { useMobileHeader } from '@/components/mobile/MobileHeaderContext'
import { useLanguage } from '@/contexts/LanguageContext'

type Person = { full_name: string | null; email: string }

export interface MessagesMobileProps {
  meId: string | null
  conversations: DmConversation[] | null
  contactsCount: number
  shownConversations: DmConversation[]
  newContacts: DmContact[]
  search: string
  onSearch: (q: string) => void
  activeId: string | null
  activeName: string
  onOpen: (userId: string, name: string) => void
  onBack: () => void
  messages: DirectMessage[]
  threadLoading: boolean
  draft: string
  onDraft: (s: string) => void
  sending: boolean
  onSend: () => void
  displayName: (p: Person) => string
  timeLabel: (iso: string) => string
}

export default function MessagesMobile(p: MessagesMobileProps) {
  const { t } = useLanguage()
  // The thread owns the app header: the person's name and a back arrow.
  useMobileHeader(p.activeId ? { title: p.activeName || t('messages.page_title'), onBack: p.onBack } : null)
  return p.activeId ? <Thread {...p} /> : <ConversationList {...p} />
}

// ── The list ─────────────────────────────────────────────────────────────────
function ConversationList(p: MessagesMobileProps) {
  const { t } = useLanguage()
  const q = p.search.trim()
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
      <div style={{ position: 'relative' }}>
        <Search size={17} aria-hidden="true" style={{
          position: 'absolute', left: 14, top: '50%', transform: 'translateY(-50%)',
          color: 'var(--dim)', pointerEvents: 'none',
        }} />
        <input
          type="text" inputMode="search" autoComplete="off"
          value={p.search}
          onChange={e => p.onSearch(e.target.value)}
          placeholder={t('messages.search_people')}
          aria-label={t('messages.search_people')}
          enterKeyHint="search"
          style={{
            width: '100%', boxSizing: 'border-box', minHeight: 48, borderRadius: 12,
            padding: '0 44px 0 42px', fontSize: 16, color: 'var(--text)',
            background: 'var(--surface)', border: '1px solid var(--border)', outline: 'none',
          }}
        />
        {p.search && (
          <button
            onClick={() => p.onSearch('')}
            aria-label={t('messages.clear_search')}
            style={{
              all: 'unset', position: 'absolute', right: 2, top: 2, width: 44, height: 44,
              display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: 'var(--dim)',
            }}
          >
            <X size={17} aria-hidden="true" />
          </button>
        )}
      </div>
      <p style={{ margin: '-4px 4px 0', fontSize: 12, color: 'var(--dim)' }}>
        {t('messages.people_count', { n: p.contactsCount })}
      </p>

      {p.conversations === null && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 32 }}><Spinner size={20} /></div>
      )}

      {p.conversations !== null && p.conversations.length === 0 && !q && (
        <div style={{ padding: '40px 20px', textAlign: 'center' }}>
          <div style={{
            width: 56, height: 56, borderRadius: '50%', margin: '0 auto 12px',
            background: 'var(--accent-dim)', display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <MessageSquare size={24} color="var(--accent)" aria-hidden="true" />
          </div>
          <div style={{ fontSize: 15, color: 'var(--text)', fontWeight: 600 }}>{t('messages.no_conversations')}</div>
          <div style={{ fontSize: 13, color: 'var(--dim)', marginTop: 6, lineHeight: 1.5 }}>{t('messages.start_hint')}</div>
        </div>
      )}

      {q && p.shownConversations.length === 0 && p.newContacts.length === 0 && (
        <div style={{ padding: '28px 16px', textAlign: 'center', fontSize: 14, color: 'var(--dim)' }}>
          {t('messages.no_search_results', { q })}
        </div>
      )}

      {p.shownConversations.length > 0 && (
        <ul className="page-enter" style={groupStyle}>
          {p.shownConversations.map(c => (
            <PersonRow
              key={c.counterpart_id}
              name={p.displayName(c)}
              time={p.timeLabel(c.last_at)}
              preview={`${c.last_is_mine ? `${t('messages.you')}: ` : ''}${c.last_body}`}
              unread={c.unread_count}
              onClick={() => p.onOpen(c.counterpart_id, p.displayName(c))}
            />
          ))}
        </ul>
      )}

      {p.newContacts.length > 0 && (
        <>
          <h2 style={{
            margin: '6px 4px 0', fontSize: 12, fontWeight: 700, letterSpacing: '0.06em',
            textTransform: 'uppercase', color: 'var(--dim)',
          }}>
            {t('messages.start_new_with')}
          </h2>
          <ul style={groupStyle}>
            {p.newContacts.map(c => (
              <PersonRow
                key={c.id}
                name={p.displayName(c)}
                preview={c.email}
                muted
                onClick={() => p.onOpen(c.id, p.displayName(c))}
              />
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

const groupStyle: React.CSSProperties = {
  listStyle: 'none', margin: 0, padding: 0, background: 'var(--surface)',
  border: '1px solid var(--border)', borderRadius: 14, overflow: 'hidden',
}

function PersonRow({ name, time, preview, unread = 0, muted, onClick }: {
  name: string
  time?: string
  preview: string
  unread?: number
  muted?: boolean
  onClick: () => void
}) {
  const { t } = useLanguage()
  return (
    <li className="mobile-list-item">
      <button
        type="button"
        onClick={onClick}
        className="tap-feedback"
        aria-label={unread > 0 ? `${name} — ${t('mobile.unread_count', { n: unread })}` : name}
        style={{
          all: 'unset', boxSizing: 'border-box', width: '100%', cursor: 'pointer',
          display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', minHeight: 64,
        }}
      >
        <span aria-hidden="true" style={{
          width: 44, height: 44, borderRadius: '50%', flexShrink: 0,
          background: muted ? 'var(--surface-3)' : 'var(--accent-dim)',
          color: muted ? 'var(--muted)' : 'var(--accent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 17, fontWeight: 700,
        }}>
          {name.charAt(0).toUpperCase()}
        </span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{
              flex: 1, minWidth: 0, fontSize: 15.5, fontWeight: unread ? 700 : 600, color: 'var(--text)',
              overflow: 'hidden', overflowWrap: 'anywhere',
            }}>{name}</span>
            {time && <span style={{ fontSize: 12, color: unread ? 'var(--accent)' : 'var(--dim)', flexShrink: 0 }}>{time}</span>}
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 }}>
            <span style={{
              flex: 1, minWidth: 0, fontSize: 13.5,
              color: unread ? 'var(--text)' : 'var(--dim)', fontWeight: unread ? 600 : 400,
              overflow: 'hidden', overflowWrap: 'anywhere',
            }}>{preview}</span>
            {unread > 0 && (
              <span aria-hidden="true" style={{
                minWidth: 22, height: 22, borderRadius: 11, padding: '0 6px', flexShrink: 0, boxSizing: 'border-box',
                background: 'var(--accent)', color: '#fff', fontSize: 12, fontWeight: 700,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>{unread}</span>
            )}
          </span>
        </span>
      </button>
    </li>
  )
}

// ── One conversation, full screen ────────────────────────────────────────────
function Thread(p: MessagesMobileProps) {
  const { t } = useLanguage()
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const lastId = p.messages[p.messages.length - 1]?.id

  // Pinned to the newest message whenever the thread grows (and on open).
  useLayoutEffect(() => { scrollPageToBottom() }, [lastId, p.activeId, p.threadLoading])

  // The draft box grows with what is typed, up to five lines.
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 132)}px`
  }, [p.draft])

  const canSend = !p.sending && !!p.draft.trim()

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: '100%' }}>
      <div role="log" aria-live="polite" aria-label={p.activeName} style={{ flex: 1, padding: '4px 0 8px' }}>
        {p.threadLoading && (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 32 }}><Spinner size={20} /></div>
        )}
        {!p.threadLoading && p.messages.length === 0 && (
          <div style={{ textAlign: 'center', padding: '48px 16px', fontSize: 14, color: 'var(--dim)', lineHeight: 1.5 }}>
            <MessageSquare size={26} aria-hidden="true" style={{ display: 'block', margin: '0 auto 10px', opacity: 0.6 }} />
            {t('messages.thread_empty')}
          </div>
        )}
        {p.messages.map((m, i) => {
          const mine = m.sender_id === p.meId
          const prev = p.messages[i - 1]
          const grouped = prev && prev.sender_id === m.sender_id
          return (
            <div key={m.id} className="page-enter" style={{
              display: 'flex', justifyContent: mine ? 'flex-end' : 'flex-start', marginTop: grouped ? 3 : 10,
            }}>
              <div style={{
                maxWidth: '82%', padding: '8px 12px', boxSizing: 'border-box',
                borderRadius: mine ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
                background: mine ? 'var(--accent)' : 'var(--surface)',
                color: mine ? '#fff' : 'var(--text)',
                border: mine ? 'none' : '1px solid var(--border)',
              }}>
                <div style={{ fontSize: 15, lineHeight: 1.45, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.body}</div>
                <div style={{
                  fontSize: 11, marginTop: 2, textAlign: 'right',
                  color: mine ? 'rgba(255,255,255,0.78)' : 'var(--dim)',
                }}>
                  {p.timeLabel(m.created_at)}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      <ComposerDock ariaLabel={t('messages.placeholder')} onHeightChange={() => scrollPageToBottom()}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <textarea
            ref={inputRef}
            name="dm_body"
            rows={1}
            value={p.draft}
            onChange={e => p.onDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (canSend) p.onSend() } }}
            onFocus={() => setTimeout(() => scrollPageToBottom(true), 250)}
            placeholder={t('messages.placeholder')}
            aria-label={t('messages.placeholder')}
            maxLength={4000}
            enterKeyHint="send"
            style={{
              flex: 1, minWidth: 0, boxSizing: 'border-box', resize: 'none',
              minHeight: 44, maxHeight: 132, padding: '11px 14px', borderRadius: 22,
              border: '1px solid var(--border)', background: 'var(--surface-2)',
              color: 'var(--text)', fontSize: 16, lineHeight: 1.35, fontFamily: 'inherit', outline: 'none',
            }}
          />
          <button
            onClick={() => { p.onSend(); inputRef.current?.focus() }}
            disabled={!canSend}
            aria-label={t('messages.send')}
            style={{
              all: 'unset', boxSizing: 'border-box', flexShrink: 0, width: 44, height: 44, borderRadius: 22,
              cursor: canSend ? 'pointer' : 'default',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: 'var(--accent)', color: '#fff', opacity: canSend ? 1 : 0.45,
              transition: 'opacity var(--dur-2) var(--ease-out), transform var(--dur-1) var(--ease-out)',
            }}
          >
            {p.sending ? <Spinner size={16} /> : <Send size={18} aria-hidden="true" />}
          </button>
        </div>
      </ComposerDock>
    </div>
  )
}
