'use client'
import { useState, useEffect, useCallback } from 'react'
import { getPOHistory, getSupplierContactHealth, getSupplierLeadTimeAlerts } from '@/lib/api'
import type { POLogEntry, SupplierContactHealthRow, SupplierLeadTimeAlert } from '@/lib/types'
import { POHistoryTable, ReceptionModal } from '@/components/po/POHistory'
import {
  SupplierContactHealthBanner, SupplierLeadTimeAlertBanner,
} from '@/components/suppliers/SupplierHealthBanners'
import { EmptyState, ErrorState, LoadingState, SkeletonTable } from '@/components/ui/States'
import { ClipboardList, ShoppingCart } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', amber: '#f59e0b',
}

export default function PedidosPage() {
  const { t } = useLanguage()
  const [history,     setHistory]     = useState<POLogEntry[]>([])
  const [loading,     setLoading]     = useState(true)
  // Holds the raw error so ErrorState can classify it by kind rather than
  // rendering a pre-flattened string.
  const [error,       setError]       = useState<unknown>(null)
  const [receivingPO, setReceivingPO] = useState<string | null>(null)
  const [contactHealth,  setContactHealth]  = useState<SupplierContactHealthRow[]>([])
  const [leadTimeAlerts, setLeadTimeAlerts] = useState<SupplierLeadTimeAlert[]>([])

  // `silent: true` — this screen renders the failure itself as a full ErrorState,
  // so the interceptor's toast would say the same thing twice.
  const load = useCallback(async (initial = false) => {
    if (initial) setLoading(true)
    setError(null)
    try { setHistory(await getPOHistory(50, { silent: true })) }
    catch (e: unknown) { setError(e) }
    finally { if (initial) setLoading(false) }
  }, [])

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

      {/* The three states: loading -> error -> empty -> data. */}
      {loading ? (
        <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: 8 }}>
          <LoadingState label={t('pedidos.loading_label')}>
            <SkeletonTable rows={6} columns={5} />
          </LoadingState>
        </div>
      ) : error ? (
        <ErrorState error={error} onRetry={() => load(true)} />
      ) : history.length === 0 ? (
        <EmptyState
          icon={<ClipboardList size={22} />}
          title={t('pedidos.empty_title')}
          body={t('pedidos.empty_hint')}
          bullets={[
            t('pedidos.empty_bullet_1'),
            t('pedidos.empty_bullet_2'),
            t('pedidos.empty_bullet_3'),
          ]}
          actions={[{ label: t('pedidos.go_to_hoy'), href: '/hoy', icon: <ShoppingCart size={14} /> }]}
        />
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
