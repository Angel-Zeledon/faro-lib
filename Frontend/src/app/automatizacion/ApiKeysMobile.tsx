'use client'
/**
 * The API-keys tab of /automatizacion on a phone. State and API calls stay in
 * `ApiKeysTab` (page.tsx); this renders them as an app would: keys as cards,
 * minting a key in a sheet, and the one-time secret shown in that same sheet
 * with a full-width Copy — on a phone the inline row (input + scope select +
 * two buttons) was 4 controls in 330px and the secret was a 11px input you
 * could not select.
 */
import { useEffect, useRef, useState } from 'react'
import { Key, Plus, Copy, Check, AlertTriangle, X } from 'lucide-react'
import type { ApiKey, ApiKeyScope } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { BottomSheet, MobileList, MobileCard, StickyActionBar } from '@/components/mobile'
import MobileFormScope from '@/components/mobile/MobileFormScope'
import Input from '@/components/ui/Input'
import Spinner from '@/components/ui/Spinner'

const MONO = "ui-monospace, 'SF Mono', 'Cascadia Mono', Consolas, monospace"

export interface ApiKeysMobileProps {
  keys: ApiKey[]
  loading: boolean
  error: string | null
  newName: string
  setNewName: (v: string) => void
  newScope: ApiKeyScope
  setNewScope: (v: ApiKeyScope) => void
  creating: boolean
  handleCreate: () => void
  newKey: string | null
  clearNewKey: () => void
  copied: boolean
  copyKey: () => void
  revoking: string | null
  revoke: (id: string) => Promise<boolean>
}

function SecretBox({ value, copied, onCopy }: { value: string; copied: boolean; onCopy: () => void }) {
  const { t } = useLanguage()
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <code
        // Wraps instead of scrolling sideways: the whole secret is visible,
        // and a long-press selects it like any text.
        style={{
          display: 'block', padding: '12px 14px', borderRadius: 10,
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          fontFamily: MONO, fontSize: 14, lineHeight: 1.5, color: 'var(--text)',
          overflowWrap: 'anywhere', userSelect: 'all', WebkitUserSelect: 'all',
        }}
      >
        {value}
      </code>
      <button type="button" className="mobile-btn mobile-btn-primary" onClick={onCopy}>
        {copied ? <Check size={18} aria-hidden="true" /> : <Copy size={18} aria-hidden="true" />}
        {copied ? t('settings.copied') : t('settings.copy')}
      </button>
    </div>
  )
}

