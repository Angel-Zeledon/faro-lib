'use client'
import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import type { BusinessProfile } from '@/lib/types'

// ── Terminology per profile ───────────────────────────────────────────────────
export const PROFILE_TERMS: Record<BusinessProfile, Record<string, string>> = {
  retail: {
    target:          'ventas',
    group:           'tienda',
    sku:             'producto',
    order_action:    'Reabastecer',
    demand:          'ventas',
    inventory_title: 'Inventario',
    coverage:        'Días de cobertura',
    supplier:        'Proveedor',
    profile_label:   'Retail',
    profile_desc:    'Tiendas · E-commerce · Supermercados',
    forecast_target: 'Cuánto venderás',
    hero_tagline:    'Tu inventario, listo para el cliente.',
  },
  distributor: {
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
    hero_tagline:    'Repón antes de quedarte sin stock.',
  },
  manufacturer: {
    target:          'demanda',
    group:           'producto terminado',
    sku:             'referencia',
    order_action:    'Comprar materia prima',
    demand:          'plan de producción',
    inventory_title: 'Materiales e Inventario',
    coverage:        'Días de cobertura',
    supplier:        'Proveedor de insumos',
    profile_label:   'Manufactura',
    profile_desc:    'Fábricas · Productores · Plantas',
    forecast_target: 'Cuánto producir',
    hero_tagline:    'De la demanda al plan de producción.',
  },
}

// ── Nav visibility per profile (routes hidden for certain profiles) ────────────
export const PROFILE_NAV_RULES: Record<string, BusinessProfile[]> = {
  '/produccion': ['manufacturer'],                          // only manufacturers
  '/inventory/suppliers': ['distributor', 'manufacturer'],  // not default retail
}

// ── Context ───────────────────────────────────────────────────────────────────
interface BusinessProfileCtx {
  profile:      BusinessProfile | null
  setProfile:   (p: BusinessProfile) => void
  terms:        Record<string, string>
  advancedMode: boolean
  setAdvancedMode: (v: boolean) => void
  isLoaded:     boolean
}

const defaultTerms = PROFILE_TERMS.distributor
const Ctx = createContext<BusinessProfileCtx>({
  profile: null, setProfile: () => {}, terms: defaultTerms,
  advancedMode: false, setAdvancedMode: () => {}, isLoaded: false,
})

export function BusinessProfileProvider({ children }: { children: React.ReactNode }) {
  const [profile,      setProfileState] = useState<BusinessProfile | null>(null)
  const [advancedMode, setAdvancedMode] = useState(false)
  const [isLoaded,     setIsLoaded]     = useState(false)

  useEffect(() => {
    // Instant load from localStorage
    const savedProfile  = localStorage.getItem('bp') as BusinessProfile | null
    const savedAdvanced = localStorage.getItem('adv') === '1'
    if (savedProfile) setProfileState(savedProfile)
    setAdvancedMode(savedAdvanced)

    // Sync from API preferences
    import('@/lib/api').then(({ getPreferences }) => {
      getPreferences()
        .then(prefs => {
          if (prefs.business_profile) {
            setProfileState(prefs.business_profile)
            localStorage.setItem('bp', prefs.business_profile)
          }
          if (prefs.advanced_mode !== undefined) {
            setAdvancedMode(prefs.advanced_mode)
            localStorage.setItem('adv', prefs.advanced_mode ? '1' : '0')
          }
        })
        .catch(() => {})
        .finally(() => setIsLoaded(true))
    })
  }, [])

  const setProfile = useCallback((p: BusinessProfile) => {
    setProfileState(p)
    localStorage.setItem('bp', p)
    import('@/lib/api').then(({ updatePreferences }) => {
      updatePreferences({ business_profile: p } as any).catch(() => {})
    })
  }, [])

  const handleSetAdvancedMode = useCallback((v: boolean) => {
    setAdvancedMode(v)
    localStorage.setItem('adv', v ? '1' : '0')
    import('@/lib/api').then(({ updatePreferences }) => {
      updatePreferences({ advanced_mode: v } as any).catch(() => {})
    })
  }, [])

  const terms = profile ? PROFILE_TERMS[profile] : defaultTerms

  return (
    <Ctx.Provider value={{
      profile, setProfile, terms,
      advancedMode, setAdvancedMode: handleSetAdvancedMode,
      isLoaded,
    }}>
      {children}
    </Ctx.Provider>
  )
}

export const useBusinessProfile = () => useContext(Ctx)
