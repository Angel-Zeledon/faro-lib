'use client'
/**
 * The counting surface: scan (camera or keyboard-wedge scanner) or type a code,
 * see the product, set the quantity, move on.
 *
 * Every action becomes a queued scan FIRST (lib/stockCountDevice.ts) and is
 * sent from there, so losing the connection in the middle of an aisle loses
 * nothing: the chip says how many are waiting, and a scan the server refuses
 * stays on screen until the person dismisses it.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, Camera, CameraOff, Check, CloudOff, Minus, Plus, ScanLine, X } from 'lucide-react'

import {
  closeStockCount, deleteStockCountLine, listInventoryStock, lookupStockCode,
  upsertStockCountLine, isApiError,
  type StockCountDetail, type StockCountLine,
} from '@/lib/api'
import {
  BARCODE_FORMATS, buildCatalogue, cameraScanSupported, feedbackError, feedbackOk,
  loadCatalogue, loadQueue, matchLocal, newRef, projectedQty, saveCatalogue, saveQueue,
  type CatalogueItem, type QueuedScan,
} from '@/lib/stockCountDevice'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useErrorDetail } from '@/components/ui/States'

interface Props {
  count:     StockCountDetail
  onLeave:   () => void
  onClosed:  () => void
}

interface Shown { sku: string; name: string | null; uom: string | null; system: number; inWarehouse: boolean }
type CameraState = 'off' | 'starting' | 'on' | 'denied' | 'unsupported' | 'error'

const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', accent: 'var(--accent)',
  green: '#2E8B62', amber: '#B7791F', red: '#C0504D',
}

const fmt = (n: number) => (Number.isInteger(n) ? String(n) : String(Math.round(n * 100) / 100))

export default function CountScan({ count, onLeave, onClosed }: Props) {
  const { t } = useLanguage()
  const narrow = useIsNarrow()
  const confirm = useConfirm()
  const errorDetail = useErrorDetail()
  const warehouse = count.warehouse

  // ── What the server has confirmed, and what is waiting to be sent ──────────
  const [server, setServer] = useState<Record<string, { counted: number; system: number }>>(() => {
    const m: Record<string, { counted: number; system: number }> = {}
    for (const l of count.lines) m[l.sku] = { counted: l.counted_qty, system: l.system_qty_at_count }
    return m
  })
  const [queue, setQueue] = useState<QueuedScan[]>(() => loadQueue(count.id))
  const queueRef = useRef(queue)
  const [persistent, setPersistent] = useState(true)
  const [online, setOnline] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const flushing = useRef(false)
  // SKUs in the order they were last touched, newest first.
  const [order, setOrder] = useState<string[]>(() =>
    [...count.lines].sort((a: StockCountLine, b: StockCountLine) => b.scanned_at.localeCompare(a.scanned_at)).map(l => l.sku))

  // ── Catalogue (instant lookups, works with no signal) ──────────────────────
  const [catalogue, setCatalogue] = useState<CatalogueItem[]>(() => loadCatalogue())
  const names = useRef<Record<string, string | null>>({})

  // ── The card ───────────────────────────────────────────────────────────────
  const [shown, setShown] = useState<Shown | null>(null)
  const [draft, setDraft] = useState<string>('')
  const [message, setMessage] = useState<{ kind: 'error' | 'info'; text: string } | null>(null)
  const [code, setCode] = useState('')
  const [suggestions, setSuggestions] = useState<CatalogueItem[]>([])
  const [busy, setBusy] = useState(false)
  const codeRef = useRef<HTMLInputElement>(null)

  const commit = useCallback((next: QueuedScan[]) => {
    queueRef.current = next
    setQueue(next)
    setPersistent(saveQueue(count.id, next))
  }, [count.id])

  // Online / offline awareness.
  useEffect(() => {
    setOnline(typeof navigator === 'undefined' ? true : navigator.onLine)
    const up = () => setOnline(true)
    const down = () => setOnline(false)
    window.addEventListener('online', up)
    window.addEventListener('offline', down)
    return () => { window.removeEventListener('online', up); window.removeEventListener('offline', down) }
  }, [])

  // Fresh catalogue for this session; the stored copy answers until it lands.
  useEffect(() => {
    let alive = true
    listInventoryStock().then(rows => {
      if (!alive) return
      const items = buildCatalogue(rows)
      setCatalogue(items)
      saveCatalogue(items)
    }).catch(() => { /* keep whatever was stored */ })
    return () => { alive = false }
  }, [])

  const nameOf = useCallback((sku: string): string | null => {
    if (sku in names.current) return names.current[sku]
    return catalogue.find(i => i.sku === sku)?.display_name ?? null
  }, [catalogue])

  // ── Sending ────────────────────────────────────────────────────────────────
  const flush = useCallback(async () => {
    if (flushing.current) return
    flushing.current = true
    setSyncing(true)
    try {
      for (;;) {
        const next = queueRef.current.find(q => !q.error)
        if (!next) break
        try {
          const res = await upsertStockCountLine(count.id, {
            sku: next.sku, quantity: next.quantity, mode: next.mode,
            source: next.source, client_ref: next.ref,
          })
          const line = res.line
          if (line) {
            setServer(s => ({ ...s, [next.sku]: { counted: line.counted_qty, system: line.system_qty_at_count } }))
          }
          commit(queueRef.current.filter(q => q.ref !== next.ref))
          setOnline(true)
        } catch (err) {
          if (isApiError(err) && err.kind !== 'network' && err.kind !== 'server' && err.kind !== 'session') {
            // The server understood and said no: keep the scan, flagged.
            commit(queueRef.current.map(q => q.ref === next.ref
              ? { ...q, error: { code: err.code, params: err.params } } : q))
            continue
          }
          if (isApiError(err) && err.kind === 'network') setOnline(false)
          break // offline or server trouble: try again later, nothing is lost
        }
      }
    } finally {
      flushing.current = false
      setSyncing(false)
    }
  }, [commit, count.id])

  useEffect(() => { void flush() }, [flush, online])
  useEffect(() => {
    const id = window.setInterval(() => {
      if (queueRef.current.some(q => !q.error)) void flush()
    }, 12000)
    return () => window.clearInterval(id)
  }, [flush])

  // Warn before the tab closes with scans that never reached the server.
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (queueRef.current.some(q => !q.error) && !persistent) { e.preventDefault() }
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [persistent])

  const pending = queue.filter(q => !q.error)
  const rejected = queue.filter(q => q.error)

  const countedOf = useCallback((sku: string) =>
    projectedQty(server[sku]?.counted ?? 0, sku, queue), [server, queue])

  const record = useCallback((sku: string, quantity: number, mode: 'add' | 'set', source: 'scan' | 'manual') => {
    const scan: QueuedScan = { ref: newRef(), sku, quantity, mode, source, at: Date.now() }
    commit([...queueRef.current, scan])
    setOrder(o => [sku, ...o.filter(s => s !== sku)])
    void flush()
  }, [commit, flush])

  const show = useCallback((item: { sku: string; name: string | null; uom: string | null; system: number; inWarehouse: boolean }) => {
    names.current[item.sku] = item.name
    setShown(item)
    setDraft('')
  }, [])

  // ── Resolving a code ───────────────────────────────────────────────────────
  const handleCode = useCallback(async (raw: string, source: 'scan' | 'manual' = 'scan') => {
    const value = raw.trim()
    if (!value) return
    setBusy(true)
    setMessage(null)
    try {
      const local = matchLocal(catalogue, value)
      if (local.kind === 'found') {
        const it = local.item
        const system = server[it.sku]?.system ?? it.qty[warehouse] ?? 0
        show({ sku: it.sku, name: it.display_name, uom: it.unit_of_measure, system, inWarehouse: warehouse in it.qty })
        record(it.sku, 1, 'add', source)
        feedbackOk()
        return
      }
      if (local.kind === 'ambiguous') {
        feedbackError()
        setMessage({ kind: 'error', text: t('count.ambiguous', { code: value, skus: local.skus.join(', ') }) })
        return
      }
      // Not in the stored catalogue: it may be newer than our copy.
      try {
        const hit = await lookupStockCode(value, warehouse)
        show({ sku: hit.sku, name: hit.display_name, uom: hit.unit_of_measure, system: server[hit.sku]?.system ?? hit.system_qty, inWarehouse: !!hit.in_warehouse })
        record(hit.sku, 1, 'add', source)
        feedbackOk()
      } catch (err) {
        feedbackError()
        if (isApiError(err) && err.kind === 'network') {
          setOnline(false)
          setMessage({ kind: 'error', text: t('count.not_cached_offline', { code: value }) })
        } else if (isApiError(err) && err.code === 'lookup_code_not_found') {
          setMessage({ kind: 'error', text: t('count.not_found', { code: value }) })
        } else if (isApiError(err) && err.code) {
          setMessage({ kind: 'error', text: errorDetail(err) })
        } else {
          setMessage({ kind: 'error', text: t('count.lookup_failed') })
        }
      }
    } finally {
      setBusy(false)
    }
  }, [catalogue, errorDetail, record, server, show, t, warehouse])

  const submitCode = () => {
    const v = code
    setCode('')
    setSuggestions([])
    void handleCode(v, 'scan')
    codeRef.current?.focus()
  }

  // Typing: offer matches by SKU / name so the manual path never needs a code.
  useEffect(() => {
    const q = code.trim().toLowerCase()
    if (q.length < 2) { setSuggestions([]); return }
    const hits = catalogue.filter(i =>
      i.sku.toLowerCase().includes(q) || (i.display_name ?? '').toLowerCase().includes(q)).slice(0, 6)
    setSuggestions(hits)
  }, [code, catalogue])

  // ── Camera ─────────────────────────────────────────────────────────────────
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const loopRef = useRef<number | null>(null)
  const lastSeen = useRef<{ code: string; at: number }>({ code: '', at: 0 })
  const handleCodeRef = useRef(handleCode)
  useEffect(() => { handleCodeRef.current = handleCode }, [handleCode])
  const [camera, setCamera] = useState<CameraState>('off')

  const stopCamera = useCallback(() => {
    if (loopRef.current) { window.clearTimeout(loopRef.current); loopRef.current = null }
    streamRef.current?.getTracks().forEach(tr => tr.stop())
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
  }, [])

  const startCamera = useCallback(async () => {
    if (!cameraScanSupported()) { setCamera('unsupported'); return }
    setCamera('starting')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' } }, audio: false,
      })
      streamRef.current = stream
      const video = videoRef.current
      if (!video) { stopCamera(); setCamera('off'); return }
      video.srcObject = stream
      await video.play().catch(() => undefined)
      const Detector = (window as unknown as { BarcodeDetector: new (o?: { formats: string[] }) => {
        detect: (v: HTMLVideoElement) => Promise<Array<{ rawValue: string }>>
      } }).BarcodeDetector
      let detector: InstanceType<typeof Detector>
      try { detector = new Detector({ formats: BARCODE_FORMATS }) } catch { detector = new Detector() }
      setCamera('on')
      const tick = async () => {
        if (!streamRef.current) return
        try {
          const found = await detector.detect(video)
          const value = found[0]?.rawValue
          const now = Date.now()
          // The same code held in view must not count a unit every frame.
          if (value && !(value === lastSeen.current.code && now - lastSeen.current.at < 2000)) {
            lastSeen.current = { code: value, at: now }
            void handleCodeRef.current(value, 'scan')
          } else if (value) {
            lastSeen.current.at = now
          }
        } catch { /* a frame that cannot be read is skipped */ }
        loopRef.current = window.setTimeout(tick, 220)
      }
      loopRef.current = window.setTimeout(tick, 220)
    } catch (err) {
      stopCamera()
      const name = (err as { name?: string })?.name
      setCamera(name === 'NotAllowedError' || name === 'SecurityError' ? 'denied' : 'error')
    }
  }, [stopCamera])

  useEffect(() => () => stopCamera(), [stopCamera])
  useEffect(() => { if (!cameraScanSupported()) setCamera('unsupported') }, [])

  // ── Stepper ────────────────────────────────────────────────────────────────
  const shownCounted = shown ? countedOf(shown.sku) : 0
  const setQty = (qty: number) => {
    if (!shown) return
    const q = Math.max(0, Math.min(1_000_000_000, qty))
    record(shown.sku, q, 'set', 'manual')
    setDraft('')
  }
  const commitDraft = () => {
    if (!shown || draft === '') return
    const n = Number(draft.replace(',', '.'))
    if (Number.isFinite(n) && n >= 0) setQty(n)
    else setDraft('')
  }

  const next = () => {
    commitDraft()
    setShown(null)
    setMessage(null)
    codeRef.current?.focus()
  }

  // ── Removing a line ────────────────────────────────────────────────────────
  const removeLine = async (sku: string) => {
    try {
      commit(queueRef.current.filter(q => q.sku !== sku))
      if (server[sku]) await deleteStockCountLine(count.id, sku)
      setServer(s => { const c = { ...s }; delete c[sku]; return c })
      setOrder(o => o.filter(s => s !== sku))
      if (shown?.sku === sku) setShown(null)
    } catch (err) {
      setMessage({ kind: 'error', text: errorDetail(err) })
    }
  }

  // ── Finishing ──────────────────────────────────────────────────────────────
  const finish = async () => {
    if (queueRef.current.some(q => !q.error)) await flush()
    const left = queueRef.current.filter(q => !q.error).length
    if (left > 0) {
      setMessage({ kind: 'error', text: t('count.finish_blocked_pending', { n: left }) })
      return
    }
    if (queueRef.current.some(q => q.error)) {
      setMessage({ kind: 'error', text: t('count.finish_blocked_rejected') })
      return
    }
    if (!(await confirm({ title: t('count.finish_title'), message: t('count.finish_message') }))) return
    try {
      await closeStockCount(count.id)
      onClosed()
    } catch (err) {
      setMessage({ kind: 'error', text: errorDetail(err) })
    }
  }

  // ── Derived ────────────────────────────────────────────────────────────────
  const skus = useMemo(() => {
    const all = new Set<string>([...Object.keys(server), ...queue.filter(q => !q.error).map(q => q.sku)])
    const ordered = order.filter(s => all.has(s))
    Array.from(all).forEach(s => { if (!ordered.includes(s)) ordered.push(s) })
    return ordered
  }, [order, queue, server])
  const units = skus.reduce((sum, s) => sum + countedOf(s), 0)

  const wrapStyle: React.CSSProperties = narrow
    ? { position: 'fixed', inset: 0, zIndex: 60, background: 'var(--bg)', overflowY: 'auto', display: 'flex', flexDirection: 'column' }
    : { maxWidth: 720, margin: '0 auto', display: 'flex', flexDirection: 'column' }

  const cameraNote =
    camera === 'denied' ? t('count.camera_denied')
    : camera === 'unsupported' ? t('count.camera_unsupported')
    : camera === 'error' ? t('count.camera_error') : null

  const btn: React.CSSProperties = {
    all: 'unset', cursor: 'pointer', boxSizing: 'border-box', display: 'inline-flex', alignItems: 'center',
    justifyContent: 'center', gap: 6, minHeight: 44, padding: '0 14px', borderRadius: 10, fontSize: 14,
    fontWeight: 600, border: `1px solid ${C.border}`, color: C.text, background: C.surface,
  }

  return (
    <div style={wrapStyle}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: narrow ? '10px 12px' : '0 0 12px', borderBottom: narrow ? `1px solid ${C.border}` : 'none', background: narrow ? C.surface : 'transparent', position: narrow ? 'sticky' : 'static', top: 0, zIndex: 2 }}>
        <button type="button" style={{ ...btn, padding: '0 10px' }} onClick={onLeave} aria-label={t('count.exit')}>
          <ArrowLeft size={16} aria-hidden="true" />
        </button>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: C.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{warehouse}</div>
          <div style={{ fontSize: 12, color: C.muted }}>{t('count.items_counted', { items: skus.length, units: fmt(units) })}</div>
        </div>
        <span
          role="status"
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, fontWeight: 600, padding: '4px 9px', borderRadius: 999,
            border: `1px solid ${pending.length || !online ? C.amber : C.border}`,
            color: pending.length || !online ? C.amber : C.muted,
          }}
        >
          {!online && <CloudOff size={13} aria-hidden="true" />}
          {pending.length > 0 ? t('count.pending_sync', { n: pending.length })
            : syncing ? t('count.syncing') : t('count.synced')}
        </span>
        <button type="button" style={{ ...btn, background: C.accent, color: '#fff', border: 'none' }} onClick={() => void finish()} disabled={skus.length === 0}>
          <Check size={15} aria-hidden="true" /> {t('count.finish')}
        </button>
      </div>

      <div style={{ padding: narrow ? '12px' : 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {!online && <Note tone="amber">{t('count.offline')}</Note>}
        {!persistent && pending.length > 0 && <Note tone="amber">{t('count.not_persistent')}</Note>}

        {/* Camera */}
        <div style={{ position: 'relative', borderRadius: 12, overflow: 'hidden', background: '#000', display: camera === 'on' || camera === 'starting' ? 'block' : 'none', aspectRatio: narrow ? '4 / 3' : '16 / 7' }}>
          <video ref={videoRef} playsInline muted style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
          <div aria-hidden="true" style={{ position: 'absolute', inset: '22% 12%', border: '2px solid rgba(255,255,255,0.85)', borderRadius: 12, pointerEvents: 'none' }} />
          {camera === 'starting' && <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: 13 }}>{t('count.camera_starting')}</div>}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          {camera === 'on' || camera === 'starting' ? (
            <button type="button" style={btn} onClick={() => { stopCamera(); setCamera('off') }}>
              <CameraOff size={16} aria-hidden="true" /> {t('count.camera_off')}
            </button>
          ) : camera !== 'unsupported' ? (
            <button type="button" style={btn} onClick={() => void startCamera()}>
              <Camera size={16} aria-hidden="true" /> {t('count.camera_on')}
            </button>
          ) : null}
        </div>
        {cameraNote && <Note tone="info">{cameraNote}</Note>}

        {/* Code field: also where a USB / Bluetooth scanner types (it ends with Enter). */}
        <form onSubmit={e => { e.preventDefault(); submitCode() }} style={{ display: 'flex', gap: 8 }}>
          <div style={{ position: 'relative', flex: 1 }}>
            <ScanLine size={16} aria-hidden="true" style={{ position: 'absolute', left: 12, top: 14, color: C.dim }} />
            <input
              ref={codeRef}
              value={code}
              onChange={e => setCode(e.target.value)}
              aria-label={t('count.code_label')}
              placeholder={t('count.code_placeholder')}
              autoCapitalize="off" autoCorrect="off" autoComplete="off" spellCheck={false}
              inputMode="text" enterKeyHint="go"
              style={{ width: '100%', boxSizing: 'border-box', minHeight: 44, padding: '0 12px 0 34px', fontSize: 16, borderRadius: 10, border: `1px solid ${C.border}`, background: C.surface, color: C.text }}
            />
            {suggestions.length > 0 && (
              <div style={{ position: 'absolute', left: 0, right: 0, top: 48, zIndex: 3, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: 'hidden', boxShadow: '0 6px 18px rgba(0,0,0,0.12)' }}>
                {suggestions.map(s => (
                  <button
                    key={s.sku} type="button"
                    onClick={() => { setCode(''); setSuggestions([]); void handleCode(s.sku, 'manual') }}
                    style={{ all: 'unset', boxSizing: 'border-box', display: 'block', width: '100%', padding: '10px 12px', minHeight: 44, cursor: 'pointer', borderBottom: `1px solid ${C.border}` }}
                  >
                    <div style={{ fontSize: 14, color: C.text, fontWeight: 600 }}>{s.display_name || s.sku}</div>
                    <div style={{ fontSize: 12, color: C.muted }}>{s.sku}</div>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button type="submit" style={{ ...btn, background: C.accent, color: '#fff', border: 'none' }} disabled={busy || !code.trim()}>
            {t('count.code_add')}
          </button>
        </form>

        {message && <Note tone={message.kind === 'error' ? 'red' : 'info'}>{message.text}</Note>}

        {/* The last product */}
        {shown ? (
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 14, padding: 14 }}>
            <div style={{ fontSize: 17, fontWeight: 700, color: C.text }}>{shown.name || shown.sku}</div>
            <div style={{ fontSize: 12.5, color: C.muted, marginTop: 2 }}>
              {shown.sku}{shown.uom ? ` · ${shown.uom}` : ''} · {t('count.system_qty', { qty: fmt(shown.system) })}
            </div>
            {!shown.inWarehouse && <div style={{ fontSize: 12, color: C.amber, marginTop: 6 }}>{t('count.not_in_warehouse', { warehouse })}</div>}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 14, margin: '14px 0 8px' }}>
              <button type="button" aria-label={t('count.decrease')} style={{ ...btn, width: 56, minHeight: 56, borderRadius: 14 }} onClick={() => setQty(shownCounted - 1)} disabled={shownCounted <= 0}>
                <Minus size={22} aria-hidden="true" />
              </button>
              <input
                aria-label={t('count.qty_label')}
                value={draft !== '' ? draft : fmt(shownCounted)}
                onChange={e => setDraft(e.target.value)}
                onBlur={commitDraft}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); commitDraft() } }}
                inputMode="decimal"
                style={{ width: 110, textAlign: 'center', fontSize: 36, fontWeight: 700, minHeight: 56, borderRadius: 14, border: `1px solid ${C.border}`, background: C.surface, color: C.text }}
              />
              <button type="button" aria-label={t('count.increase')} style={{ ...btn, width: 56, minHeight: 56, borderRadius: 14 }} onClick={() => setQty(shownCounted + 1)}>
                <Plus size={22} aria-hidden="true" />
              </button>
            </div>
            <div style={{ textAlign: 'center', fontSize: 13, color: C.muted, marginBottom: 12 }}>
              {t('count.difference')}: <strong style={{ color: shownCounted === shown.system ? C.muted : shownCounted > shown.system ? C.green : C.red }}>
                {shownCounted - shown.system > 0 ? '+' : ''}{fmt(shownCounted - shown.system)}
              </strong>
            </div>
            <button type="button" style={{ ...btn, width: '100%', background: C.accent, color: '#fff', border: 'none' }} onClick={next}>
              {t('count.next')}
            </button>
          </div>
        ) : (
          <div style={{ fontSize: 13.5, color: C.muted, padding: '10px 2px' }}>{t('count.scan_prompt')}</div>
        )}

        {/* Scans the server refused: never dropped silently. */}
        {rejected.length > 0 && (
          <div style={{ border: `1px solid ${C.red}`, borderRadius: 12, padding: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: C.red, marginBottom: 6 }}>{t('count.rejected_title')}</div>
            {rejected.map(r => {
              const key = `errors.${r.error?.code}`
              const text = r.error?.code && t(key, r.error.params) !== key ? t(key, r.error.params) : t('count.rejected_generic')
              return (
                <div key={r.ref} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, padding: '4px 0' }}>
                  <span style={{ flex: 1, color: C.text }}>{r.sku} × {fmt(r.quantity)} — <span style={{ color: C.muted }}>{text}</span></span>
                  <button type="button" onClick={() => commit(queueRef.current.filter(q => q.ref !== r.ref))} style={{ ...btn, minHeight: 36, fontSize: 12 }}>
                    {t('count.rejected_discard')}
                  </button>
                </div>
              )
            })}
          </div>
        )}

        {/* Running list */}
        {skus.length > 0 && (
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase', color: C.dim, margin: '4px 2px 6px' }}>{t('count.running_list')}</div>
            <div style={{ border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden', background: C.surface }}>
              {skus.map((sku, i) => {
                const counted = countedOf(sku)
                const system = server[sku]?.system ?? catalogue.find(c => c.sku === sku)?.qty[warehouse] ?? 0
                const diff = counted - system
                return (
                  <div key={sku} style={{ display: 'flex', alignItems: 'center', gap: 8, borderTop: i ? `1px solid ${C.border}` : 'none' }}>
                    <button
                      type="button"
                      onClick={() => { setShown({ sku, name: nameOf(sku), uom: catalogue.find(c => c.sku === sku)?.unit_of_measure ?? null, system, inWarehouse: true }); setDraft('') }}
                      style={{ all: 'unset', boxSizing: 'border-box', flex: 1, minWidth: 0, minHeight: 48, padding: '8px 12px', cursor: 'pointer' }}
                    >
                      <div style={{ fontSize: 14, fontWeight: 600, color: C.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{nameOf(sku) || sku}</div>
                      <div style={{ fontSize: 12, color: C.muted }}>{sku} · {t('count.system_qty', { qty: fmt(system) })}</div>
                    </button>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 16, fontWeight: 700, color: C.text }}>{fmt(counted)}</div>
                      <div style={{ fontSize: 11.5, fontWeight: 600, color: diff === 0 ? C.dim : diff > 0 ? C.green : C.red }}>{diff > 0 ? '+' : ''}{fmt(diff)}</div>
                    </div>
                    <button type="button" aria-label={t('count.remove_line')} title={t('count.remove_line')} onClick={() => void removeLine(sku)} style={{ all: 'unset', cursor: 'pointer', minWidth: 44, minHeight: 44, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: C.dim }}>
                      <X size={16} aria-hidden="true" />
                    </button>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Note({ tone, children }: { tone: 'amber' | 'red' | 'info'; children: React.ReactNode }) {
  const color = tone === 'amber' ? C.amber : tone === 'red' ? C.red : C.muted
  return (
    <div role={tone === 'red' ? 'alert' : 'status'} style={{ fontSize: 13, lineHeight: 1.5, color, padding: '9px 12px', borderRadius: 10, border: `1px solid ${tone === 'info' ? C.border : color}`, background: C.card }}>
      {children}
    </div>
  )
}