export default function ApiKeysMobile(p: ApiKeysMobileProps) {
  const { t } = useLanguage()
  const [sheet, setSheet] = useState<'closed' | 'create' | 'created'>('closed')
  const [detail, setDetail] = useState<ApiKey | null>(null)
  const [confirming, setConfirming] = useState(false)
  const lastDetail = useRef<ApiKey | null>(null)
  if (detail) lastDetail.current = detail
  const d = detail ?? lastDetail.current

  // A key was minted while the create sheet is up: the same sheet turns into
  // the one-time reveal, so the secret appears where the person is looking.
  useEffect(() => {
    if (p.newKey && sheet === 'create') setSheet('created')
  }, [p.newKey, sheet])

  useEffect(() => { setConfirming(false) }, [detail?.id])

  const fmt = (iso: string | null) => (iso ? iso.slice(0, 10) : t('settings.never'))

  return (
    <MobileFormScope>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div role="status" style={{
          display: 'flex', gap: 10, alignItems: 'flex-start', padding: '12px 14px', borderRadius: 12,
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          fontSize: 13, color: 'var(--text)', lineHeight: 1.5,
        }}>
          <Key size={16} style={{ flexShrink: 0, marginTop: 2, color: 'var(--accent)' }} aria-hidden="true" />
          <span>{t('settings.api_keys_shown_once')}</span>
        </div>
        <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.5, padding: '0 4px' }}>
          {t('settings.api_keys_desc')}
        </div>

        {/* The secret stays on the page after the sheet closes, until it is
            dismissed or the key is revoked — same as desktop. */}
        {p.newKey && sheet !== 'created' && (
          <div style={{
            padding: '12px 14px', borderRadius: 12,
            background: 'rgba(46,139,98,0.07)', border: '1px solid rgba(46,139,98,0.25)',
            display: 'flex', flexDirection: 'column', gap: 10,
          }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
              <div style={{ flex: 1, fontSize: 13, color: '#2F855A', fontWeight: 600, lineHeight: 1.45 }}>
                {t('settings.key_generated')}
              </div>
              <button type="button" onClick={p.clearNewKey} aria-label={t('common.close')}
                      style={{ all: 'unset', cursor: 'pointer', width: 44, height: 44, margin: '-12px -12px 0 0',
                               display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--dim)' }}>
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            <SecretBox value={p.newKey} copied={p.copied} onCopy={p.copyKey} />
          </div>
        )}

        {p.error && (
          <div role="alert" style={{ fontSize: 13, color: '#C0504D', display: 'flex', gap: 8, alignItems: 'flex-start' }}>
            <AlertTriangle size={15} style={{ flexShrink: 0, marginTop: 1 }} aria-hidden="true" />{p.error}
          </div>
        )}

        {p.loading ? (
          <div style={{ textAlign: 'center', padding: 32 }}><Spinner /></div>
        ) : p.keys.length === 0 ? (
          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10,
            padding: '32px 16px', textAlign: 'center', color: 'var(--dim)', fontSize: 14,
          }}>
            <Key size={26} style={{ opacity: 0.5 }} aria-hidden="true" />
            {t('settings.no_api_keys')}
          </div>
        ) : (
          <MobileList ariaLabel={t('settings.tab_api_keys')}>
            {p.keys.map(k => (
              <MobileCard
                key={k.id}
                title={<>{k.name}{k.last4 && <span style={{ marginLeft: 6, fontSize: 13, color: 'var(--dim)', fontFamily: MONO, fontWeight: 400 }}>…{k.last4}</span>}</>}
                subtitle={`${t('settings.col_last_used')}: ${fmt(k.last_used)}`}
                status={{
                  label: k.scope === 'write' ? t('settings.scope_write') : t('settings.scope_read'),
                  tone: k.scope === 'write' ? 'warning' : 'neutral',
                }}
                onClick={() => setDetail(k)}
              />
            ))}
          </MobileList>
        )}
      </div>

      <StickyActionBar>
        <button type="button" className="mobile-btn mobile-btn-primary" onClick={() => setSheet('create')}>
          <Plus size={18} aria-hidden="true" />{t('settings.generate_key')}
        </button>
      </StickyActionBar>

      {/* Create → reveal */}
      <BottomSheet
        open={sheet !== 'closed'}
        onClose={() => { setSheet('closed'); if (sheet === 'create') p.setNewName('') }}
        title={sheet === 'created' ? t('settings.key_generated') : t('settings.generate_key')}
        footer={sheet === 'created' ? (
          <button type="button" className="mobile-btn mobile-btn-secondary" onClick={() => setSheet('closed')}>
            {t('settings.m_key_done')}
          </button>
        ) : (
          <div style={{ display: 'flex', gap: 8, flex: 1, minWidth: 0 }}>
            <button type="button" className="mobile-btn mobile-btn-secondary"
                    onClick={() => { setSheet('closed'); p.setNewName('') }}>
              {t('common.cancel')}
            </button>
            <button type="button" className="mobile-btn mobile-btn-primary"
                    disabled={!p.newName.trim() || p.creating} onClick={p.handleCreate}>
              {p.creating ? <Spinner size={16} /> : null}{t('settings.create')}
            </button>
          </div>
        )}
      >
        <MobileFormScope>
          {sheet === 'created' && p.newKey ? (
            <SecretBox value={p.newKey} copied={p.copied} onCopy={p.copyKey} />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {p.error && (
                <div role="alert" style={{ fontSize: 13, color: '#C0504D', display: 'flex', gap: 8 }}>
                  <AlertTriangle size={15} style={{ flexShrink: 0, marginTop: 1 }} aria-hidden="true" />{p.error}
                </div>
              )}
              <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--muted)' }}>{t('settings.m_key_name_label')}</span>
                <Input
                  name="api_key_name" enterKeyHint="done" autoComplete="off"
                  placeholder={t('settings.key_name_placeholder')}
                  value={p.newName}
                  onChange={e => p.setNewName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') p.handleCreate() }}
                  style={{ width: '100%' }}
                />
              </label>
              <fieldset style={{ border: 0, margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
                <legend style={{ fontSize: 13, fontWeight: 600, color: 'var(--muted)', marginBottom: 6, padding: 0 }}>
                  {t('settings.key_role_label')}
                </legend>
                {(['read', 'write'] as const).map(s => {
                  const active = p.newScope === s
                  return (
                    <label key={s} className="tap-feedback" style={{
                      display: 'flex', alignItems: 'center', gap: 12, minHeight: 52, padding: '0 14px',
                      borderRadius: 12, cursor: 'pointer',
                      border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
                      background: active ? 'var(--accent-dim)' : 'var(--surface)',
                    }}>
                      <input type="radio" name="api_key_scope" value={s} checked={active}
                             onChange={() => p.setNewScope(s)}
                             style={{ accentColor: 'var(--accent)', width: 20, height: 20, margin: 0 }} />
                      <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>
                        {s === 'read' ? t('settings.key_role_viewer') : t('settings.key_role_analyst')}
                      </span>
                    </label>
                  )
                })}
              </fieldset>
            </div>
          )}
        </MobileFormScope>
      </BottomSheet>

      {/* One key: its facts and the revoke, asked in place. */}
      <BottomSheet
        open={!!detail}
        onClose={() => setDetail(null)}
        title={d ? `${d.name}${d.last4 ? ` …${d.last4}` : ''}` : ''}
        footer={d && (confirming ? (
          <div style={{ display: 'flex', gap: 8, flex: 1, minWidth: 0 }}>
            <button type="button" className="mobile-btn mobile-btn-secondary" onClick={() => setConfirming(false)}>
              {t('common.cancel')}
            </button>
            <button type="button" className="mobile-btn mobile-btn-danger" disabled={p.revoking === d.id}
                    onClick={async () => { if (await p.revoke(d.id)) setDetail(null) }}>
              {p.revoking === d.id ? <Spinner size={16} /> : null}{t('settings.m_revoke_yes')}
            </button>
          </div>
        ) : (
          <button type="button" className="mobile-btn mobile-btn-secondary" style={{ color: 'var(--danger)' }}
                  onClick={() => setConfirming(true)}>
            {t('settings.revoke')}
          </button>
        ))}
      >
        {d && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'auto minmax(0,1fr)', gap: '10px 16px', fontSize: 14 }}>
              <dt style={{ color: 'var(--dim)' }}>{t('settings.col_scope')}</dt>
              <dd style={{ margin: 0, textAlign: 'right', color: d.scope === 'write' ? 'var(--warning)' : 'var(--text)' }}>
                {d.scope === 'write' ? t('settings.scope_write') : t('settings.scope_read')}
              </dd>
              <dt style={{ color: 'var(--dim)' }}>{t('settings.col_created')}</dt>
              <dd style={{ margin: 0, textAlign: 'right', color: 'var(--text)' }}>{fmt(d.created_at)}</dd>
              <dt style={{ color: 'var(--dim)' }}>{t('settings.col_last_used')}</dt>
              <dd style={{ margin: 0, textAlign: 'right', color: 'var(--text)' }}>{fmt(d.last_used)}</dd>
            </dl>
            {confirming && (
              <div role="alert" style={{
                display: 'flex', gap: 8, padding: '10px 12px', borderRadius: 10, fontSize: 13, lineHeight: 1.5,
                background: 'rgba(192,80,77,0.08)', border: '1px solid rgba(192,80,77,0.25)', color: 'var(--text)',
              }}>
                <AlertTriangle size={15} style={{ flexShrink: 0, marginTop: 2, color: '#C0504D' }} aria-hidden="true" />
                {t('settings.revoke_confirm')}
              </div>
            )}
          </div>
        )}
      </BottomSheet>
    </MobileFormScope>
  )
}
