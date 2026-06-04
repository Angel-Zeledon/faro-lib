'use client'
import { createContext, useContext, useState, useEffect } from 'react'

interface SidebarCtx { collapsed: boolean; toggle: () => void }
const SidebarContext = createContext<SidebarCtx>({ collapsed: false, toggle: () => {} })

export function SidebarProvider({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false)

  useEffect(() => {
    setCollapsed(localStorage.getItem('fp_sidebar_collapsed') === '1')
  }, [])

  const toggle = () =>
    setCollapsed(v => {
      const next = !v
      localStorage.setItem('fp_sidebar_collapsed', next ? '1' : '0')
      return next
    })

  return <SidebarContext.Provider value={{ collapsed, toggle }}>{children}</SidebarContext.Provider>
}

export const useSidebar = () => useContext(SidebarContext)
