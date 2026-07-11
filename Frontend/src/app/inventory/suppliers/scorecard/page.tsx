'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { getSupplierScorecard } from '@/lib/api'
import type { SupplierScorecardRow } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import { BarChart3, ArrowLeft, AlertTriangle, Truck } from 'lucide-react'

// ── Palette ───────────────────────────────────────────────────────────────────
const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  red: '#ef4444', amber: '#f59e0b', green: '#22c55e', indigo: '#818cf8',
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('es', { day: 'numeric', month: 'short', year: 'numeric' })
}

function fmtCurrency(n: number): string {
  return '$' + n.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

function fmtPct(n: number | null): string {
  return n == null ? '—' : `${Math.round(n * 100)}%`
}

function fmtRange(min: number | null, max: number | null): string {
  if (min == null || max == null) return '—'
  return min === max ? `${min}d` : `${min}–${max}d`
}

// ── Table ─────────────────────────────────────────────────────────────────────
function ScorecardTable({ rows }: { rows: SupplierScorecardRow[] }) {
  const columns = [
    'Proveedor', 'Recepciones', 'Lead time real', 'Declarado',
    '% A tiempo', '% Fill rate', 'Valor comprado', 'Última recepción',
  ]

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr style={{ background: C.card }}>
            {columns.map(h => (
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
          {rows.map((row, idx) => {
            const onTimeColor = row.on_time_rate == null
              ? C.dim
              : row.on_time_rate >= 0.7 ? C.green : row.on_time_rate >= 0.4 ? C.amber : C.red
            return (
              <tr key={row.proveedor} style={{
                background: idx % 2 === 0 ? C.surface : C.card,
                borderBottom: `1px solid ${C.border}`,
              }}>
                <td style={{ padding: '11px 14px', color: C.text, fontWeight: 600 }}>{row.proveedor}</td>
                <td style={{ padding: '11px 14px', color: C.text }}>{row.n_recepciones}</td>
                <td style={{ padding: '11px 14px', color: C.text, fontFamily: 'monospace' }}>
                  {fmtRange(row.lead_time_real_min, row.lead_time_real_max)}
                </td>
                <td style={{ padding: '11px 14px', color: C.muted, fontFamily: 'monospace' }}>
                  {row.lead_time_declarado != null ? `${row.lead_time_declarado}d` : '—'}
                </td>
                <td style={{ padding: '11px 14px', color: onTimeColor, fontWeight: 700 }}>
                  {fmtPct(row.on_time_rate)}
                </td>
                <td style={{ padding: '11px 14px', color: C.text }}>{fmtPct(row.fill_rate)}</td>
                <td style={{ padding: '11px 14px', color: C.green, fontFamily: 'monospace', fontWeight: 600 }}>
                  {fmtCurrency(row.valor_comprado)}
                </td>
                <td style={{ padding: '11px 14px', color: C.dim }}>{fmtDate(row.ultima_recepcion)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function SupplierScorecardPage() {
  const [rows,    setRows]    = useState<SupplierScorecardRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try { setRows(await getSupplierScorecard()) }
    catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error cargando el scorecard') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

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
            <BarChart3 size={17} color="#fff" strokeWidth={2.5} />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>
              Scorecard de proveedores
            </h1>
            <p style={{ margin: 0, fontSize: 11, color: C.dim }}>
              Lead time real, cumplimiento y fill rate — calculado de tus recepciones registradas
            </p>
          </div>
        </div>
        <Link href="/inventory/suppliers" style={{
          display: 'flex', alignItems: 'center', gap: 6,
          fontSize: 12, color: C.dim, textDecoration: 'none',
          padding: '7px 12px', border: `1px solid ${C.border}`, borderRadius: 8,
        }}>
          <ArrowLeft size={12} /> Volver a Proveedores
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
      ) : rows.length > 0 ? (
        <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>
          <ScorecardTable rows={rows} />
        </div>
      ) : (
        <div style={{
          padding: '40px 24px', textAlign: 'center', borderRadius: 12,
          background: C.card, border: `1px solid ${C.border}`,
        }}>
          <Truck size={32} color={C.dim} style={{ margin: '0 auto 12px', opacity: 0.4 }} />
          <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginBottom: 6 }}>
            Aún no hay recepciones registradas
          </div>
          <div style={{ fontSize: 12, color: C.dim, marginBottom: 16 }}>
            Registra la llegada de una orden de compra desde el historial de Impacto para que Faro empiece a aprender el desempeño de tus proveedores.
          </div>
          <Link href="/inventory/suppliers" style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '8px 16px', borderRadius: 8, fontSize: 12, fontWeight: 600,
            background: 'rgba(129,140,248,0.1)', border: '1px solid rgba(129,140,248,0.3)',
            color: C.indigo, textDecoration: 'none',
          }}>
            <Truck size={13} /> Ir a Proveedores
          </Link>
        </div>
      )}
    </div>
  )
}
