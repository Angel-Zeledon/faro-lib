/**
 * Number formatting that follows the interface language.
 *
 * Twenty-two call sites used to write `n.toLocaleString('es')`, so an English
 * screen printed `1.234` where it meant `1,234` — the same digits reading as a
 * different quantity to anyone used to the other convention. Threading `lang`
 * into ten components to fix a separator was more churn than the bug deserved,
 * so the locale lives here and `LanguageProvider` keeps it in step.
 *
 * The mutable module value is deliberate: the provider sets it during render,
 * before any child formats anything, and there is exactly one language per
 * document. Do NOT read `current` for anything that must re-render on a
 * language change — that is what the `lang` from `useLanguage()` is for.
 */

let current = 'es-CR'

export function localeFor(lang: string): string {
  return lang === 'en' ? 'en-US' : 'es-CR'
}

export function setNumberLocale(lang: string): void {
  current = localeFor(lang)
}

/** `n.toLocaleString(...)` in the interface language. */
export function fmtNum(n: number, opts?: Intl.NumberFormatOptions): string {
  return n.toLocaleString(current, opts)
}
