'use client'
/**
 * `/pronosticos` on a phone.
 *
 * The desktop screen is a two-pane workspace: a 280px SKU column next to a
 * chart that fills the rest. Squeezed into 360px that layout measured 628px
 * wide, with the chart and its tabs pushed off the right edge. A phone does
 * one thing at a time, so the narrow view is two screens in one route:
 *
 *   list   — the session, the buyer/technical toggle, a search box and one
 *            card per SKU (name, series type, live signal). Tapping a card
 *            opens…
 *   detail — that SKU with the header's back arrow, its tabs as a swipeable
 *            strip, and the same panels the desktop renders (the chart is the
 *            same ChartPanel, in its compact phone mode).
 *
 * Everything the desktop toolbar offers is still here — refresh, the
 * all-SKUs export and session compare sit in an "Opciones" sheet, because
 * three small buttons in a row are what pushed the toolbar past the edge.
 * All state lives in the page; this file only lays it out, so the two layouts
 * can never disagree about which session, SKU or tab is selected.
 */
import { useEffect, useRef, useState } from 'react'
import {
  Search, Package, MoreHorizontal, RefreshCw, FileSpreadsheet, GitCompare, Loader2, X,
} from 'lucide-react'
import type {
  SessionInfo, MetricRow, InventoryRecommendation, QualityReport, InventorySignal,
  InventoryStatusItem, CoverageUnit, PolicyBacktest, PolicySkuComparison, DemandRiskEntry,
} from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import RunWarningsPanel from '@/components/ui/RunWarningsPanel'
import RunLineagePanel from '@/components/ui/RunLineagePanel'
import { EmptyState, InlineError, LoadingState } from '@/components/ui/States'
import Pagination from '@/components/table/Pagination'
import { SIGNAL_STYLES } from '@/components/ui/SignalBadge'
import {
  BottomSheet, MobileCard, MobileList, MobileTabs, StatusBadge, signalTone, useMobileHeader,
} from '@/components/mobile'
import { useLanguage } from '@/contexts/LanguageContext'
import { seriesTypeLabel } from '@/lib/enumLabels'
import { SERIES_COLOR, pct, reliabilityInfo } from '@/components/forecast/shared'
import { SessionSelector } from '@/components/forecast/SessionSelector'
import { ChartPanel } from '@/components/forecast/ChartPanel'
import { SalesPatternPanel } from '@/components/forecast/SalesPatternPanel'
import { MetricsTable } from '@/components/forecast/MetricsTable'
import { QualityTab, QualityWarningList } from '@/components/forecast/QualityPanel'
import { InventoryPanel } from '@/components/forecast/InventoryPanel'
import { PolicyBacktestPanel } from '@/components/forecast/PolicyBacktestPanel'
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
  search: string
  onSearch: (s: string) => void
  /** All SKUs matching the search (the count); `pageRows` is what is shown. */
  skuCount: number
  page: { page: number; pageCount: number; offset: number; total: number; rows: string[] }
  onPage: (p: number) => void
  quality: QualityReport
  signalForSku: (sku: string) => InventorySignal | undefined

  selectedSku: string | null
  detailOpen: boolean
  onOpenSku: (sku: string) => void
  onCloseDetail: () => void

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
  cmpSessionId: string | null
  onCmpSession: (id: string) => void
  cmpSkus: string[]
  cmpSku: string | null
  onCmpSku: (sku: string) => void
  cmpLoading: boolean
  cmpError: string | null

  bulkExporting: boolean
  bulkProgress: number
  bulkFailed: string[]
  onBulkExport: () => void

  policyBacktest: PolicyBacktest | null
  catalogueSize: number
}

