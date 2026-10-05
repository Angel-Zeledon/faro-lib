'use client'
// The visitor's chosen sample language, shared by every code block on a screen:
// picking Go in one card switches them all. Persisted by lib/codeSamples.ts.
import { useCallback, useEffect, useState } from 'react'
import { loadSampleLang, saveSampleLang, type SampleLang } from '@/lib/codeSamples'

const EVENT = 'stockai:code-lang'

export function useSampleLang(): [SampleLang, (l: SampleLang) => void] {
  // cURL on the server and first paint, the saved choice right after: reading
  // storage during render would mismatch the server's HTML.
  const [lang, setLang] = useState<SampleLang>('curl')
  useEffect(() => {
    setLang(loadSampleLang())
    const on = () => setLang(loadSampleLang())
    window.addEventListener(EVENT, on)
    return () => window.removeEventListener(EVENT, on)
  }, [])
  const choose = useCallback((l: SampleLang) => {
    saveSampleLang(l)
    setLang(l)
    window.dispatchEvent(new Event(EVENT))
  }, [])
  return [lang, choose]
}
