'use client'
/**
 * `/pronosticos` on a phone.
 *
 * One screen, chart first. The desktop layout is a product column beside the
 * chart; on 360px that column does not fit, and pushing a second "detail"
 * screen made every product change a round trip. Here the product picker is a
 * button that opens a bottom sheet holding the same searchable, sortable list
 * the desktop column shows, and the chart (the same ChartPanel, in its compact
 * phone mode) stays on screen underneath.
 *
 * Everything the desktop toolbar offers is still here — refresh, the
 * all-SKUs export and session compare sit in an options sheet, because three
 * small buttons in a row are what pushed the toolbar past the edge. All state
 * lives in the page; this file only lays it out, so the two layouts can never
 * disagree about which session, product or tab is selected.
 */
import { useState } from 'react'
import {
  Package, MoreHorizontal, RefreshCw, FileSpreadsheet, GitCompare, Loader2, X, ChevronDown,
} from 'lucide-react'
import type {
  SessionInfo, MetricRow, InventoryRecommendation, QualityReport, InventorySignal,
  InventoryStatusItem, CoverageUnit, PolicyBacktest, PolicySkuComparison, DemandRiskEntry,
} from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import RunWarningsPanel from '@/components/ui/RunWarningsPanel'
import { EmptyState, InlineError, LoadingState } from '@/components/ui/States'
import { SIGNAL_STYLES } from '@/components/ui/SignalBadge'
import {
  BottomSheet, MobileTabs, StatusBadge, signalTone,
} from '@/components/mobile'
import { useLanguage } from '@/contexts/LanguageContext'
import { useTenantFacts, has } from '@/hooks/useTenantFacts'
import { seriesTypeLabel } from '@/lib/enumLabels'
import { SERIES_COLOR, pct } from '@/components/forecast/shared'
import { SessionSelector } from '@/components/forecast/SessionSelector'
import { ChartPanel } from '@/components/forecast/ChartPanel'
import CompareView from '@/components/forecast/CompareView'
import { SalesPatternPanel } from '@/components/forecast/SalesPatternPanel'
import { MetricsTable } from '@/components/forecast/MetricsTable'
import { QualityTab, QualityWarningList } from '@/components/forecast/QualityPanel'
import { InventoryPanel } from '@/components/forecast/InventoryPanel'
import { RunDetails } from '@/components/forecast/RunDetails'
import { PanelPlaceholder } from '@/components/forecast/PanelChrome'
import { ViewToggle, type ForecastView } from '@/components/forecast/ViewToggle'

// Thumb-sized: the smallest control a finger hits reliably.
const TAP = 44

export interface PronosticosMobileProps {
  view: ForecastView
  onView: (v: ForecastView) => void
  showTechnical: boolean

  sessions: SessionInfo[]
  sessLoading: boolean
  sessionId: string | null
  onSelectSession: (id: string) => void
  onRefresh: () => void
  sessError: unknown
  onRetrySessions: () => void
  onDismissSessError: () => void
  loadError: string | null
  onDismissLoadError: () => void

  loading: boolean
  /** Products in the open session, before any search. */
  catalogueCount: number
  /** The page's own product list; `afterPick` closes the sheet it sits in. */
  renderList: (touch: boolean, afterPick?: () => void) => React.ReactNode
  quality: QualityReport
  signalForSku: (sku: string) => InventorySignal | undefined

  selectedSku: string | null

  tab: string
  onTab: (tab: string) => void
  isDark: boolean
  skuMetrics: MetricRow[]
  skuInventory: InventoryRecommendation | undefined
  skuStatus: InventoryStatusItem | undefined
  coverageUnit: CoverageUnit | undefined
  skuPolicy: PolicySkuComparison | undefined
  skuRisk: DemandRiskEntry | undefined
  skuAccuracy: number | null
  skuWarnings: string[]
  showSkuStats: boolean
  onToggleSkuStats: () => void

