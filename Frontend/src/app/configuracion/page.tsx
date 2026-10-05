'use client'
/**
 * /configuracion — the settings home.
 *
 * The sidebar used to list eighteen screens, and the owner's reading of it was
 * right: a menu that size hides the five screens a buyer opens every day among
 * the ones opened twice a year. The sidebar now carries the daily screens and
 * this one entry; everything else that is "set up once, look at sometimes"
 * lives here, one click further, each row linking to the screen that already
 * existed. Nothing here edits anything — it is an index, so no screen had to be
 * rewritten and every old URL still works.
 *
 * Layout: ONE column, one width, on desktop and phone alike. Each section is a
 * small heading over a single card; every row in every card is the same height
 * with the same icon tile, so nothing staggers. The earlier card grid mixed
 * cards of one and three rows and a "Legal" card of a different shape, which
 * read as misaligned boxes.
 *
 * Which rows are drawn follows the same presentation rules as the sidebar
 * (components/layout/navItems.ts `visibleWhen`, fed by hooks/useTenantFacts):
 * a row for something the tenant has no use for yet is left out, never locked —
 * the screens stay reachable by URL and from the command palette.
 */
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ChevronRight, User } from 'lucide-react'
import { getUser, type AuthUser } from '@/lib/auth'
import { roleLabel } from '@/lib/enumLabels'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { SCREENS, canSee, type Screen } from '@/components/layout/navItems'
import { useTenantFacts, type TenantFacts } from '@/hooks/useTenantFacts'
import LegalLinks from '@/components/legal/LegalLinks'

interface Row { screen: Screen; descKey: string }
interface Section { id: string; titleKey: string; rows: Row[] }

const screen = (href: string) => SCREENS.find(s => s.href === href)!

// Order is deliberate: who works here, what goes in, what runs on its own,
// what already happened, and the deployment itself last (operators only).
const SECTIONS: Section[] = [
  { id: 'team', titleKey: 'hub.team_title', rows: [
    { screen: screen('/usuarios'), descKey: 'hub.users_desc' },
    { screen: screen('/aprobaciones'), descKey: 'hub.po_approval_desc' },
  ] },
  { id: 'data', titleKey: 'hub.data_title', rows: [
    { screen: screen('/ventas'),                descKey: 'hub.sales_desc' },
    { screen: screen('/archivos'),              descKey: 'hub.files_desc' },
    { screen: screen('/configurar-inventario'), descKey: 'hub.inventory_setup_desc' },
  ] },
  { id: 'connect', titleKey: 'hub.connect_title', rows: [
    { screen: screen('/automatizacion'), descKey: 'hub.automation_desc' },
    { screen: screen('/api'),            descKey: 'hub.api_desc' },
  ] },
  { id: 'activity', titleKey: 'hub.activity_title', rows: [
    { screen: screen('/actividad'), descKey: 'activity.subtitle' },
  ] },
  { id: 'installation', titleKey: 'hub.installation_title', rows: [
    { screen: screen('/instalacion'), descKey: 'hub.installation_desc' },
  ] },
]

const ACCOUNT_HREF = '/mi-cuenta'

/** True only when every count is known and zero: nothing connected yet. */
const nothingConnected = (f: TenantFacts) =>
  [f.apiKeys, f.webhooks, f.schedules].every(n => n === 0)

// One size for every row, so no card is taller than its neighbour by accident.
const ROW_MIN_HEIGHT = 68

