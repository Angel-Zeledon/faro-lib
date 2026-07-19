'use client'
import { createContext, useContext, useState, useCallback } from 'react'

// Shared open/close state for the global SKU command-palette (Ctrl-K / Cmd-K
// overlay). A context — rather than local state inside the overlay component
// — lets any layout-level trigger (TopBar search icon, future entry points)
// open the same overlay instance without prop drilling.
interface SkuSearchCtx {
  isOpen: boolean
  open:   () => void
  close:  () => void
  toggle: () => void
}

const Ctx = createContext<SkuSearchCtx>({
  isOpen: false,
  open:   () => {},
  close:  () => {},
  toggle: () => {},
})

export function SkuSearchProvider({ children }: { children: React.ReactNode }) {
  const [isOpen, setIsOpen] = useState(false)

  const open   = useCallback(() => setIsOpen(true), [])
  const close  = useCallback(() => setIsOpen(false), [])
  const toggle = useCallback(() => setIsOpen(v => !v), [])

  return (
    <Ctx.Provider value={{ isOpen, open, close, toggle }}>
      {children}
    </Ctx.Provider>
  )
}

export const useSkuSearch = () => useContext(Ctx)
