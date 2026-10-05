'use client'
/**
 * The guided upload conversation (ventas wizard, step 2).
 *
 * The file was read by the guide in ForecastingCore; this component only SHOWS
 * what it found, in plain words, and sends back the one answer it asks for:
 *
 *  - the first rows exactly as the system reads them, with the three required
 *    columns highlighted and a sample parsed value under each ("14 ene 2025",
 *    "120 unidades");
 *  - a verdict per column (looks right / check this);
 *  - at most ONE question at a time, phrased with examples from THEIR file and
 *    answered with a big tap;
 *  - what was fixed for them, and a way back to the file as it was;
 *  - for an unusable file: what to change in their Excel, an example of a
 *    correct file and the template.
 *
 * Nothing here decides anything: every sentence is keyed off a stable code the
 * backend sends, and a code this build does not know falls back to a generic
 * line instead of a raw key.
 */

import { AlertTriangle, Check } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import UploadGuide from '@/components/upload/UploadGuide'
import type {
  GuidanceFinding, GuidanceOption, GuidanceReport, GuidedRecord,
} from '@/lib/types'

type Params = Record<string, string | number>
type PreviewRow = NonNullable<GuidanceReport['preview']>['rows'][number]

const SLOTS = ['sku', 'date', 'demand'] as const

// Findings worth telling the person about, with the params their sentence needs.
const APPLIED_CODES = new Set([
  'header_row_offset', 'totals_row', 'wide_format', 'date_excel_serial', 'number_decimal_comma',
  'sku_float_codes', 'sku_leading_zeros', 'sku_whitespace', 'sheet_auto_selected',
  'date_text_months', 'date_two_digit_year', 'date_timezone', 'date_order_inferred',
  'columns_swapped', 'demand_currency', 'demand_negatives', 'conflicting_duplicates',
  'demand_text_values', 'dates_in_future',
])

type Translate = (k: string, p?: Params) => string

function useCopy() {
  const { t, lang } = useLanguage()
  // `t` echoes an unknown key back; a missing entry must not reach the screen.
  const tr: Translate = (k, p) => {
    const v = t(k, p as Record<string, unknown>)
    return v === k ? '' : v
  }
  const date = (iso: string | null | undefined, long = false) => {
    if (!iso) return ''
    const d = new Date(`${iso.slice(0, 10)}T00:00:00`)
    if (Number.isNaN(d.getTime())) return iso
    return new Intl.DateTimeFormat(lang === 'es' ? 'es' : 'en', {
      day: 'numeric', month: long ? 'long' : 'short', year: 'numeric',
    }).format(d)
  }
  const num = (n: number) => new Intl.NumberFormat(lang === 'es' ? 'es' : 'en', { maximumFractionDigits: 3 }).format(n)
  return { t: tr, date, num }
}

const card: React.CSSProperties = {
  border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px',
  background: 'var(--surface)',
}

// ── Plain-language sentence for one finding ──────────────────────────────────
function appliedLine(f: GuidanceFinding, t: Translate, num: (n: number) => string): string {
  const p = f.params as Record<string, unknown>
  const params: Params = { column: f.column ?? '' }
  for (const [k, v] of Object.entries(p)) {
    if (typeof v === 'string' || typeof v === 'number') params[k] = v
  }
  if (f.code === 'demand_negatives') params.pct = num(Math.round(Number(p.share ?? 0) * 1000) / 10)
  if (f.code === 'sku_leading_zeros') params.example = String((f.examples ?? [])[0] ?? '')
  if (f.code === 'date_order_inferred') params.order = t(`guide.order.${String(p.order)}`)
  if (f.code === 'sheet_auto_selected') params.sheet = String(p.sheet ?? '')
  return t(`guide.applied.${f.code}`, params)
}

