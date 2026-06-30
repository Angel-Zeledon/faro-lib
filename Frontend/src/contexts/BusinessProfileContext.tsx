'use client'
import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import type { BusinessProfile } from '@/lib/types'

// ── Fixed distributor terms ───────────────────────────────────────────────────
export const DISTRIBUTOR_TERMS: Record<string, string> = {
  target:          'ventas',
  group:           'cliente / ruta',
  sku:             'SKU',
  order_action:    'Pedir a proveedor',
  demand:          'demanda de clientes',
  inventory_title: 'Inventario y Reposición',
  coverage:        'Días de cobertura',
  supplier:        'Proveedor',
  profile_label:   'Distribución',
  profile_desc:    'Distribuidores · Mayoristas · Logística',
  forecast_target: 'Cuánto pedirán tus clientes',
  hero_tagline:    'Tus próximas compras, calculadas.',
}

// Legacy exports kept for backward compatibility — both always point to distributor
export const PROFILE_TERMS = {
  retail:       DISTRIBUTOR_TERMS,
  distributor:  DISTRIBUTOR_TERMS,
  manufacturer: DISTRIBUTOR_TERMS,
} as Record<BusinessProfile, Record<string, string>>

// Legacy nav rules — no longer used but kept to avoid import errors
export const PROFILE_NAV_RULES: Record<string, BusinessProfile[]> = {}

// ── Context ───────────────────────────────────────────────────────────────────
interface BusinessProfileCtx {
  profile:         BusinessProfile | null
  setProfile:      (p: BusinessProfile) => void
  terms:           Record<string, string>
  advancedMode:    boolean
  setAdvancedMode: (v: boolean) => void
  isLoaded:        boolean
}

const Ctx = createContext<BusinessProfileCtx>({
  profile: 'distributor', setProfile: () => {}, terms: DISTRIBUTOR_TERMS,
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
      updatePreferences({ advanced_mode: v } as any).catch(() => {})
    })
  }, [])

  return (
    <Ctx.Provider value={{
      profile:         'distributor' as const,
      setProfile:      () => {},
      terms:           DISTRIBUTOR_TERMS,
      advancedMode,
      setAdvancedMode: handleSet,
      isLoaded:        true,
    }}>
      {children}
    </Ctx.Provider>
  )
}

export const useBusinessProfile = () => useContext(Ctx)
