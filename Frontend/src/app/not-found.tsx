import Link from 'next/link'
import Button from '@/components/ui/Button'
import { SearchX, ArrowLeft } from 'lucide-react'

export default function NotFound() {
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
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>Página no encontrada</div>
        <div style={{ fontSize: 13, color: 'var(--dim)', maxWidth: 400, lineHeight: 1.5 }}>
          La página que buscas no existe o fue movida.
        </div>
      </div>
      <Link href="/dashboard" style={{ textDecoration: 'none' }}>
        <Button variant="primary" icon={<ArrowLeft size={13} />}>
          Volver al panel
        </Button>
      </Link>
    </div>
  )
}
