'use client'
// next/link for the public site's pages: the href is written once, with the
// Spanish slug the catalogues use, and comes out as the English counterpart on
// an English page (see localizeHref). The public components import this in
// place of next/link, so no English page can link to a Spanish one by accident.
import Link from 'next/link'
import { forwardRef, type ComponentProps } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import { localizeHref } from '@/components/landing/sitePaths'

type Props = Omit<ComponentProps<typeof Link>, 'href'> & { href: string }

const SiteLink = forwardRef<HTMLAnchorElement, Props>(function SiteLink({ href, ...rest }, ref) {
  const { lang } = useLanguage()
  return <Link ref={ref} href={localizeHref(href, lang)} {...rest} />
})

export default SiteLink

/** The same mapping for a plain <a>, where a full page load is wanted. */
export function useLocalHref(): (href: string) => string {
  const { lang } = useLanguage()
  return (href: string) => localizeHref(href, lang)
}
