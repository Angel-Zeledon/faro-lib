'use client'
import { useRouter } from 'next/navigation'
import { Store, Truck, Cog, CheckCircle2, ArrowRight } from 'lucide-react'
import { useBusinessProfile, PROFILE_TERMS } from '@/contexts/BusinessProfileContext'
import type { BusinessProfile } from '@/lib/types'

const PROFILES: {
 id: BusinessProfile
 icon: React.ElementType
 color: string
 bg: string
 title: string
 subtitle: string
 uses: string[]
}[] = [
 {
 id: 'retail', icon: Store, color: '#22c55e', bg: 'rgba(34,197,94,0.08)',
 title: 'Retail',
 subtitle: 'Tiendas · E-commerce · Supermercados',
 uses: [
 'Forecast de ventas por tienda y categoría',
 'Reposición automática de productos',
 'Alertas de faltantes y sobrestock',
 'Análisis de temporadas y promociones',
 ],
 },
 {
 id: 'distributor', icon: Truck, color: '#818cf8', bg: 'rgba(129,140,248,0.08)',
 title: 'Distribución / Mayoristas',
 subtitle: 'Distribuidores · Mayoristas · Logística',
 uses: [
 'Forecast de demanda por cliente o ruta',
 'Órdenes de compra recomendadas al proveedor',
 'Control de inventario y cobertura',
 'Gestión de lead times y proveedores',
 ],
 },
 {
 id: 'manufacturer', icon: Cog, color: '#f97316', bg: 'rgba(249,115,22,0.08)',
 title: 'Manufactura / Producción',
 subtitle: 'Fábricas · Productores · Plantas',
 uses: [
 'Forecast de demanda de productos terminados',
 'Lista de materiales (BOM) y explosión MRP',
 'Detección de escasez de materias primas',
 'Plan de producción y compras de insumos',
 ],
 },
]

export default function PerfilPage() {
 const router = useRouter()
 const { profile, setProfile } = useBusinessProfile()

 function select(p: BusinessProfile) {
 setProfile(p)
 router.push('/hoy')
 }

 return (
 <div style={{
 minHeight: '100vh', display: 'flex', flexDirection: 'column',
 alignItems: 'center', justifyContent: 'center',
 padding: '40px 24px',
 background: 'var(--bg)',
 }}>
 <div style={{ maxWidth: 760, width: '100%' }}>

 {/* Header */}
 <div style={{ textAlign: 'center', marginBottom: 48 }}>
 <div style={{
 width: 48, height: 48, borderRadius: 12, margin: '0 auto 16px',
 background: 'linear-gradient(135deg, #818cf8, #6366f1)',
 display: 'flex', alignItems: 'center', justifyContent: 'center',
 fontSize: 24, fontWeight: 900, color: '#fff',
 }}>F</div>
 <h1 style={{ fontSize: 26, fontWeight: 800, color: 'var(--text)', margin: '0 0 10px', letterSpacing: '-0.03em' }}>
 ¿Cómo describe tu empresa?
 </h1>
 <p style={{ fontSize: 15, color: 'var(--muted)', margin: 0, lineHeight: 1.6 }}>
 Faro adapta la experiencia, terminología y módulos visibles según tu tipo de operación.
 Puedes cambiarlo en cualquier momento.
 </p>
 </div>

 {/* Profile cards */}
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 32 }}>
 {PROFILES.map(({ id, icon: Icon, color, bg, title, subtitle, uses }) => {
 const isSelected = profile === id
 return (
 <button
 key={id}
 onClick={() => select(id)}
 style={{
 all: 'unset', cursor: 'pointer',
 display: 'flex', flexDirection: 'column',
 padding: '24px', borderRadius: 14,
 border: `2px solid ${isSelected ? color : 'var(--border)'}`,
 background: isSelected ? bg : 'var(--surface)',
 transition: 'all 0.2s', textAlign: 'left',
 boxShadow: isSelected ? `0 0 0 1px ${color}40` : 'none',
 }}
 onMouseEnter={e => {
 if (!isSelected) {
 (e.currentTarget as HTMLButtonElement).style.borderColor = color + '60'
 ;(e.currentTarget as HTMLButtonElement).style.background = bg
 }
 }}
 onMouseLeave={e => {
 if (!isSelected) {
 (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--border)'
 ;(e.currentTarget as HTMLButtonElement).style.background = 'var(--surface)'
 }
 }}
 >
 {/* Icon + check */}
 <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14 }}>
 <div style={{
 width: 44, height: 44, borderRadius: 11,
 background: color + '18', border: `1px solid ${color}30`,
 display: 'flex', alignItems: 'center', justifyContent: 'center',
 }}>
 <Icon size={22} color={color} strokeWidth={2} />
 </div>
 {isSelected && <CheckCircle2 size={18} color={color} />}
 </div>

 {/* Title */}
 <div style={{ fontSize: 15, fontWeight: 800, color: 'var(--text)', marginBottom: 4, letterSpacing: '-0.02em' }}>
 {title}
 </div>
 <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 16 }}>{subtitle}</div>

 {/* Features */}
 <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
 {uses.map(u => (
 <div key={u} style={{ display: 'flex', alignItems: 'flex-start', gap: 7 }}>
 <div style={{ width: 5, height: 5, borderRadius: '50%', background: color, flexShrink: 0, marginTop: 5 }} />
 <span style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.5 }}>{u}</span>
 </div>
 ))}
 </div>

 {/* CTA */}
 <div style={{
 marginTop: 20, display: 'flex', alignItems: 'center', gap: 5,
 fontSize: 12, fontWeight: 700, color: isSelected ? color : 'var(--dim)',
 transition: 'color 0.15s',
 }}>
 {isSelected ? 'Seleccionado' : 'Seleccionar'}
 <ArrowRight size={12} />
 </div>
 </button>
 )
 })}
 </div>

 {/* Bottom hint */}
 <div style={{ textAlign: 'center' }}>
 <p style={{ fontSize: 12, color: 'var(--dim)', margin: '0 0 16px' }}>
 Esta configuración se guarda en tu cuenta. Puedes cambiarla desde la barra lateral en cualquier momento.
 </p>
 {profile && (
 <button
 onClick={() => router.push('/hoy')}
 style={{
 all: 'unset', cursor: 'pointer',
 display: 'inline-flex', alignItems: 'center', gap: 8,
 padding: '10px 24px', borderRadius: 9,
 background: 'linear-gradient(135deg, #6366f1, #818cf8)',
 color: '#fff', fontSize: 14, fontWeight: 700,
 boxShadow: '0 4px 14px rgba(99,102,241,0.3)',
 }}
 >
 Continuar con {PROFILE_TERMS[profile].profile_label} <ArrowRight size={14} />
 </button>
 )}
 </div>
 </div>
 </div>
 )
}
