// The public site's English URLs, and the one place that pairs every Spanish
// public URL with its English counterpart. A plain module (no 'use client') so
// the server wrappers, the sitemap, the app shell, the auth guard and the
// language switch all read the same map.
//
// The Spanish URLs are permanent (people have shared them) and stay exactly as
// they are; English lives under /en. Every map below is a `Record` over the
// same keys as its Spanish twin, so a page without an English counterpart does
// not compile. `npm run check:landing-routes` reads this file as text (every
// path is a quoted literal for that reason) and fails when an English path is
// duplicated, does not start with /en, or is missing from the sitemap, robots,
// the Caddy matcher or the page files.
//
// Not here on purpose: the signed-in app, the sign-in forms (/login, /signup,
// /prueba) and the links in messages (/proveedor/…, /aprobar/…). Those follow
// the user's own language setting, not the URL.
import type { Lang } from '@/i18n/translations'
import { SUBPAGE_PATHS, type SubpageKey } from '@/components/landing/subpagePaths'
import { LEGAL_HUB_PATH, LEGAL_PATHS, type LegalKey } from '@/components/landing/legalPaths'
import {
  CONTENT_PATHS, GUIDE_PATHS, INDUSTRIES_HUB_PATH, INDUSTRY_PATHS,
  type ContentKey, type GuideKey, type IndustryKey,
} from '@/components/landing/contentPaths'
import { DOC_ORDER, DOCS_BASE, type DocPageId } from '@/i18n/docs/tree'

export const EN_HOME_PATH = '/en'

export const DEVELOPERS_PATH = '/desarrolladores'

export const SUBPAGE_PATHS_EN: Record<SubpageKey, string> = {
  pricing: '/en/pricing',
  how: '/en/how-it-works',
  faq: '/en/faq',
  security: '/en/security',
}

export const DEVELOPERS_PATH_EN = '/en/developers'

export const INDUSTRIES_HUB_PATH_EN = '/en/industries'

export const INDUSTRY_SLUGS_EN: Record<IndustryKey, string> = {
  consumer: 'consumer-goods',
  hardware: 'hardware',
  pharmacy: 'pharmacy',
  autoparts: 'auto-parts',
  retail: 'retail',
}

export const INDUSTRY_PATHS_EN: Record<IndustryKey, string> = {
  consumer: '/en/industries/consumer-goods',
  hardware: '/en/industries/hardware',
  pharmacy: '/en/industries/pharmacy',
  autoparts: '/en/industries/auto-parts',
  retail: '/en/industries/retail',
}

export const CONTENT_PATHS_EN: Record<ContentKey, string> = {
  excel: '/en/stockai-vs-excel',
  method: '/en/how-it-is-calculated',
  integrations: '/en/integrations',
  changelog: '/en/whats-new',
}

export const GUIDE_PATHS_EN: Record<GuideKey, string> = {
  about: '/en/what-is-stockai',
  distributors: '/en/inventory-software-for-distributors',
  reorderPoint: '/en/how-to-calculate-the-reorder-point',
  safetyStock: '/en/safety-stock',
  forecast: '/en/demand-forecasting-for-purchasing',
}

export const LEGAL_PATHS_EN: Record<LegalKey, string> = {
  privacy: '/en/privacy',
  terms: '/en/terms',
  cookies: '/en/cookies',
  notice: '/en/legal-notice',
  acceptableUse: '/en/acceptable-use',
  dpa: '/en/data-processing',
  ai: '/en/ai',
  disclosure: '/en/responsible-disclosure',
  accessibility: '/en/accessibility',
  commercial: '/en/commercial-terms',
  license: '/en/license',
}

export const LEGAL_HUB_PATH_EN = '/en/legal'

export const DOCS_BASE_EN = '/en/docs'

