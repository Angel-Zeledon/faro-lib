'use client'
import { useState } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { Download, LifeBuoy, LogOut, Share, User } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useTheme } from '@/contexts/ThemeContext'
import { getUser, clearAuth } from '@/lib/auth'
import { authLogout } from '@/lib/api'
import { roleLabel } from '@/lib/enumLabels'
import { useInstall } from '@/lib/pwa'
import { SCREENS, ANALYSIS_TABS, SETTINGS_HREF, navItemMatches, type Screen } from '@/components/layout/navItems'
import BottomSheet from './BottomSheet'
import LegalLinks from '@/components/legal/LegalLinks'
import { siteHref } from '@/lib/siteUrls'

/**
 * "Más": the desktop sidebar entries that are not bottom tabs — Proveedores,
 * Pronósticos (with its three analysis screens one tap away), Mensajes and
 * Configuración, which holds everything else exactly as on desktop — plus
 * what the sidebar footer held: language, theme, install the app, the
 * signed-in user and log out.
 */
const MORE_ROUTES = ['/proveedores', '/pronosticos', '/mensajes', SETTINGS_HREF]
const MORE: Screen[] = MORE_ROUTES.map(href => SCREENS.find(s => s.href === href)!)

export default function MoreSheet({ open, onClose, unread }: {
  open: boolean
  onClose: () => void
  unread: number
}) {
  const path = usePathname()
  const router = useRouter()
  const { t, lang, setLang } = useLanguage()
  const { theme, setTheme } = useTheme()
  const { mode: installMode, install } = useInstall()
  const [iosHelp, setIosHelp] = useState(false)
  const user = getUser()

  function logout() {
    authLogout().catch(() => {})
    clearAuth()
    onClose()
    router.replace('/login')
  }

  const row: React.CSSProperties = {
    all: 'unset', boxSizing: 'border-box', cursor: 'pointer', width: '100%',
    display: 'flex', alignItems: 'center', gap: 14, minHeight: 48, padding: '0 12px',
    borderRadius: 10, fontSize: 15, color: 'var(--text)', textDecoration: 'none',
  }
  const groupTitle: React.CSSProperties = {
    margin: '14px 12px 4px', fontSize: 11, fontWeight: 700, color: 'var(--dim)',
    textTransform: 'uppercase', letterSpacing: '0.08em',
  }

  return (
    <BottomSheet open={open} onClose={onClose} title={t('mobile.tab_more')}>
      <nav aria-label={t('mobile.tab_more')}>
        {MORE.map(item => {
          const active = navItemMatches(item, path)
          const Icon = item.Icon
          return (
            <div key={item.href}>
              <Link
                href={item.href}
                onClick={onClose}
                aria-current={active && path === item.href ? 'page' : undefined}
                className="tap-feedback"
                style={{
                  ...row,
                  background: active ? 'var(--accent-dim)' : 'transparent',
                  color: active ? 'var(--accent)' : 'var(--text)',
                  fontWeight: active ? 600 : 400,
                }}
              >
                <Icon size={20} aria-hidden="true" color={active ? 'var(--accent)' : 'var(--muted)'} />
                <span style={{ flex: 1 }}>{t(item.labelKey)}</span>
                {item.href === '/mensajes' && unread > 0 && (
                  <span aria-label={t('mobile.unread_count', { n: unread })} style={{
                    minWidth: 22, height: 22, borderRadius: 11, padding: '0 7px', boxSizing: 'border-box',
                    background: 'var(--accent)', color: '#fff', fontSize: 11.5, fontWeight: 700,
                    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  }}>{unread > 99 ? '99+' : unread}</span>
                )}
              </Link>
              {/* The analysis screens straight from the sheet, so none of them
                  is three taps away on a phone. Same set as the tab strip at
                  the top of Pronósticos. */}
              {item.href === '/pronosticos' && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, padding: '2px 12px 8px 46px' }}>
                  {ANALYSIS_TABS.slice(1).map(sub => {
                    const on = path === sub.href || path.startsWith(`${sub.href}/`)
                    return (
                      <Link
                        key={sub.href}
                        href={sub.href}
                        onClick={onClose}
                        aria-current={on ? 'page' : undefined}
                        className="tap-feedback"
                        style={{
                          display: 'inline-flex', alignItems: 'center', minHeight: 40, padding: '0 14px',
                          borderRadius: 20, fontSize: 13, textDecoration: 'none',
                          border: `1px solid ${on ? 'var(--accent)' : 'var(--border)'}`,
                          color: on ? 'var(--accent)' : 'var(--muted)', fontWeight: on ? 600 : 500,
                        }}
                      >
                        {t(sub.labelKey)}
                      </Link>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </nav>

      <h3 style={groupTitle}>{t('mobile.more_preferences')}</h3>
      <Segmented
        label={t('mobile.language')}
        value={lang}
        options={[{ id: 'es', label: 'Español' }, { id: 'en', label: 'English' }]}
        onChange={v => setLang(v as 'es' | 'en')}
      />
      <Segmented
        label={t('mobile.theme')}
        value={theme}
        options={[{ id: 'light', label: t('mobile.theme_light') }, { id: 'dark', label: t('mobile.theme_dark') }]}
        onChange={v => setTheme(v as 'light' | 'dark')}
      />

      <div style={{ height: 1, background: 'var(--border)', margin: '12px 0 6px' }} />

      {/* On the landing's origin, in its own tab (see the sidebar). */}
      <a href={siteHref('/docs')} target="_blank" rel="noopener" className="tap-feedback" style={row} onClick={onClose}>
        <LifeBuoy size={20} color="var(--muted)" aria-hidden="true" />
        <span style={{ flex: 1 }}>{t('help.center')}</span>
      </a>

      {installMode && (
        <>
          <button
            type="button"
            className="tap-feedback"
            onClick={() => (installMode === 'prompt' ? void install() : setIosHelp(v => !v))}
            aria-expanded={installMode === 'ios' ? iosHelp : undefined}
            style={row}
          >
            <Download size={20} color="var(--muted)" aria-hidden="true" />
            <span style={{ flex: 1 }}>{t('pwa.install')}</span>
          </button>
          {iosHelp && (
            <div style={{ margin: '0 12px 8px 46px', fontSize: 13, color: 'var(--muted)', lineHeight: 1.6 }}>
              <strong style={{ display: 'block', color: 'var(--text)' }}>{t('pwa.ios_title')}</strong>
              <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>1. {t('pwa.ios_step1')} <Share size={14} aria-hidden="true" /></span>
              <span style={{ display: 'block' }}>2. {t('pwa.ios_step2')}</span>
            </div>
          )}
        </>
      )}

      {user && (
        <div style={{ ...row, cursor: 'default', marginTop: 6, gap: 12 }}>
          <span style={{
            width: 34, height: 34, borderRadius: '50%', flexShrink: 0,
            background: 'var(--accent-dim)', display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <User size={16} color="var(--accent)" aria-hidden="true" />
          </span>
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: 'block', fontSize: 14, fontWeight: 600, overflow: 'hidden', overflowWrap: 'anywhere', }}>
              {user.full_name || user.email.split('@')[0]}
            </span>
            <span style={{ display: 'block', fontSize: 12, color: 'var(--dim)' }}>{roleLabel(t, user.role)}</span>
          </span>
        </div>
      )}
      <button type="button" className="tap-feedback" onClick={logout} style={{ ...row, color: 'var(--signal-order-now-fg)' }}>
        <LogOut size={20} aria-hidden="true" />
        <span style={{ flex: 1 }}>{t('sidebar.logout')}</span>
      </button>
      <h3 style={groupTitle}>{t('legal.group')}</h3>
      <LegalLinks onNavigate={onClose} />
    </BottomSheet>
  )
}

function Segmented({ label, value, options, onChange }: {
  label: string
  value: string
  options: { id: string; label: string }[]
  onChange: (id: string) => void
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, minHeight: 56, padding: '0 12px' }}>
      <span style={{ flex: 1, fontSize: 15 }}>{label}</span>
      <div role="radiogroup" aria-label={label} style={{
        display: 'flex', padding: 3, gap: 3, borderRadius: 10,
        background: 'var(--surface-2)', border: '1px solid var(--border)',
      }}>
        {options.map(o => {
          const on = o.id === value
          return (
            <button
              key={o.id}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => onChange(o.id)}
              style={{
                all: 'unset', boxSizing: 'border-box', cursor: 'pointer',
                minHeight: 44, minWidth: 64, padding: '0 12px', borderRadius: 8,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 13, fontWeight: on ? 700 : 500,
                background: on ? 'var(--surface)' : 'transparent',
                color: on ? 'var(--text)' : 'var(--muted)',
                boxShadow: on ? '0 1px 3px rgba(0,0,0,0.12)' : 'none',
                transition: 'background var(--dur-2) var(--ease-out)',
              }}
            >
              {o.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}
