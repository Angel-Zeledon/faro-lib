'use client'
import Link from 'next/link'
import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import {
  getSessions, getMetrics, getTrainingResults, getQuality,
  getSkuIntelligence, getInventoryStatus,
} from '@/lib/api'
import type {
  SessionInfo, MetricRow, InventoryRecommendation, QualityReport,
  InventorySignal, InventoryStatusItem, CoverageUnit,
  PolicyBacktest, DemandRiskEntry, TrainingResults,
} from '@/lib/types'
import { downloadWorkbook } from '@/lib/excel'
import SignalBadge from '@/components/ui/SignalBadge'
import Spinner from '@/components/ui/Spinner'
import RunWarningsPanel from '@/components/ui/RunWarningsPanel'
import { useTenantFacts, has } from '@/hooks/useTenantFacts'
import {
  EmptyState, InlineError, LoadingState, SkeletonTable,
} from '@/components/ui/States'
import Button from '@/components/ui/Button'
import { usePlanning } from '@/contexts/PlanningContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { seriesTypeLabel } from '@/lib/enumLabels'
import {
  Package, RefreshCw,
  GitCompare, FileSpreadsheet, Loader2,
} from 'lucide-react'
// The screen's panels. They lived in this file until it passed 3,400 lines;
// they moved out unchanged when the page was split into a buyer view and a
// technical view (docs/stability.md section 18).
import {
  SERIES_COLOR, ACTION_SIGNAL, championError, pct,
} from '@/components/forecast/shared'
import { SessionSelector } from '@/components/forecast/SessionSelector'
import { SkuList, sortSkus, type SkuRowInfo, type SkuSort } from '@/components/forecast/SkuList'
import { ChartPanel } from '@/components/forecast/ChartPanel'
import CompareView from '@/components/forecast/CompareView'
import { SalesPatternPanel } from '@/components/forecast/SalesPatternPanel'
import { MetricsTable } from '@/components/forecast/MetricsTable'
import {
  QualityTab, QualityWarningList, useQualityWarnings,
} from '@/components/forecast/QualityPanel'
import { InventoryPanel } from '@/components/forecast/InventoryPanel'
import { RunDetails } from '@/components/forecast/RunDetails'
import { PanelPlaceholder, TabBar } from '@/components/forecast/PanelChrome'
import { ViewToggle, useForecastView } from '@/components/forecast/ViewToggle'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import PronosticosMobile from './PronosticosMobile'

// ── Main page ─────────────────────────────────────────────────────────────────
//
// Two views behind one toggle at the top (docs/stability.md section 18):
//
//   Buyer (default) — the SKU list, the chart and its band, what is expected to
//     sell in the next periods, the stock signal, the Pattern and Inventory
//     tabs, and the data warnings that change how far the curve can be trusted.
//   Technical — everything above plus everything the screen had before the
//     split: model chips and overlays, the champion and its error, the Metrics
//     and Quality tabs, run details, per-model sparklines, session compare and
//     the all-SKUs export.
//
// Nothing exists only in the buyer view that the technical view lacks, except
// the upcoming-periods strip, which restates the chart for a non-analyst.

