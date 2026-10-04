import type { Lang } from '@/i18n/translations'
import { DOCS } from '@/i18n/docs/index'
import { DOC_ORDER, type DocPageId } from '@/i18n/docs/tree'

/** The sidebar's labels in both languages — the only part of the whole
 *  catalogue every help-center page sends to the browser. */
export function navTitles(): Record<Lang, Record<DocPageId, string>> {
  const pick = (lang: Lang) =>
    Object.fromEntries(DOC_ORDER.map(id => [id, DOCS[lang][id].nav ?? DOCS[lang][id].title])) as Record<DocPageId, string>
  return { es: pick('es'), en: pick('en') }
}
