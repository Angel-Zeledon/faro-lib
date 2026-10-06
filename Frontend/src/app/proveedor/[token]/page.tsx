'use client'
/**
 * /proveedor/<token> — where a supplier answers a purchase order.
 *
 * No account, no app shell (ConditionalShell lets this prefix through), and no
 * session: the link in the message is the credential. The page shows what the
 * supplier needs to answer — product, SKU, quantity, unit, requested date — and
 * nothing else: the API never sends prices, costs, stock or other orders.
 *
 * Language: the buyer's language to start, switchable here. The switch is local
 * to this page on purpose; `LanguageContext.setLang` would write to an account
 * the supplier does not have.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { translations, type Lang } from '@/i18n/translations'
import {
  fetchSupplierPortal, submitSupplierPortal, PortalError,
} from '@/lib/supplierPortal'
import type { SupplierPortalAnswer, SupplierPortalLine, SupplierPortalView } from '@/lib/types'

// ── Tokens: the app's own variables, so light/dark follow the same theme ──────
const C = {
  bg: 'var(--bg)', surface: 'var(--surface)', card: 'var(--surface-2)',
  border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  accent: 'var(--accent)',
  green: '#2E8B62', amber: '#B7791F', red: '#C0504D',
}
const MAX_NOTE = 500
const MAX_DAYS_AHEAD = 730

type Phase = 'loading' | 'ready' | 'invalid' | 'failed'
type RowState = { declined: boolean; qty: string; date: string; note: string }
type RowError = 'qty' | 'date'
type LineStatus = 'confirmed' | 'changed' | 'declined'

function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function fmtDay(iso: string | null | undefined, lang: Lang): string {
  if (!iso) return '—'
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(lang === 'en' ? 'en-US' : 'es-CR', { day: 'numeric', month: 'short', year: 'numeric' })
}

function fmtQty(n: number, lang: Lang): string {
  return n.toLocaleString(lang === 'en' ? 'en-US' : 'es-CR', { maximumFractionDigits: 2 })
}

function initialRow(line: SupplierPortalLine): RowState {
  const r = line.response
  if (r) {
    return {
      declined: r.status === 'declined',
      qty: r.confirmed_qty != null ? String(r.confirmed_qty) : String(line.quantity),
      date: r.promised_date ?? line.requested_date ?? '',
      note: r.note ?? '',
    }
  }
  return { declined: false, qty: String(line.quantity), date: line.requested_date ?? '', note: '' }
}

/** What the buyer will read for this line. Mirrors the server's rule, which is
 *  the one that decides: same quantity and same date means confirmed. */
function statusOf(line: SupplierPortalLine, row: RowState): LineStatus {
  if (row.declined) return 'declined'
  const qty = Number(row.qty)
  const sameQty = Number.isFinite(qty) && Math.abs(qty - line.quantity) < 1e-9
  const sameDate = !line.requested_date || row.date === line.requested_date
  return sameQty && sameDate ? 'confirmed' : 'changed'
}

const STATUS_COLOR: Record<LineStatus, string> = {
  confirmed: C.green, changed: C.amber, declined: C.red,
}
const STATUS_MARK: Record<LineStatus, string> = { confirmed: '✓', changed: '↻', declined: '✕' }

