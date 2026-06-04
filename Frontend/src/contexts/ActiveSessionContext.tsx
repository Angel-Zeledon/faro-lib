'use client'
import { createContext, useContext, useState } from 'react'

interface ActiveSessionCtx {
  activeSessionId: string | null
  setActiveSessionId: (id: string | null) => void
}

const ActiveSessionContext = createContext<ActiveSessionCtx>({
  activeSessionId: null,
  setActiveSessionId: () => {},
})

export function ActiveSessionProvider({ children }: { children: React.ReactNode }) {
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  return (
    <ActiveSessionContext.Provider value={{ activeSessionId, setActiveSessionId }}>
      {children}
    </ActiveSessionContext.Provider>
  )
}

export const useActiveSession = () => useContext(ActiveSessionContext)
