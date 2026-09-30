'use client'
import { useCallback, useEffect, useState } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'

/**
 * /pronosticos has two readers: a buyer who wants to know what will sell and
 * whether to trust it, and an analyst who wants the model competition behind
 * it (docs/stability.md section 18, "an analyst's instrument panel on a
 * buyer's screen"). One toggle chooses between them. Nothing is removed by the
 * buyer view — every control the screen had before the split is still one
 * click away in the technical view.
 */
export type ForecastView = 'buyer' | 'tech'

const STORAGE_KEY = 'stockai.forecast_view'
const QUERY_PARAM = 'view'

function readQuery(): ForecastView | null {
  try {
    const v = new URLSearchParams(window.location.search).get(QUERY_PARAM)
    return v === 'tech' || v === 'buyer' ? v : null
  } catch {
    return null
  }
}

function readStored(): ForecastView | null {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY)
    return v === 'tech' || v === 'buyer' ? v : null
  } catch {
    return null
  }
}

/**
 * The viewer's chosen view. Default is buyer. A `?view=tech` link wins over the
 * remembered choice, so a shared link opens where its sender meant it to.
 * Storage is a convenience only: private windows and blocked storage throw,
 * and the page then simply opens on the buyer view.
 *
 * Read after mount, not during render — the server has no localStorage, and a
 * different first render on the client would be a hydration mismatch.
 */
export function useForecastView(): [ForecastView, (v: ForecastView) => void] {
  const [view, setViewState] = useState<ForecastView>('buyer')

  useEffect(() => {
    const initial = readQuery() ?? readStored()
    if (initial) setViewState(initial)
  }, [])

  const setView = useCallback((v: ForecastView) => {
    setViewState(v)
    try { window.localStorage.setItem(STORAGE_KEY, v) } catch { /* storage unavailable */ }
    // Keep the address bar shareable without adding a history entry. The
    // existing state object is passed back so the router's own bookkeeping
    // in it survives; other query params (`?session=`) are preserved.
    try {
      const url = new URL(window.location.href)
      if (v === 'tech') url.searchParams.set(QUERY_PARAM, 'tech')
      else url.searchParams.delete(QUERY_PARAM)
      window.history.replaceState(window.history.state, '', url.toString())
    } catch { /* non-fatal: the view still switched */ }
  }, [])

  return [view, setView]
}

export function ViewToggle({ value, onChange }: {
  value: ForecastView
  onChange: (v: ForecastView) => void
}) {
  const { t } = useLanguage()
  const options: { value: ForecastView; label: string }[] = [
    { value: 'buyer', label: t('skus.view_buyer') },
    { value: 'tech',  label: t('skus.view_tech') },
  ]
  return (
    <div
      role="radiogroup"
      aria-label={t('skus.view_toggle_label')}
      data-tour="skus.view"
      style={{
        display: 'flex', gap: 2, background: 'var(--surface-2)', borderRadius: 8,
        padding: 3, border: '1px solid var(--border)',
      }}
    >
      {options.map(o => {
        const on = value === o.value
        return (
          <button
            key={o.value}
            role="radio"
            aria-checked={on}
            onClick={() => onChange(o.value)}
            style={{
              all: 'unset', cursor: 'pointer',
              padding: '4px 11px', borderRadius: 6, fontSize: 11.5, fontWeight: 600,
              background: on ? 'var(--accent)' : 'transparent',
              color: on ? '#fff' : 'var(--dim)',
              transition: 'all 0.12s',
            }}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