export default function SkusPage() {
  const { t } = useLanguage()
  // Shared active-period session (multi-period) — same resolver /hoy and
  // /inventory follow. Drives the default-select on load (#6).
  const planningCtx = usePlanning()
  const activeSessionId = planningCtx?.planning?.active_session_id ?? ''
  const [sessions,       setSessions]       = useState<SessionInfo[]>([])
  const [sessionId,      setSessionId]      = useState<string | null>(null)
  const [metrics,        setMetrics]        = useState<MetricRow[]>([])
  const [inventory,      setInventory]      = useState<InventoryRecommendation[]>([])
  // Live inventory status (from inventory_stock via /inventory/status) —
  // reflects post-reception stock, unlike the training-time `inventory`.
  const [invStatus,      setInvStatus]      = useState<InventoryStatusItem[]>([])
  const [coverageUnit,   setCoverageUnit]   = useState<CoverageUnit | undefined>(undefined)
  const [quality,        setQuality]        = useState<QualityReport>({})
  // What the ordering policy would have done over real past demand, and the
  // measured lead-time uncertainty behind each safety stock. Both ride on the
  // same `/results` payload the metrics and inventory come from.
  const [policyBacktest, setPolicyBacktest] = useState<PolicyBacktest | null>(null)
  const [demandRisk,     setDemandRisk]     = useState<Record<string, DemandRiskEntry>>({})
  const [loading,        setLoading]        = useState(false)
  const [search,         setSearch]         = useState('')
  const [sort,           setSort]           = useState<SkuSort>('urgency')
  // Technical view: the run's provenance and the policy backtest sit behind one
  // disclosure instead of stacking above and below the chart.
  const [showRunDetails, setShowRunDetails] = useState(false)
  const [selectedSku,    setSelectedSku]    = useState<string | null>(null)
  const [tab,            setTab]            = useState('Forecast')
  const [sessLoading,    setSessLoading]    = useState(true)
  const [sessError,      setSessError]      = useState<unknown>(null)
  const [loadError,      setLoadError]      = useState<string | null>(null)
  const [isDark,         setIsDark]         = useState(true)
  const [showSkuStats,   setShowSkuStats]   = useState(false)
  // Buyer or technical view — the page-level toggle. Remembered per viewer and
  // shareable as `?view=tech`; see useForecastView.
  const [view, setView] = useForecastView()
  const { completedSessions } = useTenantFacts()
  const showTechnical = view === 'tech'
  // Compare mode
  const [compareMode,    setCompareMode]    = useState(false)
  // Sessions compared against the open one; CompareView draws them all on one chart.
  const [cmpSessionIds,  setCmpSessionIds]  = useState<string[]>([])
  // Metrics/Quality only exist as tabs in the technical view — switching to
  // the buyer view while one of them is active would otherwise leave `tab`
  // pointing at a tab no longer in the TabBar.
  useEffect(() => {
    if (!showTechnical && (tab === 'Metrics' || tab === 'Quality')) setTab('Forecast')
  }, [showTechnical, tab])
  // Compare is a technical control too: leaving the technical view closes it
  // (the same way its own button does) rather than leaving a split chart the
  // buyer view has no button to undo.
  useEffect(() => {
    if (!showTechnical && compareMode) { setCompareMode(false); setCmpSessionIds([]) }
  }, [showTechnical, compareMode])
  // Phones get one screen (PronosticosMobile): the chart, with the product
  // picker in a bottom sheet. Desktop shows the list beside it.
  const narrow = useIsNarrow()
  // Bulk export
  const [bulkExporting,  setBulkExporting]  = useState(false)
  const [bulkProgress,   setBulkProgress]   = useState(0)
  const [bulkFailed,     setBulkFailed]     = useState<string[]>([])

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    setIsDark(document.documentElement.dataset.theme !== 'light')
    const observer = new MutationObserver(() => {
      setIsDark(document.documentElement.dataset.theme !== 'light')
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])

  // Extracted so the error banner's retry can re-run exactly this load.
  const reloadSessions = useCallback(() => {
    setSessLoading(true)
    setSessError(null)
    getSessions()
      .then(s => { setSessions(s); setSessLoading(false) })
      .catch((e: unknown) => {
        setSessError(e)
        setSessLoading(false)
      })
  }, [])

  useEffect(() => { reloadSessions() }, [reloadSessions])

  // Default-select the active session on load (#6) so the user lands on data
  // instead of an empty state that needs a manual pick. Reuses the shared
  // active-period resolution (PlanningContext) — the same one /hoy and
  // /inventory follow — and falls back to the latest-completed session for
  // tenants without a resolved active period.
  //
  // `sessions` and the planning state load in parallel, so we may land on
  // latest-completed before the active session resolves. `lastAutoRef` tracks
  // the session WE auto-applied: once planning resolves an active session and
  // the user hasn't switched since, we upgrade to it. A manual pick (which
  // never touches `lastAutoRef`) always wins and is never overridden.
  const lastAutoRef = useRef('')
  // /sessions history deep-link (/skus?session=<id>): honored once on load,
  // before the auto-pick. Treated like a manual pick, so the planning-resolved
  // active session never overrides it.
  const urlSessionConsumedRef = useRef(false)
  useEffect(() => {
    if (!urlSessionConsumedRef.current && !sessionId && sessions.length) {
      urlSessionConsumedRef.current = true
      const wanted = new URLSearchParams(window.location.search).get('session')
      if (wanted && sessions.some(s => s.session_id === wanted && s.status === 'COMPLETED')) {
        setSessionId(wanted)
        setTab('Forecast')
        return
      }
    }
    const trained = sessions
      .filter(s => s.status === 'COMPLETED')
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    if (!trained.length) return
    const active = activeSessionId
      ? trained.find(s => s.session_id === activeSessionId)?.session_id
      : undefined
    if (!sessionId) {
      const pick = active ?? trained[0].session_id
      lastAutoRef.current = pick
      setSessionId(pick)
      setTab('Forecast')
    } else if (active && sessionId === lastAutoRef.current && sessionId !== active) {
      // Planning resolved later — upgrade our auto-pick to the active session.
      lastAutoRef.current = active
      setSessionId(active)
    }
  }, [sessions, sessionId, activeSessionId])

  useEffect(() => {
    if (!sessionId) return
    setLoading(true)
    setLoadError(null)
    setSelectedSku(null)
    const failedParts: string[] = []
    Promise.all([
      // One call, not three: `/metrics` and `/inventory` are slices of this
      // very payload, and the policy backtest and the demand-risk bands are two
      // more. Asking separately would have downloaded the metric rows twice.
      getTrainingResults(sessionId).catch(() => {
        failedParts.push(t('skus.part_metrics'), t('skus.part_inventory'))
        return {} as TrainingResults
      }),
      getQuality(sessionId).catch(() => { failedParts.push(t('skus.part_data_quality')); return {} }),
      // Live stock/coverage/signal. Failing soft — the panels fall back to the
      // training recommendation if this is unavailable, so no error is surfaced.
      getInventoryStatus(sessionId, 0.95, { silent: true }).catch(() => ({ items: [], coverage_unit: undefined })),
    ]).then(([res, q, status]) => {
      const rows = res.metrics?.rows ?? []
      setMetrics(rows)
      setInventory(res.inventory?.recommendations ?? [])
      // `?? null` and `?? {}`, never a zeroed stand-in: a session trained before
      // these existed has nothing to say, and the panels render nothing at all
      // rather than a simulation that never ran.
      setPolicyBacktest(res.policy_backtest ?? null)
      setDemandRisk(res.demand_risk ?? {})
      setInvStatus(status.items ?? [])
      setCoverageUnit(status.coverage_unit)
      setQuality(q as QualityReport)
      if (failedParts.length) {
        setLoadError(`${t('skus.err_load_failed_prefix')}: ${failedParts.join(', ')}. ${t('skus.err_load_failed_suffix')}`)
      }
    }).finally(() => setLoading(false))
  }, [sessionId, t])

  // One pass instead of one full scan of `metrics` per card. With 2.000 SKUs
  // and ~4 rows each, the per-card `metrics.filter(...)` was 2.000 × 8.000
  // comparisons on every keystroke in the search box — before React had even
  // started rendering.
  const metricsBySku = useMemo(() => {
    const map = new Map<string, MetricRow[]>()
    for (const row of metrics) {
      if (!row.sku) continue
      const list = map.get(row.sku)
      if (list) list.push(row)
      else map.set(row.sku, [row])
    }
    return map
  }, [metrics])

  const recBySku = useMemo(
    () => new Map(inventory.map(r => [r.sku, r])),
    [inventory],
  )
  const statusBySku  = useMemo(() => new Map(invStatus.map(i => [i.sku, i])), [invStatus])

  // Resolve the semáforo for a SKU: prefer the live inventory_stock signal,
  // fall back to the training-time recommendation when no live row exists.
  const signalForSku = useCallback((sku: string): InventorySignal | undefined => {
    const live = statusBySku.get(sku)
    if (live) return live.signal
    const rec = recBySku.get(sku)
    return rec ? ACTION_SIGNAL[rec.action] : undefined
  }, [statusBySku, recBySku])

  // Everything a list row shows, computed once per load instead of per render.
  // Display only: the signal, quantity and coverage are the API's own.
  const rowInfo = useMemo(() => {
    const map = new Map<string, SkuRowInfo>()
    metricsBySku.forEach((rows, sku) => {
      const live = statusBySku.get(sku)
      const qty = live?.recommended_qty ?? null
      map.set(sku, {
        signal: signalForSku(sku),
        qty,
        spark: (live?.stock_history ?? []).slice(-30).map(h => h.stock),
        accuracy: championError(rows).accuracy,
        impact: (qty ?? 0) * (live?.unit_cost ?? 1),
        coverage: live?.coverage_days ?? null,
        name: live?.display_name ?? null,
      })
    })
    return map
  }, [metricsBySku, statusBySku, signalForSku])

  // The whole catalogue, then what the search lets through, in the chosen order.
  const catalogue = useMemo(() => Array.from(metricsBySku.keys()), [metricsBySku])
  const skus = useMemo(() => {
    const q = search.trim().toLowerCase()
    const matched = q
      ? catalogue.filter(s => s.toLowerCase().includes(q) || (rowInfo.get(s)?.name ?? '').toLowerCase().includes(q))
      : catalogue
    return sortSkus(matched, rowInfo, sort)
  }, [catalogue, search, rowInfo, sort])

  // Land on the product that needs attention first, not on whichever the file
  // listed first. A manual pick is never overridden.
  useEffect(() => {
    if (!selectedSku && !loading && sessionId && skus.length) setSelectedSku(skus[0])
  }, [selectedSku, loading, sessionId, skus])


  const skuMetrics   = useMemo(() => metrics.filter(r => r.sku === selectedSku), [metrics, selectedSku])
  const skuInventory = useMemo(() => inventory.find(r => r.sku === selectedSku), [inventory, selectedSku])
  const skuQuality   = useMemo(() => selectedSku ? quality[selectedSku] : undefined, [quality, selectedSku])
  const qualityWarnings = useQualityWarnings()
  const skuWarnings  = skuQuality ? qualityWarnings(skuQuality) : []
  const skuStatus    = useMemo(() => selectedSku ? statusBySku.get(selectedSku) : undefined, [statusBySku, selectedSku])
  // Both keyed by the raw SKU, and both legitimately missing for a SKU whose
  // model produced no rolling-origin backtest — the Inventory tab simply omits
  // the block rather than filling it in.
  const skuPolicy    = useMemo(
    () => (selectedSku ? policyBacktest?.by_sku?.[selectedSku] : undefined),
    [policyBacktest, selectedSku],
  )
  const skuRisk      = useMemo(
    () => (selectedSku ? demandRisk[selectedSku] : undefined),
    [demandRisk, selectedSku],
  )
  const seriesType   = skuQuality?.series_type ?? 'unknown'
  const skuColor     = SERIES_COLOR[seriesType] ?? SERIES_COLOR.unknown
  // Accuracy (1 − WAPE) of the model this SKU's chart is actually drawn from —
  // the single discreet figure next to the chart header. Chosen by makeChampionRank,
  // not by WAPE: picking the lowest-WAPE row here would quote the accuracy of a
  // model the user is not looking at, and the two differ whenever being short
  // costs more than being long.
  const skuAccuracy  = useMemo(() => championError(skuMetrics).accuracy, [skuMetrics])

  // Bulk export all SKUs
  const handleBulkExport = useCallback(async () => {
    if (!sessionId || !skus.length) return
    setBulkExporting(true)
    setBulkProgress(0)
    setBulkFailed([])
    const failed: string[] = []
    try {
      const sheets: { name: string; rows: (string | number | null)[][] }[] = []
      for (let i = 0; i < skus.length; i++) {
        const sku = skus[i]
        try {
          const d = await getSkuIntelligence(sessionId, sku, {})
          const rows: (string | number | null)[][] = [
            ['date', 'historical', 'forecast_p50', 'lower', 'upper'],
            ...d.historical.map(p => [p.date, p.value, null, null, null] as (string | number | null)[]),
            ...d.forecast.map(p => {
              const fp = p as unknown as Record<string, number | null | undefined>
              return [p.date, null, p.value, fp['lower'] ?? fp['q10'] ?? null, fp['upper'] ?? fp['q90'] ?? null] as (string | number | null)[]
            }),
          ]
          sheets.push({ name: sku, rows })
        } catch { failed.push(sku) }
        setBulkProgress(i + 1)
      }
      await downloadWorkbook(`forecast_all_skus_${sessionId.slice(0, 8)}.xlsx`, sheets)
      if (failed.length) setBulkFailed(failed)
    } finally {
      setBulkExporting(false)
    }
  }, [sessionId, skus])

  const refresh = useCallback(() => {
    const id = sessionId
    setSessionId(null)
    setTimeout(() => setSessionId(id), 10)
  }, [sessionId])
  const pickSku = useCallback((sku: string) => { setSelectedSku(sku); setTab('Forecast') }, [])

  // One list, two homes: a column on desktop, a bottom sheet on phones.
  const renderList = (touch: boolean, afterPick?: () => void) => (
    <SkuList
      items={skus}
      total={catalogue.length}
      info={rowInfo}
      search={search}
      onSearch={setSearch}
      sort={sort}
      onSort={setSort}
      selected={selectedSku}
      onSelect={sku => { pickSku(sku); afterPick?.() }}
      touch={touch}
      height={touch ? 'min(62vh, 520px)' : undefined}
    />
  )

  if (narrow) {
    return (
      <PronosticosMobile
        view={view}
        onView={setView}
        showTechnical={showTechnical}
        sessions={sessions}
        sessLoading={sessLoading}
        sessionId={sessionId}
        onSelectSession={id => { setSessionId(id); setTab('Forecast'); setCompareMode(false); setCmpSessionIds([]) }}
        onRefresh={refresh}
        sessError={sessError}
        onRetrySessions={reloadSessions}
        onDismissSessError={() => setSessError(null)}
        loadError={loadError}
        onDismissLoadError={() => setLoadError(null)}
        loading={loading}
        catalogueCount={catalogue.length}
        renderList={renderList}
        quality={quality}
        signalForSku={signalForSku}
        selectedSku={selectedSku}
        tab={tab}
        onTab={setTab}
        isDark={isDark}
        skuMetrics={skuMetrics}
        skuInventory={skuInventory}
        skuStatus={skuStatus}
        coverageUnit={coverageUnit}
        skuPolicy={skuPolicy}
        skuRisk={skuRisk}
        skuAccuracy={skuAccuracy}
        skuWarnings={skuWarnings}
        showSkuStats={showSkuStats}
        onToggleSkuStats={() => setShowSkuStats(v => !v)}
        compareMode={compareMode}
        onToggleCompare={() => { setCompareMode(v => !v); if (compareMode) setCmpSessionIds([]) }}
        cmpSessionIds={cmpSessionIds}
        onCmpSessionIds={setCmpSessionIds}
        skus={skus}
        onSku={setSelectedSku}
        bulkExporting={bulkExporting}
        bulkProgress={bulkProgress}
        bulkFailed={bulkFailed}
        onBulkExport={handleBulkExport}
        policyBacktest={policyBacktest}
        catalogueSize={metricsBySku.size}
      />
    )
  }

  const noProducts = !!sessionId && !loading && catalogue.length === 0
  const displayName = skuStatus?.display_name && skuStatus.display_name !== selectedSku ? skuStatus.display_name : null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 'calc(100vh - 56px - var(--section-tabs-h, 0px))' }}>

      {/* Top toolbar. The top bar and the tab strip already say "Pronósticos",
          so it carries only the controls. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, justifyContent: 'space-between', paddingBottom: 16, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <ViewToggle value={view} onChange={setView} />
          {showTechnical && (
            <Link href="/precision" style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none' }}>
              {t('precision.link_from_forecasts')}
            </Link>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {/* Bulk export — technical view. Kept on screen while a run is in
              progress even if the viewer switches away, so its progress and
              its failure count are never hidden mid-export. */}
          {sessionId && catalogue.length > 0 && (showTechnical || bulkExporting) && (
            <button
              onClick={handleBulkExport}
              disabled={bulkExporting}
              title={t('skus.export_all_skus_title')}
              style={{
                all: 'unset', cursor: bulkExporting ? 'default' : 'pointer',
                display: 'flex', alignItems: 'center', gap: 5,
                padding: '4px 10px', borderRadius: 7, fontSize: 11,
                border: '1px solid var(--border)', color: 'var(--dim)',
                background: 'var(--surface)', opacity: bulkExporting ? 0.7 : 1,
              }}
            >
              {bulkExporting
                ? <><Loader2 size={11} style={{ animation: 'spin 1s linear infinite' }} /> {bulkProgress} / {skus.length} {t('skus.skus_count_plural')}…</>
                : <><FileSpreadsheet size={11} /> {t('skus.btn_export_all_skus')}</>
              }
            </button>
          )}
          {bulkFailed.length > 0 && !bulkExporting && (
            <span style={{ fontSize: 10, color: '#D07878' }} title={bulkFailed.join(', ')}>
              {bulkFailed.length} {bulkFailed.length !== 1 ? t('skus.skus_failed_plural') : t('skus.skus_failed_singular')}
            </span>
          )}

          {/* Compare toggle — technical view: comparing two training runs is
              an analyst's question, not a buyer's. */}
          {sessionId && showTechnical && has(completedSessions, 2) && (
            <button
              data-tour="skus.compare"
              onClick={() => { setCompareMode(v => !v); if (compareMode) setCmpSessionIds([]) }}
              title={t('skus.compare_sessions_title')}
              style={{
                all: 'unset', cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 5,
                padding: '4px 10px', borderRadius: 7, fontSize: 11,
                border: `1px solid ${compareMode ? 'var(--accent)' : 'var(--border)'}`,
                color: compareMode ? 'var(--accent)' : 'var(--dim)',
                background: compareMode ? 'color-mix(in srgb, var(--accent) 8%, transparent)' : 'var(--surface)',
              }}
            >
              <GitCompare size={11} /> {t('skus.btn_compare')}
            </button>
          )}

          {sessLoading ? <Spinner size={13} /> : (
            <SessionSelector tourAnchor="skus.session" sessions={sessions} selected={sessionId} onSelect={id => { setSessionId(id); setTab('Forecast'); setCompareMode(false); setCmpSessionIds([]) }} />
          )}
          {sessionId && (
            <Button variant="ghost" size="sm" icon={<RefreshCw size={12} />} onClick={refresh}>
              {t('skus.btn_refresh')}
            </Button>
          )}
        </div>
      </div>

      {/* Session list failed: retry reloads it. Partial-result failures carry
          their own composed sentence naming which parts are missing. */}
      {sessError != null && (
        <div style={{ marginBottom: 12 }}>
          <InlineError error={sessError} onRetry={reloadSessions} onDismiss={() => setSessError(null)} />
        </div>
      )}
      {loadError && (
        <div style={{ marginBottom: 12 }}>
          <InlineError error={new Error(loadError)} onRetry={refresh} onDismiss={() => setLoadError(null)} />
        </div>
      )}

      {/* Data problems the engine found while training. They never abort a run,
          so this is the only place the user can learn the accuracy above is
          inflated by leakage. Collapsed to one line so the chart stays in view. */}
      <RunWarningsPanel sessionId={sessionId} collapsible />
      {/* How the forecast was produced and what the ordering policy would have
          done are an analyst's questions: one collapsed line, technical view. */}
      {showTechnical && (
        <RunDetails sessionId={sessionId} backtest={policyBacktest} catalogueSize={metricsBySku.size} />
      )}

      {/* `minHeight` is a floor, not decoration: this row is `flex: 1`, i.e. it
          takes whatever is LEFT OVER after the panels above it, and those grow
          with how much the engine has to report. Without a floor the row once
          resolved to 0 and the cards' `overflow: hidden` clipped the chart away.
          The floor is the chart plus its answers, so the more StockAI has to say
          about the data the more the page scrolls, instead of the graph
          silently disappearing. */}
      <div style={{ display: 'grid', gridTemplateColumns: '320px minmax(0, 1fr)', gap: 16, flex: 1, minHeight: 700 }}>

        {/* Product list */}
        <div style={{
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 12, overflow: 'hidden', display: 'flex', flexDirection: 'column',
          position: 'sticky', top: 0, alignSelf: 'start', height: 'min(calc(100vh - 140px), 760px)', minHeight: 420,
        }}>
          {!sessionId ? (
            /* Nothing trained yet: point at the action that creates the data. */
            <div style={{ padding: 14 }}>
              <EmptyState
                compact
                icon={<Package size={20} />}
                title={t('skus.empty_title')}
                body={t('skus.empty_body')}
                actions={[{ label: t('skus.empty_cta'), href: '/ventas' }]}
              />
            </div>
          ) : loading ? (
            <div style={{ padding: 12 }}>
              <LoadingState label={t('skus.loading_label')}>
                <SkeletonTable rows={7} columns={1} />
              </LoadingState>
            </div>
          ) : noProducts ? (
            <div style={{ padding: 14 }}>
              <EmptyState
                compact
                icon={<Package size={20} />}
                title={t('skus.empty_no_skus_found')}
                body={t('skus.empty_no_products_body')}
                actions={[{ label: t('skus.empty_cta'), href: '/ventas' }]}
              />
            </div>
          ) : renderList(false)}
        </div>

        {/* Detail panel */}
        <div style={{
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 12, overflow: 'hidden', display: 'flex', flexDirection: 'column', minWidth: 0,
        }}>
          {!selectedSku ? (
            <PanelPlaceholder message={sessionId ? t('skus.empty_select_sku_from_list') : t('skus.empty_no_session_selected')} />
          ) : (
            <>
              {/* SKU header */}
              <div data-tour="skus.header" style={{
                padding: '14px 16px', borderBottom: '1px solid var(--border)',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
                  <div style={{
                    width: 32, height: 32, borderRadius: 9, flexShrink: 0,
                    background: skuColor + '18',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}>
                    <Package size={14} color={skuColor} />
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 16, fontWeight: 700, overflowWrap: 'anywhere' }}>
                      {selectedSku}
                      {displayName && <span style={{ fontWeight: 400, color: 'var(--dim)', fontSize: 13, marginLeft: 8 }}>{displayName}</span>}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 1 }}>
                      {skuQuality ? (
                        <>
                          <span style={{ color: skuColor }}>{seriesTypeLabel(t, skuQuality.series_type)}</span>
                          {/* Row count and quality score are the Quality tab's
                              numbers; the buyer view says the same thing as a
                              confidence cue under the chart. */}
                          {showTechnical && (
                            <>
                              {' · '}
                              {skuQuality.n_rows} {t('skus.rows_label')} · {pct(skuQuality.quality_score)} {t('skus.quality_label_lower')}
                            </>
                          )}
                        </>
                      ) : t('skus.no_quality_data')}
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
                  {/* Backtest accuracy is a model's score — technical view.
                      Section 18: a number a buyer cannot act on. */}
                  {showTechnical && skuAccuracy != null && (
                    <span style={{ fontSize: 11, color: 'var(--dim)' }}>
                      {t('skus.accuracy_label')}: <strong style={{ color: 'var(--fg)' }}>{skuAccuracy}%</strong>
                    </span>
                  )}
                  {selectedSku && signalForSku(selectedSku) && (
                    <SignalBadge signal={signalForSku(selectedSku)!} size="md" />
                  )}
                </div>
              </div>

              {/* Buyer view: the engine's warnings about this series, which in
                  the technical view live on the Quality tab. A buyer does not
                  open that tab, and short or patchy history is exactly what
                  should make them trust the curve less. */}
              {!showTechnical && skuQuality && skuWarnings.length > 0 && (
                <div style={{ padding: '8px 16px', borderBottom: '1px solid var(--border)' }}>
                  <QualityWarningList lines={skuWarnings} />
                </div>
              )}

              <TabBar
                tourAnchor="skus.tabs"
                // Metrics and Quality are an analyst's tabs, not a buyer's —
                // they exist only in the technical view.
                tabs={showTechnical
                  ? ['Forecast', 'Pattern', 'Metrics', 'Quality', 'Inventory']
                  : ['Forecast', 'Pattern', 'Inventory']}
                active={tab}
                onChange={setTab}
                labelFor={tabKey => ({
                  Forecast: t('skus.tab_forecast'),
                  Pattern: t('skus.tab_pattern'),
                  Metrics: t('skus.tab_metrics'),
                  Quality: t('skus.tab_quality'),
                  Inventory: t('skus.tab_inventory'),
                }[tabKey] ?? tabKey)}
              />

              <div style={{ flex: 1, overflow: tab === 'Forecast' && showTechnical ? 'hidden' : 'auto', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                {tab === 'Forecast' && sessionId && (
                  compareMode ? (
                    /* One chart, one time axis, every compared session on it */
                    <div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
                      <CompareView
                        sessions={sessions}
                        primaryId={sessionId}
                        extraIds={cmpSessionIds}
                        onExtraIds={setCmpSessionIds}
                        skus={skus}
                        sku={selectedSku}
                        onSku={setSelectedSku}
                        isDark={isDark}
                      />
                    </div>
                  ) : (
                    <ChartPanel
                      key={`${sessionId}-${selectedSku}`}
                      tourAnchor="skus.chart"
                      sessionId={sessionId} sku={selectedSku} isDark={isDark}
                      quality={skuQuality} showTechnical={showTechnical}
                      onSeeOrder={() => setTab('Inventory')}
                      status={skuStatus} coverageUnit={coverageUnit}
                    />
                  )
                )}
                {tab === 'Pattern' && sessionId && (
                  <SalesPatternPanel
                    key={`${sessionId}-${selectedSku}`}
                    sessionId={sessionId}
                    sku={selectedSku}
                    isDark={isDark}
                  />
                )}
                {tab === 'Metrics' && <MetricsTable rows={skuMetrics} sku={selectedSku} />}
                {tab === 'Quality' && (
                  skuQuality ? (
                    <QualityTab
                      q={skuQuality}
                      showStats={showSkuStats}
                      onToggleStats={() => setShowSkuStats(v => !v)}
                    />
                  ) : <PanelPlaceholder message={t('skus.empty_no_quality_data')} />
                )}
                {tab === 'Inventory' && (
                  skuInventory
                    ? <InventoryPanel
                        inv={skuInventory}
                        live={skuStatus}
                        coverageUnit={coverageUnit}
                        policy={skuPolicy}
                        risk={skuRisk}
                      />
                    : <div style={{ padding: 20, color: 'var(--dim)', fontSize: 13 }}>
                        {t('skus.no_inventory_recommendations')}
                      </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
