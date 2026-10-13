import type { Metadata } from 'next'
import LandingPage from '@/components/landing/LandingPage'
import StructuredData from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { EN_HOME_PATH } from '@/components/landing/sitePaths'
import { SITE_META } from '@/i18n/siteMeta'
import type { Lang } from '@/i18n/translations'

// The home page's server side in either language: `/` (Spanish) and `/en`
// (English) render the same page from their own catalogue.
export function homeMetadata(lang: Lang): Metadata {
  const M = SITE_META[lang].home
  return {
    ...subpageMetadata(lang === 'en' ? EN_HOME_PATH : '/', M.title, M.description, M.imageAlt),
    keywords: M.keywords,
  }
}

export function LandingHome({ lang }: { lang: Lang }) {
  return (
    <>
      <StructuredData lang={lang} />
      <LandingPage />
    </>
  )
}