// The help center's English page ids, one per Spanish id: the URL is
// /en/docs/<id>. A section with a single page keeps a single segment.
export const DOC_IDS_EN: Record<DocPageId, string> = {
  'primeros-pasos/que-es-stockai': 'getting-started/what-is-stockai',
  'primeros-pasos/crear-tu-cuenta': 'getting-started/create-your-account',
  'primeros-pasos/subir-tus-ventas': 'getting-started/upload-your-sales',
  'primeros-pasos/configurar-inventario': 'getting-started/set-up-inventory',
  'primeros-pasos/tu-primera-orden': 'getting-started/your-first-order',
  'primeros-pasos/instalar-la-app': 'getting-started/install-the-app',
  'uso-diario/panel-de-compras': 'daily-use/purchasing-panel',
  'uso-diario/pedidos': 'daily-use/orders',
  'uso-diario/inventario': 'daily-use/inventory',
  'uso-diario/bodegas-y-traslados': 'daily-use/warehouses-and-transfers',
  'uso-diario/proveedores': 'daily-use/suppliers',
  'uso-diario/mensajes': 'daily-use/messages',
  'uso-diario/alertas-diarias': 'daily-use/daily-alerts',
  'analisis/pronosticos': 'analysis/forecasts',
  'analisis/impacto': 'analysis/impact',
  'analisis/escenarios': 'analysis/scenarios',
  'analisis/historial': 'analysis/history',
  'analisis/actividad': 'analysis/activity',
  'conceptos/semaforo': 'concepts/stock-signal',
  'conceptos/punto-de-reorden': 'concepts/reorder-point',
  'conceptos/stock-de-seguridad': 'concepts/safety-stock',
  'conceptos/tiempo-de-entrega-aprendido': 'concepts/learned-lead-time',
  'conceptos/como-compiten-los-modelos': 'concepts/how-the-models-compete',
  'conceptos/granularidad': 'concepts/granularity',
  'conceptos/en-camino': 'concepts/in-transit',
  'asistente/asistente-ia': 'assistant/ai-assistant',
  'asistente/asistente-por-whatsapp': 'assistant/whatsapp-assistant',
  'asistente/privacidad-del-asistente': 'assistant/assistant-privacy',
  'administracion/usuarios-y-roles': 'administration/users-and-roles',
  'administracion/mi-cuenta': 'administration/my-account',
  'administracion/automatizacion': 'administration/automation',
  'administracion/instalacion': 'administration/installation',
  'administracion/limites-del-plan': 'administration/plan-limits',
  'integraciones/api': 'integrations/api',
  'integraciones/mcp': 'integrations/mcp',
  'integraciones/fuentes-sql': 'integrations/sql-sources',
  'integraciones/whatsapp': 'integrations/whatsapp',
  'integraciones/correo': 'integrations/email',
  'solucion-de-problemas/archivo-rechazado': 'troubleshooting/rejected-file',
  'solucion-de-problemas/sin-datos': 'troubleshooting/no-data',
  'solucion-de-problemas/cuenta-y-acceso': 'troubleshooting/account-and-access',
  'novedades': 'whats-new',
}

/** A help-center page's URL in either language. */
export function docHrefFor(id: DocPageId, lang: Lang): string {
  return lang === 'en' ? `${DOCS_BASE_EN}/${DOC_IDS_EN[id]}` : `${DOCS_BASE}/${id}`
}

/** The Spanish page id behind an English one, or null. */
export function docIdFromEn(enId: string): DocPageId | null {
  return DOC_ORDER.find(id => DOC_IDS_EN[id] === enId) ?? null
}

const keysOf = <K extends string>(r: Record<K, string>) => Object.keys(r) as K[]

/** Every public page as a [Spanish, English] pair, help center included. */
export const ROUTE_PAIRS: readonly (readonly [string, string])[] = [
  ['/', EN_HOME_PATH],
  ...keysOf(SUBPAGE_PATHS).map(k => [SUBPAGE_PATHS[k], SUBPAGE_PATHS_EN[k]] as const),
  [DEVELOPERS_PATH, DEVELOPERS_PATH_EN],
  [INDUSTRIES_HUB_PATH, INDUSTRIES_HUB_PATH_EN],
  ...keysOf(INDUSTRY_PATHS).map(k => [INDUSTRY_PATHS[k], INDUSTRY_PATHS_EN[k]] as const),
  ...keysOf(CONTENT_PATHS).map(k => [CONTENT_PATHS[k], CONTENT_PATHS_EN[k]] as const),
  ...keysOf(GUIDE_PATHS).map(k => [GUIDE_PATHS[k], GUIDE_PATHS_EN[k]] as const),
  ...keysOf(LEGAL_PATHS).map(k => [LEGAL_PATHS[k], LEGAL_PATHS_EN[k]] as const),
  [LEGAL_HUB_PATH, LEGAL_HUB_PATH_EN],
  [DOCS_BASE, DOCS_BASE_EN],
  ...DOC_ORDER.map(id => [`${DOCS_BASE}/${id}`, docHrefFor(id, 'en')] as const),
]

