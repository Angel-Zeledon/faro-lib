'use client'
import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { PolicyBacktest } from '@/lib/types'
import RunLineagePanel from '@/components/ui/RunLineagePanel'
import { useLanguage } from '@/contexts/LanguageContext'
import { PolicyBacktestPanel } from './PolicyBacktestPanel'

/**
 * Technical view only: how this forecast was produced (fingerprints, the
 * configuration) and what the ordering policy would have done over real past
 * demand. One collapsed line instead of two panels stacked around the chart;
 * the chart stays the first thing on the screen.
 */
export function RunDetails({ sessionId, backtest, catalogueSize, touch = false }: {
  sessionId: string | null
  backtest: PolicyBacktest | null
  catalogueSize: number
  touch?: boolean
}) {
  const { t } = useLanguage()
  const [open, setOpen] = useState(false)
  if (!sessionId) return null
  return (
    <div style={{ marginBottom: touch ? 0 : 12 }}>
      <button
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
        style={{
          all: 'unset', cursor: 'pointer', boxSizing: 'border-box',
          display: 'flex', alignItems: 'center', gap: 6, width: touch ? '100%' : undefined,
          minHeight: touch ? 44 : 28, padding: touch ? '0 12px' : '0 10px', borderRadius: 8,
          fontSize: touch ? 14 : 12, fontWeight: 600, color: 'var(--dim)',
          border: '1px solid var(--border)', background: 'var(--surface)',
        }}
      >
        <ChevronDown size={14} aria-hidden="true" style={{ transform: open ? 'none' : 'rotate(-90deg)', transition: 'transform 0.12s' }} />
        {t('skus.run_details')}
      </button>
      {open && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 10 }}>
          <RunLineagePanel sessionId={sessionId} />
          <PolicyBacktestPanel backtest={backtest} catalogueSize={catalogueSize} />
        </div>
      )}
    </div>
  )
}
