'use client'
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
              <Shell>{children}</Shell>
              <ToastContainer />
              <ApiErrorBridge />
              <SkuSearchOverlay />
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
function Shell({ children }: { children: React.ReactNode }) {
  const narrow = useIsNarrow()

  return (
    <div className="app-shell">
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
          {children}
        </div>
      </div>
    </div>
  )
}