const ES_TO_EN = new Map(ROUTE_PAIRS.map(([es, en]) => [es, en]))
const EN_TO_ES = new Map(ROUTE_PAIRS.map(([es, en]) => [en, es]))

/** Every English public URL, for the app shell and the auth guard. */
export const EN_PUBLIC_PATHS: string[] = ROUTE_PAIRS.map(([, en]) => en)

/** True for /en and anything below it. */
export function isEnglishPath(pathname: string): boolean {
  return pathname === EN_HOME_PATH || pathname.startsWith(`${EN_HOME_PATH}/`)
}

/**
 * The language a public page is written in, decided by its URL alone; null for
 * anything that is not a page of the public site (the app, the sign-in forms),
 * which follows the user's own setting instead.
 */
export function siteLangOfPath(pathname: string): Lang | null {
  if (EN_TO_ES.has(pathname)) return 'en'
  if (ES_TO_EN.has(pathname)) return 'es'
  return null
}

/** The same page in the other language: the path the language switch goes to. */
export function counterpartPath(pathname: string, lang: Lang): string | null {
  const fromEn = EN_TO_ES.get(pathname)
  const fromEs = ES_TO_EN.get(pathname)
  if (lang === 'en') return fromEn !== undefined ? pathname : fromEs ?? null
  return fromEs !== undefined ? pathname : fromEn ?? null
}

/** Both language versions of a public page, for hreflang. */
export function alternatesOf(pathname: string): { es: string; en: string } | null {
  const en = ES_TO_EN.get(pathname)
  if (en !== undefined) return { es: pathname, en }
  const es = EN_TO_ES.get(pathname)
  if (es !== undefined) return { es, en: pathname }
  return null
}

const APP_URL = (process.env.NEXT_PUBLIC_APP_URL || '').replace(/\/+$/, '')

// The sign-in forms a visitor reaches from the public site. An English page
// tells them which language to open in with ?lang=en, because on a split
// deployment the app is another origin and cannot read the landing's storage.
const APP_ENTRY_PATHS = ['/login', '/signup', '/prueba']

/**
 * A link as it must appear on a page in `lang`. On English pages a Spanish
 * public path (catalogue copy is written with the Spanish slugs) becomes its
 * English counterpart, keeping any #anchor or ?query, and a link into the
 * app's sign-in forms carries lang=en. Spanish pages get every href unchanged.
 */
export function localizeHref(href: string, lang: Lang): string {
  if (lang !== 'en') return href
  let origin = ''
  let rest = href
  if (APP_URL && href.startsWith(APP_URL)) {
    origin = APP_URL
    rest = href.slice(APP_URL.length) || '/'
  } else if (!href.startsWith('/') || href.startsWith('//')) {
    return href
  }
  const cut = rest.search(/[?#]/)
  const path = cut === -1 ? rest : rest.slice(0, cut)
  const suffix = cut === -1 ? '' : rest.slice(cut)
  if (APP_ENTRY_PATHS.includes(path)) {
    if (/[?&]lang=/.test(suffix)) return href
    const [query, hash = ''] = suffix.split('#')
    const q = query ? `${query}&lang=en` : '?lang=en'
    return `${origin}${path}${q}${hash ? `#${hash}` : ''}`
  }
  if (origin) return href
  const en = ES_TO_EN.get(path || '/')
  return en !== undefined ? `${en}${suffix}` : href
}