  compareMode: boolean
  onToggleCompare: () => void
  cmpSessionIds: string[]
  onCmpSessionIds: (ids: string[]) => void
  /** Every SKU of the open session, for the comparison's own SKU picker. */
  skus: string[]
  onSku: (sku: string) => void

  bulkExporting: boolean
  bulkProgress: number
  bulkFailed: string[]
  onBulkExport: () => void

  policyBacktest: PolicyBacktest | null
  catalogueSize: number
}

export default function PronosticosMobile(p: PronosticosMobileProps) {
  const { t } = useLanguage()
  const { completedSessions } = useTenantFacts()
  const [optionsOpen, setOptionsOpen] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)

  const tabs = (p.showTechnical
    ? ['Forecast', 'Pattern', 'Metrics', 'Quality', 'Inventory']
    : ['Forecast', 'Pattern', 'Inventory']
  ).map(id => ({
    id,
    label: ({
      Forecast: t('skus.tab_forecast'),
      Pattern: t('skus.tab_pattern'),
      Metrics: t('skus.tab_metrics'),
      Quality: t('skus.tab_quality'),
      Inventory: t('skus.tab_inventory'),
    } as Record<string, string>)[id] ?? id,
  }))

  const options = (
    <BottomSheet open={optionsOpen} onClose={() => setOptionsOpen(false)} title={t('skus.mobile_options')}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, paddingBottom: 8 }}>
        {p.sessionId && (
          <button
            className="mobile-btn mobile-btn-secondary"
            style={{ flex: 'none', width: '100%', justifyContent: 'flex-start' }}
            onClick={() => { p.onRefresh(); setOptionsOpen(false) }}
          >
            <RefreshCw size={16} aria-hidden="true" /> {t('skus.btn_refresh')}
          </button>
        )}

        {/* Same rule as the desktop toolbar: technical view, or a run still
            in progress so its progress is never hidden. */}
        {p.sessionId && p.catalogueCount > 0 && (p.showTechnical || p.bulkExporting) && (
          <button
            className="mobile-btn mobile-btn-secondary"
            style={{ flex: 'none', width: '100%', justifyContent: 'flex-start' }}
            onClick={p.onBulkExport}
            disabled={p.bulkExporting}
          >
            {p.bulkExporting
              ? <><Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} aria-hidden="true" /> {p.bulkProgress} / {p.skus.length} {t('skus.skus_count_plural')}…</>
              : <><FileSpreadsheet size={16} aria-hidden="true" /> {t('skus.btn_export_all_skus')}</>}
          </button>
        )}
        {p.bulkFailed.length > 0 && !p.bulkExporting && (
          <div style={{ fontSize: 13, color: 'var(--signal-order-now-fg)', overflowWrap: 'anywhere' }}>
            {p.bulkFailed.length} {p.bulkFailed.length !== 1 ? t('skus.skus_failed_plural') : t('skus.skus_failed_singular')}: {p.bulkFailed.join(', ')}
          </div>
        )}

        {p.sessionId && p.showTechnical && has(completedSessions, 2) && (
          <button
            className={`mobile-btn ${p.compareMode ? 'mobile-btn-primary' : 'mobile-btn-secondary'}`}
            style={{ flex: 'none', width: '100%', justifyContent: 'flex-start' }}
            aria-pressed={p.compareMode}
            onClick={p.onToggleCompare}
          >
            {p.compareMode ? <X size={16} aria-hidden="true" /> : <GitCompare size={16} aria-hidden="true" />}
            {p.compareMode ? t('skus.mobile_compare_stop') : t('skus.btn_compare')}
          </button>
        )}
      </div>
    </BottomSheet>
  )

  const errors = (
    <>
      {p.sessError != null && (
        <InlineError error={p.sessError} onRetry={p.onRetrySessions} onDismiss={p.onDismissSessError} />
      )}
      {p.loadError && (
        <InlineError error={new Error(p.loadError)} onRetry={p.onRefresh} onDismiss={p.onDismissLoadError} />
      )}
    </>
  )

  const sku = p.selectedSku
  const skuQuality = sku ? p.quality[sku] : undefined
  const color = SERIES_COLOR[skuQuality?.series_type ?? 'unknown'] ?? SERIES_COLOR.unknown
  const signal = sku ? p.signalForSku(sku) : undefined
  const comparing = p.compareMode && !!p.sessionId
  const noSession = !p.sessionId
  const noProducts = !!p.sessionId && !p.loading && p.catalogueCount === 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
      {/* Product picker + options. The picker names what is on screen, so the
          chart below never needs a title of its own. */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'stretch', minWidth: 0 }}>
        <button
          onClick={() => setPickerOpen(true)}
          disabled={noSession || p.loading || p.catalogueCount === 0}
          aria-haspopup="dialog"
          aria-label={t('skus.picker_aria')}
          className="tap-feedback"
          style={{
            all: 'unset', boxSizing: 'border-box', cursor: 'pointer', flex: 1, minWidth: 0,
            minHeight: TAP + 4, padding: '0 14px', borderRadius: 12,
            border: '1px solid var(--border)', background: 'var(--surface)',
            display: 'flex', alignItems: 'center', gap: 10,
            opacity: noSession || p.catalogueCount === 0 ? 0.55 : 1,
          }}
        >
          <span aria-hidden="true" style={{
            width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
            background: signal ? SIGNAL_STYLES[signal].fg : 'var(--border)',
          }} />
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: 'block', fontSize: 16, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {sku ?? t('skus.picker_placeholder')}
            </span>
            {sku && (
              <span style={{ display: 'block', fontSize: 12.5, color: 'var(--dim)' }}>
                {skuQuality && <span style={{ color }}>{seriesTypeLabel(t, skuQuality.series_type)}</span>}
                {p.showTechnical && skuQuality && <> · {skuQuality.n_rows} {t('skus.rows_label')} · {pct(skuQuality.quality_score)} {t('skus.quality_label_lower')}</>}
                {p.showTechnical && p.skuAccuracy != null && <> · {t('skus.accuracy_label')} {p.skuAccuracy}%</>}
              </span>
            )}
          </span>
          {signal && <StatusBadge label={t(SIGNAL_STYLES[signal].labelKey)} tone={signalTone(signal)} />}
          <ChevronDown size={18} aria-hidden="true" style={{ color: 'var(--dim)', flexShrink: 0 }} />
        </button>
        <button
          onClick={() => setOptionsOpen(true)}
          aria-label={t('skus.mobile_options')}
          aria-haspopup="dialog"
          className="tap-feedback"
          style={{
            all: 'unset', boxSizing: 'border-box', cursor: 'pointer', flexShrink: 0,
            width: TAP + 4, minHeight: TAP + 4, borderRadius: 12,
            border: '1px solid var(--border)', background: 'var(--surface)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)',
            position: 'relative',
          }}
        >
          <MoreHorizontal size={20} aria-hidden="true" />
          {(p.compareMode || p.bulkExporting) && (
            <span aria-hidden="true" style={{
              position: 'absolute', top: 8, right: 8, width: 8, height: 8, borderRadius: 4,
              background: 'var(--accent)',
            }} />
          )}
        </button>
      </div>

      {errors}

      {noSession ? (
        p.sessLoading ? <div style={{ minHeight: TAP, display: 'flex', alignItems: 'center' }}><Spinner size={16} /></div> : (
          <EmptyState
            icon={<Package size={20} />}
            title={t('skus.empty_title')}
            body={t('skus.empty_body')}
            actions={[{ label: t('skus.empty_cta'), href: '/ventas' }]}
          />
        )
      ) : p.loading ? (
        <LoadingState label={t('skus.loading_label')}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div className="skeleton" style={{ height: 300, borderRadius: 12 }} />
            <div className="skeleton" style={{ height: 76, borderRadius: 12 }} />
            <div className="skeleton" style={{ height: 76, borderRadius: 12 }} />
          </div>
        </LoadingState>
      ) : noProducts ? (
        <EmptyState
          compact
          icon={<Package size={20} />}
          title={t('skus.empty_no_skus_found')}
          body={t('skus.empty_no_products_body')}
          actions={[{ label: t('skus.empty_cta'), href: '/ventas' }]}
        />
      ) : sku ? (
        <>
          {!p.showTechnical && skuQuality && p.skuWarnings.length > 0 && (
            <div style={{ padding: '10px 12px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14 }}>
              <QualityWarningList lines={p.skuWarnings} />
            </div>
          )}

          <MobileTabs
            ariaLabel={t('skus.mobile_tabs_aria')}
            tabs={tabs}
            value={p.tab}
            onChange={p.onTab}
            panelId="skus-mobile-panel"
          />

          <div
            id="skus-mobile-panel"
            role="tabpanel"
            key={p.tab}
            className="mobile-fade-enter"
            style={{
              background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14,
              overflow: 'hidden', display: 'flex', flexDirection: 'column', minWidth: 0,
            }}
          >
            {p.tab === 'Forecast' && p.sessionId && (
              comparing ? (
                <CompareView
                  sessions={p.sessions}
                  primaryId={p.sessionId}
                  extraIds={p.cmpSessionIds}
                  onExtraIds={p.onCmpSessionIds}
                  skus={p.skus}
                  sku={sku}
                  onSku={p.onSku}
                  isDark={p.isDark}
                />
              ) : (
                <ChartPanel
                  key={`${p.sessionId}-${sku}`}
                  tourAnchor="skus.chart"
                  sessionId={p.sessionId} sku={sku} isDark={p.isDark} quality={skuQuality}
                  showTechnical={p.showTechnical}
                  onSeeOrder={() => p.onTab('Inventory')}
                  status={p.skuStatus} coverageUnit={p.coverageUnit}
                />
              )
            )}
            {p.tab === 'Pattern' && p.sessionId && (
              <SalesPatternPanel key={`${p.sessionId}-${sku}`} sessionId={p.sessionId} sku={sku} isDark={p.isDark} />
            )}
            {p.tab === 'Metrics' && <MetricsTable rows={p.skuMetrics} sku={sku} />}
            {p.tab === 'Quality' && (
              skuQuality
                ? <QualityTab q={skuQuality} showStats={p.showSkuStats} onToggleStats={p.onToggleSkuStats} />
                : <PanelPlaceholder message={t('skus.empty_no_quality_data')} />
            )}
            {p.tab === 'Inventory' && (
              p.skuInventory
                ? <InventoryPanel inv={p.skuInventory} live={p.skuStatus} coverageUnit={p.coverageUnit} policy={p.skuPolicy} risk={p.skuRisk} />
                : <div style={{ padding: 16, color: 'var(--dim)', fontSize: 14 }}>{t('skus.no_inventory_recommendations')}</div>
            )}
          </div>
        </>
      ) : (
        <PanelPlaceholder message={t('skus.empty_select_sku_from_list')} />
      )}

      <ViewToggle value={p.view} onChange={p.onView} touch />

      {!p.sessLoading && p.sessions.length > 0 && (
        <div className="skus-mobile-session" style={{ minWidth: 0 }}>
          <SessionSelector
            tourAnchor="skus.session"
            sessions={p.sessions}
            selected={p.sessionId}
            onSelect={p.onSelectSession}
          />
        </div>
      )}

      <RunWarningsPanel sessionId={p.sessionId} collapsible />
      {p.showTechnical && (
        <RunDetails sessionId={p.sessionId} backtest={p.policyBacktest} catalogueSize={p.catalogueSize} touch />
      )}

      <BottomSheet open={pickerOpen} onClose={() => setPickerOpen(false)} title={t('skus.picker_title')}>
        {p.renderList(true, () => setPickerOpen(false))}
      </BottomSheet>
      {options}
    </div>
  )
}
