'use client'
/**
 * "Set up my inventory" — the answer to the stock/lead-time/cost wall
 * (onboarding-friction plan #1, phases 3 and 4).
 *
 * Two ways up the wall, in the order that gets people over it:
 *  1. Upload the file your system already exports (no header editing).
 *  2. Or fill in, by hand, only the products that carry your monthly purchase
 *     — ordered by money, with a bar that measures money.
 *
 * Both write to the same `inventory_stock`, so doing either moves the same
 * progress bar. The session is resolved server-side (the tenant's active
 * period) when none is passed.
 */
import { useCallback, useState } from 'react'

import SetupGapsPanel from '@/components/inventory/SetupGapsPanel'
import SignalThresholdsPanel from '@/components/inventory/SignalThresholdsPanel'
import ServiceLevelClassesPanel from '@/components/inventory/ServiceLevelClassesPanel'
import StockImportWizard from '@/components/inventory/StockImportWizard'
import { useSetupCopy } from '@/i18n/useSetupCopy'
import { useIsNarrow } from '@/hooks/useIsNarrow'

export default function InventorySetupPage() {
  const c = useSetupCopy()
  // Phone: the shell already pads the screen; a second 26px gutter left the
  // panels 250px wide. The compact header carries the title.
  const narrow = useIsNarrow()
  // Bumping the key remounts the gaps panel after an import, so the money bar
  // reflects the rows that just landed instead of the state before them.
  const [version, setVersion] = useState(0)
  const refresh = useCallback(() => setVersion(v => v + 1), [])

  return (
    <div style={{ padding: narrow ? 0 : '22px 26px', maxWidth: 1180, margin: '0 auto' }}>
      {/* The top bar / phone header already carries the title. */}
      <p style={{ fontSize: 13, color: 'var(--dim)', margin: '0 0 12px', lineHeight: 1.5 }}>
        {c('setupStock.page.subtitle')}
      </p>

      {/* Why the screen exists. It used to open straight onto two panels that
          asked for stock, cost and lead time without ever saying what they were
          for — so the honest reading was "more forms", when the actual answer is
          that a product missing any of the three cannot enter the traffic light
          at all. */}
      <p style={{
        fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.7,
        margin: narrow ? '0 0 14px' : '0 0 18px', padding: '11px 13px', maxWidth: 760,
        background: 'var(--surface-2)', border: '1px solid var(--border)',
        borderRadius: 9,
      }}>
        {c('setupStock.page.why')}
      </p>

      {/* Tour anchors sit on wrappers, not on the panels: both are shared
          components that do not forward unknown props to the DOM. */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <div data-tour="setup.import">
          <StockImportWizard onImported={refresh} />
        </div>
        <div data-tour="setup.gaps">
          <SetupGapsPanel key={version} onChanged={refresh} />
        </div>
        {/* The semáforo's own rules: when it says "order now" and when it
            says "you have too much", as multiples of the supplier's time. */}
        <SignalThresholdsPanel />
        {/* Suggested service level per ABC class: read here, applied only
            by the person, class by class. */}
        <ServiceLevelClassesPanel />
      </div>
    </div>
  )
}
