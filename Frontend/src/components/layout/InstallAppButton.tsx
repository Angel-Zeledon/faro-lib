'use client'
import { useEffect, useState } from 'react'
import { Download, Share, X } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useInstall, registerServiceWorker } from '@/lib/pwa'

/** Mounted once by AppShell: registers the service worker. */
export function PwaRegister() {
  useEffect(() => { registerServiceWorker() }, [])
  return null
}

/**
 * "Install the app", in the sidebar. Shown only when this browser can install
 * StockAI and it is not already running installed. On iPhone (no install
 * prompt exists) it opens the two steps Safari needs instead.
 */
export function InstallAppButton({ collapsed }: { collapsed: boolean }) {
  const { t } = useLanguage()
  const { mode, install } = useInstall()
  const [iosHelp, setIosHelp] = useState(false)

  if (!mode) return null

  return (
    <div style={{ position: 'relative', marginTop: 8 }}>
      <button
        type="button"
        onClick={() => (mode === 'prompt' ? void install() : setIosHelp(v => !v))}
        title={collapsed ? t('pwa.install') : undefined}
        style={{
          all: 'unset', cursor: 'pointer', boxSizing: 'border-box',
          display: 'flex', alignItems: 'center', justifyContent: collapsed ? 'center' : 'flex-start',
          gap: collapsed ? 0 : 8, width: '100%',
          padding: collapsed ? '8px 0' : '8px 10px', borderRadius: 7,
          color: 'var(--sidebar-text-active)', fontSize: 12, fontWeight: 600,
          background: 'var(--sidebar-active-bg)',
        }}
      >
        <Download size={14} />
        {!collapsed && <span>{t('pwa.install')}</span>}
      </button>

      {iosHelp && (
        <div
          role="dialog"
          aria-label={t('pwa.install')}
          style={{
            position: 'absolute', left: collapsed ? 52 : 0, bottom: 'calc(100% + 8px)', zIndex: 50,
            width: 220, padding: '12px 14px', borderRadius: 10,
            background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)',
            boxShadow: '0 12px 30px rgba(0,0,0,0.25)', fontSize: 12.5, lineHeight: 1.5,
          }}
        >
          <button
            type="button" onClick={() => setIosHelp(false)} aria-label={t('common.close')}
            style={{ all: 'unset', cursor: 'pointer', position: 'absolute', top: 8, right: 8, color: 'var(--dim)' }}
          >
            <X size={13} />
          </button>
          <strong style={{ display: 'block', marginBottom: 6 }}>{t('pwa.ios_title')}</strong>
          <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            1. {t('pwa.ios_step1')} <Share size={13} />
          </span>
          <span style={{ display: 'block', marginTop: 4 }}>2. {t('pwa.ios_step2')}</span>
        </div>
      )}
    </div>
  )
}
