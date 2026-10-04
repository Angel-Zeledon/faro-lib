'use client'
import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Dictation through the browser's own Web Speech API — nothing is uploaded to
 * our backend, the browser (and its vendor's speech service) does the work.
 *
 * Contract with the caller: the hook never owns the text. It reports the whole
 * composed string through `onText` (what was in the box when recording started,
 * plus everything heard so far, interim words included) and the caller puts it
 * in its own state. That is what makes the words appear as you speak.
 *
 * Failure vocabulary is a stable code (`SpeechError`), not prose: the screen
 * translates it, the same rule the API error layer follows.
 */

export type SpeechError =
  | 'denied'        // microphone permission refused (or blocked by the site)
  | 'no_microphone' // no input device
  | 'no_speech'     // started, but nothing was heard before the engine gave up
  | 'network'       // the browser's speech service could not be reached
  | 'language'      // the engine does not support the language
  | 'failed'        // anything else

// The slice of the Web Speech API we use. It is not in lib.dom.d.ts.
interface RecognitionAlternative { transcript: string }
interface RecognitionResult { isFinal: boolean; 0: RecognitionAlternative; length: number }
interface RecognitionEvent { results: { length: number; [i: number]: RecognitionResult } }
interface RecognitionErrorEvent { error: string }
interface Recognition {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((e: RecognitionEvent) => void) | null
  onerror: ((e: RecognitionErrorEvent) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
  abort(): void
}
type RecognitionCtor = new () => Recognition

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === 'undefined') return null
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

/** App language → BCP-47 tag for the recognizer. es-419 is Latin American Spanish. */
export function speechLocale(appLang: string): string {
  return appLang === 'en' ? 'en-US' : 'es-419'
}

function errorFromEngine(code: string): SpeechError {
  switch (code) {
    case 'not-allowed':
    case 'service-not-allowed': return 'denied'
    case 'audio-capture':       return 'no_microphone'
    case 'no-speech':           return 'no_speech'
    case 'network':             return 'network'
    case 'language-not-supported': return 'language'
    default:                    return 'failed'
  }
}

interface Options {
  /** App language ('es' | 'en'). */
  lang: string
  /** The text currently in the box; read once, when recording starts. */
  getBase: () => string
  /** Called with the full composed text on every interim/final update. */
  onText: (full: string) => void
}

export function useSpeechToText({ lang, getBase, onText }: Options) {
  // Support is only knowable in the browser, so the first render (server and
  // hydration pass) is always "unsupported" and the real answer lands after
  // mount — same rule as useIsNarrow, for the same hydration-mismatch reason.
  const [supported, setSupported] = useState(false)
  const [listening, setListening] = useState(false)
  const [error, setError] = useState<SpeechError | null>(null)
  const recRef = useRef<Recognition | null>(null)
  // Latest callbacks without re-creating `start` on every keystroke.
  const getBaseRef = useRef(getBase); getBaseRef.current = getBase
  const onTextRef = useRef(onText);   onTextRef.current = onText
  const langRef = useRef(lang);       langRef.current = lang

  useEffect(() => { setSupported(recognitionCtor() !== null) }, [])

  const stop = useCallback(() => {
    const rec = recRef.current
    recRef.current = null
    if (rec) {
      rec.onresult = null; rec.onerror = null; rec.onend = null
      try { rec.stop() } catch { /* already stopped */ }
    }
    setListening(false)
  }, [])

  const start = useCallback(() => {
    const Ctor = recognitionCtor()
    if (!Ctor || recRef.current) return
    setError(null)

    const rec = new Ctor()
    rec.lang = speechLocale(langRef.current)
    rec.continuous = true
    rec.interimResults = true

    const base = getBaseRef.current()
    const sep = base && !/\s$/.test(base) ? ' ' : ''

    rec.onresult = (e) => {
      let spoken = ''
      for (let i = 0; i < e.results.length; i++) spoken += e.results[i][0].transcript
      onTextRef.current(base + sep + spoken.trimStart())
    }
    rec.onerror = (e) => {
      // 'aborted' is us calling stop/abort; nothing to tell the user.
      if (e.error === 'aborted') return
      setError(errorFromEngine(e.error))
    }
    // The engine ends the session by itself on silence (and on some mobile
    // browsers after every phrase). Either way: we are no longer recording.
    rec.onend = () => { recRef.current = null; setListening(false) }

    recRef.current = rec
    try {
      rec.start()
      setListening(true)
    } catch {
      recRef.current = null
      setError('failed')
    }
  }, [])

  const toggle = useCallback(() => { if (recRef.current) stop(); else start() }, [start, stop])

  // Never leave the microphone open behind the user's back: stop when the tab
  // is hidden or the window loses focus (switching apps on a phone, a call
  // coming in), and when the component goes away.
  useEffect(() => {
    if (!listening) return
    const halt = () => stop()
    const onVisibility = () => { if (document.hidden) stop() }
    window.addEventListener('blur', halt)
    window.addEventListener('pagehide', halt)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.removeEventListener('blur', halt)
      window.removeEventListener('pagehide', halt)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [listening, stop])

  useEffect(() => () => {
    const rec = recRef.current
    recRef.current = null
    if (rec) {
      rec.onresult = null; rec.onerror = null; rec.onend = null
      try { rec.abort() } catch { /* ignore */ }
    }
  }, [])

  // Changing the app language mid-recording would keep listening in the old one.
  useEffect(() => { if (recRef.current) stop() }, [lang, stop])

  return { supported, listening, error, clearError: () => setError(null), start, stop, toggle }
}
