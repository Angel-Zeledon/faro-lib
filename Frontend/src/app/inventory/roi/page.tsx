'use client'
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { getInventoryROI, getPOHistory } from '@/lib/api'
import type { InventoryROISummary, POLogEntry } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import { TrendingUp, ArrowLeft, Package, ShoppingCart, Calendar, AlertTriangle } from 'lucide-react'

// ── Palette ───────────────────────────────────────────────────────────────────
const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  red: '#ef4444', amber: '#f59e0b', green: '#22c55e', indigo: '#818cf8',
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('es', { day: 'numeric', month: 'long', year: 'numeric' })
}

function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString('es', {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtCurrency(n: number): string {
  return '$' + n.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

function fmtUnits(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

// ── Sub-components ────────────────────────────────────────────────────────────

function HeroCard({ roi }: { roi: InventoryROISummary }) {
  const hasValue = roi.estimated_value_protected > 0

  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 14, padding: '28px 32px',
      borderTop: `4px solid ${C.indigo}`,
    }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: C.indigo, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 20 }}>
        Tu historial con Faro
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 28 }}>
        {/* POs generated */}
        <div>
          <div style={{ fontSize: 48, fontWeight: 900, color: C.indigo, lineHeight: 1 }}>
            {roi.total_pos_generated}
          </div>
          <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginTop: 6 }}>
            {roi.total_pos_generated === 1 ? 'orden de compra generada' : 'órdenes de compra generadas'}
          </div>
          {roi.first_po_at && (
            <div style={{ fontSize: 12, color: C.dim, marginTop: 4 }}>
              desde {fmtDate(roi.first_po_at)}
              {roi.active_days > 0 && (
                <span style={{ marginLeft: 6, padding: '2px 8px', borderRadius: 20, background: 'rgba(129,140,248,0.1)', color: C.indigo, fontSize: 11 }}>
                  {roi.active_days} días activo
                </span>
              )}
            </div>
          )}
        </div>

        {/* SKUs protected */}
        <div>
          <div style={{ fontSize: 48, fontWeight: 900, color: C.red, lineHeight: 1 }}>
            {fmtUnits(roi.total_skus_protected)}
          </div>
          <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginTop: 6 }}>
            SKUs protegidos de stockout
          </div>
          <div style={{ fontSize: 12, color: C.dim, marginTop: 4 }}>
            en ordenes urgentes (Pedir YA)
          </div>
        </div>

        {/* Value or units */}
        <div>
          {hasValue ? (
            <>
              <div style={{ fontSize: 42, fontWeight: 900, color: C.green, lineHeight: 1 }}>
                {fmtCurrency(roi.estimated_value_protected)}
              </div>
              <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginTop: 6 }}>
                valor de inventario ordenado
              </div>
              <div style={{ fontSize: 12, color: C.dim, marginTop: 4 }}>
                en todas las ordenes de compra
              </div>
            </>
          ) : (
            <>
              <div style={{ fontSize: 48, fontWeight: 900, color: C.amber, lineHeight: 1 }}>
                {fmtUnits(roi.total_units_ordered)}
              </div>
              <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginTop: 6 }}>
                unidades totales ordenadas
              </div>
              <div style={{ fontSize: 12, color: C.dim, marginTop: 4 }}>
                Agrega costo unitario a tus SKUs para ver el valor en dinero
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function MonthKPIs({ roi }: { roi: InventoryROISummary }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
      <div style={{
        background: C.surface, border: `1px solid ${C.border}`,
        borderRadius: 12, padding: '20px 22px',
        borderTop: `3px solid ${C.indigo}`,
      }}>
        <div style={{ fontSize: 32, fontWeight: 800, color: C.indigo }}>{roi.pos_this_month}</div>
        <div style={{ fontSize: 13, fontWeight: 600, color: C.text, marginTop: 4 }}>
          {roi.pos_this_month === 1 ? 'orden generada' : 'ordenes generadas'} este mes
        </div>
        <div style={{ fontSize: 11, color: C.dim, marginTop: 2 }}>Mes actual</div>
      </div>
      <div style={{
        background: C.surface, border: `1px solid ${C.border}`,
        borderRadius: 12, padding: '20px 22px',
        borderTop: `3px solid rgba(129,140,248,0.3)`,
      }}>
        <div style={{ fontSize: 32, fontWeight: 800, color: C.muted }}>{roi.pos_last_month}</div>
        <div style={{ fontSize: 13, fontWeight: 600, color: C.text, marginTop: 4 }}>
          {roi.pos_last_month === 1 ? 'orden generada' : 'ordenes generadas'} el mes pasado
        </div>
        <div style={{ fontSize: 11, color: C.dim, marginTop: 2 }}>Mes anterior</div>
      </div>
    </div>
  )
}

