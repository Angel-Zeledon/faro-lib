'use client'
import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { translations, type Lang } from '@/i18n/translations'

interface LangCtx {
  lang: Lang
  t:    (k: string) => string
  setLang: (l: Lang) => void
}

const Ctx = createContext<LangCtx>({ lang: 'es', t: (k: string) => k, setLang: () => {} })

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>('es')

  useEffect(() => {
    const saved = (localStorage.getItem('lang') as Lang | null) ?? 'es'
    setLangState(saved)
  }, [])

  const setLang = useCallback((l: Lang) => {
    setLangState(l)
    localStorage.setItem('lang', l)
  }, [])

  // Falls back to the Spanish value, then to the raw key, so a missing
  // translation degrades gracefully instead of showing an empty string.
  const t = useCallback((k: string) => {
    const dict = translations[lang] as Record<string, string>
    return dict[k] ?? (translations.es as Record<string, string>)[k] ?? k
  }, [lang])

  return <Ctx.Provider value={{ lang, t, setLang }}>{children}</Ctx.Provider>
}

export const useLanguage = () => useContext(Ctx)
