'use client'
/**
 * The tab strip across the top of Pronósticos and the three analysis screens
 * that live under it (Escenarios, Impacto, Historial).
 *
 * Those three used to be sidebar entries of their own. Nobody opens them to
 * get the day's buying done — they open them to understand a forecast — so
 * the sidebar now carries Pronósticos alone and this strip moves between the
 * four. It sits between the top bar and the page, rendered by AppShell, so the
 * four screens did not have to be rewritten to carry it and it reads the same
 * on desktop and on a phone.
 *
 * On a phone it steps aside while a screen shows an in-page detail view with
 * its own back arrow (one SKU on /pronosticos), where a second row of
 * navigation would compete with that arrow.
 */
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { useMobileHeaderOverride } from '@/components/mobile/MobileHeaderContext'
import { ANALYSIS_TABS, drawn, type Screen } from './navItems'
import { useTenantFacts } from '@/hooks/useTenantFacts'
import { getUser } from '@/lib/auth'

/** Height the strip takes, exported so a screen sized to the viewport
 *  (/pronosticos on desktop) can subtract it. */
export const SECTION_TABS_HEIGHT = 41

const onRoute = (href: string, path: string) => path === href || path.startsWith(`${href}/`)

/** The strip's tabs for this path: the analysis screens the tenant has data
 *  for (plus the one open). Fewer than two is not a strip — a lone
 *  "Pronósticos" tab is chrome without a choice, so nothing is drawn. */
export function useSectionTabs(): Screen[] {
  const path  = usePathname()
  const facts = useTenantFacts()
  if (!ANALYSIS_TABS.some(tab => onRoute(tab.href, path))) return []
  const role = getUser()?.role
  const tabs = ANALYSIS_TABS.filter(tab => drawn(tab, role, facts, path))
  return tabs.length >= 2 ? tabs : []
}

export default function SectionTabs() {
  const path   = usePathname()
  const { t }  = useLanguage()
  const narrow = useIsNarrow()
  const header = useMobileHeaderOverride()
  const tabs   = useSectionTabs()

  if (tabs.length === 0) return null
  if (narrow && (header?.onBack || header?.backHref)) return null

  return (
    <nav
      aria-label={t('nav.analysis_tabs')}
      className="section-tabs"
      style={{
        display: 'flex', alignItems: 'stretch', gap: narrow ? 0 : 4,
        height: SECTION_TABS_HEIGHT, boxSizing: 'border-box', flexShrink: 0,
        padding: narrow ? '0 4px' : '0 20px',
        background: 'var(--surface)', borderBottom: '1px solid var(--border)',
        overflowX: 'auto', scrollbarWidth: 'none',
      }}
    >
      {tabs.map(({ href, labelKey, Icon }) => {
        const active = onRoute(href, path)
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? 'page' : undefined}
            className="tap-feedback"
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
              padding: narrow ? '0 6px' : '0 12px',
              ...(narrow ? { flex: '1 0 auto', minWidth: 0 } : {}),
              fontSize: 13, fontWeight: active ? 650 : 500, whiteSpace: 'nowrap',
              textDecoration: 'none',
              color: active ? 'var(--text)' : 'var(--muted)',
              // The indicator sits on the strip's own bottom rule, so the
              // active tab reads as the open page of the four.
              boxShadow: active ? 'inset 0 -2px 0 var(--accent)' : 'none',
              transition: 'color var(--dur-2) var(--ease-out), box-shadow var(--dur-2) var(--ease-out)',
            }}
          >
            {!narrow && <Icon size={14} strokeWidth={active ? 2.2 : 1.8} color={active ? 'var(--accent)' : 'currentColor'} aria-hidden="true" />}
            {t(labelKey)}
          </Link>
        )
      })}
    </nav>
  )
}
