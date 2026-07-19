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
import ToastContainer from '@/components/ui/Toast'

export default function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <BusinessProfileProvider>
        <ToastProvider>
          <SidebarProvider>
            <ActiveSessionProvider>
              <SkuSearchProvider>
                <div className="app-shell">
                  <Sidebar />
                  <div className="main-content">
                    <TopBar />
                    <div className="page-content">
                      {children}
                    </div>
                  </div>
                </div>
                <ToastContainer />
                <SkuSearchOverlay />
              </SkuSearchProvider>
            </ActiveSessionProvider>
          </SidebarProvider>
        </ToastProvider>
      </BusinessProfileProvider>
    </AuthGuard>
  )
}
