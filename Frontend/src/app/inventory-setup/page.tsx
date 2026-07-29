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
import StockImportWizard from '@/components/inventory/StockImportWizard'
import { useSetupCopy } from '@/i18n/useSetupCopy'

export default function InventorySetupPage() {
  const c = useSetupCopy()
  // Bumping the key remounts the gaps panel after an import, so the money bar
  // reflects the rows that just landed instead of the state before them.
  const [version, setVersion] = useState(0)
  const refresh = useCallback(() => setVersion(v => v + 1), [])

  return (
    <div style={{ padding: '22px 26px', maxWidth: 1180, margin: '0 auto' }}>
      <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--text)', margin: 0 }}>
        {c('setupStock.page.title')}
      </h1>
      <p style={{ fontSize: 13, color: 'var(--dim)', margin: '6px 0 18px', lineHeight: 1.5 }}>
        {c('setupStock.page.subtitle')}
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
      </div>
    </div>
  )
}
