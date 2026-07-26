'use client'
import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { translations, type Lang } from '@/i18n/translations'

interface LangCtx {
  lang: Lang
  /** `params` interpolates `{name}` placeholders in the resolved string —
   * used by the error i18n layer to fill in a backend error's dynamic values
   * (sku, qty, …) it sends as `error_params`. */
  t:    (k: string, params?: Record<string, unknown>) => string
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
  const t = useCallback((k: string, params?: Record<string, unknown>) => {
    const dict = translations[lang] as Record<string, string>
    let str = dict[k] ?? (translations.es as Record<string, string>)[k] ?? k
    if (params) {
      for (const [pk, pv] of Object.entries(params)) {
        str = str.replace(new RegExp(`\\{${pk}\\}`, 'g'), String(pv))
      }
    }
    return str
  }, [lang])

  return <Ctx.Provider value={{ lang, t, setLang }}>{children}</Ctx.Provider>
}

export const useLanguage = () => useContext(Ctx)
