'use client'
// Bridge between the global i18n layer and the stock-setup catalogue.
//
// It asks `useLanguage().t()` first, so the moment these keys are merged into
// `i18n/translations.ts` the merged copy wins with no component change. Until
// then `t()` returns the key unchanged (its documented fallback), and we serve
// the same string from `stockSetup.ts` — the screen never shows a raw key.

import { useCallback } from 'react'

import { useLanguage } from '@/contexts/LanguageContext'
import { stockSetupEn, stockSetupEs, type StockSetupKey } from './stockSetup'

type Params = Record<string, string | number>

function interpolate(text: string, params?: Params): string {
  if (!params) return text
  return Object.entries(params).reduce(
    (acc, [k, v]) => acc.replace(new RegExp(`\\{${k}\\}`, 'g'), String(v)),
    text,
  )
}

export function useSetupCopy() {
  const { t, lang } = useLanguage()

  return useCallback((key: StockSetupKey | string, params?: Params): string => {
    const global = t(key, params)
    if (global !== key) return global
    const dict = (lang === 'en' ? stockSetupEn : stockSetupEs) as Record<string, string>
    const local = dict[key] ?? stockSetupEs[key as StockSetupKey]
    return local ? interpolate(local, params) : key
  }, [t, lang])
}
