'use client'
import { useEffect } from 'react'
import { usePathname } from 'next/navigation'
import { getTenantCurrency } from '@/lib/api'
import { setActiveCurrency } from '@/lib/currency'
import Sidebar from './Sidebar'
import TopBar from './TopBar'
import AuthGuard from './AuthGuard'
import SkuSearchOverlay from './SkuSearchOverlay'
import { SidebarProvider } from '@/contexts/SidebarContext'
import { ToastProvider } from '@/contexts/ToastContext'
import { SkuSearchProvider } from '@/contexts/SkuSearchContext'
import { ConfirmProvider } from '@/components/ui/ConfirmDialog'
import { WarehousesProvider } from '@/components/inventory/WarehouseControls'
import { PlanningProvider } from '@/contexts/PlanningContext'
import { TourProvider } from '@/contexts/TourContext'
import TourOverlay from '@/components/tour/TourOverlay'
import ToastContainer from '@/components/ui/Toast'
import ApiErrorBridge from './ApiErrorBridge'
import { EntitlementsProvider } from '@/lib/entitlements'
import ReadOnlyBanner from './ReadOnlyBanner'
import VerifyEmailBanner from './VerifyEmailBanner'
import MobileNavButton from '@/components/mobile/MobileNavButton'
import DesktopOnlyNotice from '@/components/mobile/DesktopOnlyNotice'
import { useIsNarrow } from '@/hooks/useIsNarrow'

export default function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <EntitlementsProvider>
      <WarehousesProvider>
      <PlanningProvider>
        <ToastProvider>
          <ConfirmProvider>
          <SidebarProvider>
            <SkuSearchProvider>
              <TourProvider>
                <Shell>{children}</Shell>
                <ToastContainer />
                <ApiErrorBridge />
                <SkuSearchOverlay />
                {/* Last child, so its overlay sits above the app chrome but
                    below nothing that matters — it must never cover a toast
                    reporting that something failed. */}
                <TourOverlay />
              </TourProvider>
            </SkuSearchProvider>
          </SidebarProvider>
          </ConfirmProvider>
        </ToastProvider>
      </PlanningProvider>
      </WarehousesProvider>
      </EntitlementsProvider>
    </AuthGuard>
  )
}

/**
 * The chrome itself, split out only so it can call `useIsNarrow()` — the hook
 * has to run *inside* `SidebarProvider`, since the drawer trigger it renders
 * reads that context.
 *
 * Narrow viewports get exactly two changes here:
 *   · a 44px hamburger beside the top bar, because the sidebar has become an
 *     off-canvas drawer and would otherwise be unreachable;
 *   · `overflowX: hidden` on the scroll container, so one over-wide table
 *     cannot turn every vertical swipe on every page into a fight with a
 *     horizontal scrollbar.
 * `TopBar` itself is untouched — it is shared with four other screens.
 */
/**
 * Loads the tenant's currency once, for the whole app.
 *
 * `formatMoney` reads a module-level value rather than a hook, because it is
 * called from chart builders and export helpers that are not components. Without
 * this, every screen would render the default (colones) until the user happened
 * to open Mi cuenta, and a dollar tenant would see the wrong symbol everywhere.
 *
 * Renders nothing and never blocks: the figures start in the default symbol and
 * settle a moment later, which beats holding the whole shell on one request.
 */
function CurrencyBoot() {
  useEffect(() => {
    getTenantCurrency({ silent: true })
      .then(d => setActiveCurrency(d.current))
      .catch(() => { /* keep the default rather than surfacing a toast */ })
  }, [])
  return null
}

function Shell({ children }: { children: React.ReactNode }) {
  const narrow   = useIsNarrow()
  const pathname = usePathname()

  return (
    <div className="app-shell">
      <CurrencyBoot />
      <Sidebar />
      <div className="main-content" style={narrow ? { minWidth: 0 } : undefined}>
        <div style={{ display: 'flex', alignItems: 'stretch', flexShrink: 0, minWidth: 0 }}>
          {narrow && <MobileNavButton />}
          <div style={{ flex: 1, minWidth: 0 }}>
            <TopBar />
          </div>
        </div>
        <div
          className="page-content"
          style={narrow ? { overflowX: 'hidden', padding: 12 } : undefined}
        >
          <ReadOnlyBanner />
          <VerifyEmailBanner />
          <DesktopOnlyNotice />
          {/* One entrance for every route, instead of the four screens that
              each hand-rolled their own fade at a different duration. Keyed on
              the pathname so it runs on navigation and not on every state
              update inside a screen. The banners stay outside: they are
              persistent chrome, not route content. */}
          <div key={pathname} className="page-enter">{children}</div>
        </div>
      </div>
    </div>
  )
}
