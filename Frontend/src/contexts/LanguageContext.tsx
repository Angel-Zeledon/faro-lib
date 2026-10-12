'use client'
import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { usePathname } from 'next/navigation'
import { translations, type Lang } from '@/i18n/translations'
import { setErrorLanguage } from '@/lib/errorMessage'
import { setNumberLocale } from '@/lib/numberLocale'
import { persistPreference } from '@/lib/persistPreference'
import { siteLangOfPath } from '@/components/landing/sitePaths'

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
  const [stored, setLangState] = useState<Lang>('es')
  // A page of the public site is written in the language of its URL (/en/…
  // is English, the rest Spanish), on the server and on the first client
  // render alike, so the HTML a crawler or a shared link gets is already in
  // the right language. Everything else (the app, the sign-in forms) follows
  // the user's own setting below.
  const pathname = usePathname()
  const lang: Lang = siteLangOfPath(pathname ?? '') ?? stored

  // The api layer has no React context; it reads the language from here so
  // `ApiError.message` is always in the language the user chose.
  useEffect(() => { setErrorLanguage(lang) }, [lang])
  // Set during render, not in an effect: children format numbers on their very
  // first paint, and an effect would run after them — showing one frame of
  // Spanish separators on an English screen. See lib/numberLocale.ts.
  setNumberLocale(lang)

  useEffect(() => {
    // `?lang=en` is how an English page of the public site sends a visitor
    // into the sign-in forms: on a split deployment the app is another origin
    // and cannot read the landing's storage. It becomes the stored default.
    let fromUrl: Lang | null = null
    try {
      const q = new URLSearchParams(window.location.search).get('lang')
      if (q === 'es' || q === 'en') fromUrl = q
    } catch { /* no URL to read */ }
    let saved: Lang = 'es'
    try {
      if (fromUrl) localStorage.setItem('lang', fromUrl)
      saved = (localStorage.getItem('lang') as Lang | null) ?? 'es'
    } catch { /* storage blocked: keep the default */ }
    setLangState(fromUrl ?? saved)
  }, [])

  // The tab title and meta description are a `metadata` export in
  // `app/layout.tsx`, which Next renders on the server — where the chosen
  // language is unknowable, because it lives in this browser's localStorage.
  // So that export stays Spanish (the app's default, and the right thing for
  // a crawler in the primary market) and an English user's tab is corrected
  // here, after hydration. Without this the browser tab said
  // the Spanish app title on an otherwise fully English app.
  //
  // Only the app's generic title is swapped. The landing and its subpages
  // (/precios, /como-funciona, …) export their own title and description, and
  // this used to overwrite them too — so a crawler that runs JavaScript read
  // the generic Spanish app title on every public page. A title that is
  // not one of the two app titles belongs to that page and is left alone.
  // The server renders <html lang="es">; an English visitor's document must say
  // so too (screen readers pick the voice from it, browsers offer translation).
  useEffect(() => { document.documentElement.lang = lang }, [lang])

  useEffect(() => {
    const dicts =Object.values(translations) as Record<string, string>[]
    const dict = translations[lang] as Record<string, string>
    const title = dict['app.title']
    const description = dict['app.description']
    if (title && dicts.some(d => d['app.title'] === document.title)) document.title = title
    const meta = document.querySelector('meta[name="description"]')
    const current = meta?.getAttribute('content')
    if (meta && description && dicts.some(d => d['app.description'] === current)) {
      meta.setAttribute('content', description)
    }
  }, [lang])

  const setLang = useCallback((l: Lang) => {
    setLangState(l)
    localStorage.setItem('lang', l)
    // …and on the account, which is where /mi-cuenta reads it back from.
    //
    // This wrote to localStorage only. The ES/EN toggle in the sidebar is on
    // every screen and is the one people actually use, so the choice stuck —
    // until /mi-cuenta mounted, called getPreferences() and overwrote
    // localStorage with the server's untouched value. The app flipped back to
    // Spanish mid-session, the ES/EN buttons on that very screen showed ES as
    // selected, and the tour copy promised "los dos se guardan en tu cuenta".
    // Persisting here covers every caller at once — the sidebar, the Ctrl-K
    // palette and /mi-cuenta — instead of each remembering to do it.
    void persistPreference({ language: l })
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
