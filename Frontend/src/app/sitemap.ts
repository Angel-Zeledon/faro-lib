import type { MetadataRoute } from 'next'
import { SITE_URL } from '@/lib/siteUrls'
import { DOC_ORDER, docHref } from '@/i18n/docs/tree'

// Served as /sitemap.xml. The landing, its four pages and the two manuals are
// the whole public surface; robots.ts keeps everything else out.
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: `${SITE_URL}/`, changeFrequency: 'weekly', priority: 1 },
    { url: `${SITE_URL}/como-funciona`, changeFrequency: 'monthly', priority: 0.8 },
    { url: `${SITE_URL}/precios`, changeFrequency: 'monthly', priority: 0.8 },
    { url: `${SITE_URL}/preguntas-frecuentes`, changeFrequency: 'monthly', priority: 0.7 },
    { url: `${SITE_URL}/seguridad`, changeFrequency: 'monthly', priority: 0.6 },
    { url: `${SITE_URL}/desarrolladores`, changeFrequency: 'monthly', priority: 0.6 },
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
    { url: `${SITE_URL}/stockai-manual-es.pdf`, changeFrequency: 'monthly', priority: 0.5 },
    { url: `${SITE_URL}/stockai-manual-en.pdf`, changeFrequency: 'monthly', priority: 0.4 },
  ]
}
