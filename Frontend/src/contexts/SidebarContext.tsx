'use client'
import { createContext, useContext, useState, useEffect, useCallback } from 'react'

interface SidebarCtx {
  /** Desktop: the rail is narrowed to icons. Persisted per browser. */
  collapsed: boolean
  toggle: () => void
  /** Narrow viewports only: the sidebar is off-canvas and this is whether it is
   *  currently slid in over the page. Deliberately NOT persisted — a drawer
   *  that reopens itself on the next page load is a bug, not a preference. */
  drawerOpen: boolean
  openDrawer:  () => void
  closeDrawer: () => void
}

const SidebarContext = createContext<SidebarCtx>({
  collapsed: false, toggle: () => {},
  drawerOpen: false, openDrawer: () => {}, closeDrawer: () => {},
})

export function SidebarProvider({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)

  useEffect(() => {
    setCollapsed(localStorage.getItem('fp_sidebar_collapsed') === '1')
  }, [])

  const toggle = () =>
    setCollapsed(v => {
      const next = !v
      localStorage.setItem('fp_sidebar_collapsed', next ? '1' : '0')
      return next
    })

  const openDrawer  = useCallback(() => setDrawerOpen(true), [])
  const closeDrawer = useCallback(() => setDrawerOpen(false), [])

  return (
    <SidebarContext.Provider value={{ collapsed, toggle, drawerOpen, openDrawer, closeDrawer }}>
      {children}
    </SidebarContext.Provider>
  )
}

export const useSidebar = () => useContext(SidebarContext)
