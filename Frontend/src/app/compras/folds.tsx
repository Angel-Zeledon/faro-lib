'use client'
/**
 * The two folds that keep the Panel's first view short.
 *
 * Both are presentation over data the Panel already fetched: nothing here asks
 * the backend for anything, and nothing is removed — everything folded stays
 * one tap away, on desktop and on a phone alike.
 *
 *  - `ReviewStrip`   one strip, "Data to review (N)", in place of the stack of
 *                    banners (assumptions, late suppliers, unconfirmed
 *                    deliveries, suppliers with no contact). It opens by itself
 *                    when any item inside is critical.
 *  - `MoreAnalysis`  one closed-by-default section around the optimizer plan,
 *                    demand peaks, demand changes, recommendations and capital.
 *
 * The stale-data banner is deliberately NOT folded into either: when the stock
 * or sales behind the semáforo have gone blind, every recommendation on the
 * screen is unreliable, and that caveat has to stay in plain sight.
 */
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { ChevronDown, ChevronRight, EyeOff, X } from 'lucide-react'
import type { DataFreshnessInfo } from '@/lib/api'
import type { MorningBriefing, OptimizationResponse } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { C } from './shared'

/** Open/closed, remembered for this browser tab only (sessionStorage). The
 *  storage can be missing or throw (private window), so every access is
 *  guarded and the fold simply starts at its default. `explicit` says whether
 *  the user has chosen, so an automatic default never overrides a choice. */
export function useTabFold(key: string): { open: boolean | null; set: (v: boolean) => void } {
  const [open, setOpen] = useState<boolean | null>(null)
  useEffect(() => {
    try {
      const v = window.sessionStorage.getItem(key)
      if (v === '1') setOpen(true)
      else if (v === '0') setOpen(false)
    } catch { /* storage unavailable: keep the default */ }
  }, [key])
  const set = useCallback((v: boolean) => {
    setOpen(v)
    try { window.sessionStorage.setItem(key, v ? '1' : '0') } catch { /* ignore */ }
  }, [key])
  return { open, set }
}

const headStyle = (compact: boolean): React.CSSProperties => ({
  all: 'unset', boxSizing: 'border-box', width: '100%', cursor: 'pointer',
  display: 'flex', alignItems: 'center', gap: 10,
  padding: compact ? '10px 12px' : '11px 16px', minHeight: compact ? 52 : 44,
})

const badgeStyle: React.CSSProperties = {
  fontSize: 11.5, fontWeight: 700, lineHeight: 1, padding: '3px 8px', borderRadius: 20,
  color: C.muted, background: 'var(--surface-2)', fontVariantNumeric: 'tabular-nums',
}

/** The one banner-like thing that stays: the stock or sales behind the
 *  semáforo have gone blind, so every recommendation below is unreliable. One
 *  slim neutral line, with the way out, that can be dismissed for this tab. */
export function StaleLine({ freshness, onDismiss, compact = false }: {
  freshness: DataFreshnessInfo
  onDismiss: () => void
  compact?: boolean
}) {
  const { t } = useLanguage()
  if (freshness.semaphore !== 'degraded') return null
  const staleStock = freshness.degraded_by.includes('stock')
  const staleSales = freshness.degraded_by.includes('sales')
  const parts: string[] = []
  if (staleStock && freshness.stock.age_days != null) parts.push(t('freshness.banner_stock_line', { days: freshness.stock.age_days }))
  if (staleSales && freshness.sales.age_days != null) parts.push(t('freshness.banner_sales_line', { days: freshness.sales.age_days }))
  const link: React.CSSProperties = { color: 'var(--accent)', fontWeight: 600, textDecoration: 'none', whiteSpace: 'nowrap' }
  return (
    <div role="status" style={{
      display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: compact ? 14 : 24,
      padding: compact ? '10px 0' : '10px 0', borderBottom: `1px solid ${C.border}`,
      fontSize: compact ? 12.5 : 13, color: C.muted, lineHeight: 1.55,
    }}>
      <EyeOff size={15} color="var(--dim)" aria-hidden="true" style={{ flexShrink: 0, marginTop: 3 }} />
      <span style={{ flex: 1, minWidth: 0 }}>
        <strong style={{ color: C.text, fontWeight: 600 }}>{t('freshness.banner_title')}</strong>{' '}
        {parts.join(' ')}{' '}
        {staleSales && <Link href="/ventas" style={link}>{t('freshness.banner_cta_sales')}</Link>}
        {staleSales && staleStock && ' · '}
        {staleStock && <Link href="/inventario" style={link}>{t('freshness.banner_cta_stock')}</Link>}
      </span>
      <button type="button" onClick={onDismiss} aria-label={t('hoy.stale_line_dismiss')} title={t('hoy.stale_line_dismiss')} style={{
        all: 'unset', cursor: 'pointer', color: 'var(--dim)', display: 'flex', alignItems: 'center',
        justifyContent: 'center', width: compact ? 44 : 28, height: compact ? 44 : 28, margin: compact ? '-12px -8px -12px 0' : '-4px 0',
      }}>
        <X size={15} aria-hidden="true" />
      </button>
    </div>
  )
}