export default function PronosticosMobile(p: PronosticosMobileProps) {
  const { t } = useLanguage()
  const [optionsOpen, setOptionsOpen] = useState(false)
  const showDetail = p.detailOpen && !!p.selectedSku

  // The detail is a screen of its own: the header shows the SKU and a back
  // arrow, exactly like a pushed view in a native app.
  useMobileHeader(showDetail ? { title: p.selectedSku ?? undefined, onBack: p.onCloseDetail } : null)

  // Going into a SKU starts at the top; coming back lands where the list was
  // left, so a buyer walking the catalogue never loses their place.
  const listScroll = useRef(0)
  const wasDetail = useRef(false)
  useEffect(() => {
    const scroller = document.querySelector('.page-content')
    if (!scroller) return
    if (showDetail && !wasDetail.current) scroller.scrollTo({ top: 0 })
    if (!showDetail && wasDetail.current) scroller.scrollTo({ top: listScroll.current })
    wasDetail.current = showDetail
  }, [showDetail])

  const openSku = (sku: string) => {
    listScroll.current = document.querySelector('.page-content')?.scrollTop ?? 0
    p.onOpenSku(sku)
  }

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
        {p.sessionId && p.skuCount > 0 && (p.showTechnical || p.bulkExporting) && (
          <button
            className="mobile-btn mobile-btn-secondary"
            style={{ flex: 'none', width: '100%', justifyContent: 'flex-start' }}
            onClick={p.onBulkExport}
            disabled={p.bulkExporting}
          >
            {p.bulkExporting
              ? <><Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} aria-hidden="true" /> {p.bulkProgress} / {p.skuCount} {t('skus.skus_count_plural')}…</>
              : <><FileSpreadsheet size={16} aria-hidden="true" /> {t('skus.btn_export_all_skus')}</>}
          </button>
        )}
        {p.bulkFailed.length > 0 && !p.bulkExporting && (
          <div style={{ fontSize: 13, color: 'var(--signal-order-now-fg)', overflowWrap: 'anywhere' }}>
            {p.bulkFailed.length} {p.bulkFailed.length !== 1 ? t('skus.skus_failed_plural') : t('skus.skus_failed_singular')}: {p.bulkFailed.join(', ')}
          </div>
        )}

        {p.sessionId && p.showTechnical && (
          <>
            <button
              className={`mobile-btn ${p.compareMode ? 'mobile-btn-primary' : 'mobile-btn-secondary'}`}
              style={{ flex: 'none', width: '100%', justifyContent: 'flex-start' }}
              aria-pressed={p.compareMode}
              onClick={p.onToggleCompare}
            >
              {p.compareMode ? <X size={16} aria-hidden="true" /> : <GitCompare size={16} aria-hidden="true" />}
              {p.compareMode ? t('skus.mobile_compare_stop') : t('skus.btn_compare')}
            </button>
            {p.compareMode && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '4px 0' }}>
                <div style={{ fontSize: 13, color: 'var(--muted)' }}>{t('skus.compare_sessions_title')}</div>
                <SessionSelector
                  sessions={p.sessions}
                  selected={p.cmpSessionId}
                  onSelect={p.onCmpSession}
                  selectId="skus-compare-session-select-mobile"
                  name="skus_compare_session_mobile"
                />
                {p.cmpSkus.length > 0 && (
                  <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13, color: 'var(--muted)' }}>
                    {t('skus.sku_label')}
                    <select
                      className="form-select"
                      value={p.cmpSku ?? ''}
                      onChange={e => p.onCmpSku(e.target.value)}
                      style={{ fontSize: 16, minHeight: TAP }}
                    >
                      <option value="" disabled>{t('skus.select_sku_placeholder')}</option>
                      {p.cmpSkus.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </label>
                )}
                {p.cmpLoading && <Spinner size={14} />}
                {p.cmpError && <div style={{ fontSize: 13, color: 'var(--signal-order-now-fg)' }}>{p.cmpError}</div>}
              </div>
            )}
          </>
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
        <InlineError error={new Error(p.loadError)} onDismiss={p.onDismissLoadError} />
      )}
    </>
  )

  // ── Detail ────────────────────────────────────────────────────────────────
  if (showDetail && p.selectedSku) {
    const sku = p.selectedSku
    const skuQuality = p.quality[sku]
    const color = SERIES_COLOR[skuQuality?.series_type ?? 'unknown'] ?? SERIES_COLOR.unknown
    const signal = p.signalForSku(sku)
    const comparing = p.compareMode && !!p.cmpSessionId && !!p.cmpSku
    const sessionName = (id: string | null) => p.sessions.find(s => s.session_id === id)?.name ?? id

    return (
      <div key="detail" className="mobile-push-enter" style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
        {errors}

        {/* Who this is: series type, signal, and — technical view — the
            numbers the desktop header carries. */}
        <div data-tour="skus.header" style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '12px 14px',
          background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14,
        }}>
          <div style={{
            width: 36, height: 36, borderRadius: 10, flexShrink: 0, background: color + '18',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <Package size={16} color={color} aria-hidden="true" />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, color: 'var(--muted)' }}>
              {skuQuality ? (
                <>
                  <span style={{ color, fontWeight: 600 }}>{seriesTypeLabel(t, skuQuality.series_type)}</span>
                  {p.showTechnical && <> · {skuQuality.n_rows} {t('skus.rows_label')} · {pct(skuQuality.quality_score)} {t('skus.quality_label_lower')}</>}
                </>
              ) : t('skus.no_quality_data')}
            </div>
            {p.showTechnical && p.skuAccuracy != null && (
              <div style={{ fontSize: 13, color: 'var(--dim)', marginTop: 2 }}>
                {t('skus.accuracy_label')}: <strong style={{ color: 'var(--text)' }}>{p.skuAccuracy}%</strong>
              </div>
            )}
          </div>
          {signal && <StatusBadge label={t(SIGNAL_STYLES[signal].labelKey)} tone={signalTone(signal)} />}
        </div>

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
              <>
                <CompareLabel tone="a">A · {sessionName(p.sessionId)}</CompareLabel>
                <ChartPanel key={`${p.sessionId}-${sku}`} sessionId={p.sessionId} sku={sku} isDark={p.isDark} quality={skuQuality} showTechnical={p.showTechnical} />
                <div style={{ height: 2, background: 'var(--accent)' }} />
                <CompareLabel tone="b">B · {sessionName(p.cmpSessionId)} · {p.cmpSku}</CompareLabel>
                <ChartPanel key={`${p.cmpSessionId}-${p.cmpSku}`} sessionId={p.cmpSessionId!} sku={p.cmpSku!} isDark={p.isDark} showTechnical={p.showTechnical} />
              </>
            ) : (
              <ChartPanel
                key={`${p.sessionId}-${sku}`}
                tourAnchor="skus.chart"
                sessionId={p.sessionId} sku={sku} isDark={p.isDark} quality={skuQuality}
                showTechnical={p.showTechnical}
                onSeeOrder={() => p.onTab('Inventory')}
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
        {options}
      </div>
    )
  }

  // ── List ──────────────────────────────────────────────────────────────────
  return (
    <div key="list" className={wasDetail.current ? 'mobile-pop-enter' : undefined} style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'stretch', minWidth: 0 }}>
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
          {p.sessLoading
            ? <div style={{ minHeight: TAP, display: 'flex', alignItems: 'center' }}><Spinner size={16} /></div>
            : (
              <div style={{ minWidth: 0 }} className="skus-mobile-session">
                <SessionSelector
                  tourAnchor="skus.session"
                  sessions={p.sessions}
                  selected={p.sessionId}
                  onSelect={p.onSelectSession}
                />
              </div>
            )}
        </div>
        <button
          onClick={() => setOptionsOpen(true)}
          aria-label={t('skus.mobile_options')}
          aria-haspopup="dialog"
          className="tap-feedback"
          style={{
            all: 'unset', boxSizing: 'border-box', cursor: 'pointer', flexShrink: 0,
            width: TAP + 4, minHeight: TAP, borderRadius: 10,
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

      <ViewToggle value={p.view} onChange={p.onView} touch />

      {errors}

      <RunWarningsPanel sessionId={p.sessionId} collapsible />
      <RunLineagePanel sessionId={p.sessionId} />

      <div style={{ position: 'relative' }}>
        <Search size={16} aria-hidden="true" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--dim)' }} />
        <input
          data-tour="skus.search"
          type="search"
          inputMode="search"
          enterKeyHint="search"
          placeholder={t('skus.search_placeholder')}
          aria-label={t('skus.search_placeholder')}
          value={p.search}
          onChange={e => p.onSearch(e.target.value)}
          className="form-input"
          style={{ paddingLeft: 36, fontSize: 16, minHeight: TAP, boxSizing: 'border-box', width: '100%' }}
        />
      </div>

      {p.skuCount > 0 && (
        <div style={{ fontSize: 13, color: 'var(--dim)', padding: '0 2px' }}>
          {p.skuCount} {p.skuCount !== 1 ? t('skus.skus_count_plural') : t('skus.skus_count_singular')}
        </div>
      )}

      {!p.sessionId ? (
        p.sessLoading ? null : (
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
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="skeleton" style={{ height: 56, borderRadius: 12 }} />
            ))}
          </div>
        </LoadingState>
      ) : p.skuCount === 0 ? (
        <EmptyState compact icon={<Search size={20} />} title={t('skus.empty_no_skus_found')} />
      ) : (
        <>
          <MobileList ariaLabel={t('skus.mobile_list_aria')}>
            {p.page.rows.map(sku => {
              const q = p.quality[sku]
              const signal = p.signalForSku(sku)
              const color = SERIES_COLOR[q?.series_type ?? 'unknown'] ?? SERIES_COLOR.unknown
              const rel = q ? reliabilityInfo(q.quality_score, t) : null
              return (
                <MobileCard
                  key={sku}
                  title={sku}
                  subtitle={q
                    ? <><span style={{ color }}>{seriesTypeLabel(t, q.series_type)}</span>{rel && <> · {rel.label}</>}</>
                    : t('skus.no_quality_data')}
                  status={signal ? { label: t(SIGNAL_STYLES[signal].labelKey), tone: signalTone(signal) } : undefined}
                  leading={
                    <span style={{
                      width: 32, height: 32, borderRadius: 9, background: color + '18',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}>
                      <Package size={15} color={color} aria-hidden="true" />
                    </span>
                  }
                  onClick={() => openSku(sku)}
                />
              )
            })}
          </MobileList>
          {p.page.pageCount > 1 && (
            <div className="skus-mobile-pagination" style={{ minWidth: 0 }}>
              <Pagination
                page={p.page.page}
                pageCount={p.page.pageCount}
                offset={p.page.offset}
                total={p.page.total}
                rowsOnPage={p.page.rows.length}
                onPage={p.onPage}
                label="SKU"
              />
            </div>
          )}
        </>
      )}

      <PolicyBacktestPanel backtest={p.policyBacktest} catalogueSize={p.catalogueSize} />
      {options}
    </div>
  )
}

function CompareLabel({ tone, children }: { tone: 'a' | 'b'; children: React.ReactNode }) {
  const a = tone === 'a'
  return (
    <div style={{
      margin: '10px 12px 0', alignSelf: 'flex-start', maxWidth: 'calc(100% - 24px)',
      fontSize: 12, fontWeight: 600, padding: '3px 8px', borderRadius: 6,
      color: a ? 'var(--accent)' : '#2E8B62',
      background: a ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : 'rgba(46,139,98,0.12)',
      overflow: 'hidden', overflowWrap: 'anywhere',
    }}>
      {children}
    </div>
  )
}
