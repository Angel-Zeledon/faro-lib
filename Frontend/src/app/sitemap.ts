import type { MetadataRoute } from 'next'
import { SITE_URL } from '@/lib/siteUrls'

// Served as /sitemap.xml. The landing and the two manuals are the whole public
// surface; robots.ts keeps everything else out.
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: `${SITE_URL}/`, changeFrequency: 'weekly', priority: 1 },
    { url: `${SITE_URL}/stockai-manual-es.pdf`, changeFrequency: 'monthly', priority: 0.5 },
    { url: `${SITE_URL}/stockai-manual-en.pdf`, changeFrequency: 'monthly', priority: 0.4 },
  ]
}
