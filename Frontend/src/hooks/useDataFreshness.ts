'use client'
import { useCallback, useEffect, useState } from 'react'
import { getDataFreshness } from '@/lib/api'
import type { DataFreshnessInfo } from '@/lib/api'

/**
 * How old the data behind the semáforo is.
 *
 * Deliberately shared through a tiny module-level cache: `/hoy` renders both
 * the header pill (DataFreshness) and the degraded-semáforo banner, and both
 * need the same answer. Without the cache the page would issue two identical
 * requests on every mount and could — briefly — show a warning in one place
 * and not the other.
 *
 * The failure mode is silent on purpose (`silent: true`, error swallowed): this
 * is a secondary indicator, and a banner reading "could not check freshness"
 * would be noise on top of whatever already broke.
 */

const TTL_MS = 60_000

let _cache: { at: number; data: DataFreshnessInfo } | null = null
let _inFlight: Promise<DataFreshnessInfo> | null = null

function fetchFreshness(force = false): Promise<DataFreshnessInfo> {
  if (!force && _cache && Date.now() - _cache.at < TTL_MS) {
    return Promise.resolve(_cache.data)
  }
  if (!force && _inFlight) return _inFlight
  _inFlight = getDataFreshness({ silent: true })
    .then(data => {
      _cache = { at: Date.now(), data }
      return data
    })
    .finally(() => { _inFlight = null })
  return _inFlight
}

/** Drops the cache so the next reader refetches — call after a new upload. */
export function invalidateDataFreshness(): void {
  _cache = null
}

export interface UseDataFreshness {
  freshness: DataFreshnessInfo | null
  loading:   boolean
  refresh:   () => void
}

export function useDataFreshness(): UseDataFreshness {
  const [freshness, setFreshness] = useState<DataFreshnessInfo | null>(_cache?.data ?? null)
  const [loading, setLoading]     = useState(_cache === null)

  const load = useCallback((force = false) => {
    let cancelled = false
    setLoading(true)
    fetchFreshness(force)
      .then(data => { if (!cancelled) setFreshness(data) })
      .catch(() => { if (!cancelled) setFreshness(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => load(), [load])

  return { freshness, loading, refresh: () => load(true) }
}