function POHistoryTable({ entries }: { entries: POLogEntry[] }) {
  if (entries.length === 0) {
    return (
      <div style={{ padding: '40px 24px', textAlign: 'center', color: C.dim, fontSize: 13 }}>
        Aun no has generado ninguna orden de compra desde Faro.
        <br />
        <span style={{ fontSize: 12, opacity: 0.7, marginTop: 6, display: 'block' }}>
          Ve a Inventario y usa "Exportar OC" para registrar tu primera orden.
        </span>
      </div>
    )
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr style={{ background: C.card }}>
            {['Fecha y hora', 'SKUs en la orden', 'Urgentes', 'Proximos', 'Unidades totales', 'Valor total'].map(h => (
              <th key={h} style={{
                padding: '9px 14px', textAlign: 'left', whiteSpace: 'nowrap',
                color: C.dim, fontWeight: 600, fontSize: 10,
                borderBottom: `1px solid ${C.border}`,
                textTransform: 'uppercase', letterSpacing: '0.06em',
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, idx) => (
            <tr key={entry.id} style={{
              background: idx % 2 === 0 ? C.surface : C.card,
              borderBottom: `1px solid ${C.border}`,
            }}>
              <td style={{ padding: '11px 14px', color: C.text, fontVariantNumeric: 'tabular-nums' }}>
                {fmtDateTime(entry.generated_at)}
              </td>
              <td style={{ padding: '11px 14px', fontWeight: 600, color: C.text }}>
                {entry.sku_count}
              </td>
              <td style={{ padding: '11px 14px' }}>
                {entry.skus_pedir_ya > 0
                  ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 9px', borderRadius: 20, background: 'rgba(239,68,68,0.1)', color: C.red, fontWeight: 700, fontSize: 11 }}>
                      {entry.skus_pedir_ya}
                    </span>
                  : <span style={{ color: C.dim }}>—</span>
                }
              </td>
              <td style={{ padding: '11px 14px' }}>
                {entry.skus_pedir_pronto > 0
                  ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 9px', borderRadius: 20, background: 'rgba(245,158,11,0.1)', color: C.amber, fontWeight: 700, fontSize: 11 }}>
                      {entry.skus_pedir_pronto}
                    </span>
                  : <span style={{ color: C.dim }}>—</span>
                }
              </td>
              <td style={{ padding: '11px 14px', color: C.muted, fontFamily: 'monospace' }}>
                {fmtUnits(entry.total_units)}
              </td>
              <td style={{ padding: '11px 14px', color: entry.total_value ? C.green : C.dim, fontFamily: 'monospace', fontWeight: entry.total_value ? 600 : 400 }}>
                {entry.total_value != null ? fmtCurrency(entry.total_value) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function WhyItMattersCard() {
  return (
    <div style={{
      background: 'rgba(129,140,248,0.04)',
      border: `1px solid rgba(129,140,248,0.18)`,
      borderRadius: 12, padding: '22px 26px',
    }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: C.indigo, marginBottom: 12 }}>
        Por que esto importa
      </div>
      <p style={{ margin: 0, fontSize: 13, color: C.muted, lineHeight: 1.75 }}>
        Cada vez que exportas una orden de compra desde Faro, el sistema registra que
        productos tenia riesgo de stockout. Con el tiempo, esto construye un historial
        de decisiones que te permite ver el impacto real del sistema en tu operacion:
        cuantos quiebres de stock evitaste, que valor de inventario gestionaste de forma
        proactiva, y por cuanto tiempo has confiado en datos para tomar decisiones.
      </p>
      <p style={{ margin: '12px 0 0', fontSize: 12, color: C.dim, lineHeight: 1.65 }}>
        Este historial tambien es util para justificar la inversion en el sistema ante
        directivos o clientes — mostrando evidencia concreta de valor generado.
      </p>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function ROIPage() {
  const [roi,     setRoi]     = useState<InventoryROISummary | null>(null)
  const [history, setHistory] = useState<POLogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  useEffect(() => {
    async function load() {
      setLoading(true); setError(null)
      try {
        const [roiData, histData] = await Promise.all([
          getInventoryROI(),
          getPOHistory(20),
        ])
        setRoi(roiData)
        setHistory(histData)
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Error cargando datos de ROI')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, animation: 'fadeIn 0.3s ease-out' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 9,
            background: 'linear-gradient(135deg, #818cf8, #6366f1)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <TrendingUp size={17} color="#fff" strokeWidth={2.5} />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>
              Impacto & ROI
            </h1>
            <p style={{ margin: 0, fontSize: 11, color: C.dim }}>
              Historial acumulado de valor generado por Faro
            </p>
          </div>
        </div>
        <Link href="/inventory" style={{
          display: 'flex', alignItems: 'center', gap: 6,
          fontSize: 12, color: C.dim, textDecoration: 'none',
          padding: '7px 12px', border: `1px solid ${C.border}`, borderRadius: 8,
        }}>
          <ArrowLeft size={12} /> Volver a Inventario
        </Link>
      </div>

      {/* Error */}
      {error && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '10px 14px', borderRadius: 8,
          background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.2)',
          fontSize: 13, color: C.red,
        }}>
          <AlertTriangle size={13} style={{ flexShrink: 0 }} /> {error}
        </div>
      )}

      {loading ? (
        <div style={{ padding: 64, display: 'flex', justifyContent: 'center' }}>
          <Spinner />
        </div>
      ) : roi ? (
        <>
          {/* Section 1 — Hero counters */}
          <HeroCard roi={roi} />

          {/* Section 2 — Month comparison */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: C.dim, marginBottom: 10 }}>
              Actividad mensual
            </div>
            <MonthKPIs roi={roi} />
          </div>

          {/* Section 3 — PO history table */}
          <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>
            <div style={{
              padding: '14px 18px', borderBottom: `1px solid ${C.border}`,
              background: C.card, display: 'flex', alignItems: 'center', gap: 8,
            }}>
              <ShoppingCart size={14} color={C.indigo} />
              <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>
                Historial de ordenes de compra
              </span>
              <span style={{ fontSize: 11, color: C.dim, marginLeft: 'auto' }}>
                Ultimas {history.length} generadas
              </span>
            </div>
            <POHistoryTable entries={history} />
          </div>

          {/* Section 4 — Why it matters */}
          <WhyItMattersCard />

          {/* Empty state hint if no POs yet */}
          {roi.total_pos_generated === 0 && (
            <div style={{
              padding: '24px', borderRadius: 12, textAlign: 'center',
              background: C.card, border: `1px solid ${C.border}`,
            }}>
              <Package size={32} color={C.dim} style={{ margin: '0 auto 12px', opacity: 0.4 }} />
              <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginBottom: 6 }}>
                Aun no hay ordenes registradas
              </div>
              <div style={{ fontSize: 12, color: C.dim, marginBottom: 16 }}>
                Exporta tu primera orden de compra desde la pagina de Inventario para
                que Faro empiece a registrar tu impacto operativo.
              </div>
              <Link href="/inventory" style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '8px 16px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                background: 'rgba(129,140,248,0.1)', border: '1px solid rgba(129,140,248,0.3)',
                color: C.indigo, textDecoration: 'none',
              }}>
                <ShoppingCart size={13} /> Ir a Inventario
              </Link>
            </div>
          )}

          {/* Last updated note */}
          {roi.last_po_at && (
            <div style={{ fontSize: 11, color: C.dim, textAlign: 'center', paddingBottom: 4 }}>
              Ultima orden registrada el {fmtDateTime(roi.last_po_at)}
            </div>
          )}
        </>
      ) : null}
    </div>
  )
}
