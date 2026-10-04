'use client'
/**
 * /configuracion — the settings home.
 *
 * The sidebar used to list eighteen screens, and the owner's reading of it was
 * right: a menu that size hides the five screens a buyer opens every day among
 * the ones opened twice a year. The sidebar now carries the daily screens and
 * this one entry; everything else that is "set up once, look at sometimes"
 * lives here, one click further, each card linking to the screen that already
 * existed. Nothing here edits anything — it is an index, so no screen had to be
 * rewritten and every old URL still works.
 *
 * Desktop: an account band, then a grid of section cards. Phone: the same
 * sections as a grouped list (components/mobile), like a phone's own settings.
 * Role decides what is listed, by the same `adminOnly` rule as the rest of the
 * navigation (components/layout/navItems.ts).
 */
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ChevronRight, User } from 'lucide-react'
import { getUser, type AuthUser } from '@/lib/auth'
import { roleLabel } from '@/lib/enumLabels'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { SCREENS, canSee, type Screen } from '@/components/layout/navItems'
import { MobileList, MobileCard, MobileSection } from '@/components/mobile'
import LegalLinks from '@/components/legal/LegalLinks'

interface Row { screen: Screen; descKey: string }
interface Section { id: string; titleKey: string; rows: Row[] }

const screen = (href: string) => SCREENS.find(s => s.href === href)!

// Order is deliberate: who works here, what goes in, what runs on its own,
// what already happened, and the deployment itself last (operators only).
const SECTIONS: Section[] = [
  { id: 'team', titleKey: 'hub.team_title', rows: [
    { screen: screen('/usuarios'), descKey: 'hub.users_desc' },
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

export default function SettingsHubPage() {
  const { t }  = useLanguage()
  const narrow = useIsNarrow()
  // Read after mount: the cached identity lives in localStorage, which does
  // not exist while the page renders on the server.
  const [user, setUser] = useState<AuthUser | null>(null)
  useEffect(() => { setUser(getUser()) }, [])

  const sections = SECTIONS
    .map(s => ({ ...s, rows: s.rows.filter(r => canSee(r.screen, user?.role)) }))
    .filter(s => s.rows.length > 0)

  const name = user ? (user.full_name || user.email.split('@')[0]) : ''

  if (narrow) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        <MobileSection title={t('hub.account_title')}>
          <MobileList ariaLabel={t('hub.account_title')}>
            <MobileCard
              href={ACCOUNT_HREF}
              leading={<Avatar size={40} />}
              title={name || t('nav.account')}
              subtitle={t('hub.account_desc')}
            />
          </MobileList>
        </MobileSection>

        {sections.map(s => (
          <MobileSection key={s.id} title={t(s.titleKey)}>
            <MobileList ariaLabel={t(s.titleKey)}>
              {s.rows.map(({ screen: sc, descKey }) => (
                <MobileCard
                  key={sc.href}
                  href={sc.href}
                  leading={<IconTile Icon={sc.Icon} size={32} />}
                  title={t(sc.labelKey)}
                  subtitle={t(descKey)}
                />
              ))}
            </MobileList>
          </MobileSection>
        ))}

        <MobileSection title={t('legal.group')}>
          <LegalLinks />
        </MobileSection>
      </div>
    )
  }

  return (
    <div style={{ width: '100%', maxWidth: 1040, display: 'flex', flexDirection: 'column', gap: 22 }}>
      <header>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, letterSpacing: '-0.02em', color: 'var(--text)' }}>
          {t('nav.config')}
        </h1>
        <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--muted)', maxWidth: 560, lineHeight: 1.5 }}>
          {t('hub.subtitle')}
        </p>
      </header>

      {/* Your account, in the sidebar's petrol: the one place on this screen
          that is about you rather than the business. */}
      <Link href={ACCOUNT_HREF} className="hub-account" style={{
        display: 'flex', alignItems: 'center', gap: 16, textDecoration: 'none',
        padding: '18px 22px', borderRadius: 14,
        background: 'var(--sidebar-bg)', color: 'var(--sidebar-text-active)',
      }}>
        <Avatar size={46} onPetrol />
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 16, fontWeight: 650, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {name || t('nav.account')}
            </span>
            {user && (
              <span style={{ fontSize: 12, color: 'var(--sidebar-dim)' }}>
                {user.email} · {roleLabel(t, user.role)}
              </span>
            )}
          </span>
          <span style={{ display: 'block', marginTop: 4, fontSize: 13, color: 'var(--sidebar-text)' }}>
            {t('hub.account_desc')}
          </span>
        </span>
        <span style={{
          display: 'inline-flex', alignItems: 'center', gap: 4, flexShrink: 0,
          fontSize: 12.5, fontWeight: 600, color: 'var(--sidebar-beam)',
        }}>
          {t('hub.account_open')}
          <ChevronRight size={15} aria-hidden="true" />
        </span>
      </Link>

      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
        gap: 16, alignItems: 'start',
      }}>
        {sections.map(s => (
          <section key={s.id} aria-labelledby={`hub-${s.id}`} style={card}>
            <h2 id={`hub-${s.id}`} style={cardTitle}>{t(s.titleKey)}</h2>
            <ul style={{ listStyle: 'none', margin: 0, padding: '0 6px 6px' }}>
              {s.rows.map(({ screen: sc, descKey }) => (
                <li key={sc.href}>
                  <Link href={sc.href} className="hub-row" style={{
                    display: 'flex', alignItems: 'center', gap: 12, textDecoration: 'none',
                    padding: '10px 10px', borderRadius: 9, color: 'var(--text)',
                  }}>
                    <IconTile Icon={sc.Icon} size={32} />
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ display: 'block', fontSize: 13.5, fontWeight: 600 }}>{t(sc.labelKey)}</span>
                      <span style={{ display: 'block', marginTop: 2, fontSize: 12, color: 'var(--muted)', lineHeight: 1.4 }}>
                        {t(descKey)}
                      </span>
                    </span>
                    <ChevronRight size={15} color="var(--dim)" aria-hidden="true" style={{ flexShrink: 0 }} />
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))}

        <section aria-labelledby="hub-legal" style={card}>
          <h2 id="hub-legal" style={cardTitle}>{t('legal.group')}</h2>
          <div style={{ padding: '0 8px 8px' }}>
            <LegalLinks />
          </div>
        </section>
      </div>
    </div>
  )
}

const card: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12,
  minWidth: 0,
}

const cardTitle: React.CSSProperties = {
  margin: 0, padding: '14px 16px 6px', fontSize: 14, fontWeight: 650,
  color: 'var(--text)', letterSpacing: '-0.01em',
}

function IconTile({ Icon, size }: { Icon: React.ElementType; size: number }) {
  return (
    <span aria-hidden="true" style={{
      width: size, height: size, borderRadius: 8, flexShrink: 0,
      background: 'var(--accent-dim)', color: 'var(--accent)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <Icon size={Math.round(size * 0.5)} strokeWidth={1.9} />
    </span>
  )
}

function Avatar({ size, onPetrol }: { size: number; onPetrol?: boolean }) {
  return (
    <span aria-hidden="true" style={{
      width: size, height: size, borderRadius: '50%', flexShrink: 0,
      background: onPetrol ? 'var(--sidebar-active-bg)' : 'var(--accent-dim)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <User size={Math.round(size * 0.45)} color={onPetrol ? 'var(--sidebar-beam)' : 'var(--accent)'} />
    </span>
  )
}
