'use client'
/**
 * The saved messages of /asistente: the answer text, the question that produced
 * it, when it was written and which conversation it came from, with "open in
 * chat" and "remove". One list for the desktop panel and the phone tab.
 */
import { useState } from 'react'
import { MessageSquare, Star, Trash2 } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import type { FavoriteMessage } from '@/lib/types'
import { messageToPlainText } from '@/lib/chatText'
import { Markdown } from '@/components/ui/Markdown'
import Spinner from '@/components/ui/Spinner'
import { CopyButton } from './MessageActions'
import { clampStyle } from './parts'

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString([], {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

// Long answers start collapsed: a favorites list is for scanning.
const COLLAPSE_AT = 420

function FavoriteCard({ item, large, onOpen, onRemove }: {
  item: FavoriteMessage
  large: boolean
  onOpen: () => void
  onRemove: () => void
}) {
  const { t } = useLanguage()
  const [open, setOpen] = useState(false)
  const long = item.content.length > COLLAPSE_AT
  const btn: React.CSSProperties = {
    all: 'unset', boxSizing: 'border-box', cursor: 'pointer', display: 'inline-flex', alignItems: 'center',
    gap: 6, minHeight: large ? 40 : 28, padding: large ? '0 12px' : '0 8px', borderRadius: large ? 10 : 6,
    fontSize: large ? 14 : 12, fontWeight: 600, color: 'var(--accent)',
  }
  return (
    <li
      data-testid="favorite-item"
      style={{
        background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: large ? 14 : 10,
        padding: large ? '14px 14px 8px' : '14px 16px 8px', minWidth: 0,
        listStyle: 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, marginBottom: 8 }}>
        <Star size={13} fill="#B7791F" color="#B7791F" aria-hidden="true" style={{ flexShrink: 0 }} />
        <span className="msg-meta" style={{ flex: 1, minWidth: 0, ...clampStyle(1) }} title={item.chat_title}>
          {t('analyst.favorite_from', { chat: item.chat_title })}
        </span>
        <time className="msg-meta" dateTime={item.created_at} style={{ flexShrink: 0 }}>
          {fmtDate(item.created_at)}
        </time>
      </div>

      {item.question && (
        <div style={{
          borderLeft: '2px solid var(--border)', paddingLeft: 10, marginBottom: 10,
          fontSize: large ? 14 : 12.5, color: 'var(--muted)', lineHeight: 1.5,
        }}>
          <span style={{ fontWeight: 600, color: 'var(--dim)' }}>{t('analyst.favorite_question')}: </span>
          <span style={{ overflowWrap: 'anywhere' }}>{item.question}</span>
        </div>
      )}

      <div style={{
        position: 'relative',
        ...(long && !open ? { maxHeight: 170, overflow: 'hidden' } : {}),
      }}>
        {item.role === 'assistant'
          ? <Markdown text={item.content} large={large} small={!large} />
          : <div className={`msg-prose msg-plain${large ? ' msg-prose-lg' : ' msg-prose-sm'}`}>{item.content}</div>}
        {long && !open && (
          <div aria-hidden="true" style={{
            position: 'absolute', left: 0, right: 0, bottom: 0, height: 44,
            background: 'linear-gradient(to bottom, transparent, var(--surface))',
          }} />
        )}
      </div>
      {long && (
        <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
          style={{ ...btn, color: 'var(--muted)', marginTop: 2 }}>
          {open ? t('analyst.favorite_show_less') : t('analyst.favorite_show_more')}
        </button>
      )}

      <div style={{
        display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 2,
        marginTop: 8, paddingTop: 6, borderTop: '1px solid var(--border)',
      }}>
        <button type="button" data-testid="favorite-open" onClick={onOpen} style={btn}>
          <MessageSquare size={large ? 16 : 13} aria-hidden="true" /> {t('analyst.favorite_open')}
        </button>
        <span className={large ? 'msg-row-large' : undefined} style={{ display: 'inline-flex', alignItems: 'center' }}>
          {/* Always visible here: this list is not a message row. */}
          <span className="msg-actions">
            <CopyButton getText={() => messageToPlainText(item.role, item.content)} />
          </span>
        </span>
        <span style={{ flex: 1 }} />
        <button type="button" data-testid="favorite-remove" onClick={onRemove}
          style={{ ...btn, color: 'var(--muted)' }}>
          <Trash2 size={large ? 16 : 13} aria-hidden="true" /> {t('analyst.favorite_remove')}
        </button>
      </div>
    </li>
  )
}

export default function FavoritesList({ items, error, large = false, onOpen, onRemove }: {
  /** null while loading. */
  items: FavoriteMessage[] | null
  error: string | null
  large?: boolean
  onOpen: (item: FavoriteMessage) => void
  onRemove: (item: FavoriteMessage) => void
}) {
  const { t } = useLanguage()
  if (error) {
    return <div role="alert" style={{ padding: 16, textAlign: 'center', fontSize: large ? 14 : 12.5, color: '#C0504D' }}>{error}</div>
  }
  if (items === null) {
    return <div style={{ display: 'flex', justifyContent: 'center', padding: 32 }}><Spinner size={18} /></div>
  }
  if (items.length === 0) {
    return (
      <div data-testid="favorites-empty" style={{
        padding: '28px 20px', textAlign: 'center', color: 'var(--dim)', lineHeight: 1.55,
        fontSize: large ? 14 : 13, border: '1px dashed var(--border)', borderRadius: 14,
      }}>
        <Star size={22} aria-hidden="true" style={{ display: 'block', margin: '0 auto 8px', opacity: 0.6 }} />
        {t('analyst.favorites_empty')}
      </div>
    )
  }
  return (
    <ul data-testid="favorites-list" style={{ margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: large ? 12 : 10 }}>
      {items.map(it => (
        <FavoriteCard key={it.id} item={it} large={large} onOpen={() => onOpen(it)} onRemove={() => onRemove(it)} />
      ))}
    </ul>
  )
}
