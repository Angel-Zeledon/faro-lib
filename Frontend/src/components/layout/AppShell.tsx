'use client'
import Sidebar from './Sidebar'
import TopBar from './TopBar'
import AuthGuard from './AuthGuard'
import { SidebarProvider } from '@/contexts/SidebarContext'
import { ActiveSessionProvider } from '@/contexts/ActiveSessionContext'
import { ToastProvider } from '@/contexts/ToastContext'
import { BusinessProfileProvider } from '@/contexts/BusinessProfileContext'
import ToastContainer from '@/components/ui/Toast'

export default function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <BusinessProfileProvider>
        <ToastProvider>
          <SidebarProvider>
            <ActiveSessionProvider>
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
            </ActiveSessionProvider>
          </SidebarProvider>
        </ToastProvider>
      </BusinessProfileProvider>
    </AuthGuard>
  )
}