export default function SupplierPortalPage({ params }: { params: { token: string } }) {
  const token = params.token
  const [phase, setPhase] = useState<Phase>('loading')
  const [view, setView] = useState<SupplierPortalView | null>(null)
  const [lang, setLang] = useState<Lang>('es')
  const [rows, setRows] = useState<Record<string, RowState>>({})
  const [errors, setErrors] = useState<Record<string, RowError>>({})
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [sent, setSent] = useState<{ confirmed: number; changed: number; declined: number } | null>(null)

  const t = useCallback((key: string, p?: Record<string, unknown>) => {
    const dict = translations[lang] as Record<string, string>
    let s = dict[key] ?? (translations.es as Record<string, string>)[key] ?? key
    if (p) for (const [k, v] of Object.entries(p)) s = s.split(`{${k}}`).join(String(v))
    return s
  }, [lang])

  useEffect(() => { document.documentElement.lang = lang }, [lang])

  const load = useCallback(async (keepLang = false) => {
    try {
      const v = await fetchSupplierPortal(token)
      setView(v)
      if (!keepLang) setLang(v.language === 'en' ? 'en' : 'es')
      setRows(Object.fromEntries(v.lines.map(l => [l.line_id, initialRow(l)])))
      setPhase('ready')
    } catch (e) {
      // Any bad link is one answer on purpose: say so without guessing why.
      setPhase(e instanceof PortalError && e.kind === 'invalid' ? 'invalid' : 'failed')
    }
  }, [token])

  useEffect(() => { void load() }, [load])

  const today = useMemo(() => new Date(), [])
  const minDate = useMemo(() => { const d = new Date(today); d.setDate(d.getDate() - 1); return isoDay(d) }, [today])
  const maxDate = useMemo(() => { const d = new Date(today); d.setDate(d.getDate() + MAX_DAYS_AHEAD); return isoDay(d) }, [today])

  const patch = (id: string, change: Partial<RowState>) => {
    setRows(prev => ({ ...prev, [id]: { ...prev[id], ...change } }))
    setErrors(prev => { if (!(id in prev)) return prev; const { [id]: _drop, ...rest } = prev; return rest })
  }

  const confirmAllAsRequested = () => {
    if (!view) return
    setRows(Object.fromEntries(view.lines.map(l => [l.line_id, {
      declined: false, qty: String(l.quantity), date: l.requested_date ?? rows[l.line_id]?.date ?? '',
      note: rows[l.line_id]?.note ?? '',
    }])))
    setErrors({})
    setNotice(null)
  }

  const counts = useMemo(() => {
    const c = { confirmed: 0, changed: 0, declined: 0 }
    if (view) for (const l of view.lines) { const r = rows[l.line_id]; if (r) c[statusOf(l, r)] += 1 }
    return c
  }, [view, rows])

  async function submit() {
    if (!view || submitting) return
    const found: Record<string, RowError> = {}
    const answers: SupplierPortalAnswer[] = []
    for (const l of view.lines) {
      const r = rows[l.line_id]
      const note = r.note.trim() || undefined
      if (r.declined) { answers.push({ line_id: l.line_id, decision: 'decline', note }); continue }
      const qty = Number(r.qty)
      if (!r.qty.trim() || !Number.isFinite(qty) || qty <= 0) { found[l.line_id] = 'qty'; continue }
      if (!r.date || r.date < minDate || r.date > maxDate) { found[l.line_id] = 'date'; continue }
      answers.push({ line_id: l.line_id, decision: 'confirm', confirmed_qty: qty, promised_date: r.date, note })
    }
    setErrors(found)
    if (Object.keys(found).length > 0) {
      setNotice(t('supplier_portal.review_errors'))
      document.getElementById(`line-${Object.keys(found)[0]}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
      return
    }
    setNotice(null)
    setSubmitting(true)
    try {
      const res = await submitSupplierPortal(token, answers)
      setSent(res.counts)
      await load(true)
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (e) {
      if (e instanceof PortalError) {
        if (e.kind === 'invalid') setPhase('invalid')
        else if (e.kind === 'locked') { await load(true) }
        else setNotice(t(`supplier_portal.err_${e.kind}`))
      } else setNotice(t('supplier_portal.err_server'))
    } finally {
      setSubmitting(false)
    }
  }

  // ── Frame ──────────────────────────────────────────────────────────────────
  const frame = (children: React.ReactNode) => (
    <main style={{
      minHeight: '100dvh', background: C.bg, color: C.text,
      padding: '20px 16px calc(24px + env(safe-area-inset-bottom))',
    }}>
      <div style={{ maxWidth: 640, margin: '0 auto' }}>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
          <div role="group" aria-label={t('supplier_portal.language')} style={{ display: 'inline-flex', gap: 4 }}>
            {(['es', 'en'] as const).map(l => (
              <button key={l} type="button" onClick={() => setLang(l)} aria-pressed={lang === l}
                lang={l}
                style={{
                  minHeight: 36, minWidth: 44, padding: '0 10px', borderRadius: 8, cursor: 'pointer',
                  fontSize: 13, fontWeight: 600, fontFamily: 'inherit',
                  border: `1px solid ${lang === l ? C.accent : C.border}`,
                  background: lang === l ? C.accent : 'transparent',
                  color: lang === l ? '#fff' : C.muted,
                }}>
                {l === 'es' ? 'Español' : 'English'}
              </button>
            ))}
          </div>
        </div>
        {children}
      </div>
    </main>
  )

  if (phase === 'loading') {
    return frame(<p role="status" style={{ color: C.dim, fontSize: 15 }}>{t('supplier_portal.loading')}</p>)
  }

  if (phase === 'invalid' || phase === 'failed' || !view) {
    const invalid = phase === 'invalid'
    return frame(
      <section role="alert" style={{ padding: '28px 4px' }}>
        <h1 style={{ margin: '0 0 10px', fontSize: 24, lineHeight: 1.2 }}>
          {t(invalid ? 'supplier_portal.invalid_title' : 'supplier_portal.failed_title')}
        </h1>
        <p style={{ margin: 0, fontSize: 16, lineHeight: 1.55, color: C.muted, maxWidth: '60ch' }}>
          {t(invalid ? 'supplier_portal.invalid_body' : 'supplier_portal.failed_body')}
        </p>
        {!invalid && (
          <button type="button" onClick={() => { setPhase('loading'); void load(true) }}
            style={{
              marginTop: 18, minHeight: 48, padding: '0 20px', borderRadius: 10, border: 'none',
              background: C.accent, color: '#fff', fontSize: 15, fontWeight: 600, cursor: 'pointer',
            }}>
            {t('supplier_portal.retry')}
          </button>
        )}
      </section>,
    )
  }

  const locked = view.locked

  return frame(
    <>
      <header style={{ marginBottom: 20 }}>
        <h1 style={{ margin: 0, fontSize: 26, lineHeight: 1.2, letterSpacing: '-0.01em' }}>
          {t('supplier_portal.heading', { reference: view.reference })}
        </h1>
        <p style={{ margin: '8px 0 0', fontSize: 15, color: C.muted, lineHeight: 1.5 }}>
          {view.buyer
            ? t('supplier_portal.from_to', { buyer: view.buyer, supplier: view.supplier })
            : t('supplier_portal.for_supplier', { supplier: view.supplier })}
        </p>
        {!locked && (
          <p style={{ margin: '14px 0 0', fontSize: 15, lineHeight: 1.55, color: C.text, maxWidth: '62ch' }}>
            {t('supplier_portal.intro')}
          </p>
        )}
      </header>

      {sent && (
        <section role="status" style={{
          marginBottom: 18, padding: '16px 18px', borderRadius: 12,
          border: `1px solid ${C.green}`, background: C.surface,
        }}>
          <h2 style={{ margin: '0 0 6px', fontSize: 18, color: C.green }}>✓ {t('supplier_portal.success_title')}</h2>
          <p style={{ margin: 0, fontSize: 15, lineHeight: 1.5 }}>
            {t('supplier_portal.success_body', { buyer: view.buyer ?? t('supplier_portal.the_buyer') })}
          </p>
          <p style={{ margin: '8px 0 0', fontSize: 14, color: C.muted }}>
            {t('supplier_portal.counts', sent)}
          </p>
        </section>
      )}

      {locked && !sent && (
        <section role="status" style={{
          marginBottom: 18, padding: '16px 18px', borderRadius: 12,
          border: `1px solid ${C.border}`, background: C.surface,
        }}>
          <h2 style={{ margin: '0 0 6px', fontSize: 18 }}>{t('supplier_portal.locked_title')}</h2>
          <p style={{ margin: 0, fontSize: 15, lineHeight: 1.5, color: C.muted }}>
            {t('supplier_portal.locked_body', { date: fmtDay(view.submitted_at, lang) })}
          </p>
        </section>
      )}

      <ul aria-label={t('supplier_portal.lines_label')}
          style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {view.lines.map(line => {
          const row = rows[line.line_id] ?? initialRow(line)
          const status = statusOf(line, row)
          const err = errors[line.line_id]
          const color = STATUS_COLOR[status]
          const qtyId = `qty-${line.line_id}`
          const dateId = `date-${line.line_id}`
          const noteId = `note-${line.line_id}`
          return (
            <li key={line.line_id} id={`line-${line.line_id}`} style={{
              background: C.surface, border: `1px solid ${C.border}`, borderLeft: `5px solid ${color}`,
              borderRadius: 12, padding: '14px 16px',
            }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, justifyContent: 'space-between' }}>
                <div style={{ minWidth: 0 }}>
                  <h2 style={{ margin: 0, fontSize: 17, lineHeight: 1.3, overflowWrap: 'anywhere' }}>{line.name}</h2>
                  <div style={{ marginTop: 2, fontSize: 13, color: C.dim, fontFamily: 'ui-monospace, monospace', overflowWrap: 'anywhere' }}>
                    {line.sku}
                  </div>
                </div>
                <span style={{
                  flexShrink: 0, padding: '3px 10px', borderRadius: 20, fontSize: 13, fontWeight: 600,
                  color, border: `1px solid ${color}`, whiteSpace: 'nowrap',
                }}>
                  <span aria-hidden="true">{STATUS_MARK[status]} </span>{t(`supplier_portal.status_${status}`)}
                </span>
              </div>

              <dl style={{ margin: '12px 0 0', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '6px 16px', fontSize: 14 }}>
                <div>
                  <dt style={{ color: C.dim }}>{t('supplier_portal.ordered_qty')}</dt>
                  <dd style={{ margin: 0, fontWeight: 700, fontSize: 18, fontVariantNumeric: 'tabular-nums' }}>
                    {fmtQty(line.quantity, lang)}{line.unit ? ` ${line.unit}` : ''}
                  </dd>
                </div>
                <div>
                  <dt style={{ color: C.dim }}>{t('supplier_portal.requested_date')}</dt>
                  <dd style={{ margin: 0, fontWeight: 600 }}>{fmtDay(line.requested_date, lang)}</dd>
                </div>
              </dl>

              {locked ? (
                <div style={{ marginTop: 12, fontSize: 15, lineHeight: 1.5 }}>
                  {row.declined ? (
                    <strong style={{ color: C.red }}>{t('supplier_portal.declined_line')}</strong>
                  ) : (
                    <>
                      <div>{t('supplier_portal.you_deliver', { qty: fmtQty(Number(row.qty), lang), date: fmtDay(row.date, lang) })}</div>
                    </>
                  )}
                  {row.note && (
                    // The supplier's own words, as a text node: never HTML.
                    <div style={{ marginTop: 6, color: C.muted, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{row.note}</div>
                  )}
                </div>
              ) : row.declined ? (
                <div style={{ marginTop: 12 }}>
                  <p style={{ margin: '0 0 10px', fontSize: 15, color: C.red, fontWeight: 600 }}>{t('supplier_portal.declined_line')}</p>
                  <label htmlFor={noteId} style={{ display: 'block', fontSize: 14, color: C.muted, marginBottom: 4 }}>
                    {t('supplier_portal.note_declined')}
                  </label>
                  <textarea id={noteId} value={row.note} maxLength={MAX_NOTE} rows={2}
                    onChange={e => patch(line.line_id, { note: e.target.value })} style={fieldStyle(false)} />
                  <button type="button" onClick={() => patch(line.line_id, { declined: false })} style={linkButton}>
                    {t('supplier_portal.undo_decline')}
                  </button>
                </div>
              ) : (
                <div style={{ marginTop: 12 }}>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
                    <div>
                      <label htmlFor={qtyId} style={labelStyle}>{t('supplier_portal.your_qty')}</label>
                      <input id={qtyId} type="number" inputMode="decimal" min={0} step="any"
                        value={row.qty} onChange={e => patch(line.line_id, { qty: e.target.value })}
                        aria-invalid={err === 'qty'} aria-describedby={err === 'qty' ? `${qtyId}-err` : undefined}
                        style={fieldStyle(err === 'qty')} />
                      {err === 'qty' && <p id={`${qtyId}-err`} role="alert" style={errorStyle}>{t('supplier_portal.err_qty')}</p>}
                    </div>
                    <div>
                      <label htmlFor={dateId} style={labelStyle}>{t('supplier_portal.your_date')}</label>
                      <input id={dateId} type="date" min={minDate} max={maxDate}
                        value={row.date} onChange={e => patch(line.line_id, { date: e.target.value })}
                        aria-invalid={err === 'date'} aria-describedby={err === 'date' ? `${dateId}-err` : undefined}
                        style={fieldStyle(err === 'date')} />
                      {err === 'date' && <p id={`${dateId}-err`} role="alert" style={errorStyle}>{t('supplier_portal.err_date')}</p>}
                    </div>
                  </div>
                  <div style={{ marginTop: 12 }}>
                    <label htmlFor={noteId} style={labelStyle}>{t('supplier_portal.note')}</label>
                    <textarea id={noteId} value={row.note} maxLength={MAX_NOTE} rows={2}
                      placeholder={t('supplier_portal.note_placeholder')}
                      onChange={e => patch(line.line_id, { note: e.target.value })} style={fieldStyle(false)} />
                  </div>
                  <button type="button" onClick={() => patch(line.line_id, { declined: true })} style={{ ...linkButton, color: C.red }}>
                    {t('supplier_portal.decline')}
                  </button>
                </div>
              )}
            </li>
          )
        })}
      </ul>

      {!locked && (
        <div style={{
          position: 'sticky', bottom: 0, marginTop: 18, marginInline: -16,
          padding: '12px 16px calc(12px + env(safe-area-inset-bottom))',
          background: C.surface, borderTop: `1px solid ${C.border}`,
        }}>
          <div style={{ maxWidth: 640, margin: '0 auto' }}>
            {notice && <p role="alert" style={{ ...errorStyle, margin: '0 0 8px', fontSize: 14 }}>{notice}</p>}
            <p style={{ margin: '0 0 8px', fontSize: 14, color: C.muted }} aria-live="polite">
              {t('supplier_portal.counts', counts)}
            </p>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <button type="button" onClick={confirmAllAsRequested} disabled={submitting}
                style={{
                  flex: '1 1 200px', minHeight: 48, padding: '0 16px', borderRadius: 10, cursor: 'pointer',
                  border: `1px solid ${C.border}`, background: 'transparent', color: C.text,
                  fontSize: 15, fontWeight: 600, fontFamily: 'inherit',
                }}>
                {t('supplier_portal.confirm_all')}
              </button>
              <button type="button" onClick={() => void submit()} disabled={submitting} aria-busy={submitting}
                style={{
                  flex: '1 1 200px', minHeight: 48, padding: '0 16px', borderRadius: 10, border: 'none',
                  background: C.accent, color: '#fff', fontSize: 16, fontWeight: 700, fontFamily: 'inherit',
                  cursor: submitting ? 'not-allowed' : 'pointer', opacity: submitting ? 0.7 : 1,
                }}>
                {submitting ? t('supplier_portal.submitting') : t('supplier_portal.submit')}
              </button>
            </div>
            <p style={{ margin: '10px 0 0', fontSize: 12.5, color: C.dim }}>
              {t('supplier_portal.expires', { date: fmtDay(view.expires_at, lang) })}
            </p>
          </div>
        </div>
      )}
    </>,
  )
}

const labelStyle: React.CSSProperties = { display: 'block', fontSize: 14, color: 'var(--muted)', marginBottom: 4 }
const errorStyle: React.CSSProperties = { margin: '4px 0 0', fontSize: 13, color: '#C0504D', fontWeight: 600 }
const linkButton: React.CSSProperties = {
  // Not `all: unset`: that would also remove the browser's keyboard focus ring.
  background: 'none', border: 'none', padding: '0 2px', fontFamily: 'inherit',
  cursor: 'pointer', marginTop: 12, minHeight: 44, display: 'inline-flex', alignItems: 'center',
  fontSize: 14, fontWeight: 600, color: 'var(--accent)', textDecoration: 'underline',
}

function fieldStyle(invalid: boolean): React.CSSProperties {
  return {
    display: 'block', boxSizing: 'border-box', width: '100%', minHeight: 48, padding: '10px 12px',
    borderRadius: 10, fontSize: 16, fontFamily: 'inherit', color: 'var(--text)',
    background: 'var(--surface-2)', border: `1px solid ${invalid ? '#C0504D' : 'var(--border)'}`,
  }
}
