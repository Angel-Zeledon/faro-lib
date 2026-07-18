'use client'
import { createContext, useContext, useEffect, useState, useCallback } from 'react'

// ── Context ───────────────────────────────────────────────────────────────────
// Faro is positioned specifically for distributors (see CLAUDE.md) — this
// context now only carries the advanced-mode toggle. It used to also carry a
// retail/distributor/manufacturer business-profile picker (/perfil), but that
// picker had no entry point anywhere in the app and was removed.
interface BusinessProfileCtx {
  advancedMode:    boolean
  setAdvancedMode: (v: boolean) => void
  isLoaded:        boolean
}

const Ctx = createContext<BusinessProfileCtx>({
  advancedMode: false, setAdvancedMode: () => {}, isLoaded: false,
})

export function BusinessProfileProvider({ children }: { children: React.ReactNode }) {
  const [advancedMode, setAdvancedModeState] = useState(false)

  useEffect(() => {
    const saved = localStorage.getItem('adv') === '1'
    setAdvancedModeState(saved)
  }, [])

  const handleSet = useCallback((v: boolean) => {
    setAdvancedModeState(v)
    localStorage.setItem('adv', v ? '1' : '0')
    import('@/lib/api').then(({ updatePreferences }) => {
      updatePreferences({ advanced_mode: v }).catch(() => {})
    })
  }, [])

  return (
    <Ctx.Provider value={{
      advancedMode,
      setAdvancedMode: handleSet,
      isLoaded:        true,
    }}>
      {children}
    </Ctx.Provider>
  )
}

export const useBusinessProfile = () => useContext(Ctx)
