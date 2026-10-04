'use client'
/**
 * Which features this deployment can actually answer with, right now.
 *
 * The product is meant to be deployed by whoever bought the source, with only
 * the services they chose to pay for. Without this, an unconfigured service is
 * discovered the expensive way: the user writes a question, waits, and gets an
 * error — or worse, sends an alert that quietly goes nowhere. `/capabilities`
 * is the cheap way: booleans, no configuration, readable by any signed-in user,
 * so a screen can say the assistant is off BEFORE somebody types into it.
 *
 * It fails OPEN, and that is deliberate. If the call itself fails — a network
 * blip, a deploy — we do not know that anything is off, and hiding working
 * features on no evidence is a worse failure than letting a request try and
 * report honestly. The backend is the authority either way; this only decides
 * how early the user finds out.
 */
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { getCapabilities, type Capabilities } from './api'

type Ctx = {
  caps: Capabilities | null
  loading: boolean
  /** True unless we know it is off. See "fails open" above. */
  can: (key: keyof Capabilities) => boolean
  refresh: () => Promise<void>
}

const CapabilitiesContext = createContext<Ctx>({
  caps: null, loading: true, can: () => true, refresh: async () => {},
})

export function CapabilitiesProvider({ children }: { children: React.ReactNode }) {
  const [caps, setCaps] = useState<Capabilities | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      // Silent: a deployment with nothing configured is a normal state, not an
      // error worth a toast on every page load.
      setCaps(await getCapabilities({ silent: true }))
    } catch {
      setCaps(null)
    }
  }, [])

  useEffect(() => { refresh().finally(() => setLoading(false)) }, [refresh])

  const can = useCallback((key: keyof Capabilities) => {
    if (!caps) return true
    const value = caps[key]
    return typeof value === 'boolean' ? value : true
  }, [caps])

  return (
    <CapabilitiesContext.Provider value={{ caps, loading, can, refresh }}>
      {children}
    </CapabilitiesContext.Provider>
  )
}

export const useCapabilities = () => useContext(CapabilitiesContext)
