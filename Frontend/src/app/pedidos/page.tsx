'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { getPOHistory, getSupplierContactHealth, getSupplierLeadTimeAlerts } from '@/lib/api'
import type { POLogEntry, SupplierContactHealthRow, SupplierLeadTimeAlert } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import { POHistoryTable, ReceptionModal } from '@/components/po/POHistory'
import {
  SupplierContactHealthBanner, SupplierLeadTimeAlertBanner,
} from '@/components/suppliers/SupplierHealthBanners'
import { ClipboardList, AlertTriangle, ShoppingCart } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'

const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  red: '#ef4444', amber: '#f59e0b', green: '#22c55e', indigo: '#818cf8',
}

export default function PedidosPage() {
  const { t } = useLanguage()
  const [history,     setHistory]     = useState<POLogEntry[]>([])
  const [loading,     setLoading]     = useState(true)
  const [error,       setError]       = useState<string | null>(null)
  const [receivingPO, setReceivingPO] = useState<string | null>(null)
  const [contactHealth,  setContactHealth]  = useState<SupplierContactHealthRow[]>([])
  const [leadTimeAlerts, setLeadTimeAlerts] = useState<SupplierLeadTimeAlert[]>([])

  const load = useCallback(async (initial = false) => {
    if (initial) setLoading(true)
    setError(null)
    try { setHistory(await getPOHistory(50)) }
    catch (e: unknown) { setError(e instanceof Error ? e.message : t('pedidos.error_loading')) }
    finally { if (initial) setLoading(false) }
  }, [t])

  useEffect(() => { load(true) }, [load])

  // Supplier health signals (features 2.5 / 3.3) — server-computed.
  useEffect(() => {
    getSupplierContactHealth().then(setContactHealth).catch(() => {})
    getSupplierLeadTimeAlerts().then(setLeadTimeAlerts).catch(() => {})
  }, [])

  const pendingCount = history.filter(p =>
    ['pending', 'partial'].includes(p.reception_status ?? 'pending'),
  ).length

  // On this screen there is no cart, so relevance is exactly "named on an
  // order that is still open" — those are the orders that still need to
  // reach the supplier.
  const relevantContactHealth = contactHealth.filter(r => r.en_ordenes_pendientes)

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
            <ClipboardList size={17} color="#fff" strokeWidth={2.5} />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>
              {t('pedidos.page_title')}
            </h1>
            <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('pedidos.page_subtitle')}</p>
          </div>
        </div>
        {pendingCount > 0 && (
          <span style={{
            fontSize: 12, fontWeight: 700, padding: '4px 12px', borderRadius: 20,
            background: 'rgba(245,158,11,0.1)', color: C.amber,
          }}>
            {pendingCount} {t('pedidos.pending_suffix')}
          </span>
        )}
      </div>

      {/* Supplier health (2.5) and lead-time deviation (3.3) */}
      <SupplierContactHealthBanner rows={relevantContactHealth} />
      <SupplierLeadTimeAlertBanner alerts={leadTimeAlerts} />

      {/* Error */}
      {error && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderRadius: 8,
          background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.2)',
          fontSize: 13, color: C.red,
        }}>
          <AlertTriangle size={13} style={{ flexShrink: 0 }} /> {error}
        </div>
      )}

      {loading ? (
        <div style={{ padding: 64, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
      ) : history.length === 0 && !error ? (
        <div style={{
          padding: '48px 24px', borderRadius: 12, textAlign: 'center',
          background: C.card, border: `1px solid ${C.border}`,
        }}>
          <ClipboardList size={32} color={C.dim} style={{ margin: '0 auto 12px', opacity: 0.4 }} />
          <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginBottom: 6 }}>
            {t('pedidos.empty_title')}
          </div>
          <div style={{ fontSize: 12, color: C.dim, marginBottom: 16 }}>{t('pedidos.empty_hint')}</div>
          <Link href="/hoy" style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '8px 16px', borderRadius: 8, fontSize: 12, fontWeight: 600,
            background: 'rgba(129,140,248,0.1)', border: '1px solid rgba(129,140,248,0.3)',
            color: C.indigo, textDecoration: 'none',
          }}>
            <ShoppingCart size={13} /> {t('pedidos.go_to_hoy')}
          </Link>
        </div>
      ) : (
        <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>
          <POHistoryTable entries={history} onReceive={setReceivingPO} />
        </div>
      )}

      {receivingPO && (
        <ReceptionModal
          poId={receivingPO}
          onClose={() => setReceivingPO(null)}
          onSaved={() => { setReceivingPO(null); load() }}
        />
      )}
    </div>
  )
}