// ── Preview of the first rows as the system reads them ───────────────────────
function ReadingPreview({ report }: { report: GuidanceReport }) {
  const { t, date, num } = useCopy()
  const pv = report.preview
  if (!pv) return null
  const slotOf: Record<string, string> = {}
  for (const [slot, col] of Object.entries(pv.slots)) slotOf[col] = slot
  // Required columns first, then a few others; the rest are summarised.
  const order = pv.columns.map((_, i) => i).sort((a, b) => {
    const sa = slotOf[pv.columns[a]] ? 0 : 1
    const sb = slotOf[pv.columns[b]] ? 0 : 1
    return sa - sb || a - b
  })
  const shown = order.slice(0, 5)
  const status = Object.fromEntries(report.columns.map(c => [c.slot, c.status]))
  const read = (slot: string, row: PreviewRow) => {
    if (slot === 'date') return row.read.date ? date(row.read.date) : ''
    if (slot === 'demand') return row.read.demand == null ? '' : t('guide.units', { n: num(row.read.demand) })
    return row.read.sku ?? ''
  }
  return (
    <div style={{ marginTop: 14 }}>
      <p style={{ fontSize: 12, color: 'var(--dim)', margin: '0 0 8px' }}>{t('guide.preview_caption')}</p>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 12.5, width: '100%' }}>
          <thead>
            <tr>
              {shown.map(i => {
                const slot = slotOf[pv.columns[i]]
                const st = slot ? status[slot] : undefined
                return (
                  <th key={i} style={{
                    textAlign: 'left', padding: '6px 10px', fontWeight: 600, whiteSpace: 'nowrap',
                    borderBottom: `2px solid ${slot ? (st === 'ok' ? '#2E8B62' : '#d97706') : 'var(--border)'}`,
                    color: slot ? 'var(--text)' : 'var(--dim)',
                    background: slot ? 'var(--surface-2, #f8fafc)' : 'transparent',
                  }}>
                    {slot && <div style={{ fontSize: 10.5, color: 'var(--accent)', textTransform: 'uppercase', letterSpacing: 0.4 }}>
                      {t(`guide.slot.${slot}`)}
                    </div>}
                    {pv.columns[i]}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {pv.rows.map((row, r) => (
              <tr key={r}>
                {shown.map(i => {
                  const slot = slotOf[pv.columns[i]]
                  const raw = row.cells[i]
                  const parsed = slot ? read(slot, row) : ''
                  return (
                    <td key={i} style={{
                      padding: '6px 10px', borderBottom: '1px solid var(--border)',
                      background: slot ? 'var(--surface-2, #f8fafc)' : 'transparent', verticalAlign: 'top',
                    }}>
                      <div style={{ color: 'var(--text)' }}>{raw == null || raw === '' ? '—' : String(raw)}</div>
                      {slot && parsed && String(parsed) !== String(raw) && (
                        <div style={{ fontSize: 11, color: 'var(--accent)' }}>{parsed}</div>
                      )}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pv.columns.length > shown.length && (
        <p style={{ fontSize: 11.5, color: 'var(--dim)', margin: '6px 0 0' }}>
          {t('guide.more_columns', { n: pv.columns.length - shown.length })}
        </p>
      )}
    </div>
  )
}

// ── Verdict per column ───────────────────────────────────────────────────────
function ColumnVerdicts({ report }: { report: GuidanceReport }) {
  const { t, date, num } = useCopy()
  return (
    <ul style={{ listStyle: 'none', margin: '14px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {report.columns.map(c => {
        const ok = c.status === 'ok'
        const sample = (c.samples ?? [])[0]
        const shownSample = c.slot === 'date' && typeof sample === 'string' && /^\d{4}-\d{2}-\d{2}/.test(sample)
          ? date(sample) : c.slot === 'demand' && typeof sample === 'number'
            ? t('guide.units', { n: num(sample) }) : String(sample ?? '')
        return (
          <li key={c.slot} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 13.5, lineHeight: 1.45 }}>
            <span aria-hidden style={{ color: ok ? '#2E8B62' : '#d97706', marginTop: 2, flexShrink: 0 }}>
              {ok ? <Check size={16} /> : <AlertTriangle size={16} />}
            </span>
            <span style={{ color: 'var(--text)' }}>
              {c.column
                ? t(`guide.read.${c.slot}`, { column: c.column, sample: shownSample })
                : t('guide.read_missing', { slot: t(`guide.slot.${c.slot}`) })}
              {!ok && <span style={{ color: 'var(--dim)' }}> {t(c.status === 'bad' ? 'guide.verdict.bad' : 'guide.verdict.check')}</span>}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

// ── Progress: "revisamos 6 de 6 cosas" ───────────────────────────────────────
function Progress({ report }: { report: GuidanceReport }) {
  const { t } = useCopy()
  return (
    <div style={{ marginBottom: 12 }}>
      <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', margin: '0 0 6px' }}>
        {t('guide.progress', { done: report.checks_done, total: report.checks_total })}
      </p>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {report.checks.map(c => {
          const done = c.status === 'ok' || c.status === 'check'
          return (
            <span key={c.id} style={{
              fontSize: 11.5, padding: '3px 9px', borderRadius: 999,
              border: `1px solid ${done ? '#2E8B6255' : 'var(--border)'}`,
              color: done ? '#2E8B62' : 'var(--dim)',
            }}>
              {t(`guide.check.${c.id}`)}
            </span>
          )
        })}
      </div>
    </div>
  )
}

// ── The ONE question ─────────────────────────────────────────────────────────
function optionLabel(q: GuidanceFinding, o: GuidanceOption, t: Translate,
  date: (iso: string | null | undefined, long?: boolean) => string,
  num: (n: number) => string): string {
  const ex = (q.examples ?? [])[0] as Record<string, unknown> | undefined
  const p = o.params as Record<string, unknown>
  switch (q.code) {
    case 'date_order_ambiguous': {
      const iso = ex ? (o.id === 'day_first' ? ex.day_first : ex.month_first) : null
      return iso ? date(String(iso), true) : t(`guide.q.date_order_ambiguous.opt_${o.id}`)
    }
    case 'number_format_ambiguous': {
      const v = ex ? (o.id === ',' ? ex.decimal_comma : ex.decimal_dot) : null
      return v == null ? t(`guide.q.number_format_ambiguous.opt_${o.id === ',' ? 'comma' : 'dot'}`)
        : t('guide.q.number_format_ambiguous.opt', { raw: String(ex?.raw), value: num(Number(v)) })
    }
    case 'sheet_choice':
      return t('guide.q.sheet_choice.opt', { sheet: String(p.sheet), rows: Number(p.rows) })
    case 'wide_year_missing':
      return String(p.year)
    case 'sku_leading_zeros_lost':
      return t(`guide.q.sku_leading_zeros_lost.opt_${o.id}`, {
        padded: String((ex as Record<string, unknown> | undefined)?.padded ?? ''),
      })
    default:
      if (q.code.endsWith('_column_choice')) {
        const samples = Array.isArray(p.samples) ? (p.samples as unknown[]).slice(0, 3).join(', ') : ''
        return samples ? t('guide.q.column_opt', { column: String(p.column), samples }) : String(p.column)
      }
      return t(`guide.q.${q.code}.opt_${o.id}`) || o.id
  }
}

function QuestionCard({ q, busy, onAnswer }: {
  q: GuidanceFinding; busy: boolean; onAnswer: (d: Record<string, unknown>) => void
}) {
  const { t, date, num } = useCopy()
  const ex = (q.examples ?? [])[0] as Record<string, unknown> | string | string[] | undefined
  const exObj = (typeof ex === 'object' && ex && !Array.isArray(ex)) ? ex as Record<string, unknown> : undefined
  const p = q.params as Record<string, unknown>
  const params: Params = { column: q.column ?? '' }
  for (const [k, v] of Object.entries(p)) if (typeof v === 'string' || typeof v === 'number') params[k] = v
  if (exObj?.raw !== undefined) params.raw = String(exObj.raw)
  if (exObj?.day_first) params.a = date(String(exObj.day_first), true)
  if (exObj?.month_first) params.b = date(String(exObj.month_first), true)
  if (exObj?.padded) params.padded = String(exObj.padded)
  if (typeof ex === 'string') params.example = ex
  if (Array.isArray(ex)) { params.a = String(ex[0]); params.b = String(ex[1] ?? '') }
  if (q.code === 'wide_year_missing') params.months = Array.isArray(p.months) ? (p.months as string[]).join(', ') : ''
  const reason = String(p.reason ?? '')
  const slot = String(p.slot ?? '')
  const titleKey = q.code.endsWith('_column_choice')
    ? `guide.q.${slot}_column_choice.title_${reason}`
    : q.code === 'date_order_ambiguous' ? `guide.q.date_order_ambiguous.title_${reason || 'ambiguous'}`
      : q.code === 'number_format_ambiguous' ? `guide.q.number_format_ambiguous.title_${reason || 'ambiguous'}`
        : `guide.q.${q.code}.title`
  const title = t(titleKey, params) || t(`guide.q.${slot}_column_choice.title_tie`, params) || t('guide.q.generic.title')
  const options = q.options ?? []
  const withNone = q.code === 'demand_column_choice' && reason === 'units_or_money'
  const bigBtn: React.CSSProperties = {
    minHeight: 52, padding: '10px 16px', borderRadius: 10, fontSize: 15, fontWeight: 600,
    border: '1px solid var(--accent)', background: 'var(--surface)', color: 'var(--accent)',
    cursor: busy ? 'not-allowed' : 'pointer', textAlign: 'left', opacity: busy ? 0.6 : 1,
  }
  return (
    <div role="group" aria-label={title} style={{ ...card, border: '1px solid var(--accent)', marginTop: 16 }}>
      <p style={{ fontSize: 15.5, fontWeight: 700, color: 'var(--text)', margin: '0 0 12px', lineHeight: 1.4 }}>{title}</p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {options.map(o => (
          <button key={o.id} type="button" disabled={busy} style={bigBtn}
            onClick={() => onAnswer(o.decision)}>
            {optionLabel(q, o, t, date, num)}
          </button>
        ))}
        {withNone && (
          <button type="button" disabled={busy} style={{ ...bigBtn, color: 'var(--dim)', border: '1px solid var(--border)' }}
            onClick={() => onAnswer({ demand_column: '__none__' })}>
            {t('guide.q.none_of_these')}
          </button>
        )}
      </div>
    </div>
  )
}

// ── Unusable file: what to change, with an example and the template ──────────
function UnusablePanel({ report, onPickAnother }: { report: GuidanceReport; onPickAnother: () => void }) {
  const { t } = useCopy()
  const blocks = report.findings.filter(f => f.severity === 'block')
  return (
    <div>
      <div style={{ ...card, borderColor: '#C0504D66', background: 'rgba(192,80,77,0.05)' }}>
        <p style={{ fontSize: 15.5, fontWeight: 700, color: 'var(--text)', margin: '0 0 8px' }}>{t('guide.unusable.title')}</p>
        <ul style={{ margin: 0, padding: '0 0 0 18px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          {blocks.map(b => {
            const params: Params = { column: b.column ?? '' }
            for (const [k, v] of Object.entries(b.params)) {
              if (typeof v === 'string' || typeof v === 'number') params[k] = v
              if (Array.isArray(v)) params[k] = v.slice(0, 4).join(', ')
            }
            return (
              <li key={b.code} style={{ fontSize: 13.5, lineHeight: 1.55, color: 'var(--text)' }}>
                <strong>{t(`guide.block.${b.code}.title`, params) || t('guide.block.generic.title')}</strong>{' '}
                {t(`guide.block.${b.code}.body`, params)}
              </li>
            )
          })}
        </ul>
        {blocks.some(b => b.code === 'stock_snapshot') && (
          <a href="/inventario" style={{ display: 'inline-block', marginTop: 10, fontSize: 13.5, fontWeight: 600, color: 'var(--accent)' }}>
            {t('guide.unusable.stock_cta')}
          </a>
        )}
      </div>
      <p style={{ fontSize: 13, fontWeight: 600, margin: '16px 0 6px', color: 'var(--text)' }}>{t('guide.unusable.example')}</p>
      <UploadGuide kind="sales" defaultOpen />
      <button type="button" onClick={onPickAnother} style={{
        marginTop: 16, width: '100%', minHeight: 52, background: 'var(--accent)', color: '#fff',
        border: 'none', borderRadius: 10, fontSize: 15, fontWeight: 700, cursor: 'pointer',
      }}>
        {t('guide.unusable.pick')}
      </button>
    </div>
  )
}

// ── What we did for you ──────────────────────────────────────────────────────
function AppliedList({ report, applied, onUndo, busy }: {
  report: GuidanceReport; applied: GuidedRecord | null; onUndo?: () => void; busy: boolean
}) {
  const { t, num } = useCopy()
  const source: GuidanceFinding[] = (applied?.findings as GuidanceFinding[] | undefined)
    ?? report.findings.filter(f => f.severity === 'auto')
  const rest = report.findings.filter(f => f.severity === 'info' && APPLIED_CODES.has(f.code))
  const seen = new Set<string>()
  const lines = [...source, ...rest]
    .filter(f => APPLIED_CODES.has(f.code) && !seen.has(f.code + (f.column ?? '')) && seen.add(f.code + (f.column ?? '')))
    .map(f => ({ code: f.code, text: appliedLine(f, t, num) }))
    .filter(l => l.text)
  if (!lines.length) return null
  return (
    <div style={{ ...card, marginTop: 16, background: 'var(--surface-2, #f8fafc)' }}>
      <p style={{ fontSize: 13, fontWeight: 700, margin: '0 0 6px', color: 'var(--text)' }}>{t('guide.applied_title')}</p>
      <ul style={{ margin: 0, padding: '0 0 0 18px', display: 'flex', flexDirection: 'column', gap: 4 }}>
        {lines.map(l => <li key={l.code} style={{ fontSize: 13, lineHeight: 1.5, color: 'var(--text)' }}>{l.text}</li>)}
      </ul>
      {applied && onUndo && (
        <button type="button" disabled={busy} onClick={onUndo} style={{
          marginTop: 8, background: 'none', border: 'none', padding: 0, cursor: 'pointer',
          fontSize: 12.5, color: 'var(--accent)', textDecoration: 'underline',
        }}>
          {t('guide.undo')}
        </button>
      )}
    </div>
  )
}

// ── "Before you train" in plain words ────────────────────────────────────────
export function SanitySummary({ report, horizonMax }: { report: GuidanceReport; horizonMax?: string | null }) {
  const { t, date } = useCopy()
  const s = report.summary
  if (!s) return null
  const unit = t(`guide.unit.${s.granularity ?? 'irregular'}`)
  return (
    <div style={{ ...card, marginTop: 16 }}>
      <p style={{ fontSize: 14, lineHeight: 1.55, margin: 0, color: 'var(--text)' }}>
        {t('guide.summary.line', {
          products: s.products, periods: s.periods, unit,
          from: date(s.date_min), to: date(s.date_max),
        })}
        {s.granularity && s.granularity !== 'irregular' && (' ' + t('guide.summary.grain', { grain: t(`guide.grain.${s.granularity}`) }))}
      </p>
      {s.short_products > 0 && (
        <p style={{ fontSize: 13, lineHeight: 1.5, margin: '8px 0 0', color: 'var(--text)' }}>
          {t(s.short_products === 1 ? 'guide.summary.short_one' : 'guide.summary.short',
            { n: s.short_products, min: s.short_threshold, unit })}
        </p>
      )}
      {horizonMax && (
        <p style={{ fontSize: 13, lineHeight: 1.5, margin: '8px 0 0', color: 'var(--dim)' }}>
          {t('guide.summary.horizon', { span: horizonMax })}
        </p>
      )}
    </div>
  )
}

export default function GuidedReading({
  report, applied, busy, onAnswer, onUndo, onPickAnother, horizonMax,
}: {
  report: GuidanceReport
  applied: GuidedRecord | null
  busy: boolean
  onAnswer: (decision: Record<string, unknown>) => void
  onUndo?: () => void
  onPickAnother: () => void
  horizonMax?: string | null
}) {
  const { t } = useCopy()
  if (report.verdict === 'unusable') {
    return <UnusablePanel report={report} onPickAnother={onPickAnother} />
  }
  const q = report.next_question
  return (
    <div>
      <p style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', margin: '0 0 10px' }}>
        {t(q ? 'guide.title_ask' : 'guide.title')}
      </p>
      <Progress report={report} />
      <ReadingPreview report={report} />
      <ColumnVerdicts report={report} />
      {q && <QuestionCard q={q} busy={busy} onAnswer={onAnswer} />}
      <AppliedList report={report} applied={applied} onUndo={onUndo} busy={busy} />
      {!q && <SanitySummary report={report} horizonMax={horizonMax} />}
    </div>
  )
}