export default function SettingsHubPage() {
  const { t }  = useLanguage()
  const narrow = useIsNarrow()
  // Read after mount: the cached identity lives in localStorage, which does
  // not exist while the page renders on the server.
  const [user, setUser] = useState<AuthUser | null>(null)
  useEffect(() => { setUser(getUser()) }, [])
  const facts = useTenantFacts('connect')

  const sections = SECTIONS
    .map(s => ({
      ...s,
      rows: s.rows
        .filter(r => canSee(r.screen, user?.role, facts))
        // Nothing connected yet: say in one line that this is optional.
        .map(r => r.screen.href === '/automatizacion' && nothingConnected(facts)
          ? { ...r, descKey: 'hub.connect_optional_desc' }
          : r),
    }))
    .filter(s => s.rows.length > 0)

  const name = user ? (user.full_name || user.email.split('@')[0]) : ''

  return (
    <div style={{ width: '100%', maxWidth: 720, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* The top bar already says "Configuración" (a phone's app bar too):
          only the one-line purpose here, on desktop. */}
      {!narrow && (
        <p style={{ margin: 0, fontSize: 13, color: 'var(--muted)', lineHeight: 1.5 }}>
          {t('hub.subtitle')}
        </p>
      )}

      <HubSection id="account" title={t('hub.account_title')}>
        <HubRow
          href={ACCOUNT_HREF}
          icon={<User size={ICON_SIZE} strokeWidth={ICON_STROKE} aria-hidden="true" />}
          title={name || t('nav.account')}
          description={user ? `${user.email} · ${roleLabel(t, user.role)}` : t('hub.account_desc')}
          last
        />
      </HubSection>

      {sections.map(s => (
        <HubSection key={s.id} id={s.id} title={t(s.titleKey)}>
          {s.rows.map(({ screen: sc, descKey }, i) => {
            const Icon = sc.Icon
            return (
              <HubRow
                key={sc.href}
                href={sc.href}
                icon={<Icon size={ICON_SIZE} strokeWidth={ICON_STROKE} aria-hidden="true" />}
                title={t(sc.labelKey)}
                description={t(descKey)}
                last={i === s.rows.length - 1}
              />
            )
          })}
        </HubSection>
      ))}

      <HubSection id="legal" title={t('legal.group')}>
        <div style={{ padding: '6px 8px', minHeight: ROW_MIN_HEIGHT - 12, display: 'flex', alignItems: 'center' }}>
          <LegalLinks />
        </div>
      </HubSection>
    </div>
  )
}

// One icon size and weight for the whole screen (components/ui/icons.ts: 18-22
// inside tiles, default-to-light stroke).
const ICON_SIZE = 18
const ICON_STROKE = 1.7

function HubSection({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={`hub-${id}`} style={{ minWidth: 0 }}>
      <h2 id={`hub-${id}`} style={{
        margin: '0 4px 8px', fontSize: 12, fontWeight: 700, color: 'var(--dim)',
        textTransform: 'uppercase', letterSpacing: '0.06em',
      }}>
        {title}
      </h2>
      <div style={{
        background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12,
        overflow: 'hidden',
      }}>
        {children}
      </div>
    </section>
  )
}

function HubRow({ href, icon, title, description, last }: {
  href: string
  icon: React.ReactNode
  title: string
  description: string
  last?: boolean
}) {
  return (
    <Link href={href} className="hub-row tap-feedback" style={{
      display: 'flex', alignItems: 'center', gap: 14, textDecoration: 'none',
      minHeight: ROW_MIN_HEIGHT, boxSizing: 'border-box', padding: '12px 16px',
      color: 'var(--text)',
      borderBottom: last ? 'none' : '1px solid var(--border)',
    }}>
      <span aria-hidden="true" style={{
        width: 36, height: 36, borderRadius: 9, flexShrink: 0,
        background: 'var(--accent-dim)', color: 'var(--accent)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        {icon}
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: 'block', fontSize: 14, fontWeight: 600, lineHeight: 1.3, overflowWrap: 'anywhere' }}>
          {title}
        </span>
        <span style={{ display: 'block', marginTop: 2, fontSize: 13, color: 'var(--muted)', lineHeight: 1.4, overflowWrap: 'anywhere' }}>
          {description}
        </span>
      </span>
      <ChevronRight size={16} color="var(--dim)" aria-hidden="true" style={{ flexShrink: 0 }} />
    </Link>
  )
}
