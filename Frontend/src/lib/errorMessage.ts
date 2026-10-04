// One place that turns a failed API call into a sentence in the user's language.
//
// The backend sends English `detail` text plus (where it has been converted) a
// stable `error_code` + `error_params`. The user must never read either of them
// raw. Resolution order, first hit wins:
//
//   1. `errors.<code>`            — the code is in the catalogue (es + en)
//   2. Pydantic field failures    — rebuilt from `type` + `ctx`, not from `msg`
//   3. `errors.generic.<class>`   — a translated sentence per HTTP class
//
// There is deliberately no step 4 that shows `detail`: an English backend string
// in a Spanish screen (or a raw code anywhere) is the defect this file exists to
// prevent. `detail` stays on the ApiError for logs and for API consumers.
//
// This module has no React context, so the active language is pushed into it by
// `LanguageProvider` (`setErrorLanguage`) — the same pattern `explanationCopy.ts`
// uses of taking what it needs as arguments, but module-level so that
// `ApiError.message` (read by dozens of `catch (e) { e.message }` sites) is
// localized too.

import { translations, type Lang } from '@/i18n/translations'

export interface FieldErrorLike {
  field: string
  type:  string
  ctx:   Record<string, unknown>
  msg:   string
}

export interface ErrorParts {
  status:      number            // 0 = the request never reached the server
  code:        string
  params:      Record<string, unknown>
  fieldErrors: FieldErrorLike[]
}

let activeLang: Lang = 'es'

/** Called by LanguageProvider whenever the app language is (re)resolved. */
export function setErrorLanguage(lang: Lang): void {
  activeLang = lang
}

export function getErrorLanguage(): Lang {
  return activeLang
}

type Dict = Record<string, string>

function lookup(key: string, lang: Lang): string | undefined {
  return (translations[lang] as Dict)[key] ?? (translations.es as Dict)[key]
}

function fill(text: string, params: Record<string, unknown>): string {
  return text.replace(/\{(\w+)\}/g, (whole, name: string) => {
    const v = params[name]
    return v === undefined || v === null ? whole : String(v)
  })
}

/** Generic copy key for an HTTP status (0 = network). */
export function genericKeyForStatus(status: number): string {
  if (status === 0)   return 'errors.generic.network'
  if (status === 401) return 'errors.generic.unauthorized'
  if (status === 403) return 'errors.generic.forbidden'
  if (status === 404) return 'errors.generic.not_found'
  if (status === 409) return 'errors.generic.conflict'
  if (status === 413) return 'errors.generic.too_large'
  if (status === 422) return 'errors.generic.invalid'
  if (status === 429) return 'errors.generic.rate_limited'
  if (status >= 500)  return 'errors.generic.server'
  if (status >= 400)  return 'errors.generic.bad_request'
  return 'errors.generic.unknown'
}

/**
 * One Pydantic field failure as a sentence. Returns '' when the rule is not in
 * the catalogue (a brand-new Pydantic rule): the caller then falls through to
 * the generic sentence instead of printing Pydantic's English.
 */
function fieldErrorSentence(fe: FieldErrorLike, lang: Lang): string {
  const rule = lookup(`errors.validation.${fe.type}`, lang)
  if (!rule) return ''
  const ruleText = fill(rule, fe.ctx)
  const fieldLabel = fe.field ? lookup(`errors.field.${fe.field}`, lang) : undefined
  if (fieldLabel) return `${fieldLabel} ${ruleText}`
  return fe.field ? `${fe.field}: ${ruleText}` : ruleText
}

export function translateErrorParts(parts: ErrorParts, lang: Lang = activeLang): string {
  if (parts.code) {
    const text = lookup(`errors.${parts.code}`, lang)
    if (text) return fill(text, parts.params)
  }
  if (parts.fieldErrors.length) {
    const sentences = parts.fieldErrors.map(fe => fieldErrorSentence(fe, lang)).filter(Boolean)
    if (sentences.length) return sentences.join(' ')
  }
  return lookup(genericKeyForStatus(parts.status), lang) ?? ''
}
