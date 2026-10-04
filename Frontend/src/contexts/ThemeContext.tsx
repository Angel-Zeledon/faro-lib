'use client'
import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { persistPreference } from '@/lib/persistPreference'

type Theme = 'dark' | 'light'

interface ThemeCtx {
  theme:     Theme
  setTheme:  (t: Theme) => void
  toggle:    () => void
}

const Ctx = createContext<ThemeCtx>({ theme: 'light', setTheme: () => {}, toggle: () => {} })

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  // Light is the default for this audience — see the token comment in
  // globals.css. Users who chose dark keep it via localStorage.
  const [theme, setThemeState] = useState<Theme>('light')

  useEffect(() => {
    const saved = (localStorage.getItem('theme') as Theme | null) ?? 'light'
    setThemeState(saved)
    document.documentElement.setAttribute('data-theme', saved)
  }, [])

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t)
    localStorage.setItem('theme', t)
    document.documentElement.setAttribute('data-theme', t)
    // Same reason as the language toggle: the choice lived only in this
    // browser, and /mi-cuenta's getPreferences() on mount put the server's
    // untouched value back.
    void persistPreference({ theme: t })
  }, [])

  const toggle = useCallback(() => setTheme(theme === 'dark' ? 'light' : 'dark'), [theme, setTheme])

  return <Ctx.Provider value={{ theme, setTheme, toggle }}>{children}</Ctx.Provider>
}

export const useTheme = () => useContext(Ctx)
