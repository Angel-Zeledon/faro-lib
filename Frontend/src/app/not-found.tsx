'use client'
import Link from 'next/link'
import Button from '@/components/ui/Button'
import { SearchX, ArrowLeft } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'

export default function NotFound() {
  const { t } = useLanguage()
  // `t` echoes the key back when the catalog has no entry — rendering
  // "notfound.title" at the user is worse than the English sentence.
  const copy = (key: string, fallback: string) => {
    const rendered = t(key)
    return rendered === key ? fallback : rendered
  }

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', minHeight: '60vh', gap: 16, padding: 40,
    }}>
      <div style={{
        width: 48, height: 48, borderRadius: 12,
        background: 'rgba(129,140,248,0.1)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <SearchX size={22} color="#818cf8" />
      </div>
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>
          {copy('notfound.title', 'Page not found')}
        </div>
        <div style={{ fontSize: 13, color: 'var(--dim)', maxWidth: 400, lineHeight: 1.5 }}>
          {copy('notfound.body', 'The page you are looking for does not exist or was moved.')}
        </div>
      </div>
      <Link href="/hoy" style={{ textDecoration: 'none' }}>
        <Button variant="primary" icon={<ArrowLeft size={13} />}>
          {copy('notfound.back', 'Back to the dashboard')}
        </Button>
      </Link>
    </div>
  )
}
