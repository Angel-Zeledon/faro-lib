import type { MetadataRoute } from 'next'
import { SITE_URL } from '@/lib/siteUrls'

// Served as /sitemap.xml. The landing, its four pages and the two manuals are
// the whole public surface; robots.ts keeps everything else out.
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: `${SITE_URL}/`, changeFrequency: 'weekly', priority: 1 },
    { url: `${SITE_URL}/como-funciona`, changeFrequency: 'monthly', priority: 0.8 },
    { url: `${SITE_URL}/precios`, changeFrequency: 'monthly', priority: 0.8 },
    { url: `${SITE_URL}/preguntas-frecuentes`, changeFrequency: 'monthly', priority: 0.7 },
    { url: `${SITE_URL}/seguridad`, changeFrequency: 'monthly', priority: 0.6 },
    { url: `${SITE_URL}/stockai-manual-es.pdf`, changeFrequency: 'monthly', priority: 0.5 },
    { url: `${SITE_URL}/stockai-manual-en.pdf`, changeFrequency: 'monthly', priority: 0.4 },
  ]
}
