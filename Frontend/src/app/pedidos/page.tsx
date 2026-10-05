'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { getPOHistoryPage } from '@/lib/api'
import type { POLogEntry } from '@/lib/types'
import { useAttention } from '@/hooks/useAttention'
import { POHistoryTable, ReceptionModal } from '@/components/po/POHistory'
import { ManualPOModal } from '@/components/po/ManualPOModal'
import { TransfersPanel } from '@/components/po/TransfersPanel'
import BulkImportButton from '@/components/inventory/BulkImportButton'
import { useWarehouses } from '@/components/inventory/WarehouseControls'
import { EmptyState, ErrorState, LoadingState, SkeletonTable } from '@/components/ui/States'
import { ClipboardList, Plus, ShoppingCart } from 'lucide-react'
import Card from '@/components/ui/Card'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import PedidosMobile from './PedidosMobile'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', amber: '#B7791F',
}

export default function OrdersPage() {
  const { t } = useLanguage()
  const [history,     setHistory]     = useState<POLogEntry[]>([])
  // The history is filtered and paged by the server (`/inventory/po-history/page`):
  // it used to be the newest 50 filtered in the browser, so the 51st order was
  // unreachable and "unpaid" only searched what happened to be loaded.
  const [total,       setTotal]       = useState(0)
  const [awaiting,    setAwaiting]    = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loading,     setLoading]     = useState(true)
  // Holds the raw error so ErrorState can classify it by kind rather than
  // rendering a pre-flattened string.
  const [error,       setError]       = useState<unknown>(null)
  const [receivingPO, setReceivingPO] = useState<string | null>(null)
  const [creatingPO,  setCreatingPO]  = useState(false)
  const canCreate = getUser()?.role !== 'viewer'
  // What is waiting on the buyer is shown as a chip inside the order's row (and
  // in the bell / nav count), never as a banner above the table.
  const { overdue, contactHealth, reload: reloadAttention } = useAttention()
  const overdueById = Object.fromEntries(overdue.map(o => [o.po_log_id, o]))
  // Multi-warehouse (feature 5.4): transfers tab, visible only with 2+ warehouses.
  const { multi: multiWarehouse } = useWarehouses()
  const [tab, setTab] = useState<'orders' | 'transfers'>('orders')
  // Paid / unpaid / cancelled filter (math audit O3, PO cancellation).
  // Applied to the table only: the pending-arrival counter in the header
  // answers a different question and must not change with it.
  const [paidFilter, setPaidFilter] = useState<'all' | 'unpaid' | 'paid' | 'cancelled'>('all')
  // Phone or desktop. Declared with the other hooks so the hook order is stable
  // whichever tree ends up rendering (see the fork below).
  const isNarrow = useIsNarrow()

  // `silent: true` — this screen renders the failure itself as a full ErrorState,
  // so the interceptor's toast would say the same thing twice.
  const loadedRef = useRef(0)
  const load = useCallback(async (initial = false, append = false) => {
    if (initial) setLoading(true)
    if (append) setLoadingMore(true)
    setError(null)
    try {
      const page = await getPOHistoryPage(
        { limit: PAGE, offset: append ? loadedRef.current : 0, status: paidFilter },
        { silent: true })
      setTotal(page.total)
      setAwaiting(page.awaiting_reception)
      setHistory(prev => {
        const next = append ? [...prev, ...page.items] : page.items
        loadedRef.current = next.length
        return next
      })
    }
    catch (e: unknown) { if (!append) setError(e) }
    finally { if (initial) setLoading(false); setLoadingMore(false) }
  }, [paidFilter])

  // Only the very first load shows the skeleton; changing the filter keeps the
  // bar (and the rows) on screen while the server answers.
  const firstLoad = useRef(true)
  useEffect(() => { load(firstLoad.current); firstLoad.current = false }, [load])

  // Opened from the command palette (Ctrl-K → "Nueva orden"): the modal is
  // local state here, so the intent can only travel in the URL. Read once and
  // scrub the query string, or a refresh would reopen the modal by itself.
  useEffect(() => {
    if (typeof window === 'undefined') return
    if (new URLSearchParams(window.location.search).get('new') !== '1') return
    window.history.replaceState(null, '', window.location.pathname)
    if (canCreate) setCreatingPO(true)
  }, [canCreate])

  // Counted by the server over every open order, not over the loaded page: the
  // header badge answers a different question than the table's filter.
  const pendingCount = awaiting
  // "Unpaid" means an order that is owed: sent, not marked paid and not
  // cancelled. A draft was never invoiced, so it is neither. The filter itself
  // is applied by the server; what is loaded is already what the table shows.
  const visibleHistory = history

  // ── Phone: a card list, not this table ────────────────────────────────────
  // Recording a delivery is done standing at the pallet. Everything above this
  // line — the loading, the history, the supplier-health filtering, the
  // reception modal below — is shared; only the presentation forks, so the two
  // views cannot disagree about which order is still open.
  //
  // `isNarrow` is false on the first render (SSR has no viewport), so the
  // desktop tree is what hydrates and the swap happens one paint later.
  if (isNarrow) {
    return (
      <>
        <PedidosMobile
          loading={loading}
          error={error}
          onRetry={() => load(true)}
          entries={history}
          suppliersWithoutContact={contactHealth.map(r => r.supplier)}
          overdueById={overdueById}
          onReceive={setReceivingPO}
          onChanged={() => { load(); reloadAttention() }}
          canEdit={canCreate}
          onCreate={() => setCreatingPO(true)}
          multiWarehouse={multiWarehouse}
          transfers={<TransfersPanel />}
        />
        {/* The same reception form the desktop table opens (a bottom sheet on
            a phone). Reception writes stock and teaches the supplier's real
            lead time — one implementation of that mutation, or the two screens
            could record different things. */}
        {receivingPO && (
          <ReceptionModal
            poId={receivingPO}
            onClose={() => setReceivingPO(null)}
            onSaved={() => { setReceivingPO(null); load(); reloadAttention() }}
          />
        )}
        {creatingPO && (
          <ManualPOModal
            onClose={() => setCreatingPO(false)}
            onSaved={() => { setCreatingPO(false); load() }}
          />
        )}
      </>
    )
  }

  // No page-level entrance on this root: the route fade is applied once by
  // AppShell, and a second one here would double-animate the same screen.
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        {/* The top bar / phone header already names the screen: only what it
            is for. */}
        <p style={{ margin: 0, fontSize: 12, color: C.dim, flex: '1 1 200px', minWidth: 0 }}>{t('orders.page_subtitle')}</p>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {pendingCount > 0 && (
            <span data-tour="pedidos.pending" style={{
              fontSize: 12, fontWeight: 700, padding: '4px 12px', borderRadius: 20,
              background: 'rgba(183,121,31,0.1)', color: C.amber,
            }}>
              {pendingCount} {t('orders.pending_suffix')}
            </span>
          )}
          <BulkImportButton kind="orders" onImported={() => load()} />
          {canCreate && (
            <button
              data-tour="pedidos.manual"
              onClick={() => setCreatingPO(true)}
              style={{
                all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center',
                gap: 6, padding: '7px 14px', borderRadius: 8, fontSize: 12, fontWeight: 700,
                background: 'var(--accent)', color: '#fff',
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
        <div data-tour="pedidos.tabs" role="tablist" aria-label={t('transfers.tablist_aria')} style={{ display: 'flex', gap: 4 }}>
          <button role="tab" aria-selected={tab === 'orders'} onClick={() => setTab('orders')}
                  style={tabStyle(tab === 'orders')}>{t('transfers.tab_orders')}</button>
          <button role="tab" aria-selected={tab === 'transfers'} onClick={() => setTab('transfers')}
                  style={tabStyle(tab === 'transfers')}>{t('transfers.tab_transfers')}</button>
        </div>
      )}

      {tab === 'transfers' && multiWarehouse ? <TransfersPanel /> : (
      <>
      {/* The three states: loading -> error -> empty -> data. */}
      {loading ? (
        <Card padding={8}>
          <LoadingState label={t('orders.loading_label')}>
            <SkeletonTable rows={6} columns={5} />
          </LoadingState>
        </Card>
      ) : error ? (
        <ErrorState error={error} onRetry={() => load(true)} />
      ) : history.length === 0 && paidFilter === 'all' ? (
        <EmptyState
          icon={<ClipboardList size={22} />}
          title={t('orders.empty_title')}
          body={t('orders.empty_hint')}
          bullets={[
            t('orders.empty_bullet_1'),
            t('orders.empty_bullet_2'),
            t('orders.empty_bullet_3'),
          ]}
          actions={[{ label: t('orders.go_to_hoy'), href: '/compras', icon: <ShoppingCart size={14} /> }]}
        />
      ) : (
        <>
        <div role="group" aria-label={t('po.paid_filter_label')}
             style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11.5, color: C.dim, marginRight: 4 }}>{t('po.paid_filter_label')}:</span>
          {(['all', 'unpaid', 'paid', 'cancelled'] as const).map(f => (
            <button key={f} aria-pressed={paidFilter === f} onClick={() => setPaidFilter(f)}
                    style={tabStyle(paidFilter === f)}>
              {t(`po.paid_filter_${f}`)}
            </button>
          ))}
        </div>
        {visibleHistory.length === 0 ? (
          <Card padding={16}>
            <p style={{ margin: 0, fontSize: 12.5, color: C.dim }}>{t('po.paid_filter_empty')}</p>
          </Card>
        ) : (
        // The skeleton above already has the shape of this table, so fading the
        // rows in reads as the placeholder becoming the data, not as a blink.
        <Card data-tour="pedidos.table" className="page-enter" padding={0} overflow="hidden">
          <POHistoryTable
            entries={visibleHistory}
            onReceive={setReceivingPO}
            // Reloads after an undo or a payment change. It was never passed,
            // so an un-send or un-receive left the row showing the old state
            // until the page was reloaded by hand.
            onUndone={() => { load(); reloadAttention() }}
            suppliersWithoutContact={contactHealth.map(r => r.supplier)}
            overdueById={overdueById}
          />
        </Card>
        )}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11, color: C.dim }}>
            {t('list.showing', { shown: history.length, total })}
          </span>
          {history.length < total && (
            <button type="button" disabled={loadingMore} onClick={() => load(false, true)}
                    style={{ all: 'unset', cursor: loadingMore ? 'default' : 'pointer', fontSize: 12,
                             color: 'var(--accent)', opacity: loadingMore ? 0.5 : 1 }}>
              {loadingMore ? t('common.loading') : t('list.load_more')}
            </button>
          )}
        </div>
        </>
      )}

      {receivingPO && (
        <ReceptionModal
          poId={receivingPO}
          onClose={() => setReceivingPO(null)}
          onSaved={() => { setReceivingPO(null); load(); reloadAttention() }}
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

const PAGE = 50

const tabStyle = (active: boolean): React.CSSProperties => ({
  all: 'unset', cursor: 'pointer', padding: '5px 12px', borderRadius: 7,
  fontSize: 11.5, fontWeight: 600,
  background: active ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : 'transparent',
  color: active ? 'var(--accent)' : C.dim,
})
