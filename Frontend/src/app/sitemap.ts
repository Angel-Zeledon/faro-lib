import type { MetadataRoute } from 'next'
import { SITE_URL } from '@/lib/siteUrls'
import { DOC_ORDER, docHref } from '@/i18n/docs/tree'

// The day the landing, product and content pages last had their copy revised
// (repositioned as sales forecasting). A lastmod a crawler can trust is one that
// moves only when the page does, so it is a constant, not the build time; legal
// and help pages carry none.
const COPY_REVISED = new Date('2026-10-12')

// Served as /sitemap.xml. The landing, its pages (subpages, industries,
// content pages, legal documents, help center) is the whole public surface
// (the two manual PDFs are downloads, kept out of the index); robots.ts keeps everything else out. Every landing
// route is listed here by name: `npm run check:landing-routes` enforces it.
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { lastModified: COPY_REVISED, url: `${SITE_URL}/`, changeFrequency: 'weekly', priority: 1 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/como-funciona`, changeFrequency: 'monthly', priority: 0.8 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/precios`, changeFrequency: 'monthly', priority: 0.8 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/preguntas-frecuentes`, changeFrequency: 'monthly', priority: 0.7 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/seguridad`, changeFrequency: 'monthly', priority: 0.6 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/desarrolladores`, changeFrequency: 'monthly', priority: 0.6 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/industrias`, changeFrequency: 'monthly', priority: 0.7 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/industrias/consumo-masivo`, changeFrequency: 'monthly', priority: 0.6 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/industrias/ferreteria`, changeFrequency: 'monthly', priority: 0.6 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/industrias/farmacia`, changeFrequency: 'monthly', priority: 0.6 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/industrias/autopartes`, changeFrequency: 'monthly', priority: 0.6 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/industrias/retail`, changeFrequency: 'monthly', priority: 0.6 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/stockai-vs-excel`, changeFrequency: 'monthly', priority: 0.7 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/como-se-calcula`, changeFrequency: 'monthly', priority: 0.7 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/que-es-stockai`, changeFrequency: 'monthly', priority: 0.8 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/software-de-inventario-para-distribuidores`, changeFrequency: 'monthly', priority: 0.7 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/como-calcular-el-punto-de-reorden`, changeFrequency: 'monthly', priority: 0.7 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/stock-de-seguridad`, changeFrequency: 'monthly', priority: 0.7 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/pronostico-de-demanda-para-compras`, changeFrequency: 'monthly', priority: 0.7 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/integraciones`, changeFrequency: 'monthly', priority: 0.6 },
    { lastModified: COPY_REVISED, url: `${SITE_URL}/novedades`, changeFrequency: 'weekly', priority: 0.6 },
    { url: `${SITE_URL}/privacidad`, changeFrequency: 'yearly', priority: 0.3 },
    { url: `${SITE_URL}/terminos`, changeFrequency: 'yearly', priority: 0.3 },
    { url: `${SITE_URL}/cookies`, changeFrequency: 'yearly', priority: 0.3 },
    { url: `${SITE_URL}/aviso-legal`, changeFrequency: 'yearly', priority: 0.3 },
    { url: `${SITE_URL}/legal`, changeFrequency: 'yearly', priority: 0.3 },
    { url: `${SITE_URL}/uso-aceptable`, changeFrequency: 'yearly', priority: 0.3 },
    { url: `${SITE_URL}/procesamiento-de-datos`, changeFrequency: 'yearly', priority: 0.3 },
    { url: `${SITE_URL}/ia`, changeFrequency: 'yearly', priority: 0.3 },
    { url: `${SITE_URL}/divulgacion-responsable`, changeFrequency: 'yearly', priority: 0.3 },
    { url: `${SITE_URL}/accesibilidad`, changeFrequency: 'yearly', priority: 0.3 },
    { url: `${SITE_URL}/condiciones-comerciales`, changeFrequency: 'yearly', priority: 0.3 },
    { url: `${SITE_URL}/licencia`, changeFrequency: 'yearly', priority: 0.3 },
    { url: `${SITE_URL}/docs`, changeFrequency: 'weekly', priority: 0.7 },
    ...DOC_ORDER.map(id => ({ url: `${SITE_URL}${docHref(id)}`, changeFrequency: 'monthly' as const, priority: 0.5 })),
  ]
}
