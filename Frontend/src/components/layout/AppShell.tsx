'use client'
import Sidebar from './Sidebar'
import TopBar from './TopBar'
import AuthGuard from './AuthGuard'
import SkuSearchOverlay from './SkuSearchOverlay'
import { SidebarProvider } from '@/contexts/SidebarContext'
import { ActiveSessionProvider } from '@/contexts/ActiveSessionContext'
import { ToastProvider } from '@/contexts/ToastContext'
import { BusinessProfileProvider } from '@/contexts/BusinessProfileContext'
import { SkuSearchProvider } from '@/contexts/SkuSearchContext'
import { ConfirmProvider } from '@/components/ui/ConfirmDialog'
import { WarehousesProvider } from '@/components/inventory/WarehouseControls'
import ToastContainer from '@/components/ui/Toast'
import ApiErrorBridge from './ApiErrorBridge'
import { EntitlementsProvider } from '@/lib/entitlements'
import ReadOnlyBanner from './ReadOnlyBanner'

export default function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <EntitlementsProvider>
      <BusinessProfileProvider>
      <WarehousesProvider>
        <ToastProvider>
          <ConfirmProvider>
          <SidebarProvider>
            <ActiveSessionProvider>
              <SkuSearchProvider>
                <div className="app-shell">
                  <Sidebar />
                  <div className="main-content">
                    <TopBar />
                    <div className="page-content">
                      <ReadOnlyBanner />
                      {children}
                    </div>
                  </div>
                </div>
                <ToastContainer />
                <ApiErrorBridge />
                <SkuSearchOverlay />
              </SkuSearchProvider>
            </ActiveSessionProvider>
          </SidebarProvider>
          </ConfirmProvider>
        </ToastProvider>
      </WarehousesProvider>
      </BusinessProfileProvider>
      </EntitlementsProvider>
    </AuthGuard>
  )
}