export interface AnalysisSection { id: string; labelKey: string }

/** Which of the folded sections have something to show — the same guards the
 *  sections themselves use, so the badge can never promise an empty fold. The
 *  recommendations count is passed in because the filter lives in page.tsx. */
export function analysisSections(
  briefing: MorningBriefing,
  optimization: OptimizationResponse | null,
  recsCount: number,
): AnalysisSection[] {
  const out: AnalysisSection[] = []
  if (optimization && (optimization.orders.length > 0 || optimization.transfers.length > 0
                       || optimization.status === 'fallback')) {
    out.push({ id: 'plan', labelKey: 'hoy.more_label_plan' })
  }
  if ((briefing.demand_spikes?.length ?? 0) > 0) out.push({ id: 'spikes', labelKey: 'hoy.more_label_spikes' })
  if (briefing.demand_changes.length > 0) out.push({ id: 'changes', labelKey: 'hoy.more_label_changes' })
  if (recsCount > 0) out.push({ id: 'recs', labelKey: 'hoy.more_label_recs' })
  if (briefing.overstocked.length > 0 && briefing.kpis.capital_in_overstock > 0) {
    out.push({ id: 'capital', labelKey: 'hoy.more_label_capital' })
  }
  return out
}

export function MoreAnalysis({ sections, compact = false, children }: {
  sections: AnalysisSection[]
  compact?: boolean
  children: React.ReactNode
}) {
  const { t } = useLanguage()
  const { open, set } = useTabFold('compras.more_open')
  if (sections.length === 0) return null
  const isOpen = open === true   // closed unless this tab opened it
  return (
    <section data-tour="hoy.more" style={{
      marginTop: compact ? 14 : 28, marginBottom: compact ? 14 : 28, minWidth: 0,
      border: `1px solid ${C.border}`, borderRadius: compact ? 12 : 10, background: C.surface,
    }}>
      <button type="button" onClick={() => set(!isOpen)} aria-expanded={isOpen} style={headStyle(compact)}>
        {isOpen
          ? <ChevronDown size={16} color={C.dim} aria-hidden="true" style={{ flexShrink: 0 }} />
          : <ChevronRight size={16} color={C.dim} aria-hidden="true" style={{ flexShrink: 0 }} />}
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: compact ? 15 : 14, fontWeight: 700, color: C.text }}>
              {t('hoy.more_analysis_title')}
            </span>
            <span style={badgeStyle}>{sections.length}</span>
          </span>
          {!isOpen && (
            <span style={{ display: 'block', fontSize: 12, color: C.dim, marginTop: 3, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
              {sections.map(s => t(s.labelKey)).join(' · ')}
            </span>
          )}
        </span>
      </button>
      {isOpen && (
        <div style={{ padding: compact ? '4px 12px 14px' : '4px 20px 6px', minWidth: 0 }}>{children}</div>
      )}
    </section>
  )
}
