'use client'
import { useState, useEffect, useCallback } from 'react'
import { getPOHistory, getSupplierContactHealth, getSupplierLeadTimeAlerts } from '@/lib/api'
import type { POLogEntry, SupplierContactHealthRow, SupplierLeadTimeAlert } from '@/lib/types'
import { POHistoryTable, ReceptionModal } from '@/components/po/POHistory'
import { ManualPOModal } from '@/components/po/ManualPOModal'
import { TransfersPanel } from '@/components/po/TransfersPanel'
import { useWarehouses } from '@/components/inventory/WarehouseControls'
import {
  SupplierContactHealthBanner, SupplierLeadTimeAlertBanner,
} from '@/components/suppliers/SupplierHealthBanners'
import { EmptyState, ErrorState, LoadingState, SkeletonTable } from '@/components/ui/States'
import { ClipboardList, Plus, ShoppingCart } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', amber: '#f59e0b',
}

export default function OrdersPage() {
  const { t } = useLanguage()
  const [history,     setHistory]     = useState<POLogEntry[]>([])
  const [loading,     setLoading]     = useState(true)
  // Holds the raw error so ErrorState can classify it by kind rather than
  // rendering a pre-flattened string.
  const [error,       setError]       = useState<unknown>(null)
  const [receivingPO, setReceivingPO] = useState<string | null>(null)
  const [creatingPO,  setCreatingPO]  = useState(false)
  const canCreate = getUser()?.role !== 'viewer'
  const [contactHealth,  setContactHealth]  = useState<SupplierContactHealthRow[]>([])
  const [leadTimeAlerts, setLeadTimeAlerts] = useState<SupplierLeadTimeAlert[]>([])
  // Multi-warehouse (feature 5.4): transfers tab, visible only with 2+ warehouses.
  const { multi: multiWarehouse } = useWarehouses()
  const [tab, setTab] = useState<'orders' | 'transfers'>('orders')

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
            background: '#6366f1',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <ClipboardList size={17} color="#fff" strokeWidth={2.5} />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>
              {t('orders.page_title')}
            </h1>
            <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('orders.page_subtitle')}</p>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {pendingCount > 0 && (
            <span style={{
              fontSize: 12, fontWeight: 700, padding: '4px 12px', borderRadius: 20,
              background: 'rgba(245,158,11,0.1)', color: C.amber,
            }}>
              {pendingCount} {t('orders.pending_suffix')}
            </span>
          )}
          {canCreate && (
            <button
              onClick={() => setCreatingPO(true)}
              style={{
                all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center',
                gap: 6, padding: '7px 14px', borderRadius: 8, fontSize: 12, fontWeight: 700,
                background: '#6366f1', color: '#fff',
              }}
            >
              <Plus size={13} aria-hidden="true" />
              {t('po.manual_new_order')}
            </button>
          )}
        </div>
      </div>

      {/* Orders vs. transfers (feature 5.4) — tab bar only for multi-warehouse tenants */}
      {multiWarehouse && (
        <div role="tablist" aria-label={t('transfers.tablist_aria')} style={{ display: 'flex', gap: 4 }}>
          <button role="tab" aria-selected={tab === 'orders'} onClick={() => setTab('orders')}
                  style={tabStyle(tab === 'orders')}>{t('transfers.tab_orders')}</button>
          <button role="tab" aria-selected={tab === 'transfers'} onClick={() => setTab('transfers')}
                  style={tabStyle(tab === 'transfers')}>{t('transfers.tab_transfers')}</button>
        </div>
      )}

      {tab === 'transfers' && multiWarehouse ? <TransfersPanel /> : (
      <>
      {/* Supplier health (2.5) and lead-time deviation (3.3) */}
      <SupplierContactHealthBanner rows={relevantContactHealth} />
      <SupplierLeadTimeAlertBanner alerts={leadTimeAlerts} />

      {/* The three states: loading -> error -> empty -> data. */}
      {loading ? (
        <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: 8 }}>
          <LoadingState label={t('orders.loading_label')}>
            <SkeletonTable rows={6} columns={5} />
          </LoadingState>
        </div>
      ) : error ? (
        <ErrorState error={error} onRetry={() => load(true)} />
      ) : history.length === 0 ? (
        <EmptyState
          icon={<ClipboardList size={22} />}
          title={t('orders.empty_title')}
          body={t('orders.empty_hint')}
          bullets={[
            t('orders.empty_bullet_1'),
            t('orders.empty_bullet_2'),
            t('orders.empty_bullet_3'),
          ]}
          actions={[{ label: t('orders.go_to_hoy'), href: '/hoy', icon: <ShoppingCart size={14} /> }]}
        />
      ) : (
        <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>
          <POHistoryTable
            entries={history}
            onReceive={setReceivingPO}
            suppliersWithoutContact={contactHealth.map(r => r.supplier)}
          />
        </div>
      )}

      {receivingPO && (
        <ReceptionModal
          poId={receivingPO}
          onClose={() => setReceivingPO(null)}
          onSaved={() => { setReceivingPO(null); load() }}
        />
      )}

      {creatingPO && (
        <ManualPOModal
          onClose={() => setCreatingPO(false)}
          onSaved={() => { setCreatingPO(false); load() }}
        />
      )}
      </>
      )}
    </div>
  )
}

const tabStyle = (active: boolean): React.CSSProperties => ({
  all: 'unset', cursor: 'pointer', padding: '5px 12px', borderRadius: 7,
  fontSize: 11.5, fontWeight: 600,
  background: active ? 'rgba(129,140,248,0.12)' : 'transparent',
  color: active ? '#818cf8' : C.dim,
})
