'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import {
 AlertTriangle, Clock, TrendingUp, TrendingDown, Archive,
 RefreshCw, ArrowRight, BarChart2, Package,
} from 'lucide-react'
import { getMorningBriefing, getMorningNarrative } from '@/lib/api'
import type { MorningBriefing, BriefingRecommendation, InventoryStatusItem, MorningNarrative } from '@/lib/types'
import { useAutoSession } from '@/hooks/useAutoSession'
import SessionBar from '@/components/ui/SessionBar'
import { getUser } from '@/lib/auth'
import Spinner from '@/components/ui/Spinner'
import NarrativeCard from '@/components/ui/NarrativeCard'
import { useBusinessProfile, PROFILE_TERMS } from '@/contexts/BusinessProfileContext'
import { Store, Truck, Cog } from 'lucide-react'

// ── Colour palette ────────────────────────────────────────────────────────────
const C = {
 bg: 'var(--bg)',
 surface: 'var(--surface)',
 card: 'var(--surface-2)',
 border: 'var(--border)',
 text: 'var(--text)',
 muted: 'var(--muted)',
 dim: 'var(--dim)',
 red: '#ef4444',
 amber: '#f59e0b',
 green: '#22c55e',
 blue: '#3b82f6',
 indigo: '#818cf8',
}

