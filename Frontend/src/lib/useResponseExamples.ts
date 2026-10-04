'use client'
// The captured answers of the API (public/api-response-examples.json), fetched
// once, the first time an endpoint is shown, so the page itself stays light.
// Shared by /desarrolladores and the in-app /api screen. Until it arrives, or
// for an endpoint that has no entry, `useResponseExample` returns null and the
// caller shows the generic envelope.
import { useEffect, useState } from 'react'
import type { SchemaNode } from '@/lib/apiSchema'

export type ResponseExample = {
  status: number
  content_type?: string
  binary?: boolean
  example?: unknown
  schema?: SchemaNode
}

type Table = Record<string, ResponseExample>

let cache: Table | null = null
let pending: Promise<Table> | null = null

export function useResponseExample(id: string): ResponseExample | null {
  const [all, setAll] = useState<Table | null>(cache)
  useEffect(() => {
    if (cache) { setAll(cache); return }
    pending ??= fetch('/api-response-examples.json')
      .then(r => (r.ok ? r.json() : {}))
      .catch(() => ({}))
      .then(d => (cache = d as Table))
    let alive = true
    pending.then(d => { if (alive) setAll(d) })
    return () => { alive = false }
  }, [])
  return all?.[id] ?? null
}
