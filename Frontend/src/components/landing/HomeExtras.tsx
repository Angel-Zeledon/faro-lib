'use client'
// Two blocks added to the home page (2026-10-05, owner: "keep expanding the
// landing"): a short product tour with the real screenshots the tour already
// ships, and "who it is for / who it is not" with a way into the content
// pages. Copy: i18n/landingContent.ts (`home`). The screenshots are the same
// files the full tour uses (Frontend/public/shot-*.png): nothing is made up.
import Link from 'next/link'
import { useLanguage } from '@/contexts/LanguageContext'
import { LANDING } from '@/i18n/landing'
import { LANDING_CONTENT } from '@/i18n/landingContent'
import { Section, Tag, H2, Lead, Check, Dash } from '@/components/landing/primitives'
import { CP_CSS } from '@/components/landing/ContentPages'
import { GUIDES } from '@/i18n/landingGuides'
import { CONTENT_PATHS, GUIDE_ORDER, GUIDE_PATHS, INDUSTRIES_HUB_PATH } from '@/components/landing/contentPaths'

// The first three screens of the tour's first chapter: the panel, the
// inventory and the purchase orders — what the morning section describes.
const TOUR_SCREENS = 3

export function HomeTour() {
  const { lang } = useLanguage()
  const C = LANDING_CONTENT[lang].home
  const screens = LANDING[lang].tour.chapters[0].screens.slice(0, TOUR_SCREENS)
  return (
    <Section id="aplicacion" alt>
      <style dangerouslySetInnerHTML={{ __html: CP_CSS }} />
      <Tag>{C.tourTag}</Tag>
      <H2>{C.tourTitle}</H2>
      <Lead maxWidth={640}>{C.tourLead}</Lead>
      <ul className="cp-tour">
        {screens.map(({ img, name, does, alt }) => (
          <li key={img} data-reveal>
            <figure>
              <div className="tour-shot">
                <div className="tour-shot-in">
                  <img src={img} alt={alt} loading="lazy" decoding="async" width={3200} height={2000} />
                </div>
              </div>
              <figcaption>
                <h3>{name}</h3>
                <p>{does}</p>
              </figcaption>
            </figure>
          </li>
        ))}
      </ul>
      <Link href="/como-funciona#guia" className="lp-more">{C.tourMore}</Link>
    </Section>
  )
}

export function HomeAudience() {
  const { lang } = useLanguage()
  const C = LANDING_CONTENT[lang]
  const H = C.home
  const links: [string, string][] = [
    [INDUSTRIES_HUB_PATH, C.industries.hub.label],
    [CONTENT_PATHS.method, C.method.label],
    [CONTENT_PATHS.excel, C.excel.label],
    [CONTENT_PATHS.integrations, C.integrations.label],
    [CONTENT_PATHS.changelog, C.changelog.label],
    ...GUIDE_ORDER.map((k): [string, string] => [GUIDE_PATHS[k], GUIDES[lang].items[k].label]),
  ]
  return (
    <Section id="para-quien">
      <style dangerouslySetInnerHTML={{ __html: CP_CSS }} />
      <Tag>{H.audienceTag}</Tag>
      <H2>{H.audienceTitle}</H2>
      <Lead maxWidth={640}>{H.audienceLead}</Lead>
      <div className="cp-audience">
        <div className="lp-card">
          <div className="lp-card-title">{H.forTitle}</div>
          <ul className="cp-list">
            {H.for.map(t => <li key={t}><Check /><span>{t}</span></li>)}
          </ul>
        </div>
        <div className="lp-card">
          <div className="lp-card-title">{H.notForTitle}</div>
          <ul className="cp-list">
            {H.notFor.map(t => <li key={t}><Dash /><span>{t}</span></li>)}
          </ul>
        </div>
      </div>
      <nav aria-label={H.exploreTitle} className="cp-pills">
        <span className="lp-label" style={{ marginRight: 4 }}>{H.exploreTitle}:</span>
        {links.map(([href, label]) => <Link key={href} href={href}>{label}</Link>)}
      </nav>
    </Section>
  )
}