// ── Formatters ────────────────────────────────────────────────────────────────
function fmtM(n: number) {
 if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
 if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`
 return `$${n.toFixed(0)}`
}

function fmtPct(n: number | null) {
 if (n == null) return '—'
 return `${(n * 100).toFixed(1)}%`
}

function timeSince(date: Date) {
 const mins = Math.floor((Date.now() - date.getTime()) / 60000)
 if (mins < 1) return 'hace un momento'
 if (mins === 1) return 'hace 1 minuto'
 return `hace ${mins} minutos`
}

function formatDateES(isoDate: string) {
 const d = new Date(isoDate + 'T12:00:00')
 return d.toLocaleDateString('es', {
 weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
 })
}

// ── Subcomponents ─────────────────────────────────────────────────────────────

function RecIcon({ rec_type }: { rec_type: BriefingRecommendation['rec_type'] }) {
 switch (rec_type) {
 case 'STOCKOUT_RISK': return <AlertTriangle size={16} color={C.red} />
 case 'REORDER_SOON': return <Clock size={16} color={C.amber} />
 case 'DEMAND_UP': return <TrendingUp size={16} color={C.green} />
 case 'DEMAND_DOWN': return <TrendingDown size={16} color={C.red} />
 case 'OVERSTOCK': return <Archive size={16} color={C.blue} />
 }
}

interface RiskCardProps {
 item: InventoryStatusItem
 level: 'risk' | 'warning'
}

function RiskCard({ item, level }: RiskCardProps) {
 const isRisk = level === 'risk'
 const accent = isRisk ? C.red : C.amber
 const bgColor = isRisk ? 'rgba(239,68,68,0.05)' : 'rgba(245,158,11,0.05)'
 const label = isRisk ? 'PEDIR YA' : 'PEDIR PRONTO'
 const dot = ''

 return (
 <div style={{
 background: bgColor,
 border: `1px solid ${accent}33`,
 borderRadius: 10,
 padding: '14px 16px',
 marginBottom: 10,
 }}>
 <div style={{
 display: 'flex', alignItems: 'center', justifyContent: 'space-between',
 marginBottom: 10,
 }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
 <span style={{ fontSize: 13 }}>{dot}</span>
 <span style={{
 fontSize: 11, fontWeight: 700, color: accent,
 textTransform: 'uppercase', letterSpacing: '0.06em',
 }}>
 {label}
 </span>
 <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>
 {item.sku}
 </span>
 {item.display_name && (
 <span style={{ fontSize: 13, color: C.muted }}>— {item.display_name}</span>
 )}
 </div>
 {item.proveedor && (
 <span style={{ fontSize: 12, color: C.dim }}>{item.proveedor}</span>
 )}
 </div>

 <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
 <div>
 {item.dias_cobertura != null && (
 <p style={{ fontSize: 13, color: C.text, margin: '0 0 4px' }}>
 Te quedan <strong>{Math.round(item.dias_cobertura)}</strong> días de stock.
 </p>
 )}
 <p style={{ fontSize: 13, color: C.muted, margin: '0 0 4px' }}>
 Tu proveedor tarda <strong>{item.lead_time_dias}</strong> días.
 </p>
 {item.cantidad_recomendada != null && (
 <p style={{ fontSize: 13, color: C.text, margin: 0 }}>
 Pedir: <strong>{item.cantidad_recomendada.toLocaleString('es')} unidades</strong>
 </p>
 )}
 </div>
 <Link
 href="/inventory"
 style={{
 fontSize: 12, color: C.indigo, textDecoration: 'none',
 display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0,
 }}
 >
 Ver en inventario <ArrowRight size={12} />
 </Link>
 </div>
 </div>
 )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function HoyPage() {
 const { sessionId, setSessionId, currentSession, completedSessions, loading: sessionsLoading } = useAutoSession()
 const [briefing, setBriefing] = useState<MorningBriefing | null>(null)
 const [loading, setLoading] = useState(false)
 const [error, setError] = useState<string | null>(null)
 const [loadedAt, setLoadedAt] = useState<Date | null>(null)
 const [narrative, setNarrative] = useState<MorningNarrative | null>(null)
 const [loadingNarrative, setLoadingNarrative] = useState(false)

 const user = getUser()
 const { profile, isLoaded: profileLoaded, terms } = useBusinessProfile()

 // Load briefing when session changes
 const load = useCallback(async (sid: string) => {
 if (!sid) return
 setLoading(true)
 setError(null)
 try {
 const data = await getMorningBriefing(sid)
 setBriefing(data)
 setLoadedAt(new Date())
 } catch (e: unknown) {
 setError(e instanceof Error ? e.message : 'Error cargando el briefing')
 } finally {
 setLoading(false)
 }
 }, [])

 useEffect(() => {
 if (sessionId) load(sessionId)
 }, [sessionId, load])

 // Generates a fallback narrative from briefing data — no API required
 function buildFallbackNarrative(b: MorningBriefing): MorningNarrative {
   const k = b.kpis
   const urgency = k.pedir_ya > 0 ? 'critical' : k.pedir_pronto > 0 ? 'warning' : 'ok'
   const parts: string[] = []
   if (k.pedir_ya > 0) {
     const names = b.risks.slice(0,3).map(r => r.display_name || r.sku).join(', ')
     parts.push(`${k.pedir_ya} producto${k.pedir_ya > 1 ? 's' : ''} en riesgo inmediato de quiebre: ${names}.`)
   }
   if (k.pedir_pronto > 0)
     parts.push(`${k.pedir_pronto} producto${k.pedir_pronto > 1 ? 's' : ''} necesitan pedido esta semana.`)
   if (k.sobrestock > 0 && k.capital_in_overstock > 0)
     parts.push(`$${(k.capital_in_overstock/1_000_000).toFixed(1)}M inmovilizados en sobrestock — considera pausar esos pedidos.`)
   if (k.pedir_ya === 0 && k.pedir_pronto === 0)
     parts.push('El inventario está bajo control hoy. No hay acciones urgentes pendientes.')
   if (k.avg_accuracy)
     parts.push(`Precisión del forecast: ${(k.avg_accuracy*100).toFixed(1)}%.`)
   return { narrative: parts.join(' '), key_points: [], urgency, fallback: true }
 }

 useEffect(() => {
   if (!briefing || !sessionId) return
   setLoadingNarrative(true)
   // 8-second timeout — always shows something, with or without Anthropic key
   const timeout = setTimeout(() => {
     setNarrative(buildFallbackNarrative(briefing))
     setLoadingNarrative(false)
   }, 8000)
   getMorningNarrative(sessionId, profile || 'distributor')
     .then(data => { clearTimeout(timeout); setNarrative(data) })
     .catch(() => { clearTimeout(timeout); setNarrative(buildFallbackNarrative(briefing)) })
     .finally(() => setLoadingNarrative(false))
   return () => clearTimeout(timeout)
 }, [briefing?.session_id, profile])

 // ── No session state ──────────────────────────────────────────────────────
 if (!loading && !sessionsLoading && !sessionId && completedSessions.length === 0) {
 return (
 <div style={{
 display: 'flex', flexDirection: 'column', alignItems: 'center',
 justifyContent: 'center', flex: 1, gap: 20, padding: 40,
 color: C.muted, textAlign: 'center',
 }}>
 <BarChart2 size={40} color={C.dim} style={{ opacity: 0.4 }} />
 <p style={{ fontSize: 16, color: C.text, margin: 0 }}>
 Selecciona una sesión entrenada para ver el briefing diario.
 </p>
 <div style={{ display: 'flex', gap: 12 }}>
 <Link
 href="/quick-start"
 style={{
 padding: '10px 20px', background: C.indigo, color: '#fff',
 borderRadius: 8, textDecoration: 'none', fontSize: 14, fontWeight: 600,
 }}
 >
 Ir a Inicio Rápido
 </Link>
 <Link
 href="/inventory"
 style={{
 padding: '10px 20px', background: C.surface, color: C.text,
 border: `1px solid ${C.border}`, borderRadius: 8,
 textDecoration: 'none', fontSize: 14, fontWeight: 600,
 }}
 >
 Ir a Inventario
 </Link>
 </div>
 </div>
 )
 }

 const kpis = briefing?.kpis

 // ── Accuracy colour ───────────────────────────────────────────────────────
 function accuracyColor(v: number | null | undefined): string {
 if (v == null) return C.muted
 if (v >= 0.85) return C.green
 if (v >= 0.70) return C.amber
 return C.red
 }

 // ── Health banner ─────────────────────────────────────────────────────────
 function renderBanner() {
 if (!kpis) return null
 let bg: string, border: string, msg: string
 if (kpis.pedir_ya > 0) {
 bg = 'rgba(239,68,68,0.08)'
 border = C.red
 msg = `Atención requerida hoy — hay ${kpis.pedir_ya} producto(s) en riesgo inmediato de quedarse sin stock.`
 } else if (kpis.pedir_pronto > 0) {
 bg = 'rgba(245,158,11,0.08)'
 border = C.amber
 msg = `Situación manejable — ${kpis.pedir_pronto} producto(s) necesitan pedido esta semana.`
 } else {
 bg = 'rgba(34,197,94,0.08)'
 border = C.green
 msg = 'El inventario está bajo control. No hay acciones urgentes hoy.'
 }
 return (
 <div style={{
 background: bg, border: `1px solid ${border}`,
 borderRadius: 10, padding: '14px 18px', marginBottom: 24,
 fontSize: 14, color: C.text, lineHeight: 1.5,
 }}>
 {msg}
 </div>
 )
 }

 return (
 <div style={{ background: C.bg, minHeight: '100vh', padding: '32px 40px', position: 'relative' }}>

 {/* ── Profile first-run banner ── */}
 {profileLoaded && !profile && (
 <div style={{
 display: 'flex', alignItems: 'center', gap: 14,
 padding: '14px 20px', marginBottom: 24, borderRadius: 10,
 background: 'rgba(129,140,248,0.06)', border: '1px solid rgba(129,140,248,0.25)',
 }}>
 <div style={{ width: 22, height: 22, borderRadius: 6, background: 'rgba(129,140,248,0.2)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><AlertTriangle size={12} color="#818cf8" /></div>
 <div style={{ flex: 1 }}>
 <div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>Configura el tipo de empresa para personalizar la experiencia</div>
 <div style={{ fontSize: 12, color: C.dim, marginTop: 2 }}>
 Faro adapta los módulos, terminología y prioridades según si eres retailer, distribuidor o fabricante.
 </div>
 </div>
 <Link href="/perfil" style={{
 all: 'unset' as any, cursor: 'pointer',
 display: 'flex', alignItems: 'center', gap: 6,
 padding: '8px 16px', borderRadius: 8, fontSize: 12, fontWeight: 700,
 background: 'linear-gradient(135deg, #6366f1, #818cf8)', color: '#fff',
 flexShrink: 0,
 }}>
 Configurar perfil →
 </Link>
 </div>
 )}

 {/* ── Profile tagline (when set) ── */}
 {profileLoaded && profile && (
 <div style={{ marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
 <span style={{
 fontSize: 10, fontWeight: 700, color: C.indigo,
 padding: '2px 8px', borderRadius: 20,
 background: 'rgba(129,140,248,0.1)', border: '1px solid rgba(129,140,248,0.2)',
 textTransform: 'uppercase' as const, letterSpacing: '0.07em',
 }}>
 {terms.profile_label}
 </span>
 <span style={{ fontSize: 11, color: C.dim }}>{terms.hero_tagline}</span>
 </div>
 )}

 {/* ── Header ── */}
 <div style={{
 display: 'flex', alignItems: 'flex-start',
 justifyContent: 'space-between', marginBottom: 28,
 }}>
 <div>
 <h1 style={{ fontSize: 22, fontWeight: 700, color: C.text, margin: '0 0 6px' }}>
 Buenos días{user?.full_name ? `, ${user.full_name.split(' ')[0]}` : ''}.
 </h1>
 {briefing ? (
 <p style={{ fontSize: 13, color: C.dim, margin: 0 }}>
 Hoy es {formatDateES(briefing.date)}.{' '}
 Sesión activa: <span style={{ color: C.muted }}>{briefing.session_name}</span>
 </p>
 ) : (
 <p style={{ fontSize: 13, color: C.dim, margin: 0 }}>
 Cargando datos del día…
 </p>
 )}
 </div>

 {/* Session selector */}
 <SessionBar
 currentSession={currentSession}
 completedSessions={completedSessions}
 sessionId={sessionId}
 onSelect={setSessionId}
 loading={sessionsLoading}
 onRefresh={() => load(sessionId)}
 />
 </div>

 {/* ── Loading state ── */}
 {loading && (
 <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
 <Spinner size={32} color={C.indigo} />
 </div>
 )}

 {/* ── Error state ── */}
 {!loading && error && (
 <div style={{
 background: 'rgba(239,68,68,0.08)', border: `1px solid ${C.red}33`,
 borderRadius: 10, padding: '16px 20px', marginBottom: 24,
 display: 'flex', alignItems: 'center', justifyContent: 'space-between',
 }}>
 <span style={{ fontSize: 14, color: C.text }}>{error}</span>
 <button
 onClick={() => load(sessionId)}
 style={{
 display: 'flex', alignItems: 'center', gap: 6,
 padding: '6px 14px', background: C.surface,
 border: `1px solid ${C.border}`, borderRadius: 6,
 cursor: 'pointer', fontSize: 13, color: C.text,
 }}
 >
 <RefreshCw size={13} /> Reintentar
 </button>
 </div>
 )}

 {/* ── Main content (only if briefing loaded) ── */}
 {!loading && briefing && (
 <>
 {/* B. Health banner */}
 {renderBanner()}

 {/* C. No-data empty state */}
 {!briefing.has_data ? (
 <div style={{
 display: 'flex', flexDirection: 'column', alignItems: 'center',
 justifyContent: 'center', padding: '60px 0', gap: 16, textAlign: 'center',
 }}>
 <Package size={36} color={C.dim} style={{ opacity: 0.4 }} />
 <p style={{ fontSize: 16, color: C.text, margin: 0 }}>
 No hay datos de inventario para esta sesión.
 </p>
 <p style={{ fontSize: 13, color: C.muted, margin: 0 }}>
 Agrega stock en la sección de inventario para empezar a ver el briefing diario.
 </p>
 <Link
 href="/inventory"
 style={{
 padding: '10px 20px', background: C.indigo, color: '#fff',
 borderRadius: 8, textDecoration: 'none', fontSize: 14, fontWeight: 600,
 }}
 >
 Ir a Inventario
 </Link>
 </div>
 ) : (
 <>
 {/* C. KPI row */}
 <div style={{
 display: 'flex', gap: 12, marginBottom: 28, flexWrap: 'wrap',
 }}>
 {/* Total SKUs */}
 <KpiCard
 label="Total SKUs monitoreados"
 value={String(kpis!.total_skus)}
 color={C.text}
 />
 {/* Riesgo hoy */}
 <KpiCard
 label="Riesgo hoy"
 value={String(kpis!.pedir_ya)}
 color={kpis!.pedir_ya > 0 ? C.red : C.text}
 />
 {/* Esta semana */}
 <KpiCard
 label="Esta semana"
 value={String(kpis!.pedir_pronto)}
 color={kpis!.pedir_pronto > 0 ? C.amber : C.text}
 />
 {/* Precisión */}
 <KpiCard
 label="Precisión promedio"
 value={fmtPct(kpis!.avg_accuracy)}
 color={accuracyColor(kpis!.avg_accuracy)}
 />
 {/* Valor en bodega */}
 <KpiCard
 label="Valor en bodega"
 value={fmtM(kpis!.total_inventory_value)}
 color={C.text}
 />
 </div>

 {/* ── AI Narrative ── */}
 {(narrative || loadingNarrative) && (
 <div style={{ marginBottom: 28 }}>
 <NarrativeCard
 title="Resumen ejecutivo del día"
 narrative={narrative?.narrative ?? null}
 keyPoints={narrative?.key_points ?? []}
 urgency={narrative?.urgency ?? 'ok'}
 loading={loadingNarrative}
 fallback={narrative?.fallback ?? false}
 analytistLink="/analyst"
 onRefresh={() => {
 if (!sessionId || !profile) return
 setLoadingNarrative(true)
 getMorningNarrative(sessionId, profile || 'distributor')
 .then(setNarrative).catch(() => {}).finally(() => setLoadingNarrative(false))
 }}
 />
 </div>
 )}

 {/* D. Riesgos prioritarios */}
 {(briefing.risks.length > 0 || briefing.warnings.length > 0) && (
 <section style={{ marginBottom: 28 }}>
 <div style={{
 display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14,
 }}>
 <h2 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: 0 }}>
 Riesgos prioritarios
 </h2>
 <span style={{
 fontSize: 12, fontWeight: 600,
 background: C.red, color: '#fff',
 borderRadius: 99, padding: '2px 8px',
 }}>
 {briefing.risks.length + briefing.warnings.length}
 </span>
 </div>

 {briefing.risks.slice(0, 5).map(item => (
 <RiskCard key={item.sku} item={item} level="risk" />
 ))}
 {briefing.warnings.slice(0, 5).map(item => (
 <RiskCard key={item.sku} item={item} level="warning" />
 ))}

 <Link
 href="/inventory"
 style={{
 display: 'inline-flex', alignItems: 'center', gap: 6,
 fontSize: 13, color: C.indigo, textDecoration: 'none',
 marginTop: 6,
 }}
 >
 Ver todos en Inventario <ArrowRight size={13} />
 </Link>
 </section>
 )}

 {/* E. Cambios en demanda */}
 {briefing.demand_changes.length > 0 && (
 <section style={{ marginBottom: 28 }}>
 <h2 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: '0 0 14px' }}>
 Cambios en demanda
 </h2>
 <div style={{
 background: C.surface, border: `1px solid ${C.border}`,
 borderRadius: 10, overflow: 'hidden',
 }}>
 {briefing.demand_changes.map((item, idx) => {
 const pct = item.demand_trend_pct
 const up = pct > 0
 const pctColor = up ? C.green : C.red
 const sign = up ? '+' : ''
 return (
 <div
 key={item.sku}
 style={{
 display: 'flex', alignItems: 'center', gap: 14,
 padding: '12px 16px',
 borderBottom: idx < briefing.demand_changes.length - 1
 ? `1px solid ${C.border}` : 'none',
 }}
 >
 {up
 ? <TrendingUp size={16} color={C.green} />
 : <TrendingDown size={16} color={C.red} />
 }
 <span style={{
 fontSize: 13, fontWeight: 700, color: pctColor,
 minWidth: 52,
 }}>
 {sign}{pct.toFixed(0)}%
 </span>
 <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>
 {item.sku}
 </span>
 {item.display_name && (
 <span style={{ fontSize: 13, color: C.muted }}>
 — {item.display_name}
 </span>
 )}
 <span style={{ fontSize: 12, color: C.dim, marginLeft: 'auto' }}>
 {up
 ? 'Demanda real corriendo por encima del pronóstico'
 : 'Demanda real por debajo del pronóstico'
 }
 </span>
 </div>
 )
 })}
 </div>
 </section>
 )}

 {/* F. Recomendaciones */}
 {briefing.recommendations.length > 0 && (
 <section style={{ marginBottom: 28 }}>
 <h2 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: '0 0 14px' }}>
 Recomendaciones del sistema
 </h2>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
 {briefing.recommendations.slice(0, 8).map((rec, idx) => (
 <div
 key={idx}
 style={{
 background: C.surface, border: `1px solid ${C.border}`,
 borderRadius: 10, padding: '14px 16px',
 }}
 >
 <div style={{
 display: 'flex', alignItems: 'flex-start', gap: 10,
 marginBottom: 8,
 }}>
 <div style={{ flexShrink: 0, marginTop: 1 }}>
 <RecIcon rec_type={rec.rec_type} />
 </div>
 <span style={{ fontSize: 13, color: C.text, lineHeight: 1.5 }}>
 {rec.text}
 </span>
 </div>
 <p style={{ fontSize: 12, color: C.muted, margin: 0, paddingLeft: 26 }}>
 Acción sugerida: {rec.action}
 </p>
 </div>
 ))}
 </div>
 </section>
 )}

 {/* G. Oportunidades de capital */}
 {briefing.overstocked.length > 0 && kpis!.capital_in_overstock > 0 && (
 <section style={{ marginBottom: 28 }}>
 <h2 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: '0 0 14px' }}>
 Oportunidades de capital
 </h2>
 <div style={{
 background: 'rgba(59,130,246,0.05)', border: `1px solid ${C.blue}33`,
 borderRadius: 10, padding: '16px 20px',
 }}>
 <p style={{ fontSize: 14, color: C.text, margin: '0 0 16px' }}>
 Tienes {fmtM(kpis!.capital_in_overstock)} en inventario con cobertura excesiva.
 Pausar pedidos en los siguientes productos puede liberar capital:
 </p>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
 {briefing.overstocked.slice(0, 3).map(item => (
 <div
 key={item.sku}
 style={{
 display: 'flex', alignItems: 'center', justifyContent: 'space-between',
 background: C.surface, border: `1px solid ${C.border}`,
 borderRadius: 8, padding: '10px 14px',
 }}
 >
 <div>
 <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>
 {item.sku}
 </span>
 {item.display_name && (
 <span style={{ fontSize: 13, color: C.muted }}> — {item.display_name}</span>
 )}
 </div>
 <div style={{ display: 'flex', gap: 20, alignItems: 'center' }}>
 {item.dias_cobertura != null && (
 <span style={{ fontSize: 12, color: C.dim }}>
 {Math.round(item.dias_cobertura)} días de cobertura
 </span>
 )}
 {item.valor_inventario != null && (
 <span style={{ fontSize: 13, fontWeight: 600, color: C.blue }}>
 {fmtM(item.valor_inventario)}
 </span>
 )}
 </div>
 </div>
 ))}
 </div>
 </div>
 </section>
 )}
 </>
 )}

 {/* H. Footer */}
 <div style={{
 marginTop: 24, paddingTop: 20, borderTop: `1px solid ${C.border}`,
 display: 'flex', alignItems: 'center', justifyContent: 'space-between',
 flexWrap: 'wrap', gap: 12,
 }}>
 <div style={{ fontSize: 12, color: C.dim }}>
 {loadedAt && <>Última actualización: {timeSince(loadedAt)}</>}
 {briefing.session_name && (
 <> &nbsp;|&nbsp; Sesión: {briefing.session_name}</>
 )}
 {kpis?.avg_accuracy != null && (
 <> &nbsp;|&nbsp; Precisión del modelo: {fmtPct(kpis.avg_accuracy)}</>
 )}
 </div>
 <button
 onClick={() => load(sessionId)}
 style={{
 display: 'flex', alignItems: 'center', gap: 6,
 padding: '7px 16px', background: C.surface,
 border: `1px solid ${C.border}`, borderRadius: 7,
 cursor: 'pointer', fontSize: 13, color: C.text,
 }}
 >
 <RefreshCw size={13} /> Actualizar datos
 </button>
 </div>
 </>
 )}
 </div>
 )
}

// ── KPI card helper ───────────────────────────────────────────────────────────
function KpiCard({ label, value, color }: { label: string; value: string; color: string }) {
 return (
 <div style={{
 flex: '1 1 160px',
 background: 'var(--surface)',
 border: '1px solid var(--border)',
 borderRadius: 10,
 padding: '14px 18px',
 }}>
 <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 6, fontWeight: 500 }}>
 {label}
 </div>
 <div style={{ fontSize: 22, fontWeight: 700, color }}>
 {value}
 </div>
 </div>
 )
}
